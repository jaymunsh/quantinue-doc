# Final public/local sample run result

- Run ID: `95e7ca45a09946a6b4785746c071400f`
- Ticker: `NVDA`
- Safe runtime: public data, memory database, local LLM, mock broker, trading disabled
- Terminal status: `failed`
- Completed checkpoints: `4 / 11`

## Stage timing

| Role | Status | Duration | Safe failure code |
|---|---|---:|---|
| 01 | completed | 2,330 ms | - |
| 02 | completed | 24,123 ms | - |
| 03 | completed | unavailable | - |
| 04 | completed | 206 ms | - |
| 05 | failed | 90,040 ms | `ROLE_TIMEOUT` |

## Observed 50 -> 50 -> 10 boundary

- Role 01 selected and rendered 50 securities; NVDA was retained.
- Role 02 attempted the 50-name universe, produced 45 technical snapshots, and
  explicitly recorded 5 exclusions: SPCX, BRK/B, BRK/A, GOOGM, GOOGN.
- Role 03 rendered 10 ranked candidates and retained NVDA for requested deep analysis.
- Role 04 used a real DFF observation of 3.62%; other macro fields remain documented
  MVP baseline values.
- Role 05 reached the configured local Ollama model but exhausted its bounded response
  deadline. Roles 06--11 did not execute.

## Hypothesis verdicts

1. Public provider failure before screening completed: refuted for this run.
2. Local Ollama failure at roles 05--08: confirmed at Role 05 as `ROLE_TIMEOUT`.
3. Stale server serving the prior implementation: refuted by the observed 50-name
   universe, 45 successes plus 5 exclusions, and 10 ranked candidates.

No PostgreSQL, Docker, live broker, or trading side effect was used.

