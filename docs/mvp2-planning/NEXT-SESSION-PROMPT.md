# 다음 세션 시작 프롬프트

> `/clear` 또는 compact 후 아래 블록을 그대로 붙여넣으면 된다.
> 이 파일 자체도 세션 종료 시 갱신할 것. (최종 갱신 2026-07-19 — Phase 2 공시까지 완료)

---

```
Quantinue MVP-2 개발 이어서 진행. 나는 문성혁, app-v2/에서 2차 개발 중이다.

먼저 이 셋을 읽고 현재 상태를 파악해라:
1. docs/mvp2-planning/dev-handoff.md         ← 현재 상태·Phase별 커밋 대응표 (여기부터)
2. docs/mvp2-planning/pipeline-redesign.md   ← 실행 정본. 확정 결정 D1~D8 + Phase 1~5
3. docs/mvp2-planning/future-roadmap.md      ← 의도적으로 미룬 것 — 여기 있는 건 지금 안 만든다

작업 브랜치는 sunghyuk. Phase 1 완료 · Phase 2는 **뉴스만 남았다**.
남은 뉴스 일괄 수집은 소비자가 Phase 3라 Phase 3와 함께 착수한다.

핵심 확정(redesign §0, 되묻지 말 것):
- 체결은 로컬 시뮬(MockBroker 승격) + 시세는 실물(Alpaca 마켓데이터) — D1
- 무장(BROKER_MODE=alpaca) 개념 소멸 — mock이 최종 상태다 — D2
- 주기는 전부 config 소유, 기본 일 1회. 아키텍처는 실시간형 유지 — D3
- 정규장 전용(D4) · 손절·익절 동시 발동 시 손절 우선(D5) · 점진 교체(D6)
- 매도 = 별도 청산 행(order_type='close'+closes_order_id) + 자기 sell 시그널 — D7
- 계좌 평가 = 현금 + 보유수량 * 종가 — D8

이미 끝난 것(다시 만들지 말 것):
- Phase 1 전체 — 장부 바닥 6곳 · 시뮬 체결 엔진 · 청산 3층 잡
- Phase 2 데이터층 — tb_daily_bar 원장 · Alpaca 배치 일봉 어댑터 · 계좌 시가평가
- Phase 2 배관(계획에 없던 선행 항목) — Phase 1c가 청산 잡 클래스만 만들고
  스케줄러 탑재를 안 해서 수집기·청산 잡이 전부 고아였다. 잡 러너를 세워 연결했다.
  Phase 3~5의 모든 잡이 여기 올라탄다:
    tb_job_run 원장 + is_job_due(7d77e6a) · JobRunner + mvp2.jobs config(c6776cc) ·
    일봉 수집 잡 + 청산 잡 마운트(1a79c41) · mock+거래활성 기동 가능(acbe36f) ·
    유니버스 주간 잡(eef0857) · Alpaca 심볼 강건화(bff6166)
- Phase 2 공시 — SEC 일일 인덱스 일괄 수집 + 하드 이벤트 → 청산 관측(7563def).
  종목당 1콜을 그날 1콜로 바꿨고 ontology.delisting_halt에 소비자가 처음 생겼다.
  함께 고친 것: 봉이 안 찍히는 거래정지 종목이 청산 관측에서 누락되던 버그.
- 청산 버그 수정(4a2f46a) — 한 계좌·같은 종목에 열린 포지션이 둘이면 하나만
  청산되고 나머지가 UNIQUE(account_id,signal_id)로 죽던 것.

  등록 순서가 계약이다: 유니버스 → 일봉 → 공시 → 청산. 주기는
  mvp2.jobs.cadences 소유(universe 7일, 나머지 1일). mvp2.jobs.enabled는 현재
  false. 실 스모크로 잡 4개 완주 확인: universe 2000 · daily_bars 1998/2013 ·
  disclosures 1738건(하드 2) · exits 4/12 청산.

이어서 진행해라 — **Phase 3(분석 잡)에 뉴스 일괄 수집을 얹어서**:

- 뉴스 일괄 수집: 소스는 확정됐고 구현만 남았다. Google News는 전체 피드가
  없어 막혔지만 **Alpaca 뉴스 API로 해결된다**(2026-07-19 실 API 확인):
    GET https://data.alpaca.markets/v1beta1/news
    start/end RFC3339 · limit · next_page_token 페이지네이션
    심볼 미지정이면 전 시장이 오고 기사마다 symbols 배열이 붙는다
    우리가 이미 쓰는 자격증명 그대로 200 · 소스는 benzinga
  tb_news_raw는 tb_disclosure_raw와 같은 패턴(FK 없음 — tb_news는
  (trade_date,ticker) → tb_daily_pick FK 때문에 픽 밖 종목을 못 담는다).
  **소비자가 Phase 3 분석 잡이므로 원장·어댑터·잡을 그 소비자와 같은 커밋에
  넣을 것**(유령 금지). 하드 이벤트는 뉴스가 아니라 SEC 폼이 판정한다 —
  뉴스 헤드라인 키워드로 매도를 발동시키지 말 것.
- Phase 3 본체는 redesign §6 그대로: 스크리닝 잡(tb_daily_bar 기반 DB 랭킹,
  전 유니버스) → 상위 screening.llm_depth(20) ∪ 보유 → 분석 잡(종목별 LLM
  2~3콜) · role_07 sell 개방 + 보유 맥락 + 성향 페르소나 2종 · role_08 매도 검증.
  ⚠️ 역할 번호 주의: role_05 = 공시, role_06 = 뉴스. 뒤집혀 있다.
  ⚠️ ensure_holding_in_scope는 Phase 3 스크리닝 잡이 넘겨받을 임시 소유다 —
  "상위 N ∪ 보유"가 구현되면 거기서 사라진다.
- 그 다음 Phase 4 → 5는 redesign §7~8 그대로

진행 방식은 지금까지와 동일하게:
- TDD (실패 테스트 → 최소 구현 → green → 태스크 단위 1커밋)
- 테스트만 믿지 말고 실제로 돌려볼 것. 지난 세션에서 잡힌 결함 **7건이 전부**
  테스트를 통과한 뒤 실행에서 나왔다(앱이 안 뜸 · 유니버스 가림 · 배치 전멸 ·
  거래정지 종목 청산 누락 · 같은 종목 두 포지션 중 하나만 청산 등).
  잡을 만들면 실 config·실 DB·실 API로 한 틱 돌려봐라
- 문턱·주기·한도는 전부 config/pipeline.yaml 소유, 코드 리터럴 금지
- 유령 금지: 새 config 키·DB 컬럼은 소비자와 같은 커밋에
- 스키마를 바꾸면 4곳 전부: db/schema.sql · db/migrations/mvp2.sql ·
  tests/integration/schema_sql_expectations.py(TABLES·PK·CHECKS 전부) ·
  정본 HTML(docs/quantinue-integrated-design.html 스키마 섹션).
  그리고 '신규 설치 == 마이그레이션' 카탈로그 대조 + 마이그레이션 2회 멱등 확인
- 테스트는 고정하는 코드와 함께만 삭제, 대체 테스트 같은 커밋
- 핵심·에이전트(roles/) 코드에는 '왜'를 설명하는 한국어 인라인 주석
  (docstring은 영어 한 줄 — 기존 관행)

검증:
  cd app-v2 && uv run pytest tests/unit tests/test_web.py -q   # 782 green 유지
  uv run ruff check src tests scripts
  통합(100 green)은 일회용 DB 전제 — 새 컨테이너에 db/schema.sql 적재 후 1회만.
  통합은 -p no:unraisableexception이 필요하다: asyncpg 연결 GC의 ResourceWarning이
  에러로 승격돼 15건이 가짜 실패로 뜬다(로직 실패 아님).
  포트 5481~5497은 이전 세션 컨테이너가 쓸 수 있으니 비어 있는 것을 골라라.
  통합 테스트는 전용 계좌(broker_account_id)를 쓸 것 — 기본 계좌
  quantinue-local-simulated는 다른 테스트가 현금 잔고를 정확히 단언하는 공용 자원

주의:
- app/(1차)은 다른 작업자 WIP — 절대 수정 금지. git stash도 쓰지 말 것
  (stash가 app/까지 삼킨다)
- 앱 실행 포트 8020, DB 5445
- .env는 이미 정리됐다(QUANTINUE_CONTROL_ROOM_TOKEN 추가 완료, 앱 기동 확인).
  .env를 .env.example로 덮어쓰지 말 것 — Alpaca 키가 날아간다
- Alpaca 분당 호출 한도는 여전히 미확인. 추정해서 박지 말 것.
  (배치 400종목까지 OK · 클래스 구분자는 점(BRK.B) · 미지 심볼 1개가 배치 전체를
  죽인다 — 전부 실측으로 확인됐고 어댑터에 반영돼 있다)
- 재설계 결정 D1~D8·계좌 금액·매도 주문 표현은 확정됨 — 되묻지 말 것
- Phase 4 구 러너 삭제는 동등성 증거 보고 + 내 확인 후에만 — 유일한 확인 지점
- push 금지(공유 저장소). 커밋만 쌓을 것
- 끝까지 자율 진행할 것. 심각한 문제가 없으면 중간에 멈추지 말고,
  컨텍스트 한계가 오면 문서 갱신하고 다음 세션 프롬프트를 만들어라

계획 세우고 시작 전에 한 번 짚어줘.
```
