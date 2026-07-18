"""Behavioral in-memory fake for the durable run repository."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Final

import anyio
from typing_extensions import override

from quantinue.core.contracts import (
    PipelineContext,
    PipelineRequest,
    PipelineRun,
    RunId,
    RunStatus,
)
from quantinue.db.active_snapshot import ActivePipelineSnapshot, active_pipeline_snapshot
from quantinue.db.contracts import (
    AppOrderExposureReservationOutcome,
    AppOrderExposureReservationResult,
    AppOrderExposureStatus,
    AppOrderExposureSummary,
    AttemptFailure,
    DailyOrderReservation,
    PersistedAttempt,
    RunClaim,
)
from quantinue.db.memory_completed_buy import MemoryCompletedBuyMixin
from quantinue.db.memory_exposure import (
    TERMINAL_APP_ORDER_STATUSES,
    AppOrderExposure,
    app_order_exposure_summary,
)
from quantinue.db.simulated_portfolio import (
    MarkSource,
    PortfolioMark,
    SimulatedFill,
    SimulatedOrder,
    SimulatedPortfolioSnapshot,
    ensure_fill_is_affordable,
    project_buy_only_portfolio,
)

_DEFAULT_OPENING_CASH: Final = Decimal("1000000.00")


class InMemoryRunStore(MemoryCompletedBuyMixin):
    """Mutable process-local fake with atomic claim and checkpoint semantics."""

    def __init__(self, opening_cash: Decimal = _DEFAULT_OPENING_CASH) -> None:
        """Create empty atomic fake state."""
        super().__init__()
        self._runs: dict[str, PipelineRun] = {}
        self._contexts: dict[str, PipelineContext] = {}
        self._attempts: dict[str, list[PersistedAttempt]] = {}
        self._active: dict[str, anyio.Event] = {}
        self._resumable: set[str] = set()
        self._daily_orders: dict[tuple[int, date], set[str]] = {}
        self._simulated_orders: dict[str, SimulatedOrder] = {}
        self._opening_cash = opening_cash

    async def initialize(self) -> None:
        """No initialization is required."""

    async def close(self) -> None:
        """No external resources are owned."""

    async def claim(
        self, key: str, request: PipelineRequest, *, resume_failed: bool = False
    ) -> RunClaim:
        """Claim a key once or expose its completed outcome."""
        async with self._lock:
            terminal = self._runs.get(key)
            if terminal is not None and not (resume_failed and key in self._resumable):
                return RunClaim(acquired=False, terminal_run=terminal)
            if key in self._active:
                return RunClaim(acquired=False)
            self._active[key] = anyio.Event()
            _ = self._runs.pop(key, None)
            context = self._contexts.get(key, PipelineContext(request=request))
            self._contexts[key] = context
            attempts = self._attempts.setdefault(key, [])
            now = datetime.now().astimezone()
            self._attempts[key] = [
                replace(
                    item,
                    status="failed",
                    finished_at=now,
                    error_code="ABANDONED_ATTEMPT",
                    error_message="prior owner exited before attempt finalization",
                )
                if item.status == "running"
                else item
                for item in attempts
            ]
            return RunClaim(acquired=True, context=context)

    async def wait_for_release(self, key: str) -> PipelineRun | None:
        """Wait until the current owner completes or abandons its claim."""
        async with self._lock:
            event = self._active.get(key)
            terminal = self._runs.get(key)
        if event is not None:
            await event.wait()
        return self._runs.get(key, terminal)

    async def start_attempt(
        self, key: str, component: str, started_at: datetime
    ) -> PersistedAttempt:
        """Append the next one-based attempt for a component."""
        attempts = self._attempts[key]
        number = 1 + sum(item.component == component for item in attempts)
        attempt = PersistedAttempt(component, number, "running", started_at)
        attempts.append(attempt)
        return attempt

    async def complete_stage(
        self, key: str, context: PipelineContext, attempt: PersistedAttempt
    ) -> None:
        """Atomically persist a completed attempt and its resulting context."""
        finished = replace(attempt, status="completed", finished_at=datetime.now().astimezone())
        attempts = self._attempts[key]
        attempts[attempts.index(attempt)] = finished
        self._contexts[key] = context

    async def fail_attempt(
        self,
        key: str,
        attempt: PersistedAttempt,
        finished_at: datetime,
        failure: AttemptFailure,
    ) -> None:
        """Persist a typed failure observation."""
        failed = replace(
            attempt,
            status=failure.status,
            finished_at=finished_at,
            error_code=failure.error_code,
            error_message=failure.error_message,
        )
        attempts = self._attempts[key]
        attempts[attempts.index(attempt)] = failed

    async def finish_run(self, key: str, run: PipelineRun, *, resumable: bool = False) -> None:
        """Publish the terminal snapshot and release waiters."""
        async with self._lock:
            self._runs[key] = run
            if resumable:
                self._resumable.add(key)
            else:
                self._resumable.discard(key)
            event = self._active.pop(key)
            event.set()

    async def abandon(self, key: str) -> None:
        """Release an interrupted claim while preserving its checkpoint."""
        async with self._lock:
            event = self._active.pop(key, None)
            if event is not None:
                event.set()

    async def get_by_key(self, key: str) -> PipelineRun | None:
        """Return a terminal run by key."""
        return self._runs.get(key)

    async def latest_cycle_ts(self) -> datetime | None:
        """Return the newest cycle timestamp not lost to failure."""
        async with self._lock:
            candidates = [
                run.cycle_ts
                for run in self._runs.values()
                if run.status is not RunStatus.FAILED
            ]
            candidates.extend(
                self._contexts[key].request.cycle_ts
                for key in self._active
                if key in self._contexts
            )
        return max(candidates, default=None)

    async def list_attempts(self, run_id: RunId) -> tuple[PersistedAttempt, ...]:
        """Return attempts for a run in insertion order."""
        for key, context in self._contexts.items():
            if context.run_id == run_id:
                return tuple(self._attempts[key])
        return ()

    async def list_recent(self, limit: int = 20) -> tuple[PipelineRun, ...]:
        """Return recent terminal runs."""
        return tuple(
            sorted(self._runs.values(), key=lambda run: run.cycle_ts, reverse=True)[:limit]
        )

    async def list_active(self, limit: int = 20) -> tuple[ActivePipelineSnapshot, ...]:
        """Return current claimed contexts with redacted attempt detail."""
        async with self._lock:
            active_contexts = tuple(
                (context, tuple(self._attempts[key]))
                for key, context in self._contexts.items()
                if key in self._active
            )
        ordered = sorted(active_contexts, key=lambda item: item[0].request.cycle_ts, reverse=True)
        return tuple(
            active_pipeline_snapshot(context, attempts) for context, attempts in ordered[:limit]
        )

    async def simulated_portfolio(self, opening_cash: Decimal) -> SimulatedPortfolioSnapshot:
        """Return the process-local simulated portfolio."""
        async with self._lock:
            orders = tuple(self._simulated_orders.values())
            fills = tuple(self._simulated_fills.values())
            marks = tuple(
                PortfolioMark(
                    ticker=context.request.ticker,
                    price=Decimal(str(context.last_price)),
                    source=MarkSource.COMPLETED_RUN,
                    as_of=context.request.cycle_ts,
                )
                for key, context in self._contexts.items()
                if (run := self._runs.get(key)) is not None
                and run.status is RunStatus.COMPLETED
                and context.last_price is not None
            )
        return project_buy_only_portfolio(opening_cash, orders, fills, marks)

    @override
    async def record_simulated_order(
        self,
        order: SimulatedOrder,
        fill: SimulatedFill | None,
    ) -> None:
        """Atomically retain one local order and its optional unique fill."""
        async with self._lock:
            if fill is not None and fill.fill_id not in self._simulated_fills:
                ensure_fill_is_affordable(
                    self._opening_cash, tuple(self._simulated_fills.values()), fill
                )
            _ = self._simulated_orders.setdefault(order.order_id, order)
            if fill is not None:
                _ = self._simulated_fills.setdefault(fill.fill_id, fill)

    async def reserve_daily_new_order(
        self, request: DailyOrderReservation
    ) -> AppOrderExposureReservationResult:
        """Reserve one canonical identity under the daily and app-exposure caps."""
        async with self._lock:
            existing = self._app_order_exposures.get(request.idempotency_key)
            if existing is not None:
                if existing.request != request:
                    return AppOrderExposureReservationResult(
                        outcome=AppOrderExposureReservationOutcome.REJECTED,
                        summary=app_order_exposure_summary(
                            self._app_order_exposures.values(),
                            request.account_id,
                            request.max_app_order_exposure_usd,
                        ),
                    )
                return AppOrderExposureReservationResult(
                    outcome=AppOrderExposureReservationOutcome.REPLAYED,
                    summary=app_order_exposure_summary(
                        self._app_order_exposures.values(),
                        existing.request.account_id,
                        request.max_app_order_exposure_usd,
                    ),
                )
            identities = self._daily_orders.setdefault(
                (request.account_id, request.trade_date), set()
            )
            if len(identities) >= request.cap:
                return AppOrderExposureReservationResult(
                    outcome=AppOrderExposureReservationOutcome.REJECTED,
                    summary=app_order_exposure_summary(
                        self._app_order_exposures.values(),
                        request.account_id,
                        request.max_app_order_exposure_usd,
                    ),
                )
            summary = app_order_exposure_summary(
                self._app_order_exposures.values(),
                request.account_id,
                request.max_app_order_exposure_usd,
            )
            if summary.planned_or_reserved + request.reference_notional > summary.cap:
                return AppOrderExposureReservationResult(
                    outcome=AppOrderExposureReservationOutcome.REJECTED,
                    summary=summary,
                )
            identities.add(request.idempotency_key)
            self._app_order_exposures[request.idempotency_key] = AppOrderExposure(
                request=request,
                status=AppOrderExposureStatus.PLANNED,
            )
            return AppOrderExposureReservationResult(
                outcome=AppOrderExposureReservationOutcome.ACQUIRED,
                summary=app_order_exposure_summary(
                    self._app_order_exposures.values(),
                    request.account_id,
                    request.max_app_order_exposure_usd,
                ),
            )

    async def app_order_exposure_summary(
        self, account_id: int, cap: Decimal
    ) -> AppOrderExposureSummary:
        """Return app-owned eligible reference exposure for one account."""
        async with self._lock:
            return app_order_exposure_summary(self._app_order_exposures.values(), account_id, cap)

    async def reconcile_app_order_exposure(
        self, idempotency_key: str, status: AppOrderExposureStatus
    ) -> AppOrderExposureSummary | None:
        """Replace one canonical order's lifecycle state without adding exposure."""
        async with self._lock:
            existing = self._app_order_exposures.get(idempotency_key)
            if existing is None:
                return None
            updated = (
                existing
                if existing.status in TERMINAL_APP_ORDER_STATUSES
                else replace(existing, status=status)
            )
            self._app_order_exposures[idempotency_key] = updated
            return app_order_exposure_summary(
                self._app_order_exposures.values(),
                updated.request.account_id,
                updated.request.max_app_order_exposure_usd,
            )
