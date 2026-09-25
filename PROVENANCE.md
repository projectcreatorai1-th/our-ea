# PROVENANCE.md — OUR-EA

## Chain (verify forward and backward)

```
Runtime (this repo, tag ea/v1.0.0 → 5cb5a06)
  ↓ sealed in
runs/demo_1.0.0/deployment_manifest.json
  (spec 4648DFBB… · model 124F0898… · build contract 1.0.0 · gateway 1.0.0 ·
   deployment_hash CF3889FDBE66… · live_locked=true · mode PAPER)
  ↓ resolves to
SNIPER tag spec/1.0.0 → commit 175a259 (spec_version.json created_from_commit b7c87b4)
  ↓ generated from
Frozen Evidence Model V1.68-EVIDENCE-MODEL-v1.0 (SHA256 124F0898…)
```

Reverse: given an EA build, its manifest names the spec hash; that hash is
findable in the SNIPER repository's `spec_version.json` history / tag
(`git log -S <hash> -- STRATEGY_SPEC/` or the `spec/x.y.z` tag).

## How the runtime enforces it (no trust in filenames)

`our_ea/spec/repository.py` → `verify()`:

1. canonical-JSON SHA256 of `strategy_spec.json` must equal BOTH
   `spec_version.strategy_spec_hash` AND the build contract's compatibility
   pin (three-way).
2. Evidence model file SHA256 must equal the hardcoded pin + its sidecar.
3. Spec MAJOR must equal the compat constant (1).
4. Any mismatch → `SpecIntegrityError` → runtime refuses to start.

## Sanctioned source path

- Default: `../SNIPER-CashFlow-Analyzer` (sibling checkout)
- Override: env `SNIPER_ANALYZER_ROOT` (used by CI to pin the analyzer at
  tag `spec/1.0.0`)

## Known provenance limitation

`runs/demo_1.0.0/deployment_manifest.json` was sealed before this
repository had git history, so it carries no EA `git_sha` field. Future
release manifests (ecosystem Phase Q) will include git SHAs for all three
repositories. The sealed artifact is never edited retroactively.
