# 파이프라인 재설계 정본 (2026-07-19)

> 결정: 문성혁 + Claude (2026-07-19 세션). **이 문서가 M5 이후의 실행 정본이다.**
> `dev-playbook.md`의 M5~M11 테이블은 superseded — 완료 기록(W0~M4·M6)과 보완 목록은 계속 유효하다.
> 근거 실사: 이 문서의 file:line 인용은 2026-07-19 코드 실사(에이전트 3방향 병렬) 결과다. 착수 시 재확인할 것.

## 0. 확정 결정 — 되묻지 말 것

| # | 결정 | 근거 요약 |
|---|---|---|
| D1 | **체결은 로컬 시뮬, 시세는 실물(Alpaca 마켓데이터)** | 시연을 한국 낮에 라이브로 돌릴 수 있다(NYSE 정규장 = KST 22:30~05:00). 실브로커 팬아웃 비대칭(M6 안전 제약)이 소멸하고 계좌 7개가 동등한 체결 엔진을 탄다. Alpaca 페이퍼는 원래 무료·가상 자금이므로 비용 문제가 아니라 시연·단순화 문제 |
| D2 | **무장(W0-7/W0-8) 개념 삭제 → 로드맵 "페이퍼/실거래 전환"으로 이동** | D1의 귀결. `broker/alpaca.py` 제출 경로는 삭제하지 않고 휴면 보존 |
| D3 | **아키텍처는 실시간형 유지, 주기는 전부 config 값 + 기본값 느슨(일 1회)** | M1 스케줄러(세션 인식·슬롯 멱등)는 그대로 재사용. 30분으로 조이는 건 yaml 숫자 변경이지 코드 변경이 아니어야 한다. 관리자페이지 주기 조정 UI는 M9(런타임 설정 저장소 설계 필요 — 지금 만들면 유령) |
| D4 | **정규장 전용. 장외 거래(구 5-6·5-7)는 로드맵행** | 주기 문제가 아니라 세션 범위 문제. 로컬 시뮬에선 장외 개념 자체가 페이퍼 전환의 하위 항목 |
| D5 | **같은 날 손절·익절 동시 발동 시 손절 우선** | 일봉으로는 장중 순서를 알 수 없다 — 보수 규칙으로 고정, 시뮬 한계로 문서화 |
| D6 | **점진 교체(교살자), 빅뱅 금지** | 새 잡을 구 러너 옆에 세우고, E2E 동등성 통과 조각부터 구 러너를 잘라낸다. 전 구간에서 항상 돌아가는 시스템 유지 |
| D7 | **매도 주문 표현은 기존 확정 유지** | 별도 청산 행 `order_type='close'` + `closes_order_id`, 기계적 청산도 `side='sell'` 시그널 행 생성(`tb_order.signal_id NOT NULL` + `UNIQUE(account_id,signal_id)` — schema.sql:132,141) |
| D8 | **계좌 시가평가 = 현금(원장) + 보유 × 실호가** | 지금은 실시간이 아니었다 — `tb_account.equity`는 최초 자본에 동결(ghost 감사 §2). Phase 2에서 처음 진짜가 되고, `daily_loss_limit`의 마지막 전제가 충족된다 |

## 1. 진단 — 왜 갈아엎는가

1. **작업 단위가 잘못됐다.** 시장 전체 스캔(주간·일간)·종목 판단(일간)·계좌 배분·보유 청산은 주기·비용·실패 도메인이 다른데 "11단계 선형 런" 하나에 묶였다. 증상: 픽 50개를 05~08이 아무도 안 읽는 50→1 절벽(`daily_screener_output`은 표시 전용, role_05~08은 전부 `context.request.ticker`만 소비), 스케줄 트리거의 NVDA 하드코딩(`api/schemas.py:18`), 계좌 팬아웃을 role 내부에 우겨넣은 구조.
2. **재개 기계가 확장을 막는다.** `PipelineOrchestrator.run()`이 `self._roles[len(context.stages):]`(pipeline.py:123)로 스테이지 수 = 역할 인덱스를 전제 — 다종목·다단계 팬아웃과 정면 충돌.
3. **살 줄만 알고 팔 줄 모른다.** 매도 경로 0. 이게 실포지션 생기는 순간 최대 리스크 공백이므로 청산이 Phase 1이다.
4. **"배분"이라는 단계가 없다.** "매수 신호 N개 중 어느 것을 살까"를 아무도 묻지 않는다 — 일일 캡을 선착순 소진. 사이징·max_weight·min_cash_ratio는 후보 집합 전체를 놓고 푸는 문제다.

## 2. 목표 아키텍처

```
[주간 잡]   유니버스 재구축(~2000)            → tb_universe 계열
[일간 잡]   일봉 증분 적재(배치, 전일 1봉)     → tb_daily_bar
[일간 잡]   뉴스·공시 일괄 수집(피드 통째)     → tb_news / tb_disclosure
[일간 잡]   스크리닝: DB 랭킹(API 0콜)        → 상위 llm_depth(20) ∪ 보유(캡 무관)
[종목별 잡] 분석: 증거 종합 → 07 판단(buy/hold/sell) → 08 크리틱   → tb_strategist_signals
[일간 잡]   배분: 후보 전체 × 계좌별 선택+사이징 → 집행(시뮬 체결)  → tb_order_plan/tb_order/tb_fill
[일간 잡]   청산 수호: 보유 열거 → 브래킷 판정·시간 청산·하드 이벤트 → close 주문
[T+5]      리뷰·학습 (기존 role_11)
```

- 잡 간 핸드오프는 **DB**. 한 잡이 죽어도 다른 잡은 어제 데이터로 돈다.
- 각 잡은 M1 스케줄러에 탑재, 주기는 config (D3).
- 판단 규칙(M4 방어선 8종·크리틱 하드게이트·M6 사이징·리스크 한도)은 **로직 재사용** — 배관만 교체.

## 3. 진행 원칙 (기존 규칙 승계)

- TDD: 실패 테스트 → 최소 구현 → green → 태스크 단위 1커밋. 유닛/웹 **681 green** 기준선(계약 변경 시 테스트 같은 커밋 갱신).
- **테스트 삭제 규칙(2026-07-19 확정)**: 테스트는 그것이 고정하는 코드와 **함께만** 삭제하고, 대체 테스트를 같은 커밋에 넣는다. Phase 4 구 러너 삭제 시 총 개수 변동은 정상 — 숫자 유지가 아니라 이 규칙이 기준이다.
- 문턱·주기·한도는 `config/pipeline.yaml` 소유. 코드 리터럴 금지.
- **유령 금지**: 새 config 키·DB 컬럼은 소비자와 같은 커밋. 스키마 변경 시 4곳 미러(schema.sql · migrations/mvp2.sql · schema_sql_expectations.py · 정본 HTML).
- 로직 확정 시 정본 HTML `#logic` 미러 + changelog.
- **주석 규칙(2026-07-19 확정, 문성혁 지시)**: 핵심 로직 — 특히 `roles/`(에이전트) — 에는 **"왜 이렇게 했는가"를 설명하는 친절한 주석**을 단다. 기존 관행 준수: **docstring은 영어 한 줄, 판단 근거·트레이드오프·함정을 적는 인라인 주석은 한국어**(선례: `market_data/http_client.py:29-35`, `role_07/contracts.py:105`, `role_10/service.py:56`). "무엇을"이 아니라 **"왜, 그리고 왜 다른 방식이 아닌지"**를 적을 것 — 코드를 읽으면 무엇을 하는지는 보이지만 왜는 안 보인다.
- `app/`(1차)은 다른 작업자 WIP — 절대 불간섭.

## 4. Phase 1 — 청산 수호 (착수점)

**목표**: 시스템이 팔 줄 알게 된다. 가장 작은 독립 조각으로 "잡 + DB 핸드오프" 패턴을 실증한다.

### 1a. 장부 바닥 4곳 + 캡 필터 (매도 체결이 착지할 곳 — 최우선)

| # | 결함 | 위치 | 수리 |
|---|---|---|---|
| a1 | sell fill 회계 부재 — side="buy" 하드코딩, 현금 차감만 있고 입금 없음 | `db/postgres_accounting.py:96-113` (차감 `:76-88`) | side 인식 + 매도 시 현금 입금(수량×가격) |
| a2 | 오픈 포지션 판정 오류 — `COUNT(DISTINCT ticker) WHERE status='filled'`, order_type 필터·closes 제외 없음 → 청산 후에도 보유로 계상 + close 행 이중 계상 | `db/domain.py:141-152`(active_accounts) · `:182-187`(account_risk_state) | 공용 `open_positions()` 신설: `tb_order b LEFT JOIN tb_order c ON c.closes_order_id=b.id AND c.status IN ('filled','submitted') WHERE b.order_type='bracket' AND b.status='filled' AND c.id IS NULL`. 기존 카운트를 이 술어로 재정의. 죽어 있는 `has_position`/`has_open_order` 게이트(`role_09/contracts.py:29-30,149-152` — service가 안 넘겨 항상 False) 배선 |
| a3 | 포트폴리오 투영이 buy-only — 모든 fill을 매수 합산, **매도 체결이 포지션을 늘린다** | `db/simulated_portfolio.py:186-231` (합산 `:215-219`), fill 쿼리에 side 필터 없음(`postgres_portfolio.py:106-125`) | side 인식 투영: 매도 fill은 수량 차감 + 실현손익 산출 |
| a4 | Python Order 계약이 SQL보다 뒤처짐 — `order_type: Literal["bracket"]`, stop/take 필수 | `core/schemas.py:113-137` (`:125`) | SQL 수준으로 확장: close 표현, stop/take nullable, 삼중제약은 bracket 한정. SQL 쪽은 **이미 완료**(schema.sql:131-149, migrations/mvp2.sql:171-190) |
| a5 | 신규 매수 캡에 order_type 필터 없음 → 청산 행이 매수 캡을 소진 | `db/postgres_query.py:199-210` (reserve_daily_order 카운트) | `order_type='bracket'` 필터 추가 |
| a6 | client_order_id 파생 리터럴 2곳 중복 | `role_10_order_execution/contracts.py:23-27` vs `role_09_risk_portfolio/service.py:147` (`q-a{account}-s{signal}`) | 공용 헬퍼로 단일화 + close용 파생 규칙 추가(예: `-c` 접미). `max_length=48`(broker/contracts.py:19) 준수 |

부수: `db/reviews.py:62-70`(fill 평균가에 side 필터 없음)은 close가 자기 sell 시그널을 갖는 D7 규약 덕에 당장 안전 — 회귀 테스트로 규약을 고정할 것.

### 1b. 시뮬 체결 엔진 승격

- MockBroker는 더 이상 테스트 대역이 아니라 **제품 구성요소**다(D1). 개명·승격: 현재 즉시 브래킷 체결(`broker/mock.py:38-73`)에 다음 추가 —
  - **브래킷 발동 판정**: 일봉 고저 대비 — 저가 ≤ stop → 손절 체결, 고가 ≥ take → 익절 체결, 동시면 손절 우선(D5)
  - **close 체결**: `OrderPlan`이 close를 표현 못 함(`broker/contracts.py:13-31`의 `require_buy_bracket`) → ClosePlan 모델 또는 판별 유니온
- 시세는 Phase 1에선 기존 종목별 조회로 충분(보유 몇 개뿐). Alpaca 어댑터는 Phase 2.
- Alpaca 취소 API·close 페이로드 분기(`alpaca.py:262-274`는 buy-bracket 하드코딩)는 **만들지 않는다** — 로드맵 "페이퍼 전환"의 일부(D2).

### 1c. 청산 잡

- **독립 잡**(11단계 런의 스테이지 아님). M1 스케줄러 탑재, 주기 config(기본 일 1회).
- 흐름: `open_positions()` 열거 → ① 브래킷 발동 판정(1b) ② 시간 청산: 보유 ≥ `exits.time_exit_bdays`(10, **첫 소비자 탄생** — ghost §5) & 논지 미실현, 영업일은 `core/market_calendar.NyseCalendar.add_business_days`(role_11의 자체 캘린더 말고 이쪽 — 이중 캘린더 정리는 Phase 5) ③ 하드 이벤트(`delisting_halt` 등 `ontology.EventType`) 즉시 청산. soft 논지 붕괴(LLM 경유)는 Phase 3에서 연결.
- 청산 결정은 `side='sell'` 시그널 행 + close 주문 행(D7). `tb_order_plan.decision`이 현재 `('planned','skipped')`뿐(schema.sql:171-179)이라 청산 기록엔 스키마 확장 필요 — 4곳 미러 + 소비자 같은 커밋.
- E2E: `tests/integration/test_m4_guards_end_to_end.py` 패턴 재사용(`_ConditionInjector` 방식 역할 치환, 테스트별 `cycle_ts` 분리 — 멱등키 충돌 방지).

**완료 기준**: 시간 청산·하드 이벤트 청산 강제 발동 E2E green + 청산↔매수 조인 실현손익 실계산 + 시뮬 계좌에서 매수→청산→현금 증가 왕복 확인.

## 5. Phase 2 — 데이터층

- `tb_daily_bar` 신설(**소비자와 같은 커밋**) + 시세 어댑터. 필요한 데이터는 정확히 둘: **일봉 EOD ~2000종목**(스크리닝·브래킷 판정·시간 청산) + **최근 체결가**(평가·체결 시뮬) — 15분 지연도 시연에 충분.
  **소스 폴백 체인(2026-07-19 확정)**: ① Alpaca 마켓데이터(IEX 무료, 키 기존재 — ⏳ 엔드포인트·한도 착수 시 문서 확인) ② Stooq(무키 완전 무료 EOD CSV — 일봉 폴백) ③ Finnhub 무료 티어(분당 60콜 — 호가 폴백) ④ 최악에도 기존 public 소스 유지. 소스가 바뀌어도 어댑터 인터페이스 뒤에 숨긴다 — 소비자는 `tb_daily_bar`만 본다.
- 증분 적재: 매일 전일 1봉. 종목당 1콜×500(현 role_02, 타임아웃 900s) 구조 폐기.
- 유니버스 **주간** 실배선 — 선언은 원래 weekly(`role_01_universe_screener/contracts.py:62`), 코드만 매 런이었다.
- 뉴스·공시 **일괄 수집**: RSS 전체 피드·SEC 일일 인덱스를 통째로 받아 종목 매칭. 종목별 폴링 폐기. 콜 수가 종목 수와 무관해진다.

  **⚠️ 착수 전 확인된 구조적 제약 (2026-07-19 실사)**
  - **역할 번호가 뒤집혀 있다**: `role_05` = 공시(SEC), `role_06` = 뉴스(RSS).
  - `tb_news`(schema.sql:68)와 `tb_disclosure`(:40) 둘 다 `(trade_date, ticker) → tb_daily_pick` FK를 건다. **그날 픽에 없는 종목은 행을 넣을 수 없다** — 그런데 일괄 수집의 목적이 바로 픽 밖(탈락한 보유) 종목을 덮는 것이다. → `tb_daily_bar`와 같은 패턴으로 **원시 원장 신설**(`tb_news_raw`/`tb_disclosure_raw`, 픽 무관) + 기존 두 테이블은 LLM 채점 결과(분석 대상 한정)로 역할 유지.
  - **공시는 완료**(커밋 `7563def`). SEC 일일 인덱스 `form.{YYYYMMDD}.idx` 1콜로 그날 전 시장(3000~4100행)을 받아 CIK→티커 매칭 후 `tb_disclosure_raw`에 적재한다.
  - **뉴스는 소스가 확정됐고 구현만 남았다.** Google News는 전체 피드가 없어 막혔지만, **Alpaca 뉴스 API로 해결된다**(2026-07-19 실 API 확인):
    `GET https://data.alpaca.markets/v1beta1/news` · `start`/`end` RFC3339 · `limit` · `next_page_token` 페이지네이션 · 심볼 미지정이면 **전 시장**이 오고 기사마다 `symbols` 배열이 붙는다. 우리가 이미 쓰는 자격증명 그대로 200. 소스는 benzinga.
    → 종목별 폴링이 구조적으로 사라진다. 단 **`tb_news_raw`의 소비자는 Phase 3 분석 잡**이므로(유령 금지) 원장·어댑터·잡을 그 소비자와 **같은 커밋**에 넣을 것. 하드 이벤트는 뉴스가 아니라 SEC 폼이 판정한다(권위 있는 쪽) — 뉴스 헤드라인 키워드로 매도를 발동시키지 말 것.
  - `ontology.EventType`의 `delisting_halt`(ontology.py:17)는 **소비자 0**. 하드 이벤트는 별도 불리언(`is_hard_blocked`)으로만 존재하고 둘을 잇는 다리가 없다.
  - **`exit_observations`에 실제 버그가 있다**(`db/domain.py`): dict를 `bars.items()` 키로만 만든다. 거래정지 종목은 봉이 안 찍히므로 관측에서 조용히 사라진다 — 정확히 `delisting_halt` 케이스가 빠진다. 하드 이벤트를 붙일 때 **두 키 집합 union으로 재구조화**해야 한다.
- 계좌 시가평가 배선(D8): 평가액 = 현금 + 보유×실호가. 미장 시간엔 직전 종가. `daily_loss_limit` 전제 충족.

## 6. Phase 3 — 분석 잡 (구 01~08 대체)

- 스크리닝 잡: `tb_daily_bar` 기반 DB 랭킹(전 유니버스 — 봉이 있으면 계산은 공짜, 500 캡 존재 이유 소멸) → 상위 `screening.llm_depth`(20, **소비자 탄생** — ghost §4) ∪ 보유(캡 무관).
- 분석 잡(종목별): 05·06을 증거 종합으로 통합(종목당 LLM 2~3콜), **07 sell 판단** — 보유 맥락(진입가·보유일·미실현손익) 입력, 계약 개방(`role_07/contracts.py:118` Literal buy/hold → sell. `ontology.Side.SELL`은 기존재. 금지 테스트 `test_roles_05_08_contracts.py:56` 교체), 프롬프트 전면 개정 + **성향 페르소나 2종**(현재 2줄뿐 — `prompts/role_07_strategist.md`), **08 매도 검증** — 패닉 매도 반박, 하드 이벤트는 검증 예외(`CriticInput.side Literal["buy"]` 고정 해제 — `role_08/contracts.py:30`, `service.py:41-55`의 no_buy_proposal 분기).
- ghost 일괄 해소: `risk_off_action`(role_08이 risk_off를 무조건 reject — `role_08/contracts.py:146-157` — 라 aggressive의 penalty가 무시되던 것 포함) · conservative 도달 불가(`factory.py:50` 하드코딩) · `skipped_rules` 생성·저장(`db/domain.py:283-294` insert 누락).
- 청산 잡의 3층 soft path(점수 악재 → 07 sell 경유)를 여기서 연결.

## 7. Phase 4 — 배분 잡 + 구 러너 폐기

> ✅ **2026-07-20 완료(폐기 제외)** — 배분 잡(`95b2ca0`)·매크로 잡(`436ac00`)·
> 캡 정리(`5eb15e4`)·daily_loss_limit 배선(전제 3/3 충족). §10의 정렬 기준은
> **확신도 단독, 동률만 랭크**로 확정. 구 러너 삭제는 동등성 증거 패킷과 함께
> **사용자 확인 대기**(dev-handoff "구 러너 삭제 — 결정 패킷") — 웹 계층이
> 러너를 전제하므로 Phase 5 대시보드 전환과 같이 지우는 것을 권고.

- **후보 집합 전체**를 계좌별로 한 번에 놓고 "어느 N개를 살까" 결정 — 신설 단계. 사이징·리스크 한도는 기존 로직 승계(`build_order_plan`·`_blocking_reason` — E2E-5로 증명된 메커니즘: 같은 신호 → 공격 200주/안전 100주). 수량 결정은 여전히 주문 직전, 그 시점 계좌 자본 기준.
- 캡 기본값 4곳 불일치 정리: `core/config.py:106`=1 · `orchestration/policy.py:102`=1 · `role_09/service.py:39`=1 · `role_09/contracts.py:33`=5 (실효값은 env=5).
- `daily_loss_limit` 배선(전제 충족 시): 당일 시작 equity 스냅샷 `tb_account_equity_daily` — 소비자와 같은 커밋(ghost §2). 판정은 E2E-3.
- 새 경로가 구 러너와 E2E 동등성 통과 → **11단계 러너 삭제**. ⚠️ **삭제 직전 동등성 증거(E2E 결과)를 사용자에게 보고하고 확인 후 삭제**(2026-07-19 문성혁 지시 — 자율 런 중 유일한 확인 지점).

## 8. Phase 5 — 정리

- 대시보드를 잡 상태 기반으로 · 정본 HTML 미러(파이프라인 흐름도·#logic·changelog) · ghost 재감사(선언·소비자 전수 재확인) · 이중 캘린더 정리(role_11 자체 캘린더 → `core/market_calendar`).

## 9. 검증 명령

```bash
cd app-v2
uv run pytest tests/unit tests/test_web.py -q     # 681 green 유지 (기준선 2026-07-19)
uv run ruff check src tests scripts
# 통합(63 green)은 일회용 DB 전제 — 새 컨테이너에 db/schema.sql 적재 후 1회
```

## 10. ⏳ 착수 시 확인할 것

| Phase | 항목 |
|---|---|
| 1c | `tb_order_plan` 청산 기록 스키마 확장안 — decision enum 확장 vs 별도 컬럼 |
| 2 | Alpaca 마켓데이터: 배치 일봉 엔드포인트·IEX 무료 티어 한도·호가 스냅샷 API |
| 2 | 기존 public 데이터 소스(NASDAQ·SEC)와 Alpaca의 역할 분담 — 일봉·호가는 Alpaca, 공시는 SEC 유지 |
| 3 | 05·06 통합 후 `tb_disclosure_signal`/`tb_news_signal` 기록 계약 유지 여부 |
| 4 | 배분 잡의 후보 정렬 기준(확신도 단독 vs 리스크 조정) — 착수 시 설계 |

## 11. 상장폐지된 보유를 팔 수 없는 구조 (2026-07-19 발견, 미해결)

**증상.** 보유 중인 종목이 상장폐지되면 시스템이 그것을 **팔 수 없다.** 정확히
`ontology.delisting_halt`가 겨냥한 시나리오인데, 하필 그 경우에 경로가 막힌다.

**원인은 FK 사슬이다.**

```
tb_universe(as_of_date, ticker)          ← 상장 피드 스냅샷. 주간 재구축(D3)
   ↑ FK
tb_daily_pick(trade_date, ticker)        ← 그날의 분석 범위
   ↑ FK
tb_strategist_signals(trade_date,ticker) ← 판단. sell 시그널도 여기 앉는다
   ↑ FK (signal_id NOT NULL)
tb_order(order_type='close')             ← 청산 주문
```

상장폐지 종목은 다음 주간 재구축에서 상장 피드에 없으므로 `tb_universe`에서
사라진다 → `tb_daily_pick` 행을 만들 수 없다 → sell 시그널을 남길 자리가 없다
→ close 주문을 못 만든다. 포지션이 원장에 열린 채 영구히 남는다.

`ensure_holding_in_scope`(Phase 1c)도 못 구한다 — 그 함수도 `tb_universe`에
없으면 `False`를 반환한다. 스크리닝 잡(`724ddc2`)은 이런 보유를 발견하면
요약에 `skipped N unlisted holdings`로 남기기만 한다(조용히 지나가지는 않는다).

### 검토한 선택지

| # | 안 | 얻는 것 | 치르는 것 |
|---|---|---|---|
| **A** | **유니버스가 보유를 이월한다** — 재구축 시 상장 피드 ∪ 현재 보유. 이월분은 컬럼으로 표시(`listing_status` 또는 `is_listed`) | FK 사슬을 하나도 안 건드리고 뿌리에서 해결. 하위 전부(픽·시그널·주문)가 그대로 작동. 이월 사실이 원장에 남아 감사 가능 | `tb_universe`의 의미가 "상장 피드 스냅샷" → "거래 가능 범위"로 바뀐다. `market_cap NOT NULL CHECK(>=0)`을 뭘로 채울지 결정 필요(0 vs 마지막 관측값) |
| B | `tb_daily_pick → tb_universe` FK 제거 | 가장 적은 코드 | 픽이 알려진 유니버스에서 나왔다는 계보 보증을 잃는다. 오타 티커가 원장에 앉는 것을 막던 방어선이 사라진다 |
| C | 청산 시그널이 `tb_daily_pick`을 우회 (close 전용 경로) | 유니버스 의미 보존 | 계보가 매수/매도로 갈린다. role_11이 "내 판단의 결말"을 찾는 조인이 두 갈래가 된다. D7(매도도 같은 계보)의 취지에 정면으로 반한다 |
| D | 청산 잡이 필요할 때 `tb_universe` 행을 만든다 | 변경 범위 최소 | 청산 잡이 유니버스를 쓰는 건 책임 역전이고, **사후**라서 그 사이 스크리닝·분석은 여전히 못 본다 |

### 권고: A

사용자(문성혁) 방향도 A다 — "컬럼에 표기하는 등". 근거:

- 문제의 뿌리는 "거래 가능 종목의 정의가 상장 피드와 같다"는 **암묵 가정**이다.
  실제 정의는 **상장 피드 ∪ 우리가 든 것**이다. A는 그 정의를 코드가 아니라
  스키마에 적는다.
- 청산뿐 아니라 스크리닝·분석도 자동으로 고쳐진다. 상장폐지 종목이 범위에
  들어와 하드 이벤트로 청산되는 정상 경로를 타게 된다.
- 이월분에 라벨이 붙으므로 "왜 상장 피드에 없는 종목이 유니버스에 있나"에
  답할 수 있다. 라벨 없이 union만 하면 그 자체가 다음 세대의 유령이 된다.

### 착수 시 결정할 것

1. **컬럼 형태** — `listing_status TEXT CHECK IN ('listed','held_delisted')` 권장.
   불리언(`is_listed`)보다 나은 이유: 나중에 `suspended`·`pending_delisting`이
   생겨도 CHECK만 늘리면 된다. 불리언은 세 번째 상태가 오면 못 늘린다.
2. **`market_cap`** — 이월분은 마지막 관측값을 옮길지 0으로 둘지.
   0이면 유니버스 잡의 시총 내림차순 정렬에서 맨 뒤로 가는데, `universe_size`
   절단에 걸려 잘려나가면 문제가 되돌아온다. **보유 이월분은 절단 대상에서
   제외**해야 한다(캡 무관 — 스크리닝의 "보유는 캡과 무관"과 같은 원리).
3. **유니버스 잡이 보유를 알아야 한다** — 현재 `build_universe_job`은 티커
   소스를 안 받는다. `build_screening_job`의 `held` 인자와 같은 형태로 주입.
4. **해제 조건** — 이월은 현재 보유 기준이므로, 팔고 나면 다음 재구축에서
   자연히 빠진다. 별도 정리 로직 불필요.
5. 스키마 4곳 미러 + 신규 설치 == 마이그레이션 카탈로그 대조 + 2회 멱등.
6. **검증은 반드시 실 포지션으로.** 상장폐지 종목을 보유한 상태를 만들고
   유니버스 재구축 → 스크리닝 → 청산까지 한 바퀴 돌려 실제로 팔리는지 볼 것.
   이 결함 자체가 "테스트는 통과하는데 실행에서 막히는" 종류다.
