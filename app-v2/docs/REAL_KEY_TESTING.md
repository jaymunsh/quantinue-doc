# 실제 공급자 opt-in 통합 테스트

기본 실행과 기본 테스트는 외부 키를 사용하지 않는다. 아래 절차는 비용 또는 paper 계좌의
상태 변경을 이해하고 명시적으로 선택한 경우에만 수행한다. 모든 예시는 Alpaca **paper** 전용이다.

기본 `uv run pytest`는 `real_key` 마커를 사유와 함께 skip한다. OpenAI와 Alpaca 테스트는
비용과 계좌 상태 변경 범위를 분리하기 위해 각각 실행한다.

## 1. 공통 준비

```bash
cp .env.example .env
chmod 600 .env
```

`.env`는 커밋하지 않는다. 실행 전 `git status --short`에서 나타나지 않는지 확인한다. 로그,
스크린샷, 이슈 본문에도 키나 Authorization 헤더를 남기지 않는다.

## 2. OpenAI opt-in

`.env`에 `QUANTINUE_LLM_MODE=openai`, `QUANTINUE_OPENAI_API_KEY`, 사용할 모델을 설정한다.
호출은 과금될 수 있으므로 계정의 예산/사용량 한도를 먼저 확인한다. broker는 계속 mock이고
`QUANTINUE_TRADING_ENABLED=false`로 둔다.

```bash
uv run uvicorn quantinue.main:app --port 8000
curl -fsS http://127.0.0.1:8000/health
curl -fsS -X POST http://127.0.0.1:8000/api/runs \
  -H 'content-type: application/json' -d '{"ticker":"NVDA"}'
```

한 번의 소량 호출만 확인하고 서버를 종료한다. 응답에 실행 ID와 완료/실패 상태가 있으며,
키가 출력되지 않는 것이 합격 조건이다.

```bash
QUANTINUE_RUN_REAL_KEY_TESTS=1 uv run pytest tests/real_key/test_openai_real_key.py -v
```

이 테스트는 출력 토큰을 16개로 제한한 실제 스키마 호출이며 소액 과금/사용량이 발생한다.

## 3. 로컬 LLM opt-in

Ollama라면 `ollama serve`와 `ollama pull qwen2.5:7b` 후 `.env`에서 mode를 `local`로 바꾼다.
호스트 uv 실행은 `http://127.0.0.1:11434/v1`, Docker Desktop 컨테이너에서는
`http://host.docker.internal:11434/v1`을 사용한다. 위 OpenAI와 같은 curl 시나리오로 확인한다.

## 4. Alpaca paper opt-in

paper dashboard에서 별도 paper 키를 발급한다. `.env`에 다음만 설정한다.

```dotenv
QUANTINUE_BROKER_MODE=alpaca
QUANTINUE_ALPACA_API_KEY=<paper key>
QUANTINUE_ALPACA_SECRET_KEY=<paper secret>
QUANTINUE_ALPACA_BASE_URL=https://paper-api.alpaca.markets
QUANTINUE_TRADING_ENABLED=true
QUANTINUE_CONTROL_ROOM_TOKEN=<local control token>
```

live URL은 허용되지 않는다. control-room token은 사용자가 별도로 정한 비밀값이며 API 요청에는
`X-Quantinue-Control-Token` 헤더, 서버 렌더링 폼에는 토큰 입력으로만 전달한다. 이 작업은 paper 주문을 제출하거나 취소하여 paper 계좌 상태를
변경할 수 있다. 정규장 여부, 기존 미체결 주문, 종목, 수량을 paper dashboard에서 먼저 확인하고
한 번만 실행한다. 실행 후 dashboard에서 생성된 주문 ID/상태를 확인하고 불필요한 미체결 paper
주문을 취소한다. 반복 실행 시 동일 주문 키가 중복 제출을 막는지 확인한다.

읽기 전용 credential preflight는 PAPER 계좌 상태를 바꾸지 않는다.

```bash
QUANTINUE_RUN_REAL_KEY_TESTS=1 uv run pytest \
  tests/real_key/test_alpaca_real_key.py -k credentials -v
```

주문 probe는 추가로 `QUANTINUE_RUN_ALPACA_ORDER_TEST=1`과
`QUANTINUE_TEST_DATABASE_URL`을 요구한다. 주문은 PostgreSQL의 durable reservation을 반드시
사용하고 수량 1로 제출된다. 테스트가 `finally`에서 취소를 시도해도 네트워크 단절 시 남을 수
있으므로 PAPER dashboard에서 미체결 주문을 확인하고 수동 정리한다. 기본 ticker는 `SPY`이고
`QUANTINUE_ALPACA_TEST_TICKER`로 변경할 수 있다.

```bash
QUANTINUE_RUN_REAL_KEY_TESTS=1 QUANTINUE_RUN_ALPACA_ORDER_TEST=1 \
  uv run pytest tests/real_key/test_alpaca_real_key.py -k order -v
```

## 5. PostgreSQL integration runner

`scripts/test_postgres_integration.sh`는 고유 컨테이너를 만들고 `127.0.0.1:55400-55499` 중
비어 있는 임시 포트만 컨테이너 `5432`에 연결한다. 종료·실패·인터럽트 시 해당 컨테이너만
삭제한다. `localhost:5432`, 앱의 `5444`, 기존 컨테이너, 기존 볼륨은 조회하거나 조작하지 않는다.

```bash
./scripts/test_postgres_integration.sh
```

## 6. 정리와 실패 처리

- 서버를 종료하고 `.env`에서 mode를 mock, trading을 false로 되돌린다.
- paper dashboard에서 미체결 주문이 0인지 확인하고 필요한 주문만 수동 취소한다.
- 키가 노출되었다면 즉시 provider에서 폐기/재발급하고 노출된 기록을 정리한다.
- HTTP timeout/429/5xx는 제한된 재시도 후 실패 체크포인트로 남아야 한다. 무한 반복하지 않는다.
- 실제 키 테스트는 CI와 `uv run pytest` 기본 실행에 포함하지 않는다. opt-in 실행 결과는 비밀을
  제거한 실행 ID, 상태, 타임스탬프만 증거로 보관한다.
