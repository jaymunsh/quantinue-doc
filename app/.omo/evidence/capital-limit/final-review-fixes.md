# Capital-limit final-review fixes

## Scope

- Reject non-positive `account_id`, `signal_id`, `quantity`, and daily `cap` in the shared reservation value.
- Persist broker order, parent, stop-leg, and take-profit-leg identifiers even when Role 10 has already reconciled the same canonical exposure status.
- Scope the dashboard's app-only exposure summary to the latest durable run account, never a hard-coded account id; with no durable account, show no fabricated amounts.

## Regression evidence

- `uv run pytest -q`: `426 passed, 22 skipped`.
- `sh scripts/test_postgres_integration.sh -q`: `25 passed` on the disposable runner.
- `uv run ruff format --check ...`, `uv run ruff check ...`, and `uv run basedpyright`: passed with zero diagnostics.
- The added disposable PostgreSQL regression first transitions the order to `filled` through the Role-10 exposure boundary, then records the broker and bracket-leg identifiers; all four durable fields were retained.

## Runtime and visual evidence

- Fresh screenshots: `final-review-fixes/dashboard-empty-1440.png`, `dashboard-populated-1440.png`, and `dashboard-populated-390.png`.
- Safe isolated runtime used memory persistence, fixture data, mock LLM, mock broker, and trading disabled.
- Empty state showed only the durable-account waiting message. A mock form execution then rendered the app-only cap, planned/reserved, remaining values, and the explicit no-broker-balance/no-actual-fill disclaimer.
- Independent design/functional and CJK review both returned PASS; no desktop or 390px overflow, clipping, or Korean wrapping issues were found.

## Cleanup

- The isolated Uvicorn process on `127.0.0.1:8013` and the QA browser were closed.
- No `.env`, secrets, product Docker, or `localhost:5432` resource was accessed or changed.
