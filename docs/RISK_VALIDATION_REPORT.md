# RISK VALIDATION REPORT

## Gate independence (Phase 22 requirement)

The RiskGuard is constructed from `parameter_spec.json` (source =
`our_ea_policy`), has no strategy knowledge, and sits **after** the decision
engine and **before** every adapter call. Nothing can reach an adapter
without a risk decision; the kill switch precedes even that.

## Limits enforced (parameter registry SSOT)

| Limit | Value | Check |
|---|---|---|
| MaxPositions | 12 | per-intent open-position cap |
| MaxGridDepth | 12 | per-side ladder cap (stops soft-martingale growth) |
| MaxTotalLot | 2.0 | projected total exposure cap |
| MaxLossUSD | 50.0 | cycle gross surveillance → kill |
| MaxDrawdownPct | 30 % | balance-from-peak surveillance → kill |
| MaxSpreadUSD | 0.6 | per-intent (data gate also rejects wider ticks) |
| MaxConsecutiveFailures | 3 | adapter failures → kill |
| MaxTickVolatilityUSD | 25.0 | data-quality gate (tick rejected, no trade) |

## Layered defense verified in tests

1. **Data gate**: impossible price, inverted quote, spread anomaly,
   volatility spike, regressing/future timestamp, stale tick →
   `TICK_REJECTED`, no trade (bad data ⇒ no trade, Phase 89).
2. **Surveillance**: max-loss and drawdown engage the kill switch and
   SAFE_STOP; three consecutive adapter failures engage it.
3. **Intent gate**: every rule individually tested to block (Scenario I)
   — MAX_SPREAD, MAX_POSITIONS, MAX_GRID_DEPTH, MAX_TOTAL_LOT, MARGIN,
   LOT_INVALID, KILL_SWITCH.
4. **Kill switch**: operator KILL (layers STRATEGY/ORDER/ACCOUNT_RISK/
   GLOBAL_EMERGENCY) → engagement journaled + audited; release operator-only;
   engaged switch short-circuits the tick loop to SAFE_STOP before any gate.
5. **Reconciliation block**: MISMATCH ⇒ `block_new_orders` until a MATCH
   reconcile; RESUME refuses while blocked.

## Emergency semantics honesty

The V1.68 emergency mechanism is UNKNOWN (0 events observed, E021) — it is
**not** implemented as legacy behavior. The policy limits above are declared
OUR_EA_POLICY (as the frozen model itself specifies) and are marked
CONFIGURED, not historical.

## Scenario evidence

- Scenario I (signal→risk→reject→no order): depth-cap breach blocks the
  grid add; journal carries exactly one RISK_BLOCK(MAX_GRID_DEPTH) and no
  fill (`tests/test_runtime_scenarios.py`).
- Scenario H (kill→block): no fills after KILL; engagement audited.
- Duplicate protection (Scenario J): same idempotency key → one logical
  order at ledger and adapter level.
