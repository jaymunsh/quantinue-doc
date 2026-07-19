"""Phase 5: the job-shaped control room page and its API.

구 관제실은 "런 하나가 11단계를 어디까지 갔나"를 그렸고, 잡 기반에서는 그
질문이 성립하지 않는다. 여기서 고정하는 것은 새 질문이다 — 오늘 체인이
어디서 끊겼고, 무엇을 샀고 왜 못 샀고, 두 성향이 어떻게 갈렸나.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from quantinue.core.config import Settings
from quantinue.db.control_room_reads import (
    AccountEquityPoint,
    JobRunRecord,
    JudgementRecord,
    OrderPlanRecord,
)
from quantinue.db.memory import InMemoryRunStore
from quantinue.main import create_app

_DAY = date(2026, 7, 20)
_START = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)


class _StubReads:
    """The job ledger reads, answered from fixtures instead of PostgreSQL."""

    def __init__(  # noqa: PLR0913 - 원장 축이 곧 인자다, 옵션 스프롤이 아니다
        self,
        *,
        slot_date: date | None = _DAY,
        jobs: tuple[JobRunRecord, ...] = (),
        plans: tuple[OrderPlanRecord, ...] = (),
        equity: tuple[AccountEquityPoint, ...] = (),
        judged: tuple[JudgementRecord, ...] = (),
        older_slots: tuple[date, ...] = (),
    ) -> None:
        self._older_slots = older_slots
        self._slot_date = slot_date
        self._jobs = jobs
        self._plans = plans
        self._equity = equity
        self._judged = judged

    async def latest_job_slot(self) -> date | None:
        return self._slot_date

    async def recent_job_slots(self, *, limit: int) -> tuple[date, ...]:
        assert limit > 0
        return (*self._older_slots, self._slot_date) if self._slot_date else ()

    async def job_runs(self, slot_date: date) -> tuple[JobRunRecord, ...]:
        return self._jobs if slot_date == self._slot_date else ()

    async def order_plans(self, trade_date: date) -> tuple[OrderPlanRecord, ...]:
        return self._plans if trade_date == self._slot_date else ()

    async def account_equity_series(self, *, days: int) -> tuple[AccountEquityPoint, ...]:
        assert days > 0
        return self._equity

    async def judgements(self, trade_date: date) -> tuple[JudgementRecord, ...]:
        return self._judged if trade_date == self._slot_date else ()


class _LedgerStore(InMemoryRunStore):
    """An in-memory store that also exposes a job ledger, the way Postgres does."""

    def __init__(self, reads: _StubReads) -> None:
        super().__init__()
        self.domain = reads


def _job(
    name: str,
    *,
    status: str = "succeeded",
    detail: str | None = "ok",
    offset_minutes: int = 0,
    finished: bool = True,
) -> JobRunRecord:
    started = _START + timedelta(minutes=offset_minutes)
    return JobRunRecord(
        job_name=name,
        slot_date=_DAY,
        status=status,
        detail=detail,
        started_at=started,
        finished_at=started + timedelta(seconds=12) if finished else None,
    )


def _chain_list(body: str) -> str:
    """Return only the rendered job chain, since the page inlines the stylesheet."""
    opening = body.index('<ol class="job-chain-list">')
    return body[opening : body.index("</ol>", opening)]


def _client(reads: _StubReads) -> TestClient:
    settings = Settings(app_name="Quantinue Test")
    return TestClient(create_app(settings, store=_LedgerStore(reads)))


def test_the_page_renders_the_chain_in_execution_order() -> None:
    # Given
    reads = _StubReads(
        jobs=(
            _job("universe", offset_minutes=0),
            _job("daily_bars", offset_minutes=1),
            _job("allocation", offset_minutes=2),
        )
    )

    # When
    with _client(reads) as client:
        body = client.get("/").text

    # Then — CSS가 인라인이라 페이지 전체 검색은 클래스 이름에 걸린다
    chain = _chain_list(body)
    assert chain.index("universe") < chain.index("daily_bars") < chain.index("allocation")


def test_a_chain_without_failures_does_not_claim_it_finished_everything() -> None:
    """등록된 잡 중 몇 개가 돌았는지는 원장이 모른다 — 분모를 지어내면 안 된다.

    실 dev DB에서 잡힌 결함이다: 9개 중 3개만 돈 슬롯을 화면이 "체인 완주"로
    그렸다. 자격증명에 따라 잡 등록 자체가 갈리므로 완주는 원장만으로 말할 수
    있는 사실이 아니다.
    """
    # Given
    reads = _StubReads(jobs=(_job("screening"), _job("analysis", offset_minutes=1)))

    # When
    with _client(reads) as client:
        body = client.get("/").text

    # Then
    assert "완주" not in body
    assert "잡 2개 실행" in body


def test_an_older_slot_can_be_opened_from_the_navigation() -> None:
    """하루만 볼 수 있으면 '어제 뭐가 깨졌나'를 물을 수 없다."""
    # Given
    yesterday = _DAY - timedelta(days=1)
    reads = _StubReads(jobs=(_job("universe"),), older_slots=(yesterday,))

    # When
    with _client(reads) as client:
        body = client.get("/").text
        picked = client.get(f"/api/pipeline/today?slot={yesterday}").json()

    # Then
    assert f'href="/?slot={yesterday}"' in body
    assert picked["chain"]["slot_date"] == yesterday.isoformat()


def test_a_slot_that_never_ran_falls_back_to_the_latest_one() -> None:
    """없는 날을 빈 화면으로 그리면 '그날은 조용했다'로 읽힌다."""
    # Given
    reads = _StubReads(jobs=(_job("universe"),))

    # When
    with _client(reads) as client:
        payload = client.get("/api/pipeline/today?slot=1999-01-01").json()

    # Then
    assert payload["chain"]["slot_date"] == _DAY.isoformat()


def test_a_broken_chain_names_the_job_that_broke_it() -> None:
    """어디서 끊겼는지가 상태 집계보다 먼저 보여야 한다."""
    # Given
    reads = _StubReads(
        jobs=(
            _job("universe"),
            _job("daily_bars", status="failed", detail="alpaca 400", offset_minutes=1),
        )
    )

    # When
    with _client(reads) as client:
        body = client.get("/").text

    # Then
    assert "체인이 daily_bars에서 끊겼습니다" in body
    assert "alpaca 400" in body


def test_an_installation_that_never_ran_a_job_still_renders() -> None:
    """잡을 아직 안 켠 것도 정상 상태다 — 그때 화면이 죽으면 안 된다."""
    # Given
    reads = _StubReads(slot_date=None)

    # When
    with _client(reads) as client:
        response = client.get("/")

    # Then
    assert response.status_code == 200
    assert "아직 돌지 않았습니다" in response.text


def test_a_store_without_a_job_ledger_renders_the_empty_room() -> None:
    """메모리 스토어에는 tb_job_run이 없다. 500이 아니라 빈 관제실이 맞다."""
    # Given
    settings = Settings(app_name="Quantinue Test")

    # When
    with TestClient(create_app(settings, store=InMemoryRunStore())) as client:
        response = client.get("/")

    # Then
    assert response.status_code == 200
    assert "아직 돌지 않았습니다" in response.text


def test_the_page_shows_why_the_allocation_stopped_buying() -> None:
    """산 것만 보이면 '후보가 없었다'와 '지갑이 막았다'가 같아 보인다."""
    # Given
    plans = (
        OrderPlanRecord(
            ticker="AAA",
            account_id=1,
            trade_date=_DAY,
            decision="planned",
            skipped_reason=None,
            quantity=10,
            entry_price=Decimal(50),
        ),
        OrderPlanRecord(
            ticker="BBB",
            account_id=1,
            trade_date=_DAY,
            decision="skipped",
            skipped_reason="min_cash",
            quantity=0,
            entry_price=None,
        ),
    )
    reads = _StubReads(jobs=(_job("allocation"),), plans=plans)

    # When
    with _client(reads) as client:
        body = client.get("/").text

    # Then
    assert "1 매수 · 1 보류" in body
    assert "min_cash" in body


def test_the_page_separates_the_two_investment_profiles() -> None:
    """성향 격차는 이 시스템의 주장이라 합쳐 보여주면 안 된다."""
    # Given
    judged = (
        JudgementRecord(
            ticker="AAA",
            inv_type="aggressive",
            side="buy",
            conviction=Decimal("0.800"),
            summary="공격형 판단",
            bull_case="상대강도 상위",
            key_risk="국면 반전",
            verdict_decision="pass",
            verdict_confidence=Decimal("0.700"),
            objection="반박 없음",
        ),
        JudgementRecord(
            ticker="AAA",
            inv_type="conservative",
            side="hold",
            conviction=Decimal("0.500"),
            summary="안전형 판단",
            bull_case=None,
            key_risk=None,
            verdict_decision="reject",
            verdict_confidence=Decimal("0.600"),
            objection="거래량이 평균의 절반",
        ),
    )
    reads = _StubReads(jobs=(_job("analysis"),), judged=judged)

    # When
    with _client(reads) as client:
        body = client.get("/").text

    # Then
    assert "aggressive" in body
    assert "conservative" in body
    assert "거래량이 평균의 절반" in body
    # 판단 서사(잔여 작업 B): 모델이 만든 근거·리스크가 화면에 도달한다.
    assert "근거: 상대강도 상위" in body
    assert "리스크: 국면 반전" in body


def test_the_api_answers_with_the_same_day_the_page_draws() -> None:
    """화면과 API가 다른 원장을 보면 관제실을 신뢰할 수 없다."""
    # Given
    reads = _StubReads(
        jobs=(_job("universe"), _job("daily_bars", status="failed", offset_minutes=1)),
        equity=(
            AccountEquityPoint(
                account_id=1, trade_date=_DAY - timedelta(days=1), equity=Decimal(1000)
            ),
            AccountEquityPoint(account_id=1, trade_date=_DAY, equity=Decimal(1100)),
        ),
    )

    # When
    with _client(reads) as client:
        payload = client.get("/api/pipeline/today").json()

    # Then
    assert payload["chain"]["broke_at"] == "daily_bars"
    assert payload["chain"]["slot_date"] == "2026-07-20"
    assert payload["curves"][0]["change_pct"] == "10.00"


@pytest.mark.parametrize("status", ["running", "succeeded", "failed"])
def test_every_job_status_renders(status: str) -> None:
    # Given
    reads = _StubReads(jobs=(_job("universe", status=status, finished=status != "running"),))

    # When
    with _client(reads) as client:
        response = client.get("/")

    # Then
    assert response.status_code == 200
    assert status in response.text
