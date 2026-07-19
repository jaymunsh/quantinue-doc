# Final sample retry

- Run ID: `3e080ea9e5234707984e92d2a820ece8`
- Runtime: public data, memory persistence, local LLM, mock broker, trading disabled
- Explicit LLM request timeout: 90 seconds
- Terminal status: failed at Role 05
- Safe failure code: `UNEXPECTED_ROLE_FAILURE`

| Role | Status | Duration | Items |
|---|---|---:|---:|
| 01 | completed | 3,802 ms | 50 |
| 02 | completed | 26,111 ms | 45 successes + 5 explicit exclusions |
| 03 | completed | 0 ms | 10 |
| 04 | completed | 535 ms | DFF 3.62% observed |
| 05 | failed | 90,956 ms | 0 |
| 06--11 | pending | - | 0 |

This retry no longer failed with the pipeline-level `ROLE_TIMEOUT`; it reached a
safe `UNEXPECTED_ROLE_FAILURE` at the configured 90-second LLM request boundary.
No raw provider exception or secret is recorded here.

Fresh Chromium captures:

- `desktop-1440.png`: 11 roles, 11 descriptions, 50/45/10 items, no horizontal overflow
- `mobile-390.png`: 11 roles, 11 descriptions, 50/45/10 items, no horizontal overflow
- No browser console or page errors in either viewport
- Local LLM mode and `UNEXPECTED_ROLE_FAILURE` are visible

No PostgreSQL, Docker, live broker, or trading side effect was used.

