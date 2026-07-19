# Final QA repair: evidence reference redaction and responsive money values

## Scope

- Evidence ledger references retain a readable scheme/host/path locator but omit URL query and fragment material before API and dashboard projection.
- The existing safe collection-brief link policy remains unchanged.
- App-order exposure values are indivisible display values: they use `white-space: nowrap` and a local horizontal-overflow fallback rather than mid-number wrapping.

## Regression verification

`uv run pytest -q tests/test_web.py tests/unit/test_api_terminal_detail.py tests/test_dashboard_detail.py`

Result: `29 passed`.

The new dashboard/API regression supplies a synthetic credential-like query and fragment marker and verifies neither reaches the rendered response, while the scheme/host/path locator remains. No real credentials were used or recorded.

`uv run ruff format --check src/quantinue/api/presentation.py tests/test_web.py`

`uv run ruff check src/quantinue/api/presentation.py tests/test_web.py`

`uv run basedpyright src/quantinue/api/presentation.py tests/test_web.py`

Result: all passed; basedpyright reported `0 errors, 0 warnings, 0 notes`.

## Fresh browser QA

An isolated `127.0.0.1:8149` Uvicorn process used only memory storage, fixture data, mock LLM, mock broker, and disabled trading. A completed mock NVDA run rendered the populated app-order exposure panel.

| Viewport | Money display | Document overflow | Browser errors | Capture |
| --- | --- | --- | --- | --- |
| 390 px | three whole values, no mid-number break | none | none | `redaction-responsive-390.png` |
| 768 px | three whole values, no mid-number break | none | none | `redaction-responsive-768.png` |
| 900 px | three whole values, no mid-number break | none | none | `redaction-responsive-900.png` |

At each viewport the browser observed exactly three `.money-value` elements with computed `white-space: nowrap` and `overflow-x: auto`; each value fit without needing local scroll. Korean labels and supporting copy remained legible without clipping.

## Independent visual review

- CJK/responsive pass: PASS, high confidence, no blockers.
- Design/functional pass: PASS, high confidence, no blockers.

## Cleanup

- The temporary Uvicorn process on `127.0.0.1:8149` is stopped after review.
- Its temporary log is removed after port-release verification.
- No Docker, Compose, PostgreSQL, host port 5432, `.env`, or external provider was used.
