# SECURITY.md — OUR-EA

## LIVE safety

- **LIVE permanently locked** (INV-007): every execution path refuses LIVE;
  the refusal itself is audited. Verified by
  `tests/test_execution_and_gateway.py` (LIVE lock, 3 paths) and mode-ladder
  tests.
- Mode ladder: PAPER / SIMULATION / REPLAY only. No flag, env var or config
  in this repository enables LIVE.

## Spec integrity (fail-closed)

- All frozen artifacts are hash-verified at load (see `PROVENANCE.md`);
  tampered/missing/stale artifacts raise `SpecIntegrityError` and the
  runtime refuses to start. Verified by
  `tests/test_spec_and_engines.py::TestSpecRepository`.

## Boundary

- **No analyzer runtime imports** — only frozen JSON artifacts are consumed
  through `our_ea/spec/repository.py`. Verified by
  `tests/test_conformance_and_boundary.py`.
- Never resolve the analyzer at a floating revision; pin to `spec/x.y.z`.

## Secrets

- No credentials, API keys, tokens or private keys in source (enforced by
  `tests/test_conformance_and_boundary.py::test_no_hardcoded_secrets_or_endpoints`
  and repo scan policy).
- `.gitignore` excludes `.env*`, `*.secret`, `*.key`, `credentials*`,
  virtualenvs, pid files, and runtime outputs (`runs/**/audit.jsonl`,
  `events.jsonl`, `runtime_state.json`, `summary.json`).
- Broker/MT5 credentials are owned by the 1144-Trading-OS adapter plane,
  never by this repository.
- Journals/audit logs contain no credentials (synthetic account IDs only,
  e.g. `DEMO-001` / `TEST-001`).

## Runtime data policy

- Runtime outputs (journals, state, summaries) stay on disk, out of git;
  only sealed `deployment_manifest.json` files are committed as provenance.
