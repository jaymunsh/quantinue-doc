#!/usr/bin/env bash
set -euo pipefail

name="quantinue-test-pg-$(date +%s)-$$"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
port="$(python3 - <<'PY'
import socket

for candidate in range(55400, 55500):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", candidate))
        except OSError:
            continue
        print(candidate)
        break
else:
    raise SystemExit("no free test port in 55400-55499")
PY
)"

cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run --rm -d --name "$name" \
  -e POSTGRES_DB=quantinue_test \
  -e POSTGRES_USER=quantinue_test \
  -e POSTGRES_PASSWORD=test-only \
  -p "127.0.0.1:${port}:5432" \
  -v "$root/db/schema.sql:/docker-entrypoint-initdb.d/001-schema.sql:ro" \
  postgres:17-alpine >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$name" pg_isready -U quantinue_test -d quantinue_test >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

docker exec "$name" pg_isready -U quantinue_test -d quantinue_test >/dev/null
export QUANTINUE_TEST_DATABASE_URL="postgresql+asyncpg://quantinue_test:test-only@127.0.0.1:${port}/quantinue_test"
export QUANTINUE_BROKER_MODE=mock
export QUANTINUE_DATA_MODE=fixture
export QUANTINUE_LLM_MODE=mock
export QUANTINUE_LOCAL_LLM_MODEL=qwen2.5:7b
export QUANTINUE_MOCK_MODEL=deterministic-mock-v1
export QUANTINUE_OPENAI_MODEL=gpt-4o-mini
export QUANTINUE_TRADING_ENABLED=false
cd "$root"
uv run pytest tests/integration "$@"
