#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
LAUNCHER="$ROOT_DIR/scripts/run_observation.sh"
TEST_DIR="$(mktemp -d)"
BIN_DIR="$TEST_DIR/bin"
LOCK_DIR="$TEST_DIR/owner.lock"
LAUNCHER_PID=""
OWNED_PID=""
UNRELATED_PID=""

cleanup() {
  for pid in "$LAUNCHER_PID" "$OWNED_PID" "$UNRELATED_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  rm -rf "$TEST_DIR"
}
trap cleanup EXIT INT TERM

fail() {
  printf 'launcher orphan regression failed: %s\n' "$1" >&2
  exit 1
}

wait_for_file() {
  local path="$1"
  local attempt=0
  while [[ ! -s "$path" ]] && (( attempt < 100 )); do
    sleep 0.02
    attempt=$((attempt + 1))
  done
  [[ -s "$path" ]]
}

wait_for_health() {
  local attempt=0
  while ! curl -fsS "http://127.0.0.1:$owned_port/" > /dev/null 2>&1 && (( attempt < 100 )); do
    sleep 0.02
    attempt=$((attempt + 1))
  done
  (( attempt < 100 ))
}

start_launcher() {
  PATH="$BIN_DIR:$PATH" \
  QUANTINUE_OBSERVATION_LOCK_DIR="$LOCK_DIR" \
  QUANTINUE_OBS_LOG="$TEST_DIR/observation.log" \
  QUANTINUE_OBS_PORT="$owned_port" \
  FAKE_OWNED_PORT="$owned_port" \
  FAKE_OWNED_PID_FILE="$TEST_DIR/owned.pid" \
  FAKE_WRAPPER_PID_FILE="$TEST_DIR/wrapper.pid" \
  FAKE_SIGNAL_LOG="$TEST_DIR/signals.log" \
    "$LAUNCHER" > "$TEST_DIR/launcher.out" 2>&1 &
  LAUNCHER_PID=$!
}

mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/uv" <<'FAKE_UV'
#!/usr/bin/env bash
set -euo pipefail
trap 'printf "TERM %s\n" "$$" >> "$FAKE_SIGNAL_LOG"; exit 0' TERM
(trap '' TERM; exec python3 -m http.server "$FAKE_OWNED_PORT" --bind 127.0.0.1) > /dev/null 2>&1 &
printf '%s\n' "$!" > "$FAKE_OWNED_PID_FILE"
printf '%s\n' "$$" > "$FAKE_WRAPPER_PID_FILE"
while :; do sleep 1; done
FAKE_UV
chmod +x "$BIN_DIR/uv"

owned_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
unrelated_port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
python3 -m http.server "$unrelated_port" --bind 127.0.0.1 > /dev/null 2>&1 &
UNRELATED_PID=$!

start_launcher

wait_for_file "$TEST_DIR/owned.pid" || fail "owned listener PID was not published"
OWNED_PID="$(cat "$TEST_DIR/owned.pid")"
WRAPPER_PID="$(cat "$TEST_DIR/wrapper.pid")"
wait_for_health || fail "owned mock web process was not healthy"
ps -o pid=,ppid=,pgid=,sess=,command= -p "$LAUNCHER_PID","$WRAPPER_PID","$OWNED_PID","$UNRELATED_PID" > "$TEST_DIR/tree-before.txt"
printf '%s\n' 'process tree before TERM:'
cat "$TEST_DIR/tree-before.txt"

kill -TERM "$LAUNCHER_PID"
kill -TERM "$LAUNCHER_PID" 2>/dev/null || true
wait "$LAUNCHER_PID" || launcher_exit=$?
LAUNCHER_PID=""
[[ "${launcher_exit:-0}" == "143" ]] || fail "TERM exit was ${launcher_exit:-0}, expected 143"
[[ -s "$TEST_DIR/signals.log" ]] || fail "TERM was not forwarded to direct uv child"
[[ ! -e "$LOCK_DIR" ]] || fail "launcher lock remained after TERM"
if kill -0 "$OWNED_PID" 2>/dev/null; then
  ps -o pid=,ppid=,pgid=,sess=,command= -p "$OWNED_PID" > "$TEST_DIR/tree-after.txt"
  fail "owned listener survived graceful TERM (PID $OWNED_PID)"
fi
if curl -fsS "http://127.0.0.1:$owned_port/" > /dev/null 2>&1; then
  fail "owned port remained healthy without its launcher lock"
fi
kill -0 "$UNRELATED_PID" 2>/dev/null || fail "unrelated listener was killed"

rm -f "$TEST_DIR/owned.pid" "$TEST_DIR/wrapper.pid" "$TEST_DIR/signals.log"
start_launcher
wait_for_file "$TEST_DIR/owned.pid" || fail "guarded restart did not publish a listener PID"
OWNED_PID="$(cat "$TEST_DIR/owned.pid")"
wait_for_health || fail "guarded restart was not healthy"
set +e
PATH="$BIN_DIR:$PATH" \
QUANTINUE_OBSERVATION_LOCK_DIR="$LOCK_DIR" \
QUANTINUE_OBS_LOG="$TEST_DIR/contender.log" \
QUANTINUE_OBS_PORT="$owned_port" \
FAKE_OWNED_PORT="$owned_port" \
FAKE_OWNED_PID_FILE="$TEST_DIR/contender-owned.pid" \
FAKE_WRAPPER_PID_FILE="$TEST_DIR/contender-wrapper.pid" \
FAKE_SIGNAL_LOG="$TEST_DIR/contender-signals.log" \
  "$LAUNCHER" > "$TEST_DIR/contender.out" 2>&1
contender_exit=$?
set -e
[[ "$contender_exit" -ne 0 ]] || fail "guarded restart admitted a second owner"
[[ ! -e "$TEST_DIR/contender-owned.pid" ]] || fail "second owner reached the mock web process"
kill -TERM "$LAUNCHER_PID"
wait "$LAUNCHER_PID" || true
LAUNCHER_PID=""
[[ ! -e "$LOCK_DIR" ]] || fail "guarded restart left its lock"
if kill -0 "$OWNED_PID" 2>/dev/null; then
  fail "guarded restart left its listener"
fi

printf 'launcher process-tree regression passed\n'
