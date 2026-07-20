# 다음 세션 시작 프롬프트

> `/clear` 또는 compact 후 아래 블록을 그대로 붙여넣으면 된다.
> 이 파일 자체도 세션 종료 시 갱신할 것.
> (최종 갱신 2026-07-20 심야 — **웹 W1 완료 · 디자인 시스템 도입 · 다음 계획은 `web-next-steps.md`**)

---

```
Quantinue MVP-2 개발 이어서 진행. 나는 문성혁, app-v2/에서 2차 개발 중이다.

먼저 이 셋을 읽어라. 1번이 이번 블록의 정본이고 나머지는 배경이다:

1. docs/mvp2-planning/web-next-steps.md   ← ★ 이번 세션의 정본.
     막는 작업 1건 · 웹 정리 항목 · W2~W4 순서 · 잡 관찰 2건 ·
     실행 환경(계정·포트·디자인 원본·마크업 계약)까지 전부 여기 있다.
2. docs/mvp2-planning/dev-handoff.md      ← 현재 상태와 완료 기록
3. docs/mvp2-planning/future-roadmap.md   ← 의도적으로 미룬 것. 여기 있는 건 지금 안 만든다

부수 정본: web-two-sides-plan.md(W1~W4 큰 틀 · 확정 결정 W-D1~D5) ·
open-items.md(웹과 무관한 열린 항목) · quantinue-integrated-design.html(설계 v6.0) ·
quantinue-engineering.html(개발기·결함 사전·코드 맵·ERD). 전부 코드 기준으로
다시 쓴 것이라 믿어도 된다.

========== 상태 ==========

**자동화도 웹 인증도 끝났다.** JOB 12종이 매일 자동으로 돌고(jobs.enabled=true),
로그인·세션·role 가드가 섰다. 화면 셋이 돈다:
  /login (관리자·유저 공용) · / (관제실, admin) · /me (내 계좌, user)

디자인 시스템도 도입됐다 — 네이비 사이드바 셸 + oklch 팔레트 +
Manrope/IBM Plex Mono(자체 호스팅) + metric/panel/badge/table.

기준선: 유닛/웹 558 green · 통합 106 green · ruff clean.
HEAD는 main. 2차 전체가 이미 병합·push됐다(608ed1d).

핵심 확정(되묻지 말 것):
- 체결은 로컬 시뮬(MockBroker) + 시세는 실물(Alpaca) — D1
- 무장 개념 소멸, mock이 최종 상태 — D2 · 주기는 config 소유, 기본 일 1회 — D3
- 정규장 전용(D4) · 동시 발동 시 손절 우선(D5) · 매도 = 별도 청산 행(D7)
- 계좌 평가 = 현금 + 보유수량 × 종가 — D8
- 1유저=1계좌 · 셀프 가입 없음 · 유저 화면 read-only(쓰기 엔드포인트 0)

JOB 등록 순서가 계약이다 (12종):
  유니버스 → 일봉 → 공시 → 뉴스 → 뉴스와이어 → 매크로 → 스크리닝 →
  인사이더채점 → 분석×성향2 → 청산 → 배분

========== 이번 세션에 할 것 ==========

**web-next-steps.md §6의 순서를 따라라.** 요약하면:

1. ⛔ **§1-1 유령 계좌 (막는 작업)** — 관제실에서 가장 큰 패널이 구 러너
   유물 계좌(quantinue-local-simulated, 체결 0건)를 보고 있어서, 실제로 움직인
   돈 전부(계좌 9개 · 체결 46건)가 화면에 안 보인다. 결함 15·16과 같은
   계열인데 방향이 반대다 — 많이 세는 게 아니라 있는 것을 안 센다.
   W3-2(계좌 총람)로 흡수하면 한 번에 끝난다.

2. **§5 잡 관찰 2건** — 코드보다 먼저 확인할 것.
   ① 07-21 슬롯에 잡이 3개만 돌았다(daily_bars가 주기상 돌았어야 하는데 안 돎
      → 픽 0 → 분석 0). 지난 세션에 앱을 여러 번 재시작해서 그 영향인지
      실제 결함인지 아직 못 가렸다.
   ② 잡 소요시간이 실행 시간이 아닐 수 있다(news가 9.8시간으로 찍혀 있는데
      실측은 14.5초). 재실행 시 started_at이 첫 시도 값으로 남는 것으로
      보이지만 확인 안 했다. 사실이면 관제실 소요시간이 거짓말을 한다.
   **둘 다 추정을 사실로 적지 말 것.** 확인 방법은 문서에 적어뒀다.

3. **W2 유저 포털** — /me가 지금 뼈대(계좌번호·성향·상태)뿐이다. 총자산·
   수익률·계좌 곡선·보유 카드를 붙인다. 소유권 검증이 W1 가드의 첫 실전
   시험이다. 완료 기준에 **화면 숫자와 원장 대조 테스트**가 있다 — §1-1이
   정확히 그 대조를 안 해서 생긴 문제다.

========== 진행 방식 (지금까지와 동일) ==========

- TDD(실패 테스트 → 최소 구현 → green → 태스크 단위 1커밋)
- 테스트만 믿지 말고 **실제로 돌려볼 것** — 실행에서만 잡힌 결함 통산 23건.
  화면은 앱을 띄워 HTTP로 확인하고, Chrome headless로 스크린샷을 찍어
  눈으로 봐라(--screenshot). 지난 블록에서만 3건이 그렇게 잡혔다.
- ⚠️ **화면을 확인할 때 포트를 먼저 확인해라.** 지난 세션에 옛 포트의 옛
  서버를 보며 "안 바뀐다"를 두 번 반복했다. 앱은 8020이고 --reload가 필수다
  (CSS가 import 시점에 인라인되므로 --reload 없이는 CSS를 고쳐도 안 바뀐다).
- 문턱·주기·한도는 config/pipeline.yaml 소유, 코드 리터럴 금지
- 유령 금지: 새 config 키·DB 컬럼·저장소 함수는 **소비자와 같은 커밋**
- 테스트 삭제 규칙: 고정하던 코드와 **함께만** 삭제, 대체 테스트 같은 커밋
- 스키마 바꾸면 4곳 미러(db/schema.sql · db/migrations/mvp2.sql ·
  tests/integration/schema_sql_expectations.py · 정본 HTML) + 카탈로그 대조
  + 마이그레이션 2회 멱등
- roles/ 등 핵심 코드에 한국어 '왜' 주석(docstring은 영어 한 줄)
- 화면 문구와 숫자는 **원장이 답할 수 있는 것만** 적는다. 지어낸 지표·없는
  자격·가짜 계정 수는 이 프로젝트가 두 번 잡은 결함이다(15·16).
- 비밀번호·세션 키는 절대 하드코딩·로그 금지. .env로.

검증:
  cd app-v2
  uv run pytest tests/unit tests/test_pipeline_dashboard.py -q   # → 558 green 기준
  uv run ruff check src tests scripts     # 파이프(| tail) 걸지 말 것 — 종료코드가 가려진다
  통합(106 green)은 일회용 DB — 새 컨테이너에 db/schema.sql 적재 후 1회만.
  -p no:unraisableexception 필요. 같은 컨테이너에서 두 번 못 돌린다
  (멱등 가드가 옛 행을 지켜내 실패를 가린다 — 실제로 밟은 함정).
  포트 5480~5498은 이전 세션 컨테이너가 쓸 수 있으니 빈 것을 골라라.

실 확인 환경 (상세는 web-next-steps.md §7):
  앱 8020 · DB 5445 · 계정 admin/quantinue-admin · user1~5/quantinue-user
  ⚠️ .env의 QUANTINUE_DATABASE_URL은 5444(1차 DB)를 가리킨다. 반드시 5445로
  덮어써라. 안 그러면 다른 작업자의 DB에 쓴다.
  ⚠️ tb_user에 행이 있어 관제실은 로그인을 요구한다(예고된 거동).

  LLM이 필요하면 QUANTINUE_LLM_MODE=local (oMLX 127.0.0.1:8888/v1,
  Qwen3.6-35B-A3B-OptiQ-4bit). max_tokens 기본 512. 성향 2종 한 바퀴 ≈ 15분.

  디자인 원본은 저장소 밖에 있다:
  ~/Desktop/quantinue-design/quantinue-admin-polish-files/
  DESIGN.md가 팔레트·타입·컴포넌트 정본이고, styles.css에 아직 안 옮긴
  컴포넌트(.chart · .allocation)가 W2에서 쓸 재료다. 단 그 폴더의 HTML에
  적힌 숫자·문구는 참고하지 말 것 — 암호화폐·원화·지어낸 지표다.

주의:
- app/(1차)은 다른 작업자 WIP — 절대 수정 금지. git stash 금지.
- .env를 .env.example로 덮어쓰지 말 것.
- Alpaca 키는 유효하다. 단 기동 중 앱은 옛 키를 메모리에 들고 있으므로
  키를 바꾸면 재시작할 것. 한도는 Basic 무료 데이터·트레이딩 각각 분당
  200요청이고 현재 피크는 백필 ~88/분이라 2.3배 여유. 레이트 리미터는
  일부러 안 넣었다.
- **push는 사용자 지시가 있을 때만.** (공유 저장소)

먼저 web-next-steps.md를 읽고, §1-1부터 시작할지 §5 관찰부터 할지 짚어줘.
```
