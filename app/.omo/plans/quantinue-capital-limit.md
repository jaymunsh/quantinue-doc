# quantinue-capital-limit - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** A one-cycle paper-trading budget that blocks Quantinue from planning more than $1,000 of its own buy orders, plus a screen showing the budget, reserved amount, and amount still available. The first-cycle daily order limit defaults to one.

**Why this approach:** The limit is enforced before broker submission in one atomic reservation, so concurrent runs cannot spend past it. The screen uses that same durable reservation state and does not pretend to show broker cash or a real portfolio.

**What it will NOT do:** It will not add a scheduler, broker balance/position sync, virtual accounts, sells, or real-money trading. It is an app-managed planned-exposure limit, not a guarantee of the final dollar amount of a market-order fill.

**Effort:** Medium
**Risk:** Medium - concurrent durable budget accounting and external broker status reconciliation must remain fail-closed.
**Decisions to sanity-check:** `$1,000` means Quantinue's submitted-plan exposure at its reference price; actual Alpaca balance and market-fill cost are deliberately outside this first-cycle feature.

Your next move: execution is in progress from the user's explicit start-work instruction. Full execution detail follows below.

---

> TL;DR (machine): Medium effort, medium risk; atomically cap app-owned planned buy exposure at $1,000, default one daily attempt, and render the same reservation summary without broker-account claims.

## Scope
### Must have
* `QUANTINUE_MAX_APP_ORDER_EXPOSURE_USD=1000.00` must be a validated, documented configuration default; daily new-order default must be `1`.
* Role 09 must size from the configured exposure budget rather than the current hard-coded 10,000 value.
* Both stores must atomically reserve exactly one idempotent planned order only if account-wide eligible notional remains at or below the cap, regardless of trade date.
* A durable result must reconcile capital state exactly once; provider `accepted` maps to durable `submitted`, `rejected` to durable `failed`, and `canceled` remains canceled. Ambiguous transport failures retain the reservation.
* Dashboard must server-render a clearly labelled app-budget panel with configured cap, planned/reserved amount, remaining amount, and no broker-balance implication.
* Tests must cover Decimal-cent boundaries, concurrency, idempotency, terminal release rules, configured sizing, disposable PostgreSQL, and responsive browser behavior.
### Must NOT have (guardrails, anti-slop, scope boundaries)
* Do not access, inspect, stop, or change any localhost:5432 container, network, or volume. PostgreSQL QA uses only `scripts/test_postgres_integration.sh` disposable runner.
* Do not modify `.env`, create or record secret values, change product Docker host port 5444/internal db:5432, add a scheduler, add broker portfolio/account reads, or claim actual cash/position reconciliation.
* Do not release a planned reservation merely because a broker transport outcome is ambiguous.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD + pytest, disposable PostgreSQL integration script, and Playwright browser QA
- Evidence: `.omo/evidence/capital-limit/task-<N>-<name>.md` plus captured browser screenshots/logs; redact all secrets and provider headers.

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.
Wave 1 serializes the common data contract before dependent store and role work. Wave 2 runs the PostgreSQL implementation and in-memory/role implementation in parallel after the contract. Wave 3 runs dashboard and docs after the read model. Wave 4 performs integration, browser QA, review, and runtime audit.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 2, 3, 4, 5 | none |
| 2 | 1 | 5, 6 | 3, 4 |
| 3 | 1 | 5, 6 | 2, 4 |
| 4 | 1 | 5 | 2, 3 |
| 5 | 2, 3, 4 | 6 | none |
| 6 | 5 | final wave | none |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. Define the app-order exposure contract and first-cycle configuration
  What to do / Must NOT do: Add a typed Decimal-money budget configuration and a typed store-visible summary/result contract. Set safe defaults to USD 1000.00 and one daily new-order attempt, wire configured factory policy injection, update `.env.example` and user-facing wording. Do not edit `.env`, introduce a scheduler, use floating-point money, or imply broker cash.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 2, 3, 4, 5
  References (executor has NO interview context - be exhaustive): `src/quantinue/core/config.py:56-122`; `src/quantinue/orchestration/factory.py:76-160`; `src/quantinue/orchestration/policy.py:89-160`; `src/quantinue/db/contracts.py:47-136`; `.env.example:50-58`; `tests/unit/test_config.py`.
  Acceptance criteria (agent-executable): failing-first pytest proves default USD 1000.00/one attempt, rejects invalid non-positive/non-cent values, and configured factory passes both values into Role 09; `uv run pytest -q tests/unit/test_config.py` passes.
  QA scenarios (name the exact tool + invocation): happy: `uv run python -c 'from quantinue.core.config import Settings; print(Settings().max_app_order_exposure_usd)'` prints `1000.00`; failure: parse zero/non-cent values in focused pytest; Evidence `.omo/evidence/capital-limit/task-1-config.md`.
  Commit: N | feat(risk): define app-owned exposure cap

- [x] 2. Enforce account-wide exposure atomically in the in-memory store
  What to do / Must NOT do: Extend the existing daily reservation seam so one atomic operation preserves daily-attempt semantics and app-owned eligible buy exposure. Retain per-order Decimal notional and provide a typed safe summary; idempotent replay must not double spend. Do not derive exposure from terminal runs or release uncertain planned submissions.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 5, 6
  References (executor has NO interview context - be exhaustive): `src/quantinue/db/contracts.py:47-136`; `src/quantinue/db/memory.py:25-174`; `src/quantinue/roles/role_09_risk_portfolio/service.py:32-76`; `tests/unit/test_daily_order_cap.py:16-77`.
  Acceptance criteria (agent-executable): TDD tests prove concurrent 600+600 permits one, 600+400 permits both, 999.99+0.01 succeeds, a further cent fails, replay is idempotent, and planned/submitted/filled count while failed/canceled do not; `uv run pytest -q tests/unit/test_daily_order_cap.py` passes.
  QA scenarios (name the exact tool + invocation): happy/failure: execute the focused async tests and capture assertions plus budget summary without secrets; Evidence `.omo/evidence/capital-limit/task-2-memory.md`.
  Commit: N | feat(risk): reserve in-memory app exposure atomically

- [x] 3. Enforce account-wide exposure and canonical status reconciliation in PostgreSQL
  What to do / Must NOT do: Under fixed-order PostgreSQL advisory locks, check idempotency, total eligible planned/submitted/filled reference notional across all dates, daily count, and insert in one transaction. Add exactly-once reconciliation that maps provider statuses to schema-valid canonical statuses and changes budget eligibility accordingly. Preserve reservations on ambiguous outcomes; do not use host 5432 or modify runtime Compose port contracts.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 5, 6
  References (executor has NO interview context - be exhaustive): `src/quantinue/db/postgres_query.py:128-169`; `src/quantinue/db/postgres_read.py`; `src/quantinue/db/postgres_run_reads.py`; `src/quantinue/db/postgres_lifecycle.py:72-108`; `src/quantinue/db/domain.py:197-253`; `src/quantinue/db/schema.sql:107-127`; `tests/integration/test_persistence_postgres.py:226-305`; `scripts/test_postgres_integration.sh`.
  Acceptance criteria (agent-executable): disposable PostgreSQL tests from two independent stores on different dates cannot reserve more than USD 1000.00; replay is idempotent; submitted/filled remain counted; rejected/failed and canceled release exposure; accepted/rejected never violate `tb_order` status constraints.
  QA scenarios (name the exact tool + invocation): happy/failure: `sh scripts/test_postgres_integration.sh -q` using only its disposable runner; assert budget rows/summary through test code, not raw secret-bearing logs; Evidence `.omo/evidence/capital-limit/task-3-postgres.md`.
  Commit: N | feat(db): persist app exposure reservations safely

- [x] 4. Apply the cap to Role 09 and reconcile every order result once
  What to do / Must NOT do: Replace hard-coded sizing equity with configured app exposure budget, make cap rejection observable at Role 09, and ensure Role 10/store lifecycle reconciliation runs exactly once for memory and PostgreSQL. Keep the current market-order behavior but use precise “planned exposure” language. Do not claim actual fill cost is capped.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 5
  References (executor has NO interview context - be exhaustive): `src/quantinue/roles/role_09_risk_portfolio/service.py:18-100`; `src/quantinue/roles/role_09_risk_portfolio/contracts.py:13-126`; `src/quantinue/roles/role_10_order_execution/service.py:16-76`; `src/quantinue/broker/alpaca.py:79-254`; `src/quantinue/core/terminal_run_types.py:1-18`; `tests/unit/test_risk_order_contracts.py`; `tests/unit/test_broker_provider.py`.
  Acceptance criteria (agent-executable): focused tests prove USD 1000 sizing, a cap-exceeded decision has zero quantity and no broker submission, durable accepted maps to submitted, rejected maps to failed, and retry/replay does not double reconcile; type checking is clean for changed files.
  QA scenarios (name the exact tool + invocation): happy/failure: `uv run pytest -q tests/unit/test_risk_order_contracts.py tests/unit/test_daily_order_cap.py tests/unit/test_broker_provider.py`; Evidence `.omo/evidence/capital-limit/task-4-pipeline.md`.
  Commit: N | feat(pipeline): apply app exposure policy before submission

- [x] 5. Render a responsive app-order exposure panel in the control room
  What to do / Must NOT do: Pass the typed store summary through the dashboard handler and add a server-rendered, accessible panel after the existing four summary cards. Render cap, planned/reserved, remaining, and explicit “not broker balance/portfolio” wording. Update `DESIGN.md` and tests. Do not overload per-run `OrderView`, expose raw broker/provider payloads, or require JavaScript for the baseline.
  Parallelization: Wave 3 | Blocked by: 2, 3, 4 | Blocks: 6
  References (executor has NO interview context - be exhaustive): `src/quantinue/main.py:119-165`; `src/quantinue/web/templates/dashboard.html:43-66`; `src/quantinue/web/static/dashboard.css:81-110,192-231`; `DESIGN.md:32`; `tests/test_web.py`; `.omo/evidence/live-progress/task-4-browser.spec.js`.
  Acceptance criteria (agent-executable): HTML tests cover empty/seeded summaries and assert exact currency strings, clear non-broker language, no secrets, readable no-JS server render, and responsive CSS behavior.
  QA scenarios (name the exact tool + invocation): start a safe memory/mock server, use Playwright at 1440/1024/768/390 and 390 with JavaScript disabled; assert panel visibility, no horizontal overflow/console errors, and keyboard focus; Evidence `.omo/evidence/capital-limit/task-5-browser/`.
  Commit: N | feat(web): show app order exposure budget

- [x] 6. Run the full first-cycle safety verification and record cleanup
  What to do / Must NOT do: Run all required quality, unit, disposable database, compose, HTTP, and browser checks; perform the independent five-lane review and three-hypothesis runtime audit; write redacted evidence/ledger receipts. Do not call localhost:5432, use product Compose as a QA database, or leave new processes/containers running.
  Parallelization: Wave 4 | Blocked by: 2, 3, 4, 5 | Blocks: final wave
  References (executor has NO interview context - be exhaustive): `scripts/test_postgres_integration.sh`; `scripts/test_compose_contract.sh`; `.omo/start-work/ledger.jsonl`; `.omo/evidence/runtime-debug-audit/`; `.omo/evidence/live-progress/`.
  Acceptance criteria (agent-executable): `uv run ruff format --check .`, `uv run ruff check .`, `uv run basedpyright`, `uv run pytest -q`, `sh scripts/test_postgres_integration.sh -q`, and `sh scripts/test_compose_contract.sh` all pass; evidence records cleanup and exactly three runtime hypotheses.
  QA scenarios (name the exact tool + invocation): happy: safe mock HTTP launch plus dashboard/API/cap rejection scenario; failure: malformed config/cap-exceeded scenario; Evidence `.omo/evidence/capital-limit/task-6-final.md`.
  Commit: N | test: verify first-cycle app exposure limit

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Scope fidelity

## Commit strategy
No commit unless the user explicitly requests one. Keep all changes in the shared dirty worktree and preserve unrelated files.

## Success criteria
* A user can configure and see a USD 1,000 Quantinue app-order planned-exposure cap with one daily new-order attempt by default.
* Concurrent/replayed runs cannot reserve more eligible app-owned reference notional than the configured cap across dates.
* Provider accepted/rejected status does not corrupt PostgreSQL lifecycle state; unsafe ambiguity remains fail-closed.
* Dashboard accurately labels its values as app-order budget facts, remains accessible and responsive, and does not expose secrets or broker-account assertions.
* Required automated gates, manual browser use, independent review, runtime audit, evidence, and cleanup all pass.
