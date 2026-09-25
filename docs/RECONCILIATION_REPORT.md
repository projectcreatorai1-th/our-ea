# RECONCILIATION REPORT

## Scope compared

EA-tracked positions (journal + basket) vs broker/adapter positions, per
position: existence both sides, side, volume (1e-9 tolerance), entry price
(1e-6). Difference taxonomy: `POSITION_MISSING_AT_BROKER`,
`UNEXPECTED_BROKER_POSITION`, `VOLUME_MISMATCH`, `SIDE_MISMATCH`,
`PRICE_MISMATCH`.

## Outcomes and enforcement

- `MATCH` → trading continues.
- `MISMATCH` → `block_new_orders = True`: the runtime drops ticks with a
  `RISK_BLOCK(RECONCILIATION_BLOCK)` journal entry; `RESUME` is refused
  until a MATCH reconcile clears the block (operator-visible loop).
- Result journaled (`RECONCILIATION` event) and audited via the RECONCILE
  command (`OK`/`BLOCKED`).

## Tests

- MATCH passthrough; volume mismatch blocks; unexpected broker position
  detected; block + RESUME refusal + clear-on-match lifecycle
  (`tests/test_safety_layers.py::TestReconciliation`,
  `tests/test_runtime_scenarios.py::TestServiceCommands`).

## Honest boundary

With the gateway server absent, the "broker view" is the adapter's own
position book (paper/simulation). True MT5-vs-EA reconciliation ships with
the gateway bridge (BLOCK-1) — the engine, tolerances and block semantics
are already final.
