# Task 4 independent review: Role 09/Role 10 app-exposure pipeline

## Verdict

**PASS** for the finalized in-memory pipeline contract reviewed here.

This review covers only Todo 4's Role 09, Role 10, configured factory wiring, and
the current in-memory exposure lifecycle. It does not assert an actual market-fill
cost limit or broker-account balance limit.

## Static contract evidence

- `src/quantinue/roles/role_09_risk_portfolio/service.py:52` passes the configured
  `Decimal` `max_app_order_exposure_usd` into the legacy float-only risk-sizing
  contract at its single boundary. There is no `10_000` / `10000` sizing constant
  in the Role 09/Role 10/factory application path; the relevant defaults are
  `Decimal("1000.00")`.
- `src/quantinue/roles/role_09_risk_portfolio/service.py:60-94` reserves before
  submission, converts a denied reservation to zero quantity, and writes the
  observable planned-exposure/daily-limit summary.
- `src/quantinue/roles/role_10_order_execution/service.py:65-104` normalizes the
  returned broker result to the canonical status before retaining it and
  reconciling once per Role 10 execution: `accepted`/`submitted` map to
  `submitted`, `filled` to `filled`, `rejected`/`failed` to `failed`, `canceled`
  to `canceled`, and `planned` to `planned`.
- `src/quantinue/db/memory.py:244-262` plus
  `src/quantinue/db/memory_exposure.py:20-30` keep a terminal exposure state from
  regressing on a later stale failure/cancel request.
- `.env.example:57-60` expressly calls this app-managed planned buy-order
  exposure, not broker cash, portfolio value, or fill cost. The Alpaca payload is
  a market order, so the code correctly makes no actual-fill-price-cap claim.

## Focused automated verification

Command:

```sh
uv run pytest -q tests/unit/test_capital_limit_pipeline.py \
  tests/unit/test_daily_order_cap.py \
  tests/unit/test_risk_order_contracts.py \
  tests/unit/test_broker_provider.py \
  tests/unit/test_persistence_factory.py
```

Observed:

```text
53 passed in 1.12s
```

Changed-scope type check:

```sh
uv run basedpyright src/quantinue/roles/role_09_risk_portfolio/service.py \
  src/quantinue/roles/role_09_risk_portfolio/contracts.py \
  src/quantinue/roles/role_10_order_execution/service.py \
  src/quantinue/roles/role_10_order_execution/contracts.py \
  src/quantinue/orchestration/factory.py \
  tests/unit/test_capital_limit_pipeline.py \
  tests/unit/test_daily_order_cap.py \
  tests/unit/test_persistence_factory.py
```

Observed: `0 errors, 0 warnings, 0 notes`.

## Runtime audit (direct in-memory pipeline)

The probe used the real deterministic analyzer, Role 09, Role 10,
`InMemoryRunStore`, and a recording in-memory broker. It created no listener,
container, database connection, or secret value.

### H1: Configured $1,000 replaces the old hard-coded sizing basis

Observed:

```text
ACCEPTED_REPLAY {'quantity': 1, 'same_terminal_object': True,
 'broker_submissions': 1,
 'reconciliations': [('q-a1-s1311724674', 'submitted')],
 'planned_or_reserved': '128.40', 'remaining': '871.60'}
```

The deterministic run planned one share from the configured first-cycle policy;
the store reports exact Decimal reference exposure. This is planned reference
exposure, not a statement about a fill amount.

### H2: A full cap rejection produces zero quantity and no broker side effect

Observed after pre-reserving exactly `1000.00` under account 1:

```text
CAP_REJECTION {'reservation_outcome': 'acquired', 'order_is_none': True,
 'role09_summary': '수량 0, 앱 주문 계획 노출 한도 또는 일일 신규 주문 한도 도달',
 'broker_submissions': 0, 'reconciliations': 0}
```

This confirms denial is visible before Role 10 and cannot submit a buy order.

### H3: Lifecycle mapping is idempotent under ordinary pipeline replay and a stale
terminal failure cannot release a fill

Observed durable rejection mapping:

```text
REJECTED_MAPPING {'status': 'rejected',
 'reconciliations': [('q-a1-s1311724674', 'failed')],
 'planned_or_reserved': '0', 'remaining': '1000.00'}
```

Observed terminal stale-state protection:

```text
STALE_TERMINAL {'filled_planned_or_reserved': '500.00',
 'after_stale_failed': '500.00', 'terminal_preserved': True}
```

The accepted replay in H1 also made exactly one broker submission and one
reconciliation call for the same terminal pipeline request.

### Canonical-result coordination recheck

After the Role 10 canonical-status coordination change, a second direct
in-memory probe observed the retained run status, stored lifecycle status,
submission count, and replay result for every currently supported canonical
input:

```text
CANONICAL_STATUS accepted submitted ['submitted'] 1 True
CANONICAL_STATUS submitted submitted ['submitted'] 1 True
CANONICAL_STATUS filled filled ['filled'] 1 True
CANONICAL_STATUS rejected failed ['failed'] 1 True
CANONICAL_STATUS failed failed ['failed'] 1 True
CANONICAL_STATUS canceled canceled ['canceled'] 1 True
CANONICAL_STATUS planned planned ['planned'] 1 True
```

This removes the prior mismatch where the retained order result could expose a
noncanonical `accepted` or `rejected` value while the exposure ledger used a
different canonical value.

## Scope, worktree, and cleanup

- No host `localhost:5432`, Docker container, Docker volume, Docker network,
  `.env`, broker credential, or market API was read or touched.
- No source or test file was edited by this review. The only review artifact is
  this evidence file.
- The pre-existing shared worktree is dirty and includes tracked `__pycache__`
  entries. Running the focused Python checks can update those generated entries;
  they were not reverted because the reviewer must not overwrite another
  worker's changes. Parent orchestration should keep generated-bytecode cleanup
  separate from product-source review.
- `git diff --check` exited successfully. No temporary script, process, or
  listener remains from the runtime probe.
