Quantinue MVP-2 개발 이어서 진행. 나는 문성혁, app-v2/에서 2차 개발 중이다.

먼저 이 다섯을 읽어라:

1. docs/operations-runbook.md              ← ★ 운영 정본. 켜고·보고·끄는 법
2. docs/mvp2-planning/dev-handoff.md       ← ★ 현재 상태 (맨 위 배너가 최신)
3. docs/mvp2-planning/intraday-realignment.md ← ★ 방향 재정렬(07-21 밤).
   개발 방향은 이 문서가 정본 — "하루 1회" 전제보다 우선한다
4. docs/mvp2-planning/completion-plan.md   ← 남은 것과 순서
5. docs/mvp2-planning/open-items.md        ← 열린 항목 정본

부수 정본: llm-usage-guide.md(LLM 사용처·실측) · aws-migration-review.md(보류
중, 착수 체크리스트) · how-to-check-guide.md(완성 확인 루틴) ·
quantinue-integrated-design.html(설계 v6.2) · quantinue-engineering.html(부록 v1.2).

========== 상태 ==========

**관측 인스턴스(8020)가 openai 모드로 돌고 있다** — gpt-5.4-mini, 출력 상한
4000, LLM 예산 배선 완료(⑨). 완성 조건 3개 중 2개 충족, 무인 완주는 카운트 중.

화면 여섯: /login · /admin(관제실) · /admin/schedule(운영 기준+용어집) ·
/admin/logs(작동 로그) · /admin/accounts(계좌 관리) · /me(내 계좌).
텔레그램 알림 4종: 실패 즉시 · 일일 안내(평일 KST 13:20) · 방어선 발동 ·
기동/슬롯 굳음(관측 인스턴스만, QUANTINUE_OPS_ALERTS).

장중 재정렬 **M1~M7 완료**. 1분 방어, 사건·정기 재판단, 장중 매수·매도,
관제실 장중 감시 카드와 보유 종목 웹소켓까지 코드에 있다. 무료 계정 실측은
30종목 성공·31번째 한도 오류였으므로, 보유 종목만 스트리밍하고 오늘의 픽은
1분 폴링하는 하이브리드다. 기본 설정은 계속 꺼짐이라 8020에는 아직 적용되지
않았다.

기준선: 유닛/웹 **629 green** · 통합 **112 green** · ruff clean.
HEAD는 main. **미푸시 커밋 있음** — push는 지시할 때만.

핵심 확정(되묻지 말 것): 이전 확정(D1~D8 · 1유저=1계좌 · 유저 쓰기 0 ·
잡 12종+조건부 1)에 더해 —
- ⚠️ **단, "하루 1회" 주기 전제는 재개봉됐다(07-21 밤 사용자 확정)** —
  intraday-realignment.md의 헌장 C1~C3와 층별 주기가 우선한다.
  R1(실브로커)은 폐기: 페이퍼(MockBroker)가 최종이다
- LLM은 openai 전환 완료. 모델 요율은 config/pipeline.yaml의
  budget.model_pricing 소유 — **미선언 모델로는 기동이 거부된다**(의도)
- 모든 LLM 콜은 BudgetedAnalyzer를 통과한다. 예산 초과 = 그 종목 skip
- 화면 시각은 KST, 원장은 UTC, 슬롯은 뉴욕 날짜 — 셋 다 불변
- AWS는 보류(사용자 결정). 착수 시 aws-migration-review.md §4-보류 순서대로

========== 이번 세션에 할 것 ==========

**1순위: openai 첫 슬롯 검증** (07-22 KST 13:00 이후면 결과가 있다)
- 텔레그램 ✅ 왔나 · /admin의 LLM 지출 카드에 ~$0.05 찍혔나 ·
  tb_llm_usage에 콜이 쌓였나 · 승인율이 로컬 때와 크게 다른가
- 구조화 출력 실패가 있었다면 retries 동작을 로그로 확인(§3-3 마지막 항목)
- 이상 없으면 open-items.md 3-3을 닫는다

**남은 후속 개발 (장중 재정렬 본편 밖):**

| | 항목 | 규모 |
|---|---|---|
| M7 | ✅ 보유 종목 웹소켓 + 전체 1분 폴링 | 30종목 한도 실측·하이브리드 구현 완료. 운영 활성화만 첫 슬롯 검증 뒤 결정 |
| W3-4 잔여 | 방어선 발동 내역(청산 사유를 관제실에) | 읽기, 가벼움 |
| ⑦ | W2-4 벤치마크(SPY) 잡 → /me에 SPY 대비 | 잡 1종 + 화면 |
| ⑧ | risk_rebuttal·counter_scenarios·persona_notes 채우기 | 프롬프트 계약 |
| ⑩ | pipeline_runs 계열 스키마 정리 | 관측 끝나면 |

계좌 정리 대기: DEMO-CONSERVATIVE-09(체결 0)는 지워도 안전.
TEST-DELISTED-01·TEST-SELLGAP은 체결 24건이 얽혀 있다 — 사용자에게 물을 것.

========== 진행 방식 (지금까지와 동일) ==========

- TDD(실패 테스트 → 최소 구현 → green → 태스크 단위 1커밋)
- 테스트만 믿지 말고 **실제로 돌려볼 것** — 실행에서만 잡힌 결함 통산 27건
  (27번째: 평가액 곡선이 -0.01%를 절벽으로 그림 — min/max 정규화).
  화면은 로그인 세션 curl로 받아 Chrome headless 렌더로 눈 확인.
- ⚠️ 포트: 관측 8020(건드리지 말 것) · 코드 작업 8021(**LLM_MODE=mock**으로 —
  openai로 띄우면 개발 반복이 비용 원장을 오염시킨다)
- ⚠️ **템플릿(.html)을 고쳤으면 관측 인스턴스를 그날 안에 재기동할 것.**
  Jinja는 파일 변경 시 즉시 다시 읽지만 파이썬·CSS는 재기동해야 반영된다 —
  템플릿이 새 필드를 참조하면 옛 코드로 도는 인스턴스에서 그 화면만 500이
  난다(실제로 밟았다, runbook §4-3).
- ⚠️ **관측 인스턴스 재기동은 (위 경우가 아니면) 몰아서 한 번에.** 재기동마다 텔레그램 기동
  알림이 가고, 잦으면 사용자가 장애로 오인한다(실제로 그랬다). 재기동 전
  running 잡 0 확인, 굳으면 관제실 잠금 해제.
- ⚠️ **테스트가 .env를 읽지 않게** — IsolatedSettings(env_file=None).
  이번 세션이 세 번째로 밟았다(출력 상한 변경에 계약 테스트 2건이 깨짐).
- 문턱·주기·한도·요율은 config/pipeline.yaml 소유, 코드 리터럴 금지
- 유령 금지 · 테스트 삭제 규칙 · 스키마 4곳 미러 + 멱등 2회 · 한국어 '왜' 주석
- 화면 문구와 숫자는 **원장이 답할 수 있는 것만** · 비밀은 .env로,
  커밋 전 ./scripts/scan_secrets.sh

검증:
  cd app-v2
  uv run pytest tests/unit tests/test_pipeline_dashboard.py tests/test_my_account.py tests/test_ops_log.py tests/test_schedule_page.py -q   # 629
  uv run ruff check src tests scripts     # 파이프(| tail) 걸지 말 것
  통합(110)은 **새 컨테이너**에서만 · -p no:unraisableexception 필수
  ⚠️ 5480은 다른 작업자 컨테이너가 점유 중일 수 있다 — 5490을 써라
  ⚠️ 스키마 붓기 전 `pg_isready` 대기 — 바로 부으면 error 6건이 난다(실측).
     명령 전문은 runbook §5

실 확인 환경:
  관측 앱 8020(openai) · 코드 작업용 8021(mock) · DB 5445
  계정 admin/quantinue-admin · user1~5/quantinue-user
  ⚠️ .env의 QUANTINUE_DATABASE_URL은 5444(남의 DB)를 가리킨다. 반드시 5445로.
  ⚠️ .env의 LLM_MODE는 mock이 정상 — 관측 스크립트가 openai로 덮어 기동한다.

주의:
- app/(1차)은 다른 작업자 WIP — 절대 수정 금지. git stash 금지.
- .env를 .env.example로 덮어쓰지 말 것 (openai 키가 들어 있다).
- **push는 사용자 지시가 있을 때만.** (공유 저장소)

먼저 위 다섯을 읽고, **openai 첫 슬롯이 정상이었는지부터 확인해서 짚어줘.**
정상이면 `open-items.md` 3-3을 닫고, 8020에 감시 층을 언제 켤지 사용자와
결정한다. M1~M6을 다시 만들지 말 것.
