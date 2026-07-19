# Todo 7 runtime E2E verdict — PASS

## Actual public data + local Ollama + mock broker

The fresh NVDA API run `155fe0d9b6814e819dd78925d02079ec` completed all eleven roles through the real HTTP adapter boundary. Runtime logs showed successful NASDAQ screener and historical-price requests, SEC submissions, Google News RSS, and three successful local Ollama chat-completion calls. The terminal result retained model provider `local` and model `qwen3.6:35b-a3b-nvfp4`.

The observed stage contract was exactly 50 universe members, 20 technical-analysis snapshots, and 10 daily candidates. Role 06 retained all 100 fetched rows: 93 relevant, 7 excluded, and exactly one representative. Structured match reasons included ticker-title, ticker-snippet, and below-minimum-score. The model proposed `hold`; Role 09 truthfully skipped the order, Role 10 recorded zero shares, and the local account remained USD 1,000,000 with no positions, orders, or fills.

The first attempt exposed a real provider-boundary defect: one sanitized Google News redirect path exceeded the original 512-character display contract and raised a Pydantic validation error after the model calls. Failing regression tests reproduced the exact boundary. The corrected contract preserves credential/query/fragment-free safe URLs in full up to 4,096 characters, so a clickable URL is never truncated. Inputs beyond that boundary become a stable, collision-resistant `long-reference:sha256:` display identity with no clickable destination. Distinct over-boundary URLs, secret removal, unchanged short URLs, and browser rendering are covered by focused tests. The final terminal-detail/dashboard/web set passed 29 tests, followed by Ruff and basedpyright. The identical real runtime then returned HTTP 201 and completed.

## Product Compose PostgreSQL durability

The supported existing Compose project uses the product database on host 5444 and internal service DNS. Its database stayed healthy. The web image was rebuilt while preserving the existing named volume. A fresh fixture/mock run `416846b9a4cb461f8f4a73bbd6c10a5c` completed all eleven roles and created one truthful local simulated fill: one NVDA share at USD 128.40. The durable account changed from USD 1,000,000 to USD 999,871.60 and exposed the matching position, order, and fill.

Only the web service was restarted. After restart, the same run ID remained completed with progress 11 and the same order identity. The complete account, position, order, and fill projection was unchanged.

A supported one-off Compose web service on the same product network proved the combined `postgres + local LLM + mock broker + external trading off` contract. Its health endpoint reported local/mock; the dashboard reported `POSTGRES + 로컬 모의 계좌`, `mock broker`, and `외부 주문 OFF`. A fresh AAPL run completed all eleven stages, recorded the local model lineage, and container logs showed three HTTP 200 Ollama calls through `host.docker.internal`. No Alpaca request occurred.

## Browser QA

Playwright loaded the real public/local dashboard and the restarted PostgreSQL dashboard at 1440, 768, and 375 CSS pixels. At every viewport `document.body.scrollWidth` equaled `innerWidth`; no console or page errors occurred. The public dashboard rendered the 50/20/10 summaries and all 100 structured news rows. The PostgreSQL dashboard rendered USD 999,871.60 cash, NVDA, and the filled order/position. Fresh screenshots are in this directory, including viewport-level pages and focused runtime, portfolio, Role 06, and Role 09 captures.

## Cleanup and safety

- The failed, agent-owned alternate Compose project and its empty volume/network were removed.
- The short-lived local-mode one-off web container was removed after proof.
- The agent-owned Uvicorn process on 8127 was stopped.
- The established product `app` database and web services were preserved; the durable product volume was not removed.
- No `.env` file or credential was read or printed. No secret value was generated or recorded.
- Host port 5432 was never inspected or used. No Alpaca endpoint was called.
