"""Select the first-stage security universe."""

from dataclasses import dataclass, replace
from datetime import date
from typing import ClassVar, Final

from quantinue.core.contracts import PipelineContext
from quantinue.core.errors import ValidationFailureError
from quantinue.core.ontology import EvidenceKind
from quantinue.core.schemas import Evidence
from quantinue.market_data import MarketData, SecuritySnapshot
from quantinue.roles.role_01_universe_screener.contracts import (
    UniverseMember,
    UniverseScreenerInput,
    UniverseScreenerOutput,
)

PUBLIC_UNIVERSE_LIMIT: Final = 50


def _select_public_universe(
    snapshots: tuple[SecuritySnapshot, ...], requested_ticker: str | None
) -> tuple[SecuritySnapshot, ...]:
    unique: dict[str, SecuritySnapshot] = {}
    for snapshot in snapshots:
        if snapshot.market_cap > 0 and snapshot.ticker not in unique:
            unique[snapshot.ticker] = snapshot
    eligible = tuple(unique.values())
    if not eligible:
        field = "universe"
        reason = "no eligible securities"
        raise ValidationFailureError(field, reason)
    if requested_ticker is None:
        return eligible[:PUBLIC_UNIVERSE_LIMIT]
    requested = unique.get(requested_ticker)
    if requested is None:
        field = "universe"
        reason = f"requested ticker {requested_ticker} is unavailable"
        raise ValidationFailureError(field, reason)
    selected = eligible[:PUBLIC_UNIVERSE_LIMIT]
    if requested not in selected:
        return (*selected[: PUBLIC_UNIVERSE_LIMIT - 1], requested)
    return selected


@dataclass(frozen=True, slots=True)
class UniverseScreener:
    """MVP implementation for the 50-name public universe boundary."""

    component: ClassVar[str] = "01"
    name: ClassVar[str] = "1차 스크리너"
    market_data: MarketData | None = None

    def fixture(self, context: PipelineContext) -> UniverseScreenerOutput:
        """Build the deterministic API-key-free role result."""
        ticker = context.request.ticker
        source = Evidence(
            evidence_id=f"{context.run_id}:01:market",
            run_id=context.run_id,
            source="fixture",
            source_ref=f"fixture://universe/{ticker}",
            observed_at=context.request.cycle_ts,
            captured_at=context.request.cycle_ts,
            confidence=1.0,
            kind=EvidenceKind.MARKET_DATA,
        )
        role_input = UniverseScreenerInput(
            run_id=context.run_id,
            execution_at=context.request.cycle_ts,
            evidence=(source,),
            as_of_date=context.request.cycle_ts.date(),
        )
        member = UniverseMember(
            as_of_date=role_input.as_of_date or date.min,
            ticker=ticker,
            company_name="NVIDIA Corporation" if ticker == "NVDA" else ticker,
            market_cap=3_210_000_000_000,
            evidence_ids=(source.evidence_id,),
        )
        return UniverseScreenerOutput(
            run_id=context.run_id,
            generated_at=context.request.cycle_ts,
            members=(member,),
        )

    async def execute(self, context: PipelineContext) -> PipelineContext:
        """Keep the requested ticker in the local MVP universe."""
        if self.market_data is None:
            result = self.fixture(context)
            updated = replace(
                context,
                universe=tuple(member.ticker for member in result.members),
                universe_output=result,
            )
            evidence = Evidence(
                evidence_id=result.members[0].evidence_ids[0],
                run_id=context.run_id,
                source="market-fixture",
                source_ref=f"fixture://universe/{context.request.ticker}",
                observed_at=context.request.cycle_ts,
                captured_at=context.request.cycle_ts,
                confidence=1.0,
                kind=EvidenceKind.MARKET_DATA,
            )
            return updated.add_stage(
                self.component,
                self.name,
                f"{context.request.ticker} 유니버스 포함",
                evidence=evidence,
            )
        snapshots = await self.market_data.screener(str(context.run_id))
        selected = _select_public_universe(
            snapshots,
            None if context.request.automatic else context.request.ticker,
        )
        snapshot = selected[0]
        result = UniverseScreenerOutput(
            run_id=context.run_id,
            generated_at=context.request.cycle_ts,
            members=tuple(
                UniverseMember(
                    as_of_date=context.request.cycle_ts.date(),
                    ticker=item.ticker,
                    company_name=item.name,
                    market_cap=int(item.market_cap),
                    evidence_ids=(f"{context.run_id}:01:market",),
                )
                for item in selected
            ),
        )
        updated = replace(
            context,
            universe=tuple(item.ticker for item in selected),
            universe_output=result,
        )
        provenance = snapshot.provenance
        evidence = Evidence(
            evidence_id=f"{context.run_id}:01:market",
            run_id=context.run_id,
            source=provenance.source,
            source_ref=provenance.source_ref,
            observed_at=min(provenance.observed_at, context.request.cycle_ts),
            captured_at=context.request.cycle_ts,
            confidence=provenance.confidence,
            kind=EvidenceKind.MARKET_DATA,
        )
        included = context.request.ticker in updated.universe
        summary = f"{len(selected)}개 기업 선정"
        if included:
            summary = f"{summary} · {context.request.ticker} 포함"
        return updated.add_stage(self.component, self.name, summary, evidence=evidence)
