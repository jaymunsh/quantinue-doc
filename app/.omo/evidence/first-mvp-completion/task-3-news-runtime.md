# Task 3: ticker-aware news runtime

## Baseline and RED

- Baseline characterization passed: public `NewsAnalysis` called `rss(run_id)` and analyzed the first SEC press-release item.
- RED command covered five cases: ticker-aware representative, empty/irrelevant zero path, exact Google query, malformed XML.
- RED result: 5 failed for the intended reasons (`first feed item`, unwanted model call, missing `ticker_news`).

## Implemented contract

- Public transport requests `https://news.google.com/rss/search` with `q=(TICKER OR "company")`, `hl=en-US`, `gl=US`, and `ceid=US:en` through the existing bounded HTTP client.
- XML is parsed once at the boundary; malformed XML is a typed `ValidationFailureError`. HTTP and transport failures retain the existing retry classification.
- Selection is deterministic by relevance score, newest publication time, canonical URL, then source order. Exact-word matching avoids short-ticker substring false positives.
- Every fetched item remains in `news_sources` with status, score, reasons, and canonical identity. Duplicate records remain visible but are excluded from representative selection.
- Only the selected record receives model/evidence metadata and becomes `news_source`, the canonical `tb_news_signal` input.
- Empty or zero-relevant feeds complete with score `0.0`, an explicit advisory, and no model call or model metadata.
- External prompt text is prefixed as untrusted data. UI references strip credentials, query, and fragment.

## Verification

- Targeted suite: `52 passed` across news selection, market transport, public pipeline, and terminal detail, including retryable provider outage classification.
- Ruff touched paths: PASS after formatting.
- basedpyright full project: `0 errors, 0 warnings, 0 notes`.
- Existing no-excuse audit observations remain in oversized shared modules; new news status annotations are typed. No unrelated module split was attempted in this scoped task.

## Manual public QA

Actual provider request used `HttpMarketData.ticker_news()` for NVDA without credentials:

- fetched: 100
- relevant: 99
- excluded: 1
- representative: `NVIDIA (NVDA) Reassures Investors Amid Quality Concerns with She - GuruFocus`
- representative reference: `https://news.google.com/rss/articles/...` with query, fragment, and credentials removed
- owned HTTP client closed: true

No Docker, database, `.env`, Alpaca, secret value, or localhost port was accessed.
