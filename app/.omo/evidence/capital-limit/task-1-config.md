# Task 1: first-cycle app-order exposure configuration

## Result

PASS. The validated environment contract now defaults to an app-managed planned buy-order
exposure of `1000.00` USD and one daily new-order attempt. The setting rejects non-positive
and non-cent Decimal input. The configured factory forwards both settings into Role 09 without
changing Role 09 sizing or store reservation behavior.

## TDD evidence

- Red: `uv run pytest -q tests/unit/test_config.py tests/unit/test_persistence_factory.py`
  initially reported five expected failures: missing exposure setting, missing Role 09 injection,
  and invalid values not rejected.
- Green: `PYTHONDONTWRITEBYTECODE=1 uv run pytest -q tests/unit/test_config.py tests/unit/test_persistence_factory.py tests/unit/test_pipeline_policy.py`
  passed: `32 passed`.

## Static checks

- `uv run ruff format --check` on the seven changed Python files: pass.
- `uv run ruff check` on the seven changed Python files: pass.
- `uv run basedpyright` on the seven changed Python files: `0 errors, 0 warnings, 0 notes`.

## Manual configuration check

Executed with safe in-process overrides for mock LLM, fixture data, mock broker, disabled
trading, and memory persistence:

```text
uv run python -c 'from quantinue.core.config import Settings; print(Settings().max_app_order_exposure_usd)'
1000.00
```

## Cleanup and scope

- No `.env`, provider credential, Docker runtime, database container, volume, network, or
  localhost:5432 resource was inspected or changed.
- No scheduler, UI, order sizing, reservation algorithm, broker balance/portfolio access, or
  order execution behavior was changed.
- The new summary/result dataclasses intentionally make no broker-cash or fill-cost claim;
  later store and UI tasks consume them.

## Artifact cleanup receipt

The targeted reverse binary patch restored the seven tracked bytecode artifacts generated during
this task to their repository versions. `git status --short` afterwards contains no modified
`*.pyc` path, and `git diff --check` passes.

- `src/quantinue/core/__pycache__/config.cpython-311.pyc`
- `src/quantinue/db/__pycache__/contracts.cpython-311.pyc`
- `src/quantinue/orchestration/__pycache__/factory.cpython-311.pyc`
- `src/quantinue/orchestration/__pycache__/policy.cpython-311.pyc`
- `src/quantinue/roles/role_09_risk_portfolio/__pycache__/service.cpython-311.pyc`
- `tests/unit/__pycache__/test_config.cpython-311-pytest-9.1.1.pyc`
- `tests/unit/__pycache__/test_persistence_factory.cpython-311-pytest-9.1.1.pyc`
