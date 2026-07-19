"""PostgreSQL repository using canonical operational tables."""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Final

import anyio
from pydantic_core import to_json
from sqlalchemy import MetaData, Table, and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from typing_extensions import override

from quantinue.core.contracts import PipelineContext, PipelineRequest, PipelineRun
from quantinue.db.codec import CONTEXT_ADAPTER, encode_context
from quantinue.db.contracts import AttemptFailure, PersistedAttempt, RunClaim
from quantinue.db.domain import PostgresDomainRepository
from quantinue.db.order_reservations import PostgresOrderReservations
from quantinue.db.postgres_lifecycle import PostgresDomainLifecycleMixin
from quantinue.db.postgres_lock import try_lock, unlock
from quantinue.db.postgres_portfolio import LOCAL_SIMULATED_ACCOUNT_ID
from quantinue.db.postgres_query import (
    close_stale_attempts,
    failed_run_is_resumable,
    resume_context,
    run_id_for,
)
from quantinue.db.postgres_run_reads import PostgresRunReadMixin
from quantinue.db.postgres_tables import RUN_STORE_TABLES

_METADATA = MetaData()
_DEFAULT_OPENING_CASH: Final = Decimal("1000000.00")


class PostgresRunStore(PostgresDomainLifecycleMixin, PostgresRunReadMixin):
    """Durable repository with session advisory locks as crash-safe claims."""

    def __init__(
        self,
        database_url: str,
        opening_cash: Decimal = _DEFAULT_OPENING_CASH,
        account_identity: str = LOCAL_SIMULATED_ACCOUNT_ID,
    ) -> None:
        """Create a tuned async engine without opening a connection."""
        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        self._claims: dict[str, AsyncConnection] = {}
        self.order_reservations = PostgresOrderReservations(database_url)
        self.domain = PostgresDomainRepository(database_url)
        PostgresDomainLifecycleMixin.__init__(self, self.domain, account_identity, opening_cash)

    async def initialize(self) -> None:
        """Reflect canonical tables created by schema bootstrap."""
        async with self._engine.begin() as connection:
            await connection.run_sync(_METADATA.reflect, only=RUN_STORE_TABLES)
        await self.order_reservations.initialize()
        await self.domain.initialize()
        _ = await self.domain.save_account(self._account)

    async def close(self) -> None:
        """Release live claims and dispose the pool."""
        for key in tuple(self._claims):
            await self.abandon(key)
        await self.order_reservations.close()
        await self.domain.close()
        await self._engine.dispose()

    async def claim(
        self, key: str, request: PipelineRequest, *, resume_failed: bool = False
    ) -> RunClaim:
        """Try the per-key advisory lock and load or create durable state."""
        connection = await self._engine.connect()
        locked = await try_lock(connection, key)
        if not locked:
            await connection.close()
            return RunClaim(acquired=False)
        runs = self._table("pipeline_runs")
        row = (
            (await connection.execute(select(runs).where(runs.c.idempotency_key == key)))
            .mappings()
            .one_or_none()
        )
        is_resumable = False
        if row is not None and row["status"] == "failed" and resume_failed:
            is_resumable = await failed_run_is_resumable(
                connection,
                runs,
                self._table("pipeline_stage_attempts"),
                key,
            )
        if row is not None and row["status"] in {"completed", "failed"} and not is_resumable:
            await unlock(connection, key)
            return RunClaim(
                acquired=False,
                terminal_run=PipelineRun.model_validate_json(to_json(row["payload"])),
            )
        if row is None:
            context = PipelineContext(request=request)
            _ = await connection.execute(
                insert(runs).values(
                    run_id=str(context.run_id),
                    idempotency_key=key,
                    ticker=request.ticker,
                    cycle_ts=request.cycle_ts,
                    status="running",
                    payload=encode_context(context),
                    started_at=datetime.now().astimezone(),
                )
            )
            await connection.commit()
        else:
            run_id = await run_id_for(connection, runs, key)
            await close_stale_attempts(connection, self._table("pipeline_stage_attempts"), run_id)
            context = await resume_context(
                connection,
                runs,
                self._table("pipeline_checkpoints"),
                key,
                request,
            )
            _ = await connection.execute(
                runs.update()
                .where(runs.c.idempotency_key == key)
                .values(status="running", finished_at=None)
            )
            await connection.commit()
        self._claims[key] = connection
        return RunClaim(acquired=True, context=context)

    async def wait_for_release(self, key: str) -> PipelineRun | None:
        """Yield briefly, then observe a terminal owner outcome when available."""
        await anyio.sleep(0.01)
        return await self.get_by_key(key)

    async def seed_context(self, key: str, context: PipelineContext) -> None:
        """Persist shared discovery state before candidate-specific work."""
        connection = self._claims[key]
        run_id = await run_id_for(connection, self._table("pipeline_runs"), key)
        checkpoints = self._table("pipeline_checkpoints")
        _ = await connection.execute(
            insert(checkpoints)
            .values(
                run_id=run_id,
                component="04",
                payload=encode_context(context),
                payload_hash=hashlib.sha256(CONTEXT_ADAPTER.dump_json(context)).hexdigest(),
            )
            .on_conflict_do_nothing(index_elements=["run_id", "component"])
        )
        await connection.commit()

    async def start_attempt(
        self, key: str, component: str, started_at: datetime
    ) -> PersistedAttempt:
        """Insert the next attempt while holding the run claim."""
        connection = self._claims[key]
        attempts = self._table("pipeline_stage_attempts")
        run_id = await run_id_for(connection, self._table("pipeline_runs"), key)
        count = await connection.scalar(
            select(func.count())
            .select_from(attempts)
            .where(and_(attempts.c.run_id == run_id, attempts.c.component == component))
        )
        number = int(count or 0) + 1
        _ = await connection.execute(
            insert(attempts).values(
                run_id=run_id,
                component=component,
                attempt_no=number,
                status="running",
                started_at=started_at,
            )
        )
        await connection.commit()
        return PersistedAttempt(component, number, "running", started_at)

    async def complete_stage(
        self, key: str, context: PipelineContext, attempt: PersistedAttempt
    ) -> None:
        """Commit completed attempt, checkpoint, and run payload atomically."""
        connection = self._claims[key]
        attempts = self._table("pipeline_stage_attempts")
        checkpoints = self._table("pipeline_checkpoints")
        runs = self._table("pipeline_runs")
        run_id = str(context.run_id)
        payload = encode_context(context)
        now = datetime.now().astimezone()
        async with connection.begin():
            _ = await connection.execute(
                attempts.update()
                .where(
                    and_(
                        attempts.c.run_id == run_id,
                        attempts.c.component == attempt.component,
                        attempts.c.attempt_no == attempt.attempt_no,
                    )
                )
                .values(status="completed", finished_at=now)
            )
            _ = await connection.execute(
                insert(checkpoints)
                .values(
                    run_id=run_id,
                    component=attempt.component,
                    payload=payload,
                    payload_hash=hashlib.sha256(CONTEXT_ADAPTER.dump_json(context)).hexdigest(),
                )
                .on_conflict_do_nothing(index_elements=["run_id", "component"])
            )
            _ = await connection.execute(
                runs.update().where(runs.c.run_id == run_id).values(payload=payload)
            )

    async def fail_attempt(
        self,
        key: str,
        attempt: PersistedAttempt,
        finished_at: datetime,
        failure: AttemptFailure,
    ) -> None:
        """Persist an observable failed attempt."""
        connection = self._claims[key]
        attempts = self._table("pipeline_stage_attempts")
        run_id = await run_id_for(connection, self._table("pipeline_runs"), key)
        _ = await connection.execute(
            attempts.update()
            .where(
                and_(
                    attempts.c.run_id == run_id,
                    attempts.c.component == attempt.component,
                    attempts.c.attempt_no == attempt.attempt_no,
                )
            )
            .values(
                status=failure.status,
                finished_at=finished_at,
                error_code=failure.error_code,
                error_message=failure.error_message,
            )
        )
        await connection.commit()

    async def finish_run(self, key: str, run: PipelineRun, *, resumable: bool = False) -> None:
        """Publish the terminal payload and release the advisory lock."""
        del resumable
        connection = self._claims[key]
        runs = self._table("pipeline_runs")
        _ = await connection.execute(
            runs.update()
            .where(runs.c.idempotency_key == key)
            .values(
                status=run.status.value,
                payload=run.model_dump(mode="json"),
                finished_at=datetime.now().astimezone(),
                updated_at=datetime.now().astimezone(),
            )
        )
        await connection.commit()
        await unlock(connection, key)
        _ = self._claims.pop(key, None)

    async def abandon(self, key: str) -> None:
        """Release an interrupted claim without deleting its checkpoint."""
        connection = self._claims.get(key)
        if connection is not None:
            await unlock(connection, key)
            _ = self._claims.pop(key, None)

    @property
    @override
    def engine(self) -> AsyncEngine:
        """Return the engine used by safe read-boundary operations."""
        return self._engine

    @override
    def _table(self, name: str) -> Table:
        return _METADATA.tables[name]
