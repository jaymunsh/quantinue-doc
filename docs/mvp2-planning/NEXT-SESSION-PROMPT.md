# 다음 세션 시작 프롬프트

> `/clear` 또는 compact 후 아래 블록을 그대로 붙여넣으면 된다.
> 이 파일 자체도 세션 종료 시 갱신할 것. (최종 갱신 2026-07-20 — **잔여 작업 5건(B·C·A·D·E) 전부 완료**)

---

```
Quantinue MVP-2 개발 이어서 진행. 나는 문성혁, app-v2/에서 2차 개발 중이다.

먼저 이 셋을 읽고 현재 상태를 파악해라:
1. docs/mvp2-planning/dev-handoff.md         ← 현재 상태·커밋 대응표 (여기부터)
2. docs/mvp2-planning/pipeline-redesign.md   ← 확정 결정 D1~D8 (Phase 1~5는 전부 완료)
3. docs/mvp2-planning/future-roadmap.md      ← 의도적으로 미룬 것 — 여기 있는 건 지금 안 만든다

작업 브랜치는 sunghyuk. **재설계는 끝났다** — Phase 1~5 전부 완료이고 구
11단계 러너는 2026-07-20에 삭제됐다. 시스템은 잡 9종으로 돈다.

핵심 확정(redesign §0, 되묻지 말 것):
- 체결은 로컬 시뮬(MockBroker) + 시세는 실물(Alpaca 마켓데이터) — D1
- 무장 개념 소멸 — mock이 최종 상태다 — D2
- 주기는 전부 config 소유, 기본 일 1회 — D3
- 정규장 전용(D4) · 손절·익절 동시 발동 시 손절 우선(D5) · 매도 = 별도 청산 행(D7)
- 계좌 평가 = 현금 + 보유수량 * 종가 — D8

잡 등록 순서가 계약이다:
  유니버스 → 일봉 → 공시 → 뉴스 → 매크로 → 스크리닝 → 분석×성향수 → 청산 → 배분

========== 상태: 잔여 작업 5건 완료 ==========

remaining-work-plan.md의 다섯 항목(B 판단 서사 · C jobs.enabled=true ·
A 런 저장소 삭제 · D 캘린더 통합 · E 와이어 RSS)이 전부 완료됐다.
각 항목의 실행 결과·판정은 그 파일에 있다.

⚠️ **최우선 — Alpaca 키 재발급 (사용자만 할 수 있다)**: .env의 키로 bars·news
둘 다 401. 재발급 전까지 daily_bars·news 잡이 매일 failed로 기록된다(잡 격리
확인됨). 재발급 후 .env 갱신 → 다음 틱에서 자동 회복된다(실패 슬롯은 같은 날
재예약 가능).

이번 세션 후보 (정해진 순서 없음 — 사용자에게 물을 것):
- R11 2단계: news_score 실채점 잡 → 뉴스가 07 게이트에서 실제로 투표.
  비용: 종목당 LLM 콜 증가. 와이어 allow 행이 이미 원장에 쌓이고 있다.
- 판단 필드 나머지: tb_disclosure(_signal)·tb_news(_signal) 채점 경로 부활
  여부(src_disclosure_at/news_at 계보의 전제).
- 로드맵 R1(페이퍼 전환): 전제 = role_10 거래정지 가드 재구현.
- 대시보드 개선: 실 사용 피드백 기반.

========== (아래는 기록 — 이전 세션에서 고른 다섯) ==========

**A. 런 모양 저장소 정리 (규모 큼, 기술 부채)**
   구 러너는 죽었는데 그 저장소가 휴면 상태로 남아 있다: RunStore 프로토콜의
   런 생명주기 절반 · db/postgres_run_reads.py · postgres_lifecycle.py ·
   active_snapshot.py · core/contracts.py의 런 타입 · context_detail ·
   terminal_detail · terminal_run_types · orchestration/lifecycle.py.
   ⚠️ 통째 삭제 불가 — core/contracts.py는 importer가 78개이고 브로커 등
   살아 있는 코드가 같은 모듈을 쓴다. **분할**이 필요하다. 그리고 잡이 실제로
   쓰는 것(simulated_portfolio·record_completed_fill·reserve_daily_new_order·
   app_order_exposure_summary·.domain)이 같은 클래스에 살아서, 가르려면 모든
   잡이 타는 스토어를 수술해야 한다. 지금은 호출자가 없어 무해하다.

**B. 판단 필드를 채운다 (기능, 아마 가장 값어치 있음)**
   tb_strategist_signals의 bull_case·key_risk·risk_rebuttal·counter_scenarios·
   persona_notes가 전부 비어 있다. 구 role_07이 채우던 것을 새 분석 잡이 안
   채운다. **프롬프트는 이미 그 내용을 만들고 있다** — 계약과 저장만 이으면
   관제실이 "왜 그렇게 판단했나"를 문장으로 보여줄 수 있다.
   src_{disclosure,news,macro}_at(계보)도 같은 성격이다.

**C. mvp2.jobs.enabled 를 켤지 결정**
   여전히 false. 켜면 9종 잡이 매일 자동으로 돈다. 운영 결정이라 임의로
   켜지 않았다. 켜기 전에 하루치를 수동으로 한 바퀴 돌려볼 것.

**D. 이중 캘린더 통합**
   core/market_calendar(잡 전부) vs role_11_reviewer/calendar(리뷰 경로).
   자연소멸하지 않았다 — api/reviews·api/review_runtime이 직접 쓴다.
   지금은 tests/unit/test_calendar_agreement.py가 어긋남을 감시한다.
   통합은 리뷰 날짜 산술을 갈아끼우는 일이라 신중히.

**E. 로드맵(future-roadmap.md)에서 하나 착수**
   R11(보도자료 와이어 RSS — 뉴스에 투표권이 생긴다)이 비용 대비 효과가 좋고,
   R1(페이퍼 전환)은 role_10의 거래정지 가드 재구현이 전제다(Phase 5에서
   서비스와 함께 죽었다 — 로컬 시뮬에선 무해).

→ **사용자에게 어느 것부터 할지 물어라.** 전부 성격이 달라서 임의로 고를 일이
   아니다. 다만 물으면서 네 판단(추천 하나)을 같이 말할 것.

========== 진행 방식 (지금까지와 동일) ==========

- TDD(실패 테스트 → 최소 구현 → green → 태스크 단위 1커밋)
- 테스트만 믿지 말고 실제로 돌려볼 것 — 실행에서만 잡힌 결함 통산 **17건**.
  대시보드는 앱을 띄워 HTTP로 확인해라(포트 8020, 관제실은 GET /).
- 문턱·주기·한도는 config/pipeline.yaml 소유, 코드 리터럴 금지
- 유령 금지: 새 config 키·DB 컬럼은 소비자와 같은 커밋
- 테스트 삭제 규칙: 고정하던 코드와 **함께만** 삭제, 대체 테스트 같은 커밋
- 스키마 바꾸면 4곳 미러(db/schema.sql · db/migrations/mvp2.sql ·
  tests/integration/schema_sql_expectations.py · 정본 HTML) + 카탈로그 대조
  + 마이그레이션 2회 멱등
- roles/ 코드에 한국어 '왜' 주석(docstring은 영어 한 줄)

검증:
  cd app-v2 && uv run pytest tests/unit tests/test_web.py -q   # ⚠️ test_web.py는
    Phase 5에서 삭제됐다. 지금은: uv run pytest tests/unit tests/test_pipeline_dashboard.py -q
    → **497 green** 기준
  uv run ruff check src tests scripts     # 파이프(| tail) 걸지 말 것 — 종료코드가 가려진다
  통합(**102 green**)은 일회용 DB — 새 컨테이너에 db/schema.sql 적재 후 1회만.
  -p no:unraisableexception 필요. 같은 컨테이너에서 두 번 못 돌린다.
  포트 5481~5498은 이전 세션 컨테이너가 쓸 수 있으니 빈 것을 골라라.

실 스모크 방법:
  build_job_runner를 실 Settings + JobSources(market_data=…, macro=…,
  analyzer=…)로 세우고 잡을 직접 run(as_of) 한다. reserve_job_run +
  finish_job_run(succeeded=True)을 함께 부를 것. 슬롯 재측정 전 비우기 SQL은
  dev-handoff 참조.

  ⚠️ .env의 QUANTINUE_DATABASE_URL은 5444(1차 DB)를 가리킨다. 반드시 5445로
  덮어써라. 안 그러면 다른 작업자의 DB에 쓴다.

  환경: QUANTINUE_DATA_MODE=public · QUANTINUE_DATABASE_MODE=postgres ·
  URL은 5445. dev DB에는 실 봉 53만 + 뉴스 1440행 + 07-19·07-20 잡 실행 기록 +
  실 포지션(계좌 10개) + equity 스냅샷이 들어 있다.

  LLM 검증은 QUANTINUE_LLM_MODE=local (oMLX 127.0.0.1:8888/v1,
  Qwen3.6-35B-A3B-OptiQ-4bit). max_tokens 기본 512(실측 근거).
  성향 2종 한 바퀴 ≈ 12분. mock 분석기는 고정 점수라 성향 격차 검증 불가.

주의:
- app/(1차)은 다른 작업자 WIP — 절대 수정 금지. git stash 금지.
- 앱 포트 8020(이전 세션 서버가 잡고 있을 수 있다 — 남의 프로세스를 죽이지
  말고 다른 포트를 쓸 것). DB 5445. .env를 .env.example로 덮어쓰지 말 것(Alpaca 키)
- Alpaca 분당 한도는 여전히 미확인 — 추정해 박지 말 것
- push 금지(공유 저장소). 커밋만 쌓을 것

계획 세우고 시작 전에 한 번 짚어줘.
```
