# Task 2: simulated portfolio contracts

## Baseline and TDD proof

- Existing characterization: `test_first_cycle_order_controls_default_to_one_thousand_usd_and_one_attempt` preserved the USD 1,000.00 app exposure cap and one-attempt default before production edits.
- Red run: `uv run pytest -q tests/unit/test_config.py -k simulated_opening_cash` failed 4 tests because `simulated_account_opening_cash_usd` did not exist and invalid values were ignored.
- Green run: focused portfolio, config, daily-cap, and capital-limit suites passed 73 tests.

## Manual pure-driver observation

Input was two NVDA buys (2 at USD 100.00, 1 at USD 130.00), one replay of the first fill identity, and a completed-run mark of USD 120.00.

```json
{"allocation":"0.0004","average_cost":"110.00","current_cash":"999670.00","equity":"1000030.00","fill_count":2,"mark":"120.00","mark_source":"completed_run","market_value":"360.00","opening_cash":"1000000.00","quantity":3,"realized_pnl_status":"not_applicable_buy_only","ticker":"NVDA","unrealized_pnl":"30.00"}
```

The duplicate replay did not create a third fill or debit cash twice. The calculation is local buy-only accounting and makes no Alpaca account, balance, or execution claim.

## Boundaries and cleanup

- No database, Docker service, network broker, environment file, or secret was accessed.
- No temporary driver file or process was created; the manual driver exited 0.
- Persistence and account mutation remain delegated to Tasks 4 and 5.
