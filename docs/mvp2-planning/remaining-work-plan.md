# 잔여 작업 계획 — Phase 5 이후 (2026-07-20)

> 결정: 문성혁 ("그 5개에 대해서 어떻게 해결할 것인지 계획 md 만들고 진행하자").
> 순서는 **B → C → A → D → E**. B가 가치 대비 비용이 가장 좋고, C는 B의 실
> 검증을 겸하며, A·D는 썩지 않는 부채라 뒤로, E는 신기능이라 맨 뒤다.
> 각 항목은 완료 시 이 파일에 체크와 커밋 해시를 남긴다.

## 진행 규칙 (기존과 동일)

- TDD · 태스크 단위 1커밋 · 문턱은 config 소유 · 유령 금지(소비자와 같은 커밋)
- 테스트는 고정하던 코드와 함께만 삭제, 대체 테스트 같은 커밋
- 실행 검증 필수 — 실행에서만 잡힌 결함 통산 17건
- 스키마 변경 시 4곳 미러 + 카탈로그 대조 + 마이그레이션 2회 멱등
- push 금지, app/(1차) 불간섭, DB 5445, 앱 8020(점유 시 8021)

---

## B. 판단 필드 채우기 — 분석 잡이 만든 것을 버리지 않는다

**문제.** `tb_strategist_signals`의 `bull_case`·`key_risk`·`risk_rebuttal`·
`counter_scenarios`·`persona_notes`·`src_*_at`이 전부 NULL이다. 구 role_07이
채우던 판단 서사를 새 분석 잡이 안 채운다. role_11 리뷰가 "왜 그렇게
판단했나"를 물을 재료가 사라지고 있다.

**조사로 확정된 제약 (2026-07-20).**
- LLM 구조화 출력(`AnalysisResult`)은 `score/label/reason` 셋뿐이다. bull_case·
  key_risk는 지금 `reason` 산문 안에 섞여 있다 → **출력 계약 확장이 필요하다.**
  이건 공짜가 아니다: 출력이 길어지면 max_tokens 512 실측이 흔들리고, 구조화
  출력 실패는 종목 단위 skip으로 이어진다(결함 통산 사례 있음).
- `src_disclosure_at`/`src_news_at`은 `tb_disclosure_signal`/`tb_news_signal`에
  FK가 걸려 있는데 **새 경로는 그 부모 테이블을 쓰지 않는다** → 채우려면
  부모 행을 지어내야 한다. **채우지 않는다** (지어내기 금지). 부활 여부는
  공시/뉴스 채점 잡이 생기는 날의 문제다.
- `src_macro_at`은 `tb_macro(as_of)` FK — 매크로 잡이 실제로 쓴 행이 있으므로
  **정직하게 채울 수 있다.** 단 `MacroSnapshot`에 `as_of`가 없어 확장 필요
  (소비자 = 이 lineage 기록, 같은 커밋).

**작업 순서.**
1. `StrategyOutput`/모델 출력에 `bull_case`·`key_risk` 필드 추가(짧은 문장 강제,
   프롬프트에 길이 제한 명시). 실패 허용 설계: 필드가 비어도 분석은 죽지 않는다
   — 서사는 부가물이지 판단의 전제가 아니다.
2. `StrategistSignalWrite`에 대응 필드 + `save_signal` 배선 + 스키마는 변경
   없음(컬럼은 이미 있다).
3. `MacroSnapshot.as_of` 추가 → `src_macro_at` 기록.
4. `risk_rebuttal`: 크리틱 **통과** 시 검증 요약을 되쓸지 검토 — 이미
   `tb_critic_verdict.objection`에 있으므로 중복이면 안 쓴다(관제실 조인으로
   충분). 조사 후 결정.
5. **실 LLM A/B 필수**: max_tokens 512에서 새 필드 포함 출력이 잘리는지,
   skip률이 0을 유지하는지. 잘리면 640 등으로 재실측(수치는 실측으로만).
6. 관제실 판단 패널에 bull_case·key_risk 노출(소비자 완성).

**완료 기준.** 실 LLM 한 바퀴에서 신규 필드가 원장에 앉고 skip 0 · 관제실에
표시 · 유닛/통합 green.

- [ ] 미착수

## C. `mvp2.jobs.enabled` 켜기 — 수동 스모크의 졸업

**문제.** 잡 9종이 전부 수동 실행으로만 검증됐다. 시스템이 "매일 알아서
도는" 상태가 된 적이 없다.

**작업 순서.**
1. 프리플라이트: dev DB의 `tb_job_run` 슬롯 상태 확인(오늘 슬롯이 이미
   succeeded면 루프는 "not due"만 판정할 것이다 — 그것도 검증이다).
2. `mvp2.jobs.enabled: true` + 앱 기동 → 루프가 실제로 돌고, 주기 판정이
   설계대로인지 로그로 확인(due/not-due 사유).
3. 관제실이 자동 실행분을 정확히 그리는지 확인.
4. 하루 뒤 슬롯(다음 거래일)은 사용자가 관찰 — 세션 안에서 검증 불가능한
   부분은 정직하게 남긴다.

**완료 기준.** enabled=true로 기동한 앱이 크래시 없이 주기 판정을 내리고,
그 판정 근거가 로그·관제실에서 읽힌다.

- [ ] 미착수

## A. 런 모양 저장소 정리 — 죽은 절반을 산 절반에서 뗀다

**문제.** 구 러너의 저장소(런 생명주기)가 잡이 쓰는 저장소와 같은 클래스에
산다. 호출자는 없지만, 남겨두면 모든 후속 작업이 "이거 살아 있나?"를 다시
묻는다.

**전수 확인된 대상.**
- `RunStore` 프로토콜: claim·wait_for_release·complete_stage·start_attempt·
  fail_attempt·finish_run·abandon·get_by_key·list_attempts·list_recent·
  list_active·latest_cycle_ts → 삭제. 남는 것: initialize·close·
  simulated_portfolio·record_completed_fill·reserve_daily_new_order·
  app_order_exposure_summary·reconcile_app_order_exposure (+postgres의 `.domain`).
- `db/postgres_run_reads.py`: 런 읽기는 죽고 `simulated_portfolio`·
  `app_order_exposure_summary`는 산다 → 산 것을 옮기고 파일 삭제.
- `db/postgres_lifecycle.py`·`db/active_snapshot.py`·`core/context_detail.py`·
  `core/terminal_detail.py`·`core/terminal_run_types.py`·
  `orchestration/lifecycle.py` → 소비자 재확인 후 삭제.
- `core/contracts.py`: importer 78개 — **분할**. 런 타입(PipelineRun·
  PipelineContext·Stage*)만 걷어내고 살아 있는 타입(PriceSnapshot·
  DisclosureSourceRecord 등)은 유지. 통째 삭제 금지.
- `pipeline_runs` 테이블: **남긴다.** 구 러너 역사 + 리뷰의 레거시 외부 조인이
  읽는다. 쓰는 코드가 없으니 자연히 동결된다.
- `db/memory.py`: 런 생명주기 구현 제거(테스트 스토어로서의 최소면만 유지).

**작업 순서.** 프로토콜부터 좁히고(컴파일러가 낙진을 알려준다) → 구현 삭제 →
산 것 이주 → core/contracts 분할 → 전 스위트 + 실 기동.

**완료 기준.** 유닛/통합 green · 실 기동 · `pipeline_runs`에 쓰는 코드 0 ·
런 타입 import 0.

- [ ] 미착수

## D. 이중 캘린더 통합 — 감시를 소유로

**문제.** `core/market_calendar`(잡 전부)와 `role_11_reviewer/calendar`(리뷰
경로)가 공존. 지금은 어긋남 감시 테스트가 지킨다.

**작업 순서.**
1. 리뷰 경로가 캘린더에 실제로 묻는 질문 목록화(T+5 산술·세션 마감 등).
2. 그 질문들을 `core/market_calendar`가 답할 수 있게 확장(없는 것만).
   ⚠️ XNYS 데이터 경계(2027-07) 밖 질문이 있는지 확인 — role_11 자체 규칙은
   무한하지만 exchange_calendars는 유한하다. 경계 밖 T+5가 필요해지는 날짜가
   오기 전이면 문제없고, 아니면 경계 처리를 명시적으로.
3. `api/reviews`·`api/review_runtime`·`role_11/processor`를 갈아태우고
   `UsEquityTradingCalendar` 삭제 — **감시 테스트도 같은 커밋에 삭제**
   (고정하던 코드가 죽는다), 대체는 core 캘린더의 기존 테스트.

**완료 기준.** 캘린더 구현 1개 · 리뷰 유닛/통합 green.

- [ ] 미착수

## E. 로드맵 R11 — 보도자료 와이어 RSS

**문제.** 뉴스가 전부 benzinga(gray 0.50)라 투표권이 없다. 와이어 3사
(businesswire·globenewswire·prnewswire)는 `allow`(0.95)로 문턱(0.55)을 넘는다.

**정직한 범위 설정.** "투표권"은 두 단계다:
- **1단계(이번 범위)**: 와이어 RSS 수집 → 티커 추출(`(NASDAQ: AAPL)` 패턴) →
  `tb_news_raw` 적재 → 분석 프롬프트 headlines에 출처 등급과 함께 도달.
  고신뢰 헤드라인이 증거 종합에 들어간다.
- **2단계(이번 범위 아님)**: `news_score` 실채점(LLM 채점 잡) — 그래야 07
  게이트의 투표가 산다. 채점 잡은 별도 결정(비용: 종목당 LLM 콜 증가).

**작업 순서.**
1. 와이어 RSS 실물 확인(피드 URL·필드·티커 패턴 실측 — 추정 금지).
2. 어댑터 + 티커 추출(오탐 처리: 패턴 밖 매칭 금지, 못 찾으면 버린다) → TDD.
3. 뉴스 잡의 소스를 다중화(Alpaca + 와이어), `(article_id, ticker)` 멱등은
   그대로 흡수.
4. 실 수집 스모크 → 원장 적재량·출처 분포 확인.

**완료 기준.** 실 피드에서 와이어 기사가 `tb_news_raw`에 앉고 분석 프롬프트에
등급과 함께 도달 · 유닛/통합 green.

- [ ] 미착수

---

## 세션 운영

- 컨텍스트 한계가 오면: 이 파일의 체크 상태 갱신 → dev-handoff 갱신 →
  NEXT-SESSION-PROMPT를 "이 계획의 다음 미완 항목부터"로 갱신.
- 항목 사이가 자연스러운 중단점이다. 항목 중간에서 끊기면 마지막 커밋이
  green인 상태를 유지할 것.
