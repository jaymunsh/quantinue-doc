"""Typed durable reads for the PostgreSQL simulated buy-only portfolio."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict
from sqlalchemy import MetaData, Table, and_, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from quantinue.db.simulated_portfolio import (
    MarkSource,
    PortfolioMark,
    SimulatedAccount,
    SimulatedFill,
    SimulatedOrder,
    SimulatedOrderStatus,
    SimulatedPortfolioSnapshot,
    project_buy_only_portfolio,
)

LOCAL_SIMULATED_ACCOUNT_ID: Final = "quantinue-local-simulated"


class _AccountRow(BaseModel):
    model_config = ConfigDict(strict=True)

    cash: Decimal
    buying_power: Decimal


class _OrderRow(BaseModel):
    model_config = ConfigDict(strict=True)

    order_id: str
    ticker: str
    quantity: int
    reference_price: Decimal
    status: str
    created_at: datetime


class _FillRow(BaseModel):
    model_config = ConfigDict(strict=True)

    fill_id: str
    order_id: str
    ticker: str
    quantity: int
    price: Decimal
    filled_at: datetime


class _MarkRow(BaseModel):
    model_config = ConfigDict(strict=True)

    ticker: str
    price: Decimal
    as_of: datetime


async def read_simulated_portfolio(
    engine: AsyncEngine,
    metadata: MetaData,
    opening_cash: Decimal,
    account_identity: str = LOCAL_SIMULATED_ACCOUNT_ID,
) -> SimulatedPortfolioSnapshot:
    """Project canonical account, order, fill, and completed-run mark rows."""
    accounts = _table(metadata, "tb_account")
    orders = _table(metadata, "tb_order")
    fills = _table(metadata, "tb_fill")
    signals = _table(metadata, "tb_strategist_signals")
    runs = _table(metadata, "pipeline_runs")
    async with engine.connect() as connection:
        account = _AccountRow.model_validate(
            dict(
                (
                    await connection.execute(
                        select(accounts.c.cash, accounts.c.buying_power).where(
                            accounts.c.broker_account_id == account_identity
                        )
                    )
                )
                .mappings()
                .one()
            )
        )
        order_rows = (
            await connection.execute(
                select(
                    func.coalesce(orders.c.broker_order_id, orders.c.idempotency_key).label(
                        "order_id"
                    ),
                    orders.c.ticker,
                    orders.c.quantity,
                    orders.c.entry_price.label("reference_price"),
                    orders.c.status,
                    signals.c.cycle_ts.label("created_at"),
                )
                .select_from(
                    orders.join(accounts, orders.c.account_id == accounts.c.id).join(
                        signals, orders.c.signal_id == signals.c.id
                    )
                )
                .where(accounts.c.broker_account_id == account_identity)
                .order_by(orders.c.created_at, orders.c.id)
            )
        ).mappings()
        fill_rows = (
            await connection.execute(
                select(
                    fills.c.broker_fill_id.label("fill_id"),
                    func.coalesce(orders.c.broker_order_id, orders.c.idempotency_key).label(
                        "order_id"
                    ),
                    orders.c.ticker,
                    fills.c.quantity,
                    fills.c.price,
                    fills.c.filled_at,
                )
                .select_from(
                    fills.join(orders, fills.c.order_id == orders.c.id).join(
                        accounts, orders.c.account_id == accounts.c.id
                    )
                )
                .where(accounts.c.broker_account_id == account_identity)
                .order_by(fills.c.filled_at, fills.c.id)
            )
        ).mappings()
        mark_rows = (
            await connection.execute(
                select(
                    signals.c.ticker,
                    signals.c.decision_close.label("price"),
                    signals.c.cycle_ts.label("as_of"),
                )
                .select_from(
                    signals.join(
                        runs,
                        and_(
                            signals.c.ticker == runs.c.ticker,
                            signals.c.cycle_ts == runs.c.cycle_ts,
                        ),
                    )
                )
                .where(runs.c.status == "completed")
                .order_by(signals.c.cycle_ts)
            )
        ).mappings()
    parsed_orders = tuple(_OrderRow.model_validate(dict(row)) for row in order_rows)
    parsed_fills = tuple(_FillRow.model_validate(dict(row)) for row in fill_rows)
    parsed_marks = tuple(_MarkRow.model_validate(dict(row)) for row in mark_rows)
    projected = project_buy_only_portfolio(
        opening_cash,
        tuple(
            SimulatedOrder(
                order_id=row.order_id,
                ticker=row.ticker,
                quantity=row.quantity,
                reference_price=row.reference_price,
                status=SimulatedOrderStatus(row.status),
                created_at=row.created_at,
            )
            for row in parsed_orders
        ),
        tuple(
            SimulatedFill(
                fill_id=row.fill_id,
                order_id=row.order_id,
                ticker=row.ticker,
                quantity=row.quantity,
                price=row.price,
                filled_at=row.filled_at,
            )
            for row in parsed_fills
        ),
        tuple(
            PortfolioMark(
                ticker=row.ticker,
                price=row.price,
                source=MarkSource.COMPLETED_RUN,
                as_of=row.as_of,
            )
            for row in parsed_marks
        ),
    )
    market_value = sum(
        (position.market_value for position in projected.positions), start=Decimal(0)
    )
    durable_account = SimulatedAccount(
        opening_cash=opening_cash,
        current_cash=account.cash,
        equity=account.cash + market_value,
        buying_power=account.buying_power,
    )
    return replace(projected, account=durable_account)


def _table(metadata: MetaData, name: str) -> Table:
    return metadata.tables[name]
