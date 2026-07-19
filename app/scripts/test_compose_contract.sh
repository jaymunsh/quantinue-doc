#!/bin/sh
set -eu

rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT

docker compose config >"$rendered"

# Given: the checked-in Compose definition rendered with Docker Compose.
# When: its externally visible networking and initialization contract is inspected.
# Then: Postgres stays loopback-only, the admin web port is available to the LAN,
# the safe local-LLM/mock-broker contract is explicit, and Postgres loads the schema.
grep -F 'host_ip: 127.0.0.1' "$rendered" >/dev/null
grep -F 'host_ip: 0.0.0.0' "$rendered" >/dev/null
grep -F 'published: "5444"' "$rendered" >/dev/null
grep -F 'published: "8011"' "$rendered" >/dev/null
grep -F 'target: /docker-entrypoint-initdb.d/001-schema.sql' "$rendered" >/dev/null
grep -F 'postgresql+asyncpg://quantinue:quantinue@db:5432/quantinue' "$rendered" >/dev/null
grep -F 'QUANTINUE_DATA_MODE: public' "$rendered" >/dev/null
grep -F 'QUANTINUE_LLM_MODE: local' "$rendered" >/dev/null
grep -F 'QUANTINUE_LOCAL_LLM_BASE_URL: http://host.docker.internal:8888/v1' "$rendered" >/dev/null
grep -F 'QUANTINUE_BROKER_MODE: mock' "$rendered" >/dev/null
grep -F 'QUANTINUE_TRADING_ENABLED: "false"' "$rendered" >/dev/null

[ "$(grep -c 'host_ip: 127.0.0.1' "$rendered")" -eq 1 ]
[ "$(grep -c 'host_ip: 0.0.0.0' "$rendered")" -eq 1 ]
[ "$(grep -c 'published:' "$rendered")" -eq 2 ]
[ "$(grep -c 'target: 5432' "$rendered")" -eq 1 ]
[ "$(grep -c 'target: 8000' "$rendered")" -eq 1 ]

if grep -F 'published: "5432"' "$rendered" >/dev/null; then
  echo 'unsafe host port 5432 publication detected' >&2
  exit 1
fi

if grep -F 'localhost:5432' "$rendered" >/dev/null; then
  echo 'unsafe localhost:5432 connection detected' >&2
  exit 1
fi

echo 'compose contract: PASS'
