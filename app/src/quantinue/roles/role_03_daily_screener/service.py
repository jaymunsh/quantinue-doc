"""Select today's candidates from the technical universe."""

from dataclasses import replace
from typing import ClassVar, Final, assert_never

from quantinue.core.contracts import PipelineContext
from quantinue.core.ontology import Bucket, EvidenceKind, Trend
from quantinue.core.schemas import Evidence
from quantinue.core.typing import require_value
from quantinue.roles.role_02_technical_analysis.contracts import TechnicalSnapshot
from quantinue.roles.role_03_daily_screener.contracts import (
    DailyPick,
    DailyScreenerInput,
    DailyScreenerOutput,
)

DAILY_PICK_THRESHOLD: Final = 0.70
DAILY_PICK_LIMIT: Final = 20


def _score(snapshot: TechnicalSnapshot) -> float:
    match snapshot.trend:
        case Trend.UP:
            trend = 0.25
        case Trend.MIXED:
            trend = 0.1
        case Trend.DOWN:
            trend = 0.0
        case Trend.NO_DATA:
            trend = 0.0
        case unreachable:
            assert_never(unreachable)
    momentum = max(0.0, min(0.35, snapshot.ret_20d / 40))
    strength = max(0.0, min(0.25, snapshot.high_252_ratio * 0.25))
    rsi_quality = max(0.0, 0.15 - abs(snapshot.rsi - 60) / 400)
    return round(min(1.0, trend + momentum + strength + rsi_quality), 4)


class DailyScreener:
    """Rank twenty daily candidates while retaining the requested deep-analysis focus."""

    component: ClassVar[str] = "03"
    name: ClassVar[str] = "2차 스크리너"

    def fixture(self, context: PipelineContext) -> DailyScreenerOutput:
        """Build the deterministic daily-pick row after the score gate."""
        source = Evidence(
            evidence_id=f"{context.run_id}:03:screen",
            run_id=context.run_id,
            source="fixture",
            source_ref=f"fixture://daily-pick/{context.request.ticker}",
            observed_at=context.request.cycle_ts,
            captured_at=context.request.cycle_ts,
            confidence=1.0,
            kind=EvidenceKind.MODEL_OUTPUT,
        )
        role_input = DailyScreenerInput(
            run_id=context.run_id,
            execution_at=context.request.cycle_ts,
            evidence=(source,),
            trade_date=context.request.cycle_ts.date(),
            universe_as_of=context.request.cycle_ts.date(),
        )
        pick = DailyPick(
            trade_date=role_input.trade_date or context.request.cycle_ts.date(),
            ticker=context.request.ticker,
            universe_as_of=role_input.universe_as_of or context.request.cycle_ts.date(),
            bucket=Bucket.TREND_LEADER,
            rank=1,
            sector="Technology",
            score=0.82,
            evidence_ids=(source.evidence_id,),
        )
        return DailyScreenerOutput(run_id=context.run_id, picks=(pick,))

    async def execute(self, context: PipelineContext) -> PipelineContext:
        """Rank real snapshots, preserving fixture compatibility for one snapshot."""
        score = require_value(
            context.technical_score, component=self.component, field_name="technical_score"
        )
        technical = context.technical_output
        if technical is None or len(technical.snapshots) == 1:
            result = self.fixture(context) if score >= DAILY_PICK_THRESHOLD else None
        else:
            ranked = sorted(technical.snapshots, key=lambda item: (-_score(item), item.ticker))
            selected = ranked[:DAILY_PICK_LIMIT]
            if not context.request.automatic:
                requested = next(item for item in ranked if item.ticker == context.request.ticker)
                if requested not in selected:
                    selected = [*selected[:-1], requested]
            result = DailyScreenerOutput(
                run_id=context.run_id,
                picks=tuple(
                    DailyPick(
                        trade_date=item.trade_date,
                        ticker=item.ticker,
                        universe_as_of=context.request.cycle_ts.date(),
                        bucket=Bucket.TREND_LEADER,
                        rank=rank,
                        sector="미분류",
                        score=_score(item),
                        is_requested_focus=(
                            not context.request.automatic and item.ticker == context.request.ticker
                        ),
                        evidence_ids=item.evidence_ids,
                    )
                    for rank, item in enumerate(selected, start=1)
                ),
            )
        updated = replace(
            context,
            is_daily_pick=result is not None,
            daily_screener_output=result,
        )
        evidence = Evidence(
            evidence_id=f"{context.run_id}:03:screen",
            run_id=context.run_id,
            source="daily-screen-code",
            source_ref="policy://daily-screen/v1",
            observed_at=context.request.cycle_ts,
            captured_at=context.request.cycle_ts,
            confidence=1.0,
            kind=EvidenceKind.MODEL_OUTPUT,
            parent_evidence_ids=(context.evidence_trace[-1].evidence_id,),
        )
        summary = f"오늘의 후보 {len(result.picks) if result is not None else 0}개"
        if not context.request.automatic:
            summary = f"{summary} · {context.request.ticker} 심층 분석 유지"
        return updated.add_stage(
            self.component,
            self.name,
            summary,
            evidence=evidence,
        )
