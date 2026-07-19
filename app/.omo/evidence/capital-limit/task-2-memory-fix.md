# Task 2 fix: Decimal money and replay identity

## Remediated review findings

- `DailyOrderReservation` now stores only positive, finite, cent-normalized `Decimal` money values for entry, stop, take-profit, and app cap. Its exact reference notional is derived from those Decimals only.
- Role 09 parses its legacy numeric plan values through the typed Decimal-cent boundary before it constructs the persistence request.
- Reservation results now expose `acquired`, `replayed`, or `rejected`; there is no ambiguous success boolean.
- A replay is accepted only when the full immutable reservation request is identical. Different account, ticker, notional, or any other immutable field under the same idempotency key is rejected and preserves the requesting account's own safe summary.

## Regression verification

| Check | Result |
| --- | --- |
| `uv run pytest -q tests/unit/test_daily_order_cap.py` | `20 passed` |
| `uv run ruff format --check src/quantinue/db/contracts.py src/quantinue/db/memory.py src/quantinue/db/memory_exposure.py src/quantinue/roles/role_09_risk_portfolio/service.py tests/unit/test_daily_order_cap.py` | pass |
| `uv run ruff check src/quantinue/db/contracts.py src/quantinue/db/memory.py src/quantinue/db/memory_exposure.py src/quantinue/roles/role_09_risk_portfolio/service.py tests/unit/test_daily_order_cap.py` | pass |
| `uv run basedpyright src/quantinue/db/contracts.py src/quantinue/db/memory.py src/quantinue/db/memory_exposure.py src/quantinue/roles/role_09_risk_portfolio/service.py tests/unit/test_daily_order_cap.py` | `0 errors, 0 warnings, 0 notes` |
| `git diff --check` | pass |

New regression coverage rejects fractional-cent and non-finite Decimal values at the store contract, rejects fractional-cent and non-finite float values at the Role 09 parser, distinguishes exact replay from new acquisition, and rejects idempotency-key collisions that differ by account, ticker, or notional.

## Manual store QA

```text
{'acquired': 'acquired', 'cross_account_collision': 'rejected', 'collision_exposure': '0', 'fractional_cent_rejected': True}
```

## Scope and cleanup

- No Docker, localhost:5432, PostgreSQL, `.env`, network broker, credential, or process was used.
- The focused Python commands refreshed only worker-created tracked bytecode artifacts; this worker restored all seven and confirmed no `*.pyc` path remains in the diff.
- The exposure state value object and calculation moved to `memory_exposure.py`; the original store now remains below the pure-LOC size limit without changing behavior.
- PostgreSQL parity remains the owner task's required follow-up for this revised shared contract.
