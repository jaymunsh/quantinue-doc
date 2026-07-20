"""Typed write records for canonical trading-domain persistence."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from typing_extensions import override

from quantinue.core.ontology import FillSide


@dataclass(frozen=True, slots=True)
class StrategistSignalWrite:
    """Database-complete strategist signal linked to source snapshots."""

    run_id: str
    trade_date: date
    ticker: str
    cycle_ts: datetime
    side: str
    conviction: Decimal
    summary: str
    decision_close: Decimal
    evidence: tuple[str, ...]
    # 기본값이 없는 이유: 이 열은 `UNIQUE (ticker, cycle_ts, inv_type)`의 축이라
    # 어느 페르소나가 판단했는지가 곧 행의 정체성이다. 기본값을 두면 부르는
    # 쪽이 말하지 않아도 통과하는데, 실제로 그렇게 해서 aggressive로 돌린
    # 판단이 원장에 전부 conservative로 찍혀 있었다 — 성향 2종 팬아웃이
    # 붙는 순간 두 페르소나가 같은 행을 덮어쓴다. 말하지 않으면 못 쓰게 한다.
    inv_type: str
    disclosure_score: Decimal = Decimal(0)
    news_score: Decimal = Decimal(0)
    signal_consensus: int = 0
    # 판단 서사 — 모델이 만들었을 때만 존재한다(None = 안 만들었다, "" 금지).
    bull_case: str | None = None
    key_risk: str | None = None
    # 판단이 읽은 국면 관측의 시각(tb_macro FK). 안 읽었으면 계보도 없다.
    src_macro_at: datetime | None = None
    # 판단이 투표에 쓴 공시 채점 행의 시각(tb_disclosure_signal FK). 채점이
    # 없는 종목은 기권이므로 계보도 없다 — 없는 부모를 가리키면 FK가 막는다.
    src_disclosure_at: datetime | None = None
    # 어느 모델·프롬프트가 판단했는가 — 재현과 사후 감사의 전제.
    model_provider: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    input_hash: str | None = None


@dataclass(frozen=True, slots=True)
class CriticVerdictWrite:
    """Canonical critic outcome for a persisted signal."""

    signal_id: int
    ticker: str
    decision: str
    category: str
    objection: str
    confidence: Decimal
    decided_layer: str
    verdict_source: str = "fresh"
    # 이 판정에 적용되지 않은 게이트들. 화면("건너뛴 규칙")이 읽는 값이고,
    # 비어 있으면 "전부 검증했다"로 읽힌다 — 매도는 그렇지 않다.
    skipped_rules: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountWrite:
    """Paper account snapshot used by risk and order records."""

    broker_account_id: str
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    currency: str = "USD"
    inv_type: str | None = None
    """공격형/안전형 — 프로필 선택의 근거. None이면 기본 프로필을 쓴다."""


@dataclass(frozen=True, slots=True)
class OrderReconciliation:
    """Broker state applied to an already-reserved canonical order."""

    idempotency_key: str
    status: str
    broker_order_id: str | None
    parent_order_id: str | None = None
    stop_leg_order_id: str | None = None
    take_profit_leg_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class FillWrite:
    """One broker fill linked to its canonical order."""

    order_id: int
    side: str
    quantity: int
    price: Decimal
    filled_at: datetime
    broker_fill_id: str


@dataclass(frozen=True, slots=True)
class CompletedFillWrite:
    """One app-owned filled order applied atomically to the local account.

    ``side`` defaults to a buy so every pre-close call site keeps its meaning;
    only a close order has to state it.
    """

    idempotency_key: str
    broker_order_id: str
    broker_fill_id: str
    quantity: int
    price: Decimal
    filled_at: datetime
    side: FillSide = FillSide.BUY


class InsufficientSimulatedCashError(ValueError):
    """A local fill whose notional exceeds durable available cash."""

    def __init__(self, available: Decimal, required: Decimal) -> None:
        """Retain typed amounts while exposing only a stable error message."""
        self.available = available
        self.required = required
        super().__init__("insufficient simulated cash")

    @override
    def __str__(self) -> str:
        """Return a stable non-sensitive boundary message."""
        return "insufficient simulated cash"


@dataclass(frozen=True, slots=True)
class OrderPlanWrite:
    """Role 09's decision for one ticker and cycle, blocked or not."""

    run_id: str
    ticker: str
    cycle_ts: datetime
    trade_date: date
    decision: str
    quantity: int
    account_id: int | None = None
    signal_id: int | None = None
    skipped_reason: str | None = None
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class AccountRiskState:
    """One account's capital and book size at decision time."""

    account_id: int
    cash: Decimal
    equity: Decimal
    open_position_count: int
    inv_type: str | None


@dataclass(frozen=True, slots=True)
class CloseOrderReservation:
    """One idempotent close order awaiting broker execution."""

    signal_id: int
    account_id: int
    ticker: str
    quantity: int
    reference_price: Decimal
    closes_order_id: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DailyBarWrite:
    """One exchange session's OHLCV for one ticker.

    ``source``를 함께 담는 이유: 시세 소스가 폴백 체인(Alpaca → Stooq → …)이라
    같은 날 다른 소스가 섞일 수 있고, 값이 이상할 때 어디서 왔는지 물을 수
    있어야 한다.
    """

    trade_date: date
    ticker: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    source: str


@dataclass(frozen=True, slots=True)
class DailyPickWrite:
    """One row of a session's analysis scope.

    role_03의 ``DailyPick`` 계약을 재사용하지 않는다. 그쪽은 구 11단계 러너의
    경계 계약이라 픽 수 상한(50)과 증거 ID 규칙을 함께 들고 있는데, 잡 경로의
    범위는 ``screening.llm_depth``와 보유 수가 정하므로 그 상한이 근거를 잃는다.
    구 러너가 사라질 때(Phase 4) 저쪽만 지우면 되도록 갈라 둔다.
    """

    trade_date: date
    ticker: str
    universe_as_of: date
    bucket: str
    rank: int
    sector: str
    score: Decimal


@dataclass(frozen=True, slots=True)
class RawDisclosureWrite:
    """One filing from the day's whole-market index, matched to a ticker.

    ``tb_disclosure``(채점 결과)와 따로 두는 이유: 그쪽은
    ``(trade_date, ticker) → tb_daily_pick`` FK를 걸어 **그날 분석 대상이 아닌
    종목에는 행을 넣을 수 없다**. 그런데 일괄 수집이 노리는 것이 정확히 그
    바깥이다 — 스크리너에서 탈락한 보유 종목의 상장폐지 공시. 원시 원장은
    픽과 무관하게 받고, 채점은 분석 대상에만 한다.
    """

    filing_no: str
    trade_date: date
    ticker: str
    cik: str
    form_type: str
    company_name: str
    source_ref: str
    event_type: str | None
    is_hard_event: bool


@dataclass(frozen=True, slots=True)
class DisclosureSignalWrite:
    """One ticker's scored filings — the row role_07 votes on.

    ``cycle_ts``가 분석 잡의 자정과 같아야 하는 이유는 조인이 아니라 **계보**다:
    ``tb_strategist_signals.src_disclosure_at``이 ``(ticker, cycle_ts)``로 이 표를
    가리키는 FK라, 어긋나면 판단이 자기 근거를 못 가리킨다.

    점수가 ``sentiment_score``에 앉는 이유: 이 표에서 "얼마나 호재인가"를 담는
    칸이 그것이다(``importance``는 사건의 크기, ``risk_score``는 하방). 우리는
    폼 종류만 보고 채점하므로 정직하게 답할 수 있는 것이 방향 하나뿐이다.
    """

    ticker: str
    cycle_ts: datetime
    trade_date: date
    has_signal: bool
    sentiment_score: float
    disclosure_count: int
    # 어느 모델이 이 표를 만들었는가. 스키마가 model_provider를 NOT NULL로
    # 요구하기도 하지만, 근본적으로 채점은 판단의 입력이라 출처 없이 남으면
    # 나중에 "이 표를 믿어도 되나"에 답할 수 없다.
    model_provider: str = "unknown"
    model_name: str | None = None


@dataclass(frozen=True, slots=True)
class RawNewsWrite:
    """One article, recorded once per ticker it names.

    기사가 아니라 **(기사, 티커)**가 한 행인 이유: 소비가 종목 단위다. 기사
    단위로 저장하고 심볼 배열을 컬럼에 담으면 "이 종목의 어제 헤드라인"을
    묻는 질의가 배열 스캔이 되고, 그게 분석 잡이 매번 하는 유일한 질문이다.

    ``tb_news``(LLM 채점 결과)와 따로 두는 이유는 공시와 같다 — 그쪽은
    ``(trade_date, ticker) → tb_daily_pick`` FK를 걸어 그날 분석 대상이 아닌
    종목에는 행을 넣을 수 없는데, 일괄 수집이 노리는 것이 그 바깥이다.

    ``source``를 남기는 이유: 뉴스가 투표권을 못 갖는 근거가 정확히 출처
    등급이다(benzinga = gray 0.50 < ``gates.source_trust_min`` 0.55). 등급을
    코드가 가정하는 대신 원장이 증언하게 한다 — 나중에 ``allow`` 등급 소스
    (로드맵 R11)가 붙으면 그때 이 컬럼이 판정의 근거가 된다.
    """

    article_id: int
    ticker: str
    trade_date: date
    headline: str
    source: str
    url: str
    published_at: datetime


@dataclass(frozen=True, slots=True)
class BuyCandidate:
    """One critic-approved buy proposal awaiting allocation.

    ``reference_price``는 판단 기준가(decision_close)다 — 일 1회 로컬 시뮬에서
    체결가가 곧 이 값이므로 사이징도 같은 값으로 한다. ``rank``는 정렬의
    동률 깨기 전용이다: 확신도에 스크리닝 점수를 다시 섞는 것은 결함 12
    (상위 랭킹 보유를 팔 수 없던 산술)의 반복이라 하지 않는다.
    """

    signal_id: int
    ticker: str
    inv_type: str
    conviction: Decimal
    reference_price: Decimal
    rank: int | None
    # 최근 5세션 상승률(비율). late_entry 게이트의 입력 — 구 경로에서는
    # role_02의 ret_5d가 줬는데 새 경로에 없으면 그 게이트가 유령이 된다.
    # 봉이 5세션 미만이면 None — 잴 수 없는 것을 0으로 지어내지 않는다.
    recent_return: float | None = None


@dataclass(frozen=True, slots=True)
class MacroSnapshot:
    """The market regime the ledger last recorded.

    분석 잡이 매크로를 보게 하려고 만든 최소 계약이다. ``regime``은 크리틱의
    차단 판정(성향별)에, ``risk_score``는 확신도 감점에 쓰인다. ``as_of``는
    계보다 — 판단이 어느 국면 **관측** 위에서 내려졌는지를 원장에 남기려면
    읽은 행의 시각이 필요하다(``tb_strategist_signals.src_macro_at`` FK).
    """

    regime: str
    risk_score: float
    as_of: datetime


@dataclass(frozen=True, slots=True)
class KnownListing:
    """The last thing the ledger ever knew about a ticker's listing.

    상장폐지된 보유를 유니버스로 이월할 때 회사명·시총을 지어내지 않기 위해
    있다. 한 번도 유니버스에 없던 종목은 살 수도 없었으므로 여기서 안 나오는
    것이 정상이고, 그런 티커는 이월하지 않는다 — 없는 계보를 만들지 않는다.
    """

    company_name: str
    market_cap: int
