# Final first-MVP runtime debug audit

Date: 2026-07-14 (Asia/Seoul)

## Verdict

**PASS** — all six explicit hypotheses were refuted at an executable boundary. No
product code was edited. No `.env`, provider key, Alpaca endpoint, product
PostgreSQL instance, or `localhost:5432` was read or touched.

## Runtime boundaries

- Python: CPython 3.11.15 through the repository `uv` environment.
- Database: only `scripts/test_postgres_integration.sh`; its disposable isolated
  PostgreSQL runner performed its own teardown.
- Local-only checks: deterministic role/store runtimes with in-process fake
  providers; no network broker/model operation.

## H1 — irrelevant/zero-result ticker news creates false model evidence

Hypothesis: a false-positive or zero relevant result still invokes the analyzer
or emits model lineage.

Runtime toggle and result:

- Empty feed and one irrelevant `Market digest` feed were both executed through
  `NewsAnalysis.execute`.
- In both branches the analyzer invocation list remained exactly `[]`, score was
  `0.0`, representative source and analysis were `None`, the stage reported
  `관련 뉴스 0건`, and evidence `model_name` / `model_provider` were both `None`.
- The adversarial short-symbol substring/prompt-text selector case also passed.

Verdict: **REFUTED / PASS**. Zero relevant news creates no model evidence.

## H2 — duplicate fill, account reset, or insufficient cash is non-atomic

Hypothesis: concurrent duplicate completion double-debits; replaying account
initialization resets cash; or insufficient funds leaves a partial fill/debit.

Actual disposable PostgreSQL observations:

- Two concurrent account initializations returned the same row id. After a $200
  completed buy, another initialization left `(cash, equity, buying_power)` at
  `(999800.00, 1000000.00, 999800.00)`.
- Two concurrent writes of `fill-restart` returned the same canonical fill id;
  read-back contained exactly one fill and cash decreased by exactly `$200.00`.
- A `$200` buy against `$100.00` cash raised `insufficient simulated cash`;
  direct SQL read-back showed fill count `0` and cash `100.00`.
- Matching in-memory concurrent duplicate and insufficient-cash checks passed.

Verdict: **REFUTED / PASS**. Accounting is atomic, insert-only, and idempotent.

## H3 — restart or stale failed/running marks corrupt completed valuation

Hypothesis: store reopen loses portfolio state or newer non-completed candidates
override the latest completed mark.

Actual disposable PostgreSQL toggle and result:

- The scenario completed a buy, stored a completed `$100.00` mark, seeded newer
  failed/running candidates, closed the store, and reopened a new store.
- The pre- and post-reopen snapshots compared exactly equal.
- The position retained source `completed_run`, price `100.00`, as-of the
  completed run timestamp, one canonical fill, and the exact `$200.00` debit.
- Projection parity with the buy-only in-memory accounting function also held.

Verdict: **REFUTED / PASS**. Restart is durable and only completed runs value the
portfolio.

## G2-H1 — conflicting re-input overwrites append-only lineage/raw values

Hypothesis: replaying the same identities with deliberately changed strategist,
critic, disclosure, and news values overwrites the canonical rows.

Actual disposable PostgreSQL toggle and result:

- Initial raw/source, provenance, model, prompt, policy, hash, strategist, and
  critic rows were read directly.
- A conflicting replay using altered source refs, future timestamps, OpenAI model
  lineage, hashes, summaries, side/conviction, critic decision, and evidence was
  submitted twice.
- The repository returned the original canonical signal/verdict ids and direct
  post-replay rows remained equal to the pre-replay rows.

Verdict: **REFUTED / PASS**. Conflicting replay cannot overwrite provenance,
model, raw, strategist, or critic canonical values.

## G2-H2 — strategist `sell` crosses the model or real PostgreSQL boundary

Hypothesis: the phase-two strategist `sell` value is accepted by either typed
model validation or database persistence.

Runtime toggle and result:

- `Side("sell")` and the strategist output Pydantic model both raised validation
  errors.
- A direct insert of strategist `side='sell'` into disposable PostgreSQL raised
  `IntegrityError` under the actual table CHECK constraint.
- This is intentionally separate from execution/fill sell support; the audited
  strategist contract remains MVP `buy`/`hold` only.

Verdict: **REFUTED / PASS**. Strategist sell crosses neither boundary.

## G2-H3 — stale classifier cleanup regressed retry/terminal behavior

Hypothesis: transient and terminal failures now share the wrong lifecycle.

Minimal orchestrator runtime toggle and result:

- A role that failed transiently twice persisted attempt statuses exactly
  `retrying`, `retrying`, `completed`.
- A validation failure was terminal after one call. Repeating the same request
  returned the stored failed run and the role call count remained exactly `1`.

Verdict: **REFUTED / PASS**. Transient retry and terminal validation behavior
remain distinct.

## Commands and results

```text
uv run pytest -q \
  tests/unit/test_news_selection.py::test_news_analysis_completes_without_model_output_when_no_news_is_relevant \
  tests/unit/test_news_selection.py::test_news_selection_rejects_short_symbol_substrings_and_prompt_text \
  tests/unit/test_memory_simulated_ledger.py::test_memory_ledger_concurrent_same_fill_identity_is_atomic \
  tests/unit/test_memory_simulated_ledger.py::test_memory_ledger_rejects_insufficient_cash_without_partial_order_or_fill \
  tests/unit/test_memory_simulated_ledger.py::test_memory_portfolio_uses_latest_completed_run_mark \
  tests/unit/test_ontology.py::test_strategist_side_rejects_phase_two_sell \
  tests/unit/test_roles_05_08_contracts.py::test_strategy_output_model_rejects_phase_two_sell \
  tests/unit/test_pipeline_resilience.py::test_transient_attempts_persist_retrying_then_completed \
  tests/unit/test_pipeline_resilience.py::test_validation_failure_is_terminal_after_one_attempt

10 passed in 1.03s
```

```text
sh scripts/test_postgres_integration.sh -q \
  tests/integration/test_simulated_portfolio_postgres.py::test_account_initialization_is_concurrent_and_never_resets_mutated_cash \
  tests/integration/test_simulated_portfolio_postgres.py::test_unique_buy_fill_debits_once_and_survives_store_reopen \
  tests/integration/test_simulated_portfolio_postgres.py::test_insufficient_cash_rolls_back_fill_and_account_debit \
  tests/integration/test_append_only_postgres.py::test_postgres_keeps_first_ledger_rows_when_conflicting_payload_replays \
  tests/integration/test_domain_lifecycle_postgres.py::test_postgres_rejects_phase_two_sell_strategist_signal

30 passed in 15.12s
```

The PostgreSQL runner includes its contract fixtures, which accounts for the 30
executed tests. Exit code was `0` after teardown.

## Cleanup

- `docker ps -a --filter name=quantinue-test-pg-` returned no rows after runner
  completion.
- No debug listener, server, or temporary fixture was created.
- No product file or test file was edited by this audit.
- The pre-existing dirty/untracked worktree and running application were left
  untouched.

## Ledger-ready summary

`runtime-debug-audit final-first-mvp: PASS; H1 zero/irrelevant news produced zero
model calls and no lineage; H2 duplicate/reset/insufficient-cash remained
idempotent and atomic; H3 restart ignored failed/running marks and preserved the
latest completed $100 mark; G2 append-only conflict replay preserved canonical
raw/provenance/model values; strategist sell rejected by model and real PG; retry
classifier retained retrying,retrying,completed vs one-call terminal behavior;
unit 10 passed; disposable PG 30 passed; cleanup confirmed; no secrets.`
