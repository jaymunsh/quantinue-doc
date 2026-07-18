"""PostgreSQL persistence dedicated to delayed T+5 reviews."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from pydantic_core import to_json
from sqlalchemy import MetaData, Table, and_, case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from quantinue.core.contracts import PipelineRun, ReviewResult
from quantinue.core.terminal_detail import RoleDetail
from quantinue.roles.role_11_reviewer.processor import DueReviewSignal, ReviewSnapshotWrite

_TABLES = (
    "pipeline_runs",
    "tb_strategist_signals",
    "tb_order",
    "tb_fill",
    "tb_review_price_snapshots",
    "tb_review",
)


class _SignalRow(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int
    run_id: str
    ticker: str
    side: str
    trade_date: date
    decision_close: Decimal


class _SnapshotRow(BaseModel):
    model_config = ConfigDict(strict=True)
    day_offset: int
    close: Decimal | None = None


class PostgresReviewRepository:
    """Idempotent snapshot and final-review repository."""

    def __init__(self, database_url: str) -> None:
        """Create a lazy async engine."""
        self._engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self._metadata = MetaData()

    async def initialize(self) -> None:
        """Reflect review tables after schema bootstrap."""
        async with self._engine.begin() as connection:
            await connection.run_sync(self._metadata.reflect, only=_TABLES)

    async def close(self) -> None:
        """Dispose the connection pool."""
        await self._engine.dispose()

    async def get_signal(self, signal_id: int) -> DueReviewSignal | None:
        """Load a review projection for traded and no-trade signals."""
        signals, runs = self._table("tb_strategist_signals"), self._table("pipeline_runs")
        orders, fills = self._table("tb_order"), self._table("tb_fill")
        fill_base = (
            select(
                orders.c.signal_id.label("signal_id"),
                (func.sum(fills.c.price * fills.c.quantity) / func.sum(fills.c.quantity)).label(
                    "fill_price"
                ),
            )
            .join(fills, fills.c.order_id == orders.c.id)
            .group_by(orders.c.signal_id)
            .subquery()
        )
        query = (
            select(
                signals.c.id,
                runs.c.run_id,
                signals.c.ticker,
                signals.c.side,
                signals.c.trade_date,
                case(
                    (signals.c.side == "buy", fill_base.c.fill_price),
                    else_=signals.c.decision_close,
                ).label("decision_close"),
            )
            .join(
                runs, and_(runs.c.ticker == signals.c.ticker, runs.c.cycle_ts == signals.c.cycle_ts)
            )
            .outerjoin(fill_base, fill_base.c.signal_id == signals.c.id)
        )
        async with self._engine.connect() as connection:
            raw = (
                (await connection.execute(query.where(signals.c.id == signal_id)))
                .mappings()
                .one_or_none()
            )
        if raw is None:
            return None
        row = _SignalRow.model_validate(dict(raw))
        return DueReviewSignal(
            row.id, row.run_id, row.ticker, row.side, row.trade_date, row.decision_close
        )

    async def snapshot_offsets(self, signal_id: int) -> frozenset[int]:
        """Return offsets already persisted."""
        table = self._table("tb_review_price_snapshots")
        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(
                    select(table.c.day_offset).where(table.c.signal_id == signal_id)
                )
            ).mappings()
            return frozenset(_SnapshotRow.model_validate(dict(row)).day_offset for row in rows)

    async def save_snapshot(self, value: ReviewSnapshotWrite) -> None:
        """Upsert one official close."""
        table = self._table("tb_review_price_snapshots")
        statement = (
            insert(table)
            .values(
                signal_id=value.signal_id,
                day_offset=value.day_offset,
                price_date=value.price_date,
                close=value.close,
                source=value.source,
                source_ref=value.source_ref,
                observed_at=value.observed_at,
                captured_at=value.captured_at,
                confidence=Decimal(str(value.confidence)),
                evidence_id=value.evidence_id,
                parent_evidence_ids=list(value.parent_evidence_ids),
                model_provider=(
                    value.model_provider.value if value.model_provider is not None else None
                ),
                model_name=value.model_name,
                prompt_version=value.prompt_version,
                policy_version=value.policy_version,
                input_hash=value.input_hash,
            )
            .on_conflict_do_update(
                index_elements=["signal_id", "day_offset"],
                set_={
                    "close": value.close,
                    "source": value.source,
                    "source_ref": value.source_ref,
                    "observed_at": value.observed_at,
                    "captured_at": value.captured_at,
                    "confidence": Decimal(str(value.confidence)),
                    "evidence_id": value.evidence_id,
                    "parent_evidence_ids": list(value.parent_evidence_ids),
                    "model_provider": (
                        value.model_provider.value if value.model_provider is not None else None
                    ),
                    "model_name": value.model_name,
                    "prompt_version": value.prompt_version,
                    "policy_version": value.policy_version,
                    "input_hash": value.input_hash,
                },
                where=table.c.captured_at < value.captured_at,
            )
        )
        async with self._engine.begin() as connection:
            _ = await connection.execute(statement)

    async def finalize_review(self, signal: DueReviewSignal, lesson: str) -> None:
        """Upsert a final review only when all five closes exist."""
        snapshots = self._table("tb_review_price_snapshots")
        async with self._engine.connect() as connection:
            raw = (
                (
                    await connection.execute(
                        select(snapshots.c.day_offset, snapshots.c.close).where(
                            snapshots.c.signal_id == signal.signal_id
                        )
                    )
                )
                .mappings()
                .all()
            )
        rows = tuple(_SnapshotRow.model_validate(dict(row)) for row in raw)
        closes = {row.day_offset: row.close for row in rows if row.close is not None}
        if set(closes) != {1, 2, 3, 4, 5}:
            return
        returns = {
            offset: (close / signal.base_price - 1) * 100 for offset, close in closes.items()
        }
        fields = {
            "ret_1d": returns[1],
            "ret_3d": returns[3],
            "ret_5d": returns[5],
            "is_hit": returns[5] > 0 if signal.side == "buy" else returns[5] <= 0,
            "max_drawdown": min(Decimal(0), *returns.values()),
            "lesson": lesson,
        }
        table = self._table("tb_review")
        statement = (
            insert(table)
            .values(signal_id=signal.signal_id, **fields)
            .on_conflict_do_update(index_elements=["signal_id"], set_=fields)
        )
        runs = self._table("pipeline_runs")
        review = ReviewResult(
            outcome="hit" if fields["is_hit"] else "miss",
            summary=(
                f"T+5 return {returns[5]:.3f}% | max drawdown "
                f"{fields['max_drawdown']:.3f}% | {lesson}"
            ),
        )
        async with self._engine.begin() as connection:
            _ = await connection.execute(statement)
            payload = await connection.scalar(
                select(runs.c.payload).where(runs.c.run_id == signal.run_id).with_for_update()
            )
            if payload is not None:
                run = PipelineRun.model_validate_json(to_json(payload))
                detail = run.detail.model_copy(
                    update={
                        "roles": tuple(
                            RoleDetail(
                                component=role.component,
                                title=role.title,
                                status="completed" if role.component == "11" else role.status,
                                summary=review.summary if role.component == "11" else role.summary,
                                facts=(
                                    ("결과", review.outcome),
                                    ("리뷰", review.summary),
                                )
                                if role.component == "11"
                                else role.facts,
                                items=role.items,
                            )
                            for role in run.detail.roles
                        )
                    }
                )
                _ = await connection.execute(
                    runs.update()
                    .where(runs.c.run_id == signal.run_id)
                    .values(
                        payload=run.model_copy(
                            update={"review": review, "detail": detail}
                        ).model_dump(mode="json")
                    )
                )

    def _table(self, name: str) -> Table:
        return self._metadata.tables[name]
