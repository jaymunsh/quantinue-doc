# first-mvp-visual-completion - Work Plan

## TL;DR (For humans)
**What you'll get:** 종목과 실제로 관련된 뉴스를 골라 선정 이유와 대표 LLM 분석을 보여주고, 100만 달러 로컬 모의계좌의 현금·보유종목·평단·손익·비중·주문·체결 이력을 같은 관리자 화면에서 확인합니다. PostgreSQL 모드에서는 서버 재시작 후에도 기록이 유지됩니다.

**Why this approach:** 공시와 종목 뉴스를 분리하고, 화면 숫자를 실제 로컬 체결 원장에서 계산해 시연용 가짜 수치를 없앱니다. 100만 달러 계좌와 기존 1천 달러 주문 안전 한도는 별개로 유지합니다.

**What it will NOT do:** Alpaca나 실제 증권계좌로 주문하지 않습니다. 1차는 매수 전용이며 매도·스케줄러·자동 사후회고는 포함하지 않습니다. 로컬 5432 PostgreSQL은 어떤 경우에도 건드리지 않습니다.

**Effort:** Large
**Risk:** Medium - 뉴스 외부 공급원과 계좌 원장의 memory/PostgreSQL 동등성이 핵심 위험입니다.
**Decisions to sanity-check:** 종목 뉴스는 공개 Google News RSS를 사용하고, 1차 포트폴리오는 매수 전용이며, 전체 수집 뉴스는 실행 원장에 보존하되 대표 1건만 canonical 뉴스 신호로 저장합니다.

Your next move: 이 계획을 실행하거나, 실행 전에 선택적 고정밀 이중 리뷰를 요청할 수 있습니다. Full execution detail follows below.

---

> TL;DR (machine): Large/medium; ticker-search news selection, truthful buy-only $1M simulated ledger, PostgreSQL restart durability, and responsive control-room UI.

## Scope
### Must have
- Ticker/company-aware public RSS transport and deterministic relevance selection.
- Exact collected/relevant/excluded counts, representative identity, and selection reasons.
- Zero-relevant path that skips LLM and emits no false model evidence.
- USD 1,000,000 idempotent opening account; USD 1,000 independent lifetime app-order exposure cap.
- Unique local mock fills debit cash once and produce derived buy-only positions.
- Typed memory/PostgreSQL portfolio snapshots with identical accounting semantics.
- Dashboard cash/equity/holdings/average cost/mark price/P&L/allocation/order/fill history and explicit mode labels.
- PostgreSQL reopen and second-run no-reset verification.
### Must NOT have (guardrails, anti-slop, scope boundaries)
- No Alpaca network call, live/paper credential, real balance claim, sell support, or fabricated realized P&L.
- No localhost:5432 access. Disposable PostgreSQL only through `scripts/test_postgres_integration.sh`; product Compose only host 5444/internal `db:5432`.
- No secret/env dump, URL credential/query/fragment, raw prompt, or raw exception in UI/evidence/ledger.
- No truncation of fetched-news detail and no unrelated dirty-worktree reset/delete/checkout.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD with pytest, real disposable PostgreSQL integration, API checks, and Playwright Chromium.
- Evidence: `.omo/evidence/first-mvp-completion/task-{1..8}-*.{md,json,png}`; never include secrets.

## Execution strategy
### Parallel execution waves
- Wave 1: Todos 1–2 contracts and red tests.
- Wave 2: Todos 3–4 news and simulated-account implementations.
- Wave 3: Todos 5–6 PostgreSQL durability and dashboard.
- Wave 4: Todo 7 integrated runtime evidence.
- Wave 5: Todo 8 final gates and independent reviews.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 3, 7 | 2 |
| 2 | none | 4, 5, 6 | 1 |
| 3 | 1 | 6, 7 | 4 |
| 4 | 2 | 5, 6, 7 | 3 |
| 5 | 2, 4 | 6, 7 | none |
| 6 | 1, 2, 3, 4, 5 | 7 | none |
| 7 | 3, 4, 5, 6 | 8 | none |
| 8 | 7 | final | none |

## Todos
> Implementation + Test = ONE todo. Never separate.
- [x] 1. Lock ticker-news selection contracts with failing tests
  What to do / Must NOT do: Add typed fetched/relevant/excluded/selected status, relevance score/reasons, representative metadata, and a ticker/company-aware market-data protocol. Specify exact token-boundary matching, company normalization, canonical URL/GUID dedupe, minimum score, published-time/url tie breaks, and zero-result semantics. Do not parse display strings to recover state.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 3, 7
  References: `src/quantinue/market_data/models.py:73-99`, `src/quantinue/core/contracts.py:65-83`, `src/quantinue/roles/role_06_news_analysis/service.py:39-115`, `src/quantinue/core/context_detail.py:327-340`, `src/quantinue/api/schemas.py:167-179`.
  Acceptance criteria: New pytest cases fail before implementation and cover exact ticker, company title, snippet-only, short-symbol false positives, duplicate URL/GUID, stale/future timestamp ordering, irrelevant-only, empty feed, and stable ties.
  QA scenarios: `uv run pytest -q tests/unit/test_news_selection.py tests/unit/test_market_data.py tests/unit/test_pipeline_terminal_detail.py`; happy selects the expected representative with exact counts/reasons; failure yields zero relevant and no selected item. Evidence `.omo/evidence/first-mvp-completion/task-1-news-contracts.md`.
  Commit: N | included in later user-approved commit only if requested

- [x] 2. Lock simulated-account accounting and store contracts with failing tests
  What to do / Must NOT do: Add frozen typed account, position, order-history, fill-history, mark-source, and portfolio snapshot contracts plus a RunStore read boundary. Configure opening cash USD 1,000,000 independently from the USD 1,000 exposure cap. Define buy-only accounting and explicit realized-P&L-not-applicable state. Do not add sell support or claim Alpaca data.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 4, 5, 6
  References: `src/quantinue/core/config.py:45-90`, `src/quantinue/db/contracts.py:130-240`, `src/quantinue/db/memory.py`, `src/quantinue/db/postgres_query.py:241-299`, `db/schema.sql:107-130`.
  Acceptance criteria: Red tests assert opening/current cash, unique-fill idempotency, weighted average cost, mark fallback/source/as-of, equity, unrealized P&L, allocation rounding, empty account, rejected/no-order, duplicate fill, and `$1M account != $1k exposure cap`.
  QA scenarios: `uv run pytest -q tests/unit/test_simulated_portfolio.py tests/unit/test_daily_order_cap.py tests/unit/test_capital_limit_pipeline.py`; happy two buys update cash/average cost once; failure duplicate broker fill cannot double-debit. Evidence `.omo/evidence/first-mvp-completion/task-2-portfolio-contracts.md`.
  Commit: N | included in later user-approved commit only if requested

- [x] 3. Implement ticker-search RSS, relevance selection, and truthful Role 06 output
  What to do / Must NOT do: Query Google News RSS with URL-encoded ticker/canonical company name using existing bounded HTTP policy; label the provider honestly and keep SEC press RSS out of ticker-news claims. Select one relevant representative, analyze it once, retain every fetched item in run detail with selected/relevant/excluded status, and persist only the representative canonical signal. On zero relevant, skip LLM, use neutral score 0/advisory evidence, and continue without fake MODEL_OUTPUT evidence. Provider transport failures remain retryable; invalid XML is terminal validation failure.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 6, 7
  References: `src/quantinue/market_data/http_source.py:40-54,389-405`, `src/quantinue/market_data/http_client.py`, `src/quantinue/roles/role_01_universe_screener/contracts.py`, `src/quantinue/roles/role_06_news_analysis/service.py:39-115`, `src/quantinue/db/domain_sources.py:65-140`.
  Acceptance criteria: Focused tests from Todo 1 pass; LLM spy receives exactly the selected news once; irrelevant-only calls it zero times; all fetched entries survive terminal run serialization; canonical DB expectation remains one representative signal.
  QA scenarios: pytest wire fake with relevant+irrelevant+duplicates; minimal public probe for NVDA records URL-safe source/counts without raw query in UI evidence. Evidence `.omo/evidence/first-mvp-completion/task-3-news-runtime.{md,json}`.
  Commit: N | included in later user-approved commit only if requested

- [x] 4. Implement truthful memory simulated-account ledger
  What to do / Must NOT do: Initialize one local account at configured opening cash, retain app-owned orders/fills, apply each MockBroker filled buy exactly once, and expose the typed portfolio snapshot. Use latest completed run price per ticker, then actual latest fill price fallback with source/as-of label. Preserve the existing lifetime exposure behavior where FILLED consumes the USD 1,000 cap. Do not infer real brokerage state.
  Parallelization: Wave 2 | Blocked by: 2 | Blocks: 5, 6, 7
  References: `src/quantinue/db/memory.py`, `src/quantinue/roles/role_09_risk_portfolio/service.py:35-100`, `src/quantinue/roles/role_10_order_execution/service.py:28-74`, `src/quantinue/broker/mock.py:25-51`, `src/quantinue/orchestration/pipeline.py`.
  Acceptance criteria: Focused accounting tests pass; a real in-memory Role09→Role10 completed mock fill reduces cash and creates a holding; rejected/zero-quantity runs do not; repeated reconciliation is unchanged; no Alpaca adapter is constructed/called.
  QA scenarios: minimal runtime driver with one filled buy and one replay; assert exact snapshot and no network broker calls. Evidence `.omo/evidence/first-mvp-completion/task-4-memory-ledger.json`.
  Commit: N | included in later user-approved commit only if requested

- [x] 5. Implement idempotent PostgreSQL account/fill accounting and restart reads
  What to do / Must NOT do: Replace stage-08 balance-reset upsert with concurrency-safe initialize-once at USD 1,000,000. Apply unique fill insertion and account cash/buying-power debit in one transaction; retain equity at fill-time notional parity and derive current equity from marks. Implement portfolio reads by joining canonical account/order/fill rows and latest persisted run prices. Add an additive migration path only if schema changes prove necessary; do not assume fresh schema or rewrite existing user data.
  Parallelization: Wave 3 | Blocked by: 2, 4 | Blocks: 6, 7
  References: `src/quantinue/db/postgres_lifecycle.py:64-110`, `src/quantinue/db/domain.py:190-280`, `src/quantinue/db/postgres_query.py`, `src/quantinue/db/postgres_read.py`, `db/schema.sql:107-130`, `scripts/test_postgres_integration.sh`.
  Acceptance criteria: `scripts/test_postgres_integration.sh` tests initialize once, first fill debit, duplicate fill no debit, close/reopen same DB snapshot equality, second run no reset, and memory/PostgreSQL snapshot parity. Existing append-only/sell-contract tests remain green.
  QA scenarios: `sh scripts/test_postgres_integration.sh -q`; happy reopen retains run/account/fill/position; adversarial concurrent initialization and replay preserve one opening balance/debit. Evidence `.omo/evidence/first-mvp-completion/task-5-postgres-restart.md`.
  Commit: N | included in later user-approved commit only if requested

- [x] 6. Build the visual news-selection and simulated-portfolio control-room panels
  What to do / Must NOT do: Extend typed API/presentation composition and the existing `DESIGN.md` operational-console system. Add mode banner; opening/current cash, equity, remaining exposure; holdings with quantity/average cost/mark source+as-of/market value/unrealized P&L/allocation; buy-only realized-P&L note; orders/fills; empty and no-order states. Role 06 shows exact collected/relevant/excluded/representative counts and reason chips, with every fetched item visible. Do not say Alpaca balance or external execution.
  Parallelization: Wave 3 | Blocked by: 1, 2, 3, 4, 5 | Blocks: 7
  References: `DESIGN.md`, `src/quantinue/api/presentation.py`, `src/quantinue/api/schemas.py`, `src/quantinue/main.py:140-180`, `src/quantinue/web/templates/dashboard.html:72-240`, `src/quantinue/web/static/dashboard.css`.
  Acceptance criteria: Server/API tests assert all financial values and truthful labels; representative reasons are structured, not string-parsed; no URL query/credentials/fragments; all fetched news render; keyboard/focus semantics and native details behavior remain usable.
  QA scenarios: Playwright Chromium at 1440×1000, 768×1024, 375×812 for empty, no-order, and filled portfolios; zero document overflow, clipping, console/page/network errors. Evidence `.omo/evidence/first-mvp-completion/task-6-browser-{desktop,tablet,mobile}.png` plus JSON report.
  Commit: N | included in later user-approved commit only if requested

- [x] 7. Prove public/local/mock and PostgreSQL restart operation end to end
  What to do / Must NOT do: Run one safe public-data + local-Ollama + mock-broker + external-trading-off cycle, record ticker-news selection and local fill/hold decision truthfully, then run the supported PostgreSQL configuration through product Compose host 5444/internal db:5432, restart the app, and verify history/portfolio persistence. If critic yields no order, use a deterministic minimal runtime to prove the local fill accounting without altering production policy. Never print `.env` or inspect localhost:5432.
  Parallelization: Wave 4 | Blocked by: 3, 4, 5, 6 | Blocks: 8
  References: `.env.example`, `compose.yaml`, `src/quantinue/main.py`, `scripts/test_compose_contract.sh`, `.omo/evidence/runtime-debug-audit/ui-50-20-audit.md`.
  Acceptance criteria: Fresh run completes or truthfully reports a typed external/model failure; UI shows exact 50/20/10, news counts/reasons, and portfolio state. App restart retains prior run and portfolio. Health/config labels show postgres/local/mock/trading-off without secrets. No external broker request occurs.
  QA scenarios: actual API trigger plus Playwright filled/hold state; restart and reload same run ID/account snapshot; capture cleanup and owned-container state. Evidence `.omo/evidence/first-mvp-completion/task-7-runtime/`.
  Commit: N | included in later user-approved commit only if requested

- [x] 8. Run final quality, 5-lane review-work, and runtime debug audit
  What to do / Must NOT do: Execute all named gates, then independent goal/constraint, hands-on QA, code quality, security, and context-mining lanes; every lane must PASS. Record at least three runtime hypotheses: ticker false-positive/zero-result, duplicate fill/account reset, and restart/stale portfolio; toggle each with actual disposable PostgreSQL or minimal runtime evidence. Clean owned artifacts, retain requested safe server only, and append a secret-free ledger entry.
  Parallelization: Wave 5 | Blocked by: 7 | Blocks: final
  References: `.omo/plans/quantinue-mvp-implementation.md`, `.omo/start-work/ledger.jsonl`, `.omo/evidence/runtime-debug-audit/`, `scripts/test_postgres_integration.sh`, `scripts/test_compose_contract.sh`.
  Acceptance criteria: `uv run ruff format --check .`; `uv run ruff check .`; `uv run basedpyright`; `uv run pytest -q`; `sh scripts/test_postgres_integration.sh -q`; `sh scripts/test_compose_contract.sh`; actual Docker/API/Chromium responsive QA; all five review lanes PASS; audit and cleanup recorded without secrets.
  QA scenarios: rerun all gates after final edit; adversarial reviewer verifies dirty-worktree preservation, no localhost:5432, no Alpaca, no misleading success output. Evidence `.omo/evidence/first-mvp-completion/final/`.
  Commit: N | user did not request a commit

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Real manual QA
- [x] F4. Scope fidelity
- [x] F5. Security and context-mining reconciliation; all five review-work lanes must PASS

## Commit strategy
- Do not commit unless the user explicitly asks. Preserve unrelated dirty/untracked files.
- If later authorized, split into atomic commits: news contracts/provider; simulated ledger; PostgreSQL durability; dashboard; evidence/docs.

## Success criteria
- NVDA (and another ticker fixture) uses ticker/company-search RSS, never the first unrelated SEC press item.
- Role 06 visibly distinguishes every fetched, relevant, excluded, and representative item; zero relevant causes no LLM call or false model evidence.
- The dashboard truthfully shows a USD 1,000,000 local simulated account and independent USD 1,000 lifetime app exposure cap.
- One unique local mock buy changes cash and holdings exactly once; buy-only/realized-P&L limitation is explicit.
- Memory and PostgreSQL produce equivalent snapshots; PostgreSQL restart and second execution do not reset the opening account.
- No Alpaca call, localhost:5432 access, secret, raw prompt/error, or unrelated worktree mutation occurs.
- All six named quality/integration gates, Docker/API/three-viewport Chromium QA, five review-work lanes, and three-hypothesis runtime audit PASS.
