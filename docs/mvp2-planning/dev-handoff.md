# 개발 핸드오프 — 현재 상태

> 최종 갱신 2026-07-19 (재설계 반영). **이 파일은 "지금 어디까지 왔나"만 담는다.**
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
| 테스트 | 유닛/웹 **714 green** · 통합 **75 green** · ruff clean (Phase 1 완료 반영. 재설계 착수 기준선은 681/63) |
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
| 유니버스 주간 실배선 | ⏳ 다음 |
| 뉴스·공시 일괄 수집 | ⏳ 다음 |

**Alpaca 문서 확인 결과 (2026-07-19, 추정 금지 규칙 준수)** — `docs.alpaca.markets/us/reference/stockbars`:
- `GET https://data.alpaca.markets/v2/stocks/bars` · `symbols` 쉼표 구분 · `timeframe=1Day` · `feed=iex`(무료)
- **종목 수 상한은 문서화 없음** → URL 길이 기준으로 우리가 쪼갬(`symbols_per_request`, 기본 200)
- `limit` 최대 10000이며 **"종목당이 아니라 전체 데이터 포인트 기준"**(문서 원문) → 페이지네이션 필수
- ⚠️ **분당 호출 한도는 공식 문서에서 확인 실패.** 추정해 박지 않았고 요청 크기만 설정값으로 열어둠 — 실측 후 조일 것

**`daily_loss_limit` 전제 3개 중 2개 충족**: M5 매도 ✅(Phase 1) · 시가평가 ✅(여기) · 당일 시작 equity 스냅샷 ⏳(Phase 4).

### 이후 순서

Phase 3(분석 잡·07 sell) → 4(배분 잡·**구 러너 폐기 = 유일한 확인 지점**) → 5(정리).

## 확정된 정책 (되묻지 말 것)

- **체결 로컬 시뮬 + 시세 실물** (D1) · **무장 없음, 페이퍼 전환은 로드맵** (D2) · **주기는 config·기본 일 1회** (D3) · **정규장 전용** (D4) · **동시 발동 시 손절 우선** (D5) · **점진 교체** (D6) · **매도 = 별도 청산 행 + sell 시그널** (D7) · **시가평가 = 현금 + 보유×실호가** (D8)
- 계좌 구성(공격 $150K·$100K·$5K / 안전 $100K·$5K + 테스트 2), 거래 세션 정책(전 세션은 로드맵 R2)도 기존 확정 유지.
- `app/`(1차) 절대 수정 금지 · 문턱은 config 소유 · 유령 금지 · 스키마 4곳 미러.

## 실행 명령

```bash
cd app-v2
uv run pytest tests/unit tests/test_web.py -q          # 681 green 유지
uv run ruff check src tests scripts
uv run uvicorn quantinue.main:app --port 8020
docker compose up -d db                                 # 5445

# 통합(63)은 일회용 DB — 새 컨테이너에 db/schema.sql 적재 후 1회
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
