# Fresh NVDA sample runtime verdict

- Verdict: **PASS**
- Run ID: `f5117bb1d4024989bbdbfd67b7b7bae0`
- Started: 2026-07-14 11:09:19 KST
- Finished: 2026-07-14 11:10:01 KST
- Runtime: public market data, memory database, local Ollama `qwen3.6:35b-a3b-nvfp4`, mock broker, trading disabled, 90-second LLM timeout
- Safety: no Docker or PostgreSQL endpoint was used; no provider secret was recorded

## Stage result

All roles 01 through 11 completed without a failure code.

| Role | Result | Duration |
|---|---|---:|
| 01 Universe | 50 companies, NVDA included | 1.769 s |
| 02 Technical | 45 real-candle analyses; 5 explicit exclusions | 29.981 s |
| 03 Daily screen | 10 candidates, NVDA retained | <1 ms |
| 04 Macro | actual DFF 3.62%, neutral | 0.705 s |
| 05 Filing LLM | neutral result | 4.680 s |
| 06 News LLM | positive result | 2.169 s |
| 07 Strategist LLM | hold, conviction 0.396 | 2.643 s |
| 08 Critic | hold / no-buy gate | <1 ms |
| 09 Risk | 0-share skipped plan | <1 ms |
| 10 Order | skipped | <1 ms |
| 11 Review | no_trade | <1 ms |

The completed API record is `final.json`. Intermediate observation `poll-001.json` records Role 05 as the current live stage, proving stage-by-stage progress was exposed while the run was active.

## Browser QA

Real Chromium checks passed at 1440x1000 and 390x844:

- 11 role blocks and 11 completed states
- exact untruncated item counts: 50 / 45 / 10
- local model lineage visible
- no horizontal overflow
- no browser console or page errors

Artifacts: `desktop-full.png`, `desktop-roles.png`, `mobile-full.png`, `mobile-roles.png`, `playwright.log`.
