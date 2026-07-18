"""Cross-process PostgreSQL order-submission reservations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import MetaData, Table, and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from quantinue.broker.reservations import (
    CompletedClaim,
    InFlightClaim,
    OwnerClaim,
    ReservationClaim,
    ReservationOwnerToken,
)
from quantinue.core.contracts import OrderResult

_INT_ADAPTER = TypeAdapter(int)
_STRING_ADAPTER = TypeAdapter(str)
_METADATA = MetaData()


class _ReservationRow(BaseModel):
    model_config = ConfigDict(strict=True)
    result: OrderResult | None


class PostgresOrderReservations:
    """Canonical order_submissions adapter with token-guarded stale reclaim."""

    def __init__(self, database_url: str, *, stale_after_seconds: float = 60.0) -> None:
        """Create an unopened engine and configure stale ownership."""
        self._engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self._stale_after = timedelta(seconds=max(stale_after_seconds, 0.000001))

    async def initialize(self) -> None:
        """Reflect the canonical table without mutating its schema."""
        async with self._engine.begin() as connection:
            await connection.run_sync(_METADATA.reflect, only=("order_submissions",))

    async def close(self) -> None:
        """Dispose owned pooled connections."""
        await self._engine.dispose()

    async def claim(self, client_order_id: str) -> ReservationClaim:
        """Atomically create, join, reuse, or reclaim one order generation."""
        table = self._table()
        owner_token = ReservationOwnerToken(uuid4().hex)
        now = datetime.now(UTC)
        statement = (
            insert(table)
            .values(
                client_order_id=client_order_id,
                state="claimed",
                owner_token=str(owner_token),
                claimed_at=now,
                stale_after=now + self._stale_after,
            )
            .on_conflict_do_update(
                index_elements=[table.c.client_order_id],
                set_={
                    "owner_token": str(owner_token),
                    "claimed_at": now,
                    "stale_after": now + self._stale_after,
                    "state": "claimed",
                },
                where=and_(
                    table.c.result_payload.is_(None),
                    table.c.stale_after <= now,
                ),
            )
            .returning(table.c.owner_token)
        )
        async with self._engine.begin() as connection:
            claimed_token = await connection.scalar(statement)
            if claimed_token is not None:
                return OwnerClaim(
                    ReservationOwnerToken(_STRING_ADAPTER.validate_python(claimed_token))
                )
            raw_row = (
                (
                    await connection.execute(
                        select(table.c.result_payload).where(
                            table.c.client_order_id == client_order_id
                        )
                    )
                )
                .mappings()
                .one()
            )
        row = _ReservationRow.model_validate({"result": raw_row["result_payload"]})
        if row.result is not None:
            return CompletedClaim(row.result)
        return InFlightClaim()

    async def complete(
        self,
        client_order_id: str,
        owner_token: ReservationOwnerToken,
        result: OrderResult,
    ) -> bool:
        """Publish only when the caller still owns the current generation."""
        table = self._table()
        async with self._engine.begin() as connection:
            cursor = await connection.execute(
                table.update()
                .where(
                    and_(
                        table.c.client_order_id == client_order_id,
                        table.c.owner_token == str(owner_token),
                        table.c.result_payload.is_(None),
                    )
                )
                .values(
                    state="completed",
                    result_payload=result.model_dump(mode="json"),
                    broker_order_id=result.order_id,
                    updated_at=datetime.now(UTC),
                )
            )
        return _INT_ADAPTER.validate_python(cursor.rowcount) == 1

    async def release(self, client_order_id: str, owner_token: ReservationOwnerToken) -> bool:
        """Delete only an unfinished generation still owned by the caller."""
        table = self._table()
        async with self._engine.begin() as connection:
            cursor = await connection.execute(
                table.delete().where(
                    and_(
                        table.c.client_order_id == client_order_id,
                        table.c.owner_token == str(owner_token),
                        table.c.result_payload.is_(None),
                    )
                )
            )
        return _INT_ADAPTER.validate_python(cursor.rowcount) == 1

    async def wait(self, client_order_id: str, timeout_seconds: float) -> OrderResult | None:
        """Poll boundedly for cross-process completion."""
        with anyio.move_on_after(timeout_seconds):
            while True:
                completed = await self._completed(client_order_id)
                if completed is not None:
                    return completed
                await anyio.sleep(0.02)
        return None

    async def _completed(self, client_order_id: str) -> OrderResult | None:
        table = self._table()
        async with self._engine.connect() as connection:
            result = await connection.scalar(
                select(table.c.result_payload).where(table.c.client_order_id == client_order_id)
            )
        if result is None:
            return None
        return OrderResult.model_validate(result)

    @staticmethod
    def _table() -> Table:
        return _METADATA.tables["order_submissions"]
