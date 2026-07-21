Quantinue MVP-2 개발 이어서 진행. 나는 문성혁, app-v2/에서 2차 개발 중이다.

먼저 이 넷을 읽어라:

1. docs/operations-runbook.md              ← ★ 운영 정본. 켜고·보고·끄는 법
2. docs/mvp2-planning/completion-plan.md   ← ★ 남은 것과 순서, 그 근거
3. docs/mvp2-planning/dev-handoff.md       ← 현재 상태와 완료 기록
4. docs/mvp2-planning/open-items.md        ← 열린 항목 정본

부수 정본: web-next-steps.md · future-roadmap.md(의도적으로 미룬 것 — 여기
있는 건 지금 안 만든다) · quantinue-integrated-design.html(설계 v6.0) ·
quantinue-engineering.html(개발기·결함 사전·코드 맵·ERD).

========== 상태 ==========

**웹 두 면이 다 섰다.** 완성 조건 3개 중 2개 충족:
  ✅ 웹 두 면이 원장을 정직하게 보여준다
  ✅ 관리자가 화면에서 계좌를 운영한다 (개설·성향·정지)
  ⏳ 사람 없이 하루를 완주한 기록 — 2026-07-21부터 카운트 시작

화면 넷: /login · /admin(관제실) · /admin/accounts(계좌 관리) · /me(내 계좌)
`/`는 역할별 갈림길이다. 텔레그램 알림이 붙어 있다(실패 즉시 + 평일 13:20 일일 안내).

기준선: 유닛/웹 **572 green** · 통합 **107 green** · ruff clean.
HEAD는 main. **origin/main 대비 31커밋이 로컬에만 있다** — push는 지시할 때만.

핵심 확정(되묻지 말 것):
- 체결은 로컬 시뮬(MockBroker) + 시세는 실물(Alpaca) — D1
- 무장 개념 소멸, mock이 최종 상태 — D2 · 주기는 config 소유, 기본 일 1회 — D3
- 정규장 전용(D4) · 동시 발동 시 손절 우선(D5) · 매도 = 별도 청산 행(D7)
- 계좌 평가 = 현금 + 보유수량 × 종가 — D8
- 1유저=1계좌 · 셀프 가입 없음 · 유저 화면 read-only(쓰기 엔드포인트 0)
- 관리자의 쓰기는 계좌 CRUD와 슬롯 잠금 해제 둘뿐이다

잡 등록 순서가 계약이다 (12종 + 조건부 1):
  유니버스 → 일봉 → 공시 → 뉴스 → 뉴스와이어 → 매크로 → 스크리닝 →
  인사이더채점 → 분석×성향2 → 청산 → 배분 → [일일 안내(텔레그램 키 있을 때만)]

========== 이번 세션에 할 것 ==========

**주제는 AWS 이전 검토다.** 지금은 맥 한 대에서 사람이 켜고 끄는 운용이다.

⚠️ **먼저 이 의존 사슬을 확인하고 시작해라. 여기서 판단이 갈린다:**

AWS로 옮기면 **로컬 LLM(oMLX/Qwen3.6-35B)을 못 쓴다.** 그건 맥 전용이다.
→ openai 모드 전환이 사실상 강제된다
→ 그러면 호출당 **비용이 생긴다**
→ `open-items.md` §3-1(LLM 예산 배선 = `tb_llm_usage` + `budget.daily_llm_usd`)이
  **선택이 아니라 선행 필수**가 된다. 지금 소비자 0인 테이블이다.
→ 함께 §3-3(openai에서 retries 동작 · 성향 격차 재확인)도 딸려 온다

즉 "AWS 이전"은 인프라 작업이 아니라 **LLM 비용 통제 작업이 앞에 붙은
작업**이다. 이걸 안 짚고 옮기면 예산 없이 유료 API가 매일 도는 상태가 된다.

**검토해야 할 것 (구현 전에 판단부터):**
1. **무엇이 해결되나** — 24시간 가동(무인 완주 조건이 자동 충족) · 맥 꺼짐
   사각지대 소멸 · 알림 신뢰도 상승
2. **무엇이 새로 필요한가** — LLM 예산 배선(위) · 비밀 관리(지금은 .env) ·
   DB(RDS vs 컨테이너) · 배포 방식 · Alpaca 키가 다른 IP에서 도는 것
3. **무엇을 잃나** — 로컬 LLM의 무료 추론 · 지금의 단순함(docker 하나 + 앱 하나)
4. **정말 지금인가** — 무인 완주 관측이 아직 며칠 안 됐다. 로컬에서 며칠
   돌려 실패 패턴을 본 뒤 옮기는 편이 나을 수 있다. 이것도 결론에 넣어라.

**결론을 문서로 남겨라** — `docs/mvp2-planning/aws-migration-review.md`.
얻는 것/치르는 것/선행 조건/권고 순서. `future-roadmap.md`의 형식을 따르면 된다.

========== 남은 개발 (AWS와 별개) ==========

| | 항목 | 규모 |
|---|---|---|
| W3-4 잔여 | 방어선 발동 내역(청산 사유를 관제실에) | 읽기, 가벼움 |
| ⑦ | W2-4 벤치마크(SPY) 잡 → /me에 SPY 대비 | 잡 1종 + 화면 |
| ⑧ | risk_rebuttal·counter_scenarios·persona_notes 채우기 | 프롬프트 계약 |
| ⑨ | LLM 예산 배선 | **AWS 가면 선행 필수** |
| ⑩ | pipeline_runs 계열 스키마 정리 | 관측 끝나면 |

========== 진행 방식 (지금까지와 동일) ==========

- TDD(실패 테스트 → 최소 구현 → green → 태스크 단위 1커밋)
- 테스트만 믿지 말고 **실제로 돌려볼 것** — 실행에서만 잡힌 결함 통산 26건.
  지난 세션에서만 3건(재시도 슬롯의 started_at · 유령 계좌를 앱이 매 기동마다
  재생성 · /admin 이동 후 슬롯 링크가 안 따라감)이 그렇게 잡혔다.
  화면은 HTTP로 확인하고 Chrome headless로 스크린샷을 찍어 눈으로 봐라.
- ⚠️ **화면 확인 전에 포트를 먼저 확인해라.** 관측 인스턴스는 8020이고
  코드 작업용은 8021로 띄운다(runbook §5). --reload는 작업용에만.
- ⚠️ **관측 인스턴스를 함부로 재시작하지 마라.** 잡 도중에 끄면 슬롯이
  running으로 굳는다. 굳었으면 관제실의 `잠금 해제` 버튼으로 푼다.
- 문턱·주기·한도는 config/pipeline.yaml 소유, 코드 리터럴 금지
- 유령 금지: 새 config 키·DB 컬럼·저장소 함수는 **소비자와 같은 커밋**
- 테스트 삭제 규칙: 고정하던 코드와 **함께만** 삭제, 대체 테스트 같은 커밋
- ⚠️ **테스트가 개발자의 .env를 읽지 않게 하라** — 실제로 두 번 밟았다.
  `IsolatedSettings`(env_file=None) 패턴을 쓴다
- 스키마 바꾸면 4곳 미러 + 카탈로그 대조 + 마이그레이션 2회 멱등
- roles/ 등 핵심 코드에 한국어 '왜' 주석(docstring은 영어 한 줄)
- 화면 문구와 숫자는 **원장이 답할 수 있는 것만**
- 비밀번호·세션 키·토큰은 절대 하드코딩·로그 금지. .env로.
  커밋 전 `./scripts/scan_secrets.sh`

검증:
  cd app-v2
  uv run pytest tests/unit tests/test_pipeline_dashboard.py tests/test_my_account.py -q
  uv run ruff check src tests scripts     # 파이프(| tail) 걸지 말 것
  통합(107)은 일회용 DB · -p no:unraisableexception 필수 (runbook §5에 명령 있음)

실 확인 환경:
  관측 앱 8020 · 코드 작업용 8021 · DB 5445
  계정 admin/quantinue-admin · user1~5/quantinue-user
  ⚠️ .env의 QUANTINUE_DATABASE_URL은 5444(1차 DB)를 가리킨다. 반드시 5445로.
  LLM local은 oMLX 127.0.0.1:8888 필요. 성향 2종 한 바퀴 ≈ 15분.

주의:
- app/(1차)은 다른 작업자 WIP — 절대 수정 금지. git stash 금지.
- .env를 .env.example로 덮어쓰지 말 것.
- **push는 사용자 지시가 있을 때만.** (공유 저장소, 현재 31커밋 미푸시)

먼저 위 넷을 읽고, **AWS 이전이 지금 맞는지부터 판단해서 짚어줘.**
