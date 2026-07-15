# SESSION 정리 — Quantinue 통합 설계서 작업 (2026-07-11)

> 다른 PC에서 이어갈 때: **이 파일을 먼저 읽고**, 정본 문서 `docs/quantinue-integrated-design.html`을 브라우저로 열어 대조하면서 진행할 것.
> AI에게 시킬 때는 "SESSION.md 읽고 이어서 진행해줘"라고 하면 됨.

---

## 1. 컨텍스트 — 이 작업이 뭔가

- 프로젝트: **Quantinue** — 팀 "여름이었다"의 주식 자동매매 파이프라인 (01 유니버스 → 11 회고, 전부 페이퍼 트레이딩, 1차 MVP = 공격형 단일).
- 내(AI) 역할: **누가 들어도 이해하기 쉽게 설명하는 PM** — 팀 문서 7종을 통합·단순화·재설계.
- 원본 문서 (보존됨, 수정 안 함):
  - `docs/Official_Document.html` — 김지현(PM) 작성 역사적 뼈대. 현재 사람용 계약·결정 이력의 정본은 통합 설계서이며, 착수 후 기계 계약은 코드 3파일이 담당.
  - `docs/collector_agent/` 4종 — 정창욱(공시·뉴스 수집). 문서끼리 신·구 스키마 혼재(모순 있음).
  - `docs/strategist_agent/strategist_agent.html` — 이은미 v3.8.
  - `docs/critic_agent/` PDF 2종 — 김미연.
- 담당자: 김지현(PM·인프라·01~04·09·10) / 정창욱(05·06 수집) / 이은미(07 전략) / 김미연(08 반박) / 문성혁(11 회고).
- ⚠️ **이 세션의 사용자 = 리뷰 에이전트(11 회고) 담당자, PM 아님** (07-11 오후 확인) — 정창욱(05·06) 업무를 대행 개선 중이며, **문서 표기는 정창욱으로** 한다. 결정 표기도 "PM 확정"이 아니라 중립("07-11 확정")으로.

## 2. 산출물 (이 세션에서 만든 것)

| 파일 | 내용 |
|---|---|
| **`docs/quantinue-integrated-design.html`** | ⭐ 정본 통합 설계서. 이것만 보면 프로젝트 전체 파악 가능하도록 작성. Mermaid 3개(전체 흐름·ERD·릴레이) — **CDN 로드라 인터넷 필요** |
| `DESIGN.md` | 통합 설계서의 색·타입·레이아웃·컴포넌트·접근성 계약 |
| `SESSION.md` | 이 파일 |

문서 구조 (섹션 id): `#conv` 공통 컨벤션 → `#big` 전체 흐름 → `#erd` ERD → `#mvp` 1차vs2차 → `#s01`~`#s11` 컴포넌트별 → `#relay` 데이터 릴레이 → `#issues` 협의 현황판 → `#llm` AI 모델·요금 → `#ai` AI 추천 제안.
각 섹션 형식: **파이프라인 그림 → 설명 → 스키마 표(1행=1컬럼) → 전달 JSON**.

## 3. 확정된 핵심 결정 (전부 문서에 반영 완료)

### 구조 재개편 (사용자=PM 지시)
- **수집/전달 테이블 분리**: `tb_news`·`tb_disclosure` = **원본 원장**(기사/공시 1건 = 1행), `tb_news_signal`·`tb_disclosure_signal` 🆕 = **집계 스냅샷**(종목당 사이클 1행, 07·08이 읽음). 원본 역추적 키: `rep_news_id`, `filing_no`.
- **collected_at → created_at** 이름 통일 (팀 약속). 사건 발생 시각은 `published_at`(뉴스)·`filed_at`(공시)이 담당.
- 전 테이블 `created_at`, upsert 테이블만 `updated_at`.

### 공통 컨벤션 8규칙 (2026-07-11 원샷 확정 — `#conv` 섹션이 정본)
1. Boolean = `BOOLEAN` 타입 + `is_`(상태)/`has_`(보유·존재) 접두. INT 0/1 금지.
2. ENUM = 영어 소문자 snake_case. **Postgres 네이티브 ENUM·참조 테이블 FK는 쓰지 않음** — 물리는 TEXT, 검증 1차 책임은 백엔드(Pydantic Literal). `event_type` 허용값 정본은 `ontology.py`.
3. `*_at` = TIMESTAMPTZ(UTC 저장·표시만 KST), `*_date` = DATE.
4. **모든 점수 0~1 · NUMERIC(4,3) 소수 3자리 — 예외 없음** (tb_macro.risk_score도 0~1, regime 경계 0.30/0.70).
5. 전달 JSON 필드명 = DB 컬럼명 (개명 금지).
6. 원본=자연 명사 / 집계 전달=`_signal` 접미 / 뷰=`v_` 접두.
7. 신호 테이블은 **append 불변** (update 금지) — 최신 상태는 뷰/(ticker, cycle_ts DESC) 인덱스로.
8. 🆕 **PK 규칙 (07-11 오후 확정)** — created_at 등 시각 컬럼 PK 금지(순수 기록용). ticker 단독 PK 금지. 사이클 append 테이블 = **대리키 PK(테이블 약어+_id: tns_id·tds_id) + UNIQUE(ticker, cycle_ts)** (cycle_ts=계획 슬롯 시각, ON CONFLICT DO NOTHING 멱등). 일봉 테이블(universe·daily_pick·technical)은 (날짜, ticker) 자연 복합 PK 유지.

### 원샷 리네임 매핑 (구 이름 폐기 — `#conv` 매핑표가 정본)
`hard_block→is_hard_blocked` · `agree→is_agreed` · `hit→is_hit` · `dropped→is_dropped` · `side 매수/보류→buy/hold` · `inv_type 공격형→aggressive` · `bucket 한글→trend_leader/volume_surge/high_52w_breakout/pullback/squeeze_breakout/backfill` · `TIMESTAMP→TIMESTAMPTZ` · `risk_score 0~100→0~1`
⚠️ **반쪽 마이그레이션 금지** — #20 순서가 정본: 먼저 스키마·Pydantic/ontology·config 3파일로 계약을 단일화하고, 이후 담당별 PR로 코드·POLICY·픽스처·프롬프트를 교체한다. 전원 교체 완료 전 구 계약 코드 실행 금지. (trend "상승"vs"up" 버그 재발 방지)

### 기타 확정
- ~~tb_event_type 참조 테이블~~ → **폐기 (07-11 오후 · AI 제안 #12 대안 채택)** — event_type도 다른 ENUM처럼 **TEXT (ENUM)**. 정본 = ontology.py Literal + 문서 05 허용값 12종 표(내용은 유지 — 방향경향·발생원 제외 그대로). 마이그레이션·seed·기동 정합 검증 불필요.
- 🆕 **ENUM 문서 규칙 (07-11 오후 · 규칙 2에 명문화)** — ENUM 컬럼은 스키마 표 설명란에 **허용값 전체 필히 명시**, 미확정이면 ⚠️ 표시 후 확정 시 업데이트. 현재 ⚠️ 3곳: tb_order 유형·상태 / tb_fill 매수·매도 / tb_review_price_snapshots.source.
- **cycle_id 폐기** (이은미 v3.6) → `trade_date` + `src_disclosure_at/src_news_at/src_macro_at` FK로 대체. tb_critic_verdict의 cycle_id도 제거.
- **메모리 = `tb_review.lesson` 단일화** — tb_memory_entries 폐지 (07은 tb_review JOIN으로 읽음).
- **decision_close** 🆕 (tb_strategist_signals) — 보류 signal의 회고 P0.
- **출처등급**: GRAY=등록 2급 언론사만 / **BLOCK=소셜·블로그·미등록 전부(사전 drop)**. 구제는 화이트리스트(yaml) 등록.
- **집계 reason은 단일** — 점수별 분해 불필요 (건별 reason이 원본 테이블에 남음).
- confirmed_score 게이트 → **source_trust ≥ 0.55**로 대체 (이은미 v3.2 확정, 코드 반영은 P0 잔여).
- 전 스키마 표 **1행 = 1컬럼** (tb_news_signal은 1~26번 = 필드 수와 일치).

## 4. AI 추천 제안 (`#ai`) 처리 현황

| # | 항목 | 상태 |
|---|---|---|
| 1 | 공시도 원본/집계 대칭 분리 | ✅ **채택 확정 (07-11 오후 · PM 재논의 완료)** — 근거: Form 4는 기사화 안 돼 뉴스 매핑만으론 반쪽 신호 / 공시는 07의 독립 1표 / 집계만 두면 건별 reason 소실(#3 전제 붕괴) |
| 2 | 메모리 통일 | ✅ 해결 (tb_review.lesson) |
| 3 | reason 분해 | ✅ 해결 (단일 유지) |
| 4 | 출처등급 정책 | ✅ 해결 (BLOCK 채택) |
| 5 | 점수 스케일 | ✅ 해결 (전부 0~1·소수3) |
| 6 | Strategist→Critic payload snapshot | ✅ **해결 (07-11 오후 · PM 확정)** — payload snapshot 필수 + 누락 시 hold + **저장 = tb_strategist_signals 컬럼**(⚡그룹에 이미 3개 있어 close_prev 1개만 추가). 잔여: 허용 오차 숫자만 이은미↔김미연 (협의 현황판 #11) |
| 7 | 시각 컬럼 역할 분리 (발생시각 vs 수집시각) | ✅ **해결 (07-11 오후 · PM 확정)** — 발생시각=published_at·filed_at / created_at=DB 저장 확인용. **컴펌 #2 공식 폐기.** 구현 체크(창욱 null 정책·미연 픽스처)는 협의 현황판 #12로 이관 |
| 8 | created_at 동시각 충돌 방지 | ✅ **해결 (07-11 오후 · PM 확정)** — **컨벤션 규칙 8 신설**: PK(tns_id/tds_id 대리키) + UNIQUE(ticker, cycle_ts) · created_at 키 금지 · 일봉은 (날짜, ticker) 유지. 05/06 스키마 표 번호 +2(뉴스 1~28) · 07 src_*_at FK 대상 created_at→cycle_ts 교체 |
| 9 | 보류 경량화·hypertable/파티셔닝 | 🟡 오픈 — PM 검토 완료(07-11): 1차엔 급하지 않음, 담당자(김지현) 재량. 대안 3단계를 비유(버리는 날→서랍→전용 창고)로 쉽게 재작성 |
| 10 | bucket 영어화 | ✅ 채택됨 (원샷에 포함 · 생성과 별도 조치) |
| 11 | Form 4 reason 템플릿 생성 | ✅ **채택 (07-11 오후)** — 코드가 템플릿 조립(예: "Officer buy $1.2M, open market (code P)"). LLM 0회 유지 · null 예외 분기 제거. 문장 카탈로그는 정창욱 05 구현 때 |
| 12 | tb_event_type 채택 | ✅ **해결 (07-11 오후) — 제안 기각, 대안 채택** — 참조 테이블 폐기, event_type=TEXT (ENUM), 정본=ontology.py+허용값 표. 구현 표기=정창욱 |
| 13 | 구버전 문서 아카이브 | ✅ **처리 (07-11)** — 정본 히어로 배지 + collector 4종에 "구버전 참고용" 경고배너 부착. **collector 절대신뢰 금지** 명시 |
| 14 | 수집 주기 완화 (1차 뉴스 5분→30분) 🆕 | ✅ **채택 (07-11)** — 본문 전구간 반영(흐름도·06·배치·LLM·신설 #sched 섹션) |

### 07-11 세션 2차 작업 (AI 제안 UI 개편 + 스케줄 섹션)
- **AI 추천 제안(`#ai`) 표 → 카드 UI 전환**: 항목당 카드 1장(`.ai-item`), 헤더에 번호·제목·담당·상태 배지. 해결=초록카드(`.done`), 보류=노랑(`.hold`). 가로 스크롤 제거(제안/왜/대안을 세로 grid로). 취소선+결론 초록띠(`.ai-res`) 유지.
- **신설 `#sched` 시간대·스케줄 설계 섹션** (#big과 #erd 사이): 시간 3원칙 → 미국장 KST 변환표(서머타임 여름/겨울) → 하루 타임라인 → 스케줄 총괄표 → "수집≈판단÷2~4" 로드맵 표 → 구현원칙(주기=config) → 서머타임 함정 경고. 목차에도 추가.
- **뉴스 5분→30분** 문서 전체 반영 완료.

처리 규칙(사용자 지시): **해결되면 내용은 남기고 취소선 + 바로 아래 "↳ ✅ 해결 (날짜) — 어떻게 해결됐다" 행 추가.**

## 5. 협의 현황판 (`#issues`) 잔여 안건 (요약)

- 이은미 코드 P0: vote_news가 아직 삭제된 confirmed_score 의존 → source_trust 기준으로 재작성.
- weak_evidence 죽은 규칙 (consensus≤1 도달 불가) — 김미연↔이은미.
- halted 실시간 소스 (Alpaca 조회 필요) — 김미연↔김지현·정창욱.
- Critic 문턱 (grade 0.6→0.80 제안 · conviction 과신 8.0→0.80).
- late_entry ret_5d 12% vs 15%.
- peak_importance 정의 (최댓값 vs 대표기사).
- high_252_ratio 신규상장 왜곡 / daily_pick.score 저장 여부 / RiskCriticVerdict 3필드→13컬럼 확장.
- trend 픽스처(김미연)·게이트 문자열(이은미) 잔여 수정 — up/mixed/down/no_data로.

## 6. LLM 모델·요금 (`#llm` 섹션 신설됨)

- 호출처 6곳: 뉴스 기사별/배치종합/공시 해석/Critic 반박/Reviewer lesson = **gpt-5.4-mini**, **07 Strategist 판단만 gpt-4o** (개발·백테스트는 mini 다운시프트).
- Form 4는 LLM 0회(100% 코드). 하루 ~300–600회, 지배 비용 = 07.
- 원칙: 모델명 yaml config로(하드코딩 금지) · 단가·비용은 추정표로만(확정 시 OpenAI pricing 재확인 + 첫 주 usage 실측으로 교체).
- 🆕 **비용 추정표 추가 (07-11 오후 · PM 요청)** — 단가(2026-07 검색): gpt-5.4-mini $0.75/$4.50, gpt-4o $2.50/$10.00 per 1M. **운영 ≈ $3.0~4.5/일 (월 $60~95), 07이 8~9할** · 개발(전부 mini) ≈ $1.3~2.2/일 (월 $25~45).

## 7. 문서 편집 시 지켜야 할 것 (이어서 작업할 AI/사람용)

1. 신규 내용 = `🆕 신규 추가됨` 배지, 크게 바뀐 것 = `🔄 변경됨` 배지 + 비고란에 사유. 구 이름은 "구 xxx"로 병기.
2. AI 제안 해결 시: 취소선(`<s>`) + 아래 `<tr class="hl-new"><td colspan="7">↳ ✅ 해결...</td></tr>` 행.
3. **스키마 표는 1행 = 1컬럼** 유지.
4. 컨벤션 8규칙·리네임 매핑표(`#conv`)가 어휘의 정본 — 여기와 어긋나는 표기를 만들지 말 것.
5. Mermaid erDiagram은 속성을 **줄당 1개**로 (한 줄에 여러 속성 넣으면 렌더러가 죽음 — 실제로 겪음).
6. 파일 인코딩 UTF-8. 로컬에서 열 때 Mermaid는 인터넷 필요(jsdelivr CDN).
7. 🆕 **(07-12) 문서를 수정하면 반드시 `#changelog` 표 맨 위에 한 줄 추가** — 버전 +0.1 · 날짜 · 수정자 · 1~3줄 요약, 히어로 버전 배지도 같이 갱신. 줄이 없으면 그 수정은 없던 일로 간주.
8. 변경 이력의 버전마다 Git 커밋 1개와 같은 버전 태그를 남긴다. 변경 이력은 사람이 읽는 요약, Git은 실제 diff의 정본이다.

### 07-15 — v3.8: #23 공시/뉴스 2차 개편 (Strategist 요청 · 태그 v3.8)

- ⚠️ 상황: v3.6·v3.7은 이미 커밋됨(v3.7 = Codex의 importance·peak 판단 가이드). **app/ 실제 코드 개발이 대량 진행 중**(다른 작업자) — 나는 문서만 수정·커밋, 코드는 건드리지 않음.
- **① reason → JSONB 점수별 근거 객체** (#3 재개정 — 소비자 이은미 본인 요청이라 번복 정당). 키=점수 컬럼명(규칙 5). 원본=LLM 생성 / **집계=코드 조립(LLM 재호출 0 — Form 4 템플릿 #11 패턴)**. 원본·signal 4곳.
- **② 공시에 disclosure_count·top_evidence 추가** (뉴스 대칭 · 정렬만 importance 순 — 공시는 신뢰 축 없음). 공시 signal 18→20컬럼·전달 11→13필드.
- 반영: 05·06 스키마 표·집계 규칙·pack JSON·row 예시 + #23 카드 + #3 재개정 밴드.
- **구현 후속(정창욱)**: schema.sql `reason TEXT→JSONB`·disclosure_signal 2컬럼·schemas.py Pydantic — 이미 착수된 코드라 문서(정본) 선행 후 코드가 따라옴(#20 순서).

### 07-13 (4) — v3.5: #22 Q9 대표 기사 배열 비채택 (태그 v3.5)

- **대표 기사 = 정확히 1건 유지, 배열 비채택 확정** — 정보는 4중 보완(C그룹 전체 집계·top_evidence 3건·summary 종합·원본 역추적). 배열은 Critic ⑤ 기준 시각(단일 시각 전제 #7)과 게이트의 단일 event_type 전제를 깸.
- 알려진 한계 명시: 복수 대형 사건 동시 발생 시 event_type은 대표 것만(나머지는 summary·top_evidence). **뒤집기 트리거 = 회고 실측에서 복수 사건 빈도 유의미할 때 → 그때 정답은 배열이 아니라 사건 단위 행 분해**(종목×사건당 1행, 2차).

### 07-13 (3) — v3.4: #22 2차 보강 (Q7·Q8 추가 · 태그 v3.4)

- **#22 카드를 질문 원문+답 Q&A 8건 형식으로 재구성** (줄바꿈 가독성 — 밴드에 전문 노출, 접힘 없음).
- **Q3 상세화**: importance vs peak — 숫자 예시(로이터 0.90 + 블로그 4건 0.20 → 평균 0.38 vs peak 0.90) + 해석 3조합 표(`#peak-note`).
- **Q4 상세화**: 신뢰 3축 — 구분 질문 3개(열기 전/읽고/매기며) + 같은 로이터 기사 시나리오 4건 표. 합치면 "로이터發 소문"과 "2급 매체 진짜 단독"이 뭉개짐.
- **Q7 신규**: _signal 생성 시점 = **같은 사이클 배치의 마지막 단계**(원본 INSERT 직후 같은 실행이 집계·append). 원본=증분 / signal=매 사이클 무조건 50행 재집계(감사 장치 + ref 자동 갱신 원리) — s05 `⏱ 생성 시점` note.
- **Q8 신규**: 대표 기사 = **생성이 아니라 선발** — importance×신뢰무게(w) 최상위 1건의 얼굴 필드를 B그룹에 복사. 용도 3(신호의 얼굴·Critic ⑤ 기준 시각·역추적 진입점) — s06 `#rep-note`.
- Q5(왜 _signal) 상세화: 없으면 3중고(책임 침범·중복 계산·재현 불가) — s07 읽는 법 ①.

### 07-13 (2) — v3.3: #22 Strategist Q&A 6건 (태그 v3.3)

- 이은미 사전 미팅 질문 6건 — 결정 변경 없이 **설명 계약 보강**으로 해소 (유일한 신규 결정 = disclosure_ref 매칭 규칙).
- **📐 점수 눈금 가이드 신설**(s05 `#score-guide`) — sentiment/importance/risk/confidence의 0~1 기준점. **LLM 채점 프롬프트의 기준 정본으로도 사용**(사람·LLM 동일 눈금).
- **🔍 신뢰 3축 구분표**(s06 `#trust-axes`) — grade(봉투·코드·이산 3값)/trust(내용·LLM·연속)/confidence(자기 확신) — **3축 유지 확정**: 어긋나는 조합("봉투만 좋음")이 Critic ③ 판정 재료. + importance vs peak 읽는 법 3가지(대세 vs 한 방).
- **disclosure_ref 매칭 규칙 확정**: 같은 ticker + 같은 event_type + |filed_at−published_at| ≤ 72h 중 최신 — 집계마다 재계산. **뉴스가 먼저면 null이 정상, 다음 사이클 새 스냅샷이 자동 갱신 → upd_dt 불필요**(append 불변).
- **📖 "07이 신호를 읽는 법" 4항**(s07) — 접시/영수증 · 최신 행 SQL(ORDER BY cycle_ts DESC) · cycle_ts 용도 4가지 · ref 타이밍.

### 07-13 — v3.2: #21 공시/뉴스 개편 (collector 계승 전수 감사 · 태그 v3.2)

- **감사 기준: 컬럼마다 실제 소비자(07 문턱·08 반박·GPT 프롬프트·11 회고) 역추적** → 8건 발견·전부 반영.
- **명세 구멍 2 (신설)**: ① 공시 집계 규칙 — 대표=importance 최상위 1건·강악재(≤0.15) 최솟값·importance/risk 최댓값·신뢰가중 없음(SEC=확정 사실)·summary는 "외 N건" 코드 조립 ② 뉴스 본문 계약 — **1차 = 제목+RSS 스니펫만, 크롤링 없음**(2차 화이트리스트 검토) + news_key 정규화 규칙(리다이렉트 해제→트래킹 파라미터 제거→실패 시 원 URL).
- **정리 4**: signal 2테이블에서 sentiment 라벨·keywords 삭제(소비자 0 — 공시 signal 20→**18**·뉴스 signal 28→**26컬럼**, 원본 keywords는 사람 검색용 유지) · permission 6단계→**3단계 block/block_buy/trade_eligible** · **grade·permission 소문자화**(문서가 규칙 2 자가 위반 — 리네임 매핑표 등재) · risk_score 유지+소비자 명시("07 GPT 프롬프트 — 문턱·게이트 미사용").
- **계약 보강 2**: **filed_at·published_at 전달 JSON 추가**(Critic ⑤신선도 기준 시각이 전달에 빠져 있었음 — 공시 전달 10→11필드) · **reason "한 줄" 폐기 → 1~3문장**(사용자 지시 — reason=왜 이 점수/summary=무슨 일 역할 구분 유지).
- 협의 #7(peak_importance)에 "소비자가 GPT 프롬프트뿐 — 폐기 옵션 검토" 메모 보강. 영향 범위: 05·06 종결, 07·08엔 additive 필드 2개뿐.

### 07-12 (9) — v3.1: 원본 원장 사후 기입 5컬럼 삭제 (태그 v3.1)

- **tb_news·tb_disclosure에서 return_1d/3d/5d·outcome·updated_at 삭제** — collector 원안(가중치 낮음) 계승분이었으나 ①수집기가 자기 재료를 채점 = 역할 침범(채점은 11 회고·백테스트의 일) ②#16 look-ahead 위험의 원천 ③1차 무사용. **원본 원장 = 완전 불변(INSERT만)** 확정.
- 반영: 스키마 표(hl-del 삭제 기록 행)·row 예시·총람(사후기입 표기 제거)·ERD mermaid·#16 카드 부분 해소 밴드. 사건 단위 채점은 2차에 별도 평가 테이블로 하되 **필요성 자체를 그때 재검토**.
- 참고: 정본 파일명 = `docs/quantinue-integrated-design.html` (07-12 영문화) · 원격 = github.com/jaymunsh/quantinue-doc.

### 07-12 (8) — v3.0 폰트 우선순위 정비

- 본문 기본 글꼴을 Apple system font(`-apple-system`·`BlinkMacSystemFont`·`Apple SD Gothic Neo`)로 변경.
- Google Fonts CDN에서 `Noto Sans KR` 400·500·600·700을 `display=swap`으로 불러 Apple 계열 부재 시 2차 한글 fallback으로 사용.
- 릴리스 규칙: 커밋 1개 + 태그 `v3.0`.

### 07-12 (7) — v2.9 읽기 경험·접근성 개선

- `DESIGN.md` 신설 — 기존 GitHub 계열 시각 언어와 상태·반응형·접근성 계약을 명문화.
- 문서 상단에 **현재 상태 요약판** 추가 — 조건부 GO, 착수 게이트 4건, 단일 행복 경로 목표, #20 실행 순서를 먼저 확인.
- AI 제안은 **확인 필요 5건 / 완료·기록 15건** 필터로 분리. 완료 카드의 최종 결론을 먼저 노출하고 과거 제안·근거·대안은 기본 접기.
- 모바일 고정 플로팅 목차를 상단 sticky 바로가기로 교체. 핵심 비교·현황·보안·변경 표는 720px 이하 세로 카드로 전환하고 나머지 대형 표에는 스크롤 안내·키보드 접근성 추가.
- `<header>`·`<main>`·본문 skip link·목적지 제목 포커스·표 caption/scope·Mermaid 접근성 이름을 보강. 현재 정본과 역사적 원본의 역할 문구를 통일.
- 릴리스 규칙: 커밋 1개 + 태그 `v2.9`.

### 07-11 오후 세션 3차 작업 (AI 제안 일괄 확정 — PM 재논의)

- **#1·#6·#7·#8 전부 해결/채택 확정** (위 표 참조) — 카드 4장 초록 전환 + 근거 명분 코멘트.
- **컨벤션 규칙 8 신설** (PK·대리키) → 파급 반영: 리네임 매핑표 · ERD mermaid(tds_id/tns_id·cycle_ts UK) · 테이블 총람 · 05/06 스키마 표(tds_id·cycle_ts 행 삽입, 번호 +2 — **공시 1~20 · 뉴스 1~28**) · 07 src_*_at FK 대상 cycle_ts로 교체 · PK/FK 30초 요약 문구.
- **tb_strategist_signals에 close_prev 컬럼 추가** (#6 — ⚡스냅샷 그룹, snapshot 4필드 전부 박제 완료).
- **#llm에 💵 비용 추정표 신설** (1회 비용·하루 호출·하루/월 비용, 운영 vs 개발 요약 행).
- **협의 현황판**: 해소 #11(PK 규칙)·#12(시각 계약)·#13(snapshot 박제) 추가 / 남은 안건 #11(허용 오차)·#12(시각 계약 구현 체크) 추가.
- ⚠️ 사용자(PM) 피드백: **선택지 다이얼로그 띄우지 말 것** — 설명을 충분히 먼저, 결정은 대화로.

### 07-11 저녁 — 사용자 직접 추가 카드 #15~#17 (+#17 즉시 실행)

- **#15 (주문·체결 최소 계약 · 김지현)** 🔴 오픈 — 1차 = 고정 SL/TP 브래킷 · UNIQUE(account_id, signal_id) + client_order_id 멱등 · 상태 planned/submitted/filled. 근거의 Alpaca 제약(트레일링 스톱은 브래킷 leg 불가) 사실 확인됨. tb_order ⚠️에 상태 후보 연결해둠.
- **#16 (재현 계약 · 4인)** 🟡 오픈 — 1차 = signal→order→fill id 연결만, 2차 = model/prompt/policy 버전·input_hash·원문 snapshot·미래정보 차단(available_at ≤ decision_at). 사후 수익률은 별도 평가 테이블(규칙 7 append 불변과 정합).
- **#17 (문서 충돌 원샷) ✅ 즉시 실행 완료** — ①릴레이 created_at→cycle_ts ②강악재 ≤−0.7→**≤0.15** ③뉴스 26→28컬럼 ④7규칙→8규칙 ⑤감점 **−0.40 통일**(07 POLICY가 정본) ⑥**trade_date=America/New_York 영업일**(규칙 3 명문화). 추가 계약: strategist **cycle_ts+UNIQUE(ticker, cycle_ts, inv_type)** 🆕 · verdict **UNIQUE(signal_id)**(0~1행+ → 0~1행) · tb_news **UNIQUE(news_key, ticker)**. 잔여: verdict UNIQUE ↔ 변화 트리거 정합 김미연 확인(협의 #13).

### 07-12 — 착수 레디니스 리뷰 (#18 신설)

- **#18 카드 신설 (오픈 · 착수 전 체크)** — 전체 리뷰 결론 "조건부 GO". **블로킹 4건**: ①리네임+규칙 8 코드 반영 PR ②이은미 P0(vote_news) ③#15 주문 3테이블 ④snapshot 허용오차 숫자. **알고 출발 리스크 5**: 좁은 매수 깔때기(공시AND뉴스 — 완주 검증은 목데이터로)·halted skip·테스트 78개=구 계약 이력·서머타임 겨울 전환·tb_macro.as_of는 버그 아님. 트러블슈팅 참조용 — 블로킹 4건 완료 시 ✅ 전환.
- 낡은 표기 3곳 수정: 릴레이 07→08 상태(#6 확정 반영) · s07 "(ticker, created_at DESC)"→cycle_ts DESC · 흐름표 08 비고(78개 구 계약 주석).
- **tb_macro.as_of에 규칙 8 주석** — 시각 PK지만 cycle_ts 역할(계획 슬롯)이라 위반 아님 (스키마 행 + 총람).
- Q&A 확인: **cycle_ts는 사이클 append 테이블에 필수, 별도 관리(마스터·발급기) 불필요** — 스케줄러가 실행 시각을 주기로 내림한 정각 슬롯을 넘기면 끝(실제 시각 넣으면 멱등 깨짐 주의). **src_*_at은 이번 신설 아님** — 이은미 v3.6에서 cycle_id 폐기와 함께 도입, 이번엔 복사 값만 created_at→cycle_ts로 변경.

### 07-12 (2) — #19 카드 + row 예시 블록

- **#19 카드 신설 (✅ 정리 완료)** — cycle_ts·src_*_at 정본 가이드: 시각 3형제 역할(cycle_ts=슬롯 정체성 / created_at=기록 / src_*_at=참고문헌) · ⚠️ cycle_ts에 실제 시각 넣으면 멱등 깨짐(코드 리뷰 체크) · **"cycle 테이블 만들자" 방지선**(기각 근거 3 + 재논의 조건) · 장기 = 2차에 tb_job_run **로그**(부모 키 아님, UNIQUE(component, cycle_ts), #16 버전 기록도 여기에).
- **🧾 row 예시 블록 11곳** — 주요 테이블 하단에 파란 코드 블록(`.rowex` CSS 신설)으로 실제 row 형태 JSON 예시. 회색 전달 JSON과 시각적 구분. **NVDA 단일 시나리오(2026-07-10)로 전 테이블 연결** — 07 예시의 src_news_at=13:30Z가 뉴스 signal 예시의 cycle_ts와 같은 값 (눈 대조 가능). 대상: universe·daily_pick·technical·macro·disclosure·disclosure_signal·news·news_signal·strategist·verdict·review 2종.
- strategist created_at 설명 정합 수정 — "최신 판단 선택은 (ticker, cycle_ts DESC)".

### 07-12 (7) — v2.8: 0번 섹션 "무엇이 달라졌는가" (`#diff0` · 태그 v2.8)

- 문서 최상단(용어 사전 앞)에 **원본 Official_Document.html 대비 변경 표** — 9개 영역을 "원본에서는 → 지금은 → 왜 바꿨나 → 상세 위치" 형식으로. 원본만 아는 팀원(특히 김지현)의 갭 메우기용.
- .gitignore에 수동 백업 사본 패턴(`docs/Quantinue_통합설계서_v2_2*.html` 등) — 파일은 로컬 유지, 커밋만 제외 (사용자 지시).

### 07-12 (6) — v2.6 외부 편집 반입 (사용자 작업 · Claude 검증 후 커밋 `1b2faa3` · 태그 v2.6)

- **🔐 #security 보안 로드맵 신설** — LLM 특화 위협 7종(프롬프트 인젝션⭐·데이터 오염·출력 이상값·시크릿·DB 접근·오발주 킬스위치·감사 추적) + 1차/2차/실전 전 게이트 로드맵. 어필 문구: "LLM 출력이 검증 없이 돈에 닿는 경로 0개".
- **#20 착수 실행 전략 카드 (🔴 착수 전 결정 · 오픈)** — ①정본의 코드화(schema.sql·schemas.py/ontology.py·yaml 3파일 = 기계가 읽는 정본) ②리네임 한 PR → 담당별 PR 분할(계약 단일화 후) ③NVDA row 예시 → E2E 픽스처로 완주 강제 발화.
- payload 예시 signal_consensus 3→2 정합 수정. changelog 수정자 표기 = 문성혁(본인)으로 정리.
- ~~#20 채택 시 후속 수정 2건~~ → ✅ **v2.7에서 처리 (07-12 · 커밋 `86ccdaf` · 태그 v2.7)** — **#20 "확정 확인"** (⛔ 착수 시 필수 실행 — 지금 당장 개발 아님, **개발 시작일에 정본 3파일 PR부터**). #conv "한 PR" → #20 방식으로 대체(+"전원 교체 전 구 계약 코드 실행 금지"), 히어로에 착수 후 역할 분담(기계 계약=코드 3파일 / 본 문서=결정·이력 정본) 명시.

### 07-12 (5) — 📜 변경 이력 섹션 신설 (`#changelog`)

- 문서 맨 끝에 **버전별 변경 이력 표** (v2.0 최초 통합 ~ v2.5 현재, 날짜·수정자·요약). 히어로 배지 v2.0 → **v2.5 · 변경 이력 링크**, 목차 항목 추가.
- **편집 규칙 명문화**: 문서 수정 시 반드시 표 맨 위에 한 줄 추가(버전 +0.1) + 히어로 배지 갱신 — "줄이 없으면 그 수정은 없던 일". ⚠️ 이어서 작업하는 AI도 이 규칙을 따를 것.
- ~~근본 형상관리(git init)는 협의 사항~~ → ✅ **git 저장소 초기화 완료 (07-12)** — main 브랜치, 최초 커밋 `4fbd82f`(전체 문서 v2.5 상태) + **태그 `v2.5`**. `.gitignore`에 .DS_Store·.codegraph. **앞으로 규칙: #changelog에 한 줄 추가할 때마다 커밋 1번 + 태그(v2.6, v2.7…)** — 커밋 메시지는 changelog 요약을 그대로.

### 07-12 (4) — #19 카드 증보: 키 판단 규칙 4 확정

- **"src_*는 왜 tns_id를 안 보나" Q&A 행 추가** — 참조 2종 구분: 정체 참조=id(rep_news_id·signal_id) / 시점 참조=시각(src_*_at — 소비처가 값 자체: 신선도 뺄셈·눈 대조·시점 정렬. id면 전부 JOIN + macro 비대칭).
- **🧭 최종 권고 판단 규칙 4 (확정)** — A.물리 키=의미 없는 대리키 / B.값을 쓰는 참조=의미 있는 값 / C.시각 3역할 영구 분리 / D.마스터는 시각 외 정보 생길 때만(그때도 tb_job_run 로그). **뒤집기 트리거**: src_*→id는 2차 ORM 마찰 시만, cycle 마스터는 사이클이 메타를 가질 때만 — 그 전 재논의는 #19로 종결.

### 07-12 (3) — 비개발자 가독성 개선 5종 (전부 반영)

- **`#dict` 용어 30초 사전 섹션 신설** (목차 최상단) — DB(append·upsert·멱등·UNIQUE·ENUM·JSONB·TIMESTAMPTZ·마이그레이션)/개발(payload·픽스처·config·P0)/시장(페이퍼·브래킷·손절익절·ATR·RSI·스퀴즈·프리/애프터·8-K/10-Q/Form4·T+5) 3그룹 + row 예시 JSON 읽는 법 안내.
- **P0 개명** — "수익률 기준가" 뜻의 P0는 전부 **"기준가"**로 (s10 파이프·s11·릴레이 그림/표·decision_close·#16·row 예시). P0는 이제 우선순위(최우선) 뜻으로만. 용어 사전에 명시.
- **독자별 읽기 경로** — legend 섹션에 3경로 (비개발자/구현 개발자/회의 준비).
- **Mermaid 오프라인 안내** — 그림 3곳(흐름도·ERD·릴레이) 위에 "빈칸이면 인터넷 필요" 문구.
- **row 예시 색 수정** — 연파랑 배경에 흰 글자였던 버그 → 진한 남색 글자(#0a2540). 예시 11곳 컬럼 완전성 검산 완료(전부 스키마와 일치).
- **(07-12 추가) code 잘림 수정** — `overflow-wrap:anywhere`→`break-word`+`keep-all`: 테이블명(tb_universe 등)이 중간에 꺾이지 않음, 넓은 표는 twrap 가로 스크롤. **row 예시의 "…" 생략 값 7곳 전부 실제 값으로 채움**(summary·url·top_evidence — 컬럼이 빠진 것처럼 보이던 원인). s10 미설계 3테이블엔 "예시는 #15 확정 후" 안내.

## 8. 다음에 하면 좋은 것 (제안)

1. 협의 현황판 잔여 안건들 결론 나는 대로 🟢 전환 — 특히 신규 #11(snapshot 허용 오차 숫자)·#12(시각 계약 구현 체크).
2. 원샷 리네임(+규칙 8 키 변경) 실제 코드 반영 PR 추적 (담당별 체크리스트는 `#conv` 하단 경고 박스).
3. LLM 비용 추정표 — 운영 첫 주 usage 실측으로 교체.
4. 팀 공유: 컨벤션 섹션(`#conv`) 링크부터.
