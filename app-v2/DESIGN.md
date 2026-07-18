# Quantinue Control Room Design Contract

## 1. Product and direction

Quantinue is a restrained, GitHub-like operational surface for inspecting an 01–11 paper-trading pipeline. It favors dense evidence, legible state, and safe actions over decorative dashboard effects. The memorable element is the ordered stage ledger: every state, attempt, source, and lineage remains readable as one coherent audit trail.

## 2. Users and critical tasks

- Operator: starts a fixture run, identifies current/failed/retrying work, and verifies checkpoints without exposing secrets.
- Reviewer: traces a judgment through evidence parents, order idempotency/reconciliation, and T+5 review.
- Keyboard or mobile user: performs the same tasks at 390 px, zoomed text, reduced motion, or without pointer precision.
- Safety constraint: raw prompts, credentials, provider payloads, and raw exception messages never appear in the DOM.

## 3. Foundations and tokens

- Typography: system UI with Apple SD Gothic Neo/Noto Sans KR/Malgun Gothic CJK fallbacks; tabular operational values use the same family with `font-variant-numeric: tabular-nums`.
- Color roles: canvas `#f6f8fa`; surface/white `#ffffff`; ink `#1f2328`; secondary ink `#57606a`; border `#d0d7de`; action blue `#0969da`/`#0550ae`; blue depth `#0b3a6b`; blue soft ink/background `#dbe9ff`/`#ddf4ff`; button hover `#033d8b`; success `#1a7f37`/`#dafbe1`; warning `#9a6700`/`#fff8c5`; danger `#cf222e`/`#ffebe9`; neutral slate `#eaeef2`. Focus rings use action blue at 25% for inputs and 28% for buttons. Status color is always paired with text.
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
- `live-progress-panel`: active-run only, server-rendered current/next role plus completed count, status and redacted error code. Its narrow blue rule distinguishes a live execution from terminal audit records; it remains fully meaningful when JavaScript is unavailable.
- `stage-record`: component marker, role and summary, state badge, attempt/timing metadata, checkpoint state, and associated evidence references.
- `evidence-record`: evidence ID, source/reference, observed/captured times, confidence, and parent lineage. Long identifiers wrap safely.
- `decision-brief`: latest-run collection-to-critic panel. It compares disclosure/news scores, keeps long rationale in native `details`, uses an external link only when the safe view supplies an `http(s)` href, and makes unavailable or legacy detail explicit.
- `role-detail-record`: an ordered 01--11 execution-ledger block. It shows a stage marker, durable checkpoint summary, semantic status, compact structured facts, and every complete item produced by the role without character-count or item-count truncation. It remains deliberately long when a role produced many records; facts collapse from three to two to one column at desktop, tablet, and mobile widths, while machine values wrap inside their own row.
- `role-phase-divider`: a labelled rule before collection, judgment, and execution groups so the eleven-role ledger reads as four explicit phases without splitting the audit trail.
- `decision-strip`: a high-salience but non-decorative summary for risk sizing, simulated execution, and future T+5 review. It states what was decided, whether money moved, and what happens next without relying on color.
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
- Minimum contrast target is WCAG 2.2 AA; keyboard flow is skip link → ticker → run → audit content.
- Error output is a stable redacted error code. No secret, raw prompt, stack trace, exception string, or provider response enters rendered HTML.

## 8. Accepted design debt and handoff

- The active-run panel is progressive enhancement only; no-JavaScript users receive the durable snapshot rendered at navigation time.
- External Google font loading is optional enhancement; the local system stack remains fully usable offline.
- Any new state or primitive must be added here before CSS/template use and verified at desktop, tablet, and mobile sizes.
