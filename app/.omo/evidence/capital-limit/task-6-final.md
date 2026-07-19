# Capital-limit final verification

Started: 2026-07-13 (Asia/Seoul)

## Scope and safeguards

- Product source, tests, `.env`, and existing dirty files are read-only for this verification.
- No inspection, use, stop, or modification of `localhost:5432`, product Docker containers, volumes, or networks.
- PostgreSQL verification is limited to `sh scripts/test_postgres_integration.sh -q` disposable runner; Compose verification is limited to its contract script.
- Isolated runtime journal: planned temporary 127.0.0.1-only Uvicorn process, browser spec, runner output, and captures under `/tmp`; all will be removed before the final verdict.

## Debug hypotheses

1. Concurrent independent reservations could exceed the configured app exposure cap; confirmation would require two accepted reservations whose total exceeds the cap.
2. Canonical lifecycle updates, retry/replay behavior, or terminal state could leave stale exposure reserved; confirmation would require a terminal/replayed state that changes eligible exposure incorrectly.
3. The dashboard could show an unsafe or stale projection; confirmation would require a rendering/API mismatch, sensitive broker-state claim, or a refresh that fails to reflect the terminal state.

## Required gates

| Gate | Result |
| --- | --- |
| `uv run ruff format --check .` | PASS: 159 files already formatted |
| `uv run ruff check .` | PASS |
| `uv run basedpyright` | PASS: 0 errors, 0 warnings, 0 notes |
| `uv run pytest -q` | PASS: 418 passed; 21 expected skips for separately invoked disposable PostgreSQL and opt-in real-key suites |
| `sh scripts/test_postgres_integration.sh -q` | PASS: 24 passed |
| `sh scripts/test_compose_contract.sh` | PASS |

## Runtime-debug audit

### H1: concurrent cap enforcement

- Minimal in-memory runtime submitted two independent 600.00 USD reservations concurrently against a 1,000.00 USD cap.
- Observed outcomes were exactly `acquired` and `rejected`; visible eligible exposure was 600.00 USD.
- Verdict: confirmed protected. No pair of accepted reservations exceeded the configured cap.

### H2: canonical lifecycle, replay, and stale update safety

- One 600.00 USD identity was acquired, then replayed with the same request; the replay result was `replayed`.
- Canonical `submitted` state retained 600.00 USD exposure. Canonical `failed` reduced it to 0 USD.
- A stale later `submitted` update after the terminal failure still reported 0 USD, so it did not reopen exposure.
- Verdict: confirmed protected for this runtime path.

### H3: safe dashboard projection and freshness

- A separate `127.0.0.1:8187` Uvicorn process used only `memory`, `fixture`, `mock`, and `trading_enabled=false` overrides.
- Health endpoint reported mock broker and mock LLM. A real local `POST /api/runs` completed all 11 stages through the mock boundary; its safe order projection reported one filled mock order.
- A fresh dashboard read showed the 1,000.00 USD configured cap, 128.40 USD planned/reserved exposure, 871.60 USD remaining, and the explicit statement that these are not Alpaca balance, positions, or actual fill amounts.
- Playwright manually rendered the dashboard at 1440px and 390px with JavaScript, plus 390px without JavaScript. The panel was visible, contained the cap and safety wording, had no horizontal overflow, and emitted no browser console/page errors.
- Verdict: confirmed protected for the exercised projection and refresh path.

## Cleanup

- Stopped the isolated Uvicorn session on `127.0.0.1:8187` and observed normal shutdown.
- Removed the temporary runtime script, Playwright spec/config, runner output, and temporary screenshots under `/tmp` after visual inspection.
- The first background launch had already exited without binding and was not left running.
- Did not inspect, use, stop, or modify `localhost:5432`, product Docker containers, volumes, networks, or `.env`.

## DoneClaim

PASS. All required gates passed; the isolated safe runtime and real-browser manual QA passed; all three runtime-debug hypotheses were confirmed protected with observed evidence.
