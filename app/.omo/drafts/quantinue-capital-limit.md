---
slug: quantinue-capital-limit
status: approved-start-work-bootstrap
intent: clear
pending-action: execute .omo/plans/quantinue-capital-limit.md
approach: Enforce one app-owned $1,000 planned-exposure budget with atomic durable reservations; retain manual execution and one daily new-order attempt; display only app-budget facts, never broker balance or portfolio.
---

# Draft: quantinue-capital-limit

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
<!-- id | outcome (one line) | status: active|deferred | evidence path -->
| budget-policy | Typed, default-safe configuration and role sizing are bounded to $1,000 and one daily new order. | active | src/quantinue/core/config.py; src/quantinue/orchestration/factory.py |
| atomic-budget | Memory and PostgreSQL stores atomically retain or reject app-owned exposure across all dates. | active | src/quantinue/db/contracts.py; src/quantinue/db/postgres_query.py |
| order-lifecycle | Broker terminal results update the budget safely and map provider statuses to valid durable statuses. | active | src/quantinue/broker/alpaca.py; src/quantinue/db/postgres_lifecycle.py |
| budget-visibility | Dashboard exposes cap/reserved/remaining as app-owned facts with responsive, non-broker wording. | active | src/quantinue/main.py; src/quantinue/web/templates/dashboard.html |
| validation | Unit, disposable PostgreSQL, and browser QA establish safety and visible behavior. | active | tests/; scripts/test_postgres_integration.sh |

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
<!-- assumption | adopted default | rationale | reversible? -->
| Cap semantics | $1,000 is a Quantinue planned-exposure cap using each accepted plan's reference notional; it is not a broker-cash or actual-fill cap. | Existing market orders can fill above an observed reference price; claiming a strict broker-cash cap would be false without a separate bounded-price/reconciliation design. | yes |
| First-cycle cadence | One manual run path remains; daily new-order attempt default becomes 1. | The web app has no running scheduler, and the user requested a single-run first phase. | yes |
| Eligible exposure | planned/submitted/filled buy rows count; failed/canceled rows do not; ambiguous pre-submit plans remain counted. | Fail closed on uncertain broker submission; release only after durable terminal evidence. | yes |

## Findings (cited - path:lines)
* Role 09 currently sizes with a hard-coded 10,000 equity figure, while Settings only has a daily count cap: `src/quantinue/roles/role_09_risk_portfolio/service.py:23-75`, `src/quantinue/core/config.py:81-88`.
* The existing atomic reservation seam is `DailyOrderReservation` plus `RunStore.reserve_daily_new_order`: `src/quantinue/db/contracts.py:47-59,135-136`; PostgreSQL already owns an advisory-lock transaction for reservation: `src/quantinue/db/postgres_query.py:128-169`.
* The order table accepts canonical statuses only, but Alpaca exposes accepted/rejected and the lifecycle currently writes raw provider status: `db/schema.sql:113-123`, `src/quantinue/broker/alpaca.py:202-218`, `src/quantinue/db/postgres_lifecycle.py:87-108`.
* The app is manual-run today, not a running scheduler: `src/quantinue/main.py:168-218`, `README.md:77-82`.
* Existing dashboard order facts are per-run and cannot truthfully represent broker balance or positions: `src/quantinue/api/schemas.py:93-103,178-196`, `src/quantinue/api/presentation.py:158-168`.

## Decisions (with rationale)
* Name the new setting `QUANTINUE_MAX_APP_ORDER_EXPOSURE_USD`, default `1000.00`; its name prevents a false claim about deployed broker capital.
* Retain market orders and make the budget label explicit. A limit-order/actual-fill-cap redesign is out of scope for phase 1.
* Keep daily attempt quota and total exposure as independent gates: a rejected/canceled attempt releases exposure but still consumes the daily attempt.
* Add an account-wide PostgreSQL advisory lock before the existing account/day lock so different dates cannot oversubscribe one account-wide cap.

## Scope IN
* Config, risk sizing, durable/in-memory exposure reservation and read model, canonical order-status reconciliation, dashboard visibility, tests, evidence and docs for the $1,000 first-cycle budget.

## Scope OUT (Must NOT have)
* No broker balance/position sync, no scheduler, no sell/slot/multi-account feature, no generated secrets, no host localhost:5432 activity, and no modification of the user's `.env`.

## Open questions
* None. The user's explicit start-work instruction authorizes the start-work bootstrap; the conservative app-budget semantics are recorded above.

## Approval gate
status: satisfied-by-start-work-bootstrap
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->
