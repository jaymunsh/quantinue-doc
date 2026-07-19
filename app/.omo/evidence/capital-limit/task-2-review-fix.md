# Task 2 independent re-review: Decimal money and typed replay outcome

## Verdict

**CONFIRMED**

The two prior blocking contract defects are remediated in the in-memory path. No
product file was edited by this verifier.

## Independent runtime evidence

An isolated AnyIO driver, using the real `InMemoryRunStore`, `RiskPortfolio`, and
`OrderExecution` objects, reported:

```text
memory-fix-contract: fractional_cent=rejected concurrent=1/2 cents=pass cross_date=blocked replay=typed stale_terminal=protected collision=rejected role09_to_role10_calls=0
```

The driver independently proved all of the following:

| Scenario | Observed result |
| --- | --- |
| fractional-cent / non-finite `Decimal` request | rejected by `DailyOrderReservation` before reservation |
| fractional-cent / non-finite Role 09 float input | rejected by the typed money parser |
| concurrent `$600 + $600` | one `acquired`, one `rejected`, `$600.00` counted |
| `$999.99 + $0.01` then another cent | first two acquired, third rejected |
| account-wide cross-date limit | existing `$600` blocked new `$500` on a later trade date |
| exact same replay | `replayed`, with no second exposure |
| terminal stale reconciliation | `filled` stayed counted after stale `failed` |
| failed release and daily attempt | exposure released; new identity still rejected under daily cap one |
| same key, different account/ticker/notional | rejected; caller's own safe summary retained |
| Role 09 collision to Role 10 | mismatched ticker key rejected, Role 09 produced quantity zero, Role 10 made **0** broker `submit` calls |

Focused verification passed:

```text
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q tests/unit/test_daily_order_cap.py tests/unit/test_risk_order_contracts.py  # 32 passed
PYTHONDONTWRITEBYTECODE=1 uv run ruff format --check <Todo-2 paths>                                                    # pass
PYTHONDONTWRITEBYTECODE=1 uv run ruff check <Todo-2 paths>                                                             # pass
PYTHONDONTWRITEBYTECODE=1 uv run basedpyright <Todo-2 paths>                                                          # 0 errors, 0 warnings, 0 notes
git diff --check                                                                                                       # pass
```

## Contract review

- Persisted reservation money is now `Decimal`; `DailyOrderReservation.__post_init__`
  enforces finite positive cent values and computes notional from Decimal only.
- The old success boolean is replaced with explicit `acquired`, `replayed`, and
  `rejected` outcomes.
- Memory replay checks the complete immutable request before returning `replayed`.
  A changed request is rejected rather than treated as permission to submit.

## Scope and cleanup

- No Docker, PostgreSQL, localhost:5432, `.env`, provider credentials, network, or
  external broker was accessed.
- `PYTHONDONTWRITEBYTECODE=1` was used for this verification; no bytecode artifact
  was created by the verifier. Existing shared-worktree changes were preserved.
