# Quantinue MVP

[정본 설계서](../docs/quantinue-integrated-design.html)의 01부터 11까지를 한 번 완주하는
로컬 우선 페이퍼 트레이딩 애플리케이션이다.

## 바로 실행

스케줄러는 설정된 역할 중 현재 실행 대상만 계산하는 명령 seam이다. MVP는 백그라운드
daemon·timer·worker를 시작하지 않으며, 운영자나 외부 스케줄러가 명시적으로 호출한다.

```bash
cp .env.example .env
uv sync
uv run uvicorn quantinue.main:app --reload
```

브라우저에서 `http://localhost:8000`을 열고 `50 → 20 → 20` 자동 분석을 실행한다. 기본값은
데이터 모드 `fixture`, 메모리 저장소, 결정론적 LLM, 모의 broker라 외부 키나 네트워크가
필요 없다. `QUANTINUE_DATA_MODE=public`으로 바꾸면 키 없이 공개 HTTP 공급자를 사용하는
01·02·04·05·06 경로가 선택된다. 공개 공급자의 실제 네트워크 가용성은 기본 테스트에서
가정하지 않으며, 테스트는 동일 HTTP 경계를 결정론적 응답으로 검증한다.

공개 데이터와 Ollama를 함께 사용하는 로컬 실행 예시는 다음과 같다. `.env`는 커밋하지
않으며 실제 provider key를 저장소 파일에 기록하지 않는다.

```dotenv
QUANTINUE_DATA_MODE=public
QUANTINUE_DATABASE_MODE=postgres
QUANTINUE_LLM_MODE=local
QUANTINUE_LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1
QUANTINUE_LOCAL_LLM_API_KEY=local-not-secret
QUANTINUE_LOCAL_LLM_MODEL=qwen3.6:35b-a3b-nvfp4
QUANTINUE_BROKER_MODE=mock
QUANTINUE_TRADING_ENABLED=false
```

PostgreSQL까지 실행하려면 다음을 사용한다.

```bash
docker compose up --build --wait
```

Docker 실행 화면은 로컬에서 `http://127.0.0.1:8011`, 같은 LAN에서는
`http://<이 Mac의 내부 IP>:8011`로 접속한다. 웹만 `0.0.0.0:8011`에 게시되므로
인증이 없는 1차 관리자 화면은 신뢰할 수 있는 내부망에서만 사용한다. DB는
`postgresql://quantinue:quantinue@127.0.0.1:5444/quantinue`이다. 컨테이너 사이에서는
`db:5432`를 사용한다. 이 프로젝트는 호스트 `5432`를 게시하거나 기존 컨테이너를 조작하지
않는다. 최초 DB 볼륨 생성 시 [db/schema.sql](db/schema.sql)이 자동 적용된다.

Compose의 웹 서비스는 Docker Desktop의 `host.docker.internal:11434`를 통해 Ollama에
접속한다. Broker는 `mock`, 외부 주문은 OFF로 고정되어 Quantinue 소유의 로컬 모의계좌만
변경한다. 웹 컨테이너만 재생성해도 PostgreSQL named volume의 실행·뉴스·포지션·주문·체결
기록은 유지된다.

종료할 때는 자신이 사용한 프로젝트 이름만 내린다. 데이터를 지울 의도가 없다면 `-v`를
붙이지 않는다.

```bash
docker compose down
```

## 1차 MVP 실행 흐름

한 번의 수동 실행은 백그라운드 스케줄러 없이 01부터 11까지 순서대로 진행된다.

- 01: 공개 NASDAQ 유니버스 50개를 선정하고 요청 종목 포함 여부를 검증한다.
- 02: 같은 유니버스에서 실제 일봉 기반 기술 분석 20개를 완성한다.
- 03: 결정론적 점수로 오늘의 후보 20개를 순위대로 선정한다.
- 04: 공통 거시경제 데이터를 한 번 수집한다.
- 05~11: 후보 20개 각각의 공시·뉴스·전략·비평·리스크·주문·회고를 순서대로 처리한다.
- 07~08: 매수·보유 제안과 독립 critic 검증을 수행한다.
- 09~10: USD 1,000 주문 노출 한도 안에서 로컬 모의계좌의 주문·체결을 처리한다.
- 11: no-trade 또는 체결 결과와 T+1~T+5 회고 절차를 기록한다.

관리자 화면은 11개 역할의 상태와 전체 상세 항목, 뉴스의 수집·관련·제외·대표 건수,
USD 1,000,000 모의계좌의 현금·보유·평단·평가손익·비중·주문·체결을 함께 표시한다.
1차 MVP는 매수 전용이며 실현손익은 해당 없음으로 표시한다.

## 폴더와 담당 역할

| 폴더 | 역할 | 1차 구현 | 담당자 교체 지점 |
|---|---|---|---|
| `role_01_universe_screener` | 1차 스크리너 | fixture 또는 공개 NASDAQ 유니버스 50개 | 공급자 정책 고도화 |
| `role_02_technical_analysis` | 기술 분석 | 공개 일봉 기반 지표 20개 | 지표·조정주가 고도화 |
| `role_03_daily_screener` | 2차 스크리너 | 결정론적 점수 기반 후보 20개 | 다계정 후보 정책 |
| `role_04_macro_analysis` | 매크로 분석 | fixture 또는 공개 FRED 계열 | 국면 정책 고도화 |
| `role_05_disclosure_analysis` | 공시 분석 | fixture 또는 공개 SEC 제출 + 구조화 분석 | 증분·장애 정책 고도화 |
| `role_06_news_analysis` | 뉴스 분석 | 종목별 공개 RSS 전체 보존·관련성·대표 1건 분석 | 뉴스 공급자 고도화 |
| `role_07_strategist` | 전략 종합 | 매수·보유 제안 | 정책·프롬프트 |
| `role_08_critic` | 크리틱 검증 | 독립 승인 점수 | 반증 규칙·픽스처 |
| `role_09_risk_portfolio` | 리스크·포트폴리오 | 리스크 수량·브래킷, 원자적 계좌/일 상한 | 실계좌 잔고 동기화 |
| `role_10_order_execution` | 주문·체결 | 모의 또는 Alpaca paper, 중복 방지 | 부분 체결 재조정 |
| `role_11_reviewer` | 리뷰·회고 | no-trade 및 거래일 T+1~T+5 증분 평가 | 백그라운드 호출기 |

각 역할은 `PipelineRole` 계약을 구현한다. 폴더 내부 코드를 바꿔도 입력과 출력 계약만 유지하면 오케스트레이터는 바뀌지 않는다.

## API

- `GET /`: 운영 대시보드
- `POST /runs`: 브라우저 폼 실행
- `POST /api/runs`: JSON 실행
- `GET /api/runs`: 최근 실행 목록
- `GET /api/runs/{run_id}`: 역할·근거·주문·현재 리뷰를 포함한 실행 상세
- `GET /api/portfolio`: 로컬 모의계좌·보유·주문·체결 조회
- `GET /health`: 현재 LLM·broker 안전 모드
- `POST /api/reviews/{signal_id}/process`: PostgreSQL 실행에서 해당 signal의 due T+1~T+5
  종가를 멱등 처리하고, 다섯 번째 거래일 종가 이후 리뷰를 확정. 처리 후 같은 실행의 상세
  API와 새로고침한 대시보드는 확정된 `hit | miss` 리뷰를 표시한다.

## 스케줄과 저장 경계

`config/pipeline.yaml`의 `mvp.schedule`은 Pydantic으로 검증되는 런타임 역할 주기의 유일한
정본이다. 역할 주기에는 환경변수 override가 없다. 판단 임계값과 손절·익절 비율도 YAML이
소유한다. 모델 이름은 YAML이 adapter별 기본값을 제공하되, 명시적으로 설정한
`QUANTINUE_MOCK_MODEL`, `QUANTINUE_OPENAI_MODEL`, `QUANTINUE_LOCAL_LLM_MODEL`이 우선한다.
계좌별 일 신규 주문 상한은 운영 경계이므로 `QUANTINUE_DAILY_NEW_ORDER_CAP`이 YAML 기본값을
덮어쓴다.
`load_pipeline_document(...).due_role_scheduler().due_roles(...)` 명령 seam은 주어진 시각과
마지막 실행 시각으로 04·05·06·07 중 due 역할만 반환한다. 애플리케이션 자체는 daemon,
timer, worker를 시작하지 않는다. 운영자 또는 외부 스케줄러가 due 역할 실행과 위 T+5
처리 endpoint 호출을 담당한다.

PostgreSQL 모드에서는 01 universe, 02 technical, 03 daily pick, 04 macro를 각 역할 완료
경계에서 해당 정본 테이블에 저장한다. 이어 공개/fixture 원천 공시와 뉴스, 두 분석 signal,
07 strategist 결과를 나타내는 strategist signal, 08 critic verdict, paper account, 주문·체결,
T+1~T+5 스냅샷과 최종 review를 멱등 저장한다. 08은 07 판단을 다시 합성하지 않고, 저장된
strategist signal에 독립 critic verdict를 연결한다. 또한 08에서 universe나 daily pick 부모를
placeholder로 만들거나 backfill하지 않고 01·03이 저장한 실제 행을 사용한다. 매수하지 않는 판단은 주문을 만들지 않고
`no_trade` 리뷰 계보를 유지한다.
신규 주문은 `(account, trade date)` 상한을 저장소에서 원자적으로 예약하고, 동일 signal의
재실행은 안정된 idempotency key로 중복 broker 제출을 막는다. Alpaca bracket 응답의 parent,
stop, take-profit leg ID도 정본 주문에 보존한다.

## 검증

```bash
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
uv run pytest -q
sh scripts/test_postgres_integration.sh -q
sh scripts/test_compose_contract.sh
docker compose config
```

구현 과정에서 추가로 판단한 내용과 미확정 사항은 `IMPLEMENTATION_ASSUMPTIONS.md`에만 기록한다.
설계 요구와 구현·테스트의 추적표는 [docs/DESIGN_IMPLEMENTATION_MAP.md](docs/DESIGN_IMPLEMENTATION_MAP.md),
실제 공급자 opt-in 절차는 [docs/REAL_KEY_TESTING.md](docs/REAL_KEY_TESTING.md)를 참고한다.
