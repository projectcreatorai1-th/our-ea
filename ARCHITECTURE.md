# ARCHITECTURE.md — OUR-EA

**Role:** Strategy Specification consumer · risk & execution enforcement ·
runtime. Built to `ea_build_contract.json` v1.0.0.

```
SNIPER Analyzer (frozen artifacts, sha256-verified)
        ↓ sanctioned path only (never imports analyzer code)
OUR-EA  (this repo — strategy enforcement / risk / execution authority)
        ↓ Gateway Contract 1.0.0 (client)
1144 Gateway (1144-Trading-OS platform.gateway, WebSocket server)
        ↓
MT5 (via gateway only) → Broker
```

## Layout

| Package | Responsibility |
| --- | --- |
| `our_ea/spec` | The ONLY sanctioned consumption path of SNIPER artifacts; three-way hash verification; fail-closed (`SpecIntegrityError`) |
| `our_ea/core` | Deterministic engines: state machine, grid/basket/lot/partial engines, risk guard, rules, market data integrity, idempotency, persistence, recovery, reconciliation, replay, audit + event journals (hash-chained) |
| `our_ea/execution` | Adapters: Paper, Simulation (failure injection), MT5-gateway (env-blocked until gateway transport ships) |
| `our_ea/gateway` | Client + contracts (v1.0.0) + transports: Loopback (in-process test), File (JSONL inter-process bridge). Production WebSocket transport is a planned phase — Loopback/File are retained as fallbacks |
| `our_ea/runtime` | StrategyCore service: command surface (PAUSE/RESUME/HALT/…), mode ladder, LIVE refusal |
| `tests` | 109 stdlib unittests: spec pinning, safety layers, execution/gateway, runtime scenarios, conformance & boundary |

## Non-negotiable safety properties (verified by `python run_tests.py`)

- **LIVE permanently locked** (INV-007) — every path refuses, audited.
- **Spec fail-closed** — frozen evidence model SHA256-pinned; spec must be
  three-way consistent (spec ⇄ spec_version ⇄ build contract).
- **UNKNOWN is never guessed** (INV-008) — partial close trigger/volume are
  UNKNOWN in the frozen model: runtime emits `MODEL_UNCERTAINTY` and skips.
- **Risk gate independent** (INV-003) — every intent gated; layered kill
  switches; surveillance max-loss/max-drawdown/failure kills.
- **Idempotent execution** (INV-005) — duplicate keys never double-order.
- **Deterministic replay** (INV-010) — same ticks ⇒ same decisions.
- **No analyzer runtime imports** — only frozen JSON artifacts are consumed.

## Runtime modes

PAPER (default; demo run sealed in `runs/demo_1.0.0/`) · SIMULATION ·
REPLAY — LIVE is structurally refused everywhere.
