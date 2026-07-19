# MVP1 50→20→20 데이터 계보 및 화면 런타임 감사

## 결과

- 제품 런타임: `public + local LLM + PostgreSQL + mock broker`, 외부 주문 OFF
- 최신 자동 사이클: 후보 20개가 모두 영속 기록됨. 18 completed, 2 failed(V, ASML: 로컬 모델 구조화 응답 실패)
- 최신 상세 원장: 01 유니버스 50행, 02 기술 분석 20행, 03 후보 20행
- 브라우저: 1440 px / 390 px 모두 후보 카드 20, 역할 11, 계보 패널 11, 가로 overflow 0, console error 0

## 런타임 가설

1. **NVDA 한 건만 보이는 이유가 자동 배치가 아니라 단일 `/api/runs` 실행인가? — 확인됨.** 8099 memory/fixture 런타임의 실행은 `automatic=false`, `candidate_rank=null`, Role 01/02/03 각 1행이었다. 자동 폼 `/runs`와 단일 티커 API를 화면 배지로 구분하고, 최신 단일 실행이 최근 자동 후보 보드를 숨기지 않도록 조회 사이클을 분리했다.
2. **과거 50개 데이터가 사라진 것이 아니라 다른 영속 런타임에 남아 있는가? — 확인됨.** 제품 PostgreSQL 기반 8011 응답에서 기존 50개 유니버스와 20개 기술 스냅샷을 다시 관측했다. memory 서버 재시작은 해당 프로세스의 메모리만 비웠으며 제품 PostgreSQL 데이터는 유지됐다.
3. **최신 public 자동 배치가 NASDAQ의 경로 비호환 티커 때문에 시작 전에 실패하는가? — 확인 후 수정.** 실제 응답의 `BRK/A`, `BRK/B`가 엄격한 티커 계약을 위반했고 캔들 URL에서도 404였다. 어댑터 경계에서 지원 패턴만 통과시키는 red→green 회귀 테스트를 추가한 뒤 실제 자동 배치가 20개 후보를 생성했다.
4. **장 마감일과 실행일이 다르면 공시/뉴스 원문 FK가 실패하는가? — 확인 후 수정.** 실제 `(trade_date,ticker)=(2026-07-17,AAPL)` 원문이 `tb_daily_pick`의 2026-07-16 행을 찾지 못했다. 전략 신호와 원문 저장의 trade_date를 선택된 DailyPick 거래일로 통일한 뒤 후보 18개가 11단계를 완료했다.

## 화면 계보

각 01–11 블록은 수집 입력, 실제 선정 규칙/공식, PostgreSQL 목적지를 항상 표시한다. 공시는 `tb_disclosure → tb_disclosure_signal`, 뉴스는 `tb_news → tb_news_signal`을 명시하고 원문과 실행별 대표 신호의 역할을 구분한다.

## 검증

- `uv run ruff format --check .`: PASS
- `uv run ruff check .`: PASS
- `uv run basedpyright`: 0 errors
- `uv run pytest -q`: 532 passed, 28 skipped
- `sh scripts/test_postgres_integration.sh -q`: 31 passed
- `sh scripts/test_compose_contract.sh`: PASS
- 실제 제품 Chromium: PASS

## Cleanup

일회성 응답 헤더 파일 외에 별도 디버거/추적 프로세스를 만들지 않았다. 제품 PostgreSQL과 8011 웹은 사용자가 요청한 시연 상태로 유지했다. `.env`, 비밀값, Alpaca, host 5432는 접근하지 않았다.
