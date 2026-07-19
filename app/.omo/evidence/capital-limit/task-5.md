# Task 5 — Control-room app-order exposure panel

## Delivered

- The dashboard gets its `AppOrderExposureSummary` from the typed store contract for the fixed first-cycle account and configured cap.
- The server-rendered panel appears immediately after the four summary cards and renders exact USD strings for configured cap, planned/reserved app-order reference exposure, and remaining planned reference exposure.
- Korean copy explicitly says the panel is Quantinue app-order planning data, not Alpaca balance, positions, or actual market-fill amount.
- `DESIGN.md` documents the `app-order-exposure-panel` primitive and its responsive/safety behavior.

## TDD and static verification

1. Failing first:

   ```text
   uv run pytest -q tests/test_web.py -k app_order_exposure
   2 failed
   ```

   The panel did not yet exist in server-rendered HTML.

2. Passing after implementation:

   ```text
   uv run pytest -q tests/test_web.py
   11 passed

   uv run ruff check src/quantinue/main.py tests/test_web.py
   All checks passed!

   uv run basedpyright src/quantinue/main.py tests/test_web.py
   0 errors, 0 warnings, 0 notes
   ```

`tests/test_web.py` covers both an empty `0.00 / 1000.00` summary and a seeded `600.00 / 400.00` summary, exact formatted currency strings, explicit non-broker wording, and absence of a secret-like fixture value.

## Runtime and browser QA

An isolated server was launched with only safe modes: memory database, fixture data, mock LLM, mock broker, and trading disabled. It used loopback port 8150 only; no Docker, product Compose, PostgreSQL, or host port 5432 was accessed.

The initial detached launcher exited before binding. Runtime evidence confirmed detached-shell lifecycle as the cause, not application startup or configuration: the retained PTY server returned a healthy mock response and completed startup normally. It was stopped after capture.

```text
CAPITAL_LIMIT_BASE_URL=http://127.0.0.1:8150 \
  npx playwright test .omo/evidence/capital-limit/task-5-browser/app-order-exposure.spec.cjs \
  --workers=1 --reporter=line

2 passed
```

The run verified 1440, 1024, 768, and 390 px with JavaScript plus 390 px without JavaScript: panel visibility, all three amounts, non-broker disclaimer, no document horizontal overflow, no browser console/page errors, and keyboard order from skip link to ticker.

Fresh captures:

- `task-5-browser/app-order-exposure-1440.png`
- `task-5-browser/app-order-exposure-1024.png`
- `task-5-browser/app-order-exposure-768.png`
- `task-5-browser/app-order-exposure-390.png`
- `task-5-browser/app-order-exposure-390-no-js.png`

## Independent visual review

Two read-only visual reviewers independently returned PASS with high confidence after inspecting all five captures.

- Design/functional review: real semantic DOM and typed-store rendering, token-consistent responsive collapse, safe wording, no raw provider payload or secret exposure, no-JavaScript baseline PASS.
- CJK/fidelity review: no clipping, tofu, horizontal spill, or unnatural Korean wrapping; the 768/390 one-column layout remains readable.

## Cleanup receipt

- Isolated uvicorn process on port 8150: stopped.
- Port 8150: free after stop.
- Temporary launcher log and Playwright report/result directories: removed.
- No `.env`, Compose, Docker, database, secret, or localhost:5432 state was changed.
