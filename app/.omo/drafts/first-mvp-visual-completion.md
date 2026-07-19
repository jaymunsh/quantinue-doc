---
slug: first-mvp-visual-completion
status: approved
intent: clear
pending-action: execute `.omo/plans/first-mvp-visual-completion.md`
approach: Add ticker-search RSS and deterministic selection, implement a truthful buy-only simulated-account ledger with memory/PostgreSQL parity, expose the result in the existing control-room design, then verify restart durability and real responsive runtime behavior.
---

# Draft: first-mvp-visual-completion

## Components (topology ledger)
- ticker-news | Google News RSS query by ticker/company, deterministic relevance and zero-result semantics | active | `.omo/evidence/first-mvp-completion/news/`
- simulated-ledger | $1M opening account, local buy fills, cash/positions/P&L/order history | active | `.omo/evidence/first-mvp-completion/portfolio/`
- durable-runtime | host-5444 PostgreSQL restart persistence without balance reset | active | `.omo/evidence/first-mvp-completion/postgres/`
- dashboard | visually explicit news selection and simulated portfolio at 375/768/1440 | active | `.omo/evidence/first-mvp-completion/browser/`

## Open assumptions (announced defaults)
- opening capital | USD 1,000,000 | user requested the larger local balance; reversible by setting | yes
- order exposure | existing USD 1,000 lifetime app-owned cap including filled orders | preserves the existing hard safety boundary independently of displayed capital | yes
- phase-one accounting | buy-only; realized P&L shown as not applicable | schema/order flow has no sell side | yes, phase two
- news transport | Google News search RSS for ticker/company; SEC press RSS no longer labeled ticker news | visually useful ticker coverage without a key | yes
- zero relevant news | skip LLM, news score 0, pipeline continues with an explicit unavailable advisory | avoids false evidence while retaining a demonstrable pipeline | yes
- raw news persistence | all fetched items retained in run detail; representative only remains canonical `tb_news_signal` | matches existing canonical database contract and user-visible audit need | yes

## Findings (cited - path:lines)
- `src/quantinue/market_data/http_source.py:40-54,389-405` uses one global SEC press RSS endpoint.
- `src/quantinue/roles/role_06_news_analysis/service.py:48-113` blindly analyzes the first fetched item.
- `src/quantinue/db/postgres_lifecycle.py:72-74` resets the canonical account to USD 10,000 on each role-08 completion.
- `src/quantinue/db/contracts.py:156-171` exposes only order exposure, not account/position/fill views.
- `src/quantinue/roles/role_09_risk_portfolio/service.py:40-54` correctly sizes against the independent USD 1,000 exposure cap.
- `src/quantinue/roles/role_10_order_execution/service.py:28-74` performs a local full-fill through MockBroker even when external trading is disabled.
- `db/schema.sql:107-130` has account/order/fill rows but no position table; buy-only positions can be derived from unique fills.
- `src/quantinue/web/templates/dashboard.html:72-89` already has an app-exposure primitive to extend rather than replace.

## Decisions (with rationale)
- Add a typed `ticker_news(ticker, company_name, execution_id)` transport seam. Query `https://news.google.com/rss/search` with URL-encoded `(<ticker> OR "<company>")`, `hl=en-US`, `gl=US`, and `ceid=US:en`; use existing bounded HTTP policy and treat transport failure as retryable.
- Rank normalized, deduplicated items with exact ticker token and canonical company-name title hits above snippet hits; never substring-match short symbols such as `A`, `AI`, or `CAT`; stable ties use published time descending then canonical URL.
- Preserve fetched/relevant/excluded/selected as typed run-detail data. Analyze exactly one selected item. With zero relevant items, create no model-output evidence and use neutral score 0.
- Initialize the simulated account once with `ON CONFLICT DO NOTHING`; never overwrite balances at stage 08.
- Apply each unique mock buy fill once. Debit cash/buying power atomically in PostgreSQL; derive position quantity and weighted average cost from fills. Memory uses the same typed rules.
- Mark price is the latest completed run price for a held ticker, falling back to the actual latest fill price with an explicit source label. Cash = opening cash - unique buy fills; equity = cash + market value; unrealized P&L = market value - cost; allocation = market value/equity. Realized P&L is displayed as `해당 없음 · 1차 매수 전용`.
- Dashboard copy always distinguishes `$1,000,000 모의 계좌`, `$1,000 주문 노출 한도`, `mock broker`, `로컬 모의 체결`, and `외부 주문 OFF`.

## Scope IN
- Typed settings, news selection, portfolio snapshot, positions, orders, and fills.
- Memory and PostgreSQL parity; same disposable DB reopen test and second-run no-reset test.
- Existing host-5444 product Compose path and untracked `.env` instructions without writing secrets.
- Responsive server-rendered dashboard and API projections.
- Secret-free evidence and ledger updates.

## Scope OUT (Must NOT have)
- Alpaca Paper calls, real brokerage balance claims, sell orders/accounting, scheduler, T+1–T+5 automatic review, paid news APIs, or multi-account strategy isolation.
- localhost:5432 inspection/use/change; product PostgreSQL uses host 5444 and internal `db:5432` only.
- `.env` contents, provider keys, prompts/raw errors, or URL credentials/query/fragment in evidence/UI.
- Application-side truncation of fetched news detail.

## Open questions
- None. The user approved the visually stronger first-MVP implementation with the announced defaults.

## Approval gate
status: approved
approval: User approved the visually stronger first-MVP approach on 2026-07-14.
