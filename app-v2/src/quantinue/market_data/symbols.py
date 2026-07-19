"""Ticker spelling translation between our ledger and the venue.

우리 원장은 상장 피드(NASDAQ) 표기를 쓴다: 주식 클래스 구분자가 슬래시다
(``BRK/B``). Alpaca는 점을 쓴다(``BRK.B``). 실측으로 확인된 것이라 양쪽 다
정확해야 한다 — 요청에 슬래시를 실으면 **배치 전체가 400**이 되고, 응답의 점을
안 되돌리면 ``tb_universe``·``tb_order``와의 조인이 조용히 빈다.

번역을 여기 한 곳에 둔 이유: 봉 어댑터는 보낼 때(우리 → 거래소), 뉴스 어댑터는
받을 때(거래소 → 우리) 필요한데, 규칙은 하나다. 두 곳에 리터럴로 두면 한쪽만
고쳐지는 날이 온다.
"""

from __future__ import annotations

from typing import Final

_OUR_CLASS_SEPARATOR: Final = "/"
_VENUE_CLASS_SEPARATOR: Final = "."


def to_venue_symbol(ticker: str) -> str:
    """Translate our ledger spelling into the one the venue accepts."""
    return ticker.replace(_OUR_CLASS_SEPARATOR, _VENUE_CLASS_SEPARATOR)


def from_venue_symbol(symbol: str) -> str:
    """Translate the venue's spelling back into our ledger's."""
    return symbol.replace(_VENUE_CLASS_SEPARATOR, _OUR_CLASS_SEPARATOR)
