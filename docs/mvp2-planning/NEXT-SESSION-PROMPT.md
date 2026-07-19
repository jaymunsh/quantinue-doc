# 다음 세션 시작 프롬프트

> `/clear` 또는 compact 후 아래 블록을 그대로 붙여넣으면 된다.
> 이 파일 자체도 세션 종료 시 갱신할 것. (최종 갱신 2026-07-19 후반 — Phase 3 4/7 완료)

---

```
Quantinue MVP-2 개발 이어서 진행. 나는 문성혁, app-v2/에서 2차 개발 중이다.

먼저 이 셋을 읽고 현재 상태를 파악해라:
1. docs/mvp2-planning/dev-handoff.md         ← 현재 상태·커밋 대응표 (여기부터)
2. docs/mvp2-planning/pipeline-redesign.md   ← 실행 정본. 확정 결정 D1~D8 + Phase 1~5
3. docs/mvp2-planning/future-roadmap.md      ← 의도적으로 미룬 것 — 여기 있는 건 지금 안 만든다

작업 브랜치는 sunghyuk. Phase 1·2 완료 · Phase 3는 4/7 완료.

핵심 확정(redesign §0, 되묻지 말 것):
- 체결은 로컬 시뮬(MockBroker 승격) + 시세는 실물(Alpaca 마켓데이터) — D1
- 무장(BROKER_MODE=alpaca) 개념 소멸 — mock이 최종 상태다 — D2
- 주기는 전부 config 소유, 기본 일 1회. 아키텍처는 실시간형 유지 — D3
- 정규장 전용(D4) · 손절·익절 동시 발동 시 손절 우선(D5) · 점진 교체(D6)
- 매도 = 별도 청산 행(order_type='close'+closes_order_id) + 자기 sell 시그널 — D7
- 계좌 평가 = 현금 + 보유수량 * 종가 — D8

이미 끝난 것(다시 만들지 말 것):
- Phase 1 전체 · Phase 2 데이터층·배관·공시
- Phase 3: 성향 정합(2198467) · 일봉 백필(2c8f7b2) · 스크리닝 잡(724ddc2) ·
  분석 잡(b361772) · 상장폐지 보유 이월(29a2e00) ·
  제약 이름 드리프트 제거(6759083) · 성향 페르소나 2종(7dafdc5 + 57d49c3)

잡 등록 순서가 계약이다:
  유니버스 → 일봉 → 공시 → [뉴스] → 스크리닝 → 분석×성향수 → 청산

========== 이어서 진행할 것 — Phase 3 잔여 3개 ==========

--- 1. 뉴스 일괄 수집 ← 여기부터 ---

⚠️ 착수 전 dev-handoff의 "출처 등급 함정" 절을 반드시 읽어라. 요약:

Alpaca 뉴스는 모든 기사 url이 benzinga.com인데(실측), news_trust_policy.yaml에서
benzinga는 gray = 0.50이고 gates.source_trust_min은 0.55다. 그대로 만들면
role_07/contracts.py:165가 뉴스 투표를 통째로 박탈한다 — 값비싼 유령이 된다.

확정 방향: 뉴스는 별도 투표가 아니라 증거 종합의 **맥락**으로 넣는다.
소비 지점이 이미 뚫려 있다 — analysis_prompt(subject, holding, filings,
headlines=()) (roles/analysis/contracts.py:52,85). 아무도 안 채우고 있다.
정책·게이트를 데이터 소스 편의로 흔들지 말 것(benzinga를 allow로 올리거나
문턱을 0.50으로 내리는 것은 정책 오염이다).
news_score는 계속 None이고, job.py의 해당 주석을 그 이유로 바꾼다.

구현:
- tb_news_raw 신설 — tb_disclosure_raw와 같은 패턴(FK 없음). 스키마 4곳 미러.
- 어댑터는 market_data/alpaca_bars.py를, 잡·테스트는 sec_daily_index.py +
  build_disclosures_job을 그대로 미러.
  GET https://data.alpaca.markets/v1beta1/news · start/end RFC3339 · limit ·
  next_page_token · 심볼 미지정이면 전 시장 · 기사마다 symbols 배열
  · 응답 실물: id(정수 PK) · headline · summary · created_at/updated_at · url · source
- 소비자(분석 잡의 headlines 연결)와 같은 커밋에 넣을 것 — 유령 금지.
- 하드 이벤트는 뉴스가 아니라 SEC 폼이 판정한다. 헤드라인 키워드로 매도를
  발동시키지 말 것.

Marketaux 키를 내가 줬는지 먼저 물어라. 줬으면 같은 어댑터 인터페이스로
추가한다 — 기사별 원 출처 도메인이 오므로 allow 등급(reuters·cnbc·marketwatch·
businesswire 등 0.95) 기사는 정식 투표권을 갖는다. 100 req/day가 페이지네이션에
충분한지는 실측 전에 확정하지 말 것. 없으면 Alpaca만으로 진행 — 지장 없다.

뉴스가 들어오면 페르소나 격차를 재측정할 것(증거가 하나 늘면 판단이 바뀐다).

--- 2. 청산 3층 soft path → 07 sell 연결 (redesign §4 1c의 미완 항목) ---

여기서 "매도 방향에서 보수형이 더 늦게 판다"(dev-handoff 미해결 6번)를
정면으로 다뤄라. 프롬프트 의도와 반대다 — 두 성향 모두 하방 근거를 상방보다
낮은 문턱으로 받아야 한다.

--- 3. ghost 일괄 — 셋 다 실재 확인됨(2026-07-19) ---

- skipped_rules: 스키마 O(schema.sql:119) · 계약 O(role_08/contracts.py:104) ·
  UI가 읽음(context_detail.py:627) · db/domain.py가 안 씀. 쉬움 — insert 추가.
- risk_off_action: config 선언 O · conservative에 no_new_buys 설정 O · 소비자 0.
  role_08이 risk_off를 무조건 reject(contracts.py:155~)라 aggressive의 penalty가
  무시됨. 판단 로직이므로 신중히.
- conservative 도달 불가: orchestration/factory.py:50 구 러너 하드코딩.
  새 분석 잡은 이미 sorted(config.profiles)로 팬아웃한다 — Phase 4에서 자연소멸
  확인만 하면 될 가능성이 높다.

그 다음 Phase 4 → 5는 redesign §7~8 그대로.

========== ⚠️ 판단해야 할 것 (dev-handoff에 상세) ==========

- ensure_holding_in_scope를 지우지 않았다. 유니버스 이월(29a2e00) 이후에도 유지
  판단 그대로 — 이월은 유니버스 잡이 성공했을 때, 이 함수는 스크리닝이 실패한
  날을 덮는다. 서로 다른 실패를 막는다.
- 성향 격차가 다소 기계적이다(보수형에 일괄 -0.100 빼는 패턴). openai 모드 재확인.
- LOCAL 경로가 retries=0으로 굳어 있다(provider.py:249). 모델이 구조화 출력을
  한 번 놓치면 그 성향 20종목이 통째로 날아간다.

========== 진행 방식 (지금까지와 동일) ==========

- TDD (실패 테스트 → 최소 구현 → green → 태스크 단위 1커밋)
- 테스트만 믿지 말고 실제로 돌려볼 것. 지금까지 잡힌 결함 11건이 전부 테스트를
  통과한 뒤 실행에서 나왔다. 최근 3건:
    원장이 성향을 거짓 기록 · 범위 저장이 평일마다 FK 위반으로 스크리닝을 죽임 ·
    크리틱 reject 갈래가 계약 위반(mock이 늘 통과해서 한 번도 안 돌았다)
  잡을 만들면 실 config·실 DB·실 API로 한 틱 돌려봐라
- 문턱·주기·한도는 전부 config/pipeline.yaml 소유, 코드 리터럴 금지
- 유령 금지: 새 config 키·DB 컬럼은 소비자와 같은 커밋에
- 스키마를 바꾸면 4곳 전부: db/schema.sql · db/migrations/mvp2.sql ·
  tests/integration/schema_sql_expectations.py · 정본 HTML.
  그리고 '신규 설치 == 마이그레이션' 카탈로그 대조 + 마이그레이션 2회 멱등 확인.
  대조 방법: 두 DB(신규=app-v2/db/schema.sql · 구세대=app/db/schema.sql 적재 후
  migrations/mvp2.sql)에서 pg_constraint를 뽑아 diff. 지난 세션 155개 완전 일치.
- 테스트는 고정하는 코드와 함께만 삭제, 대체 테스트 같은 커밋
- 핵심·에이전트(roles/) 코드에는 '왜'를 설명하는 한국어 인라인 주석
  (docstring은 영어 한 줄 — 기존 관행)

검증:
  cd app-v2 && uv run pytest tests/unit tests/test_web.py -q   # 819 green 유지
  uv run ruff check src tests scripts
  통합(109 green)은 일회용 DB 전제 — 새 컨테이너에 db/schema.sql 적재 후 1회만.
  통합은 -p no:unraisableexception이 필요하다: asyncpg 연결 GC의 ResourceWarning이
  에러로 승격돼 가짜 실패가 뜬다(로직 실패 아님).
  포트 5481~5497은 이전 세션 컨테이너가 쓸 수 있으니 비어 있는 것을 골라라.
  통합 테스트는 전용 계좌(broker_account_id)를 쓸 것 — 기본 계좌
  quantinue-local-simulated는 다른 테스트가 현금 잔고를 정확히 단언하는 공용 자원

실 스모크 방법:
  build_job_runner를 실 Settings + JobSources(market_data=..., analyzer=...)로
  세우고 잡을 직접 run(as_of) 한다. 단 잡을 직접 부르면 tb_job_run에 성공 기록이
  안 남아 covered_tickers/스크리닝이 유니버스 스냅샷을 못 찾는다 —
  reserve_job_run + finish_job_run(succeeded=True)을 함께 불러라.

  ⚠️ 같은 슬롯을 다시 재려면 먼저 비워라. save_signal은 on_conflict_do_nothing
  (멱등 가드)이라 재실행이 첫 판단을 안 덮어쓴다. 안 비우면 새 측정값이 아니라
  옛 행을 읽게 된다 — 지난 세션에 두 번 속았다:
    DELETE FROM tb_critic_verdict v USING tb_strategist_signals s
     WHERE v.signal_id=s.id AND s.trade_date=:d
       AND s.id NOT IN (SELECT signal_id FROM tb_order);
    DELETE FROM tb_strategist_signals s
     WHERE s.trade_date=:d AND s.id NOT IN (SELECT signal_id FROM tb_order);
    DELETE FROM tb_job_run WHERE slot_date=:d AND job_name LIKE 'analysis:%';

  환경: QUANTINUE_DATA_MODE=public · QUANTINUE_DATABASE_MODE=postgres ·
  QUANTINUE_DATABASE_URL은 5445. 개발 DB(5445)에는 마이그레이션이 적용돼 있고
  실 봉 53만 개가 들어 있다(봉은 2026-07-17까지 — as_of는 07-20을 써야 세션에
  봉이 있다).

  LLM 검증이 필요하면 QUANTINUE_LLM_MODE=local. oMLX가 127.0.0.1:8888/v1에 떠
  있고 모델은 Qwen3.6-35B-A3B-OptiQ-4bit다. mock 분석기는 STRATEGY에 고정 0.76,
  CRITIC에 고정 0.82를 내므로 성향 격차나 크리틱 reject 갈래는 mock으로 검증
  불가능하다.

주의:
- app/(1차)은 다른 작업자 WIP — 절대 수정 금지. git stash도 쓰지 말 것
  (stash가 app/까지 삼킨다)
- 앱 실행 포트 8020, DB 5445
- .env를 .env.example로 덮어쓰지 말 것 — Alpaca 키가 날아간다(실제로 한 번 날아갔다)
- Alpaca 분당 호출 한도는 여전히 미확인. 추정해서 박지 말 것.
  (배치 400종목 OK · 클래스 구분자는 점(BRK.B) · 미지 심볼 1개가 배치 전체를
  죽인다 · 창 요청은 start/end 한 쌍으로 260일도 요청 수는 하루치와 같다
  — 전부 실측이고 어댑터에 반영돼 있다)
- 재설계 결정 D1~D8·계좌 금액·매도 주문 표현은 확정됨 — 되묻지 말 것
- Phase 4 구 러너 삭제는 동등성 증거 보고 + 내 확인 후에만 — 유일한 확인 지점
- push 금지(공유 저장소). 커밋만 쌓을 것
- 끝까지 자율 진행할 것. 심각한 문제가 없으면 중간에 멈추지 말고,
  컨텍스트 한계가 오면 문서 갱신하고 다음 세션 프롬프트를 만들어라

계획 세우고 시작 전에 한 번 짚어줘.
```
