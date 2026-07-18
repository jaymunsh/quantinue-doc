"""Price and liquidity gates that run before any indicator work."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantinue.market_data.models import Candle, Provenance
from quantinue.orchestration.policy import ScreeningConfig
from quantinue.roles.role_02_technical_analysis.service import (
    average_dollar_volume,
    passes_hard_filters,
)

NOW = datetime(2026, 7, 20, 13, 0, tzinfo=UTC)
CONFIG = ScreeningConfig(min_price_usd=5, min_avg_dollar_vol=20_000_000, dollar_volume_window=20)


def _candles(close: str, volume: int, count: int = 60) -> tuple[Candle, ...]:
    provenance = Provenance(
        source="nasdaq-historical",
        source_ref="https://example.test/historical",
        observed_at=NOW,
        captured_at=NOW,
        confidence=1.0,
        execution_id="run",
    )
    price = Decimal(close)
    return tuple(
        Candle(
            ticker="TEST",
            opened_at=NOW - timedelta(days=count - 1 - day),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=volume,
            provenance=provenance,
        )
        for day in range(count)
    )


def test_price_at_the_threshold_passes_and_a_cent_below_fails() -> None:
    liquid = 10_000_000  # x $5 = $50M/day, comfortably above the liquidity gate

    assert passes_hard_filters(_candles("5.00", liquid), CONFIG) is True
    # 유동성이 충분해도 가격 게이트를 먼저 통과해야 한다.
    assert passes_hard_filters(_candles("4.99", liquid), CONFIG) is False


def test_penny_price_is_rejected_regardless_of_volume() -> None:
    assert passes_hard_filters(_candles("4.99", 1_000_000_000), CONFIG) is False


def test_dollar_volume_at_the_threshold_passes() -> None:
    # 100.00 x 200_000 = $20,000,000 exactly.
    assert passes_hard_filters(_candles("100", 200_000), CONFIG) is True
    assert passes_hard_filters(_candles("100", 199_999), CONFIG) is False


def test_average_uses_only_the_configured_window() -> None:
    thin = _candles("100", 1, count=40)
    thick = _candles("100", 1_000_000, count=20)

    combined = (*thin, *thick)

    # 최근 20봉만 보므로 앞의 얇은 구간은 평균을 끌어내리지 않는다.
    assert average_dollar_volume(combined, 20) == 100_000_000.0
    assert passes_hard_filters(combined, CONFIG) is True


def test_empty_history_never_passes() -> None:
    assert passes_hard_filters((), CONFIG) is False
    assert average_dollar_volume((), 20) == 0.0
