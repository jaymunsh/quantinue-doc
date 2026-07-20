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
from typing import TYPE_CHECKING, Final

from quantinue.core.market_calendar import NyseCalendar
from quantinue.db.domain_records import CriticVerdictWrite, StrategistSignalWrite
from quantinue.llm.provider import AnalysisTask
from quantinue.roles.analysis.contracts import (
    HoldingContext,
    analysis_prompt,
    analysis_run_id,
    critique_prompt,
)
from quantinue.roles.exits.contracts import business_days_held
from quantinue.roles.role_07_strategist.contracts import StrategyInput, StrategyOutput
from quantinue.roles.role_08_critic.contracts import CriticInput, CriticVerdict

# 원장의 국면 문자열 → 크리틱 계약의 Literal. 모르는 값은 중립으로 떨어뜨린다:
# 못 읽은 국면을 위험회피로 읽으면 파싱 실패가 매수 금지가 된다.
_REGIMES: Final = {
    "risk_on": "risk_on",
    "neutral": "neutral",
    "risk_off": "risk_off",
}

if TYPE_CHECKING:
    from datetime import date

    from quantinue.db.domain_records import MacroSnapshot
    from quantinue.llm.provider import LlmAnalyzer
    from quantinue.orchestration.policy import GatesConfig, ProfileConfig
    from quantinue.roles.analysis.contracts import AnalysisSubject
    from quantinue.roles.screening import RankedCandidate


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    """What the job decided for one ticker."""

    ticker: str
    side: str
    conviction: float
    approved: bool


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    """What one persona's pass over today's scope produced.

    건너뛴 수를 함께 돌려주는 이유: 잡 원장(``tb_job_run.detail``)이 "20종목
    분석"이라고만 적으면 범위가 22였다는 사실이 사라진다. 조용한 절단은
    "전부 봤다"로 읽힌다.
    """

    outcomes: tuple[AnalysisOutcome, ...]
    skipped: int = 0


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

    async def run(self, *, as_of: date, session: date) -> AnalysisRun:
        """Decide, verify, and persist one signal per ticker in scope."""
        domain = getattr(self.store, "domain", self.store)
        subjects = await domain.analysis_subjects(as_of, session)
        if not subjects:
            return AnalysisRun(())
        tickers = tuple(subject.ticker for subject in subjects)
        filings = await domain.disclosure_evidence(session, tickers)
        headlines = await domain.news_evidence(
            session, tickers, self.headlines_per_ticker
        )
        holdings = await self._holdings(domain, as_of)
        # 매크로는 종목마다 같으므로 한 번만 읽는다. 이 값이 없으면 감점도
        # 차단도 없다 — 모르는 것을 근거로 막지 않는다.
        macro = await self._macro(domain, as_of)
        # 스크리닝이 이미 계산한 창 지표. 원장에는 합성 점수 하나만 남아서
        # 모델이 자기 방법론의 입력을 못 보고 있었다 — 다시 묻는 3.8초가
        # 승인율을 좌우한다.
        indicators = await self._indicators(domain, as_of, session)
        # 공시 채점 잡이 이 슬롯에 남긴 표. 종목마다 한 번만 읽는다 — 채점은
        # 성향과 무관하므로 두 페르소나가 같은 값을 본다.
        disclosure_scores = await domain.disclosure_scores(as_of)
        outcomes: list[AnalysisOutcome] = []
        failures = 0
        for subject in subjects:
            try:
                outcome = await self._analyse(
                    domain,
                    subject,
                    holdings.get(subject.ticker, HoldingContext()),
                    filings.get(subject.ticker, ()),
                    headlines.get(subject.ticker, ()),
                    macro,
                    indicators.get(subject.ticker),
                    as_of=as_of,
                    disclosure_score=disclosure_scores.get(subject.ticker),
                )
            except Exception:  # noqa: BLE001 - 종목 하나의 실패를 격리하는 자리다
                # 종목 하나가 그날 판단 전체를 지우지 않게 한다. 실측: 구조화
                # 출력을 한 번 놓친 종목 때문에 그 성향 22종목이 통째로 날아갔다.
                # 이미 원장에 앉은 판단은 유효하고, 못 본 종목은 다음 슬롯이 본다.
                failures += 1
                continue
            if outcome is not None:
                outcomes.append(outcome)
        if failures and not outcomes:
            # 전 종목 실패는 종목 문제가 아니라 모델이 죽은 것이다. 조용히
            # "0건 분석"으로 성공을 기록하면 슬롯이 잠겨 재시도되지 않는다.
            message = f"every subject failed ({failures})"
            raise RuntimeError(message)
        return AnalysisRun(tuple(outcomes), failures)

    async def _indicators(
        self, domain: object, as_of: date, session: date
    ) -> dict[str, RankedCandidate]:
        """Read the window indicators behind today's scope, if this store has them."""
        reader = getattr(domain, "pick_indicators", None)
        if reader is None:
            return {}
        return await reader(as_of, session)

    async def _macro(self, domain: object, as_of: date) -> MacroSnapshot | None:
        """Read the regime once per run, if this store knows about it."""
        reader = getattr(domain, "latest_macro", None)
        if reader is None:
            return None
        return await reader(as_of, self.gates.evidence_max_age_minutes)

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
        macro: MacroSnapshot | None,
        indicators: RankedCandidate | None,
        *,
        as_of: date,
        disclosure_score: float | None = None,
    ) -> AnalysisOutcome | None:
        """Run one ticker through evidence synthesis, the gates, and the critic."""
        cycle_ts = datetime.combine(as_of, time(), tzinfo=UTC)
        run_id = analysis_run_id(as_of, self.profile_name)
        evidence = await self.analyzer.analyze(
            AnalysisTask.STRATEGY,
            analysis_prompt(subject, holding, filings, headlines, indicators),
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
            # 채점 잡이 이 슬롯에 표를 남겼으면 그 값이 투표한다 — 뉴스와 달리
            # 공시는 vote_conviction에서 신뢰도 게이트를 타지 않는다(sec.gov는
            # allow이고, 애초에 disclosure_score 갈래에 게이트 조건이 없다).
            disclosure_score=disclosure_score,
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
            # 국면 감점(gates.macro_penalty_table)의 첫 소비자. 지금까지 새
            # 분석 경로는 매크로를 아예 보지 않아서 감점 표가 통째로 잠들어
            # 있었다 — 표를 고쳐도 아무 일이 일어나지 않았다는 뜻이다.
            macro_risk_score=0.0 if macro is None else macro.risk_score,
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
            # 하방은 상방과 다른 저울로 잰다. 확신도에는 스크리닝 점수가 섞여
            # 있는데, 픽은 정의상 그 점수 상위라 보유 종목의 여집합은 낮게
            # 눌린다 — 그대로 두면 매도가 산술적으로 발동하지 않는다.
            bearishness=StrategyOutput.vote_bearishness(
                strategy_input, self.gates, evidence.score
            ),
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
                # 판단 서사와 계보. 구 role_07이 채우던 것을 잡 전환에서 버리고
                # 있었다 — 프롬프트는 만들고 원장은 안 받는 상태였다. 서사가
                # 없으면 None 그대로 둔다(지어내지 않음), 국면 계보는 실제로
                # 읽은 tb_macro 행의 시각만 적는다.
                bull_case=evidence.bull_case,
                key_risk=evidence.key_risk,
                src_macro_at=None if macro is None else macro.as_of,
                # 점수 자체는 여기 안 적는다 — tb_strategist_signals에는
                # disclosure_score 컬럼이 없고(계약의 그 필드는 DB 집이 없는
                # 기존 유령이다), 값은 tb_disclosure_signal.sentiment_score에
                # 한 번만 산다. 판단은 그 행을 계보로 가리키기만 하면 된다.
                # 채점이 없으면 계보도 없다. 부모 행이 없는데 시각을 적으면
                # FK가 막고, 막지 않더라도 판단이 없는 근거를 가리키게 된다.
                src_disclosure_at=None if disclosure_score is None else cycle_ts,
                model_provider=evidence.metadata.provider
                if isinstance(evidence.metadata.provider, str)
                else evidence.metadata.provider.value,
                model_name=evidence.metadata.model,
                prompt_version=evidence.metadata.prompt_version,
                input_hash=evidence.metadata.input_hash,
            )
        )
        verdict = await self._verify(
            subject, decision, signal_id, cycle_ts, run_id, macro, indicators
        )
        _ = await domain.save_verdict(
            CriticVerdictWrite(
                signal_id=signal_id,
                ticker=subject.ticker,
                decision=verdict.decision,
                category=verdict.category,
                objection=verdict.objection[:500],
                confidence=Decimal(str(verdict.confidence)),
                decided_layer=verdict.decided_layer,
                skipped_rules=verdict.skipped_rules,
            )
        )
        return AnalysisOutcome(
            ticker=subject.ticker,
            side=decision.side,
            conviction=decision.conviction,
            approved=verdict.decision == "pass",
        )

    async def _verify(  # noqa: PLR0913 - 판정 하나를 만드는 데 필요한 사실들이다
        self,
        subject: AnalysisSubject,
        decision: StrategyOutput,
        signal_id: int,
        cycle_ts: datetime,
        run_id: str,
        macro: MacroSnapshot | None,
        indicators: RankedCandidate | None,
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
            macro_regime=_REGIMES.get("" if macro is None else macro.regime, "neutral"),
            evidence_ids=decision.evidence_ids,
        )
        skipped = CriticVerdict.skipped_rules_for(critic_input)
        # 성향이 여기까지 와야 risk_off_action이 의미를 갖는다. 안 넘기면
        # 크리틱이 두 성향을 똑같이 막고, 공격형의 penalty 선언은 원장 어디에도
        # 나타나지 않는다 — 선언만 있고 소비자가 없던 그 상태다.
        blocked = CriticVerdict.apply_hard_gates(
            critic_input, risk_off_action=self.profile.risk_off_action
        )
        if blocked is not None:
            return blocked.model_copy(update={"skipped_rules": skipped})
        review = await self.analyzer.analyze(
            AnalysisTask.CRITIC,
            critique_prompt(
                subject,
                decision.side,
                decision.conviction,
                decision.summary,
                indicators,
            ),
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
            # 매도는 매수용 게이트 셋을 통과하지 않는다 — 그 사실을 원장에
            # 남기지 않으면 화면이 "전부 검증했다"로 읽힌다.
            skipped_rules=skipped,
            evidence_ids=decision.evidence_ids,
        )
