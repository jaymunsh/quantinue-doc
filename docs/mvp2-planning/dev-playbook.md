# MVP-2 실행 플레이북 — Wave 0 ~ M11 전체 상세

> 작성 2026-07-18 · 문성혁 + Claude. **이 문서만 열면 어느 세션이든 이어서 실행 가능한** 태스크 레벨 완결판.
> 입력: `phase0-asbuilt-audit.md`(코드 현실·file:line) · `phase1-decisions.md`(R1~R10) · `phase2-dev-plan.md`(마스터 계획).
> **⏳ 표시** = 착수 시점에 확인·전개해야 할 보완 항목(맨 아래 §보완 목록에 집계). 실제 TDD 스텝(테스트 코드·구현 코드)은 각 태스크 착수 직전 그 자리에서 전개한다 — 후반 마일스톤 코드는 선행 마일스톤이 바꾼 코드 위에 써야 하므로 여기 미리 쓰지 않는다(이중 지출 방지).

## 0. 공통 규칙 (모든 세션·모든 태스크)

- **작업 위치**: `app-v2/` 전용. `app/`(1차)은 다른 작업자 WIP — 절대 수정 금지.
- **브랜치**: dev 통합 브랜치 **`sunghyuk`** 생성 후 거기서 M1~M8 순차 진행, Wave 단위로 `main` merge. M9~M10은 필요 시 worktree 병렬. 완성 후 담당자 핸드오프 브랜치(`eunmi`·`changwook`·`miyeon`·`jihyun`) 컷.
- **커밋**: 태스크 단위 1커밋, 메시지 `feat|fix|test(mN): 요약`. 문서(docs/)와 코드(app-v2/) 커밋 분리.
- **TDD**: 실패 테스트 → 최소 구현 → green → 커밋. 기존 **491개** 유닛 테스트는 항상 green 유지(계약 변경 시 테스트도 같은 커밋에서 수정). ※ baseline 실측 2026-07-18: `uv run pytest tests/unit -q` → 491 passed.
- **config 소유**: 문턱·주기·한도 하드코딩 금지 — `config/pipeline.yaml` + Settings. 리터럴 발견 시 즉시 config로 승격.
- **문서 미러**: 핵심 로직·프롬프트가 코드로 확정되면 정본 HTML `#logic`에 반영(M2 배지 → as-built 승격) + changelog 한 줄.
- **점수 규칙**: 0~1 · DB `NUMERIC + CHECK(0~1)` · 표기 소수 3자리. ENUM 정본 = `ontology.py`.
- **검증 실행 기본 명령**: `cd app-v2 && uv sync && uv run pytest tests/unit -q` (통합: `uv run pytest tests/integration -q` — Postgres 필요).

---

## Wave 0 — 매수 개시 런북 (월요일 07-20 개장 전까지)

목표: 기존 1차 로직 그대로, 실데이터+로컬 LLM+실 페이퍼로 첫 매수 → **T+5 시계 가동** (20일 매수 → T+5 = 27일).

| # | 태스크 | 상세 |
|---|---|---|
| W0-1 | app-v2 baseline 커밋 | ✅ **완료** (2026-07-18, 커밋 `dea5944`, 브랜치 `sunghyuk`). `.omo/`(1차 오케스트레이션 흔적 21MB)는 baseline에서 제외 + gitignore 추가 — 순수 앱소스 199파일만 커밋. `.env`(Alpaca 키)는 gitignore로 제외 확인. push는 원격 붙일 때 |
| W0-2 | 의존성 설치 | ✅ **완료**. `cd app-v2 && uv sync` 성공. 검증: `uv run pytest tests/unit -q` → **491 passed**(playbook 기존 "347개" 표기는 부정확 → 실측 491). 항상 green 유지 기준선 = 491 |
| W0-3 | Postgres 기동 | ✅ **완료**. ⚠️ **포트 5444→5445 변경**: 5444는 1차 `app-db-1`(다른 작업자 WIP, tb_order=1·pipeline_runs=91)이 점유 → app-v2 전용 DB를 **5445**로 격리(compose.yaml·.env 동시 수정). ⏳해소: compose가 schema.sql **자동 적용함**(`./db/schema.sql:/docker-entrypoint-initdb.d/001-schema.sql` 마운트, 빈 볼륨 첫 기동 시). `docker compose up -d db`(web 제외) → `app-v2-db-1` healthy, 19테이블 자동생성·전부 empty. 1차 DB 무손상 확인 |
| W0-4 | env 점검 (드라이런 구성) | ✅ **완료**. `DATA_MODE=public` ✓ · `LLM_MODE=local`(8888, Qwen3.6-35B-A3B-OptiQ-4bit) ✓ · 브로커 mock/false ✓ · DATABASE_URL 5445 ✓ |
| W0-5 | MLX 서버 확인 | ✅ **완료**. `/v1/models` 응답 정상 — Qwen3.6-35B-A3B-OptiQ-4bit 서빙 중(max_model_len 262144) |
| W0-6 | 드라이런 (mock 브로커) | ✅ **완료** (2026-07-18). 포트 8000은 다른 프로세스 점유 → **8020 사용**. 1·2차 시도에서 **버그 2건 발견·수정**(TDD, 유닛 491→493 green): ① 로컬 LLM thinking 미억제 → `enable_thinking=false` extra_body 전달(커밋 `3002fcf`) ② 주말·프리마켓에서 stage-08 trade_date가 픽과 갈라져 FK 위반 → 세션 날짜 사용(커밋 `eae11dd`). 3차 시도: **HTTP 201 · 01→11 완주 · MLX 실호출 12건 · FK 자식 3테이블 trade_date=07-17 일치 · pipeline_runs=completed**. 판단: NVDA hold(확신도 0.231, 뉴스 0.10·공시 0.00 — 크리틱 차단·주문 0주, 주말 실데이터 기반 정상 판단). 깔때기 동작: universe 50 → picks 10(NVDA 포함) |
| W0-7 | 실 페이퍼 무장 | ⚠️ 사용자 확인 후: `.env` `QUANTINUE_BROKER_MODE=alpaca` + `QUANTINUE_TRADING_ENABLED=true`. `DAILY_NEW_ORDER_CAP=5` 유지 |
| W0-8 | 스모크 (장전) | 월요일 개장(뉴욕 09:30 = KST 22:30) 직후 수동 실행 → Alpaca 페이퍼 대시보드에서 브래킷 주문 접수·체결 확인 → tb_order/tb_fill 기록 확인. ⚠️ **반드시 정규장 시간에만**: 주문 페이로드(`broker/alpaca.py:244-255`)가 `type=market·order_class=bracket·extended_hours 미설정` — Alpaca는 시장가·브래킷 주문을 장외(프리마켓/애프터아워)에서 거부. 장외 매수는 지정가+extended_hours로 바꾸는 코드변경 필요(Wave 0 범위 밖, 필요 시 별도 항목화) |
| W0-9 | 매일 반복 | 자동 스케줄(M1) 전까지는 개장 시간대 수동 트리거 1~2회/일. PC 절전 방지: `caffeinate -s` |

**완료 기준**: Alpaca 페이퍼에 실제 포지션 ≥ 1 · tb_order status=filled · T+5 카운트 시작.
⏳ W0 보완: 스크리닝 자동 모드(`POST /runs` 티커 없이)가 public 50종목에서 어떤 픽을 내는지 첫 실행 관찰 후 기록.

---

## M1. 슬롯 멱등 + 스케줄러 + 캘린더 (R2·R9) — Wave 1 ✅ **완료 (2026-07-18)**

**전제**: 없음(첫 마일스톤). **파급**: 이후 모든 실행이 이 슬롯·스케줄 위에서 돈다.

> **구현 완료 요약** (플랜: `plans/2026-07-18-m1-scheduler-plan.md` · 커밋 `31009aa`~`d5350f7` 8개):
> - 1-1 `orchestration/slots.py:slot_of` ✅ · 1-2 main.py 3개 트리거 경로 슬롯화 ✅ · 1-3 `core/market_calendar.py`(exchange-calendars 4.13 XNYS, **current_session pre/regular/after/closed 포함**, DST 3/9·11/2 검증) ✅ · 1-4 `mvp2.schedule` config(**기본 enabled=false** — 운영 전환 시 yaml 한 줄로 켬) ✅ · 1-5 `orchestration/scheduler.py:CycleScheduler`(60s 틱, lifespan task group 스폰) ✅ · 1-6 **신규 락 불필요 — 기존 `deterministic_run_key`+`store.claim`이 슬롯 양자화만으로 dedup**(E2E-2 테스트로 검증) ✅ · 1-7 catch-up=첫 틱 자연 판정 + `POST /api/scheduler/catchup`·`GET /api/scheduler/status` ✅ · 1-8 테스트 21개 신규(슬롯 6·캘린더 11·스케줄러 7 등, 전체 542 green·ruff clean) ✅
> - **스코프 노트**: 역할별 창(role_01 weekly 등)은 M1에 없음 — 파이프라인이 01→11 원자 실행이라 무의미, M3 역할 분리와 함께 확장. `RunStore.latest_cycle_ts()`가 last_runs 데이터원(전 역할 공통).
> - ⏳해소: DueRoleScheduler 시그니처 확인 완료(순수 seam 재사용, `plan_periods()` 공개 1줄 추가) · last_runs 쿼리 = `latest_useful_cycle_ts`(pending/running/completed 중 max cycle_ts).

| # | 태스크 | 파일 | 상세 |
|---|---|---|---|
| 1-1 | 슬롯 함수 | 신규 `src/quantinue/orchestration/slots.py` | `def slot_of(now: datetime, period_minutes: int) -> datetime` — UTC 기준 period 경계 내림. 성질: 같은 period 내 임의 시각 → 같은 슬롯 / 경계값 자기 자신 / tz-aware 강제 |
| 1-2 | cycle_ts 교체 | `src/quantinue/main.py:52` | `now().replace(second=0,...)` → 역할 주기 기반 `slot_of()`. 수동 `POST /runs`·`/api/runs`도 동일 경로 |
| 1-3 | NYSE 캘린더 | 신규 `src/quantinue/core/market_calendar.py` | 라이브러리 `exchange-calendars`(XNYS). 인터페이스: `is_trading_day(date)` · `session_open/close(date)` · `add_business_days(date, n)`(T+5용) · `is_market_open(dt)` · **`current_session(dt) -> pre\|regular\|after\|closed`**(세션 정책 결정 2026-07-18 파급 — 장외 청산/매수 게이트용). 서머타임은 라이브러리가 처리 |
| 1-4 | 실행 창 config | `config/pipeline.yaml` | `mvp2.windows:` — role_01: weekly_premarket / role_02·03: daily_premarket / role_04·05: extended / role_06: premarket+open / role_07~10: market_open_only / role_11: after_close. 값은 America/New_York 상대 정의 |
| 1-5 | 스케줄러 루프 | `main.py` lifespan | anyio 태스크: 60초마다 ① `DueRoleScheduler.due_roles(now, last_runs)`(기존 seam, policy.py:200) ② 창 검사(1-3·1-4) ③ due면 슬롯 cycle_ts로 파이프라인 트리거. last_runs는 pipeline_runs 조회 |
| 1-6 | claim 락 | `orchestration/` 기존 idempotent claim 확장 | `(component, cycle_ts)` 단위 claim — 선점 실행만 진행, 후발은 no-op 로그. 수동+자동 동시 발화 대비 |
| 1-7 | catch-up | 스케줄러 기동 시 | 오늘 세션 내 미실행 due 슬롯을 현재 데이터로 1회 실행(과거 소급 없음). 관리자 API `POST /api/scheduler/catchup` 추가 |
| 1-8 | 테스트 | `tests/unit/test_slots.py` 등 | 슬롯 경계·멱등(E2E-2)·휴장일 skip·DST 전환일(3/8·11/1 케이스)·claim 경쟁·catch-up |

**완료 기준**: 같은 슬롯 2회 실행 → signal 1행 · 동시 발화 중복 0 · 휴장일 07~10 미실행 · 재시작 후 catch-up 동작 · **앱 켜두면 사람 없이 하루 사이클 자동 완주**.
⏳ 보완: `DueRoleScheduler` 현재 시그니처 확인 후 창 검사와의 결합 방식 확정 · pipeline_runs에서 last_runs 조회 쿼리 작성.
※ W0에서 선반영: trade_date 이원화(시계 vs 데이터) FK 버그의 **최소 수정**이 `db/postgres_lifecycle.py:_session_trade_date()`로 들어감(커밋 `eae11dd`) — M1-3 캘린더 도입 시 이걸 정식 세션날짜 일원화(전 역할 관통)로 승격할 것.

## M2. 스키마·계약 일괄 확장 — Wave 1 ✅ **완료 (2026-07-18)**

**전제**: M1과 독립(병행 가능). **원칙**: 3파일(schema.sql·ontology/schemas·pipeline.yaml) 먼저, 구현은 이후 마일스톤.

> **구현 완료 요약** (플랜: `plans/2026-07-18-m2-schema-plan.md` · 커밋 `ee3bd56`~`538c0e4` 7개):
> - 2-1 ontology ✅(Side+SELL·AccountStatus·UserRole·LlmTask — 1차의 "sell 거부" 계약 테스트를 새 계약으로 교체) · 2-2 reason JSONB ×4 ✅(신규 `db/reason.py` — 점수 컬럼명→사유 맵, 미지 키 거부. **실제 점수별 사유 채우기는 M4**) · 2-3 공시 signal +2 ✅ · 2-4 side sell ✅ · 2-5 계보 10 ×2 ✅ · 2-6 신규 3테이블 ✅ · 2-7 tb_account 확장 ✅ · 2-8 config mvp2 ✅(profiles/gates/screening/exits/budget frozen 모델) · 2-9 마이그레이션 ✅
> - **⏳2-4 해소**: 제약명 = `tb_strategist_signals_side_check`(실 DB 조회 확인).
> - **계획에 없던 충돌 발견·해소**: `tb_critic_verdict.source`(fresh/cache/cooldown, 캐시 상태)가 R10 계보의 `source`(출처)와 **동명 충돌** → 기존 것을 **`verdict_source`로 리네임**해 6개 계보 테이블 컬럼명을 통일. `CriticVerdictWrite.source`→`verdict_source`(코드·테스트 동반 수정).
> - **무손실 검증**(완료 기준): W0 실데이터 DB(5445)에 적용 → 11테이블 **행수 완전 동일**, reason은 `{"legacy": "..."}`로 보존, **2회 실행 멱등**. 추가로 **마이그레이션 경로 == 신규 설치 경로** 확증(컬럼 344·제약 134 완전 일치) + 마이그레이션 DB에서 스키마 계약 테스트 green + 앱 기동·조회 정상.
> - 테스트: 유닛/웹 **553 green** · 통합 **30 green**(깨끗한 DB 1회 실행) · ruff clean. ※ 통합 테스트는 일회용 DB 전제라 같은 DB 재실행 시 중복키로 실패함(설계상 정상).

| # | 태스크 | 상세 |
|---|---|---|
| 2-1 | ontology.py 확장 | `Side +SELL` · 신규 `AccountStatus(active/paused/closed)` · `UserRole(admin/user)` · `LlmTask(disclosure/news/strategy/critic/review)` |
| 2-2 | reason JSONB ×4 | schema.sql 4곳 TEXT→JSONB + Pydantic `reason: dict[str,str]`(키=점수 컬럼명, Literal 검증). 마이그레이션: `ALTER TABLE tb_disclosure ALTER COLUMN reason TYPE JSONB USING jsonb_build_object('legacy', reason);` (기존 텍스트는 {"legacy": ...}로 보존) — 4테이블 동일 |
| 2-3 | 공시 signal +2 | `ALTER TABLE tb_disclosure_signal ADD COLUMN disclosure_count SMALLINT NOT NULL DEFAULT 0, ADD COLUMN top_evidence TEXT[] NOT NULL DEFAULT '{}';` |
| 2-4 | side +sell | tb_strategist_signals CHECK 재생성: `ALTER ... DROP CONSTRAINT <name>; ADD CHECK (side IN ('buy','hold','sell'));` ⏳ 제약명은 `\d tb_strategist_signals`로 확인 |
| 2-5 | 07·08 계보 10 (R10) | tb_strategist_signals·tb_critic_verdict에 `ADD COLUMN source TEXT, source_ref TEXT, captured_at TIMESTAMPTZ, evidence_id TEXT, parent_evidence_ids JSONB DEFAULT '[]', model_provider TEXT, model_name TEXT, prompt_version TEXT, policy_version TEXT, input_hash TEXT;` — 기존 행 고려해 전부 NULL 허용, 신규 기록부터 채움(코드에서 필수) |
| 2-6 | 신규 3테이블 | CREATE TABLE `tb_user`(user_id BIGSERIAL PK, login_id TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN('admin','user')), otp_secret TEXT, is_active BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now()) · `tb_llm_usage`(id BIGSERIAL PK, called_at TIMESTAMPTZ NOT NULL, task TEXT NOT NULL, model TEXT NOT NULL, prompt_tokens INT NOT NULL CHECK(prompt_tokens>=0), completion_tokens INT NOT NULL CHECK(completion_tokens>=0), est_cost_usd NUMERIC NOT NULL CHECK(est_cost_usd>=0), run_id TEXT) · `tb_benchmark_price`(price_date DATE, ticker TEXT, close NUMERIC NOT NULL CHECK(close>0), PRIMARY KEY(price_date,ticker)) |
| 2-7 | tb_account 확장 | `ADD COLUMN user_id BIGINT REFERENCES tb_user(user_id), inv_type TEXT CHECK(inv_type IN('aggressive','conservative')), status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','paused','closed'));` + `CREATE UNIQUE INDEX ON tb_account(user_id) WHERE user_id IS NOT NULL;` |
| 2-8 | config mvp2 블록 | pipeline.yaml에 profiles(공격 0.65/penalty/0.15/10/0.20/0.04/0.10 · 안전 0.75/no_new_buys/0.12/5/0.10/0.02/0.30) · gates(0.55/0.15/0.40/0.02/0.70/0.90/0.80) · screening(2000/$5/$20M/50/20) · exits(time_exit_bdays 10) · models(default·strategy 분리) · budget(daily_llm_usd ⏳M8 실측 후) + policy.py Pydantic 모델 추가 |
| 2-9 | 마이그레이션 파일 | 신규 `db/migrations/mvp2.sql` — 위 전부를 멱등(IF NOT EXISTS/DO $$ 가드)으로. 1차 DB에 적용 검증(W0에서 만든 데이터 무손실) |

**완료 기준**: 마이그레이션 1차 DB 무손실 적용 · 전체 테스트 green(계약 수정 포함) · 한 PR로 merge.

## M3. 깔때기 복원 (R8) — Wave 1 ✅ **완료 (2026-07-18)**

> **구현 완료 요약** (커밋 `c7eb8a2`·`877f5e2`):
> - 3-1 ✅ 유니버스 50→**2000**(config `universe_size`) + **응답순 앞 50 버그를 시총 내림차순 정렬로 수정**
> - 3-2 **⚠️ 계획 수정 — Alpaca 멀티심볼 벌크는 적용 불가**: 실제 public 소스는 NASDAQ이고 일봉이 **종목당 1콜**(`/quote/{ticker}/historical`)이다. Alpaca는 주문 전용이라 시장데이터 어댑터가 없음. **실측: 일봉 1콜 2.4~3.4초** → 2000종목 전량은 장전 창 초과. 대안으로 **2단 구조** 채택: 유니버스 2000은 전부 저장, 일봉은 **시총 상위 `technical_candidates`(500)**만 조회(시총 500위 = $28B, 투자 가능 대형·중형주 커버).
> - 3-3 ✅ 하드 필터 신설(주가 ≥ `min_price_usd` 5 · **20일 평균 거래대금** ≥ `min_avg_dollar_vol` $20M, 일봉에서 실계산) + `TECHNICAL_UNIVERSE_LIMIT`(20) 제거 · 동시성 5→`technical_concurrency`(10)
> - 3-4 ✅ 오늘의 픽 10→**50**(config `daily_picks`) · 3-5 ✅ LLM 심층은 요청 종목 한정(보유 합류 훅은 M5) · 3-6 ✅ 필터 경계 테스트($4.99 vs $5.00, 거래대금 정확 경계, 윈도우 밖 구간 무시)
> - **발견·수정**: 깔때기를 넓히자 role_02가 **공용 역할 타임아웃 120s**를 넘겨 재시도 3회 후 실패(365s). 전역 상향은 LLM 역할 보호를 함께 약화시키므로 **`role_timeout_overrides`(config)로 02만 900s**로 연장.
> - **실행 검증**(완료 기준 전부 충족): tb_universe **2000행** · 이번 실행 픽 **50개** · **152초 완주**(창 30분 대비 여유) · **HTTP 429 0건**(500요청 전부 200) · LLM 12콜(요청 종목만). 500 후보 중 **465개가 필터 통과**(35개 탈락).
> - 테스트: 유닛/웹 **567 green**(1차의 50/20/10 고정 계약 6건을 config 주도로 갱신 + 신규 필터·유니버스 테스트) · ruff clean.
> - ⏳ **남은 개선**: 일봉 벌크 소스(Alpaca 시장데이터 등)를 붙이면 `technical_candidates`를 2000까지 올릴 수 있다. 현재는 소스 제약이 상한을 정한다.

| # | 태스크 | 파일 | 상세 |
|---|---|---|---|
| 3-1 | 유니버스 2,000 | `roles/role_01_universe_screener/service.py:18` | `PUBLIC_UNIVERSE_LIMIT` 50→config `screening.universe_size`(2000). 시총 정렬 명시(현재 응답순 앞 50 — 버그) · 주1회 창은 M1 windows |
| 3-2 | 일봉 벌크 | `market_data/` 확장 | Alpaca 멀티심볼 bars: 심볼 배치(~200개/콜) 분할, 200req/min 레이트리밋 백오프. 인터페이스 `daily_bars(symbols: list, lookback_days: int) -> dict[str, list[Bar]]` ⏳ 기존 http_source 구조 확인 후 결합 |
| 3-3 | 하드 필터 | `roles/role_02.../service.py` 전처리 | 주가 ≥ config `min_price_usd`(5) · 20일 평균 거래대금 ≥ `min_avg_dollar_vol`($20M). `TECHNICAL_UNIVERSE_LIMIT=20` 제거(필터 통과 전종목 계산) · `TECHNICAL_CONCURRENCY` 상향 검토 |
| 3-4 | 오늘의 50 | `roles/role_03.../service.py:19` | `DAILY_PICK_LIMIT` 20→config `daily_picks`(50). 버킷 스코어링 유지 |
| 3-5 | LLM 캡 | `orchestration/pipeline.py` `run_screening` | 심층(05~08) 대상 = 픽 점수 상위 `llm_depth`(20) ∪ 보유 종목(M5에서 보유 합류 — 여기선 훅만) |
| 3-6 | 테스트 | | 필터 경계($4.99 vs $5.00)·2000 배치 페이징 mock·50픽 랭크·llm_depth 컷 |

**완료 기준**: public 1회 실행 → tb_universe 2,000행·tb_daily_pick ≤50행 · 장전 창 내(≤30분) 완료 · 레이트리밋 백오프 0 · LLM 호출 종목 ≤ 20+보유.

## M4. 판단 방어선 (R3·R5) — Wave 2

| # | 태스크 | 파일 | 상세 |
|---|---|---|---|
| 4-1 | snapshot 실전달 | `roles/role_08_critic/service.py:41-54` | 합성(`day_high=price*1.01` 등) **삭제** — 07이 박제한 4필드(current/day_high/day_low/close_prev)를 CriticInput으로 실전달. 검증: 07 current_price vs 08 재조회가 `gates.snapshot_tolerance`(2%) 초과 → hold(`stale_snapshot`) |
| 4-2 | 07 게이트 3종 | `roles/role_07_strategist/contracts.py:131` | `can_buy` 재작성: ① news signal의 `source_trust < gates.source_trust_min`(0.55) → 뉴스 점수 투표 제외 ② conviction −= 매크로 감점표(risk_score 구간→감점, cap `macro_penalty_cap` 0.40) ⏳ 감점표 구간 수치는 1차 동결본 s07 POLICY에서 이관 ③ sentiment ≤ `hard_negative_max`(0.15) → 매수 차단. 문턱 = `profiles[inv_type].buy_threshold` |
| 4-3 | 과신 에스컬레이션 | `roles/role_08_critic/service.py:20,83` | `conviction ≥ 0.90` → 승인 문턱 0.70→`overconfidence_approval`(0.80) |
| 4-4 | 출처등급 | `roles/role_06.../selection.py` + 신규 `config/news_trust_policy.yaml` | yaml: 도메인→grade 매핑(allow 목록·gray 목록·기본 block) + 화이트리스트. 파이프라인: block → LLM 도달 전 drop(`is_dropped=true, drop_reason` 기록·행은 보존). `db/domain_sources.py:61-95` 하드코딩(`grade="allow"`·`source_trust=1`·`permission` 상수) 제거 — 실값 기록 |
| 4-5 | 대표기사 하이브리드 | `selection.py:141-147` | 1단계 관련성 필터(기존 relevance ≥ `MINIMUM_RELEVANCE_SCORE` — 통과/탈락으로만) → 2단계 `importance × w` 최상위(w = confidence × (is_confirmed?1.0:0.5) × grade가중) · 동률 published_at 최신→id. `peak_importance` 실계산(신뢰 검증분 max) + 저장 배선(`domain_sources.py` insert에 누락 중) |
| 4-6 | Form 4 정책 | 신규 `roles/role_05.../form4.py` | LLM 우회 분기: form_type='4' → 템플릿 조립(reason 객체 포함). 정책: 코드 P(공개시장 매수)+임원·$문턱 → importance 정상 / 매도 기본 저평가 / 클러스터(2인+) 가산·클러스터 매도 리스크. ⏳ SEC Form 4 파싱 필드(transactionCode·officerTitle) 소스 확인 |
| 4-7 | 잔여 3건 | role_07·09·10 | consensus 실계산(4신호 동의 수, 저장만 — 게이트 아님·`domain.py:129` 하드코딩 제거) · late_entry(ret_5d ≥ profiles별 0.15/0.12 → 매수 금지, role_09) · halted 체크(role_10 주문 직전 Alpaca asset tradable 조회 → skip+사유) |
| 4-8 | 프리마켓 갭 가드 | role_09 또는 role_10 진입 직전 | (세션 정책 2026-07-18 파급) 분석 기준가(직전 종가) 대비 현재가 갭 ≥ config `gates.premarket_gap_max` 초과 시 신규 매수 skip(사유 기록) — 금요일 종가 기준 픽이 월요일 갭업이면 진입가·손절·익절이 무의미해지는 문제. 문턱값은 M4 착수 시 실측 보고 확정 |

**완료 기준**: 게이트 경계값 단위 테스트(±0.001) · block 매체 LLM 호출 0 · 08 합성 코드 부재(grep) · 문턱 전부 yaml로만 변경 가능(코드 리터럴 grep 0).

## M5. 매도·보유 재평가 (R7-1) — Wave 2 · **최대 설계 작업**

⏳ **착수 시 첫 태스크 = 매도 주문 표현 설계 확정**: tb_order는 브래킷 매수 전용(CHECK order_type IN ('bracket')) — 매도(청산)는 ① order_type 'close' 추가 ② 브래킷 leg 취소 후 시장가 매도 ③ tb_fill side='sell' 활용. 스키마 영향 있으면 M2 마이그레이션에 추가.

| # | 태스크 | 파일 | 상세 |
|---|---|---|---|
| 5-1 | 분석 대상 ∪ 보유 | `orchestration/pipeline.py` | 사이클 시작 시 전 계좌 보유 조회 → 후보 목록과 합집합. 보유는 llm_depth 캡과 무관하게 항상 포함 |
| 5-2 | 07 sell 판단 | role_07 service·contracts·`prompts/role_07*.md` | 보유 종목 입력에 보유 맥락(진입가·보유일·미실현손익) 추가 → side buy/hold/sell. 프롬프트 개정(성향 페르소나 2종 포함 — 완료 시 문서 #logic 미러) |
| 5-3 | Critic 매도 검증 | role_08 | sell 판단 검증 규칙(근거 없는 패닉 매도 반박). 하드 이벤트(delisting_halt 등) 코드 직행은 검증 예외 |
| 5-4 | 청산 3층 | 신규 `roles/exits.py` + role_09/10 | ① 브래킷(기존) ② 시간: 보유 ≥ `exits.time_exit_bdays`(10, 영업일—M1 캘린더) & 논지 미실현 → 정리 ③ 논지 붕괴: 하드 event_type → 즉시, 점수 악재 → 07 sell 경유 |
| 5-5 | 브로커 청산 | `broker/alpaca.py` | 브래킷 leg(stop·take) 취소 → 시장가 매도 제출 → tb_fill(side='sell'). 멱등: close용 client_order_id 파생 규칙 추가 |
| 5-6 | 장외 청산 개방 | broker + exits + M1 `current_session` | (세션 정책 2026-07-18 파급) 프리·애프터 세션에서 청산 허용: 지정가+`extended_hours=true` 제출(장외는 시장가 불가). 하드 악재(논지 붕괴)는 애프터아워 즉시 청산. ⏳ 장외 호가 확보 확인 선행 |
| 5-7 | 보호주문 상태기계 → 장외 매수 개방 | 신규 | (세션 정책 파급, M5 완료 후 착수 가능) 미보호 포지션 레지스터 → 개장 시 보호주문 부착(멱등·재시도) → 실패 시 알림+즉시청산 폴백 → 앱 재시작 복구. **이 상태기계 완성이 장외 매수 개방의 하드 전제**(장외 브래킷 불가 → 손절 공백 방지) |

**완료 기준**: E2E-4(보유 종목 하드 악재 fixture → 자동 청산+사유) · 스크리너 탈락 보유 종목이 05~07 분석에 포함(로그).

## M6. 계좌 구조·서킷브레이커 (R1-5·R7-2) — Wave 2

| # | 태스크 | 파일 | 상세 |
|---|---|---|---|
| 6-1 | 계좌 구독 루프 | `orchestration/pipeline.py`·role_09 | 09~10을 `for account in active_accounts:` 루프로 — 계좌의 inv_type → profiles 파라미터 적용(문턱·사이징·한도). 리서치(01~08)는 루프 밖 1회 |
| 6-2 | 서킷 4종 | role_09 확장(기존 노출 한도 예약 구조 위) | max_positions(10/5) · max_weight(20%/10%) · daily_loss_limit(−4%/−2%: 당일 시작 equity 대비 실현+평가 손실 → 도달 시 당일 신규 매수 중단 플래그, 익영업일 자동 해제) · min_cash(10%/30%). 발동 기록(사유)+알림 훅(M8) ⏳ 당일 시작 equity 기준 저장 위치 설계(간단: 일별 equity 스냅샷) |
| 6-3 | 시뮬 멀티계좌 | `db/` simulated portfolio 계열 | 단일 계좌 가정 제거 — 계좌별 현금·보유·손익 |
| 6-4 | 계좌 프로비저닝 | 신규 스크립트 `scripts/provision_accounts.py` | 데모 5(aggressive $1M·$50K / conservative $150K·$100K·$50K) + 테스트 2($100K×2, is_test 라벨 = broker_account_id 접두 `TEST-`) + tb_user 연결(관리자 생성) |

**완료 기준**: E2E-5(동일 신호 → 공격 매수/안전 보류) · E2E-3(일손실 초과 → skip·익영업일 해제) · LLM 호출 수가 계좌 수와 무관(로그 검증).

## M7. 학습 루프 (R6·R7-6) — Wave 3

| # | 태스크 | 파일 | 상세 |
|---|---|---|---|
| 7-1 | T+5 자동 채점 | `roles/role_11.../processor.py` + M1 스케줄 | 마감 후 창에서 due 리뷰(체결 후 5영업일 — M1 `add_business_days`) 일괄 처리. 수동 `/api/reviews/{id}/process` 존치(재실행용) |
| 7-2 | lesson LLM 배선 | role_11 service + `llm/prompts.py` | 미호출 `AnalysisTask.REVIEW` 연결 — 입력: 판단 요약+T+5 결과, 출력: lesson 1~2문장(구조 강제). 완료 시 프롬프트 문서 미러 |
| 7-3 | 07 메모리 주입 | role_07 + `db/postgres_read.py` | 같은 종목 최근 5 lesson: `SELECT r.lesson, s.side, r.ret_5d FROM tb_review r JOIN tb_strategist_signals s USING(signal_id) WHERE s.ticker=$1 ORDER BY r.created_at DESC LIMIT 5` → 프롬프트 주입(총량 제한 ~500자) |
| 7-4 | SPY 벤치마크 | market_data + role_11 | tb_benchmark_price 일수집(마감 후) · 리뷰에 SPY 대비 상대수익 산출 · 계좌 성과 화면용 시계열 |

**완료 기준**: 사람 개입 없이 T+5 채점 실행(휴장 보정 포함) · lesson이 다음 07 프롬프트에 등장(fixture) · 상대 성과 조회 가능.

## M8. 운영 가드 (R6-3·R7-3·5) — Wave 3

| # | 태스크 | 파일 | 상세 |
|---|---|---|---|
| 8-1 | 텔레그램 알림 | 신규 `core/notify.py` | Bot API 직접 POST(의존성 0): `send(text)` — env `QUANTINUE_TELEGRAM_BOT_TOKEN`·`_CHAT_ID`(.env.example에 자리 추가). 이벤트: 스케줄 실패·소스 무응답·서킷 발동·예산 도달·(M11)하트비트 ⏳ 봇 토큰은 사용자가 BotFather에서 생성 |
| 8-2 | LLM 사용 기록 | `llm/provider.py` | `PydanticAiAnalyzer.analyze` 래핑 — pydantic_ai usage에서 토큰 추출 → tb_llm_usage insert(task·model·tokens·est_cost: 단가표 config) ⏳ usage 추출 API 확인 |
| 8-3 | 일일 예산 가드 | provider 진입부 | 오늘 est_cost 합 ≥ `budget.daily_llm_usd` → 호출 스킵 + 해당 사이클 신규 매수 보류 + 알림. 상한 기본값: 첫 주 실측 후 확정(그 전 임시 $3) |
| 8-4 | 계좌 대사 | 신규 `broker/reconcile.py` | 마감 후: DB(tb_account·보유) vs Alpaca `GET /v2/account`·`/v2/positions` 비교 → 불일치 시 브로커 정본 보정 + 알림. 시뮬 계좌는 내부 원장 자기검산 |
| 8-5 | 소스 헬스 | 스케줄러 훅 | RSS·공시 소스 N시간(기본 2h) 무응답 감지 → 알림 |
| 8-6 | 비용표 확정 | 문서 | 첫 주 tb_llm_usage 실측 → `#llm` 비용표·budget 기본값 갱신 |

## M9. 관리자 ERP — Wave 4

⏳ **착수 시 첫 태스크 = 와이어프레임** (기능 목록은 정본 #screens 확정): 페이지 구성·내비 스케치 후 구현.

| # | 태스크 | 상세 |
|---|---|---|
| 9-1 | 인증 | tb_user 로그인 + TOTP(`pyotp`) + 세션 쿠키. role=admin 구역 가드. 관리자 계정 시드 스크립트 |
| 9-2 | 계좌 CRUD | 개설(유저 생성 포함)·성향 지정·paused/closed 전환. 유저 계정 생성 = 관리자만 |
| 9-3 | 계좌 총람 | 계좌별 수익률(SPY 대비)·보유·주문·서킷 상태 테이블 |
| 9-4 | 운영 콘솔 | 스케줄 상태(다음 실행·최근 실패)·기존 라이브 패널·방어선 발동 내역 + **수동 컨트롤(R9)**: 사이클/역할 실행·catch-up·실패 재실행·리뷰 재채점(슬롯 표시·no-op 경고) |
| 9-5 | 비용 대시보드 | tb_llm_usage 집계(오늘/월·태스크별)·잔여 예산 |
| 9-6 | 유지보수·리포트 | 로그 조회·데이터 헬스·config 조회(읽기) · 유저용 데일리 브리핑 미리보기·발행 |

## M10. 유저 포털 — Wave 4

전 페이지 read-only · 자기 계좌만(소유권 검증) · 쓰기 엔드포인트 0(라우트 감사로 검증).

| # | 태스크 | 상세 |
|---|---|---|
| 10-1 | 내 계좌 홈 | 총자산·수익률 곡선·SPY 대비·보유 카드 |
| 10-2 | 거래 타임라인 | 체결 + reason JSONB → 사람 언어 사유 렌더 |
| 10-3 | 매니저 리포트 | M9-6 발행분: 매크로 국면·오늘 산/안 산 이유·주간 요약 |
| 10-4 | 투명성 리포트 | T+5 회고 결과·lesson 공개 |
| 10-5 | 리스크 상태 | 서킷·risk-off 안내("오늘은 매수를 쉬었습니다") |

## M11. 배포 — Wave 4 · 로컬 필수 / AWS 선택

| # | 태스크 | 상세 |
|---|---|---|
| 11-1 | 로컬 완성 (필수) | compose(app+Postgres) 원커맨드 기동 · `caffeinate` 상시 구동 가이드 · DB 일 1회 dump 백업 스크립트 |
| 11-2 | AWS (선택) | t3.small급 · Docker 그대로 · HTTPS · 시크릿 env/SSM · 텔레그램 하트비트. 못 하면 로컬 상시로 대체(완료 기준 아님) |

**최종 완료 기준**: **로컬 무인 하루 사이클 완주(개입 0) + 관리자/유저 화면 동작 + E2E 6종 재현**.

---

## ⏳ 보완 목록 (착수 시 채울 것 집계)

| 위치 | 항목 |
|---|---|
| W0-3 | ✅해소: compose가 schema.sql 자동 적용함(초기화 마운트). app-v2 DB는 5445로 격리 |
| W0 완료 후 | 자동 스크리닝 첫 실행 관찰 기록 |
| M11 | compose.yaml `web` 서비스가 LLM을 Ollama(host.docker.internal:11434·`qwen3.6:35b-a3b-nvfp4`)로 설정 — 실제 운영은 MLX(127.0.0.1:8888·`Qwen3.6-35B-A3B-OptiQ-4bit`). 컨테이너 배포 시 이 불일치 정리 필요 |
| 정책(결정됨) | **거래 세션 정책 확정(2026-07-18, 문성혁)**: 최종 목표 = **전 세션 개방**(프리 04:00–09:30 · 정규 09:30–16:00 · 애프터 16:00–20:00, America/New_York). 관측(데이터 수집·판단)은 24시간. 활성화는 전제 충족 순: ① W0 = 정규장 시장가 브래킷(현행 코드) ② M5 = 장외 **청산** 개방(지정가+extended_hours, 전제 없음 — 하드 악재 시 애프터아워 즉시 청산) ③ M5+ = **보호주문 상태기계**(미보호 포지션 레지스터·개장 시 부착·실패 시 알림+청산 폴백·재시작 복구) 완성 후 장외 **매수** 개방. 근거: 장외 브래킷 불가(Alpaca) → 매수는 손절 공백이 생기나 매도는 무관(비대칭). 세션별 스위치는 config 소유. 파급: M1-3 캘린더에 `current_session(dt)→pre\|regular\|after\|closed` 추가 · M4 프리마켓 갭 가드 · M5 장외 청산+상태기계 |
| ⏳ M5 착수 시 | Alpaca 오버나이트(24/5, 20:00–04:00) 지원·대상종목 확인 + 프리/애프터 **호가 데이터** 확보 여부(장외 지정가 산정에 필요) |
| M1 | DueRoleScheduler 시그니처·last_runs 쿼리 확정 |
| M2-4 | side CHECK 제약명 확인 |
| M2-8·M8-3 | budget.daily_llm_usd — 첫 주 실측 후 확정(임시 $3) |
| M4-2 | 매크로 감점표 구간 수치 — 1차 동결본 s07 POLICY에서 이관 |
| M4-6 | Form 4 파싱 필드(transactionCode 등) 소스 확인 |
| M5 착수 시 | **매도 주문 표현 설계 확정**(order_type/leg 취소 방식) — 스키마 영향 시 M2에 추가 |
| M6-2 | 당일 시작 equity 스냅샷 저장 설계 |
| M8-1 | 텔레그램 봇 토큰 생성(사용자·BotFather) |
| M8-2 | pydantic_ai usage(토큰) 추출 API 확인 |
| M9 착수 시 | 관리자·유저 와이어프레임 |
| 각 마일스톤 | TDD 스텝(테스트·구현 코드) 그 자리 전개 · 완료 시 문서 #logic 미러 + changelog |
