# Todo 6 DoneClaim

## Delivered

- Typed `/api/portfolio` projection for the app-owned buy-only simulated account.
- Runtime truth banner: persistence mode, local simulated account, mock broker, external orders off.
- Opening cash, current cash, equity, buying power, marked holdings, unrealized P&L, allocation, order history, fill history, and explicit buy-only realized-P&L state.
- Role 06 fetched/relevant/excluded/representative counters and complete per-item status, score, reason, publication time, and sanitized source rendering.
- Responsive semantic tables/cards and truthful empty/no-order/no-fill states.

## Verification

- Focused regression: 50 passed.
- Ruff format/check: pass.
- basedpyright: 0 errors, 0 warnings.
- Chromium 1440x1000, 768x1024, and 375x812: empty and filled states pass.
- All six captures: document width equals viewport width; no console or page errors.
- Tablet-only overflow region is keyboard focusable with a verified visible focus ring; desktop and mobile avoid a redundant tab stop.
- Independent design-system/functional and visual/CJK reviewers: PASS, confidence high, no blockers.
- Browser evidence: `task-6-browser-qa.json` and six `task-6-*.png` files.

## Cleanup

- Isolated `127.0.0.1:8765` fixture/memory/mock/trading-off server stopped cleanly.
- Chromium closed by the QA runner.
- No Docker, database, `.env`, secret, Alpaca, or localhost database port was accessed.
