"""Phase 3 스크리닝: 하루의 분석 범위를 "상위 N 과 보유의 합집합"로 정하는 순수 규칙.

구 role_03은 500종목 캡 안에서만 골랐다 — 종목당 1콜로 지표를 받아야 했기
때문이다(캡의 존재 이유). 봉이 원장에 있으면 계산은 공짜라 전 유니버스를 놓고
줄을 세울 수 있고, 캡은 의미를 잃는다.

**보유가 캡과 무관하게 들어오는 이유**가 이 규칙의 핵심이다. 스크리너에서
탈락한 종목이라도 우리가 들고 있으면 팔지 말지 판단해야 한다. 범위 밖이면
시그널을 남길 자리(FK)가 없어서 청산이 막힌다.
"""

from decimal import Decimal

from quantinue.core.ontology import Bucket
from quantinue.roles.screening.contracts import (
    RankedCandidate,
    classify_bucket,
    screen_score,
    select_scope,
)


def _candidate(ticker: str, **overrides: object) -> RankedCandidate:
    fields: dict[str, object] = {
        "ticker": ticker,
        "close": Decimal("100.00"),
        "ret_20d_pct": 8.0,
        "ma20": Decimal("95.00"),
        "ma50": Decimal("90.00"),
        "high_252": Decimal("110.00"),
        "rsi": 60.0,
        "volume": 1_000_000,
        "average_volume": 1_000_000.0,
    }
    fields.update(overrides)
    return RankedCandidate(**fields)  # pyright: ignore[reportArgumentType]


def test_a_stronger_trend_outranks_a_weaker_one() -> None:
    # Given / When
    strong = screen_score(_candidate("AAA", ret_20d_pct=20.0))
    weak = screen_score(_candidate("BBB", ret_20d_pct=1.0))

    # Then: 점수는 0~1로 고정된다 — tb_daily_pick.score의 계약이다.
    assert 0.0 <= weak < strong <= 1.0


def test_a_downtrend_scores_lower_than_an_uptrend_with_the_same_momentum() -> None:
    # Given: 같은 모멘텀, 다른 추세(ma20이 ma50 아래).
    up = screen_score(_candidate("AAA"))
    down = screen_score(_candidate("BBB", ma20=Decimal("85.00"), ma50=Decimal("90.00")))

    # Then
    assert down < up


def test_a_breakout_is_labelled_before_a_plain_trend() -> None:
    # Given: 52주 고가에 붙은 종목과 그냥 추세 종목.
    breakout = _candidate("AAA", close=Decimal("109.90"), high_252=Decimal("110.00"))
    trending = _candidate("BBB", close=Decimal("100.00"), high_252=Decimal("140.00"))

    # Then: 더 구체적인 사실이 이긴다 — 라벨은 role_11의 학습 입력이다.
    assert classify_bucket(breakout) is Bucket.HIGH_52W_BREAKOUT
    assert classify_bucket(trending) is Bucket.TREND_LEADER


def test_a_ticker_below_its_averages_is_a_pullback_not_a_leader() -> None:
    # Given
    weak = _candidate(
        "AAA", ma20=Decimal("85.00"), ma50=Decimal("90.00"), high_252=Decimal("200.00")
    )

    # Then
    assert classify_bucket(weak) is Bucket.PULLBACK


def test_the_scope_is_the_top_n_ranked_by_score() -> None:
    # Given: 후보 4개, 깊이 2.
    candidates = (
        _candidate("LOW", ret_20d_pct=1.0),
        _candidate("HIGH", ret_20d_pct=30.0),
        _candidate("MID", ret_20d_pct=10.0),
        _candidate("LOWEST", ret_20d_pct=0.0),
    )

    # When
    picks = select_scope(candidates, held=(), depth=2)

    # Then: 순위는 1부터 빈틈없이 매겨진다 — tb_daily_pick.rank의 계약이다.
    assert [pick.ticker for pick in picks] == ["HIGH", "MID"]
    assert [pick.rank for pick in picks] == [1, 2]


def test_a_holding_enters_the_scope_even_when_it_ranks_below_the_cap() -> None:
    """탈락한 보유 종목이 범위 밖이면 청산 시그널을 남길 자리가 없다."""
    # Given: HELD는 점수가 꼴찌지만 우리가 들고 있다.
    candidates = (
        _candidate("HIGH", ret_20d_pct=30.0),
        _candidate("HELD", ret_20d_pct=0.0),
    )

    # When
    picks = select_scope(candidates, held=("HELD",), depth=1)

    # Then: 캡은 1이지만 보유가 더해져 둘 다 들어온다.
    assert {pick.ticker for pick in picks} == {"HIGH", "HELD"}
    assert [pick.rank for pick in picks] == [1, 2]


def test_a_holding_with_no_bars_still_enters_the_scope() -> None:
    """거래정지 종목은 봉이 안 찍힌다 — 팔아야 할 바로 그 종목이다."""
    # Given: HALTED는 랭킹 후보에 아예 없다(봉 없음·유동성 미달).
    candidates = (_candidate("HIGH", ret_20d_pct=30.0),)

    # When
    picks = select_scope(candidates, held=("HALTED",), depth=5)

    # Then: 근거가 없으므로 점수 0과 backfill 라벨로 들어온다 — 지어내지 않는다.
    halted = next(pick for pick in picks if pick.ticker == "HALTED")
    assert halted.score == 0.0
    assert halted.bucket is Bucket.BACKFILL
    assert halted.rank == 2
