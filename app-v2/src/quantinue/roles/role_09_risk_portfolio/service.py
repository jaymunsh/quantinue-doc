"""Create a deterministic risk-sized fixed-bracket order plan."""

from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
from typing import ClassVar, assert_never

from quantinue.core.contracts import PipelineContext
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.core.typing import require_value
from quantinue.db.contracts import (
    AppOrderExposureReservationOutcome,
    DailyOrderReservation,
    RunStore,
    parse_app_order_money,
)
from quantinue.roles.role_09_risk_portfolio.contracts import RiskPortfolioInput, build_order_plan
from quantinue.roles.role_09_risk_portfolio.evidence import evidence_from_pipeline_traces


@dataclass(frozen=True, slots=True)
class RiskPortfolio:
    """Deterministic risk gate between LLM output and broker submission."""

    component: ClassVar[str] = "09"
    name: ClassVar[str] = "리스크·포트폴리오"
    store: RunStore
    daily_new_order_cap: int = 1
    max_app_order_exposure_usd: Decimal = Decimal("1000.00")
    maximum_risk_score: float = 1.0
    stop_loss_ratio: float = 0.15
    take_profit_ratio: float = 0.20

    async def execute(self, context: PipelineContext) -> PipelineContext:
        """Apply role 09's hard gates and risk-budget sizing formula."""
        price = require_value(context.last_price, component=self.component, field_name="last_price")
        signal_key = f"{context.request.ticker}:{context.request.cycle_ts.isoformat()}".encode()
        signal_id = context.signal_id or int(sha256(signal_key).hexdigest()[:8], 16) + 1
        account_id = context.account_id or 1
        plan = build_order_plan(
            RiskPortfolioInput(
                run_id=context.run_id,
                execution_at=context.request.cycle_ts,
                evidence=evidence_from_pipeline_traces(context, ("08",)),
                signal_id=signal_id,
                account_id=account_id,
                ticker=context.request.ticker,
                cycle_ts=context.request.cycle_ts,
                critic_approved=context.critic_approved,
                current_price=price,
                equity=float(self.max_app_order_exposure_usd),
                daily_new_order_cap=self.daily_new_order_cap,
                risk_score=context.macro_risk_score or 0,
            ),
            stop_loss_ratio=self.stop_loss_ratio,
            take_profit_ratio=self.take_profit_ratio,
            maximum_risk_score=self.maximum_risk_score,
        )
        if plan.quantity > 0:
            reserved = await self.store.reserve_daily_new_order(
                DailyOrderReservation(
                    account_id=plan.account_id,
                    trade_date=context.request.cycle_ts.date(),
                    signal_id=plan.signal_id,
                    idempotency_key=f"q-a{plan.account_id}-s{plan.signal_id}",
                    ticker=plan.ticker,
                    quantity=plan.quantity,
                    entry_price=parse_app_order_money(plan.entry_price),
                    stop_price=parse_app_order_money(plan.stop_loss),
                    take_profit_price=parse_app_order_money(plan.take_profit),
                    cap=self.daily_new_order_cap,
                    max_app_order_exposure_usd=self.max_app_order_exposure_usd,
                )
            )
            match reserved.outcome:
                case (
                    AppOrderExposureReservationOutcome.ACQUIRED
                    | AppOrderExposureReservationOutcome.REPLAYED
                ):
                    pass
                case AppOrderExposureReservationOutcome.REJECTED:
                    plan = plan.model_copy(
                        update={
                            "decision": "skipped",
                            "quantity": 0,
                            "skipped_reason": "daily_order_cap",
                        }
                    )
                case unreachable:
                    assert_never(unreachable)
        summary = f"수량={plan.quantity} · 손절={plan.stop_loss} · 익절\u00a0{plan.take_profit}"
        if plan.quantity == 0:
            summary = f"주문 보류 · {summary}"
        if plan.quantity == 0 and plan.skipped_reason == "daily_order_cap":
            summary = "수량 0, 앱 주문 계획 노출 한도 또는 일일 신규 주문 한도 도달"
        updated = replace(
            context,
            signal_id=plan.signal_id,
            account_id=plan.account_id,
            quantity=plan.quantity,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            risk_decision=plan.decision,
            risk_skipped_reason=plan.skipped_reason,
            risk_entry_price=plan.entry_price,
        )
        evidence = Evidence(
            evidence_id=f"{context.run_id}:09:risk-plan",
            run_id=context.run_id,
            source="risk-policy-code",
            source_ref="policy://risk-portfolio/v1",
            observed_at=context.request.cycle_ts,
            captured_at=context.request.cycle_ts,
            confidence=1.0,
            kind=EvidenceKind.MODEL_OUTPUT,
            parent_evidence_ids=(context.evidence_trace[-1].evidence_id,),
        )
        return updated.add_stage(
            self.component,
            self.name,
            summary,
            evidence=evidence,
        )
