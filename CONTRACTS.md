# CONTRACTS.md — OUR-EA

OUR-EA **consumes** contracts; it owns none except its own runtime identity.

## Consumed contracts (all verified at load, fail-closed)

| Contract | Version | Pinned value | Verification |
| --- | --- | --- | --- |
| Frozen Evidence Model | V1.68-EVIDENCE-MODEL-v1.0 | SHA256 `124F08984284E880F268C37E6A4583A8653B5EB1B80EB5C45BBB64F58AC3D4AE` | hardcoded in `our_ea/spec/repository.py` + file sidecar |
| Strategy Specification | 1.0.0 (MAJOR must equal 1) | canonical SHA256 `4648DFBB…` (state at SNIPER tag `spec/1.0.0`) | three-way: canonical(spec) == spec_version pin == build-contract pin |
| EA Build Contract | 1.0.0 | SHA256 `6EFBDA5A…` | `spec_version.ea_build_contract_hash` + compatibility gate |
| Replay dataset | v1 | SHA256 `994955EA…` | hardcoded pin, checked on load |

## Emitted/shared contracts

| Contract | Version | Where |
| --- | --- | --- |
| Gateway CLIENT contract (handshake, events, signals) | 1.0.0 | `our_ea/gateway/contracts.py` — field-compatible with the SNIPER Gateway contract served by 1144-Trading-OS `platform.gateway` |
| Event/audit journal schema (hash-chained JSONL) | runtime 1.0.0 | `our_ea/core/{events,audit}.py` |
| Deployment manifest (sealed run provenance) | runtime 1.0.0 | `runs/<version>/deployment_manifest.json` |

## Rules

- The sanctioned consumption path is `our_ea/spec/repository.py` only —
  reading SNIPER artifacts any other way is a boundary violation.
- Never verify against a floating "latest" spec: CI and any production load
  must resolve the analyzer at a **tagged commit** (`spec/x.y.z`) via
  `SNIPER_ANALYZER_ROOT`.
- A strategy-spec MAJOR bump (any VERIFIED-rule change) requires EA
  revalidation before the compat constant in `repository.py` moves.
