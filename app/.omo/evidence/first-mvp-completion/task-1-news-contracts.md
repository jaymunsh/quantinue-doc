# Task 1: ticker-news selection contracts

## TDD evidence

- Baseline characterization first: the existing Role 06 behavior selected the first RSS item and retained both fetched items. Result: `1 passed` before selection production code.
- Red: six new selection scenarios failed at `select_ticker_news` with the intentional `NotImplementedError`; the baseline remained green (`6 failed, 1 passed`).
- Green: the initial focused news suites completed with `42 passed`.
- Security re-review Red: four non-absolute URL variants (`token=...`, relative path, scheme-less host, and `ftp:`) exposed raw text in canonical identity (`4 failed, 8 passed`).
- Security re-review Green: every non-absolute/non-HTTP(S) URL now uses a one-way digest, while the expanded focused suites complete with `46 passed`.

## Contract locked

- Frozen typed statuses: `fetched`, `relevant`, `excluded`, `selected`.
- Exact additive scores: ticker title 50, ticker snippet 25, normalized company title 40, normalized company snippet 20; minimum relevance score 20.
- Ticker/company matching uses normalized whole-token phrases, so short symbols do not match substrings.
- Query strings/fragments and default ports are removed from canonical URLs; GUID takes precedence for identity. Duplicate input rows remain present and receive the typed `duplicate` reason.
- Representative order is score descending, published time descending, canonical URL ascending, then original index for a fully stable tie.
- Empty and irrelevant-only feeds return a typed zero result with no representative.
- Malformed URLs and every URL without an absolute HTTP(S) scheme plus hostname use a one-way digest identity and do not expose their raw value. Valid absolute HTTP(S) URLs retain canonical scheme/host/path identity.
- Prompt-injection-shaped text is scored only as untrusted title/snippet data; it cannot alter selection rules.

## Manual mixed NVDA feed

Executed a minimal in-process driver with one strong NVDA article, one prompt-injection-shaped irrelevant article, one duplicate canonical URL, and one company-snippet article.

```text
selected: NVDA launches Blackwell update
fetched/relevant/excluded: 4/2/2
statuses: selected, excluded, excluded, relevant
scores: 70, 0, 50, 20
reasons:
- ticker_title + company_snippet
- below_minimum_score
- duplicate
- company_snippet
canonical identities:
- url:https://news.example/nvda
- url:https://news.example/other
- url:https://news.example/nvda
- url:https://news.example/supply
```

No network, Docker, database, environment file, credential, or localhost service was accessed. The driver created no temporary artifact.
