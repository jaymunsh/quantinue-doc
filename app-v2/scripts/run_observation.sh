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

QUANTINUE_DATA_MODE=public \
QUANTINUE_DATABASE_MODE=postgres \
QUANTINUE_DATABASE_URL="postgresql+asyncpg://quantinue:quantinue@127.0.0.1:5445/quantinue" \
QUANTINUE_LLM_MODE="${QUANTINUE_LLM_MODE:-local}" \
QUANTINUE_OPS_ALERTS="${QUANTINUE_OPS_ALERTS:-1}" \
  exec uv run uvicorn quantinue.main:app --port "$PORT" 2>&1 | tee -a "$LOG"
