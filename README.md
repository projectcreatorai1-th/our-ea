# OUR EA — SNIPER V1.68 Strategy Runtime

Production-grade trading runtime built to `ea_build_contract.json` v1.0.0:

```
SNIPER Analyzer  ->  Strategy Spec  ->  EA Build Contract
                                              |
                                          OUR EA  (this project)
                                              |
                                        1144 Gateway  ->  MT5  ->  Broker
```

**Authorities** — SNIPER: research/evidence/signal. Gateway: transport. **OUR EA:
strategy enforcement / risk / execution.** MT5: venue (via Gateway only).

## Non-negotiable safety properties (verified by `tests/`)

- **LIVE permanently locked** (INV-007) — every path refuses, audited.
- **Spec fail-closed** — frozen evidence model is SHA256-pinned; strategy
  spec must be three-way consistent (spec ⇄ spec_version ⇄ build contract).
- **UNKNOWN is never guessed** (INV-008) — partial close trigger/volume are
  UNKNOWN in the frozen model: runtime emits `MODEL_UNCERTAINTY` and skips.
- **Risk gate independent** (INV-003) — every intent gated; kill switch
  layers; surveillance can engage max-loss / max-drawdown / failure kills.
- **Idempotent execution** (INV-005) — duplicate keys never double-order.
- **Deterministic replay** (INV-010) — same ticks ⇒ same decisions.
- **No analyzer runtime imports** — only frozen JSON artifacts are consumed.

## Run

```bash
python run_tests.py   # 109 tests, stdlib unittest, no dependencies
python run_demo.py    # paper-mode E2E; artifacts under runs/
```

Requires the sibling project `../SNIPER-CashFlow-Analyzer` (or set
`SNIPER_ANALYZER_ROOT`) as the frozen-artifact source.

## Modes

`OBSERVATION` (compute + journal only) → `PAPER` (balance ledger) →
`DEMO` (gateway bridge; **ENVIRONMENT_BLOCKED** until the 1144 Gateway
server is deployed) → `LIVE` (**always refused**).

## Layout

| Path | Role |
|---|---|
| `our_ea/spec/` | hash-verified spec repository (the only analyzer intake) |
| `our_ea/core/` | lot/grid/basket/partial engines, state machine, rules, risk guard, market data, persistence, recovery, reconciliation, replay |
| `our_ea/execution/` | broker-neutral adapters: simulation, paper, MT5-via-gateway |
| `our_ea/gateway/` | gateway contract v1.0.0 (EA side), loopback + file transports |
| `our_ea/runtime/` | StrategyCore pipeline + RuntimeService (commands/modes/kill/health) |
| `tests/` | 109 tests incl. master-spec scenarios C, F, G, H, I, J |
| `docs/` | implementation + validation reports, inventory, blocked items |
