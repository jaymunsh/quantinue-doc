"""What one ticker looks like on the day it is analysed, and how we ask about it.

구 05·06은 종목당 각각 1콜씩 써서 공시 점수와 뉴스 점수를 따로 냈고, 07은 그
둘을 float 두 개로만 받았다 — 모델은 티커도, 가격도, 무슨 일이 있었는지도 보지
못했다(``f"technical={t}, disclosure={d}, news={n}"``가 프롬프트 전부였다).

여기서는 증거를 **한 덩어리로 종합해** 한 번에 묻는다. 콜 수가 줄어서가 아니라
판단이 맥락을 갖기 위해서다. 특히 **보유 맥락**이 들어가야 07이 "안 사는 것"과
"파는 것"을 구분할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal

    from quantinue.roles.screening import RankedCandidate


@dataclass(frozen=True, slots=True)
class AnalysisSubject:
    """One ticker in today's scope, priced from the last closed session."""

    ticker: str
    rank: int
    score: float
    bucket: str
    close: Decimal
    high: Decimal
    low: Decimal
    close_prev: Decimal | None


@dataclass(frozen=True, slots=True)
class HoldingContext:
    """What we own of this ticker, if anything.

    ``quantity``가 0이면 미보유다. 진입가와 보유일이 함께 오는 이유: "얼마에
    샀는지"만으로는 손실이 굳어가는 중인지 방금 흔들린 것인지 알 수 없다.
    """

    quantity: int = 0
    entry_price: Decimal | None = None
    business_days_held: int = 0


def indicator_lines(indicators: RankedCandidate | None) -> tuple[str, ...]:
    """State the window indicators as plain facts, or say we could not measure.

    **왜 이것들인가.** 두 페르소나가 오닐(CAN SLIM)·미너비니(SEPA)에 정박돼
    있어서 추세 템플릿(ma20/ma50과 가격의 위치)·52주 고점 근접·거래량 확인을
    **명시적으로 요구한다**. 이 값들은 스크리닝 SQL이 이미 계산하는데 원장에는
    합성 점수 하나만 남아서, 모델은 자기 방법론의 핵심 입력을 못 본 채
    판단하고 있었다 — 그리고 그 사실을 근거에 정직하게 적었다. 크리틱이 그
    고백을 반박 사유로 삼아 실 실행에서 승인율이 3%까지 떨어졌다.

    ``vol_ratio``만 여기서 파생시키는 이유: 원장에 있는 것은 당일 거래량과
    20일 평균 둘이고, 방법론이 묻는 것은 그 **비율**이다. 모델에게 나눗셈을
    시키면 틀릴 수 있고, 틀린 값이 근거로 인용된다.

    측정하지 못한 종목은 지어내지 않는다. 0으로 채우면 "약하다"로 읽히는데
    사실은 "재지 못했다"이고, 그 차이는 매도 판단에서 정반대의 결론을 만든다.
    """
    if indicators is None:
        return ("indicators=none",)
    high_252_ratio = (
        0.0
        if indicators.high_252 <= 0
        else float(indicators.close / indicators.high_252)
    )
    vol_ratio = (
        0.0
        if indicators.average_volume <= 0
        else indicators.volume / indicators.average_volume
    )
    return (
        f"ma20={indicators.ma20:.2f} ma50={indicators.ma50:.2f}"
        f" ret_20d_pct={indicators.ret_20d_pct:.2f}",
        f"high_252={indicators.high_252:.2f} high_252_ratio={high_252_ratio:.3f}"
        f" rsi={indicators.rsi:.1f} vol_ratio={vol_ratio:.2f}",
    )


def analysis_prompt(
    subject: AnalysisSubject,
    holding: HoldingContext,
    filings: tuple[str, ...],
    headlines: tuple[str, ...] = (),
    indicators: RankedCandidate | None = None,
) -> str:
    """Compose the one payload the strategist model sees for this ticker.

    모델에 넘기는 문자열은 전부 신뢰할 수 없는 외부 데이터로 취급된다
    (``PydanticAiAnalyzer``가 ``ModelInput.external_data``로 감싼다). 그래서
    여기서는 지시가 아니라 **사실만** 적는다 — 지시는 시스템 프롬프트 소유다.

    보유 중일 때 미실현 손익을 함께 적는 이유: 같은 약세 신호라도 이미 20%
    손실인 포지션과 방금 산 포지션은 다른 결정을 부른다.
    """
    lines = [
        f"ticker={subject.ticker}",
        f"screening_rank={subject.rank} screening_score={subject.score:.4f}"
        f" bucket={subject.bucket}",
        f"close={subject.close} day_high={subject.high} day_low={subject.low}",
    ]
    if subject.close_prev is not None:
        lines.append(f"previous_close={subject.close_prev}")
    if holding.quantity > 0:
        held = [f"held_quantity={holding.quantity}"]
        if holding.entry_price is not None:
            held.append(f"entry_price={holding.entry_price}")
            if holding.entry_price > 0:
                change = (subject.close - holding.entry_price) / holding.entry_price
                held.append(f"unrealized_pct={change * 100:.2f}")
        held.append(f"business_days_held={holding.business_days_held}")
        lines.append(" ".join(held))
    else:
        lines.append("held_quantity=0")
    # 없는 것도 적는다. 증거가 비었다는 사실 자체가 판단 근거이고, 항목이
    # 통째로 빠지면 모델은 "안 알려줬다"와 "없었다"를 구분할 수 없다.
    lines.extend(indicator_lines(indicators))
    lines.append(f"filings={','.join(filings) if filings else 'none'}")
    lines.append(f"headlines={' | '.join(headlines) if headlines else 'none'}")
    return "\n".join(lines)


def critique_prompt(
    subject: AnalysisSubject,
    side: str,
    conviction: float,
    rationale: str,
    indicators: RankedCandidate | None,
) -> str:
    """Compose what the critic sees — the claim **and** the evidence behind it.

    지금까지 08에 간 것은 ``proposal=... conviction=... rationale=...``이 전부라,
    반박자가 볼 수 있는 것이 07의 산문뿐이었다. 원 증거를 못 보는 반박자는
    주장을 데이터와 대조할 수 없고 산문의 약점을 공격할 수밖에 없다 — 그래서
    07이 정직하게 적은 한계가 그대로 기각 사유가 되는 동어반복이 생겼다.
    같은 증거를 주면 "네가 없다고 했다"가 아니라 "이 값으로는 부족하다"를
    말할 수 있다.
    """
    lines = [
        f"proposal={side} ticker={subject.ticker} conviction={conviction}",
        f"close={subject.close} day_high={subject.high} day_low={subject.low}",
        *indicator_lines(indicators),
        f"rationale={rationale}",
    ]
    return "\n".join(lines)
