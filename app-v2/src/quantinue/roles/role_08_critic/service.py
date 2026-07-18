"""Challenge the strategist proposal before risk execution."""

from dataclasses import dataclass, replace
from typing import ClassVar, Literal

from quantinue.core.contracts import PipelineContext
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.core.typing import require_value
from quantinue.llm.provider import AnalysisTask, LlmAnalyzer
from quantinue.roles.role_08_critic.contracts import CriticInput, CriticVerdict


@dataclass(frozen=True, slots=True)
class Critic:
    """Independent challenge layer with a hard code threshold."""

    analyzer: LlmAnalyzer
    minimum_confidence: float = 0.0
    critic_approval_score: float = 0.70
    component: ClassVar[str] = "08"
    name: ClassVar[str] = "크리틱 검증"

    async def execute(self, context: PipelineContext) -> PipelineContext:
        """Approve only buy proposals with a passing structured critique."""
        side = require_value(context.side, component=self.component, field_name="side")
        if side != "buy":
            verdict = CriticVerdict(
                run_id=str(context.run_id),
                signal_id=context.signal_id or 1,
                ticker=context.request.ticker,
                decision="hold",
                category="no_buy_proposal",
                objection="매수 제안 없음",
                confidence=1.0,
                decided_layer="gate",
                evidence_ids=(f"{context.run_id}:strategy",),
            )
            updated = replace(context, critic_approved=False, critic_verdict=verdict)
            return updated.add_stage(self.component, self.name, "크리틱 차단, 매수 제안 없음")
        price = require_value(context.last_price, component=self.component, field_name="last_price")
        critic_input = CriticInput(
            run_id=str(context.run_id),
            signal_id=1,
            ticker=context.request.ticker,
            cycle_ts=context.request.cycle_ts,
            conviction=require_value(
                context.conviction, component=self.component, field_name="conviction"
            ),
            current_price=price,
            day_high=price * 1.01,
            day_low=price * 0.99,
            close_prev=price,
            macro_regime=_critic_macro_regime(context.macro_regime),
            disclosure_filing_no=(
                context.disclosure_source.filing_no
                if context.disclosure_source is not None
                else None
            ),
            disclosure_filed_at=(
                context.disclosure_source.filed_at
                if context.disclosure_source is not None
                else None
            ),
            news_published_at=(
                context.news_source.published_at if context.news_source is not None else None
            ),
            evidence_ids=(
                f"{context.run_id}:strategy",
                f"{context.run_id}:disclosure",
                f"{context.run_id}:news",
            ),
        )
        hard_verdict = CriticVerdict.apply_hard_gates(critic_input)
        if hard_verdict is not None:
            updated = replace(context, critic_approved=False, critic_verdict=hard_verdict)
            return updated.add_stage(
                self.component,
                self.name,
                f"크리틱 차단, {hard_verdict.category}",
            )
        result = await self.analyzer.analyze(AnalysisTask.CRITIC, f"proposal={side}")
        approval_threshold = max(self.minimum_confidence, self.critic_approval_score)
        approved = side == "buy" and result.score >= approval_threshold
        verdict = CriticVerdict(
            run_id=str(context.run_id),
            signal_id=context.signal_id or 1,
            ticker=context.request.ticker,
            decision="pass" if approved else "reject",
            category=None if approved else "model_threshold",
            objection=result.reason,
            confidence=1.0 - result.score if approved else result.score,
            decided_layer="gate" if approved else "llm",
            evidence_ids=critic_input.evidence_ids,
        )
        updated = replace(context, critic_approved=approved, critic_verdict=verdict)
        label = "승인" if approved else "차단"
        metadata = result.metadata
        evidence = Evidence(
            evidence_id=f"{context.run_id}:08:critic",
            run_id=context.run_id,
            source="critic-model",
            source_ref="policy://critic/v1",
            observed_at=context.request.cycle_ts,
            captured_at=context.request.cycle_ts,
            confidence=result.score,
            kind=EvidenceKind.MODEL_OUTPUT,
            parent_evidence_ids=(context.evidence_trace[-1].evidence_id,),
            model_name=metadata.model,
            model_provider=metadata.provider,
            prompt_version=metadata.prompt_version,
            policy_version=metadata.policy_version,
            input_hash=metadata.input_hash,
        )
        return updated.add_stage(
            self.component,
            self.name,
            f"크리틱 {label}, 점수 {result.score:.2f}",
            evidence=evidence,
        )


def _critic_macro_regime(regime: str | None) -> Literal["risk_on", "neutral", "risk_off"]:
    match regime:
        case "risk_on":
            return "risk_on"
        case "risk_off":
            return "risk_off"
        case _:
            return "neutral"
