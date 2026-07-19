# Quantinue Control Room Design Contract

## 1. Product and direction

Quantinue is a restrained, GitHub-like operational surface for inspecting an 01–11 paper-trading pipeline. It favors dense evidence, legible state, and safe actions over decorative dashboard effects. The memorable element is the ordered stage ledger: every state, attempt, source, and lineage remains readable as one coherent audit trail.

The page title is descriptive rather than branded-only: “투자 판단 파이프라인 운영 문서” names the product task, and its subtitle states the 11-stage span. Runtime LLM/broker/trading state appears once in the truth banner instead of being duplicated as header pills. On desktop the header text aligns with the main document column beside the TOC; below 1200 px it aligns with the single content column.

## 2. Users and critical tasks

- Operator: starts an automatic 50-to-20-to-20 screening run, identifies current/failed/retrying work, and verifies checkpoints without exposing secrets.
- Reviewer: traces a judgment through evidence parents, order idempotency/reconciliation, and T+5 review.
- Keyboard or mobile user: performs the same tasks at 390 px, zoomed text, reduced motion, or without pointer precision.
- Safety constraint: raw prompts, credentials, provider payloads, and raw exception messages never appear in the DOM.

## 3. Foundations and tokens

- Typography: system UI with Apple SD Gothic Neo/Noto Sans KR/Malgun Gothic CJK fallbacks; tabular operational values use the same family with `font-variant-numeric: tabular-nums`.
- Documentation type uses the existing 10/12/13/14/15/16/22 px scale with 600-weight definition copy and 800-weight labels. The overview reading measure is capped at 96 characters; its label column is 96 px and its completion-criterion label is 160 px so explanatory prose retains a stable measure.
- Color roles: canvas `#f6f8fa`; surface/white `#ffffff`; ink `#1f2328`; secondary ink `#57606a`; border `#d0d7de`; action blue `#0969da`/`#0550ae`; blue depth `#0b3a6b`; blue soft ink/background `#dbe9ff`/`#ddf4ff`; button hover `#033d8b`; success `#1a7f37`/`#dafbe1`, with `#116329` reserved for small success text on blue-soft surfaces; warning `#9a6700`/`#fff8c5`; danger `#cf222e`/`#ffebe9`; neutral slate `#eaeef2`. Focus rings use action blue at 25% for inputs and 28% for buttons. Status color is always paired with text.
- Spacing scale: 4, 8, 12, 16, 20, 24, 32, 48, 64 px. Card radius 12 px; controls and stage markers 8 px; status pills are fully rounded.
- Elevation is intentionally quiet: one-pixel borders define operational grouping; the primary progress surface may use a blue depth gradient. No decorative blur or ambient animation.

## 4. Layout and responsive behavior

- Content width is capped at 1180 px with 24 px desktop and 14 px mobile gutters.
- Desktop uses four summary columns and two stage columns. At 900 px these collapse to two summary columns and one stage column; at 640 px command controls stack.
- At 640 px, run history changes from a wide table to labelled record cards while retaining semantic table markup for larger viewports.
- Nothing may create document-level horizontal overflow at 390, 768, 1024, or 1440 px.

## 5. Reusable primitives and states

- `mode-pill`: bordered runtime safety mode; neutral only.
- `status-badge`: pending, running, retrying, blocked, completed, failed, cancelled/skipped. Each state has text plus a semantic color; running/retrying expose text status without relying on animation.
- `summary-card`: label, primary value, explanatory copy; progress variant uses the blue gradient.
- `app-order-exposure-panel`: server-rendered three-value app-order budget panel with configured cap, planned/reserved reference exposure, and remaining reference exposure. It explicitly distinguishes these Quantinue planning values from broker balance, positions, and actual fill amount; its values use tabular numerals, never break within one amount, and use local horizontal overflow only if a value exceeds its card. The panel collapses to one readable column at 900 px.
- `runtime-contract`: compact, always-visible truth banner naming persistence mode, local simulated account, mock broker, and external-order lock. It never claims an Alpaca balance.
- `portfolio-panel`: local buy-only account summary with opening cash, current cash, equity, buying power, marked positions, order history, fill history, and explicit non-applicable realized P&L. Totals use tabular numerals; holdings use a semantic table that becomes labelled rows on mobile; empty states state what event will populate them.
- `news-selection-record`: Role 06 summary counters and one complete card per fetched item. Selected, relevant, and excluded statuses use text and restrained semantic rails; selection score, reasons, publication time, and sanitized source remain visible without item-count truncation.
- `batch-news-signal-ledger`: a role-owned, default-collapsed native disclosure containing one row per latest automatic-batch candidate, ordered by candidate rank. Its closed summary states the table, candidate count, and open action; opening exposes the ticker, pipeline status, aggregate summary, and every available signal-shaped fact. Candidates that failed before the role remain visible with an explicit unavailable state instead of disappearing.
- `live-progress-panel`: active-run only, server-rendered current/next role plus completed count, status and redacted error code. Its narrow blue rule distinguishes a live execution from terminal audit records; it remains fully meaningful when JavaScript is unavailable.
- `stage-record`: component marker, role and summary, state badge, attempt/timing metadata, checkpoint state, and associated evidence references.
- `evidence-record`: evidence ID, source/reference, observed/captured times, confidence, and parent lineage. Long identifiers wrap safely.
- `decision-brief`: latest-run collection-to-critic panel. It compares disclosure/news scores, keeps long rationale in native `details`, uses an external link only when the safe view supplies an `http(s)` href, and makes unavailable or legacy detail explicit.
- `role-detail-record`: an ordered 01--11 execution-ledger block. It shows a stage marker, durable checkpoint summary, semantic status, compact structured facts, and every complete item produced by the role without character-count or item-count truncation. It remains deliberately long when a role produced many records; facts collapse from three to two to one column at desktop, tablet, and mobile widths, while machine values wrap inside their own row.
- Ordered detail counters reserve enough inline space for at least four decimal digits and never wrap a counter across lines.
- `phase-rail`: four labelled 01--11 groups (탐색 01--03, 근거 04--06, 판단 07--08, 실행 09--11). Color, range text, and a downward connector jointly communicate sequence.
- `pipeline-walkthrough`: a four-step, plain-language map shown before the detailed ledger. Each card answers “무엇을 넣고 무엇이 나오는가”; arrows become downward connectors on narrow screens.
- `role-method-panel`: a compact “어디서 / 어떤 기준 / 무엇을 남김” explanation before the actual facts for roles 05--10. Fixed MVP methodology and this run's values remain visually distinct.
- `role-trace-map`: an always-visible three-column ledger inside every 01--11 role. It names the concrete input/source, the exact deterministic rule or formula, and the canonical PostgreSQL destination. Formulae use a bounded monospace inset; table names are never implied by prose.
- `schema-flow-detail`: a native collapsible database handoff diagram for roles 05--08. It distinguishes raw-source rows, per-cycle signal rows, runtime context values, and durable decision rows; shows primary/foreign/unique keys and relevant column groups; and explicitly states that the strategist consumes validated in-memory context while PostgreSQL preserves the same run for audit and refresh persistence. Cards stack vertically without horizontal overflow on narrow screens.
- `data-disclosure`: a native, default-collapsed detail wrapper for high-volume execution facts and item lists. Its summary is a 44 px minimum-height control with a plain-language title, item/field count, and explicit “펼쳐 보기” cue; opening it reveals the complete untruncated dataset. Aggregate counters and decision summaries remain outside so the pipeline is understandable without expanding every record.
- `run-scope-banner`: distinguishes a 50-to-20-to-20 automatic batch from the legacy single-ticker API path and states the observed row counts, preventing one diagnostic run from masquerading as the daily batch.
- `role-phase-divider`: a labelled rule before collection, judgment, and execution groups so the eleven-role ledger reads as four explicit phases without splitting the audit trail.
- `decision-strip`: a high-salience but non-decorative summary for risk sizing, simulated execution, and future T+5 review. It states what was decided, whether money moved, and what happens next without relying on color.
- `automatic-run-control`: one primary action that starts the 50-to-20-to-20 daily screening flow without a ticker field. The compact flow legend explains the three selection populations before execution.
- `run-launch-feedback`: progressive-enhancement status region directly below the automatic-run action. It appears immediately on submit, disables duplicate launches, and then projects the real `/api/runs` state as an 01--11 stage rail, current-role explanation, candidate completion count, and retry/failure message. Progress motion uses only `transform`; status remains legible without motion or color.
- `candidate-board`: a secondary drill-down placed after the automatic-run overview, latest-run summary, and local-account overview. Candidates always occupy one full-width row so expanding one record never creates a mismatched two-column reading order; each row retains rank, pipeline-stage progress, decision, confidence, and a native detailed audit disclosure with no gap between metrics and its collapsed control. Stage 11 is labelled as review registration, never as a completed T+5 review.
- `page-toc`: a desktop-only sticky left navigation that mirrors the page's actual reading order from runtime contract and automatic-run summary through the local account, candidate drill-down, all 01--11 roles, evidence, safety, and history. The four pipeline phases act as quiet separators, while every role is listed vertically with its number and full Korean name; the current visible destination receives `aria-current="location"`. Below 1200 px the TOC is removed from layout so it never compresses the operational content.
- `mvp-overview-panel`: the page's document-style introduction, placed immediately after the runtime truth banner and before execution controls. It defines the MVP in one plain-language sentence, distinguishes its validation goal from profit proof or production automation, maps input → selection → judgment → durable simulated result, states implemented and excluded scope, gives a reading order, and closes with an explicit completion criterion. It describes local-LLM use as narrow structured judgment rather than broad AI automation.
- `safety-record`: redacted failure code, checkpoint count, and order client ID/reconciliation state. Raw failure text is forbidden.
- `empty-state`: one clear next action, no fabricated metrics.
- `responsive-table`: semantic table on desktop; labelled card rows on narrow screens.

Required product states are empty, running, retrying, failed, and completed. State harnesses/tests must exercise all five before release.

## 6. Interaction and motion

- Native form submission and server rendering are the baseline; JavaScript is not required. An active run adds a same-origin `/api/runs` poll every 1.5 seconds that updates only the bounded live-progress fields. It clears at a terminal state; failed polls back off to 6 seconds and announce only that a retry is pending.
- All controls have visible `:focus-visible` treatment with at least a 3 px focus ring and usable 44 px mobile target height.
- The skip link is the first focusable control. Table scroll containers are focusable only when needed.
- Motion is limited to button press feedback. Under `prefers-reduced-motion: reduce`, transitions and smooth scrolling are disabled.

## 7. Accessibility and content constraints

- Semantic landmarks, ordered stage lists, table headers, captions, and explicit form labels are mandatory.
- Status, confidence, and failure meaning must not depend on color alone. Dynamic status summaries use polite live-region semantics where appropriate.
- Korean phrases use `word-break: keep-all`; identifiers and URLs use `overflow-wrap: anywhere` so CJK copy is not split merely to accommodate machine text.
- Minimum contrast target is WCAG 2.2 AA; keyboard flow is skip link → automatic run → audit content.
- Error output is a stable redacted error code. No secret, raw prompt, stack trace, exception string, or provider response enters rendered HTML.

## 8. Accepted design debt and handoff

- The active-run panel is progressive enhancement only; no-JavaScript users receive the durable snapshot rendered at navigation time.
- External Google font loading is optional enhancement; the local system stack remains fully usable offline.
- Any new state or primitive must be added here before CSS/template use and verified at desktop, tablet, and mobile sizes.
