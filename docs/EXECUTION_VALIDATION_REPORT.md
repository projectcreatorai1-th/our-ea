# EXECUTION VALIDATION REPORT

## Broker-neutral core (Phase 25)

Adapters implement one port (`ExecutionAdapter.submit/open_positions/
close_all/account_snapshot`) with `ExecutionIntent`/`ExecutionResult`.
Strategy runtime never touches a venue; grid/basket logic holds no adapter
knowledge.

## Adapter matrix

| Adapter | Mode | Behavior | Validation |
|---|---|---|---|
| SimulationAdapter | SIMULATION | deterministic fills, failure injection | unit tests; recovery/duplicate tests |
| PaperAdapter | PAPER | balance ledger, per-position P/L, adverse slippage option, idempotent per key, CLOSE via side-correct exit prices | unit + Scenario C/E2E + demo run (500 → 502.256) |
| MT5GatewayAdapter | DEMO | requires gateway bridge (contract v1.0.0); without bridge every op = `ExecutionUnavailable("ENVIRONMENT_BLOCKED")`; LIVE = `LiveLockError` at construction | unit tests incl. bridge-contract enforcement |

## Accounting correctness

Contract size 1.0 (VERIFIED E019). P/L(BUY) = (exit−entry)·lot·1.0 —
validated against observed V1.68 deal data (0.10 lot, 4382.88→4374.45 ⇒
−0.84, matches the dataset to the cent). Basket close executes one CLOSE
intent per position at side-correct prices (BUY exits at bid, SELL at ask)
— full per-position lineage in the journal.

## Idempotency & duplicates (Scenario J)

Idempotency keys (`account|symbol|cycle|event|seq`) enforced at two layers:
runtime ledger (retry → original result, `DUPLICATE_EVENT` journal entry)
and adapter (`_results` map). Verified: retry-after-timeout yields exactly
one logical order and one open position.

## Execution realism features available

Slippage (adversarial, per-side), failure injection (reject/timeout via
simulation adapter), spread guard, latency/fill-count fields present in
ExecutionFeedback for the Analyzer's TCA. Deeper venue models (partial
fills, requotes) ship with the real gateway bridge — not simulated as fake
success today.
