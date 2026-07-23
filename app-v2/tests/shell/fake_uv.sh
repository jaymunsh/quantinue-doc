#!/usr/bin/env bash
set -euo pipefail

printf '%s\t%s\t%s\n' \
  "${QUANTINUE_BACKGROUND_WORKERS:-unset}" \
  "${QUANTINUE_OPS_ALERTS:-unset}" \
  "$*" >> "${FAKE_UV_CALLS:?}"

if [[ "${FAKE_UV_BLOCK:-0}" == "1" ]]; then
  trap 'exit 0' TERM INT
  while :; do
    sleep 1
  done
fi

exit "${FAKE_UV_EXIT:-0}"
