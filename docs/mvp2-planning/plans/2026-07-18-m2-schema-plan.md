# M2: 스키마·계약 일괄 확장 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 이후 마일스톤(M3~M11)이 쓸 스키마·계약·config를 한 번에 확장하고, 기존 데이터 무손실 멱등 마이그레이션으로 실 DB에 적용한다.

**Architecture:** `db/schema.sql`(신규 설치 정본)과 신규 `db/migrations/mvp2.sql`(기존 DB 업그레이드)을 **쌍으로** 유지한다. 두 경로가 같은 최종 카탈로그로 수렴하는지는 기존 스키마 계약 테스트(`tests/integration/schema_sql_expectations.py`)가 강제한다. 구현(로직)은 이 마일스톤에 없다 — 컬럼·테이블·ENUM·config만.

**Tech Stack:** PostgreSQL 17 · SQLAlchemy(리플렉션) · pydantic v2 · pytest

## Global Constraints

- 작업 위치 `app-v2/` 전용. 검증: `cd app-v2 && uv run pytest tests/unit tests/test_web.py -q` — 기존 **542개 green 유지**.
- 통합(스키마 계약) 검증: `QUANTINUE_TEST_DATABASE_URL=... uv run pytest tests/integration -q` (Postgres 필요, 미설정 시 skip).
- 마이그레이션은 **전부 멱등**(`IF NOT EXISTS` / `DO $$ ... IF NOT EXISTS ... $$`) — 두 번 돌려도 안전.
- 기존 행 보존: 신규 컬럼은 NULL 허용 또는 DEFAULT 부여. `NOT NULL` 신설 금지(기존 행 깨짐).
- 문턱·한도는 전부 `config/pipeline.yaml` 소유 — 코드 리터럴 금지.
- 커밋: 태스크 단위 1커밋, `feat(m2): 요약`. 문서/코드 분리.

## 사전 조사로 확정된 사실 (⏳ 해소분)

- **⏳2-4 해소**: side CHECK 제약명 = **`tb_strategist_signals_side_check`** (실 DB 조회).
- **계획에 없던 충돌 발견**: `tb_critic_verdict`에 이미 `source TEXT NOT NULL CHECK (source IN ('fresh','cache','cooldown'))`가 있고 `CriticVerdictWrite.source="fresh"`로 **실사용 중**. R10 계보의 `source`(출처: sec/rss 등)와 의미가 다른 동명 컬럼.
  → **해결: 기존 컬럼을 `verdict_source`로 리네임**(캐시 상태 의미를 이름에 반영)하고 `source`를 계보 표준으로 비운다. 6개 계보 테이블의 컬럼 이름이 통일되어야 재현 계약(#16)이 성립.
- `tb_disclosure_signal`에는 계보 10컬럼이 **이미 있음**(모범 패턴). `tb_strategist_signals`·`tb_critic_verdict`에는 없음 → 이번에 추가.
- `tb_news_signal`은 이미 `news_count INT` + `top_evidence TEXT[]` 보유 → 2-3은 `tb_disclosure_signal`을 여기에 **대칭**시키는 작업.
- reason TEXT 4곳: `tb_disclosure`(schema.sql:35) · `tb_disclosure_signal`(:47) · `tb_news`(:61) · `tb_news_signal`(:73). (`drop_reason`·`hard_block_reason`은 대상 아님.)
- 현재 reason 값은 `domain_sources.py`의 하드코딩 문자열 4개("consumed by role 05" 등) → JSONB 전환 시 dict로 바꾼다. **점수별 실제 사유 채우기는 M4** (여기선 구조만).
- 스키마 계약 테스트가 `TABLES`/`PK`/`UNIQUE`/FK 집합을 강제 → 신규 테이블 3개 추가 시 이 파일도 같은 커밋에서 갱신.

---

### Task 1: ontology 확장

**Files:** Modify `src/quantinue/core/ontology.py` · Test `tests/unit/test_ontology_mvp2.py`(신규)

**Interfaces:** Produces: `Side.SELL="sell"` · `AccountStatus(ACTIVE/PAUSED/CLOSED)` · `UserRole(ADMIN/USER)` · `LlmTask(DISCLOSURE/NEWS/STRATEGY/CRITIC/REVIEW)`

- [ ] **Step 1: 실패 테스트**

```python
"""MVP-2 vocabularies added to the canonical ontology."""

from quantinue.core.ontology import AccountStatus, LlmTask, Side, UserRole


def test_side_supports_sell() -> None:
    assert Side.SELL == "sell"
    assert {item.value for item in Side} == {"buy", "hold", "sell"}


def test_account_status_vocabulary() -> None:
    assert {item.value for item in AccountStatus} == {"active", "paused", "closed"}


def test_user_role_vocabulary() -> None:
    assert {item.value for item in UserRole} == {"admin", "user"}


def test_llm_task_vocabulary_matches_call_sites() -> None:
    assert {item.value for item in LlmTask} == {
        "disclosure",
        "news",
        "strategy",
        "critic",
        "review",
    }
```

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/unit/test_ontology_mvp2.py -q` → ImportError
- [ ] **Step 3: 구현** — `ontology.py`의 `Side`에 `SELL = "sell"` 추가, 파일 말미에 3개 StrEnum 추가(`@unique`, 기존 스타일 준수)
- [ ] **Step 4: 통과 + 회귀** — `uv run pytest tests/unit tests/test_web.py -q` → 546 passed
- [ ] **Step 5: Commit** — `feat(m2): ontology 확장 — Side+SELL·AccountStatus·UserRole·LlmTask`

---

### Task 2: reason TEXT → JSONB ×4

**Files:** Modify `db/schema.sql`(4곳) · `src/quantinue/db/domain_sources.py`(insert 4곳) · Test `tests/unit/test_reason_payload.py`(신규)

**Interfaces:** Produces: 4개 reason 컬럼이 `JSONB NOT NULL DEFAULT '{}'`(tb_disclosure는 기존 NOT NULL 유지) · 쓰기 값은 `dict[str, str]`

- [ ] **Step 1: 실패 테스트** — reason 페이로드 빌더 계약

```python
"""Reason payloads are per-score maps, not opaque prose."""

import pytest

from quantinue.db.reason import ReasonPayload, reason_payload


def test_reason_payload_keys_are_score_columns() -> None:
    payload = reason_payload(sentiment_score="긍정 실적", importance="1차 촉매")

    assert payload == {"sentiment_score": "긍정 실적", "importance": "1차 촉매"}


def test_reason_payload_rejects_unknown_score_key() -> None:
    with pytest.raises(ValueError, match="unknown score"):
        _ = reason_payload(made_up_score="x")


def test_reason_payload_allows_empty() -> None:
    assert reason_payload() == {}


def test_reason_payload_model_validates_values_are_text() -> None:
    model = ReasonPayload.model_validate({"risk_score": "규제 리스크"})

    assert model.root["risk_score"] == "규제 리스크"
```

- [ ] **Step 2: 실패 확인** → ModuleNotFoundError
- [ ] **Step 3: 구현** — 신규 `src/quantinue/db/reason.py`:

```python
"""Per-score reason payloads persisted as JSONB."""

from __future__ import annotations

from typing import Final

from pydantic import RootModel

SCORE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "sentiment_score",
        "importance",
        "risk_score",
        "confidence",
        "relevance_score",
        "source_trust",
    }
)


class ReasonPayload(RootModel[dict[str, str]]):
    """Validated map from score column name to its stated rationale."""


def reason_payload(**scores: str) -> dict[str, str]:
    """Build a reason map, rejecting keys that are not score columns."""
    unknown = set(scores) - SCORE_KEYS
    if unknown:
        msg = f"unknown score keys: {sorted(unknown)}"
        raise ValueError(msg)
    return dict(scores)
```

`db/schema.sql` 4곳을 `reason JSONB NOT NULL DEFAULT '{}'`(tb_disclosure) / `reason JSONB`(나머지 3곳, 기존 NULL 허용 유지)로 변경. `domain_sources.py`의 하드코딩 4곳을 dict로:
- `reason={"summary": ...}` 형태는 SCORE_KEYS 위반이므로, 파이프라인 단계 표기는 **빈 dict `{}`**로 두고(구조만 확보), 점수별 사유는 M4에서 채운다. 즉 4곳 모두 `reason={}`.

- [ ] **Step 4: 통과 + 회귀** — 전체 green
- [ ] **Step 5: Commit** — `feat(m2): reason 4곳 JSONB 전환 + 점수별 사유 페이로드 계약`

---

### Task 3: tb_disclosure_signal 집계 2컬럼

**Files:** Modify `db/schema.sql` · Test: Task 9 마이그레이션 검증에 포함

- [ ] **Step 1: schema.sql 수정** — `tb_disclosure_signal`에 추가:
  `disclosure_count SMALLINT NOT NULL DEFAULT 0 CHECK (disclosure_count >= 0), top_evidence TEXT[] NOT NULL DEFAULT '{}',`
- [ ] **Step 2: 회귀** — `uv run pytest tests/unit tests/test_web.py -q` green
- [ ] **Step 3: Commit** — `feat(m2): tb_disclosure_signal 집계 2컬럼 — news_signal과 대칭`

---

### Task 4: side에 sell 허용

**Files:** Modify `db/schema.sql`

- [ ] **Step 1: schema.sql 수정** — `side TEXT NOT NULL CHECK (side IN ('buy','hold','sell'))`
- [ ] **Step 2: 회귀 + Commit** — `feat(m2): tb_strategist_signals side에 sell 허용 (M5 매도 전제)`

---

### Task 5: 07·08 계보 10컬럼 + critic source 리네임

**Files:** Modify `db/schema.sql` · `src/quantinue/db/domain_records.py` · `src/quantinue/db/domain.py` · Test `tests/unit/test_critic_verdict_source.py`(신규)

**Interfaces:** Produces: `CriticVerdictWrite.verdict_source`(기존 `.source` 대체) · 두 테이블에 계보 10컬럼(전부 NULL 허용)

- [ ] **Step 1: 실패 테스트**

```python
"""Critic cache-state source is renamed so lineage `source` is uniform."""

from decimal import Decimal

from quantinue.db.domain_records import CriticVerdictWrite


def test_critic_verdict_write_uses_verdict_source() -> None:
    write = CriticVerdictWrite(
        signal_id=1,
        ticker="NVDA",
        decision="pass",
        category="pipeline_gate",
        objection="accepted",
        confidence=Decimal("0.8"),
        decided_layer="gate",
    )

    assert write.verdict_source == "fresh"
    assert not hasattr(write, "source")  # lineage `source`와의 충돌 제거
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** —
  - `domain_records.py`: `source: str = "fresh"` → `verdict_source: str = "fresh"`
  - `domain.py:save_verdict`: `"source": value.source` → `"verdict_source": value.verdict_source`
  - `schema.sql` tb_critic_verdict: `source TEXT NOT NULL CHECK (source IN (...))` → `verdict_source TEXT NOT NULL CHECK (verdict_source IN ('fresh','cache','cooldown'))`
  - `schema.sql` tb_strategist_signals·tb_critic_verdict 양쪽에 계보 10컬럼 추가(전부 NULL 허용):
    `source TEXT, source_ref TEXT, captured_at TIMESTAMPTZ, evidence_id TEXT, parent_evidence_ids JSONB NOT NULL DEFAULT '[]', model_provider TEXT, model_name TEXT, prompt_version TEXT, policy_version TEXT, input_hash TEXT,`
- [ ] **Step 4: 통과 + 회귀** — 전체 green
- [ ] **Step 5: Commit** — `feat(m2): 07·08 계보 10컬럼 + critic source→verdict_source(동명 충돌 해소)`

---

### Task 6: 신규 3테이블

**Files:** Modify `db/schema.sql` · `tests/integration/schema_sql_expectations.py`

- [ ] **Step 1: schema.sql에 3테이블 추가**

```sql
CREATE TABLE IF NOT EXISTS tb_user (
  user_id BIGSERIAL PRIMARY KEY, login_id TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','user')), otp_secret TEXT,
  is_active BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tb_llm_usage (
  id BIGSERIAL PRIMARY KEY, called_at TIMESTAMPTZ NOT NULL, task TEXT NOT NULL,
  model TEXT NOT NULL, prompt_tokens INT NOT NULL CHECK (prompt_tokens >= 0),
  completion_tokens INT NOT NULL CHECK (completion_tokens >= 0),
  est_cost_usd NUMERIC NOT NULL CHECK (est_cost_usd >= 0), run_id TEXT
);

CREATE TABLE IF NOT EXISTS tb_benchmark_price (
  price_date DATE NOT NULL, ticker TEXT NOT NULL, close NUMERIC NOT NULL CHECK (close > 0),
  PRIMARY KEY (price_date, ticker)
);
```

- [ ] **Step 2: 계약 테스트 기대치 갱신** — `schema_sql_expectations.py`의 `TABLES`에 3개 추가, `PK`에 `tb_user:("user_id",)`·`tb_llm_usage:("id",)`·`tb_benchmark_price:("price_date","ticker")`, `UNIQUE`에 `tb_user:{("login_id",)}` 추가
- [ ] **Step 3: 회귀 + Commit** — `feat(m2): 신규 3테이블 tb_user·tb_llm_usage·tb_benchmark_price`

---

### Task 7: tb_account 확장

**Files:** Modify `db/schema.sql` · `tests/integration/schema_sql_expectations.py`

- [ ] **Step 1: schema.sql tb_account에 추가** — `user_id BIGINT REFERENCES tb_user(user_id), inv_type TEXT CHECK (inv_type IN ('aggressive','conservative')), status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','closed')),` + 테이블 뒤 `CREATE UNIQUE INDEX IF NOT EXISTS tb_account_user_id_key ON tb_account(user_id) WHERE user_id IS NOT NULL;`
  ※ tb_user가 tb_account보다 **앞에** 정의돼야 FK 성립 — schema.sql 내 순서 확인
- [ ] **Step 2: 계약 기대치에 FK 추가(있다면)** + 회귀
- [ ] **Step 3: Commit** — `feat(m2): tb_account 확장 — user_id·inv_type·status(1계좌 1유저 부분 유니크)`

---

### Task 8: config mvp2 블록 + typed 모델

**Files:** Modify `config/pipeline.yaml` · `src/quantinue/orchestration/policy.py` · Test `tests/unit/test_pipeline_policy.py`(추가)

**Interfaces:** Produces: `Mvp2Config`에 `profiles: dict[str, ProfileConfig]` · `gates: GatesConfig` · `screening: ScreeningConfig` · `exits: ExitsConfig` · `budget: BudgetConfig` 추가(기존 `schedule` 유지)

- [ ] **Step 1: 실패 테스트**

```python
def test_mvp2_profiles_and_gates_load_from_yaml() -> None:
    config = load_mvp2_config(Path("config/pipeline.yaml"))

    aggressive = config.profiles["aggressive"]
    conservative = config.profiles["conservative"]
    assert aggressive.buy_threshold == 0.65
    assert aggressive.max_positions == 10
    assert aggressive.daily_loss_limit == 0.04
    assert conservative.buy_threshold == 0.75
    assert conservative.min_cash_ratio == 0.30
    assert config.gates.source_trust_min == 0.55
    assert config.gates.macro_penalty_cap == 0.40
    assert config.gates.snapshot_tolerance == 0.02
    assert config.gates.overconfidence_approval == 0.80
    assert config.screening.universe_size == 2000
    assert config.screening.daily_picks == 50
    assert config.screening.llm_depth == 20
    assert config.exits.time_exit_bdays == 10
    assert config.budget.daily_llm_usd == 3.0  # 임시값 — M8 실측 후 확정
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — `config/pipeline.yaml`의 `mvp2:` 아래 추가:

```yaml
  profiles:
    aggressive:
      buy_threshold: 0.65
      risk_off_action: penalty
      late_entry_max: 0.15
      max_positions: 10
      max_weight: 0.20
      daily_loss_limit: 0.04
      min_cash_ratio: 0.10
    conservative:
      buy_threshold: 0.75
      risk_off_action: no_new_buys
      late_entry_max: 0.12
      max_positions: 5
      max_weight: 0.10
      daily_loss_limit: 0.02
      min_cash_ratio: 0.30
  gates:
    source_trust_min: 0.55
    hard_negative_max: 0.15
    macro_penalty_cap: 0.40
    snapshot_tolerance: 0.02
    critic_approval: 0.70
    overconfidence_conviction: 0.90
    overconfidence_approval: 0.80
  screening:
    universe_size: 2000
    min_price_usd: 5
    min_avg_dollar_vol: 20000000
    daily_picks: 50
    llm_depth: 20
  exits:
    time_exit_bdays: 10
  budget:
    daily_llm_usd: 3.0
```

`policy.py`에 대응 frozen BaseModel 5종 추가 후 `Mvp2Config`에 필드로 연결(각 필드는 기본값 제공 — 블록 부재 시에도 로딩).

- [ ] **Step 4: 통과 + 회귀** — 전체 green
- [ ] **Step 5: Commit** — `feat(m2): config mvp2 — profiles·gates·screening·exits·budget typed 모델`

---

### Task 9: 멱등 마이그레이션 + 실 DB 적용 검증

**Files:** Create `db/migrations/mvp2.sql` · Test `tests/integration/test_mvp2_migration.py`(신규)

**Interfaces:** Produces: 기존 1차 스키마 DB → M2 카탈로그로 올리는 멱등 스크립트

- [ ] **Step 1: 마이그레이션 작성** — Task 2~7의 모든 변경을 멱등 형태로. 요지:

```sql
-- reason TEXT → JSONB (기존 텍스트는 {"legacy": ...}로 보존)
DO $$ BEGIN
  IF (SELECT data_type FROM information_schema.columns
      WHERE table_name='tb_disclosure' AND column_name='reason') = 'text' THEN
    ALTER TABLE tb_disclosure ALTER COLUMN reason TYPE JSONB
      USING CASE WHEN reason IS NULL THEN '{}'::jsonb
                 ELSE jsonb_build_object('legacy', reason) END;
  END IF;
END $$;
-- (tb_disclosure_signal · tb_news · tb_news_signal 동일 패턴)

ALTER TABLE tb_disclosure_signal
  ADD COLUMN IF NOT EXISTS disclosure_count SMALLINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS top_evidence TEXT[] NOT NULL DEFAULT '{}';

ALTER TABLE tb_strategist_signals DROP CONSTRAINT IF EXISTS tb_strategist_signals_side_check;
ALTER TABLE tb_strategist_signals ADD CONSTRAINT tb_strategist_signals_side_check
  CHECK (side IN ('buy','hold','sell'));

-- critic source → verdict_source (멱등 리네임)
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='tb_critic_verdict' AND column_name='source')
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='tb_critic_verdict' AND column_name='verdict_source') THEN
    ALTER TABLE tb_critic_verdict RENAME COLUMN source TO verdict_source;
    ALTER TABLE tb_critic_verdict RENAME CONSTRAINT tb_critic_verdict_source_check
      TO tb_critic_verdict_verdict_source_check;
  END IF;
END $$;

-- 계보 10컬럼 ×2 테이블 · 신규 3테이블 · tb_account 3컬럼 + 부분 유니크 인덱스
-- (전부 IF NOT EXISTS)
```

- [ ] **Step 2: 실 DB 무손실 적용 검증** — W0 데이터가 있는 app-v2 DB(5445)에 적용 전후 행수 비교:

```bash
docker exec app-v2-db-1 psql -U quantinue -d quantinue -tAc \
  "SELECT 'signals='||count(*) FROM tb_strategist_signals UNION ALL SELECT 'disc='||count(*) FROM tb_disclosure UNION ALL SELECT 'news='||count(*) FROM tb_news"
docker exec -i app-v2-db-1 psql -U quantinue -d quantinue < db/migrations/mvp2.sql
# 같은 행수 + 두 번째 실행도 에러 없이 통과(멱등)
docker exec -i app-v2-db-1 psql -U quantinue -d quantinue < db/migrations/mvp2.sql
```

- [ ] **Step 3: 신규 설치 경로도 동일 카탈로그인지** — 임시 컨테이너에 `schema.sql`만 적용 → 두 DB의 컬럼 집합 비교(마이그레이션 경로 == 신규 설치 경로)
- [ ] **Step 4: 전체 회귀 + ruff** — `uv run pytest tests/unit tests/test_web.py -q` · `uv run ruff check src tests`
- [ ] **Step 5: Commit** — `feat(m2): 멱등 마이그레이션 mvp2.sql + 실 DB 무손실 적용 검증`

---

### Task 10: 문서 미러

- [ ] **Step 1** — playbook M2 표에 ✅ + ⏳해소(제약명·critic 충돌) 기록
- [ ] **Step 2** — 정본 HTML: #data 스키마 명세에 신규 3테이블·계보·reason JSONB 반영, changelog v4.7
- [ ] **Step 3: Commit** — `docs(m2): M2 완료 반영`

## 완료 기준 대조 (playbook M2)

| 기준 | 커버 |
|---|---|
| 마이그레이션 1차 DB 무손실 적용 | Task 9 Step 2 (행수 비교 + 2회 실행) |
| 전체 테스트 green(계약 수정 포함) | 각 태스크 Step 4 + Task 9 Step 4 |
| 3파일 먼저(schema·ontology·yaml), 구현은 이후 | Task 1~8이 전부 선언만 — 로직 0 |
