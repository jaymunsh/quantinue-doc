"""Pure screening rules — rank the universe, then decide today's analysis scope.

구 role_03은 500종목 캡(``technical_candidates``) 안에서만 골랐다. 그 캡의
존재 이유는 판단이 아니라 비용이었다 — 지표를 종목당 1콜(~3s)로 받아야 해서
전 종목을 보면 장전 창을 넘겼다. 봉이 원장에 앉아 있으면 계산은 공짜이므로
캡은 근거를 잃고, 랭킹은 전 유니버스를 놓고 한 번에 푼다.

여기 있는 것은 전부 순수 함수다. DB도 API도 모른다 — "왜 이 종목이 오늘의
분석 대상인가"를 픽스처만으로 전부 재현할 수 있어야 하기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from quantinue.core.ontology import Bucket

if TYPE_CHECKING:
    from collections.abc import Sequence

# 52주 고가의 이 비율 위면 돌파로 본다. 정확히 고가에 닿는 날만 세면 라벨이
# 거의 안 붙는데, 학습 입력으로서의 가치는 "고가권에 있었다"에 있다.
_BREAKOUT_RATIO = Decimal("0.98")
# 평소 거래량의 이 배수를 넘으면 거래량 급증. 추세보다 먼저 보는 이유는
# 급증이 더 구체적인 사건이기 때문이다.
_VOLUME_SURGE_MULTIPLE = 2.0


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """One ticker's window indicators as of the session being screened.

    전부 원장의 봉에서 계산된 값이다. ``ret_20d_pct``가 비율이 아니라 **퍼센트**
    인 이유: 점수식을 구 role_03에서 그대로 이어받았고(``ret_20d / 40``), 척도를
    바꾸면 지금까지 쌓인 점수와 비교가 끊긴다.
    """

    ticker: str
    close: Decimal
    ret_20d_pct: float
    ma20: Decimal
    ma50: Decimal
    high_252: Decimal
    rsi: float
    volume: int
    average_volume: float


@dataclass(frozen=True, slots=True)
class ScreenedPick:
    """One row of today's analysis scope, ready for tb_daily_pick."""

    ticker: str
    rank: int
    score: float
    bucket: Bucket
    is_held: bool


def screen_score(candidate: RankedCandidate) -> float:
    """Score one candidate in 0~1, keeping role 03's weighting intact.

    가중치를 새로 발명하지 않았다. 구 스크리너와 같은 식을 쓰면 랭킹이 바뀐
    이유가 "데이터가 늘어서"인지 "식을 바꿔서"인지 구분할 수 있다 — 두 개를
    동시에 바꾸면 어느 쪽이 효과를 냈는지 영영 모른다.
    """
    trend = 0.25 if candidate.ma20 > candidate.ma50 else 0.1
    if candidate.close < candidate.ma20:
        # 이평 위에 있느냐는 추세의 일부다. ma20 > ma50인데 가격이 그 아래면
        # 추세가 꺾이는 중이라 같은 점수를 줄 수 없다.
        trend = 0.0
    momentum = max(0.0, min(0.35, candidate.ret_20d_pct / 40))
    strength = max(0.0, min(0.25, _high_252_ratio(candidate) * 0.25))
    rsi_quality = max(0.0, 0.15 - abs(candidate.rsi - 60) / 400)
    return round(min(1.0, trend + momentum + strength + rsi_quality), 4)


def classify_bucket(candidate: RankedCandidate) -> Bucket:
    """Label why this candidate surfaced, most specific reason first.

    라벨은 표시용이 아니라 role_11의 학습 입력이다("어떤 종류의 후보가 실제로
    수익을 냈나"). 그래서 구 role_03처럼 전부 ``trend_leader``로 찍지 않고,
    더 구체적인 사실이 이기는 순서로 판정한다.
    """
    if _high_252_ratio(candidate) >= float(_BREAKOUT_RATIO):
        return Bucket.HIGH_52W_BREAKOUT
    if (
        candidate.average_volume > 0
        and candidate.volume / candidate.average_volume >= _VOLUME_SURGE_MULTIPLE
    ):
        return Bucket.VOLUME_SURGE
    if candidate.ma20 > candidate.ma50 and candidate.close >= candidate.ma20:
        return Bucket.TREND_LEADER
    # 남는 것을 pullback으로 모은다 — "추세가 아니다"까지가 우리가 아는 전부다.
    return Bucket.PULLBACK


def select_scope(
    candidates: Sequence[RankedCandidate], *, held: Sequence[str], depth: int
) -> tuple[ScreenedPick, ...]:
    """Return today's analysis scope: the top `depth` candidates, plus every holding.

    **보유는 캡과 무관하다.** 스크리너에서 탈락했어도 들고 있으면 팔지 말지
    판단해야 하고, 그러려면 그날의 분석 범위 안에 있어야 한다 — 시그널이
    ``tb_daily_pick``을 FK로 참조하기 때문에 범위 밖이면 청산을 기록할 자리가
    아예 없다.

    랭킹에 없는 보유(거래정지·유동성 미달·봉 없음)는 점수 0과 ``backfill``로
    들어온다. 근거가 없을 때 점수를 지어내면 배분 잡이 그것을 매수 근거로
    읽는다 — 0은 "모른다"의 정직한 표현이다.
    """
    scored = sorted(
        ((candidate, screen_score(candidate)) for candidate in candidates),
        # 동점일 때 티커로 갈라 순서를 결정적으로 만든다. 실행마다 순위가
        # 흔들리면 "어제 20위였는데 오늘 21위"의 원인을 추적할 수 없다.
        key=lambda item: (-item[1], item[0].ticker),
    )
    holdings = tuple(dict.fromkeys(held))
    chosen: list[tuple[str, float, Bucket, bool]] = [
        (candidate.ticker, score, classify_bucket(candidate), candidate.ticker in holdings)
        for candidate, score in scored[: max(0, depth)]
    ]
    already = {ticker for ticker, _, _, _ in chosen}
    ranked_by_ticker = {candidate.ticker: (candidate, score) for candidate, score in scored}
    for ticker in holdings:
        if ticker in already:
            continue
        found = ranked_by_ticker.get(ticker)
        if found is None:
            chosen.append((ticker, 0.0, Bucket.BACKFILL, True))
            continue
        candidate, score = found
        chosen.append((ticker, score, classify_bucket(candidate), True))
    chosen.sort(key=lambda item: (-item[1], item[0]))
    return tuple(
        ScreenedPick(ticker=ticker, rank=index, score=score, bucket=bucket, is_held=is_held)
        for index, (ticker, score, bucket, is_held) in enumerate(chosen, start=1)
    )


def _high_252_ratio(candidate: RankedCandidate) -> float:
    """How close the last close sits to the highest high we have on file."""
    if candidate.high_252 <= 0:
        return 0.0
    return float(candidate.close / candidate.high_252)
