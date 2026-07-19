# Task 4: app-order exposure pipeline application

## Result

PASS for the in-memory Role 09/Role 10 pipeline path.

- Role 09 supplies the configured `Decimal` app-order exposure amount as the legacy sizing
  equity value at the one float-only risk-math boundary.
- A rejected atomic reservation changes the plan to zero quantity, does not invoke the broker,
  and emits `수량 0, 앱 주문 계획 노출 한도 또는 일일 신규 주문 한도 도달`.
- Role 10 uses the same selected run store and reconciles one returned broker result exactly
  once: `accepted` and `submitted` map to `submitted`, `filled` maps to `filled`, `rejected`
  maps to `failed`, and `canceled` maps to `canceled`.
- The behavior is app-owned planned exposure only. It does not claim a market-order fill price
  or actual broker cash is capped.

## TDD and focused verification

Red first:

```text
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q tests/unit/test_capital_limit_pipeline.py
4 failed
```

The failures were the expected missing pipeline behavior: the old fixed `10_000` risk sizing
could not pass the configured app cap, the rejection lacked an exposure-cap summary, and Role
10 did not call the reconciliation seam.

Green verification:

```text
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q \
  tests/unit/test_capital_limit_pipeline.py \
  tests/unit/test_risk_order_contracts.py \
  tests/unit/test_daily_order_cap.py \
  tests/unit/test_broker_provider.py \
  tests/unit/test_persistence_factory.py
49 passed in 1.18s

PYTHONDONTWRITEBYTECODE=1 uv run ruff format --check <Todo-4 paths>
4 files already formatted

PYTHONDONTWRITEBYTECODE=1 uv run ruff check <Todo-4 paths>
All checks passed!

PYTHONDONTWRITEBYTECODE=1 uv run basedpyright <Todo-4 paths>
0 errors, 0 warnings, 0 notes

git diff --check
pass
```

The new test matrix covers configured `$1,000` sizing, a fully pre-reserved app cap with zero
broker calls, and accepted/filled/rejected lifecycle mapping with a replayed pipeline request
that observes exactly one reconciliation.

## Minimal runtime QA

An AnyIO in-memory pipeline driver used the real deterministic analyzer, real Role 09/Role 10,
real `InMemoryRunStore`, and `MockBroker`; it did not read broker credentials or contact a
network service.

```text
{
  'normal_quantity': 1,
  'normal_status': 'filled',
  'normal_planned_exposure': '128.40',
  'cap_rejected_order': True,
  'cap_rejected_summary': '수량 0, 앱 주문 계획 노출 한도 또는 일일 신규 주문 한도 도달'
}
```

## Scope and cleanup

- No `.env`, credential, product Docker, host `localhost:5432`, container, volume, or network
  was inspected, started, stopped, or changed.
- No PostgreSQL file was edited. PostgreSQL lifecycle parity remains with its dedicated owner.
- No secret value was generated or recorded.

## Follow-up: canonical persistent status projection

PASS. A PostgreSQL integration review found that Role 10's store reconciliation was canonical
while the `OrderResult` retained raw broker `accepted` or `rejected` status. Role 10 now maps
the broker result once before both uses it:

- `accepted` and `submitted` become `submitted`
- `filled` remains `filled`
- `rejected` and `failed` become `failed`
- `canceled` remains `canceled`
- `planned` remains `planned`

The normalized value is used for the run context, terminal/API projection, domain lifecycle,
stage summary, and app-exposure reconciliation. Unknown values fail closed at the Role 10
boundary with a typed validation failure. No Postgres source file was changed.

Focused follow-up verification:

```text
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q \
  tests/unit/test_capital_limit_pipeline.py \
  tests/unit/test_broker_provider.py \
  tests/unit/test_risk_order_contracts.py \
  tests/unit/test_daily_order_cap.py \
  tests/integration/test_domain_lifecycle_pipeline.py
52 passed in 1.23s

uv run ruff format --check src/quantinue/roles/role_10_order_execution/service.py \
  tests/unit/test_capital_limit_pipeline.py
2 files already formatted

uv run ruff check src/quantinue/roles/role_10_order_execution/service.py \
  tests/unit/test_capital_limit_pipeline.py
All checks passed!

uv run basedpyright src/quantinue/roles/role_10_order_execution/service.py \
  tests/unit/test_capital_limit_pipeline.py
0 errors, 0 warnings, 0 notes
```

An AnyIO minimal runtime using real pipeline roles and in-memory storage observed:

```text
{'accepted': 'submitted', 'rejected': 'failed'}
```
