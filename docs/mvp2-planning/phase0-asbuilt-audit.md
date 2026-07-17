# Phase 0 — 1차 MVP as-built 감사 보고서

> 작성: 2026-07-18 · 문성혁 + Claude (Explore 에이전트 4개 병렬 감사)
> 대조 기준: 설계 정본 `docs/quantinue-integrated-design.html` v3.8 (동결본과 동일 시점) vs 실제 코드 `app/` (커밋 `6163630` "1차 MVP 운영 파이프라인과 관리자 화면 완성")
> 용도: Phase 1(2차 범위·협의 항목 브레인스토밍)의 아젠다 재료. 각 괴리는 **"문서를 코드에 맞출 것인가(as-built 수용) vs 코드를 문서에 맞출 것인가(2차 구현 백로그)"** 판정 대상이다.

---

## 1. 1차 as-built 요약 (코드가 실제로 하는 것)

| 영역 | 실체 | 근거 |
|---|---|---|
| 실행 형태 | FastAPI + uvicorn 웹앱. **cron·데몬 없음** — 파이프라인은 HTTP 트리거(`POST /runs`, `/api/runs`)로만 실행 | main.py:207,232 · Dockerfile:12 |
| 스케줄 | 주기값은 선언만 존재(04=60분·05=60분·06=30분·07=120분). `DueRoleScheduler`는 순수 함수 seam — **프로덕션 호출자 없음, 자동 실행 미배선** | pipeline.yaml:27-35 · policy.py:200 |
| 스크리너 깔때기 | **50 → 20 → 20** (유니버스 50 `PUBLIC_UNIVERSE_LIMIT` → 기술분석 20 `TECHNICAL_UNIVERSE_LIMIT` → 일일 픽 최대 20 `DAILY_PICK_LIMIT`, 픽 게이트 0.70). 단 **기본 모드는 fixture(1종목)** — 깔때기는 `data_mode=public`에서만 발현 | role_01 service.py:18 · role_02:27 · role_03:19 · config.py:84 |
| 컴포넌트 01~11 | **전부 구현·조립됨.** 단 11 회고는 `pending_t_plus_5` 대기 레코드만 생성 — 실제 T+5 채점은 별도 `/api/reviews/{signal_id}/process` 수동 호출(Postgres 전용), LLM 채점 경로(`AnalysisTask.REVIEW`)는 정의만 되고 미배선 | factory.py:65 · role_11 service.py:75 |
| LLM | **`gpt-4o-mini` 단일** (문서의 gpt-5.4-mini·gpt-4o는 코드에 없음). 기본 `LlmMode.MOCK`(결정론적 목) — 실호출 지점은 05·06·07·08의 4개 태스크. 모델명은 yaml/env config 소유(하드코딩 아님) | config.py:92 · pipeline.yaml:23-26 · provider.py:196 |
| 관리자 화면 | Jinja2 서버렌더 Control Room — 실행 목록·진행률 N/11·스크리닝 랭킹·시뮬 포트폴리오·1.5초 폴링 라이브 패널. 실거래 모드에서만 토큰 게이트 | dashboard.html(606줄) · main.py:71-75 |
| 주문 | Alpaca **페이퍼 전용 트리플 게이트** + Mock. 고정 브래킷(SL −15% / TP +20%), `client_order_id = q-a{account}-s{signal}` 결정적 파생, 409/timeout 재조정 | alpaca.py:113-118,244 · role_09 contracts.py:12-14 |
| 재현·계보 | signal→order→fill id 연결 완비. 증거성 테이블 전부에 **계보 10컬럼**(model_provider·model_name·prompt_version·policy_version·input_hash 등) 인라인 + 운영 로그 4테이블(pipeline_runs·pipeline_stage_attempts·pipeline_checkpoints·order_submissions) | schema.sql:50-51,145-172 |
| 테스트 | **347개 / 57파일** (unit 288 · integration 27 · real_key 3 · top-level 29). 결정론 경로 위주 두터움, 실 LLM/브로커는 격리 | tests/ |

**한 줄 평**: 골격(11역할 조립·주문 안전장치·재현 계보·테스트)은 문서보다 튼튼하게 나왔고, **설계의 "판단 방어선"(게이트·등급·멱등 슬롯)은 대부분 아직 껍데기**다.

---

## 2. 괴리 리스트 A — 문서 계약인데 코드에 없는 것 (심각도순)

| # | 항목 | 문서 계약 | 코드 실제 | 심각도 |
|---|---|---|---|---|
| A1 | **cycle_ts 멱등 파손** | 계획 슬롯 시각(주기 경계로 내림) → UNIQUE(ticker, cycle_ts)가 멱등 장치 | `now()` 분 단위 내림 (main.py:52) — 같은 슬롯 내 재실행이 다른 cycle_ts를 받아 멱등 깨짐 | 🔴 설계 근간 |
| A2 | **출처등급 GRAY/BLOCK 전체 미구현** | allow 1.0/gray 0.6/block 사전 drop + 화이트리스트 yaml | `grade="allow"`·`is_dropped=False`·`permission="trade_eligible"`·`source_trust=1` 전부 **하드코딩** (domain_sources.py:61-95), yaml 파일 자체 없음 | 🔴 방어선 부재 |
| A3 | **07 게이트 3종 미구현** | source_trust ≥ 0.55 게이트 · 매크로 감점 −0.40 통일표 · 강악재 sentiment ≤ 0.15 컷 | conviction = 4점수 평균 ≥ 0.65/0.70만. source_trust·매크로·0.15 참조 0건 | 🔴 방어선 부재 |
| A4 | **#23 미반영** (알려진 후속) | reason JSONB(점수별 객체) 4곳 · disclosure_signal에 disclosure_count·top_evidence | reason 전부 TEXT/str · disclosure_count 흔적 없음 · top_evidence는 news_signal 컬럼만 있고 항상 '{}' (미소비) | 🟠 |
| A5 | **snapshot 검증 무력** | 07이 4필드 박제 → 08이 검증, 허용오차 협의 | 08이 last_price로 **합성**(close_prev=price, high/low=±1%) → 30% 오차 게이트(MAX_PRICE_MOVE)가 구조적으로 절대 트립 안 함. 5분 신선도 SLA는 있음 | 🟠 |
| A6 | **대표 기사 선발 다름** | importance × 신뢰무게(w) 최상위, 동률 published_at → id | relevance 매칭점수(제목/스니펫 키워드) 최상위, 동률 published_at → canonical_url → index (selection.py:141-147) | 🟠 |
| A7 | **메모리 루프 없음** | 07이 tb_review.lesson JOIN으로 최근 5개 읽기 | 11이 lesson 쓰기만, 07은 미참조 — 학습 피드백 미배선 | 🟠 |
| A8 | peak_importance 골격만 | 신뢰검증 최댓값 계산·저장·프롬프트 소비 | 계약 필드·컬럼·대시보드 표시만 있고 계산 없음 → DB 항상 NULL | 🟡 |
| A9 | Form 4 템플릿 없음 | 코드 템플릿 조립, LLM 0회 (#11) | 전량 LLM 경로, 템플릿 조립기 없음 | 🟡 |
| A10 | halted 처리 없음 | 거래정지 skip (실시간 조회) | tradable 조회 0건, `delisting_halt`는 분류 라벨뿐 | 🟡 |
| A11 | weak_evidence·consensus 죽음 | consensus 투표 규칙 | `signal_consensus=0` 하드코딩, 계산 없음 | 🟡 |
| A12 | late_entry 컷 없음 | ret_5d 12% vs 15% 협의 | 진입 컷 없음 — ret_5d는 사후 리뷰 지표만 | 🟡 |
| A13 | NUMERIC(4,3) 규칙 4 | 모든 점수 (4,3) 예외 없음 | (4,3)은 3곳뿐, 나머지 plain NUMERIC + CHECK 0~1 (범위는 지켜짐) | 🟢 경미 |

## 3. 괴리 리스트 B — 코드가 문서보다 앞선 것 (문서에 역반영 필요)

| # | 항목 | 코드 실제 | 문서 상태 |
|---|---|---|---|
| B1 | **계보 10컬럼 인라인** | source·source_ref·captured_at·evidence_id·parent_evidence_ids·model_provider·model_name·prompt_version·policy_version·input_hash — 증거성 테이블 전부에 1차 선구현 | #16에서 "2차 · 별도 tb_job_run에"로 계획했던 것 — 위치·시기 모두 다름 |
| B2 | **운영 로그 4테이블** | pipeline_runs · pipeline_stage_attempts · pipeline_checkpoints · order_submissions | 문서엔 tb_job_run 제안만 (스키마 없음) |
| B3 | **주문 멱등키 명명** | `idempotency_key` (tb_order) + `client_order_id` (order_submissions, 결정적 파생) | #15는 client_order_id 컬럼 제안 (미확정 구간이었음) |
| B4 | **실제 문턱값들** | Critic 승인 0.70 · strategist_buy 0.65/0.70 · MIN_CONVICTION 0.6 · DAILY_PICK_THRESHOLD 0.70 · SL 15%/TP 20% · MAX_PRICE_MOVE 30% · 스냅샷 신선도 5분 | 문서 협의값(0.6→0.80 제안 등)과 다르거나 문서에 없음 |
| B5 | **관리자 화면(Control Room) 전체** | 대시보드·라이브 진행·포트폴리오·접근 게이트 | 문서에 설계 없음 |
| B6 | **역할 명명** | 01="1차 스크리너"(50) · 03="2차 스크리너"(20) | 문서 통념(01 유니버스·03 일일 스크리너)과 라벨 다름 |
| B7 | **LLM 실구성** | gpt-4o-mini 단일 + MOCK 기본 + local(qwen2.5:7b) 옵션 | 문서: gpt-5.4-mini + 07만 gpt-4o (비용표도 그 전제) |

## 4. 일치 확인 (양호 — 그대로 계승)

테이블 15/15 존재 · PK 규칙 8(대리키+UNIQUE(ticker,cycle_ts)) · cycle_ts/src_*_at 컬럼과 복합 FK · BOOLEAN is_/has_ · TIMESTAMPTZ 전면 · ENUM=TEXT 소문자 · permission 3단계 · trend 4값 · regime 0.30/0.70 · 수집주기 config 소유(뉴스 30분) · 뉴스 제목+스니펫(크롤링 없음) · 주문 브래킷·멱등·페이퍼 트리플 게이트 · verdict UNIQUE(signal_id) · news UNIQUE(news_key,ticker) · snapshot 4필드 스키마 · confirmed_score 완전 제거

## 5. Phase 1 협의 아젠다 (이 감사에서 도출)

각 항목의 질문은 동일하다: **as-built 수용(문서 수정)이냐, 2차 구현(백로그)이냐, 폐기냐.**

1. **A1 cycle_ts 슬롯 정렬** — 멱등은 설계 근간. 2차 구현 1순위 후보 + 자동 스케줄 배선(B의 DueRoleScheduler 미배선)과 한 묶음
2. **A2+A3 판단 방어선 일괄** (출처등급·source_trust 게이트·감점표·강악재컷) — 2차의 "신호 품질" 테마로 묶어서 우선순위 결정
3. **A4 #23 후속** — 이미 확정된 계약, 구현만 남음 (정창욱)
4. **A5 snapshot 합성 문제** — 07→08 실전달로 고치고 허용오차 숫자 확정 (기존 이월 안건과 합류)
5. **A6~A12** — 각각 2차 범위 포함 여부 개별 판정 (특히 A7 메모리 루프는 "회고가 전략을 개선한다"는 프로젝트 서사의 핵심)
6. **B1~B7 문서 역반영** — v4.0 새 문서는 이 as-built 사실들을 기본값으로 서술 (계보 컬럼·운영 테이블·실제 문턱값·Control Room·LLM 실구성)
7. **LLM 모델 전략** — 문서 비용표(gpt-5.4-mini/gpt-4o 전제) 폐기하고 실구성(gpt-4o-mini) 기준 재작성? 아니면 2차에 모델 업그레이드?
8. **11 회고 완성** — 대기 레코드→실채점 자동화 + LLM lesson 생성 배선 (2차 범위 후보)
