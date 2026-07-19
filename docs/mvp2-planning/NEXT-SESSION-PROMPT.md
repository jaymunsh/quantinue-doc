# 다음 세션 시작 프롬프트

> `/clear` 또는 compact 후 아래 블록을 그대로 붙여넣으면 된다.
> 이 파일 자체도 세션 종료 시 갱신할 것. (최종 갱신 2026-07-19 — Phase 2 배관 완료)

---

```
Quantinue MVP-2 개발 이어서 진행. 나는 문성혁, app-v2/에서 2차 개발 중이다.

먼저 이 셋을 읽고 현재 상태를 파악해라:
1. docs/mvp2-planning/dev-handoff.md         ← 현재 상태·Phase별 커밋 대응표 (여기부터)
2. docs/mvp2-planning/pipeline-redesign.md   ← 실행 정본. 확정 결정 D1~D8 + Phase 1~5
3. docs/mvp2-planning/future-roadmap.md      ← 의도적으로 미룬 것 — 여기 있는 건 지금 안 만든다

작업 브랜치는 sunghyuk. Phase 1 완료 · Phase 2는 **배관까지 완료**.
남은 것은 Phase 2의 뉴스·공시 일괄 수집부터다.

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
  등록 순서가 계약이다: 유니버스 → 일봉 → 청산. 주기는 mvp2.jobs.cadences 소유
  (universe 7일, 나머지 1일). mvp2.jobs.enabled는 현재 false.

이어서 진행해라:
- Phase 2 잔여: 뉴스·공시 일괄 수집(종목별 폴링 → 그날 피드 통째로 받아 종목 매칭).
  착수 전 실사된 제약이 redesign §5에 박혀 있다 — 반드시 먼저 읽어라. 요약:
    · 역할 번호 주의: role_05 = 공시(SEC), role_06 = 뉴스(RSS). 뒤집혀 있다
    · tb_news·tb_disclosure가 (trade_date,ticker) → tb_daily_pick FK를 건다 →
      픽 밖 종목(탈락한 보유)에 행을 못 넣는다. 원시 원장 신설
      (tb_news_raw/tb_disclosure_raw) + 기존 테이블은 LLM 채점 결과로 역할 유지.
      tb_daily_bar와 같은 패턴
    · Google News는 전체 피드가 없다 — 뉴스 쪽 폴링 폐기는 구조적으로 막힐 수 있다.
      소스 교체 여부를 착수 시 판단할 것. 공시는 SEC 일일 인덱스로 1콜화된다
      (URL·형식은 문서 확인 후 확정 — 추정 금지)
    · ontology.EventType.delisting_halt는 소비자 0. 일괄 수집이 event_type을 채우고
      청산 관측이 하드 이벤트로 승격하면 소비자가 생긴다
    · exit_observations에 실제 버그: dict를 bars.items() 키로만 만들어 거래정지
      종목(봉이 안 찍힘)이 조용히 누락된다 — 정확히 delisting_halt 케이스다.
      하드 이벤트를 붙일 때 두 키 집합 union으로 재구조화할 것
- 그 다음 Phase 3 → 4 → 5는 redesign §6~8 그대로

진행 방식은 지금까지와 동일하게:
- TDD (실패 테스트 → 최소 구현 → green → 태스크 단위 1커밋)
- 테스트만 믿지 말고 실제로 돌려볼 것. 지난 세션에서 잡힌 결함 5건이 전부
  테스트를 통과한 뒤 실행에서 나왔다(앱이 안 뜸 · 유니버스 가림 · 배치 전멸 등).
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
  cd app-v2 && uv run pytest tests/unit tests/test_web.py -q   # 764 green 유지
  uv run ruff check src tests scripts
  통합(93 green)은 일회용 DB 전제 — 새 컨테이너에 db/schema.sql 적재 후 1회만.
  통합은 -p no:unraisableexception이 필요하다: asyncpg 연결 GC의 ResourceWarning이
  에러로 승격돼 15건이 가짜 실패로 뜬다(로직 실패 아님).
  포트 5481~5490은 이전 세션 컨테이너가 쓸 수 있으니 비어 있는 것을 골라라.
  통합 테스트는 전용 계좌(broker_account_id)를 쓸 것 — 기본 계좌
  quantinue-local-simulated는 다른 테스트가 현금 잔고를 정확히 단언하는 공용 자원

주의:
- app/(1차)은 다른 작업자 WIP — 절대 수정 금지. git stash도 쓰지 말 것
  (stash가 app/까지 삼킨다)
- 앱 실행 포트 8020, DB 5445
- .env에 QUANTINUE_CONTROL_ROOM_TOKEN이 없어 앱이 안 뜬다. 아무 로컬 값이나 한 줄
  넣으면 된다 — 사용자 파일이라 지난 세션에서 손대지 않았다
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
