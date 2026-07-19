# Todo 6 Role06 structured-contract fix

## Result

- Replaced localized delimiter parsing with `NewsSelectionDetail` and `NewsSelectionDetailItem` from pipeline context through terminal persistence and API projection.
- Removed Role06 localized serialization entirely: Role06 `RoleDetail.items` is empty and `_news_items` no longer exists.
- Representative identity comes from the actual `context.news_source` object; its display status is normalized to `selected` even when the source row arrived as `fetched`.
- Typed validators reject representative/status disagreement, unpartitioned `fetched` display rows, and multiple representatives.
- Counts are derived from the complete projected item tuple; fetched count and visible item count cannot diverge through parse drops.
- Structured status, representative flag, score, reasons, title, publication value, and sanitized reference are retained independently.
- Existing account UI and runtime settings were unchanged.

## Verification

- RED: `uv run pytest -q tests/test_dashboard_detail.py` failed because the structured contract did not exist.
- Focused regression: 50 passed across dashboard, live progress, terminal detail, API detail, and news selection tests.
- Adversarial coverage: delimiter-bearing title/reason/publication values, malformed URL, query/fragment removal, and two input rows producing two API rows.
- Actual completed fixture coverage: fetched 1, relevant 1, excluded 0, representative 1, with `fetched = relevant + excluded`.
- `uv run ruff format --check` and focused `uv run ruff check`: pass.
- `uv run basedpyright`: 0 errors, 0 warnings, 0 notes.
- Fresh isolated Chromium QA at 1440x1000, 768x1024, and 375x812 for empty and filled states: no document overflow, console error, or page error.
- Browser evidence refreshed in `task-6-browser-qa.json` and six `task-6-*.png` captures.

## Cleanup

- Isolated fixture/memory/mock/trading-off server on `127.0.0.1:8765` stopped cleanly.
- Chromium closed by the QA runner.
- No Docker, database, `.env`, credential, Alpaca, or localhost database port was accessed.
