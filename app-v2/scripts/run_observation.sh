#!/usr/bin/env bash
# 무인 완주 관측용 앱 기동 (completion-plan.md §④).
#
# 코드 작업용과 일부러 다르다:
#   - --reload 없음. 리로드는 프로세스를 다시 띄우고, 잡이 도는 중에 그러면
#     슬롯이 running으로 굳어 그날 영영 안 돈다. 관측이 오염된다.
#   - LLM은 local. mock 크리틱은 고정 0.82로 늘 통과해서 실 판단 경로를
#     한 번도 안 밟는다 — 그렇게 숨어 있던 결함이 실제로 있었다(11번).
#     가볍게 돌리고 싶으면 QUANTINUE_LLM_MODE=mock 으로 덮어쓰면 된다.
#   - DB는 5445(app-v2 전용). .env의 5444는 1차 DB이고 다른 작업자 것이다.
#
# 코드를 고칠 때는 이 인스턴스를 두고 **다른 포트**로 작업용을 띄운다.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${QUANTINUE_OBS_PORT:-8020}"
LOG="${QUANTINUE_OBS_LOG:-$PWD/observation.log}"
LOCK_DIR="${QUANTINUE_OBSERVATION_LOCK_DIR:-$PWD/.runtime/observation-owner.lock}"
CLAIM_DIR="$LOCK_DIR.claim"
CLAIM_HELD=0
CHILD_PID=""
CHILD_PGID=""

mkdir -p "$(dirname "$LOCK_DIR")"

release_claim() {
  if [[ "$CLAIM_HELD" == "1" ]]; then
    rmdir "$CLAIM_DIR" 2>/dev/null || true
    CLAIM_HELD=0
  fi
}

acquire_lock() {
  if ! mkdir "$CLAIM_DIR" 2>/dev/null; then
    printf 'observation owner claim contention: %s\n' "$CLAIM_DIR" >&2
    exit 1
  fi
  CLAIM_HELD=1

  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    ps -o lstart= -p "$$" | xargs > "$LOCK_DIR/start_identity"
    release_claim
    return
  fi

  if [[ ! -f "$LOCK_DIR/pid" ]]; then
    printf 'observation owner lock has no PID: %s\n' "$LOCK_DIR" >&2
    exit 1
  fi

  owner_pid="$(cat "$LOCK_DIR/pid")"
  if [[ ! "$owner_pid" =~ ^[0-9]+$ ]]; then
    printf 'observation owner lock has an invalid PID: %s\n' "$LOCK_DIR" >&2
    exit 1
  fi
  if kill -0 "$owner_pid" 2>/dev/null; then
    printf 'observation owner already running (PID %s)\n' "$owner_pid" >&2
    exit 1
  fi

  rm -rf "$LOCK_DIR"
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    printf 'observation owner lock contention: %s\n' "$LOCK_DIR" >&2
    exit 1
  fi
  printf '%s\n' "$$" > "$LOCK_DIR/pid"
  ps -o lstart= -p "$$" | xargs > "$LOCK_DIR/start_identity"
  release_claim
}

release_lock() {
  if [[ -f "$LOCK_DIR/pid" ]] && [[ "$(cat "$LOCK_DIR/pid")" == "$$" ]]; then
    rm -rf "$LOCK_DIR"
  fi
}

stop_owned_group() {
  local attempt=0

  if [[ -z "$CHILD_PGID" ]] || [[ "$CHILD_PGID" != "$CHILD_PID" ]]; then
    return
  fi
  kill -TERM -- "-$CHILD_PGID" 2>/dev/null || true
  while kill -0 -- "-$CHILD_PGID" 2>/dev/null && (( attempt < 20 )); do
    sleep 0.05
    attempt=$((attempt + 1))
  done
  if kill -0 -- "-$CHILD_PGID" 2>/dev/null; then
    kill -KILL -- "-$CHILD_PGID" 2>/dev/null || true
  fi
}

stop_child() {
  trap '' INT TERM HUP
  stop_owned_group
  if [[ -n "$CHILD_PID" ]]; then
    wait "$CHILD_PID" 2>/dev/null || true
  fi
  exit 143
}

trap release_claim EXIT
acquire_lock
trap release_lock EXIT
trap stop_child INT TERM HUP

set -m
QUANTINUE_DATA_MODE=public \
QUANTINUE_DATABASE_MODE=postgres \
QUANTINUE_DATABASE_URL="postgresql+asyncpg://quantinue:quantinue@127.0.0.1:5445/quantinue" \
QUANTINUE_LLM_MODE="${QUANTINUE_LLM_MODE:-local}" \
QUANTINUE_OPS_ALERTS="${QUANTINUE_OPS_ALERTS:-1}" \
QUANTINUE_BACKGROUND_WORKERS="${QUANTINUE_BACKGROUND_WORKERS:-1}" \
  uv run uvicorn quantinue.main:app --port "$PORT" > >(tee -a "$LOG") 2>&1 &
CHILD_PID=$!
set +m
CHILD_PGID="$(ps -o pgid= -p "$CHILD_PID" | tr -d ' ')"
if [[ "$CHILD_PGID" != "$CHILD_PID" ]]; then
  printf 'observation child did not enter an isolated process group (PID %s, PGID %s)\n' \
    "$CHILD_PID" "$CHILD_PGID" >&2
  kill -TERM "$CHILD_PID" 2>/dev/null || true
  wait "$CHILD_PID" 2>/dev/null || true
  exit 1
fi

if wait "$CHILD_PID"; then
  child_exit=0
else
  child_exit=$?
fi
stop_owned_group
exit "$child_exit"
