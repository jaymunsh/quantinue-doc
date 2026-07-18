"""PostgreSQL repository for canonical trading and delayed-review rows."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import MetaData, Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from quantinue.core.contracts import DisclosureSourceRecord, NewsSourceRecord
from quantinue.db.contracts import AppOrderExposureStatus
from quantinue.db.domain_records import (
    AccountWrite,
    CompletedBuyWrite,
    CriticVerdictWrite,
    OrderReconciliation,
    StrategistSignalWrite,
)
from quantinue.db.domain_sources import save_source_records
from quantinue.db.postgres_accounting import initialize_account, record_completed_buy
from quantinue.roles.role_01_universe_screener.contracts import UniverseScreenerOutput
from quantinue.roles.role_02_technical_analysis.contracts import TechnicalAnalysisOutput
from quantinue.roles.role_03_daily_screener.contracts import DailyScreenerOutput
from quantinue.roles.role_04_macro_analysis.contracts import MacroAnalysisOutput

_TABLES = (
    "tb_universe",
    "tb_daily_pick",
    "tb_technical",
    "tb_macro",
    "tb_disclosure",
    "tb_news",
    "tb_disclosure_signal",
    "tb_news_signal",
    "tb_strategist_signals",
    "tb_critic_verdict",
    "tb_account",
    "tb_order",
    "tb_fill",
)


class _IdentifierRow(BaseModel):
    model_config = ConfigDict(strict=True)
    value: int


class PostgresDomainRepository:
    """Idempotent canonical-domain adapter with real database identifiers."""

    def __init__(self, database_url: str) -> None:
        """Create a lazy async engine for the domain schema."""
        self._engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self._metadata = MetaData()

    async def initialize(self) -> None:
        """Reflect the canonical schema after bootstrap."""
        async with self._engine.begin() as connection:
            await connection.run_sync(self._metadata.reflect, only=_TABLES)

    async def close(self) -> None:
        """Dispose all pooled database connections."""
        await self._engine.dispose()

    async def save_universe(self, value: UniverseScreenerOutput) -> None:
        """Upsert the validated role-01 members without synthesizing parents."""
        table = self._table("tb_universe")
        async with self._engine.begin() as connection:
            for member in value.members:
                fields = member.model_dump(exclude={"evidence_ids"})
                statement = (
                    insert(table)
                    .values(**fields)
                    .on_conflict_do_update(index_elements=["as_of_date", "ticker"], set_=fields)
                )
                _ = await connection.execute(statement)

    async def save_daily_stage(
        self, picks: DailyScreenerOutput, technical: TechnicalAnalysisOutput
    ) -> None:
        """Persist role-03 parents and selected role-02 children atomically."""
        pick_table = self._table("tb_daily_pick")
        technical_table = self._table("tb_technical")
        async with self._engine.begin() as connection:
            for pick in picks.picks:
                fields = pick.model_dump(exclude={"evidence_ids", "is_requested_focus"})
                statement = (
                    insert(pick_table)
                    .values(**fields)
                    .on_conflict_do_update(index_elements=["trade_date", "ticker"], set_=fields)
                )
                _ = await connection.execute(statement)
            selected = frozenset((pick.trade_date, pick.ticker) for pick in picks.picks)
            for snapshot in technical.snapshots:
                if (snapshot.trade_date, snapshot.ticker) not in selected:
                    continue
                fields = snapshot.model_dump(exclude={"evidence_ids", "is_requested_focus"})
                statement = (
                    insert(technical_table)
                    .values(**fields)
                    .on_conflict_do_update(index_elements=["trade_date", "ticker"], set_=fields)
                )
                _ = await connection.execute(statement)

    async def save_macro(self, value: MacroAnalysisOutput) -> None:
        """Upsert the validated role-04 macro observation."""
        table = self._table("tb_macro")
        fields = value.model_dump(exclude={"run_id", "evidence_ids"})
        statement = (
            insert(table)
            .values(**fields)
            .on_conflict_do_update(index_elements=["as_of"], set_=fields)
        )
        async with self._engine.begin() as connection:
            _ = await connection.execute(statement)

    async def save_signal(self, value: StrategistSignalWrite) -> int:
        """Insert or reuse a strategist signal and return its real identifier."""
        table = self._table("tb_strategist_signals")
        statement = (
            insert(table)
            .values(
                trade_date=value.trade_date,
                ticker=value.ticker,
                cycle_ts=value.cycle_ts,
                inv_type=value.inv_type,
                side=value.side,
                conviction=value.conviction,
                signal_consensus=0,
                summary=value.summary,
                evidence=list(value.evidence),
                sizing_hint={},
                decision_close=value.decision_close,
                current_price=value.decision_close,
                day_high=value.decision_close,
                day_low=value.decision_close,
                close_prev=value.decision_close,
                volume=0,
                turnover=0,
                high_52w=value.decision_close,
                low_52w=value.decision_close,
            )
            .on_conflict_do_nothing(index_elements=["ticker", "cycle_ts", "inv_type"])
        )
        async with self._engine.begin() as connection:
            _ = await connection.execute(statement)
            return _IdentifierRow.model_validate(
                {
                    "value": await connection.scalar(
                        select(table.c.id).where(
                            table.c.ticker == value.ticker,
                            table.c.cycle_ts == value.cycle_ts,
                            table.c.inv_type == value.inv_type,
                        )
                    )
                }
            ).value

    async def save_source_records(
        self,
        value: StrategistSignalWrite,
        disclosure_source: DisclosureSourceRecord,
        news_source: NewsSourceRecord,
    ) -> None:
        """Delegate the atomic source transaction to its focused module."""
        tables = (
            self._table("tb_disclosure"),
            self._table("tb_news"),
            self._table("tb_disclosure_signal"),
            self._table("tb_news_signal"),
        )
        await save_source_records(self._engine, tables, value, disclosure_source, news_source)

    async def save_verdict(self, value: CriticVerdictWrite) -> int:
        """Insert or reuse the unique critic verdict for a signal."""
        table = self._table("tb_critic_verdict")
        fields = {
            "signal_id": value.signal_id,
            "ticker": value.ticker,
            "decision": value.decision,
            "is_agreed": value.decision == "pass",
            "category": value.category,
            "objection": value.objection,
            "confidence": value.confidence,
            "decided_layer": value.decided_layer,
            "source": value.source,
        }
        statement = (
            insert(table).values(**fields).on_conflict_do_nothing(index_elements=["signal_id"])
        )
        async with self._engine.begin() as connection:
            _ = await connection.execute(statement)
            return _IdentifierRow.model_validate(
                {
                    "value": await connection.scalar(
                        select(table.c.id).where(table.c.signal_id == value.signal_id)
                    )
                }
            ).value

    async def save_account(self, value: AccountWrite) -> int:
        """Initialize one local account once without resetting durable balances."""
        return await initialize_account(self._engine, self._table("tb_account"), value)

    async def initialize_local_account(self, opening_cash: Decimal) -> int:
        """Initialize the fixed app-owned local account identity once."""
        account = AccountWrite(
            "quantinue-local-simulated", opening_cash, opening_cash, opening_cash
        )
        return await self.save_account(account)

    async def record_completed_buy(self, value: CompletedBuyWrite) -> int:
        """Insert one unique local buy fill and debit its account atomically."""
        return await record_completed_buy(
            self._engine,
            self._table("tb_order"),
            self._table("tb_fill"),
            self._table("tb_account"),
            value,
        )

    async def reconcile_order(self, value: OrderReconciliation) -> int:
        """Update the pre-reserved order by stable idempotency key."""
        table = self._table("tb_order")
        async with self._engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        select(table.c.id, table.c.status)
                        .where(table.c.idempotency_key == value.idempotency_key)
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            order_id = _IdentifierRow.model_validate({"value": row["id"]}).value
            target_status = AppOrderExposureStatus(value.status)
            if (
                row["status"]
                in {
                    AppOrderExposureStatus.FILLED.value,
                    AppOrderExposureStatus.FAILED.value,
                    AppOrderExposureStatus.CANCELED.value,
                }
                and row["status"] != target_status.value
            ):
                return order_id
            return _IdentifierRow.model_validate(
                {
                    "value": await connection.scalar(
                        table.update()
                        .where(table.c.id == order_id)
                        .values(
                            status=target_status.value,
                            broker_order_id=value.broker_order_id,
                            parent_order_id=value.parent_order_id,
                            stop_leg_order_id=value.stop_leg_order_id,
                            take_profit_leg_order_id=value.take_profit_leg_order_id,
                        )
                        .returning(table.c.id)
                    )
                }
            ).value

    def _table(self, name: str) -> Table:
        return self._metadata.tables[name]
