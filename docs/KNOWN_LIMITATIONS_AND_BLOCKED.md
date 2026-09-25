# KNOWN LIMITATIONS, UNKNOWNS, CONFLICTS, BLOCKED ITEMS

Honest status per master spec §98/§106 — nothing below is hidden or faked.

## BLOCKED items (with blocker/dependency/evidence/required action)

### BLOCK-1 — Real gateway transport (DEMO/LIVE execution path)
- **Blocker**: the 1144 Gateway **server does not exist** anywhere in the
  ecosystem (audit of both repos: only a client contract + Loopback test
  transport; no URL/port/auth defined).
- **Dependency**: gateway server deployment + WebSocket transport.
- **Evidence**: `SYSTEM_INVENTORY.md`; Analyzer `SNIPER_GATEWAY_INTEGRATION_
  FINAL_REPORT.md` ("ENVIRONMENT-BLOCKED"); OS baseline ("no HTTP server,
  Phase 0 contracts only").
- **Required action**: deploy the gateway server, then attach the real
  transport in `our_ea/gateway/transport.py` (port already defined).

### BLOCK-2 — LIVE trading
- **Blocker**: INV-007 permanent lock (human decision required) + BLOCK-1 +
  MT5 terminal currently logged into a **REAL account** (documented in the
  1144-Trading-OS runtime report) — demo validation impossible until a
  human switches the terminal to a demo login.
- **Required action**: human decisions only; nothing to code.

### BLOCK-3 — True broker-side reconciliation / recovery resync
- Same dependency as BLOCK-1 (adapter-provided broker view only today).

## UNKNOWN (frozen model — never guessed, INV-008)

| Item | Status | Runtime behavior |
|---|---|---|
| Partial close trigger | UNKNOWN (E025) | disabled by policy; MODEL_UNCERTAINTY + skip if enabled |
| Partial close volume | UNKNOWN (E025) | same |
| V1.68 emergency mechanism | UNKNOWN (0 events, E021) | OUR_EA_POLICY limits substituted (declared CONFIGURED, not historical) |
| V1.68 restart behavior | UNKNOWN (E022) | OUR_EA_POLICY recovery (ADOPT/PAUSE/SAFE_STOP) |
| Magic number, max levels of the legacy EA | UNKNOWN (docs/binary) | irrelevant to runtime: policy caps (depth 12) govern |
| Grid trigger anchor | PARTIAL — hypotheses indistinguishable | declared config choice, hypothesis id on every decision |
| Basket close trigger | PARTIAL — 3 compatible hypotheses | declared config choice; REJECTED \$1.68 refuses to load |

## CONFLICTS

None open. One transient conflict occurred during the session (Analyzer
regenerated STRATEGY_SPEC provenance mid-build) — resolved via the
three-way consistency policy; see implementation report §13 / inventory.

## Known limitations (by design, documented)

- Single symbol scope `GOLDmicro` (spec `symbol_scope`).
- Execution realism: slippage + failure injection available; venue-level
  partial fills/requotes ship with the real bridge (not faked).
- ROLLBACK command is refused locally and delegated to the 1144 OS
  (deployment authority) — the EA never self-rolls-back.
- Reconciliation "broker view" = adapter book until BLOCK-1 clears.
- Analyzer sibling suite carries 1 pre-existing failure in THEIR baseline
  audit test (gateway API lines in their `app.py`) — predates this session;
  their repo was not modified here (git-verified).
