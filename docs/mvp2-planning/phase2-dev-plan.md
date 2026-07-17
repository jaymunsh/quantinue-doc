# MVP-2 마스터 개발 계획서 — Quantinue "AI 펀드 서비스"

> 작성: 2026-07-18 · 문성혁 + Claude · 입력 = `phase0-asbuilt-audit.md`(현실) + `phase1-decisions.md`(결정 R1~R8)
> **2층 구조**: 이 문서 = 마스터 계획(마일스톤·범위·스키마·성공 기준). 각 마일스톤 착수 시 superpowers writing-plans로 태스크 단위(TDD 스텝) 상세 플랜을 `docs/mvp2-planning/plans/`에 생성한다.
> 원칙: 마일스톤은 각각 **독립적으로 동작·테스트 가능한 소프트웨어**를 산출한다.

**Goal:** 1차 MVP(반자동 단일 계좌 파이프라인)를 무인 운영 + 관리자 ERP + 유저 포털을 갖춘 멀티 계좌 AI 페이퍼 펀드 서비스로 완성한다.

**Architecture:** 리서치(01~08 신호 생성)는 사이클당 1회 공용 실행, 계좌(09~10)는 성향별 파라미터로 구독·실행. 앱 내장 스케줄러가 역할별 실행 창(뉴욕 시간)에 슬롯 정렬 cycle_ts로 구동. FastAPI 단일 앱이 파이프라인·관리자·유저 화면을 모두 서빙.

**Tech Stack:** Python 3.11 · FastAPI + Jinja2 · PostgreSQL · Alpaca(페이퍼 주문 + 무료 IEX 시세) · LLM 3단(mock / ollama qwen 3.6 35b / OpenAI gpt-4o-mini) · 텔레그램 봇 · Docker → AWS

## Global Constraints (전 태스크 공통)

- 컨벤션 8규칙 계승 (BOOLEAN is_/has_ · ENUM=TEXT 소문자 · TIMESTAMPTZ · 점수 0~1 · JSON=컬럼명 · tb_/_signal/v_ · signal append 불변 · 대리키+UNIQUE(ticker, cycle_ts))
  - ⚠️ 규칙 4 개정 제안: "NUMERIC(4,3) 예외 없음" → **"NUMERIC + CHECK(0~1), 표기 소수 3자리"** — 코드 현실(plain NUMERIC) 수용. 정밀도 강제의 실익 없고 기존 데이터 라운딩 위험만 있음
- 기계 계약 정본 = 코드 3파일 (schema.sql · schemas.py/ontology.py · pipeline.yaml). 문서와 충돌 시 **계약 변경은 항상 3파일 먼저** (#20 순서)
- 모든 문턱값·주기·한도는 config 소유 (하드코딩 금지) · ⚙️ 조정 가능 원칙 (R3)
- LLM 출력이 검증 없이 돈에 닿는 경로 0개 — 매도 판단 포함 (R7-1)
- app/ 코드 작업은 개발 착수 후 담당별 PR — 이 계획서 단계에서는 코드 수정 없음

---

## 마일스톤 지도 (의존 순서)

```
Stage A 기반         M1 멱등·스케줄러 ──→ M2 스키마·계약 확장
Stage B 판단 엔진     M3 깔때기 복원 ──→ M4 방어선 ──→ M5 매도·보유 재평가 ──→ M6 계좌 구조·서킷브레이커
Stage C 루프·운영     M7 학습 루프         M8 운영 가드 (M1 이후 언제든)
Stage D 서비스        M9 관리자 ERP ──→ M10 유저 포털 ──→ M11 배포
```

- A는 전체의 전제. B는 순차 의존. C·D는 B와 부분 병행 가능 (M8은 M1만 있으면 시작 가능, M9은 M6의 계좌 스키마 필요).
- 병렬 개발 시 담당 분할 제안: A+B(파이프라인 담당) / C+D(서비스 담당) — M6 계좌 스키마가 인터페이스 경계.

---

## Stage A — 기반

### M1. 슬롯 멱등 + 스케줄러 배선 + 캘린더 (R2, R7-4)

**목표**: 사람 손 없이 역할들이 자기 창에서 자동 실행되고, 어떤 재실행도 중복을 만들지 않는다.

| # | 작업 | 대상 |
|---|---|---|
| 1 | `cycle_ts` 슬롯 정렬 함수: `slot_of(now, period_minutes)` — 역할 주기 경계로 내림. 수동 실행 경로(`POST /runs`)에도 동일 적용 | `main.py:52` 교체 · `orchestration/` 신규 `slots.py` |
| 2 | NYSE 캘린더 모듈: 공휴일·단축 거래일·서머타임 (실행 창은 America/New_York 정의) · T+5 등 영업일 연산 제공 | 신규 `core/market_calendar.py` (`pandas-market-calendars` 또는 `exchange-calendars` 라이브러리) |
| 3 | 앱 내장 스케줄러: lifespan 백그라운드 루프가 1분마다 `DueRoleScheduler.due_roles()` 조회 → 역할별 실행 창 검사 → due면 슬롯 cycle_ts로 파이프라인 트리거 | `main.py` lifespan · 기존 `orchestration/policy.py` seam 배선 |
| 4 | 역할별 실행 창 config: 01~03 장전 1회 · 04 장전+장중 60분 · 05 확장시간 60분 · 06 장전~장중 30분 · 07~10 개장 중만 · 11 마감 후 1회 | `config/pipeline.yaml` `windows:` 신설 |

**성공 기준**: ① 같은 슬롯에서 수동+자동 2회 실행 → signal 테이블 행 1개(E2E-2) ② 휴장일에 07~10 미실행 ③ 서머타임 전환일 테스트 통과 ④ 앱 재시작 후 스케줄 자동 재개.

### M2. 스키마·계약 확장 (R1-4, R5-1, R6-3, R7-1)

**목표**: MVP-2의 모든 DDL·Pydantic·config 변경을 한 번에 계약화 (#20 순서 — 코드 구현보다 먼저).

**스키마 델타 전량** (`app/db/schema.sql` + `core/schemas.py` + roles contracts):

| 변경 | 내용 |
|---|---|
| reason TEXT→JSONB ×4 | tb_disclosure · tb_disclosure_signal · tb_news · tb_news_signal — 키=점수 컬럼명 {sentiment_score, importance, risk_score, (source_trust), confidence}. Pydantic `reason: dict[str, str]` |
| tb_disclosure_signal +2 | `disclosure_count SMALLINT NOT NULL` · `top_evidence TEXT[] NOT NULL DEFAULT '{}'` (뉴스 대칭 · importance 순) |
| side ENUM 확장 | tb_strategist_signals `side CHECK IN ('buy','hold','sell')` — 매도 판단 (M5) |
| inv_type 확장 | `CHECK IN ('aggressive','conservative')` — 안전형 영문명 **conservative 확정** |
| 신규 tb_llm_usage | `id BIGSERIAL PK · called_at TIMESTAMPTZ · task TEXT · model TEXT · prompt_tokens INT · completion_tokens INT · est_cost_usd NUMERIC · run_id` — 비용 대시보드·예산 가드 데이터원 |
| 신규 tb_user | `user_id BIGSERIAL PK · login_id TEXT UNIQUE · display_name TEXT · role TEXT CHECK ('admin','user') · otp_secret TEXT · is_active BOOLEAN · created_at` — 관리자가 생성 (셀프 가입 없음) |
| tb_account 확장 | `+user_id BIGINT FK tb_user · +inv_type TEXT CHECK · +status TEXT CHECK ('active','paused','closed')` — 1유저=1계좌 UNIQUE(user_id) |
| 신규 tb_benchmark_price | `(price_date DATE, ticker TEXT) PK · close NUMERIC` — SPY 일봉 (R7-6) |
| tb_review | T+5를 영업일 기준으로 (M1 캘린더 사용 — 스키마 변경 없음, 계약 주석) |

**config 델타** (`config/pipeline.yaml` — 성향 프로파일이 핵심 구조):

```yaml
mvp2:
  profiles:
    aggressive:    {buy_threshold: 0.65, riskoff: penalty,      late_entry_ret5d: 0.15,
                    max_positions: 10, max_weight: 0.20, daily_loss_limit: 0.04, min_cash: 0.10}
    conservative:  {buy_threshold: 0.75, riskoff: no_new_buys,  late_entry_ret5d: 0.12,
                    max_positions: 5,  max_weight: 0.10, daily_loss_limit: 0.02, min_cash: 0.30}
  gates:      {source_trust_min: 0.55, hard_negative_max: 0.15, macro_penalty_cap: 0.40,
               snapshot_tolerance: 0.02, critic_approval: 0.70,
               overconfidence_conviction: 0.90, overconfidence_approval: 0.80}
  screening:  {universe_size: 2000, min_price_usd: 5, min_avg_dollar_vol: 20000000,
               daily_picks: 50, llm_depth: 20}
  exits:      {time_exit_bdays: 10}
  budget:     {daily_llm_usd: <M8에서 실측 후>}
  models:     {default: gpt-4o-mini, strategy: gpt-4o-mini}   # 태스크별 오버라이드 구조 — 07만 상위 모델 교체 가능하게
```

**성공 기준**: 3파일(schema.sql·schemas.py/ontology.py·pipeline.yaml) 변경이 한 PR로 merge, 전체 기존 테스트 green(수정 포함), 마이그레이션 스크립트로 1차 DB에서 무손실 이행.

---

## Stage B — 판단 엔진

### M3. 깔때기 원설계 복원 (R8)

**목표**: 16,000 → 2,000(주1회) → 50(매일) → 20+보유(LLM 심층).

| # | 작업 | 대상 |
|---|---|---|
| 1 | 주1회 유니버스 배치: NASDAQ screener API → 시총 상위 2,000 → tb_universe append (기존 PK(as_of_date,ticker) 설계 그대로) | `role_01` — `PUBLIC_UNIVERSE_LIMIT` 50→2000, 주1회 창(M1) |
| 2 | 일봉 벌크 수집: Alpaca 멀티심볼 bars로 2,000종목 (200req/min 내 배치) | `market_data/` 확장 |
| 3 | 하드 필터: 주가 ≥ $5 · 20일 평균 거래대금 ≥ $20M ⚙️ | `role_02` 전처리 |
| 4 | 오늘의 50: 버킷 스코어링(원설계 5버킷) → tb_daily_pick 50행 | `role_03` — `DAILY_PICK_LIMIT` 20→50 |
| 5 | LLM 심층 대상 = 점수 상위 `llm_depth`(20) ∪ 보유 종목 | `orchestration/pipeline.py` `run_screening` |

**성공 기준**: ① public 모드 1회 실행에 tb_universe 2,000행·tb_daily_pick ≤50행 ② 전체 배치가 장전 창 안(≤30분)에 완료 ③ Alpaca 레이트리밋 미위반(백오프 로그 0) ④ LLM 호출 종목 수 ≤ 20+보유수.

**알려진 한계 (명시)**: 뉴스·공시 감시망이 "오늘의 50"에 묶임 → 기술적 셋업 전에 터지는 촉매는 다음 날 volume_surge/breakout 버킷으로 후행 포착. **뒤집기 트리거**: 회고 실측에서 "50 밖 촉매 놓침"이 유의미하면 뉴스 수집만 2,000 전체로 확대 검토(RSS는 저비용).

### M4. 판단 방어선 (R3, R5)

**목표**: 문서의 게이트가 코드에서 실제로 작동한다. 순서 = 저비용순.

| # | 작업 | 대상 |
|---|---|---|
| 1 | snapshot 실전달: 08의 합성(close_prev=price 등) 제거 — 07이 박제한 4필드를 실제 전달, 허용오차 2% 검증 | `role_08/service.py:41-54` · `contracts.py` |
| 2 | 07 게이트 3종: source_trust ≥ 0.55 투표 박탈 · 매크로 감점표(risk_score→감점, cap −0.40) · 강악재 ≤ 0.15 차단 — 전부 profiles/gates config 참조 | `role_07/contracts.py` `can_buy` 재작성 |
| 3 | Critic 과신 에스컬레이션: conviction ≥ 0.90 → 승인 문턱 0.70→0.80 | `role_08/service.py` |
| 4 | 출처등급: 도메인→grade 판정(allow/gray/block) · block 사전 drop(`is_dropped=true` 보존, LLM 미도달) · 신규 `config/news_trust_policy.yaml`(화이트리스트+매체 등급표) | `role_06/selection.py` · `db/domain_sources.py` 하드코딩 제거 |
| 5 | 대표 기사 하이브리드: 관련성 필터(기존 relevance) 통과분 중 importance×w 최상위 · 동률 published_at→id · peak_importance 실계산(신뢰 검증분 최댓값) | `role_06/selection.py:141-147` |
| 6 | Form 4 신호 정책: 템플릿 조립(LLM 0회) + 매수 P코드만 정상 importance·매도 기본 저평가·클러스터 규칙 | `role_05` 신규 `form4.py` |
| 7 | consensus 실계산(설명용, 비게이트) · late_entry 성향 차등 · halted 주문 직전 체크 | `role_07` · `role_09` · `role_10` |

**성공 기준**: ① 게이트별 단위 테스트(경계값 ±0.001) ② block 매체 기사가 LLM 호출 0회로 drop 기록 ③ 08 검증이 07 박제값과 2% 초과 괴리 시 hold(합성 코드 삭제 확인) ④ 모든 문턱이 yaml 변경만으로 바뀜(코드 리터럴 grep 0).

### M5. 매도·보유 재평가 (R7-1) — **최대 설계 작업**

**목표**: 시스템이 파는 것도 판단한다.

| # | 작업 | 대상 |
|---|---|---|
| 1 | 분석 대상 = 오늘의 후보 ∪ 전 계좌 보유 종목 (사이클 시작 시 보유 조회·합집합) | `orchestration/pipeline.py` |
| 2 | 07 sell 판단: 보유 종목 입력에 보유 맥락(진입가·보유일·손익) 추가 → buy/hold/sell 출력. 프롬프트 개정 | `role_07` service·contracts·`prompts/role_07_strategy.md` |
| 3 | 매도도 Critic 경유 (sell 판단 검증 규칙 — 근거 없는 패닉 매도 반박) | `role_08` |
| 4 | 청산 3층 집행: 브래킷(기존) · 시간 청산(10영업일+논지 미실현, M1 캘린더) · 논지 붕괴(하드 event_type은 코드 직행, 점수 기반은 07→08 경유) | `role_09`/`role_10` · 신규 `roles/exits.py` |
| 5 | 기존 브래킷 주문과 수동 매도 주문의 정합 (브래킷 leg 취소 후 시장가 청산) | `broker/alpaca.py` |

**성공 기준**: E2E-4 — 보유 종목에 하드 악재 fixture 주입 → 다음 사이클 자동 청산 주문 + 사유 기록. 스크리너 탈락 보유 종목이 05~07 분석 대상에 포함됨(로그 검증).

### M6. 계좌 구조·서킷브레이커 (R1-5, R7-2)

**목표**: 신호 1회 생성 → N계좌 성향별 실행. 계좌 전체 관점 안전장치.

| # | 작업 | 대상 |
|---|---|---|
| 1 | 리서치 공용/계좌 구독: 09~10을 계좌 루프로 — 계좌별 profiles 파라미터로 게이트·사이징 차등 | `orchestration/pipeline.py` · `role_09` |
| 2 | 서킷브레이커 4종: 보유 수·종목 비중·일일 손실(당일 신규 매수 중단, 익영업일 해제)·최소 현금 — 발동 기록 테이블 or pipeline 로그 + 알림 훅(M8) | `role_09` 확장 (기존 노출 한도 예약 구조 위에) |
| 3 | 시뮬 포트폴리오의 멀티 계좌화 (기존 단일 계좌 가정 제거) | `db/` simulated portfolio 계열 |

**성공 기준**: E2E-5 — 같은 신호(conviction 0.70)에 공격형 매수·안전형 보류. E2E-3 — 일손실 한도 초과 fixture → 당일 매수 skip + 사유 + 익영업일 자동 해제.

---

## Stage C — 루프·운영

### M7. 학습 루프 (R6-1·2, R7-6)

| # | 작업 | 대상 |
|---|---|---|
| 1 | T+5(영업일) 자동 채점: 마감 후 창에서 due 리뷰 일괄 처리 (수동 API는 관리자 재실행용 존치) | `role_11/processor.py` 스케줄 배선 |
| 2 | LLM lesson 생성 배선 (미호출 REVIEW 태스크 연결) — lesson 1~2문장 구조화 | `role_11/service.py` · `llm/prompts.py` |
| 3 | 07 메모리 주입: 같은 종목 최근 5 lesson JOIN → 프롬프트 (총량 제한) | `role_07` · `db/postgres_read.py` |
| 4 | SPY 벤치마크: tb_benchmark_price 일수집 + 계좌·판단 성과의 상대 수익률 산출 | `market_data/` · `role_11/contracts.py` |

**성공 기준**: 채점이 사람 개입 없이 영업일 T+5에 실행(휴장 보정 테스트) · lesson이 다음 07 프롬프트에 나타남(fixture 검증) · 유저 화면 데이터로 상대 성과 노출 가능.

### M8. 운영 가드 (R6-3, R7-3·5)

| # | 작업 | 대상 |
|---|---|---|
| 1 | 텔레그램 봇 알림 (무료): 스케줄 실패·소스 무응답·서킷브레이커·예산 상한 — 채널 1개, 관리자용 | 신규 `core/notify.py` (Bot API 직접 호출, 의존성 최소) |
| 2 | LLM 비용 기록: 모든 `.analyze()` 호출에 tb_llm_usage insert (토큰·추정비용) + **일일 예산 상한** 초과 시 스킵→신규 매수 보류+알림 | `llm/provider.py` 래핑 |
| 3 | 계좌 대사: 마감 후 DB↔Alpaca 잔고·보유 비교 → 불일치 시 브로커 정본 보정 + 알림 | 신규 `broker/reconcile.py` |
| 4 | 데이터 소스 헬스: RSS/공시 소스 N시간 무응답 감지 → 알림 | 스케줄러 훅 |
| 5 | gpt-4o-mini 단가 기준 비용표 재계산 → `budget.daily_llm_usd` 기본값 확정 (1차 비용표 폐기) | 문서 + config |

**성공 기준**: 각 알림 시나리오 fixture 테스트 · 대사 불일치 주입 시 보정+알림 · 예산 초과 시 LLM 0회+보류 기록.

---

## Stage D — 서비스

### M9. 관리자 ERP (R1-2)

기존 Control Room(Jinja2) 확장. 인증: tb_user 로그인 + TOTP(OTP) — 관리자 role 전용 구역.

- 계좌 CRUD: 개설(유저 생성 포함)·성향 지정·일시정지·해지 / 유저 계정 생성 = 관리자만
- 계좌 총람: 계좌별 수익률(SPY 대비)·보유·주문·서킷브레이커 상태
- 운영 콘솔: 스케줄 상태(다음 실행 예정·최근 실패)·파이프라인 라이브(기존)·방어선 발동 내역
- **비용 대시보드**: tb_llm_usage 집계 — 오늘/이번 달 호출·토큰·비용·잔여 예산
- 유지보수: 로그 조회·수동 재실행(리뷰·대사·특정 역할)·config 조회(읽기)·데이터 헬스
- 리포트 발행: 유저용 데일리 브리핑 미리보기·발행

**성공 기준**: 관리자 시나리오 E2E(계좌 개설→성향 지정→다음 사이클 자동 편입) · 비관리자 접근 차단 테스트.

### M10. 유저 포털 (R1-3)

read-only. 인증: 로그인+OTP. 자기 계좌만 조회(소유권 검증).

- 내 계좌 홈: 총자산·수익률 곡선·SPY 대비·보유 카드
- 거래 타임라인: 체결 + reason JSONB 기반 사람 언어 사유
- 매니저 리포트: 매크로 regime·오늘 산/안 산 이유·주간 요약 (M9 발행분)
- 투명성 리포트: T+5 회고 결과·lesson 공개
- 리스크 상태: 서킷브레이커·risk-off 안내 ("오늘은 매수를 쉬었습니다")

**성공 기준**: 타 유저 계좌 URL 접근 차단 · 쓰기 엔드포인트 0개(라우트 감사) · 모든 화면이 실데이터로 렌더(E2E-1 완주 후).

### M11. 배포 (R2-5)

로컬 Docker Compose(app+Postgres) 완성 → AWS 소형 인스턴스(t3.small급) 이전 · HTTPS · 시크릿은 env/SSM · DB 백업(일 1회 dump) · 텔레그램 死활 알림(하트비트).

**성공 기준**: 클라우드에서 무인 5영업일 연속 운영 — 사람 개입 0회, 알림 오탐 정리, E2E 시나리오 전부 재현.

---

## E2E 검증 시나리오 (전 단계 공통 회귀)

| # | 시나리오 | 검증 |
|---|---|---|
| E2E-1 | NVDA 매수 완주 (1차 픽스처 계승) | 01→11 완주·페이퍼 주문·체결 |
| E2E-2 | 같은 슬롯 2회 실행 | signal 행 1개 (멱등) |
| E2E-3 | 일일 손실 한도 초과 | 당일 매수 skip·익영업일 해제 |
| E2E-4 | 보유 종목 하드 악재 | 자동 청산 + 사유 |
| E2E-5 | 동일 신호·2성향 | 공격형 매수 / 안전형 보류 |
| E2E-6 | 휴장일 | 07~10 미실행·T+5 영업일 보정 |

## 계획서가 스스로 표시하는 열린 항목

1. `budget.daily_llm_usd` 기본값 — M8-5 비용 재계산 후 확정
2. 뉴스 감시망 50 한계 — M3 뒤집기 트리거로 회고 실측 후 재론
3. 규칙 4(NUMERIC 정밀도) 완화 — v4.0 문서 개정 때 팀 고지
4. 태스크별 LLM 모델 오버라이드 — 구조만 M2에 확보, 07 상위 모델 교체는 운영 실측 후 판단
