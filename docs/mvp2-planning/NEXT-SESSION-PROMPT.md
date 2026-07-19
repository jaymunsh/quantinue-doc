# 다음 세션 시작 프롬프트

> `/clear` 또는 compact 후 아래 블록을 그대로 붙여넣으면 된다.
> 이 파일 자체도 세션 종료 시 갱신할 것. (최종 갱신 2026-07-20 심야 — **Phase 4 완료, 구 러너 삭제만 대기**)

---

```
Quantinue MVP-2 개발 이어서 진행. 나는 문성혁, app-v2/에서 2차 개발 중이다.

먼저 이 셋을 읽고 현재 상태를 파악해라:
1. docs/mvp2-planning/dev-handoff.md         ← 현재 상태·커밋 대응표 (여기부터)
2. docs/mvp2-planning/pipeline-redesign.md   ← 실행 정본. 확정 결정 D1~D8 + Phase 1~5
3. docs/mvp2-planning/future-roadmap.md      ← 의도적으로 미룬 것 — 여기 있는 건 지금 안 만든다

작업 브랜치는 sunghyuk. Phase 1~4 전부 완료. 남은 것은 Phase 5와 구 러너 삭제다.

핵심 확정(redesign §0, 되묻지 말 것):
- 체결은 로컬 시뮬(MockBroker 승격) + 시세는 실물(Alpaca 마켓데이터) — D1
- 무장(BROKER_MODE=alpaca) 개념 소멸 — mock이 최종 상태다 — D2
- 주기는 전부 config 소유, 기본 일 1회. 아키텍처는 실시간형 유지 — D3
- 정규장 전용(D4) · 손절·익절 동시 발동 시 손절 우선(D5) · 점진 교체(D6)
- 매도 = 별도 청산 행(order_type='close'+closes_order_id) + 자기 sell 시그널 — D7
- 계좌 평가 = 현금 + 보유수량 * 종가 — D8 (배분 잡이 프로덕션 소비자)

이미 끝난 것(다시 만들지 말 것):
- Phase 1~3 전체(핸드오프 참조) + 승인율 수리(0/22 → agg 8 · cons 3)
- Phase 4 전부: max_tokens A/B 실측→512 확정(167299e+56fa7c5) ·
  매크로 잡(436ac00) · buy 후보 리더(1914b81) · 배분 잡+equity 스냅샷+
  daily_loss_limit 배선(95b2ca0) · 캡 4곳 정리(5eb15e4)
- 실 스모크: 잡 8종 전부 succeeded (2026-07-20, dev DB) — 배분 33 bought/24
  skipped, 계좌별 게이트 발동 전부 설계 그대로(핸드오프의 표)

잡 등록 순서가 계약이다 (배분이 늘었다):
  유니버스 → 일봉 → 공시 → 뉴스 → 매크로 → 스크리닝 → 분석×성향수 → 청산 → 배분

========== ⚠️ 제일 먼저: 구 러너 삭제 판단 (유일한 확인 지점) ==========

dev-handoff의 "구 러너 삭제 — 결정 패킷"을 읽고 사용자(나)에게 물어라.
- 동등성 증거는 다 모였다: 구 11단계의 모든 durable 산출물에 새 주인이 있고
  (표 참조), role_11의 T+5 채점은 ReviewRuntime(API 계층)이라 러너와 무관하다.
- 삭제를 막는 잔여물은 웹 계층뿐이다: 관제실 런 상세·/runs API·LiveRunRuntime·
  CycleScheduler가 구 러너를 전제한다. 권고는 Phase 5 대시보드 전환과 **같이**
  지우는 것 — 지금 지우면 관제실이 빈 화면이 된다.
- 삭제 시 자연소멸 확인 목록: factory.py DEFAULT_PROFILE_NAME 유령 ·
  config mvp 블록(mvp2.allocation과 값 중복 과도기) · .env DAILY_NEW_ORDER_CAP=1

========== Phase 5 — 정리 (redesign §8) ==========

- 대시보드를 잡 상태 기반으로 전환(tb_job_run·tb_order_plan·tb_account_equity_daily
  가 이미 다 있다) → 이때 구 러너 삭제(위 판단에 따라)
- 정본 HTML 파이프라인 흐름도를 잡 체인 기준으로 미러(#logic·changelog 포함)
- ghost 재감사(선언·소비자 전수 재확인) — 특히 mvp 블록 소멸 후
- 이중 캘린더 정리(role_11 자체 캘린더 → core/market_calendar) — role_11을
  지우면 자연소멸일 수 있으니 삭제 결정 뒤에 볼 것
- mvp2.jobs.enabled는 여전히 false — 켜는 것은 운영 결정(켜면 잡 8종이 매일 돈다)

========== 진행 방식 (지금까지와 동일) ==========

- TDD(실패 테스트 → 최소 구현 → green → 태스크 단위 1커밋) · 실제로 돌려볼 것
  (실행에서만 잡힌 결함 통산 13건) · 문턱·주기·한도는 config/pipeline.yaml 소유 ·
  유령 금지(새 키·컬럼은 소비자와 같은 커밋) · 스키마 4곳 미러 + 카탈로그 대조
  (현재 제약 159·인덱스 48 완전 일치) + 마이그레이션 2회 멱등 ·
  테스트는 고정하는 코드와 함께만 삭제 · roles/ 코드에 한국어 '왜' 주석

검증:
  cd app-v2 && uv run pytest tests/unit tests/test_web.py -q   # 878 green 유지
  uv run ruff check src tests scripts
  통합(127 green)은 일회용 DB — 새 컨테이너에 db/schema.sql 적재 후 1회만.
  -p no:unraisableexception 필요. 같은 컨테이너에서 두 번 못 돌린다.
  포트 5481~5497은 이전 세션 컨테이너가 쓸 수 있으니 빈 것을 골라라.

실 스모크 방법:
  build_job_runner를 실 Settings + JobSources(market_data=…, macro=…,
  analyzer=…)로 세우고 잡을 직접 run(as_of) 한다. reserve_job_run +
  finish_job_run(succeeded=True)을 함께 부를 것(안 그러면 유니버스 스냅샷을
  못 찾는다). 슬롯 재측정 전 비우기 SQL은 dev-handoff 참조.

  ⚠️ .env의 QUANTINUE_DATABASE_URL은 5444(1차 DB)를 가리킨다. 반드시 5445로
  덮어써라. 안 그러면 다른 작업자의 DB에 쓴다.

  환경: QUANTINUE_DATA_MODE=public · QUANTINUE_DATABASE_MODE=postgres ·
  URL은 5445. dev DB에는 마이그레이션 적용됨(tb_account_equity_daily 포함),
  실 봉 53만(2026-07-17까지) + 뉴스 1440행 + 2026-07-20 잡 8종 성공 기록 +
  배분이 산 실 포지션들(계좌 9개, 33건)이 들어 있다.

  LLM 검증은 QUANTINUE_LLM_MODE=local (oMLX 127.0.0.1:8888/v1,
  Qwen3.6-35B-A3B-OptiQ-4bit). max_tokens는 이제 512가 기본(실측 근거).
  성향 2종 한 바퀴 ≈ 12분. mock 분석기는 고정 점수라 성향 격차 검증 불가.

주의:
- app/(1차)은 다른 작업자 WIP — 절대 수정 금지
- 앱 포트 8020, DB 5445 · .env를 .env.example로 덮어쓰지 말 것(Alpaca 키)
- Alpaca 분당 한도는 여전히 미확인 — 추정해 박지 말 것
- push 금지(공유 저장소). 커밋만 쌓을 것
- 끝까지 자율 진행. 구 러너 삭제만 내 확인 후에.
- 컨텍스트 한계가 오면 문서 갱신하고 다음 세션 프롬프트를 만들어라

계획 세우고 시작 전에 한 번 짚어줘.
```
