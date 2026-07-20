"""The disclosure scoring job: today's filings in, one vote per ticker out.

**왜 별도 잡인가.** 채점이 묻는 것은 "무엇이 사실인가"이지 "내 성향이면 어떻게
보는가"가 아니다 — 답이 성향과 무관해야 하므로 성향 축을 갖지 않는다(공시
요약·뉴스 채점을 role_07 밖에 둔 기존 결정과 같다). 분석 잡 안에서 채점하면
성향 수만큼 같은 질문을 반복하게 된다.

**왜 뉴스가 아니라 공시인가.** 실측이다. 와이어 뉴스(allow 0.95)는 우리 픽을
한 종목도 덮지 않았고(픽 날짜 4개 전부 겹침 0 — 와이어 24종목 중 유니버스에
있는 것이 3개뿐이다), 픽을 실제로 덮는 뉴스는 전부 benzinga(gray 0.50)라
``gates.source_trust_min``(0.55)에서 표를 잃는다. 공시는 sec.gov(allow)이고
픽의 25~30%를 덮으며, 결정적으로 ``vote_conviction``에서 ``disclosure_score``는
신뢰도 게이트를 아예 타지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import TYPE_CHECKING

from quantinue.db.domain_records import DisclosureSignalWrite
from quantinue.llm.provider import AnalysisTask
from quantinue.roles.disclosure.contracts import disclosure_prompt

if TYPE_CHECKING:
    from datetime import date

    from quantinue.llm.provider import LlmAnalyzer


@dataclass(frozen=True, slots=True)
class DisclosureScore:
    """What the model concluded about one ticker's filings."""

    ticker: str
    score: float
    reason: str
    forms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DisclosureScoringRun:
    """What one pass over today's scope produced.

    건너뛴 수를 함께 돌려주는 이유: 잡 원장이 "12종목 채점"이라고만 적으면
    대상이 14였다는 사실이 사라진다. 조용한 절단은 "전부 봤다"로 읽힌다.
    """

    scores: tuple[DisclosureScore, ...]
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class DisclosureScoringJob:
    """Score the SEC filings of every ticker in today's analysis scope."""

    store: object
    analyzer: LlmAnalyzer

    async def run(self, *, as_of: date, session: date) -> DisclosureScoringRun:
        """Score each in-scope ticker that actually filed."""
        domain = getattr(self.store, "domain", self.store)
        subjects = await domain.analysis_subjects(as_of, session)
        if not subjects:
            return DisclosureScoringRun(())
        tickers = tuple(subject.ticker for subject in subjects)
        filings = await domain.disclosure_evidence(session, tickers)
        # 분석 잡과 **같은** 자정 cycle_ts. 계보 FK가 이 값으로 걸린다.
        cycle_ts = datetime.combine(as_of, time(), tzinfo=UTC)
        scores: list[DisclosureScore] = []
        skipped = 0
        for ticker in tickers:
            forms = filings.get(ticker, ())
            # 공시가 없는 날은 기권이지 악재가 아니다. 여기서 중립값을 지어
            # 넣으면 그 가짜 표가 실제 판단을 희석한다 — role_05의 원칙이고
            # StrategyInput.disclosure_score가 None을 허용하는 이유다.
            if not forms:
                continue
            try:
                result = await self.analyzer.analyze(
                    AnalysisTask.DISCLOSURE, disclosure_prompt(ticker, forms)
                )
            except Exception:  # noqa: BLE001 - 종목 하나의 실패를 격리하는 자리다
                # 한 종목이 구조화 출력을 놓쳐 나머지가 통째로 날아간 적이 있다.
                # 채점은 부가 증거라, 없으면 그 종목이 기권할 뿐이다.
                skipped += 1
                continue
            await domain.save_disclosure_signal(
                DisclosureSignalWrite(
                    ticker=ticker,
                    cycle_ts=cycle_ts,
                    trade_date=as_of,
                    has_signal=True,
                    sentiment_score=result.score,
                    disclosure_count=len(forms),
                )
            )
            scores.append(
                DisclosureScore(
                    ticker=ticker,
                    score=result.score,
                    reason=result.reason,
                    forms=forms,
                )
            )
        # 시도한 종목이 있는데 하나도 못 채점했으면 모델이 죽은 것이다.
        # 그때 조용히 성공을 기록하면 슬롯이 잠겨 그날 재시도되지 않는다 —
        # 공시가 아예 없는 날(시도 0)과는 구별해야 한다.
        if skipped and not scores:
            msg = f"disclosure scoring failed for all {skipped} tickers"
            raise RuntimeError(msg)
        return DisclosureScoringRun(tuple(scores), skipped)
