"""Combine upstream evidence into one buy or hold proposal."""

from dataclasses import dataclass, replace
from typing import ClassVar, Final

from quantinue.core.contracts import PipelineContext
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.core.typing import require_value
from quantinue.llm.provider import AnalysisTask, LlmAnalyzer
from quantinue.orchestration.policy import GatesConfig, ProfileConfig
from quantinue.roles.role_07_strategist.contracts import StrategyInput, StrategyOutput

DEFAULT_GATES: Final[GatesConfig] = GatesConfig()
DEFAULT_PROFILE: Final[ProfileConfig] = ProfileConfig()


@dataclass(frozen=True, slots=True)
class Strategist:
    """Strategy proposal layer with deterministic code gates."""

    analyzer: LlmAnalyzer
    minimum_confidence: float = 0.60
    strategist_buy_score: float = 0.70
    gates: GatesConfig = DEFAULT_GATES
    profile: ProfileConfig = DEFAULT_PROFILE
    # `profile`은 문턱 값만 들고 자기 이름을 모른다(yaml에서 이름은 선언 키다).
    # 그런데 원장은 이름으로 행을 가른다 — 그래서 값과 이름을 함께 주입받고,
    # 판단이 끝나면 이름을 결과에 남긴다. 기본값이 실제로 쓰이는 일이 없도록
    # 조립 경로를 테스트가 고정한다(test_signal_inv_type.py).
    profile_name: str = "aggressive"
    component: ClassVar[str] = "07"
    name: ClassVar[str] = "전략 종합"

    async def execute(self, context: PipelineContext) -> PipelineContext:
        """Ask for analysis, then let code decide whether the gate passes."""
        technical = require_value(
            context.technical_score, component=self.component, field_name="technical_score"
        )
        # 공시는 없을 수 있다 — 부재는 기권이지 악재가 아니다(role_05 미해결 CIK·무공시).
        disclosure = context.disclosure_score
        news = require_value(context.news_score, component=self.component, field_name="news_score")
        strategy_input = StrategyInput(
            run_id=str(context.run_id),
            ticker=context.request.ticker,
            cycle_ts=context.request.cycle_ts,
            technical_score=technical,
            disclosure_score=disclosure,
            news_score=news,
            is_daily_pick=context.is_daily_pick,
            macro_risk_score=context.macro_risk_score or 0.0,
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
        conviction = StrategyOutput.vote_conviction(
            strategy_input, self.gates, model_result.score
        )
        gated = StrategyOutput.from_model(
            strategy_input,
            conviction,
            model_result.reason,
            gates=self.gates,
            profile=self.profile,
        )
        side = gated.side
        updated = replace(
            context,
            conviction=conviction,
            side=side,
            inv_type=self.profile_name,
            strategy_output=gated,
            signal_consensus=StrategyOutput.vote_consensus(
                strategy_input, self.gates, self.profile, model_result.score
            ),
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
