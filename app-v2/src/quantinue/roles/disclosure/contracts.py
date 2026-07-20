"""The disclosure scoring boundary: what the model is asked, what it may answer."""

from __future__ import annotations


def disclosure_prompt(ticker: str, forms: tuple[str, ...]) -> str:
    """Render one ticker's filings as the scoring question.

    폼 종류만 싣는 이유: 우리 원장이 가진 것이 그것뿐이다(``tb_disclosure_raw``는
    EDGAR 일별 인덱스에서 폼 종류·회사명까지만 받는다). 본문을 안 읽었으면서
    "실적이 좋았다"를 묻는 프롬프트를 쓰면 모델이 지어낸다 — 07 페르소나에
    ⚠️ 섹션을 둔 것과 같은 원칙이다.
    """
    listed = ", ".join(forms)
    return (
        f"Ticker: {ticker}\n"
        f"SEC filings for the session: {listed}\n\n"
        "Score how bullish these filings are for a 5-trading-day horizon, "
        "from 0 (clearly bearish) to 1 (clearly bullish). "
        "You are given filing form types only — not their contents. "
        "Judge from what the form type itself implies and say so plainly. "
        "Do not invent financial figures, guidance, or filing contents."
    )
