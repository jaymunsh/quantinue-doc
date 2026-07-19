# Task 5 PostgreSQL simulated-account restart evidence

Date: 2026-07-14 KST

## Result

PASS. The app-owned local account is initialized at USD 1,000,000 with `ON CONFLICT DO NOTHING`; an existing cash or buying-power balance is never reset. A unique filled buy updates the canonical order, inserts one `tb_fill` row, and debits `tb_account.cash` and `buying_power` in one transaction. Fill-time `equity` remains notional-equivalent, while portfolio reads derive current equity from durable cash plus the latest completed-run mark.

## Runtime hypotheses and evidence

1. A second stage-08 execution or process restart resets cash to the opening balance.
   - Reproduced in the pre-change implementation: `save_account` used an update-on-conflict balance upsert and stage 08 supplied USD 10,000.
   - Fixed with insert-only account initialization and configured USD 1,000,000 propagation.
   - Disposable PostgreSQL concurrent initialization returned one account identity; after a USD 200 fill, a second initialization retained the reduced cash.
   - The actual `stage_completed("08", ...)` boundary was then invoked for two distinct run contexts. The first stage created the account/signal, a fill debited it, and the second stage returned the same account ID without changing the debited cash.

2. Duplicate or concurrent broker-fill replay debits cash twice.
   - Two independent repositories concurrently recorded the same stable broker-fill identity.
   - Both returned the same fill identifier, exactly one fill remained, and cash changed by USD 200 exactly once.

3. Restart reads lose holdings or use a stale/nonterminal run mark.
   - The store was closed, reconstructed against the same disposable database, and read again.
   - Before/after typed snapshots were exactly equal.
   - Mark candidates were persisted in order: older completed USD 90, latest completed USD 100, newer failed USD 150, and newer running USD 200. The exact selected mark was the latest completed USD 100 value and timestamp; failed/running values were excluded.
   - A separate adapter-parity integration test instantiated isolated `InMemoryRunStore` and `PostgresRunStore` objects. Each received the same scenario through its public store contracts: claim/start/complete/finish for the USD 125 completed mark, the same `DailyOrderReservation`, the same `CompletedBuyWrite`, and `simulated_portfolio` read.
   - The two independently produced `SimulatedPortfolioSnapshot` values were exactly equal, including account, every position, every order, every fill, mark provenance/timestamps, realized-P&L state, and allocation. PostgreSQL-derived rows were not fed into the memory adapter or a projector for this parity assertion.

4. Insufficient cash leaves a partial fill or debit.
   - A USD 200 fill was attempted against USD 100 cash.
   - The typed error was raised; fill count remained zero and cash remained USD 100.
   - The pure projection and `InMemoryRunStore` were tested at the same USD 100/required USD 200 boundary. Both rejected the fill; memory retained zero orders and zero fills instead of producing negative cash.

## Gates

- `uv run ruff check` on touched Python and integration files: PASS
- `uv run basedpyright`: PASS, 0 errors and 0 warnings
- focused unit accounting/configuration tests: PASS, 42 tests
- `uv run pytest -q`: PASS, 512 passed and 27 skipped
- `sh scripts/test_postgres_integration.sh -q`: PASS, 30 tests
- measured nonblank/non-comment production sizes: `memory.py` 289, `postgres.py` 248, `memory_completed_buy.py` 34, `postgres_lifecycle.py` 115, `postgres_portfolio.py` 181, and `simulated_portfolio.py` 211 lines
- `memory.py` remains a shared pre-existing oversized module; it measured 296 lines immediately before the parity-contract revision. Extracting only the newly added completed-buy behavior reduced it to 289 rather than claiming the whole shared module was brought below 250. No unrelated module-size compatibility claim is made.

## Manual lifecycle QA and cleanup

The disposable runner performed the real close/reopen, two-distinct-stage-08, and independent public-store adapter-parity scenarios. Assertions used only non-secret balances, counts, statuses, and timestamps: USD 1,000,000 opening cash, one USD 200 debit, one unique fill, one two-share position, completed/failed/running mark ordering, exact snapshot equality after reopen, and exact memory/PostgreSQL snapshot equality. The runner exited successfully and its runner-owned cleanup trap removed the disposable container. No product Compose resource, Alpaca endpoint, secret, `.env`, or localhost port 5432 was inspected or used.
