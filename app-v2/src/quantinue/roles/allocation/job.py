"""The allocation job: put the whole candidate pool before each account, once a day.

구 러너에는 이 단계가 없었다 — 종목당 한 번 돌아서 "이 종목을 살까"만 물었고,
"오늘 승인된 후보 전체 중 **어느 N개**를 살까"는 아무도 묻지 않았다(재설계
§7의 배분 단계 부재 진단). 여기서는 후보 집합 전체를 계좌별로 놓고 확신도
순서로 지갑이 허락할 때까지 산다.

판단 규칙은 새로 만들지 않았다 — 게이트와 사이징은 role_09의
``build_order_plan``(E2E-5로 증명된 메커니즘)을 그대로 부른다. 새로운 것은
**순차 상태 갱신** 하나다: 한 종목을 살 때마다 현금·보유수가 줄므로, 후보마다
계좌를 다시 읽지 않으면 모든 후보가 첫 잔고 기준으로 통과해 지갑보다 많이 산다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

from quantinue.broker.provider import OrderPlan
from quantinue.core.market_calendar import NyseCalendar
from quantinue.core.ontology import EvidenceKind, FillSide
from quantinue.core.order_identity import derive_client_order_id
from quantinue.core.schemas import Evidence
from quantinue.db.contracts import (
    AppOrderExposureReservationOutcome,
    DailyOrderReservation,
    parse_app_order_money,
)
from quantinue.db.domain_records import CompletedFillWrite, OrderPlanWrite
from quantinue.roles.role_09_risk_portfolio.contracts import (
    RiskPortfolioInput,
    build_order_plan,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from quantinue.db.domain_records import AccountRiskState, BuyCandidate
    from quantinue.orchestration.policy import (
        AllocationConfig,
        GatesConfig,
        ProfileConfig,
    )


@dataclass(frozen=True, slots=True)
class AllocationJob:
    """Turn today's approved buy candidates into sized, filled bracket orders."""

    store: object
    broker: object
    profiles: Mapping[str, ProfileConfig]
    gates: GatesConfig
    allocation: AllocationConfig
    calendar: NyseCalendar = field(default_factory=NyseCalendar)

    async def run(self, *, as_of: date) -> str:
        """Allocate for every subscribed account, sequentially and idempotently."""
        domain = getattr(self.store, "domain", self.store)
        session = self.calendar.previous_trading_day(as_of)
        # D8의 프로덕션 소비자가 여기다. 시가평가 없이 배분하면 사이징의
        # 분모(equity)가 최초 자본에 동결된 채로 남는다.
        _ = await domain.revalue_accounts(session)
        # 스냅샷은 revalue 직후·매수 전이다 — "당일 시작"의 정의. 첫 기록이
        # 이기므로(도메인 주석) 재실행이 아침 값을 덮지 않는다.
        day_start = await domain.snapshot_daily_equity(as_of)
        candidates = await domain.approved_buy_candidates(as_of)
        if not candidates:
            return "no approved buys"
        macro = await domain.latest_macro(as_of, self.gates.evidence_max_age_minutes)
        held = await self._held_by_account(domain)
        bought = 0
        skipped = 0
        for account in await domain.active_accounts():
            profile = self.profiles.get(account.inv_type or "")
            if profile is None:
                # 성향 없는 계좌(테스트 계좌 등)는 배분을 구독하지 않는다.
                continue
            pool = candidates.get(account.inv_type or "", ())
            outcome = await self._allocate_account(
                domain,
                account=account,
                profile=profile,
                pool=pool,
                as_of=as_of,
                day_start=day_start.get(account.account_id),
                risk_score=0.0 if macro is None else macro.risk_score,
                held=held.get(account.account_id, frozenset()),
            )
            bought += outcome[0]
            skipped += outcome[1]
        return f"{bought} bought, {skipped} skipped"

    async def _held_by_account(self, domain: object) -> dict[int, frozenset[str]]:
        """Which tickers each account already owns — a re-approval is not a buy."""
        reader = getattr(domain, "open_positions", None)
        if reader is None:
            return {}
        found: dict[int, set[str]] = {}
        for position in await reader():
            found.setdefault(position.account_id, set()).add(position.ticker)
        return {account: frozenset(tickers) for account, tickers in found.items()}

    async def _allocate_account(  # noqa: PLR0913 - 계좌 하나를 판단하는 데 필요한 사실들이다
        self,
        domain: object,
        *,
        account: AccountRiskState,
        profile: ProfileConfig,
        pool: tuple[BuyCandidate, ...],
        as_of: date,
        day_start: Decimal | None,
        risk_score: float,
        held: frozenset[str],
    ) -> tuple[int, int]:
        """Walk one account through the pool, re-reading the ledger between buys."""
        if not pool:
            return (0, 0)
        run_id = f"allocation:{as_of.isoformat()}"
        cycle_ts = datetime.combine(as_of, time(), tzinfo=UTC)
        # equity는 이 루프 동안 불변이다 — 매수는 현금을 포지션으로 바꿀 뿐
        # 평가액을 바꾸지 않는다. 그래서 손실 한도는 루프 밖에서 한 번만 잰다.
        state = await domain.account_risk_state(account.account_id)
        if state is None or state.equity <= 0:
            return (0, 0)
        halted = day_start is not None and state.equity < day_start * (
            1 - Decimal(str(profile.daily_loss_limit))
        )
        owned = set(held)
        bought = 0
        skipped = 0
        for candidate in pool:
            if halted:
                # 한도를 넘긴 날은 근거가 아무리 좋아도 신규 매수가 없다.
                # 행마다 남기는 이유: 안 남기면 "그날 왜 아무것도 안 샀나"에
                # 원장이 답할 수 없다.
                await self._record_plan(
                    domain, candidate, account.account_id, run_id, cycle_ts,
                    decision="skipped", quantity=0, reason="daily_loss_limit",
                )
                skipped += 1
                continue
            if bought > 0:
                # 순차 갱신 — 방금의 매수가 현금·보유수를 바꿨다. 원장을 다시
                # 읽는 쪽을 골랐다: 체결 기록이 이미 원장을 옮겼으므로 앱
                # 메모리로 따라 계산하면 두 장부가 생긴다.
                state = await domain.account_risk_state(account.account_id)
                if state is None:
                    break
            price = float(candidate.reference_price)
            plan = build_order_plan(
                RiskPortfolioInput(
                    run_id=run_id,
                    execution_at=cycle_ts,
                    evidence=(self._evidence(run_id, candidate, account.account_id, cycle_ts),),
                    signal_id=candidate.signal_id,
                    account_id=account.account_id,
                    ticker=candidate.ticker,
                    cycle_ts=cycle_ts,
                    critic_approved=True,  # 리더의 조인 조건이 곧 승인이다
                    current_price=price,
                    equity=float(state.equity),
                    cash=float(state.cash),
                    has_position=candidate.ticker in owned,
                    open_position_count=state.open_position_count,
                    daily_new_order_count=bought,
                    daily_new_order_cap=self.allocation.daily_new_order_cap,
                    risk_score=risk_score,
                    # 장중 갭 가드는 일 1회 경로에 잴 대상이 없다(기준가가 곧
                    # 직전 종가다). None이면 게이트가 발동하지 않는다.
                    reference_gap=None,
                    recent_return=candidate.recent_return,
                ),
                stop_loss_ratio=self.allocation.stop_loss_ratio,
                take_profit_ratio=self.allocation.take_profit_ratio,
                maximum_risk_score=self.allocation.maximum_risk_score,
                late_entry_max=profile.late_entry_max,
                profile=profile,
            )
            reason = plan.skipped_reason
            quantity = plan.quantity
            if quantity > 0:
                executed = await self._execute(plan, state.equity, as_of, cycle_ts)
                if executed:
                    owned.add(candidate.ticker)
                    bought += 1
                else:
                    reason = "daily_order_cap"
                    quantity = 0
            if quantity == 0:
                skipped += 1
            await self._record_plan(
                domain, candidate, account.account_id, run_id, cycle_ts,
                decision="planned" if quantity > 0 else "skipped",
                quantity=quantity, reason=reason if quantity == 0 else None,
                entry=plan.entry_price, stop=plan.stop_loss, take=plan.take_profit,
            )
        return (bought, skipped)

    async def _execute(
        self, plan: object, equity: Decimal, as_of: date, cycle_ts: datetime
    ) -> bool:
        """Reserve, fill, and book one bracket — every step idempotent.

        순서는 청산 잡과 같은 이유로 예약 → 브로커 → 체결이다: 브로커가 체결한
        뒤 원장 자리가 없는 상태를 만들지 않는다.
        """
        client_order_id = derive_client_order_id(
            account_id=plan.account_id, signal_id=plan.signal_id
        )
        reserved = await self.store.reserve_daily_new_order(
            DailyOrderReservation(
                account_id=plan.account_id,
                trade_date=as_of,
                signal_id=plan.signal_id,
                idempotency_key=client_order_id,
                ticker=plan.ticker,
                quantity=plan.quantity,
                entry_price=parse_app_order_money(plan.entry_price),
                stop_price=parse_app_order_money(plan.stop_loss),
                take_profit_price=parse_app_order_money(plan.take_profit),
                cap=self.allocation.daily_new_order_cap,
                # 노출 천장은 계좌 자본이다(role_09와 같은 이유) — 전역 상수를
                # 쓰면 자본이 다른 계좌들이 하나의 천장을 나눠 쓴다.
                max_app_order_exposure_usd=equity,
            )
        )
        if reserved.outcome is AppOrderExposureReservationOutcome.REJECTED:
            return False
        result = await self.broker.submit(
            OrderPlan(
                ticker=plan.ticker,
                client_order_id=client_order_id,
                quantity=plan.quantity,
                entry_price=plan.entry_price,
                stop_loss=plan.stop_loss,
                take_profit=plan.take_profit,
            )
        )
        domain = getattr(self.store, "domain", self.store)
        _ = await domain.record_completed_fill(
            CompletedFillWrite(
                idempotency_key=client_order_id,
                broker_order_id=result.order_id,
                broker_fill_id=f"{result.order_id}-fill",
                quantity=result.quantity,
                price=Decimal(str(result.filled_avg_price)),
                filled_at=cycle_ts,
                side=FillSide.BUY,
            )
        )
        return True

    def _evidence(
        self, run_id: str, candidate: BuyCandidate, account_id: int, cycle_ts: datetime
    ) -> Evidence:
        """One decision-time evidence row tying the plan back to its signal."""
        return Evidence(
            evidence_id=f"{run_id}:{account_id}:{candidate.ticker}",
            run_id=run_id,
            source="allocation-job",
            source_ref=f"signal://{candidate.signal_id}",
            observed_at=cycle_ts,
            captured_at=cycle_ts,
            confidence=1.0,
            kind=EvidenceKind.MODEL_OUTPUT,
        )

    async def _record_plan(  # noqa: PLR0913 - 원장 한 행의 컬럼들이다
        self,
        domain: object,
        candidate: BuyCandidate,
        account_id: int,
        run_id: str,
        cycle_ts: datetime,
        *,
        decision: str,
        quantity: int,
        reason: str | None,
        entry: float | None = None,
        stop: float | None = None,
        take: float | None = None,
    ) -> None:
        """Leave the decision in tb_order_plan — blocked buys must be countable."""
        await domain.save_order_plan(
            OrderPlanWrite(
                run_id=run_id,
                ticker=candidate.ticker,
                cycle_ts=cycle_ts,
                trade_date=cycle_ts.date(),
                account_id=account_id,
                signal_id=candidate.signal_id,
                decision=decision,
                quantity=quantity,
                skipped_reason=reason,
                entry_price=None if entry is None else Decimal(str(entry)),
                stop_price=None if stop is None else Decimal(str(stop)),
                take_profit_price=None if take is None else Decimal(str(take)),
            )
        )
