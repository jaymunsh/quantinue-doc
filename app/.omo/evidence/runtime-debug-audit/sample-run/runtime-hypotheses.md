# Final sample runtime hypotheses

Date: 2026-07-14 (Asia/Seoul)

Safe runtime: public data, in-memory persistence, configured local Ollama model,
mock broker, trading disabled. No PostgreSQL or Docker access is part of this run.

1. Public data providers may fail before the 50 -> 50 -> 10 boundary completes.
   Distinguishing evidence: terminal component and safe failure code from the run API.
2. Ollama may be unreachable or return an invalid role payload at roles 05--08.
   Distinguishing evidence: health mode, terminal component, model label, and safe failure code.
3. A stale server process may not contain the latest 50 -> 50 -> 10 implementation.
   Distinguishing evidence: restart the workspace-owned 8001 process, then inspect role item counts.

