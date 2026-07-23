#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
LAUNCHER="$ROOT_DIR/scripts/run_observation.sh"
TEST_DIR="$(mktemp -d)"
LOCK_DIR="$TEST_DIR/owner.lock"
CALLS="$TEST_DIR/calls.tsv"
BIN_DIR="$TEST_DIR/bin"
FIRST_PID=""

cleanup() {
  if [[ -n "$FIRST_PID" ]] && kill -0 "$FIRST_PID" 2>/dev/null; then
    kill -TERM "$FIRST_PID" 2>/dev/null || true
    wait "$FIRST_PID" 2>/dev/null || true
  fi
  rm -rf "$TEST_DIR"
}
trap cleanup EXIT INT TERM

mkdir -p "$BIN_DIR"
ln -s "$ROOT_DIR/tests/shell/fake_uv.sh" "$BIN_DIR/uv"

wait_for_file() {
  local path="$1"
  local attempts=0
  while [[ ! -e "$path" ]] && (( attempts < 50 )); do
    sleep 0.02
    attempts=$((attempts + 1))
  done
  [[ -e "$path" ]]
}

fail() {
  printf 'launcher test failed: %s\n' "$1" >&2
  exit 1
}

start_owner() {
  PATH="$BIN_DIR:$PATH" \
  QUANTINUE_OBSERVATION_LOCK_DIR="$LOCK_DIR" \
  QUANTINUE_OBS_LOG="$TEST_DIR/observation.log" \
  FAKE_UV_CALLS="$CALLS" \
  FAKE_UV_BLOCK=1 \
    "$LAUNCHER" >"$TEST_DIR/first.out" 2>&1 &
  FIRST_PID=$!
  wait_for_file "$CALLS"
}

start_owner
[[ -d "$LOCK_DIR" ]] || fail "first owner did not create lock"
[[ "$(cut -f1 "$CALLS")" == "1" ]] || fail "owner did not opt into workers"

set +e
PATH="$BIN_DIR:$PATH" \
QUANTINUE_OBSERVATION_LOCK_DIR="$LOCK_DIR" \
QUANTINUE_OBS_LOG="$TEST_DIR/second.log" \
FAKE_UV_CALLS="$CALLS" \
  "$LAUNCHER" >"$TEST_DIR/second.out" 2>&1
second_exit=$?
set -e
[[ "$second_exit" -ne 0 ]] || fail "second owner succeeded"
[[ "$(wc -l < "$CALLS" | tr -d ' ')" == "1" ]] || fail "second owner reached uv"
[[ -d "$LOCK_DIR" ]] || fail "second owner removed live lock"

kill -TERM "$FIRST_PID"
wait "$FIRST_PID" || true
FIRST_PID=""
[[ ! -e "$LOCK_DIR" ]] || fail "graceful exit left lock"

mkdir "$LOCK_DIR"
printf '%s\n' "$$" > "$LOCK_DIR/pid"
printf '%s\n' "synthetic-live" > "$LOCK_DIR/start_identity"
set +e
PATH="$BIN_DIR:$PATH" \
QUANTINUE_OBSERVATION_LOCK_DIR="$LOCK_DIR" \
QUANTINUE_OBS_LOG="$TEST_DIR/live.log" \
FAKE_UV_CALLS="$CALLS" \
  "$LAUNCHER" >"$TEST_DIR/live.out" 2>&1
live_exit=$?
set -e
[[ "$live_exit" -ne 0 ]] || fail "synthetic live owner was ignored"
[[ -d "$LOCK_DIR" ]] || fail "synthetic live lock was removed"

rm -rf "$LOCK_DIR"
mkdir "$LOCK_DIR"
printf '%s\n' "not-a-pid" > "$LOCK_DIR/pid"
set +e
PATH="$BIN_DIR:$PATH" \
QUANTINUE_OBSERVATION_LOCK_DIR="$LOCK_DIR" \
QUANTINUE_OBS_LOG="$TEST_DIR/malformed.log" \
FAKE_UV_CALLS="$CALLS" \
  "$LAUNCHER" >"$TEST_DIR/malformed.out" 2>&1
malformed_exit=$?
set -e
[[ "$malformed_exit" -ne 0 ]] || fail "malformed lock owner was ignored"
[[ -d "$LOCK_DIR" ]] || fail "malformed lock was removed"

rm -rf "$LOCK_DIR"
mkdir "$LOCK_DIR"
printf '%s\n' "99999999" > "$LOCK_DIR/pid"
printf '%s\n' "synthetic-dead" > "$LOCK_DIR/start_identity"
PATH="$BIN_DIR:$PATH" \
QUANTINUE_OBSERVATION_LOCK_DIR="$LOCK_DIR" \
QUANTINUE_OBS_LOG="$TEST_DIR/stale.log" \
QUANTINUE_OPS_ALERTS=0 \
FAKE_UV_CALLS="$CALLS" \
  "$LAUNCHER" >"$TEST_DIR/stale.out" 2>&1
[[ ! -e "$LOCK_DIR" ]] || fail "stale lock was not cleaned"
[[ "$(tail -n 1 "$CALLS" | cut -f1-2)" == $'1\t0' ]] || fail "env flags leaked"

PATH="$BIN_DIR:$PATH" \
QUANTINUE_OBSERVATION_LOCK_DIR="$LOCK_DIR" \
QUANTINUE_OBS_LOG="$TEST_DIR/web-only.log" \
QUANTINUE_BACKGROUND_WORKERS=0 \
QUANTINUE_OPS_ALERTS=0 \
FAKE_UV_CALLS="$CALLS" \
  "$LAUNCHER" >"$TEST_DIR/web-only.out" 2>&1
[[ "$(tail -n 1 "$CALLS" | cut -f1-2)" == $'0\t0' ]] || fail "web-only worker flag was overridden"

calls_before_race="$(wc -l < "$CALLS" | tr -d ' ')"
mkdir "$LOCK_DIR"
printf '%s\n' "99999999" > "$LOCK_DIR/pid"
printf '%s\n' "synthetic-dead-race" > "$LOCK_DIR/start_identity"
race_pids=()
for race_index in 1 2 3 4 5 6 7 8; do
  PATH="$BIN_DIR:$PATH" \
  QUANTINUE_OBSERVATION_LOCK_DIR="$LOCK_DIR" \
  QUANTINUE_OBS_LOG="$TEST_DIR/race-$race_index.log" \
  FAKE_UV_CALLS="$CALLS" \
  FAKE_UV_BLOCK=1 \
    "$LAUNCHER" >"$TEST_DIR/race-$race_index.out" 2>&1 &
  race_pids+=("$!")
done
sleep 0.5
race_calls=$(($(wc -l < "$CALLS" | tr -d ' ') - calls_before_race))
[[ "$race_calls" == "1" ]] || fail "stale recovery launched $race_calls uv children"
for race_pid in "${race_pids[@]}"; do
  kill -TERM "$race_pid" 2>/dev/null || true
done
for race_pid in "${race_pids[@]}"; do
  wait "$race_pid" 2>/dev/null || true
done
[[ ! -e "$LOCK_DIR" ]] || fail "stale recovery race left owner lock"
[[ ! -e "$LOCK_DIR.claim" ]] || fail "stale recovery race left claim lock"

set +e
PATH="$BIN_DIR:$PATH" \
QUANTINUE_OBSERVATION_LOCK_DIR="$LOCK_DIR" \
QUANTINUE_OBS_LOG="$TEST_DIR/failed-child.log" \
FAKE_UV_CALLS="$CALLS" \
FAKE_UV_EXIT=23 \
  "$LAUNCHER" >"$TEST_DIR/failed-child.out" 2>&1
child_exit=$?
set -e
[[ "$child_exit" == "23" ]] || fail "launcher hid child exit $child_exit"
[[ ! -e "$LOCK_DIR" ]] || fail "failed child left lock"

printf 'launcher ownership tests passed\n'
