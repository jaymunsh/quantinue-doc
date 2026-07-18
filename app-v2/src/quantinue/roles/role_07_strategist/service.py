"""Combine upstream evidence into one buy or hold proposal."""

from dataclasses import dataclass, replace
from typing import ClassVar

from quantinue.core.contracts import PipelineContext
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.core.typing import require_value
from quantinue.llm.provider import AnalysisTask, LlmAnalyzer
from quantinue.roles.role_07_strategist.contracts import StrategyInput, StrategyOutput


@dataclass(frozen=True, slots=True)
class Strategist:
    """Strategy proposal layer with deterministic code gates."""

    analyzer: LlmAnalyzer
    minimum_confidence: float = 0.60
    strategist_buy_score: float = 0.70
    component: ClassVar[str] = "07"
    name: ClassVar[str] = "전략 종합"

    async def execute(self, context: PipelineContext) -> PipelineContext:
        """Ask for analysis, then let code decide whether the gate passes."""
        technical = require_value(
            context.technical_score, component=self.component, field_name="technical_score"
        )
        disclosure = require_value(
            context.disclosure_score, component=self.component, field_name="disclosure_score"
        )
        news = require_value(context.news_score, component=self.component, field_name="news_score")
        strategy_input = StrategyInput(
            run_id=str(context.run_id),
            ticker=context.request.ticker,
            cycle_ts=context.request.cycle_ts,
            technical_score=technical,
            disclosure_score=disclosure,
            news_score=news,
            is_daily_pick=context.is_daily_pick,
            disclosure_snapshot_at=context.request.cycle_ts,
            news_snapshot_at=context.request.cycle_ts,
            evidence_ids=(
                f"{context.run_id}:technical",
                f"{context.run_id}:disclosure",
                f"{context.run_id}:news",
            ),
        )
        model_result = await self.analyzer.analyze(
            AnalysisTask.STRATEGY,
            f"technical={technical}, disclosure={disclosure}, news={news}",
        )
        conviction = round((technical + disclosure + news + model_result.score) / 4, 3)
        gated = StrategyOutput.from_model(
            strategy_input,
            conviction,
            model_result.reason,
            max(self.minimum_confidence, self.strategist_buy_score),
        )
        side = gated.side
        updated = replace(
            context,
            conviction=conviction,
            side=side,
            strategy_output=gated,
        )
        metadata = model_result.metadata
        evidence = Evidence(
            evidence_id=f"{context.run_id}:07:strategy",
            run_id=context.run_id,
            source="strategy-model",
            source_ref="policy://strategy/v1",
            observed_at=context.request.cycle_ts,
            captured_at=context.request.cycle_ts,
            confidence=conviction,
            kind=EvidenceKind.MODEL_OUTPUT,
            parent_evidence_ids=tuple(
                context.evidence_trace[index].evidence_id for index in (1, 4, 5)
            ),
            model_name=metadata.model,
            model_provider=metadata.provider,
            prompt_version=metadata.prompt_version,
            policy_version=metadata.policy_version,
            input_hash=metadata.input_hash,
        )
        return updated.add_stage(
            self.component, self.name, f"{side} 제안, 확신도 {conviction:.3f}", evidence=evidence
        )
