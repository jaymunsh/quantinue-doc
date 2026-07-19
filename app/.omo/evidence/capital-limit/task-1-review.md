# Task 1 independent contract review

## Verdict

**CONFIRMED.** Todo 1 meets its stated contract boundary. The new exposure value is a
validated `Decimal` configuration value with a `1000.00` default, the daily-new-order
default is `1`, and `build_configured_orchestrator()` carries both explicit settings values
into Role 09. Role 09 retains its existing `10_000` sizing input; that is intentional for
Todo 1's passive-field scope and remains Todo 4 work, not evidence that this step has
implemented the cap.

## Independent review scope

Reviewed only the Todo 1 implementation/test paths:

- `.env.example`
- `src/quantinue/core/config.py`
- `src/quantinue/db/contracts.py`
- `src/quantinue/orchestration/factory.py`
- `src/quantinue/orchestration/policy.py`
- `src/quantinue/roles/role_09_risk_portfolio/service.py`
- `tests/unit/test_config.py`
- `tests/unit/test_persistence_factory.py`

Also inspected the unmodified policy YAML and role call sites only to establish whether a
stale hard-coded path bypassed this Todo's configuration injection.

## Contract findings

- `AppOrderExposureUsd` is an annotated `Decimal` with `gt=0`, `max_digits=12`, and
  `decimal_places=2`. It rejects zero, negative, over-cent, NaN, and infinity values.
- `Settings.max_app_order_exposure_usd` defaults to `Decimal("1000.00")`; both
  `Settings.daily_new_order_cap` and `PipelinePolicy.daily_new_order_cap` default to `1`.
- The configured factory copies both validated Settings values into the loaded policy, then
  passes both by name to `RiskPortfolio`. A real role construction probe observed
  `875.55/1`, rather than only trusting the monkeypatched factory test.
- The new summary/reservation dataclasses carry `Decimal` values and explicitly describe
  app-owned planned-order exposure. They contain no broker cash, portfolio, provider, or
  credential field.
- `.env.example` documents the new app-managed exposure wording and first-cycle daily guard.
  `git diff -- .env` was empty; no tracked `.env` change or secret-bearing output was
  produced.
- No Docker command, container/network/volume operation, database operation, or
  `localhost:5432` access occurred in this review.

## Independent commands and results

```text
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q \
  tests/unit/test_config.py tests/unit/test_persistence_factory.py \
  tests/unit/test_pipeline_policy.py
32 passed in 1.38s

uv run ruff format --check <seven Todo-1 Python paths>
7 files already formatted

uv run ruff check <seven Todo-1 Python paths>
All checks passed!

uv run basedpyright <seven Todo-1 Python paths>
0 errors, 0 warnings, 0 notes

env -i ... uv run python - <<'PY'  # isolated Settings default probe
isolated-defaults=1000.00/1

env -i ... uv run python - <<'PY'  # 0, -0.01, 1000.001, NaN, Infinity
invalid-cent-nonpositive-nonfinite=rejected

PYTHONDONTWRITEBYTECODE=1 uv run python - <<'PY'  # real factory/Role 09 probe
factory-to-role09=875.55/1

git diff --check && git diff --quiet -- .env
no-tracked-env-diff
```

Two first manual-probe attempts used `python -c` with a class statement and stopped at a
Python `SyntaxError` before importing or exercising project code. They were replaced by the
shown isolated here-doc probes, which passed. This was a command-shape correction, not an
application failure.

## Residual and anti-misleading-state check

- The local ignored `.env` may still select an older daily cap at runtime; Todo 1 correctly
  leaves it untouched. The user must add the new exposure variable and choose the daily cap
  in their own untracked file before a live configuration uses it.
- The cap is not enforced or used for order sizing yet. The `equity=10_000` line remains in
  Role 09 and must be replaced only by Todo 4 after atomic store reservation work in Todos
  2 and 3 lands. Treating this Todo as a runnable $1,000 protection would be misleading.
- Working tree is intentionally dirty with the Todo 1 paths, plan/evidence state, and
  generated artifact cleanup already in progress. No unrelated tracked file was edited by
  this reviewer.
