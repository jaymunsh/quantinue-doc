"""Stage-08 canonical writes must share the session trade_date with daily picks."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from quantinue.core.contracts import PipelineContext, PipelineRequest
from quantinue.db.domain_records import AccountWrite, CriticVerdictWrite, StrategistSignalWrite
from quantinue.db.postgres_lifecycle import persist_domain_stage
from quantinue.roles.role_02_technical_analysis.contracts import (
    TechnicalAnalysisOutput,
    TechnicalSnapshot,
    Trend,
)


class RecordingDomain:
    """Duck-typed domain repository capturing stage-08 canonical writes."""

    def __init__(self) -> None:
        self.signal: StrategistSignalWrite | None = None

    async def save_signal(self, value: StrategistSignalWrite) -> int:
        self.signal = value
        return 701

    async def save_account(self, value: AccountWrite) -> int:
        del value
        return 41

    async def save_verdict(self, value: CriticVerdictWrite) -> int:
        del value
        return 1


def _snapshot(ticker: str, trade_date: date) -> TechnicalSnapshot:
    return TechnicalSnapshot(
        trade_date=trade_date,
        ticker=ticker,
        close=128.4,
        rs_20=6.2,
        vol_ratio=1.8,
        ret_5d=4.8,
        ret_20d=6.2,
        atr_pct=2.1,
        high_252_ratio=0.97,
        rsi=61.0,
        macd=1.2,
        ma20=120.0,
        ma50=110.0,
        trend=Trend.UP,
        evidence_ids=("run:02:candles",),
    )


def _account() -> AccountWrite:
    return AccountWrite("test", Decimal(1000), Decimal(1000), Decimal(1000))


@pytest.mark.anyio
async def test_stage08_uses_session_trade_date_from_technical_snapshot() -> None:
    # Given: a weekend run — the wall clock (07-18 Sat) diverges from the last
    # session (07-17 Fri) that screening used for tb_daily_pick FK parents.
    domain = RecordingDomain()
    request = PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 18, 13, 0, tzinfo=UTC))
    context = PipelineContext(
        request=request,
        technical_output=TechnicalAnalysisOutput(
            run_id="run-1",
            snapshots=(_snapshot("NVDA", date(2026, 7, 17)),),
        ),
        last_price=128.4,
        side="hold",
        conviction=0.5,
    )

    # When
    _ = await persist_domain_stage(domain, _account(), "08", context)

    # Then: the signal joins the same session the picks were written under.
    assert domain.signal is not None
    assert domain.signal.trade_date == date(2026, 7, 17)


@pytest.mark.anyio
async def test_stage08_falls_back_to_cycle_date_without_technical_output() -> None:
    domain = RecordingDomain()
    request = PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 18, 13, 0, tzinfo=UTC))
    context = PipelineContext(request=request, last_price=128.4, side="hold", conviction=0.5)

    _ = await persist_domain_stage(domain, _account(), "08", context)

    assert domain.signal is not None
    assert domain.signal.trade_date == date(2026, 7, 18)
