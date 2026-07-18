# M1: 슬롯 멱등 + NYSE 캘린더 + 스케줄러 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 앱을 켜두면 사람 없이 거래일에 파이프라인 사이클이 자동 실행되고, 같은 슬롯의 수동+자동 동시 발화는 1회만 실행된다.

**Architecture:** cycle_ts를 기간 경계로 내림(slot_of)하면 기존 `deterministic_run_key + store.claim`이 같은 슬롯 중복을 원자적으로 dedup한다(신규 락 불필요). NYSE 캘린더(`exchange-calendars` XNYS)가 거래일·세션 게이트를 제공하고, lifespan의 anyio task group 안에서 60초 틱 스케줄러 루프가 기존 `DueRoleScheduler` seam + 창 검사 → `LiveRunRuntime.start()`로 사이클을 트리거한다. 첫 틱이 자연스럽게 catch-up이 된다.

**Tech Stack:** Python 3.11 · anyio · pydantic v2 · exchange-calendars(신규 의존성) · pytest(anyio 모드)

## Global Constraints

- 작업 위치 `app-v2/` 전용. `app/`(1차) 절대 수정 금지.
- 검증 명령: `cd app-v2 && uv run pytest tests/unit -q` — 기존 493개 항상 green.
- 문턱·주기·한도 하드코딩 금지 → `config/pipeline.yaml` + typed 모델(policy.py 패턴).
- 모든 datetime은 tz-aware 강제(naive → 즉시 예외). 저장·비교는 UTC, 창 정의는 America/New_York.
- 커밋 메시지: `feat|fix|test(m1): 요약`. 태스크 단위 1커밋.
- 코드 스타일: frozen dataclass/BaseModel·Protocol seam·한국어 도메인 docstring 없이 영어 docstring(기존 코드 관례).

## 기존 코드 팩트 (구현자가 알아야 할 것)

- `orchestration/lifecycle.py:141` `deterministic_run_key(ticker, cycle_ts) -> RunKey` — sha256, tz 필수.
- `orchestration/pipeline.py:104-113` `PipelineOrchestrator.run()`이 key로 `store.claim()` → 선점 실패 시 기존 런 대기/반환. **같은 cycle_ts면 이미 멱등.**
- `main.py:52-54` `_pipeline_request(ticker)`가 `datetime.now(UTC).replace(second=0, microsecond=0)` — 분 단위 절단이라 1분만 지나도 새 런. 이걸 slot_of로 교체하는 게 M1-2.
- `orchestration/policy.py:192-218` `DueRoleScheduler.due_roles(at, last_runs: Mapping[str, datetime]) -> tuple[str,...]` — 순수 seam, roles "04"/"05"/"06"/"07", `SchedulePlan.periods()` 기반. 이미 존재·테스트됨.
- `api/live_runtime.py:28` `LiveRunRuntime.start(request) -> bool` — task group에 런 스폰, 키 중복 시 False.
- `main.py:91-103` lifespan이 `anyio.create_task_group()` 보유 — 스케줄러 루프를 여기 스폰.
- `db/contracts.py:187` `RunStore` Protocol — 구현 2종: `db/memory.py` InMemoryRunStore · `db/postgres.py`.
- `pipeline_runs` 테이블: `cycle_ts TIMESTAMPTZ, status IN ('pending','running','completed','failed','timed_out')`.
- `orchestration/policy.py:168` `load_pipeline_policy(path)` — yaml→json→Pydantic 패턴.
- config 최상위에 `timezone: America/New_York` 이미 있음.

## File Structure

| 파일 | 책임 |
|---|---|
| Create `src/quantinue/orchestration/slots.py` | 순수 슬롯 함수 1개 |
| Create `src/quantinue/core/market_calendar.py` | XNYS 캘린더 어댑터 (거래일·세션·영업일 가감) |
| Create `src/quantinue/orchestration/scheduler.py` | 스케줄러 루프(순수 tick 로직 + anyio 루프) |
| Modify `src/quantinue/main.py` | slot_of 배선 · lifespan에 루프 스폰 · catchup 엔드포인트 |
| Modify `src/quantinue/orchestration/policy.py` | Mvp2ScheduleConfig typed 모델 추가 |
| Modify `config/pipeline.yaml` | `mvp2:` 블록(슬롯 주기·창·타임존) |
| Modify `src/quantinue/db/contracts.py`·`memory.py`·`postgres_read.py`(또는 postgres.py) | `latest_cycle_ts()` 추가 |
| Modify `pyproject.toml` | `exchange-calendars>=4.5` 추가 |

---

### Task 1: 슬롯 함수 `slot_of`

**Files:**
- Create: `src/quantinue/orchestration/slots.py`
- Test: `tests/unit/test_slots.py`

**Interfaces:**
- Produces: `slot_of(now: datetime, period_minutes: int) -> datetime` — UTC 기준 자정으로부터 period_minutes 경계로 내림, tz-aware 강제(naive면 ValueError), 반환은 UTC tz-aware.

- [ ] **Step 1: 실패 테스트 작성**

```python
"""Slot quantization: any moment inside a period maps to one deterministic slot."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from quantinue.orchestration.slots import slot_of


def test_moments_inside_same_period_share_one_slot() -> None:
    base = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)
    assert slot_of(base + timedelta(minutes=0), 30) == base
    assert slot_of(base + timedelta(minutes=17, seconds=42), 30) == base
    assert slot_of(base + timedelta(minutes=29, seconds=59), 30) == base


def test_boundary_maps_to_itself() -> None:
    boundary = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)
    assert slot_of(boundary, 30) == boundary


def test_slot_is_floored_from_utc_midnight() -> None:
    # 45-minute periods: 13:30 UTC is not a boundary (810 % 45 = 0 → it is).
    # Use 50-minute period: floor(13*60+30, 50) = 810 - 810%50 = 800 → 13:20.
    now = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)
    assert slot_of(now, 50) == datetime(2026, 7, 20, 13, 20, tzinfo=UTC)


def test_non_utc_input_is_normalized_to_utc() -> None:
    kst = timezone(timedelta(hours=9))
    now_kst = datetime(2026, 7, 20, 22, 47, tzinfo=kst)  # = 13:47 UTC
    assert slot_of(now_kst, 30) == datetime(2026, 7, 20, 13, 30, tzinfo=UTC)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        slot_of(datetime(2026, 7, 20, 13, 30), 30)  # noqa: DTZ001


def test_nonpositive_period_is_rejected() -> None:
    with pytest.raises(ValueError, match="period"):
        slot_of(datetime(2026, 7, 20, tzinfo=UTC), 0)
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/unit/test_slots.py -q` · Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: 최소 구현**

```python
"""Deterministic slot quantization for idempotent pipeline cycles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def slot_of(now: datetime, period_minutes: int) -> datetime:
    """Floor a tz-aware moment to its UTC period boundary.

    Every moment inside one period maps to the same slot, so cycle keys
    derived from the slot collapse duplicate manual/automatic triggers.
    """
    if now.tzinfo is None:
        msg = "now must include a timezone"
        raise ValueError(msg)
    if period_minutes <= 0:
        msg = "period_minutes must be a positive period"
        raise ValueError(msg)
    normalized = now.astimezone(UTC)
    midnight = normalized.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = int((normalized - midnight).total_seconds() // 60)
    return midnight + timedelta(minutes=elapsed - elapsed % period_minutes)
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/unit/test_slots.py -q` · Expected: 6 passed
- [ ] **Step 5: 전체 회귀** — Run: `uv run pytest tests/unit -q` · Expected: 499 passed
- [ ] **Step 6: Commit** — `git add app-v2/src/quantinue/orchestration/slots.py app-v2/tests/unit/test_slots.py && git commit -m "feat(m1): slot_of — UTC 기간 경계 내림으로 사이클 슬롯 양자화"`

---

### Task 2: NYSE 캘린더 어댑터

**Files:**
- Create: `src/quantinue/core/market_calendar.py`
- Modify: `pyproject.toml` (dependencies에 `"exchange-calendars>=4.5",` 추가)
- Test: `tests/unit/test_market_calendar.py`

**Interfaces:**
- Produces: class `NyseCalendar` —
  - `is_trading_day(day: date) -> bool`
  - `session_open(day: date) -> datetime` (UTC tz-aware; 비거래일이면 ValueError)
  - `session_close(day: date) -> datetime` (UTC)
  - `add_business_days(day: date, count: int) -> date` (count ≥ 0; 0이면 해당일 이후 첫 세션)
  - `is_market_open(moment: datetime) -> bool` (정규장 여부, naive 거부)
  - `current_session(moment: datetime) -> Session` — `Session` = StrEnum(`PRE|REGULAR|AFTER|CLOSED`), 프리 04:00 ET 시작·애프터 20:00 ET 종료(세션 정책 2026-07-18)

- [ ] **Step 1: 의존성 추가** — `pyproject.toml` dependencies 배열에 `"exchange-calendars>=4.5",` 추가 후 `uv sync`. Expected: Installed N packages.

- [ ] **Step 2: 실패 테스트 작성**

```python
"""XNYS calendar adapter: trading days, sessions, and extended-hours windows."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from quantinue.core.market_calendar import NyseCalendar, Session

NY_WINTER = timezone(timedelta(hours=-5))  # EST
NY_SUMMER = timezone(timedelta(hours=-4))  # EDT


@pytest.fixture(scope="module")
def calendar() -> NyseCalendar:
    return NyseCalendar()


def test_weekday_is_trading_day_and_weekend_is_not(calendar: NyseCalendar) -> None:
    assert calendar.is_trading_day(date(2026, 7, 20)) is True  # Monday
    assert calendar.is_trading_day(date(2026, 7, 18)) is False  # Saturday


def test_holiday_is_not_a_trading_day(calendar: NyseCalendar) -> None:
    assert calendar.is_trading_day(date(2026, 7, 3)) is False  # Independence Day observed
    assert calendar.is_trading_day(date(2026, 12, 25)) is False


def test_session_open_close_are_utc(calendar: NyseCalendar) -> None:
    opened = calendar.session_open(date(2026, 7, 20))
    closed = calendar.session_close(date(2026, 7, 20))
    assert opened == datetime(2026, 7, 20, 13, 30, tzinfo=UTC)  # 09:30 EDT
    assert closed == datetime(2026, 7, 20, 20, 0, tzinfo=UTC)  # 16:00 EDT


def test_session_open_rejects_non_trading_day(calendar: NyseCalendar) -> None:
    with pytest.raises(ValueError, match="trading"):
        calendar.session_open(date(2026, 7, 18))


def test_add_business_days_skips_weekend(calendar: NyseCalendar) -> None:
    # Fill T+5 from Monday 07-20: Tue,Wed,Thu,Fri,Mon → 07-27.
    assert calendar.add_business_days(date(2026, 7, 20), 5) == date(2026, 7, 27)


def test_add_business_days_skips_holiday(calendar: NyseCalendar) -> None:
    # 07-03 observed holiday: Wed 07-01 + 2 sessions → Mon 07-06.
    assert calendar.add_business_days(date(2026, 7, 1), 2) == date(2026, 7, 6)


def test_dst_transition_days_keep_correct_utc_open(calendar: NyseCalendar) -> None:
    # US DST 2026: begins 03-08, ends 11-01. Following Monday opens shift by an hour in UTC.
    assert calendar.session_open(date(2026, 3, 9)) == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)
    assert calendar.session_open(date(2026, 11, 2)) == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)


def test_current_session_partitions_a_trading_day(calendar: NyseCalendar) -> None:
    day = date(2026, 7, 20)
    assert calendar.current_session(datetime(2026, 7, 20, 5, 0, tzinfo=NY_SUMMER)) is Session.PRE
    assert (
        calendar.current_session(datetime(2026, 7, 20, 10, 0, tzinfo=NY_SUMMER))
        is Session.REGULAR
    )
    assert (
        calendar.current_session(datetime(2026, 7, 20, 17, 0, tzinfo=NY_SUMMER)) is Session.AFTER
    )
    assert (
        calendar.current_session(datetime(2026, 7, 20, 21, 0, tzinfo=NY_SUMMER)) is Session.CLOSED
    )
    assert calendar.current_session(datetime(2026, 7, 20, 3, 0, tzinfo=NY_SUMMER)) is Session.CLOSED
    del day


def test_current_session_is_closed_on_weekend(calendar: NyseCalendar) -> None:
    assert (
        calendar.current_session(datetime(2026, 7, 18, 10, 0, tzinfo=NY_SUMMER)) is Session.CLOSED
    )


def test_is_market_open(calendar: NyseCalendar) -> None:
    assert calendar.is_market_open(datetime(2026, 7, 20, 14, 0, tzinfo=UTC)) is True
    assert calendar.is_market_open(datetime(2026, 7, 20, 12, 0, tzinfo=UTC)) is False


def test_naive_moment_is_rejected(calendar: NyseCalendar) -> None:
    with pytest.raises(ValueError, match="timezone"):
        calendar.is_market_open(datetime(2026, 7, 20, 14, 0))  # noqa: DTZ001
```

- [ ] **Step 3: 실패 확인** — Run: `uv run pytest tests/unit/test_market_calendar.py -q` · Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 4: 구현**

```python
"""NYSE (XNYS) trading calendar adapter with extended-hours session windows."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum, unique
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

NEW_YORK = ZoneInfo("America/New_York")
PRE_SESSION_START = time(4, 0)
AFTER_SESSION_END = time(20, 0)


@unique
class Session(StrEnum):
    """Where a moment falls inside one New York trading day."""

    PRE = "pre"
    REGULAR = "regular"
    AFTER = "after"
    CLOSED = "closed"


def _require_timezone(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        msg = "moment must include a timezone"
        raise ValueError(msg)
    return moment


@lru_cache(maxsize=1)
def _xnys() -> xcals.ExchangeCalendar:
    return xcals.get_calendar("XNYS")


class NyseCalendar:
    """Answer trading-day, session-boundary, and business-day questions."""

    def __init__(self) -> None:
        """Bind the cached XNYS calendar."""
        self._calendar = _xnys()

    def is_trading_day(self, day: date) -> bool:
        """Return whether the exchange holds a session on this date."""
        return bool(self._calendar.is_session(day.isoformat()))

    def session_open(self, day: date) -> datetime:
        """Return the regular-session open in UTC; reject non-trading days."""
        if not self.is_trading_day(day):
            msg = f"{day.isoformat()} is not a trading day"
            raise ValueError(msg)
        opened = self._calendar.session_open(day.isoformat())
        return opened.to_pydatetime().astimezone(UTC)

    def session_close(self, day: date) -> datetime:
        """Return the regular-session close in UTC; reject non-trading days."""
        if not self.is_trading_day(day):
            msg = f"{day.isoformat()} is not a trading day"
            raise ValueError(msg)
        closed = self._calendar.session_close(day.isoformat())
        return closed.to_pydatetime().astimezone(UTC)

    def add_business_days(self, day: date, count: int) -> date:
        """Advance count sessions past the given date (T+N settlement math)."""
        if count < 0:
            msg = "count must not be negative"
            raise ValueError(msg)
        current = day
        remaining = count
        while remaining > 0 or not self.is_trading_day(current):
            current += timedelta(days=1)
            if self.is_trading_day(current):
                remaining -= 1
            if remaining <= 0 and self.is_trading_day(current):
                break
        return current

    def is_market_open(self, moment: datetime) -> bool:
        """Return whether the regular session is open at this moment."""
        return self.current_session(moment) is Session.REGULAR

    def current_session(self, moment: datetime) -> Session:
        """Partition a moment into pre/regular/after/closed (America/New_York)."""
        local = _require_timezone(moment).astimezone(NEW_YORK)
        day = local.date()
        if not self.is_trading_day(day):
            return Session.CLOSED
        opened = self.session_open(day).astimezone(NEW_YORK)
        closed = self.session_close(day).astimezone(NEW_YORK)
        if opened <= local < closed:
            return Session.REGULAR
        if local.timetz().replace(tzinfo=None) >= PRE_SESSION_START and local < opened:
            return Session.PRE
        if closed <= local and local.timetz().replace(tzinfo=None) < AFTER_SESSION_END:
            return Session.AFTER
        return Session.CLOSED
```

주의: `add_business_days`의 while 로직이 복잡하면 단순 루프로 재작성 가능 — 요구 성질은 테스트가 정의(주말·휴일 건너뛰고 count 세션 전진). `session_open` 반환형이 pandas Timestamp인 점(→ `.to_pydatetime()`)은 exchange-calendars 4.x 기준. 실제 API가 다르면 테스트를 기준으로 어댑터 내부만 조정.

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/unit/test_market_calendar.py -q` · Expected: 11 passed
- [ ] **Step 6: 전체 회귀** — `uv run pytest tests/unit -q` · Expected: 510 passed
- [ ] **Step 7: Commit** — `git add app-v2/pyproject.toml app-v2/uv.lock app-v2/src/quantinue/core/market_calendar.py app-v2/tests/unit/test_market_calendar.py && git commit -m "feat(m1): NYSE 캘린더 어댑터 — 거래일·세션창(pre/regular/after)·영업일 가감"`

---

### Task 3: mvp2 스케줄러 config 블록

**Files:**
- Modify: `config/pipeline.yaml`
- Modify: `src/quantinue/orchestration/policy.py`
- Test: `tests/unit/test_pipeline_policy.py` (기존 파일에 케이스 추가)

**Interfaces:**
- Produces: `Mvp2ScheduleConfig` (BaseModel, frozen) — `enabled: bool=False` · `tick_seconds: int=60` · `cycle_slot_minutes: int=30` · `trigger_sessions: tuple[str,...]=("pre","regular","after")`; `PipelineConfigDocument`에 `mvp2: Mvp2Config` 추가(`Mvp2Config.schedule: Mvp2ScheduleConfig`). `load_mvp2_config(path) -> Mvp2Config` 로더.

- [ ] **Step 1: 실패 테스트 작성** (`tests/unit/test_pipeline_policy.py`에 추가)

```python
def test_mvp2_schedule_config_loads_from_yaml() -> None:
    from quantinue.orchestration.policy import load_mvp2_config

    config = load_mvp2_config(Path("config/pipeline.yaml"))

    assert config.schedule.enabled is False  # 기본은 꺼짐 — W0 수동 운용 보호
    assert config.schedule.tick_seconds == 60
    assert config.schedule.cycle_slot_minutes == 30
    assert config.schedule.trigger_sessions == ("pre", "regular", "after")


def test_mvp2_schedule_rejects_unknown_session() -> None:
    from quantinue.orchestration.policy import Mvp2ScheduleConfig

    with pytest.raises(ValidationError):
        Mvp2ScheduleConfig(trigger_sessions=("lunch",))
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/unit/test_pipeline_policy.py -q` · Expected: FAIL (ImportError)

- [ ] **Step 3: 구현** — `config/pipeline.yaml` 말미에 추가:

```yaml
mvp2:
  schedule:
    enabled: false
    tick_seconds: 60
    cycle_slot_minutes: 30
    trigger_sessions: [pre, regular, after]
```

`policy.py`에 추가 (기존 `PipelineConfigDocument`·`load_pipeline_policy` 패턴을 그대로 따름):

```python
class Mvp2ScheduleConfig(BaseModel):
    """Automatic cycle trigger cadence and gates; disabled until armed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    tick_seconds: int = Field(default=60, gt=0, le=3_600)
    cycle_slot_minutes: int = Field(default=30, gt=0, le=1_440)
    trigger_sessions: tuple[Literal["pre", "regular", "after"], ...] = (
        "pre",
        "regular",
        "after",
    )


class Mvp2Config(BaseModel):
    """MVP-2 configuration surface owned by config/pipeline.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schedule: Mvp2ScheduleConfig = Mvp2ScheduleConfig()


def load_mvp2_config(path: Path) -> Mvp2Config:
    """Load the mvp2 block; absent block yields safe defaults (disabled)."""
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream) or {}
    return Mvp2Config.model_validate(document.get("mvp2", {}))
```

기존 `PipelineConfigDocument`가 `extra="forbid"`라면 `mvp2: Mvp2Config = Mvp2Config()` 필드를 문서 모델에도 추가해 로딩이 깨지지 않게 한다(기존 `load_pipeline_policy` 회귀 테스트가 잡아줌).

- [ ] **Step 4: 통과 + 회귀** — `uv run pytest tests/unit/test_pipeline_policy.py tests/unit -q` · Expected: all passed
- [ ] **Step 5: Commit** — `git commit -m "feat(m1): mvp2.schedule config — 슬롯 주기·틱·트리거 세션(기본 disabled)"`

---

### Task 4: 수동 트리거를 슬롯 경로로 (M1-2)

**Files:**
- Modify: `src/quantinue/main.py` (`_pipeline_request`)
- Test: `tests/unit/test_main_slotting.py` (신규)

**Interfaces:**
- Consumes: `slot_of` (Task 1) · `Mvp2Config` (Task 3)
- Produces: `_pipeline_request(ticker, *, slot_minutes: int) -> PipelineRequest` — cycle_ts가 슬롯 경계. `create_app`이 `load_mvp2_config`로 slot_minutes를 주입.

- [ ] **Step 1: 실패 테스트 작성**

```python
"""Manual API triggers must share the automatic slot identity."""

from datetime import UTC, datetime

from quantinue.main import _pipeline_request


def test_pipeline_request_quantizes_cycle_to_slot(monkeypatch) -> None:
    import quantinue.main as main_module

    class _FrozenDatetime:
        @staticmethod
        def now(tz):  # noqa: ANN001, ANN205
            return datetime(2026, 7, 20, 13, 47, 23, tzinfo=tz)

    monkeypatch.setattr(main_module, "datetime", _FrozenDatetime)

    request = _pipeline_request("NVDA", slot_minutes=30)

    assert request.cycle_ts == datetime(2026, 7, 20, 13, 30, tzinfo=UTC)


def test_two_calls_inside_one_slot_share_cycle_ts(monkeypatch) -> None:
    import quantinue.main as main_module

    moments = iter(
        [
            datetime(2026, 7, 20, 13, 31, tzinfo=UTC),
            datetime(2026, 7, 20, 13, 58, tzinfo=UTC),
        ]
    )

    class _SteppingDatetime:
        @staticmethod
        def now(tz):  # noqa: ANN001, ANN205
            del tz
            return next(moments)

    monkeypatch.setattr(main_module, "datetime", _SteppingDatetime)

    first = _pipeline_request("NVDA", slot_minutes=30)
    second = _pipeline_request("NVDA", slot_minutes=30)

    assert first.cycle_ts == second.cycle_ts  # → deterministic_run_key 동일 → claim dedup
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/unit/test_main_slotting.py -q` · Expected: FAIL (TypeError: unexpected keyword)

- [ ] **Step 3: 구현** — `main.py` 수정:

```python
from quantinue.orchestration.policy import load_mvp2_config
from quantinue.orchestration.slots import slot_of

CONFIG_PATH = Path("config/pipeline.yaml")


def _pipeline_request(ticker: str, *, slot_minutes: int) -> PipelineRequest:
    cycle_ts = slot_of(datetime.now(UTC), slot_minutes)
    return PipelineRequest(ticker=ticker, cycle_ts=cycle_ts)
```

`create_app` 안에서 `mvp2_config = load_mvp2_config(CONFIG_PATH)` 1회 로드 후, `_pipeline_request(...)` 호출부 전부(`POST /runs`·`POST /api/runs`·`POST /api/runs/async` 핸들러)를 `slot_minutes=mvp2_config.schedule.cycle_slot_minutes`로 교체. 호출부는 `grep -n "_pipeline_request" src/quantinue/main.py`로 전수 확인.

- [ ] **Step 4: 통과 + 전체 회귀** — `uv run pytest tests/unit -q` + `uv run pytest tests -q --ignore=tests/integration` · Expected: all passed (test_web.py의 런 생성 테스트가 cycle_ts 분절단을 단언하고 있으면 슬롯 값으로 갱신 — 같은 커밋에서 수정)
- [ ] **Step 5: Commit** — `git commit -m "feat(m1): 수동 트리거 cycle_ts를 슬롯 양자화 — 같은 슬롯 중복 실행은 기존 claim이 dedup"`

---

### Task 5: `RunStore.latest_cycle_ts()` (last_runs 데이터원)

**Files:**
- Modify: `src/quantinue/db/contracts.py` (RunStore Protocol)
- Modify: `src/quantinue/db/memory.py` · `src/quantinue/db/postgres_read.py`(포스트그레스 read 계열 — 실제 클래스 위치는 `grep -n "class.*RunStore" src/quantinue/db/*.py`로 확인)
- Test: `tests/unit/test_memory_store.py`(기존 memory 테스트 파일에 추가; 없으면 신규) · 통합은 기존 postgres 테스트 파일 패턴에 추가

**Interfaces:**
- Produces: `async def latest_cycle_ts(self) -> datetime | None` — 상태 `pending|running|completed`(실패 제외) 중 최대 cycle_ts. 스케줄러가 이를 모든 role의 last_run으로 사용(전 역할이 한 사이클에 함께 돌기 때문 — M3에서 역할 분리 시 세분화).

- [ ] **Step 1: 실패 테스트 작성** (memory 구현 기준)

```python
@pytest.mark.anyio
async def test_latest_cycle_ts_ignores_failed_runs() -> None:
    store = InMemoryRunStore()
    await store.initialize()
    early = PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 20, 13, 30, tzinfo=UTC))
    late = PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 20, 14, 0, tzinfo=UTC))

    claim_early = await store.claim(str(deterministic_run_key("NVDA", early.cycle_ts)), early)
    assert claim_early.acquired
    await store.finish_run(
        str(deterministic_run_key("NVDA", early.cycle_ts)),
        claim_early.context.to_run(),
    )
    claim_late = await store.claim(str(deterministic_run_key("NVDA", late.cycle_ts)), late)
    assert claim_late.acquired
    await store.abandon(str(deterministic_run_key("NVDA", late.cycle_ts)))  # 실패 처리

    assert await store.latest_cycle_ts() == early.cycle_ts


@pytest.mark.anyio
async def test_latest_cycle_ts_none_when_empty() -> None:
    store = InMemoryRunStore()
    await store.initialize()
    assert await store.latest_cycle_ts() is None
```

주의: InMemoryRunStore의 내부 표현(런 저장 방식·`abandon` 후 상태)은 구현 확인 후 테스트의 세팅 코드를 실제 API에 맞춰 조정한다. 성질(실패 제외 최대 cycle_ts / 빈 스토어 None)은 불변.

- [ ] **Step 2: 실패 확인** — Expected: AttributeError (latest_cycle_ts 없음)

- [ ] **Step 3: 구현** — contracts.py Protocol에 메서드 추가:

```python
    async def latest_cycle_ts(self) -> datetime | None:
        """Return the newest non-failed cycle timestamp, if any."""
        ...
```

memory.py — 내부 런 맵을 순회해 status가 failed/timed_out이 아닌 것 중 max. postgres — read 모듈에:

```sql
SELECT max(cycle_ts) FROM pipeline_runs
WHERE status IN ('pending', 'running', 'completed')
```

- [ ] **Step 4: 통과 + 회귀** — `uv run pytest tests/unit -q` · Expected: all passed
- [ ] **Step 5: Commit** — `git commit -m "feat(m1): RunStore.latest_cycle_ts — 스케줄러 last_runs 데이터원"`

---

### Task 6: 스케줄러 루프 (M1-5·1-7 핵심)

**Files:**
- Create: `src/quantinue/orchestration/scheduler.py`
- Test: `tests/unit/test_scheduler.py`

**Interfaces:**
- Consumes: `DueRoleScheduler.due_roles` · `NyseCalendar.current_session`·`is_trading_day` · `slot_of` · `RunStore.latest_cycle_ts` · `LiveRunRuntime.start`
- Produces:
  - `@dataclass(frozen=True) TickDecision` — `triggered: bool` · `reason: str`(`"due"`/`"disabled"`/`"closed_session"`/`"holiday"`/`"not_due"`) · `cycle_ts: datetime | None`
  - `class CycleScheduler` — `__init__(config: Mvp2ScheduleConfig, calendar: NyseCalendar, scheduler: DueRoleScheduler, store: RunStore, trigger: Callable[[PipelineRequest], bool], ticker: str)` · `async def tick(now: datetime) -> TickDecision`(순수 판정+트리거) · `async def run_forever() -> None`(anyio sleep 루프 — tick 예외는 로그 후 계속)
  - 첫 틱 = catch-up: 마지막 런이 오래됐으면 due로 판정되어 현재 슬롯으로 1회 실행(과거 슬롯 소급 없음 — slot_of(now)만 사용)

- [ ] **Step 1: 실패 테스트 작성**

```python
"""Automatic cycle scheduler: due + window + slot idempotency."""

from datetime import UTC, datetime

import pytest

from quantinue.core.contracts import PipelineRequest
from quantinue.core.market_calendar import NyseCalendar
from quantinue.orchestration.policy import (
    Mvp2ScheduleConfig,
    default_schedule_plan,
    DueRoleScheduler,
)
from quantinue.orchestration.scheduler import CycleScheduler

MONDAY_REGULAR = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)  # 10:00 ET Monday
SATURDAY = datetime(2026, 7, 18, 14, 0, tzinfo=UTC)


class _StoreStub:
    def __init__(self, latest: datetime | None) -> None:
        self._latest = latest

    async def latest_cycle_ts(self) -> datetime | None:
        return self._latest


class _TriggerSpy:
    def __init__(self) -> None:
        self.requests: list[PipelineRequest] = []

    def __call__(self, request: PipelineRequest) -> bool:
        self.requests.append(request)
        return True


def _scheduler(latest: datetime | None, *, enabled: bool = True) -> tuple[CycleScheduler, _TriggerSpy]:
    spy = _TriggerSpy()
    instance = CycleScheduler(
        config=Mvp2ScheduleConfig(enabled=enabled),
        calendar=NyseCalendar(),
        scheduler=DueRoleScheduler(default_schedule_plan()),
        store=_StoreStub(latest),
        trigger=spy,
        ticker="NVDA",
    )
    return instance, spy


@pytest.mark.anyio
async def test_first_tick_with_no_history_triggers_catchup() -> None:
    scheduler, spy = _scheduler(latest=None)
    decision = await scheduler.tick(MONDAY_REGULAR)
    assert decision.triggered is True
    assert decision.reason == "due"
    assert spy.requests[0].cycle_ts == datetime(2026, 7, 20, 14, 0, tzinfo=UTC)  # slot_of(now, 30)


@pytest.mark.anyio
async def test_recent_run_is_not_due() -> None:
    scheduler, spy = _scheduler(latest=datetime(2026, 7, 20, 13, 50, tzinfo=UTC))
    decision = await scheduler.tick(MONDAY_REGULAR)
    assert decision.triggered is False
    assert decision.reason == "not_due"
    assert spy.requests == []


@pytest.mark.anyio
async def test_weekend_never_triggers() -> None:
    scheduler, spy = _scheduler(latest=None)
    decision = await scheduler.tick(SATURDAY)
    assert decision.triggered is False
    assert decision.reason == "holiday"
    assert spy.requests == []


@pytest.mark.anyio
async def test_closed_session_on_trading_day_does_not_trigger() -> None:
    scheduler, spy = _scheduler(latest=None)
    night = datetime(2026, 7, 20, 3, 0, tzinfo=UTC)  # 23:00 ET Sunday→Mon 예: 03:00 UTC = 23:00 ET 전날
    decision = await scheduler.tick(night)
    assert decision.triggered is False
    assert decision.reason in {"closed_session", "holiday"}
    assert spy.requests == []


@pytest.mark.anyio
async def test_disabled_scheduler_never_triggers() -> None:
    scheduler, spy = _scheduler(latest=None, enabled=False)
    decision = await scheduler.tick(MONDAY_REGULAR)
    assert decision.triggered is False
    assert decision.reason == "disabled"
    assert spy.requests == []


@pytest.mark.anyio
async def test_same_slot_double_tick_sends_same_cycle_key() -> None:
    scheduler, spy = _scheduler(latest=None)
    _ = await scheduler.tick(datetime(2026, 7, 20, 14, 1, tzinfo=UTC))
    scheduler_second, spy_second = _scheduler(latest=None)
    _ = await scheduler_second.tick(datetime(2026, 7, 20, 14, 16, tzinfo=UTC))
    # 다른 프로세스/수동 발화가 같은 슬롯에서 나가도 cycle_ts 동일 → claim이 dedup
    assert spy.requests[0].cycle_ts == spy_second.requests[0].cycle_ts
```

- [ ] **Step 2: 실패 확인** — Expected: ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
"""Automatic cycle scheduler: 60s tick → due check → windowed slot trigger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import anyio
import structlog

from quantinue.core.contracts import PipelineRequest
from quantinue.core.market_calendar import Session
from quantinue.orchestration.slots import slot_of

if TYPE_CHECKING:
    from collections.abc import Callable

    from quantinue.core.market_calendar import NyseCalendar
    from quantinue.orchestration.policy import DueRoleScheduler, Mvp2ScheduleConfig


class _LatestCycleSource(Protocol):
    async def latest_cycle_ts(self) -> datetime | None: ...


@dataclass(frozen=True, slots=True)
class TickDecision:
    """One tick's verdict, kept observable for logs and the admin API."""

    triggered: bool
    reason: str
    cycle_ts: datetime | None = None


class CycleScheduler:
    """Trigger idempotent pipeline cycles while a trading session is open."""

    def __init__(
        self,
        config: Mvp2ScheduleConfig,
        calendar: NyseCalendar,
        scheduler: DueRoleScheduler,
        store: _LatestCycleSource,
        trigger: Callable[[PipelineRequest], bool],
        ticker: str,
    ) -> None:
        """Bind collaborators; the loop owns no state beyond them."""
        self._config = config
        self._calendar = calendar
        self._scheduler = scheduler
        self._store = store
        self._trigger = trigger
        self._ticker = ticker
        self._logger: structlog.stdlib.BoundLogger = structlog.get_logger("scheduler")

    async def tick(self, now: datetime) -> TickDecision:
        """Decide and, when due inside an allowed session, trigger one cycle."""
        if not self._config.enabled:
            return TickDecision(triggered=False, reason="disabled")
        if not self._calendar.is_trading_day(now.astimezone(UTC).date()):
            return TickDecision(triggered=False, reason="holiday")
        session = self._calendar.current_session(now)
        if session is Session.CLOSED or session.value not in self._config.trigger_sessions:
            return TickDecision(triggered=False, reason="closed_session")
        latest = await self._store.latest_cycle_ts()
        last_runs = (
            {} if latest is None else {role: latest for role, _ in self._roles_with_periods()}
        )
        if latest is not None and not self._scheduler.due_roles(now, last_runs):
            return TickDecision(triggered=False, reason="not_due")
        cycle_ts = slot_of(now, self._config.cycle_slot_minutes)
        request = PipelineRequest(ticker=self._ticker, cycle_ts=cycle_ts)
        self._trigger(request)
        return TickDecision(triggered=True, reason="due", cycle_ts=cycle_ts)

    def _roles_with_periods(self) -> tuple[tuple[str, object], ...]:
        return self._scheduler.plan_periods()

    async def run_forever(self) -> None:
        """Tick forever; a failing tick is logged and never kills the loop."""
        while True:
            try:
                decision = await self.tick(datetime.now(UTC))
                if decision.triggered:
                    await self._logger.ainfo(
                        "scheduler.cycle.triggered", cycle_ts=str(decision.cycle_ts)
                    )
            except Exception:  # noqa: BLE001 — 루프 생존이 우선
                await self._logger.aexception("scheduler.tick.failed")
            await anyio.sleep(self._config.tick_seconds)
```

`DueRoleScheduler`에 `plan_periods()` 공개가 없으면 policy.py에 1줄 추가(`return self._plan.periods()`) — 같은 커밋.

- [ ] **Step 4: 통과 + 회귀** — `uv run pytest tests/unit/test_scheduler.py tests/unit -q` · Expected: all passed
- [ ] **Step 5: Commit** — `git commit -m "feat(m1): CycleScheduler — due·세션창·슬롯 판정 틱 + 생존 루프(첫 틱=catch-up)"`

---

### Task 7: lifespan 배선 + 관리자 catch-up API

**Files:**
- Modify: `src/quantinue/main.py` (lifespan에 루프 스폰 · `POST /api/scheduler/catchup` · `GET /api/scheduler/status`)
- Test: `tests/test_web.py`(기존 파일 패턴에 추가)

**Interfaces:**
- Consumes: `CycleScheduler` (Task 6) · `Mvp2Config` (Task 3)
- Produces: `POST /api/scheduler/catchup` → `{"triggered": bool, "reason": str, "cycle_ts": str | null}` (강제 1틱 — enabled=false여도 검사만은 수행하도록 catchup은 `tick()` 호출 전 enabled 체크를 우회하지 않음: disabled면 `{"triggered": false, "reason": "disabled"}` 반환이 정직) · `GET /api/scheduler/status` → `{"enabled": bool, "last_decision": {...} | null}`

- [ ] **Step 1: 실패 테스트 작성** (test_web.py의 기존 클라이언트 픽스처 패턴 사용)

```python
@pytest.mark.anyio
async def test_scheduler_catchup_reports_decision(client) -> None:  # noqa: ANN001
    response = await client.post("/api/scheduler/catchup")
    assert response.status_code == 200
    body = response.json()
    assert body["triggered"] is False  # config 기본 enabled=false
    assert body["reason"] == "disabled"


@pytest.mark.anyio
async def test_scheduler_status_exposes_enabled_flag(client) -> None:  # noqa: ANN001
    response = await client.get("/api/scheduler/status")
    assert response.status_code == 200
    assert response.json()["enabled"] is False
```

- [ ] **Step 2: 실패 확인** — Expected: 404

- [ ] **Step 3: 구현** — `create_app`에서 `CycleScheduler` 조립(트리거 = `live_run_runtime.start` 경유 — 런타임이 None인 창구를 피하려고 클로저로 지연 참조), lifespan task_group에 `task_group.start_soon(scheduler.run_forever)` (enabled=false면 스폰 생략), 엔드포인트 2개 추가. 마지막 TickDecision을 앱 상태(`app.state.last_scheduler_decision`)에 저장해 status에서 노출.

- [ ] **Step 4: 통과 + 전체 회귀** — `uv run pytest tests -q --ignore=tests/integration` · Expected: all passed
- [ ] **Step 5: Commit** — `git commit -m "feat(m1): lifespan 스케줄러 스폰 + /api/scheduler/catchup·status"`

---

### Task 8: 슬롯 멱등 E2E(멀티트리거) 검증 + 문서 갱신

**Files:**
- Test: `tests/unit/test_slot_idempotency.py` (신규 — orchestrator 레벨)
- Modify: `docs/mvp2-planning/dev-playbook.md` · `docs/quantinue-integrated-design.html`

- [ ] **Step 1: E2E-2 테스트 작성** — mock 구성으로 같은 슬롯 cycle_ts 2회 `orchestrator.run()` → run_id 동일(두 번째는 기존 런 반환), InMemoryRunStore에 런 1개:

```python
"""E2E-2: two triggers inside one slot resolve to a single pipeline run."""

from datetime import UTC, datetime

import pytest

from quantinue.broker.mock import MockBroker
from quantinue.core.contracts import PipelineRequest
from quantinue.db.memory import InMemoryRunStore
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.orchestration.factory import build_roles
from quantinue.orchestration.pipeline import PipelineOrchestrator


@pytest.mark.anyio
async def test_same_slot_double_run_returns_single_run() -> None:
    store = InMemoryRunStore()
    roles = build_roles(DeterministicAnalyzer(), MockBroker(), store=store)
    orchestrator = PipelineOrchestrator(roles, store)
    slot = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)
    request = PipelineRequest(ticker="NVDA", cycle_ts=slot)

    first = await orchestrator.run(request)
    second = await orchestrator.run(request)

    assert first.run_id == second.run_id
    assert await store.latest_cycle_ts() == slot
```

- [ ] **Step 2: 통과 확인** (이 테스트는 기존 claim 덕에 바로 통과할 것 — 통과 자체가 "신규 락 불필요" 검증) · Expected: 1 passed
- [ ] **Step 3: 전체 회귀 + 정적 검사** — `uv run pytest tests -q --ignore=tests/integration` · ruff/pyright 있으면 `uv run ruff check src tests`
- [ ] **Step 4: 문서 갱신** — dev-playbook M1 표에 ✅·발견사항(claim 재사용으로 1-6 흡수 등) 기록 · 정본 HTML #logic에 "자동 스케줄·슬롯 멱등" as-built 카드 + changelog(코드 확정 후 미러 규칙)
- [ ] **Step 5: Commit** — `git commit -m "test(m1): E2E-2 같은 슬롯 이중 실행 → 단일 런" && git commit -m "docs(m1): M1 완료 반영"` (코드/문서 분리)

---

## 완료 기준 대조 (playbook M1)

| 기준 | 커버 태스크 |
|---|---|
| 같은 슬롯 2회 실행 → signal 1행 | Task 8 (run 단일화 → stage08 1회) |
| 동시 발화 중복 0 | Task 4+8 (슬롯 양자화 + 기존 claim) |
| 휴장일 07~10 미실행 | Task 6 (holiday/closed_session 게이트 — 사이클 자체 미발화) |
| 재시작 후 catch-up | Task 6 (첫 틱 due 판정) + Task 7 (수동 catchup API) |
| 앱 켜두면 하루 사이클 자동 완주 | Task 6+7 (enabled=true 시) — 실검증은 W0-8 이후 운영에서 |
| DST 전환일 | Task 2 (3/9·11/2 UTC 개장 시각 테스트) |

## 스코프 노트 (구현자가 헷갈리지 않도록)

- **역할별 창(role_01 weekly 등)은 M1에 없다.** 현재 파이프라인은 01→11을 원자적으로 돌므로 역할 단위 창 강제는 무의미 — M3(깔때기)에서 역할 분리와 함께 확장한다. M1의 창은 "사이클 트리거 허용 세션"(pre/regular/after)뿐.
- `enabled` 기본 false — W0 수동 운용 중 자동 발화 방지. 운영 전환 시 yaml 한 줄로 켠다.
- catch-up은 "현재 슬롯 1회"다. 과거 슬롯 소급 실행 금지(playbook R9).
