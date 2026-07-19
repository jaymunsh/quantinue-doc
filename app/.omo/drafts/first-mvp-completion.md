# First MVP Completion — Draft

- intent: clear
- review_required: false
- status: superseded-and-executed
- superseded_by: `.omo/plans/first-mvp-visual-completion.md`
- pending_action: none; final verification is tracked by the superseding plan
- test_strategy: TDD plus disposable PostgreSQL integration and real Chromium QA

## Objective

Close the four remaining first-MVP demonstration gaps without enabling Alpaca orders or touching localhost:5432: ticker-relevant news, durable PostgreSQL run history, a truthful local simulated account/portfolio view, and explicit representative-analysis selection rationale.

## Components ledger

1. `ticker-news` — select and analyze ticker-relevant entries instead of blindly taking the first general SEC RSS item. Status: grounded. Evidence: `src/quantinue/roles/role_06_news_analysis/service.py`, `src/quantinue/market_data/http_source.py`.
2. `durable-runtime` — make the supported host-5444 PostgreSQL mode explicit and verify restart persistence through the existing store factory. Status: grounded. Evidence: `src/quantinue/core/config.py`, `src/quantinue/db/store.py`, `src/quantinue/main.py`.
3. `simulated-portfolio` — project local cash, holdings, average cost, market value, realized/unrealized P&L, allocation, and order/fill history from app-owned records. Status: grounded. Evidence: `db/schema.sql`, `src/quantinue/db/domain.py`, `src/quantinue/web/templates/dashboard.html`.
4. `representative-rationale` — show collection count, relevant count, selected item, selection score/reasons, and one-analysis limitation. Status: grounded. Evidence: `src/quantinue/core/context_detail.py`, `src/quantinue/api/presentation.py`.

## Adopted reversible defaults

- Keep Alpaca Paper disabled and broker mode mock for this first-MVP work.
- Set the simulated account opening cash/equity to USD 1,000,000 while retaining the independent configured app-order exposure cap at USD 1,000 unless the user changes it.
- Use deterministic relevance ranking over fetched public RSS entries: exact ticker token, company-name aliases, and title hits outrank snippet hits; stable published-time/source-reference tie breaks.
- Preserve all fetched items for audit, but analyze the highest-ranked relevant item only. If none are relevant, show an explicit zero-relevant result and do not mislabel a general SEC item as ticker news.
- Do not change application-wide database defaults silently. Document and expose the supported `.env` switch to `QUANTINUE_DATABASE_MODE=postgres` with host port 5444; Compose continues using `db:5432` internally.
- Derive portfolio values only from Quantinue-owned simulated orders/fills and current run prices. Never label them Alpaca balances or real brokerage positions.

## Scope boundaries

### In

- Typed news relevance/selection contract and tests.
- Role 06 terminal detail with representative-selection explanation.
- Read-only account/portfolio projection for memory and PostgreSQL stores.
- Dashboard portfolio/account/order-history sections with responsive visual QA.
- Host-5444 persistence documentation/runtime verification and restart test.
- Evidence under `.omo/evidence/first-mvp-completion/` and secret-free ledger entry.

### Out

- Alpaca Paper order submission, real account balances, live credentials, scheduler, T+1–T+5 automatic review execution, paid news APIs, multi-account strategy isolation.
- Any inspection/use/change of localhost:5432.
- Writing secrets or committing `.env`.

## Approval brief

Plan the implementation as five waves: red tests/contracts; ticker-news ranking and rationale; typed account/portfolio read models for both stores; dashboard composition and visual polish; disposable PostgreSQL/restart/runtime/browser verification followed by review-work.

The only owner-level fork is the external news-source policy. Recommended first-MVP choice: keep the current no-key SEC RSS transport, add strict ticker relevance ranking, preserve all collected items, and explicitly show zero relevant items rather than introduce an undocumented third-party feed. Alternative: add a new no-key ticker-search RSS provider, which increases coverage but creates a new unstable external dependency.
