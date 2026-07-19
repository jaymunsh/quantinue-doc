# Chromium browser QA

Fresh captures from the terminal sample run:

- `desktop-1440.png`: 1440 x 1000 viewport, full-page height 10,131 px
- `mobile-390.png`: 390 x 844 viewport, full-page height 24,054 px

Observed in both viewports:

- 11 role cards and 11 non-empty role descriptions
- Role item counts: 01 = 50, 02 = 45, 03 = 10
- Role 02 exclusion fact names all five excluded securities
- Failed terminal progress and `ROLE_TIMEOUT` are visible
- Local LLM mode is visible
- No document-level horizontal overflow
- No console errors, uncaught page errors, or failed HTTP responses
- No content truncation or item-count limit was observed

The exact local model label is not rendered for this failed run because Role 05 did
not produce a durable evidence checkpoint before timing out. This is a known gap in
the requested full sample evidence, not a successful model-output claim.

The only element-level overflow detector hits were intentionally visually-hidden
table accessibility elements (`caption`, and the mobile hidden `thead`).

