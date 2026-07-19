# 개발 핸드오프 — 현재 상태

> 최종 갱신 2026-07-19 후반 (Phase 3: 상장폐지 이월·페르소나 2종까지 반영). **이 파일은 "지금 어디까지 왔나"만 담는다.**
> 실행 지시는 **`pipeline-redesign.md`**(신 정본)에 있다 — 새 세션은 그 파일을 열고 이어가면 된다.

> 🔁 **다음 세션 시작 프롬프트**: `docs/mvp2-planning/NEXT-SESSION-PROMPT.md` — clear/compact 후 그대로 붙여넣으면 된다.

## ⚠️ 2026-07-19 재설계 — 계획이 바뀌었다

M5 착수 실사에서 구조 결함(50→1 절벽·NVDA 하드코딩 트리거·배분 단계 부재·스테이지 수=역할 인덱스 재개 전제)이 확인되어 **M5~M11 마일스톤 체계를 폐기하고 잡 기반 재설계로 전환**했다. 함께 확정된 결정: **체결은 로컬 시뮬 + 시세는 실물(Alpaca)**, 무장 개념 삭제(로드맵행), 주기는 config 소유·기본 느슨. 전체 결정 D1~D8과 Phase 계획은 `pipeline-redesign.md` §0·§4~8.

## ⭐ 먼저 읽을 것

1. **`pipeline-redesign.md`** — 신 실행 정본. 확정 결정(D1~D8)·진단·Phase 1~5·착수점 file:line.
2. **`future-roadmap.md`** — 의도적으로 미룬 것(페이퍼 전환·장외·인트라데이·스트리밍…)과 각각의 얻는 것/치르는 것.
3. `dev-playbook.md` — **완료 기록**(W0~M4·M6)과 보완 목록은 유효. M5~M11 테이블은 superseded.
4. `ghost-config-audit.md` — 유령 설정 감사(동결 문서). 재설계 Phase별 해소 귀속은 redesign §4~7에 반영됨.
5. `docs/quantinue-integrated-design.html` — 설계 정본 v5.2. ⚠️ 파이프라인 흐름도는 아직 구 11단계 기준 — 코드가 확정되는 Phase부터 미러.
6. `m4-scope-decisions.md` · `troubleshooting-log.md` — 시점 기록(동결).

## 현재 상태 (2026-07-19)

| 항목 | 상태 |
|---|---|
| 작업 브랜치 | **`sunghyuk`** (여기서 계속) |
| main 병합 | Wave 0~1 병합 완료(`818416e`, `--no-ff`). push 보류 — 공유 저장소, 사용자 확인 후 |
| 테스트 | 유닛/웹 **819 green** · 통합 **109 green** · ruff clean (재설계 착수 기준선은 681/63) |
| ⚠️ 통합 실행 | `-p no:unraisableexception` 필요. asyncpg 연결 GC의 `ResourceWarning`이 에러로 승격돼 15건이 가짜 실패로 뜬다 — 로직 실패가 아니다 |
| ⚠️ `.env` | Alpaca 키 유효(뉴스·봉 실 200 재확인). **`.env.example`로 덮어쓰지 말 것** |
| 로컬 LLM | **oMLX `127.0.0.1:8888/v1` · `Qwen3.6-35B-A3B-OptiQ-4bit`**. 2026-07-19 후반에 `.env`의 base_url·모델·키 셋 다 어긋나 있던 것을 고침(ollama를 가리키고 있었다). 키는 oMLX `settings.json`의 `auth.api_key` |
| DB | app-v2 전용 포트 **5445**(`app-v2-db-1`). 1차 `app-db-1`(5444)은 다른 작업자 WIP — 불간섭 |
| 앱 포트 | **8020** |
| 브로커 | `BROKER_MODE=mock` — **이제 이게 최종 상태다**(D1: 로컬 시뮬 체결). `TRADING_ENABLED=true`·alpaca 키는 페이퍼 전환(로드맵 R1) 대비 보존 |
| 매도 스키마 | ✅ 완료 — `order_type CHECK ('bracket','close')`·`closes_order_id`·조건부 삼중제약(schema.sql:131-149) + DDL 테스트 5건 |

### 완료 (구 마일스톤 체계 기준 — 기록 유효)

- **W0** 드라이런 완주(01→11, HTTP 201) — W0-7/W0-8은 재설계로 소멸(로드맵 R1로 이동)
- **M1** 슬롯 멱등·NYSE 캘린더·스케줄러 → **재설계에서 그대로 재사용**(D3)
- **M2** 스키마 확장 · **M3** 깔때기 복원 · **M4** 판단 방어선 8+2건 → 방어선 로직은 Phase 3~4에서 재사용
- **M6** 계좌 구조 6-1·6-3·6-4 ✅, 6-2 3/4 → 사이징·리스크 한도는 Phase 4 배분 잡이 승계
- **M5** 매도 주문 표현 확정(스키마 완료, 로직은 Phase 1이 담당)

### 진행 현황 — Phase 1

| 항목 | 상태 |
|---|---|
| **1a 장부 바닥** | ✅ **완료** (커밋 `7a8da50`~`5398bcf`). 여섯 결함 전부 실패 테스트로 재현 후 수리 — 아래 표 |
| **1b 시뮬 체결 엔진** | ✅ **완료** (`795fc6e`). 브래킷 발동 판정(일봉 고저·손절 우선 D5) + `ClosePlan`/`ClosingBroker` + `MockBroker.close()` |
| **1c 청산 잡** | ✅ **완료** (`eb15d5a`~`d3fb304`). 3층 판정(순수) + 오픈 포지션 리더 + 집행 잡. **통합 테스트로 왕복 확인**: 매수 체결 → 손절 발동 → sell 시그널 → close 주문 → 매도 체결 → 현금 +170 → 보유 0 |

**Phase 1 완료 — 시스템이 팔 줄 안다.** 유닛/웹 **714 green** · 통합 **75 green** · ruff clean.

**1c에서 발견해 함께 고친 것 2건** (계획에 없던 것):
- `tb_strategist_signals`가 `(trade_date, ticker) → tb_daily_pick`을 참조한다 → **스크리너 탈락 보유 종목은 청산 시그널을 남길 자리가 없었다.** `ensure_holding_in_scope()`로 "보유는 정의상 분석 범위 안"을 기록. ⚠️ 이건 **Phase 3 스크리닝 잡이 넘겨받을 임시 소유** — "상위 N ∪ 보유"가 구현되면 여기서 사라진다.
- `business_days_held`가 캘린더 범위(XNYS는 2027-07까지) 밖에서 예외를 던졌다 → 0 반환으로 변경. 셀 수 없을 때 시간 청산을 발동시키면 모르는 것을 근거로 파는 셈.

**해소된 유령**: `exits.time_exit_bdays`(ghost 감사 §5) — 소비자 생김.

**1a에서 실제로 재현된 결함** (전부 "매도가 없다"는 전제 위에 있던 것):

| # | 재현된 증상 | 커밋 |
|---|---|---|
| a3 | 1주 매도가 포지션을 3 → **4주로 늘림** | `7a8da50` |
| a1 | 2주 매도 후 현금 999800 → **999540** (판 대금을 받기는커녕 또 차감) | `2e17151` |
| a2 | 전량 청산 후에도 `open_position_count=1` (청산 행도 보유로 계상) | `9750428` |
| a5 | cap=1에 청산 1건 → 신규 매수 **REJECTED** | `4a7e6c6` |
| a4 | `Order` 계약이 close를 표현 불가 (검증 오류 4건) | `5184818` |
| a6 | `client_order_id` 리터럴 2곳 중복 → 공용 헬퍼 + `-c` 접미사 | `eab88ff` |

이름 정리 별도 커밋(`5398bcf`): `project_buy_only_portfolio`→`project_portfolio`, `CompletedBuyWrite`→`CompletedFillWrite`, `record_completed_buy`→`record_completed_fill`, `NOT_APPLICABLE_BUY_ONLY`→`NOT_APPLICABLE_NO_CLOSES` 등.

### 진행 현황 — Phase 2 (데이터층)

| 항목 | 상태 |
|---|---|
| `tb_daily_bar` 원장 + 청산 관측 투영 | ✅ `08754b0`. 스키마 4곳 미러 · **신규 설치 == 마이그레이션 경로**(제약 정의 문자열 일치) · 마이그레이션 2회 멱등 확인 |
| Alpaca 배치 일봉 어댑터 | ✅ `bc4af6a`. 일일 증분 **500콜 → 1~2콜** |
| 계좌 시가평가 (D8) | ✅ `742a87f`. ghost §2 equity 동결 해소 |
| **잡 실행 원장 + 주기 판정** | ✅ `7d77e6a`. 계획에 없던 선행 항목 — 아래 참조 |
| **잡 레지스트리 + 주기 config** | ✅ `c6776cc` |
| **일봉 수집 잡 + 청산 잡 마운트** | ✅ `1a79c41`. 고아 4개 연결 |
| **mock + 거래 활성 기동 가능** | ✅ `acbe36f`. D2 잔재 제거 |
| 유니버스 주간 실배선 | ✅ `eef0857` |
| Alpaca 심볼 강건화 | ✅ `bff6166`. 2000종목 실수집 성공 |
| 공시 일괄 수집 + 하드 이벤트 투영 | ✅ `7563def`. 종목당 1콜 → 그날 1콜 · `delisting_halt` 소비자 탄생 |
| 같은 종목 두 포지션 청산 | ✅ `4a2f46a`. 실행 중 발견한 매도 경로 버그 |
| 뉴스 일괄 수집 | ⏳ Phase 3으로 이관(소비자가 분석 잡이므로) — 아래 Phase 3 표 참조 |

### 진행 현황 — Phase 3 (분석 잡) — 2026-07-19 추가

| 항목 | 상태 |
|---|---|
| 성향(`inv_type`) 정합 | ✅ `2198467`. 팬아웃의 전제 — 아래 참조 |
| 일봉 이력 백필 | ✅ `2c8f7b2`. 창 지표가 계산될 수 있게 |
| 스크리닝 잡 (전 유니버스 DB 랭킹) | ✅ `724ddc2`. `screening.llm_depth` 유령 해소 |
| 분석 잡 (05~08 대체 · sell 개방) | ✅ `b361772` |
| 상장폐지 보유 이월 (redesign §11 안 A) | ✅ `29a2e00`. 실 포지션으로 팔리는 것 확인 |
| 제약 이름 드리프트 제거 | ✅ `6759083`. 카탈로그 **제약 155개 완전 일치** |
| 성향 페르소나 2종 (프롬프트) | ✅ `7dafdc5`(배관) + `57d49c3`(방법론 정박) |
| 뉴스 일괄 수집 | ⏳ **다음 착수점** — ⚠️ 아래 "출처 등급 함정" 먼저 읽을 것 |
| 청산 3층 soft path → 07 sell 연결 | ⏳ 미착수 |
| ghost 일괄(risk_off_action·conservative 도달·skipped_rules) | ⏳ 미착수 |

### 2026-07-19 후반 세션 — 완료 4건

**`29a2e00` 상장폐지된 보유를 팔 수 있게 (redesign §11 안 A)**
`tb_universe.listing_status TEXT CHECK ('listed','held_delisted')`. 유니버스 잡이
`held`를 받아 상장 피드에 없는 보유를 이월하고, **이월은 `universe_size` 절단
뒤에** 더한다(먼저 섞으면 시총 낮은 폐지 종목이 캡에 걸려 잘린다). 회사명·시총은
`last_known_listings`가 마지막 관측값에서 가져오고, 한 번도 유니버스에 없던
종목은 지어내지 않고 뺀다. `UniverseMember.market_cap` gt=0 → ge=0 ·
`members` 상한 2000 → 2500(이월분이 캡 **밖에서** 더해지므로).

실 스모크(실 봉 53만 · 실 NASDAQ 피드에서 티커 하나만 제외): 유니버스
1999 listed + 1 held_delisted → 스크리닝 21픽 → 청산이 `thesis_break`(하드
이벤트)로 실 종가 16.26에 매도 → close 주문 체결 → 열린 포지션 0 · 현금 증가.
**팔고 난 뒤 다음 재구축에서 이월분이 자연히 빠지는 것도 확인**(별도 정리 불필요).
→ 미해결 4번(매도 경로 실 실행 미발동)도 함께 해소.

**`6759083` 제약 이름 드리프트 제거** (미해결 2번)
익명 CHECK는 Postgres가 **선언 순서로** 이름을 짓는다(`tb_order_check`,
`tb_order_check1`). `CONSTRAINT` 절로 이름을 명시해 순서 의존을 끊었다.
정의는 한 글자도 안 바뀐다. 카탈로그 **155개 완전 일치**(이전 1건 불일치).

**`7dafdc5` + `57d49c3` 성향 페르소나 2종**
`load_system_prompt(task, profile=)` · `SystemPrompt.variant`(폴백을 조용히
하지 않는다) · `analyzer.analyze(..., profile=)`. 성향 축은 **role_07만** 갖는다 —
공시 요약·뉴스 채점은 "무엇이 사실인가"를 묻고 답이 성향과 무관해야 한다.

두 성향을 실제 방법론에 정박시켰다: aggressive ← 오닐(CAN SLIM)·미너비니(SEPA)
+ 드러켄밀러 / conservative ← 하워드 막스(2차적 사고·사이클) + 리버모어(손절 규율).
고른 기준은 **우리 원장으로 정직하게 답할 수 있는가** 하나다. 우리 스크리닝
SQL이 이미 오닐/미너비니 방법론이다(`rs_20`·`high_252_ratio`·`vol_ratio`·`ma20/50`).
버핏·그레이엄·다모다란은 **의도적으로 뺐고**, 각 프롬프트에 ⚠️ 섹션으로
"이 프레임이 요구하지만 우리가 못 가진 것"을 적어 지어내기를 금지했다(테스트로 고정).
검토 근거·출처(ai-berkshire·swarm-trader, 둘 다 MIT)는 `future-roadmap.md`.

실 검증(Qwen3.6-35B-A3B-OptiQ-4bit / oMLX · 실 픽 22종목):
aggressive buy 20/hold 2 · conservative buy 15/hold 7 · 판단 갈린 종목 5 ·
확신도 갈린 종목 11. (mock으로는 불가능 — STRATEGY 고정 0.76)

**실행에서만 잡힌 결함 (통산 11번째)** — `analysis/job.py`가 크리틱 reject
갈래에 `decided_layer="model"`을 썼는데 계약 Literal에 없다("llm"이 맞다).
mock 크리틱이 고정 0.82로 **늘 통과**해서 한 번도 실행된 적 없는 갈래였다.
실 LLM에서 크리틱이 반박에 성공한 첫 종목에 그날 그 성향 분석 전체가 죽었다.

**결함이 아니었던 것 1건** — 원장이 잡 요약과 계속 어긋났는데
`save_signal`의 `on_conflict_do_nothing`이 **멱등 가드로 제대로 동작**한
것이었다. 한 슬롯 = 한 판단이고 재실행이 역사를 덮어쓰지 않는다. 스모크가
이미 성공한 슬롯을 다시 돌려 첫 실행 행을 읽고 있었다. **실 스모크를 다시 잴
때는 슬롯(`tb_job_run`)과 그 슬롯의 행을 먼저 비울 것.**

### ⚠️ 뉴스 착수 전 필독 — 출처 등급 함정 (2026-07-19 실 API로 확인)

Alpaca 뉴스는 **모든 기사의 `url`이 `benzinga.com`**이다(실측 5건 전부).
그런데 `config/news_trust_policy.yaml`에서 benzinga는 `gray` = trust **0.50**이고
`gates.source_trust_min`은 **0.55**다:

```python
# role_07/contracts.py:165, 189
if source.news_score is not None and source.source_trust >= gates.source_trust_min:
    votes.append(source.news_score)        # 0.50 >= 0.55 → False
```

**계획대로 만들면 뉴스를 수집·저장·채점한 뒤 투표를 통째로 박탈당한다** —
값비싼 유령이 된다.

**권고: 뉴스는 별도 투표가 아니라 증거 종합의 맥락으로 넣는다.**
- 정책·게이트를 데이터 소스 편의로 흔들지 않는다(benzinga를 allow로 올리거나
  문턱을 0.50으로 내리는 것은 정책 오염이다).
- `gray`의 정의가 정확히 그것이다 — 정책 파일 주석에 이미 "투표는 하되
  신뢰도를 낮게 본다(07 게이트에서 투표 박탈될 수 있음)".
- **소비 지점이 이미 뚫려 있다**: `analysis_prompt(subject, holding, filings,
  headlines=())`(`roles/analysis/contracts.py:52,85`) — `headlines` 인자가 아무도
  안 채운 채 남아 있다. 헤드라인이 LLM 종합에 들어가면 `evidence.score`를 통해
  conviction에 기여한다. **투표권 없이 영향은 준다.**
- 기존 원칙("하드 이벤트는 뉴스가 아니라 SEC 폼이 판정한다")과 일관.

→ 이 방향이면 `news_score`는 계속 `None`이고, `job.py`의 해당 주석을 "뉴스는
별도 투표가 아니라 증거 종합의 맥락으로 들어간다(출처 등급 gray)"로 바꾼다.

**뉴스에 투표권을 주려면 소스를 하나 더 붙여야 한다** (2026-07-19 조사):

| 소스 | 전 시장 | 티커 태깅 | 출처 다양성 | 무료 한도 | 판정 |
|---|---|---|---|---|---|
| **Alpaca** (키 보유) | O | O (`symbols[]`) | ✗ 100% benzinga | 넉넉 | 맥락용 — **먼저 만든다** |
| **Marketaux** | O | O (엔티티, ~5000 소스) | **O — 기사별 원 출처** | 100 req/day | ⭐ 투표권을 줄 수 있는 유일한 선택지 |
| Finnhub | O(`category=general`) | **✗ 없음** | O | 60/분 | company-news는 심볼당 1콜 — 2000종목에 부적합 |
| Polygon | — | O | ✗ 사실상 benzinga 재배포 | **뉴스는 무료 티어 없음** | 탈락 |

Marketaux는 기사마다 원 출처 도메인이 오므로 `news_trust_policy.yaml`의
`allow` 등급(reuters·cnbc·marketwatch·businesswire·prnewswire·globenewswire·
barrons, 0.95)이 실제로 작동하고 **그 기사들은 정식 투표권을 갖는다**.
정책을 흔들지 않고 문제가 풀린다.

**순서: Alpaca 먼저, Marketaux를 같은 어댑터 인터페이스로 추가**(redesign §5의
"소스가 바뀌어도 어댑터 뒤에 숨긴다"). `tb_news_raw`는 소스 무관하게 같다.
100 req/day가 페이지네이션에 충분한지는 **키를 받아 실측 전에는 확정하지 않는다.**
문성혁이 키 준비 여부를 알려주기로 함(2026-07-19).

**잡 등록 순서(계약)**: 유니버스 → 일봉 → 공시 → 스크리닝 → 분석×성향수 → 청산.

**실 실측(2026-07-19)**
- 일봉 백필: 유니버스 2000 → **536,879봉 / 43.6초** / 1999종목 / 275세션(2025-06-12~2026-07-17). 2회차 0.7초/0봉.
- 스크리닝 깔때기: 1998(봉 있음) → 1971(이력 60세션) → 1946(종가 $5) → **373(거래대금 $20M)** → 픽 20. 3.7초, **API 0콜**.
- 분석: 20종목 × 성향 2 = 원장 40행, `inv_type`으로 정확히 갈림. 크리틱 40건.

**실행에서만 잡힌 결함 3건 (Phase 3)**

| # | 증상 | 커밋 |
|---|---|---|
| 8 | 원장이 성향을 거짓 기록 — aggressive로 판단하고 conservative로 저장. 팬아웃 시 두 페르소나가 같은 행을 덮어씀 | `2198467` |
| 9 | 백필 2회차가 30만 봉 재수신(28초). 봉이 끊긴 종목 하나가 전 종목 재수집을 유발 — 거래소에 봉이 없어서라 소급해도 안 채워진다 | `2c8f7b2` |
| 10 | 범위 저장이 그날 행을 통째로 삭제 → 구 러너와 같은 `trade_date`를 쓰는 **평일마다** FK 위반으로 스크리닝 잡 전체 실패. 주말이라 스모크에 안 드러났다 | `724ddc2` |

**교체한 테스트 3건** (고정하던 코드와 함께, 대체 테스트 같은 커밋 — `b361772`)
- `sell` 금지 → **매도는 보유를 요구한다**
- 5분 초과 입력 구성 불가 → 설정된 나이에서 **블로커**가 된다
- role_07 matrix의 stale 케이스(같은 이유)

**해소된 유령**: `screening.llm_depth`(§4) · `SnapshotMaxAge` 코드 리터럴 → `gates.evidence_max_age_minutes`
**신규 config(전부 소비자 동반)**: `market_data.history_days` · `screening.min_history_sessions` · `profiles.*.sell_threshold` · `gates.evidence_max_age_minutes`

**스키마 변경 1건** — `tb_daily_pick.rank` 상한 제거(4곳 미러 완료).
신규 설치 == 마이그레이션 카탈로그 대조 완료(제약 154개) · 마이그레이션 2회 멱등 확인.

#### ⚠️ 미해결로 남긴 것 (다음 세션이 판단할 것)

1. ~~**상장폐지된 보유는 팔 수 없다**~~ — ✅ 해소 `29a2e00`.
2. ~~**제약 이름 드리프트 1건**~~ — ✅ 해소 `6759083`. 카탈로그 155개 완전 일치.
3. **`ensure_holding_in_scope`를 지우지 않았다.** 문서(§4 각주)는 스크리닝 잡이
   생기면 삭제하라고 하지만, 삭제하면 스크리닝이 실패한 날 청산이 통째로 막힌다.
   스크리닝이 정상이면 이 함수는 아무 일도 안 하고 실패한 날에만 작동한다 —
   폴백으로 남겼다. **유니버스 이월(`29a2e00`) 이후에도 유지 판단은 그대로다** —
   이월은 유니버스 잡이 성공했을 때 이야기이고, 이 함수는 스크리닝이 실패한
   날을 덮는다. 서로 다른 실패를 막는다.
4. ~~**매도 경로는 실 실행에서 미발동**~~ — ✅ 해소 `29a2e00` 스모크(실 포지션
   심어 delisting 하드 이벤트로 실제 매도까지 확인).
5. **성향 격차가 다소 기계적이다** (신규). 모델이 같은 값을 낸 뒤 보수형에
   일괄 `-0.100`을 빼는 패턴이 자주 보인다. 문턱 경계에서 판단이 갈리므로 목적은
   달성했지만, 프롬프트가 **재추론**을 유도하는 정도는 openai 모드에서 재확인할 것.
6. **매도 방향에서 보수형이 더 늦게 판다** (신규). 프롬프트 의도와 반대다.
   **청산 soft path 연결(다음다음 태스크)에서 정면으로 다룰 것.**
7. **LOCAL 경로가 `retries=0`으로 굳어 있다**(`provider.py:249`). 모델이 구조화
   출력을 한 번 놓치면 그 성향의 20종목이 통째로 날아간다. 잡 러너가 잡별로
   격리하고 다음 슬롯에 재시도되므로 치명적이진 않지만 openai 전환 시 재확인.

#### 계획에 없던 선행 항목 — 잡 러너 (2026-07-19 추가)

Phase 1c는 청산 잡 **클래스**만 만들고 스케줄러 탑재는 하지 않았다. 그 결과
`AlpacaBarSource.daily_bars` → `save_daily_bars` → `exit_observations` →
`ExitJob.run` 네 부품이 인터페이스까지 맞물린 채 **전부 고아**였다(테스트에서만
호출). `CycleScheduler`는 잡 레지스트리가 아니라 11단계 사이클 하나만
트리거하므로 마운트할 자리 자체가 없었다. 그래서 잡 러너를 Phase 2 잔여의
0번으로 세웠다 — 이후 Phase 3~5의 모든 잡이 여기에 올라탄다.

- `tb_job_run` PK `(job_name, slot_date)`가 "잡 하나는 하루 한 번"을 DB로 강제.
  `running`/`succeeded`/`failed` 구분 — 실패한 슬롯만 같은 날 재예약 가능.
- `is_job_due`: 주기 = 마지막 **성공**으로부터 경과일. 요일 고정이 아니라서
  그날 앱이 꺼져 있어도 다음 날 뒤늦게 돈다.
- 등록 순서가 계약: **유니버스 → 일봉 → 청산**. 한 잡의 예외는 잡마다 격리.
- 주기는 `mvp2.jobs.cadences` 소유(universe 7일, daily_bars·exits 1일).
  `mvp2.jobs.enabled`는 **현재 false** — 켜면 루프가 돈다.

#### 실행해서만 잡힌 결함 5건 (테스트로는 안 잡혔다)

| # | 증상 | 커밋 |
|---|---|---|
| 1 | 실패한 잡 슬롯이 그날 재시도를 막음 → 수집 1회 실패 = 하루 종일 묵은 봉으로 청산 | `1a79c41` |
| 2 | **`.env` 조합(mock+거래활성)으로 앱이 아예 안 뜸** — 무장 시절 검증기 잔재 | `acbe36f` |
| 3 | 구 러너 role_01의 1행 유니버스가 주간 2000행 스냅샷을 가림 → 일봉 커버리지 붕괴 | `eef0857` |
| 4 | 청산이 유니버스까지 관측을 읽음 (2013종목 읽고 12개만 사용) | `eef0857` |
| 5 | `BRK/B` 심볼 하나가 배치 전체를 400으로 죽임 → 봉 0개 | `bff6166` |
| 6 | 봉이 안 찍히는 거래정지 종목이 청산 관측에서 누락 — 팔아야 할 바로 그 종목 | `7563def` |
| 7 | 한 계좌·같은 종목에 열린 포지션이 둘이면 하나만 청산되고 나머지는 IntegrityError | `4a2f46a` |

#### Alpaca 실측 결과 (2026-07-19, 실 API — 추정 아님)

- 한 요청 **400종목까지 200 OK**. 배치 크기는 병목이 아니었다.
- 주식 클래스 구분자는 **점**: `BRK.B` 200 / `BRK/B` 400. NASDAQ 피드는 슬래시로 준다.
- 알 수 없는 심볼 하나가 **배치 전체를 400으로 죽인다**(`invalid symbol: X`).
  피드에 `E90F6115D` 같은 쓰레기 행이 실제로 섞여 온다.
- 분당 호출 한도는 **여전히 미확인** — 박지 않았다.
- 실수집 확인: 유니버스 2013종목 → **1998봉**(직전 세션). 아직 오지 않은
  세션에는 0봉(지어내지 않음).

**Alpaca 문서 확인 결과 (2026-07-19, 추정 금지 규칙 준수)** — `docs.alpaca.markets/us/reference/stockbars`:
- `GET https://data.alpaca.markets/v2/stocks/bars` · `symbols` 쉼표 구분 · `timeframe=1Day` · `feed=iex`(무료)
- **종목 수 상한은 문서화 없음** → URL 길이 기준으로 우리가 쪼갬(`symbols_per_request`, 기본 200)
- `limit` 최대 10000이며 **"종목당이 아니라 전체 데이터 포인트 기준"**(문서 원문) → 페이지네이션 필수
- ⚠️ **분당 호출 한도는 공식 문서에서 확인 실패.** 추정해 박지 않았고 요청 크기만 설정값으로 열어둠 — 실측 후 조일 것

**`daily_loss_limit` 전제 3개 중 2개 충족**: M5 매도 ✅(Phase 1) · 시가평가 ✅(여기) · 당일 시작 equity 스냅샷 ⏳(Phase 4).

### 이후 순서

Phase 3 잔여(페르소나 2종 · 뉴스 · soft path · ghost) → 4(배분 잡·**구 러너 폐기 = 유일한 확인 지점**) → 5(정리).

## 확정된 정책 (되묻지 말 것)

- **체결 로컬 시뮬 + 시세 실물** (D1) · **무장 없음, 페이퍼 전환은 로드맵** (D2) · **주기는 config·기본 일 1회** (D3) · **정규장 전용** (D4) · **동시 발동 시 손절 우선** (D5) · **점진 교체** (D6) · **매도 = 별도 청산 행 + sell 시그널** (D7) · **시가평가 = 현금 + 보유×실호가** (D8)
- 계좌 구성(공격 $150K·$100K·$5K / 안전 $100K·$5K + 테스트 2), 거래 세션 정책(전 세션은 로드맵 R2)도 기존 확정 유지.
- `app/`(1차) 절대 수정 금지 · 문턱은 config 소유 · 유령 금지 · 스키마 4곳 미러.

## 실행 명령

```bash
cd app-v2
uv run pytest tests/unit tests/test_web.py -q          # 819 green 유지
uv run ruff check src tests scripts
uv run uvicorn quantinue.main:app --port 8020
docker compose up -d db                                 # 5445

# 통합(109)은 일회용 DB — 새 컨테이너에 db/schema.sql 적재 후 1회
# ⚠️ -p no:unraisableexception 필수
docker run -d --name t -e POSTGRES_DB=quantinue -e POSTGRES_USER=quantinue \
  -e POSTGRES_PASSWORD=quantinue -p 127.0.0.1:5480:5432 postgres:17-alpine
docker exec -i t psql -q -U quantinue -d quantinue < db/schema.sql
QUANTINUE_TEST_DATABASE_URL="postgresql+asyncpg://quantinue:quantinue@127.0.0.1:5480/quantinue" \
  uv run pytest tests/integration -q
```

## app-v2 재생성이 필요할 때 (거의 없음)

```bash
rm -rf app-v2 && mkdir app-v2 \
  && git archive 6163630 app | tar -x --strip-components=1 -C app-v2 \
  && cp app/.env app-v2/.env
```
※ `.omo/`(1차 오케스트레이션 흔적 21MB)는 baseline에서 제외했다 — 다시 넣지 말 것.
