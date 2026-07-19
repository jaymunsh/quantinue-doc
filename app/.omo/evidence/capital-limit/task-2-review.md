# Task 2 independent review: in-memory app-order exposure

## Verdict

**NEEDS-FIX**

The lock-protected memory behavior is correct for the required normal paths, but the
new persistence contract violates the plan's no-floating-point-money guardrail and
collapses materially different idempotency outcomes into an unsafe `accepted: bool`.
This is a blocking contract defect for the capital gate; no product files were edited
by this review.

## Independent runtime evidence

| Probe | Result |
| --- | --- |
| concurrent `$600 + $600` | exactly one reservation accepted; counted exposure `$600.00` |
| cent boundary | `$999.99 + $0.01` accepted; one additional cent rejected |
| account-wide cross-date scope | an existing `$600` on the next trade date blocked a new `$500` request |
| terminal stale update | `filled` remained counted after a stale `failed` reconciliation |
| released exposure versus daily attempts | `failed` released exposure, but a new identity under daily cap `1` remained rejected |

The isolated AnyIO driver reported:

```text
memory-contract: concurrent=1/2 exact_cents=pass cross_date=blocked release_keeps_attempt=pass stale_terminal=protected
```

Focused checks also passed:

```text
uv run pytest -q tests/unit/test_daily_order_cap.py  # 11 passed
uv run ruff format --check <Todo-2 paths>            # pass
uv run ruff check <Todo-2 paths>                     # pass
uv run basedpyright <Todo-2 paths>                   # 0 errors, 0 warnings, 0 notes
git diff --check                                     # pass
```

## Blocking findings

1. **Raw floats cross the money persistence boundary.**
   `DailyOrderReservation.entry_price`, `stop_price`, and `take_profit_price` are
   `float` in `src/quantinue/db/contracts.py`; the memory store subsequently performs
   `Decimal(str(request.entry_price))` in `src/quantinue/db/memory.py`. This accepts
   sub-cent and binary-derived inputs instead of receiving a validated two-decimal
   Decimal value. An independent probe accepted
   `999.9999999999999 + 0.0000000000001` and stored
   `1000.0000000000000`. The plan explicitly prohibits floating-point money and
   requires Decimal-cent boundaries at this persistence seam.

2. **`accepted: bool` loses whether the caller acquired a reservation or merely hit
   an existing key.**
   The existing-key branch returns `accepted=True` without validating that the replay
   represents the same account or order attributes. A direct memory probe submitted
   the same idempotency key for account 1 then account 2: the second call returned
   success but the summary belonged to account 1 and account 2 retained `$0`
   exposure. Role 09 currently treats every `accepted=True` result as permission to
   continue. The result needs a typed outcome such as acquired/replayed/rejected (and
   mismatched replay rejection), or an equivalent contract that cannot turn a key
   collision into a successful new order path.

## Required remediation before approval

- Make all persisted/reference monetary fields validated cent `Decimal` values at the
  Role 09-to-store boundary; reject fractional-cent input before the lock.
- Replace the ambiguous bool-only success signal with a typed reservation outcome and
  validate replay identity/immutable request fields. Add regression coverage for
  cross-account/key collision and fractional-cent rejection.

## Scope and cleanup

- No Docker, PostgreSQL, localhost:5432, network broker, `.env`, or secrets were
  accessed.
- The review used only in-memory objects and focused static/test commands.
- Python execution refreshed existing tracked bytecode cache files; no source/config
  product file was changed by this verifier. The shared worktree otherwise remains
  intentionally dirty and was not reset, checked out, or modified.
