"""PostgreSQL repository for canonical trading and delayed-review rows."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import ColumnElement, MetaData, Table, and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from quantinue.core.contracts import DisclosureSourceRecord, NewsSourceRecord
from quantinue.db.contracts import AppOrderExposureStatus
from quantinue.db.domain_records import (
    AccountRiskState,
    AccountWrite,
    CompletedFillWrite,
    CriticVerdictWrite,
    OrderPlanWrite,
    OrderReconciliation,
    StrategistSignalWrite,
)
from quantinue.db.domain_sources import save_source_records
from quantinue.db.postgres_accounting import initialize_account, record_completed_fill
from quantinue.roles.exits import OpenPosition
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
    "tb_order_plan",
    "tb_account",
    "tb_order",
    "tb_fill",
)


class _IdentifierRow(BaseModel):
    model_config = ConfigDict(strict=True)
    value: int


def _is_open_position(orders: Table) -> ColumnElement[bool]:
    """Match filled buys that no close order has claimed yet.

    보유는 "체결된 매수"가 아니라 "체결됐고 아직 닫히지 않은 매수"다. 두 가지를
    걸러야 한다:

    1. ``order_type='bracket'`` — 청산 행 자체가 같은 종목으로 한 건 더 잡히면
       한 포지션이 둘로 세어진다(DISTINCT ticker라도 매수·청산이 같은 티커라
       중복은 안 나지만, 전량 청산 후에도 청산 행 때문에 보유가 남는다).
    2. 자신을 가리키는 close 행이 없을 것 — ``closes_order_id``가 실현손익의
       짝이자 "이 매수는 닫혔다"는 유일한 증거다.

    청산 주문이 아직 체결 전(submitted)이어도 열린 것으로 보지 않는다. 곧 닫힐
    포지션에 신규 매수 한도를 내주면 한도를 두 번 쓰게 된다 — 보수적으로 막는다.
    """
    closes = orders.alias("closing_order")
    return and_(
        orders.c.status == "filled",
        orders.c.order_type == "bracket",
        ~select(closes.c.id)
        .where(
            closes.c.closes_order_id == orders.c.id,
            closes.c.status.in_(("filled", "submitted")),
        )
        .exists(),
    )


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

    async def active_accounts(self) -> tuple[AccountRiskState, ...]:
        """Return every account that subscribes to this cycle, in stable order.

        Order matters: an unstable one would let a different account exhaust
        the daily caps on each run.
        """
        accounts = self._table("tb_account")
        orders = self._table("tb_order")
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    select(
                        accounts.c.id,
                        accounts.c.cash,
                        accounts.c.equity,
                        accounts.c.inv_type,
                    )
                    .where(accounts.c.status == "active")
                    .order_by(accounts.c.id)
                )
            ).all()
            held = dict(
                (
                    await connection.execute(
                        select(
                            orders.c.account_id,
                            func.count(func.distinct(orders.c.ticker)),
                        )
                        .where(_is_open_position(orders))
                        .group_by(orders.c.account_id)
                    )
                ).all()
            )
        return tuple(
            AccountRiskState(
                account_id=row.id,
                cash=Decimal(str(row.cash)),
                equity=Decimal(str(row.equity)),
                open_position_count=int(held.get(row.id, 0)),
                inv_type=row.inv_type,
            )
            for row in rows
        )

    async def open_positions(self) -> tuple[OpenPosition, ...]:
        """List every unclosed filled entry with the terms the exit rules need.

        ``account_risk_state``와 **같은 술어**(``_is_open_position``)를 쓴다.
        둘이 갈라지면 한도는 보유가 있다고 보는데 청산 잡은 없다고 보는,
        디버깅하기 최악인 상태가 된다.

        체결일은 ``tb_fill``에서 온다 — 주문 생성일이 아니다. 계획만 세우고
        체결되지 않은 주문은 보유가 아니므로 보유 기간이 시작되지 않는다.
        부분체결이 여럿이면 가장 이른 체결을 진입 시점으로 본다.
        """
        orders = self._table("tb_order")
        fills = self._table("tb_fill")
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    select(
                        orders.c.id,
                        orders.c.signal_id,
                        orders.c.account_id,
                        orders.c.ticker,
                        orders.c.quantity,
                        orders.c.entry_price,
                        orders.c.stop_price,
                        orders.c.take_profit_price,
                        func.min(fills.c.filled_at).label("first_filled_at"),
                    )
                    .select_from(orders.join(fills, fills.c.order_id == orders.c.id))
                    .where(_is_open_position(orders), fills.c.side == "buy")
                    .group_by(
                        orders.c.id,
                        orders.c.signal_id,
                        orders.c.account_id,
                        orders.c.ticker,
                        orders.c.quantity,
                        orders.c.entry_price,
                        orders.c.stop_price,
                        orders.c.take_profit_price,
                    )
                    # 순서를 고정한다 — 청산도 일일 예산을 쓰게 되면 실행마다
                    # 다른 포지션이 먼저 처리되는 일이 없어야 한다.
                    .order_by(orders.c.id)
                )
            ).all()
        return tuple(
            OpenPosition(
                order_id=int(row.id),
                signal_id=int(row.signal_id),
                account_id=int(row.account_id),
                ticker=row.ticker,
                quantity=int(row.quantity),
                entry_price=Decimal(str(row.entry_price)),
                stop_price=(
                    None if row.stop_price is None else Decimal(str(row.stop_price))
                ),
                take_profit_price=(
                    None
                    if row.take_profit_price is None
                    else Decimal(str(row.take_profit_price))
                ),
                filled_on=row.first_filled_at.date(),
            )
            for row in rows
        )

    async def account_risk_state(self, account_id: int) -> AccountRiskState | None:
        """Read the capital and book size the portfolio limits are applied to.

        Positions are derived from filled buys that nothing has closed yet — see
        ``_is_open_position``.
        """
        accounts = self._table("tb_account")
        orders = self._table("tb_order")
        async with self._engine.begin() as connection:
            row = (
                await connection.execute(
                    select(
                        accounts.c.cash, accounts.c.equity, accounts.c.inv_type
                    ).where(accounts.c.id == account_id)
                )
            ).first()
            if row is None:
                return None
            held = await connection.scalar(
                select(func.count(func.distinct(orders.c.ticker))).where(
                    orders.c.account_id == account_id,
                    _is_open_position(orders),
                )
            )
        return AccountRiskState(
            account_id=account_id,
            cash=Decimal(str(row.cash)),
            equity=Decimal(str(row.equity)),
            open_position_count=int(held or 0),
            inv_type=row.inv_type,
        )

    async def save_order_plan(self, value: OrderPlanWrite) -> None:
        """Record role 09's decision — including the ones that blocked a buy.

        Only orders that exist leave a tb_order row, so without this a guard
        that fired was invisible to SQL and threshold calibration had nothing
        to count.
        """
        table = self._table("tb_order_plan")
        statement = (
            insert(table)
            .values(
                run_id=value.run_id,
                ticker=value.ticker,
                cycle_ts=value.cycle_ts,
                trade_date=value.trade_date,
                account_id=value.account_id,
                signal_id=value.signal_id,
                decision=value.decision,
                skipped_reason=value.skipped_reason,
                quantity=value.quantity,
                entry_price=value.entry_price,
                stop_price=value.stop_price,
                take_profit_price=value.take_profit_price,
            )
            .on_conflict_do_nothing(index_elements=["ticker", "cycle_ts", "account_id"])
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
                signal_consensus=value.signal_consensus,
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
            "verdict_source": value.verdict_source,
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

    async def record_completed_fill(self, value: CompletedFillWrite) -> int:
        """Insert one unique local buy fill and debit its account atomically."""
        return await record_completed_fill(
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

    @property
    def engine(self) -> AsyncEngine:
        """Expose the engine for operational scripts and tests."""
        return self._engine

    def _table(self, name: str) -> Table:
        return self._metadata.tables[name]
