"""Calculate deterministic technical indicators from daily candles."""

from dataclasses import dataclass, replace
from statistics import fmean
from typing import ClassVar, Final, assert_never

import anyio

from quantinue.core.contracts import PipelineContext
from quantinue.core.errors import (
    AuthenticationFailureError,
    HttpFailureError,
    RetryExhaustedError,
    TransientFailureError,
    ValidationFailureError,
)
from quantinue.core.ontology import EvidenceKind, Trend
from quantinue.core.schemas import Evidence
from quantinue.market_data import Candle, MarketData
from quantinue.roles.role_02_technical_analysis.contracts import (
    TechnicalAnalysisInput,
    TechnicalAnalysisOutput,
    TechnicalSnapshot,
)

TECHNICAL_CONCURRENCY: Final = 5
TECHNICAL_UNIVERSE_LIMIT: Final = 20
MINIMUM_HISTORY: Final = 50
REQUIRED_SUCCESSFUL_SNAPSHOTS: Final = 20
EXPECTED_FETCH_ERRORS: Final = (
    AuthenticationFailureError,
    HttpFailureError,
    RetryExhaustedError,
    TransientFailureError,
    ValidationFailureError,
    TimeoutError,
)


def _average(values: tuple[float, ...]) -> float:
    return fmean(values)


def _ema(values: tuple[float, ...], period: int) -> float:
    weight = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * weight + result * (1 - weight)
    return result


def technical_score(snapshot: TechnicalSnapshot) -> float:
    """Return the canonical role-02 score for one validated snapshot."""
    match snapshot.trend:
        case Trend.UP:
            trend = 0.2
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
    rsi_quality = max(0.0, 0.2 - abs(snapshot.rsi - 60) / 300)
    return round(min(1.0, trend + momentum + strength + rsi_quality), 4)


def _snapshot(ticker: str, candles: tuple[Candle, ...], evidence_id: str) -> TechnicalSnapshot:
    closes = tuple(float(item.close) for item in candles)
    volumes = tuple(float(item.volume) for item in candles)
    latest = candles[-1]
    changes = tuple(closes[index] - closes[index - 1] for index in range(1, len(closes)))
    gains = tuple(max(change, 0.0) for change in changes[-14:])
    losses = tuple(max(-change, 0.0) for change in changes[-14:])
    average_loss = _average(losses)
    rsi = 100.0 if average_loss == 0 else 100 - 100 / (1 + _average(gains) / average_loss)
    true_ranges = tuple(
        max(
            float(item.high - item.low),
            abs(float(item.high) - closes[index - 1]),
            abs(float(item.low) - closes[index - 1]),
        )
        for index, item in enumerate(candles[1:], start=1)
    )
    ma20 = _average(closes[-20:])
    ma50 = _average(closes[-50:])
    ret_5d = (closes[-1] / closes[-6] - 1) * 100
    ret_20d = (closes[-1] / closes[-21] - 1) * 100
    trend = Trend.UP if closes[-1] > ma20 > ma50 else Trend.DOWN
    return TechnicalSnapshot(
        trade_date=latest.opened_at.date(),
        ticker=ticker,
        close=closes[-1],
        rs_20=round(ret_20d, 4),
        vol_ratio=round(volumes[-1] / _average(volumes[-20:]), 4),
        ret_5d=round(ret_5d, 4),
        ret_20d=round(ret_20d, 4),
        atr_pct=round(_average(true_ranges[-14:]) / closes[-1] * 100, 4),
        high_252_ratio=round(closes[-1] / max(closes), 4),
        rsi=round(rsi, 4),
        macd=round(_ema(closes, 12) - _ema(closes, 26), 4),
        ma20=round(ma20, 4),
        ma50=round(ma50, 4),
        trend=trend,
        evidence_ids=(evidence_id,),
    )


def _selected_snapshot(
    context: PipelineContext,
    snapshots: tuple[TechnicalSnapshot, ...],
) -> TechnicalSnapshot:
    if context.request.automatic:
        return snapshots[0]
    return next(item for item in snapshots if item.ticker == context.request.ticker)


def _usable_candles(
    collected: dict[str, tuple[Candle, ...] | None],
    ticker: str,
) -> tuple[Candle, ...]:
    candles = collected.get(ticker)
    if candles is None:
        field = "candles"
        reason = f"selected ticker {ticker} has no usable history"
        raise ValidationFailureError(field, reason)
    return candles


@dataclass(frozen=True, slots=True)
class TechnicalAnalysis:
    """Fixture scorer or bounded multi-security public candle analyzer."""

    component: ClassVar[str] = "02"
    name: ClassVar[str] = "기술 분석"
    market_data: MarketData | None = None

    def fixture(self, context: PipelineContext) -> TechnicalAnalysisOutput:
        """Build the deterministic documented technical row."""
        source = Evidence(
            evidence_id=f"{context.run_id}:02:candles",
            run_id=context.run_id,
            source="fixture",
            source_ref=f"fixture://candles/{context.request.ticker}",
            observed_at=context.request.cycle_ts,
            captured_at=context.request.cycle_ts,
            confidence=1.0,
            kind=EvidenceKind.MARKET_DATA,
        )
        role_input = TechnicalAnalysisInput(
            run_id=context.run_id,
            execution_at=context.request.cycle_ts,
            evidence=(source,),
            trade_date=context.request.cycle_ts.date(),
            ticker=context.request.ticker,
        )
        snapshot = TechnicalSnapshot(
            trade_date=role_input.trade_date or context.request.cycle_ts.date(),
            ticker=context.request.ticker,
            close=128.40,
            rs_20=6.2,
            vol_ratio=1.8,
            ret_5d=4.8,
            ret_20d=11.2,
            atr_pct=3.1,
            high_252_ratio=0.94,
            rsi=63.5,
            macd=1.42,
            ma20=118.30,
            ma50=111.70,
            trend=Trend.UP,
            evidence_ids=(source.evidence_id,),
        )
        return TechnicalAnalysisOutput(run_id=context.run_id, snapshots=(snapshot,))

    async def execute(self, context: PipelineContext) -> PipelineContext:
        """Analyze the universe and retain requested-ticker scalar fields downstream."""
        if self.market_data is None:
            result = self.fixture(context)
            snapshot = result.snapshots[0]
            updated = replace(
                context,
                technical_score=0.82,
                last_price=snapshot.close,
                technical_output=result,
            )
            evidence = Evidence(
                evidence_id=snapshot.evidence_ids[0],
                run_id=context.run_id,
                source="market-fixture",
                source_ref=f"fixture://candles/{context.request.ticker}",
                observed_at=context.request.cycle_ts,
                captured_at=context.request.cycle_ts,
                confidence=1.0,
                kind=EvidenceKind.MARKET_DATA,
                parent_evidence_ids=(context.evidence_trace[-1].evidence_id,),
            )
            return updated.add_stage(
                self.component, self.name, "기술 점수 0.82, 현재가 128.40", evidence=evidence
            )
        market_data = self.market_data
        limiter = anyio.CapacityLimiter(TECHNICAL_CONCURRENCY)
        collected: dict[str, tuple[Candle, ...] | None] = {}

        async def collect(ticker: str) -> None:
            async with limiter:
                try:
                    values = await market_data.candles(ticker, str(context.run_id))
                except EXPECTED_FETCH_ERRORS:
                    collected[ticker] = None
                    return
                collected[ticker] = values if len(values) >= MINIMUM_HISTORY else None

        initial = context.universe[:TECHNICAL_UNIVERSE_LIMIT]
        if not context.request.automatic and context.request.ticker not in initial:
            initial = (*initial[:-1], context.request.ticker)
        candidates = (*initial, *(ticker for ticker in context.universe if ticker not in initial))
        requested: list[str] = []
        for offset in range(0, len(candidates), TECHNICAL_CONCURRENCY):
            batch = candidates[offset : offset + TECHNICAL_CONCURRENCY]
            requested.extend(batch)
            async with anyio.create_task_group() as task_group:
                for ticker in batch:
                    _ = task_group.start_soon(collect, ticker)
            successful_count = sum(collected.get(ticker) is not None for ticker in requested)
            if successful_count >= TECHNICAL_UNIVERSE_LIMIT:
                break
        requested_candles = collected.get(context.request.ticker)
        if not context.request.automatic and requested_candles is None:
            field = "candles"
            reason = f"requested ticker {context.request.ticker} has no usable history"
            raise ValidationFailureError(field, reason)
        successful = tuple(ticker for ticker in requested if collected.get(ticker) is not None)[
            :TECHNICAL_UNIVERSE_LIMIT
        ]
        if len(successful) < REQUIRED_SUCCESSFUL_SNAPSHOTS:
            field = "candles"
            reason = "exactly 20 securities require usable candle history"
            raise ValidationFailureError(field, reason)
        snapshots = tuple(
            _snapshot(ticker, collected[ticker] or (), f"{context.run_id}:02:candles:{ticker}")
            for ticker in successful
        )
        excluded = tuple(ticker for ticker in requested if collected.get(ticker) is None)
        result = TechnicalAnalysisOutput(
            run_id=context.run_id,
            snapshots=snapshots,
            excluded_insufficient_history=excluded,
        )
        requested_snapshot = _selected_snapshot(context, snapshots)
        score = technical_score(requested_snapshot)
        updated = replace(
            context,
            technical_score=score,
            last_price=requested_snapshot.close,
            technical_output=result,
        )
        selected_candles = _usable_candles(collected, requested_snapshot.ticker)
        provenance = selected_candles[-1].provenance
        evidence = Evidence(
            evidence_id=f"{context.run_id}:02:candles",
            run_id=context.run_id,
            source=provenance.source,
            source_ref=provenance.source_ref,
            observed_at=min(provenance.observed_at, context.request.cycle_ts),
            captured_at=context.request.cycle_ts,
            confidence=provenance.confidence,
            kind=EvidenceKind.MARKET_DATA,
            parent_evidence_ids=(context.evidence_trace[-1].evidence_id,),
        )
        summary = "".join(
            (
                f"{len(snapshots)}개 기술 분석 · 요청 종목 점수 {score:.4f}, ",
                f"현재가 {requested_snapshot.close:.2f}",
            )
        )
        return updated.add_stage(
            self.component,
            self.name,
            summary,
            evidence=evidence,
        )
