"""Schedule compatible pipeline reviews and score completed T+5 inputs."""

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import ClassVar

from typing_extensions import override

from quantinue.core.contracts import PipelineContext, ReviewResult
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.core.typing import require_value
from quantinue.roles.role_11_reviewer.calendar import Clock, TradingCalendar
from quantinue.roles.role_11_reviewer.contracts import ReviewInput, ReviewOutput


@dataclass(frozen=True, slots=True)
class ReviewScheduler:
    """Determine readiness from an injected clock and exchange calendar."""

    calendar: TradingCalendar
    clock: Clock

    def due_at(self, trade_date: date) -> datetime:
        """Return the UTC close instant of the fifth subsequent session."""
        due_date = self.calendar.offset(trade_date, trading_days=5)
        return self.calendar.session_close(due_date)

    def is_ready(self, trade_date: date) -> bool:
        """Return true only once the T+5 regular close is final."""
        return self.clock.now() >= self.due_at(trade_date)


@dataclass(frozen=True, slots=True)
class ReviewNotReadyError(RuntimeError):
    """T+5 close is not final according to the injected dependencies."""

    due_at: datetime
    current: datetime

    @override
    def __str__(self) -> str:
        """Describe the unavailable review window."""
        return f"review is due at {self.due_at.isoformat()}, current={self.current.isoformat()}"


@dataclass(frozen=True, slots=True)
class ReviewScorer:
    """Calendar-gated scorer; output owns all numeric computation."""

    calendar: TradingCalendar
    clock: Clock

    def score(self, review_input: ReviewInput, *, lesson: str) -> ReviewOutput:
        """Calculate percentage returns, hit policy, and maximum drawdown."""
        validated = review_input.validated_for(self.calendar)
        scheduler = ReviewScheduler(self.calendar, self.clock)
        current = self.clock.now()
        due_at = scheduler.due_at(validated.signal.trade_date)
        if current < due_at:
            raise ReviewNotReadyError(due_at, current)
        return ReviewOutput(
            review_input=validated,
            reviewed_at=current,
            lesson=lesson,
        )


class Reviewer:
    """Schedule a T+5 review while preserving the filled entry price."""

    component: ClassVar[str] = "11"
    name: ClassVar[str] = "리뷰·회고"

    async def execute(self, context: PipelineContext) -> PipelineContext:
        """Create a pending review linked to the normalized fill."""
        if context.side == "hold" or not context.critic_approved or not context.quantity:
            reason = "주문 없음 · 정상 완료"
            review = ReviewResult(outcome="no_trade", summary=reason)
            updated = replace(context, review=review)
            evidence = Evidence(
                evidence_id=f"{context.run_id}:11:no-trade",
                run_id=context.run_id,
                source="review-scheduler-code",
                source_ref="policy://review/no-trade-v1",
                observed_at=context.request.cycle_ts,
                captured_at=context.request.cycle_ts,
                confidence=1.0,
                kind=EvidenceKind.MODEL_OUTPUT,
                parent_evidence_ids=(context.evidence_trace[-1].evidence_id,)
                if context.evidence_trace
                else (),
            )
            return updated.add_stage(self.component, self.name, review.summary, evidence=evidence)
        order = require_value(context.order, component=self.component, field_name="order")
        review = ReviewResult(
            outcome="pending_t_plus_5",
            summary=f"진입가 {order.filled_avg_price:.2f} 기준 T+5 평가 대기",
        )
        updated = replace(context, review=review)
        evidence = Evidence(
            evidence_id=f"{context.run_id}:11:review",
            run_id=context.run_id,
            source="review-scheduler-code",
            source_ref="policy://review/t-plus-5-v1",
            observed_at=context.request.cycle_ts,
            captured_at=context.request.cycle_ts,
            confidence=1.0,
            kind=EvidenceKind.MODEL_OUTPUT,
            parent_evidence_ids=(context.evidence_trace[-1].evidence_id,),
        )
        return updated.add_stage(self.component, self.name, review.summary, evidence=evidence)
