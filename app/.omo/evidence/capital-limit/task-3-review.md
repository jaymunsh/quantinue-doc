# Task 3 independent PostgreSQL review

## Verdict

PASS

## Static contract review

- `reserve_daily_order` acquires transaction-scoped advisory locks in the required fixed order: account-wide exposure cap, account/date daily cap, then idempotency identity. The existing-order check follows those locks, so two `PostgresRunStore` instances cannot both pass the account-wide cap check.
- Exposure is an exact `Decimal` sum of `quantity * entry_price`; only `planned`, `submitted`, and `filled` remain eligible. `failed` and `canceled` are excluded. The reservation request normalizes all money inputs to positive cent values before persistence.
- An idempotency replay compares the immutable persisted order identity (account, signal, ticker, quantity, entry/stop/take-profit prices). A matching replay is free; a changed durable-order payload is rejected without changing the exposure summary.
- The PostgreSQL lifecycle refuses any mutation once an order is terminal (`filled`, `failed`, or `canceled`). This prevents duplicate or stale terminal outcomes from reopening or regressing state. Role 10 normalizes provider `accepted` to `submitted` and `rejected` to `failed`; both are permitted by the unchanged `tb_order` status constraint.
- `db/schema.sql` has no local diff. The disposable runner mounts that schema read-only, creates a uniquely named temporary PostgreSQL container on a dynamically selected 55400-55499 loopback port, and registers trap cleanup. No host 5432 or product Compose database was used or inspected.

## Runtime evidence

Executed only the approved disposable runner:

```text
sh scripts/test_postgres_integration.sh -q
24 passed in 10.77s
```

The passing suite includes the cross-store, cross-date 600 + 600 race, replay/collision and 1000.00-cent boundary, eligible/terminal state transitions, and the Role 10 accepted/rejected pipeline paths. The runner exited successfully; its `EXIT` trap removes the unique disposable container.

## Review conclusion

Task 3 acceptance criteria are satisfied. No product files, database schema, Docker configuration, or secrets were changed by this review.
