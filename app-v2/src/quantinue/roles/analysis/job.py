"""The analysis job: today's scope in, buy/hold/sell signals out.

11단계 러너의 05~08을 대체한다. 바뀐 것은 배관만이 아니다:

1. **범위가 실제로 소비된다.** 구 러너는 픽을 50개 만들고 05~08은
   ``context.request.ticker`` 하나만 봤다 — 50 → 1 절벽. 여기서는 스크리닝이
   정한 범위 전체를 돈다.
2. **05·06이 하나로 합쳐진다.** 공시 점수와 뉴스 점수를 따로 내던 두 콜이
   증거 종합 한 콜이 되고, 모델은 처음으로 티커·가격·보유 상태를 함께 본다.
3. **07이 팔 수 있다.** 보유 맥락이 입력에 들어오고, 08이 그 매도를 검증한다.

판단 규칙은 새로 만들지 않았다 — M4 방어선(``blockers``·``vote_conviction``·
``apply_hard_gates``·승인 문턱)을 그대로 부른다. 갈아엎은 것은 누가 언제
부르는가이지 무엇을 근거로 판단하는가가 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

from quantinue.core.market_calendar import NyseCalendar
from quantinue.db.domain_records import CriticVerdictWrite, StrategistSignalWrite
from quantinue.llm.provider import AnalysisTask
from quantinue.roles.analysis.contracts import HoldingContext, analysis_prompt
from quantinue.roles.exits.contracts import business_days_held
from quantinue.roles.role_07_strategist.contracts import StrategyInput, StrategyOutput
from quantinue.roles.role_08_critic.contracts import CriticInput, CriticVerdict

if TYPE_CHECKING:
    from datetime import date

    from quantinue.llm.provider import LlmAnalyzer
    from quantinue.orchestration.policy import GatesConfig, ProfileConfig
    from quantinue.roles.analysis.contracts import AnalysisSubject


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    """What the job decided for one ticker."""

    ticker: str
    side: str
    conviction: float
    approved: bool


@dataclass(frozen=True, slots=True)
class AnalysisJob:
    """Analyse every ticker in today's scope under one persona."""

    store: object
    analyzer: LlmAnalyzer
    gates: GatesConfig
    profile: ProfileConfig
    profile_name: str
    calendar: NyseCalendar = field(default_factory=NyseCalendar)
    # 종목당 프롬프트에 넣을 헤드라인 수(``news.headlines_per_ticker``).
    headlines_per_ticker: int = 5

    async def run(self, *, as_of: date, session: date) -> tuple[AnalysisOutcome, ...]:
        """Decide, verify, and persist one signal per ticker in scope."""
        domain = getattr(self.store, "domain", self.store)
        subjects = await domain.analysis_subjects(as_of, session)
        if not subjects:
            return ()
        tickers = tuple(subject.ticker for subject in subjects)
        filings = await domain.disclosure_evidence(session, tickers)
        headlines = await domain.news_evidence(
            session, tickers, self.headlines_per_ticker
        )
        holdings = await self._holdings(domain, as_of)
        outcomes: list[AnalysisOutcome] = []
        for subject in subjects:
            outcome = await self._analyse(
                domain,
                subject,
                holdings.get(subject.ticker, HoldingContext()),
                filings.get(subject.ticker, ()),
                headlines.get(subject.ticker, ()),
                as_of=as_of,
            )
            if outcome is not None:
                outcomes.append(outcome)
        return tuple(outcomes)

    async def _holdings(self, domain: object, as_of: date) -> dict[str, HoldingContext]:
        """Fold open positions into per-ticker holding context.

        한 계좌가 같은 종목을 여러 번 샀거나 여러 계좌가 나눠 들 수 있다.
        판단은 종목 단위이므로 수량은 합치고, 진입가는 **가장 오래된 포지션**의
        것을 쓴다 — 시간 압박을 받는 쪽이 그쪽이기 때문이다.
        """
        reader = getattr(domain, "open_positions", None)
        if reader is None:
            return {}
        folded: dict[str, HoldingContext] = {}
        for position in sorted(await reader(), key=lambda item: item.filled_on):
            current = folded.get(position.ticker)
            if current is None:
                folded[position.ticker] = HoldingContext(
                    quantity=position.quantity,
                    entry_price=position.entry_price,
                    business_days_held=business_days_held(
                        position.filled_on, as_of, calendar=self.calendar
                    ),
                )
                continue
            folded[position.ticker] = HoldingContext(
                quantity=current.quantity + position.quantity,
                entry_price=current.entry_price,
                business_days_held=current.business_days_held,
            )
        return folded

    async def _analyse(  # noqa: PLR0913 - 증거 종류가 늘면 인자도 는다
        self,
        domain: object,
        subject: AnalysisSubject,
        holding: HoldingContext,
        filings: tuple[str, ...],
        headlines: tuple[str, ...],
        *,
        as_of: date,
    ) -> AnalysisOutcome | None:
        """Run one ticker through evidence synthesis, the gates, and the critic."""
        cycle_ts = datetime.combine(as_of, time(), tzinfo=UTC)
        run_id = f"analysis:{as_of.isoformat()}:{self.profile_name}"
        evidence = await self.analyzer.analyze(
            AnalysisTask.STRATEGY,
            analysis_prompt(subject, holding, filings, headlines),
            # 성향이 여기서 끊기면 두 페르소나가 같은 시스템 프롬프트로 돌고,
            # 원장에는 inv_type만 다른 **같은 확신도**가 두 줄 남는다.
            # 실제로 그랬다 — 문턱만 다르고 판단은 하나였다.
            profile=self.profile_name,
        )
        strategy_input = StrategyInput(
            run_id=run_id,
            ticker=subject.ticker,
            cycle_ts=cycle_ts,
            # 스크리닝 점수가 기술적 근거를 대신한다 — 같은 봉에서 나왔다.
            technical_score=min(1.0, max(0.0, subject.score)),
            # 공시가 없는 날은 기권이지 악재가 아니다(role_05의 원칙 승계).
            # 있는 날에도 아직 채점하지 않는다 — 공시 채점은 role_05 통합의
            # 몫이고, 여기서 임의의 숫자를 넣으면 그게 판단을 희석한다.
            disclosure_score=None,
            # 뉴스는 수집되지만 **투표하지 않는다**. 우리 뉴스 소스(Alpaca)는
            # 전 기사가 benzinga이고 news_trust_policy에서 gray(0.50)라
            # gates.source_trust_min(0.55)을 못 넘는다 — 점수를 실어 보내도
            # role_07이 그 표를 박탈하므로, 넣는 순간 값비싼 유령이 된다.
            # 대신 헤드라인이 위 프롬프트의 증거 종합에 들어가 evidence.score를
            # 통해 확신도에 기여한다: **투표권 없이 영향은 준다.**
            # 문턱을 소스에 맞춰 내리는 것은 정책 오염이라 하지 않는다.
            news_score=None,
            is_daily_pick=True,
            disclosure_snapshot_at=cycle_ts,
            news_snapshot_at=cycle_ts,
            held_quantity=holding.quantity,
            entry_price=(
                None if holding.entry_price is None else float(holding.entry_price)
            ),
            business_days_held=holding.business_days_held,
            evidence_ids=(f"{run_id}:{subject.ticker}",),
        )
        conviction = StrategyOutput.vote_conviction(
            strategy_input, self.gates, evidence.score
        )
        decision = StrategyOutput.from_model(
            strategy_input,
            conviction,
            evidence.reason,
            gates=self.gates,
            profile=self.profile,
        )
        signal_id = await domain.save_signal(
            StrategistSignalWrite(
                run_id=run_id,
                trade_date=as_of,
                ticker=subject.ticker,
                cycle_ts=cycle_ts,
                side=decision.side,
                conviction=Decimal(str(decision.conviction)),
                summary=decision.summary[:500],
                decision_close=subject.close,
                evidence=decision.evidence_ids,
                inv_type=self.profile_name,
                signal_consensus=StrategyOutput.vote_consensus(
                    strategy_input, self.gates, self.profile, evidence.score
                ),
            )
        )
        verdict = await self._verify(subject, decision, signal_id, cycle_ts, run_id)
        _ = await domain.save_verdict(
            CriticVerdictWrite(
                signal_id=signal_id,
                ticker=subject.ticker,
                decision=verdict.decision,
                category=verdict.category,
                objection=verdict.objection[:500],
                confidence=Decimal(str(verdict.confidence)),
                decided_layer=verdict.decided_layer,
            )
        )
        return AnalysisOutcome(
            ticker=subject.ticker,
            side=decision.side,
            conviction=decision.conviction,
            approved=verdict.decision == "pass",
        )

    async def _verify(
        self,
        subject: AnalysisSubject,
        decision: StrategyOutput,
        signal_id: int,
        cycle_ts: datetime,
        run_id: str,
    ) -> CriticVerdict:
        """Critique the proposal, or record why no critique was needed.

        hold은 아무것도 집행하지 않으므로 모델을 부르지 않는다 — 콜 예산은
        실제로 돈이 움직이는 판단에만 쓴다.
        """
        if decision.side == "hold":
            return CriticVerdict(
                run_id=run_id,
                signal_id=signal_id,
                ticker=subject.ticker,
                decision="hold",
                category="no_action_proposal",
                objection="집행할 제안 없음",
                confidence=1.0,
                decided_layer="gate",
                evidence_ids=decision.evidence_ids,
            )
        critic_input = CriticInput(
            run_id=run_id,
            signal_id=signal_id,
            ticker=subject.ticker,
            cycle_ts=cycle_ts,
            side=decision.side,
            conviction=decision.conviction,
            current_price=float(subject.close),
            day_high=float(subject.high),
            day_low=float(subject.low),
            close_prev=float(subject.close_prev or subject.close),
            disclosure_filed_at=cycle_ts,
            news_published_at=cycle_ts,
            evidence_ids=decision.evidence_ids,
        )
        blocked = CriticVerdict.apply_hard_gates(critic_input)
        if blocked is not None:
            return blocked
        review = await self.analyzer.analyze(
            AnalysisTask.CRITIC,
            f"proposal={decision.side} ticker={subject.ticker}"
            f" conviction={decision.conviction} rationale={decision.summary}",
        )
        threshold = max(self.gates.critic_approval, 0.0)
        if decision.conviction >= self.gates.overconfidence_conviction:
            # 과신할수록 반박을 더 세게 통과해야 한다(M4 승인 문턱 규칙 승계).
            threshold = max(threshold, self.gates.overconfidence_approval)
        passed = review.score >= threshold
        return CriticVerdict(
            run_id=run_id,
            signal_id=signal_id,
            ticker=subject.ticker,
            decision="pass" if passed else "reject",
            category="model_review",
            objection=review.reason,
            # pass 판정은 confidence < critic_approval 이어야 한다는 계약이 있다
            # (require_pass_gate_proof) — 승인은 확신이 아니라 통과의 기록이다.
            confidence=0.0 if passed else float(review.score),
            # "model"이 아니라 "llm"이다 — 계약의 Literal에 없는 값이라
            # 크리틱이 반박에 **성공한** 첫 종목에서 그날 분석 전체가 죽었다.
            # mock 크리틱이 고정 0.82로 늘 통과해서 이 갈래는 실 LLM을 붙이기
            # 전까지 한 번도 실행되지 않았다. 이름은 role_08/service.py:137을 따른다.
            decided_layer="gate" if passed else "llm",
            evidence_ids=decision.evidence_ids,
        )
