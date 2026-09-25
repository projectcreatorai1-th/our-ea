# RELEASE.md — OUR-EA

## Release gate (all must pass)

1. `python run_tests.py` — 109/109 green (spec pinning, safety layers,
   execution/gateway, runtime scenarios, conformance & boundary).
2. Three-way spec verification passes against the pinned analyzer tag
   (`SNIPER_ANALYZER_ROOT` → SNIPER checkout at `spec/x.y.z`).
3. Deployment manifest of the validating run pins exactly the released
   hashes; `live_locked=true`.
4. Working tree clean; secret scan clean.

## Release unit

- Annotated tag `ea/vX.Y.Z` on the verified commit (current: `ea/v1.0.0` →
  `5cb5a06`).
- The sealed run manifest under `runs/<version>/` is part of the release
  evidence (committed).

## Rollback

- Roll back by checking out the previous `ea/vX.Y.Z` tag — never by editing
  artifacts in place.
- After rollback, re-run the release gate; the spec three-way check will
  refuse any analyzer state that is not the pinned compatible one.
- Ecosystem-wide rollback (coordinated SNIPER + EA + OS) is documented in
  `1144-Trading-OS` (`ROLLBACK.md`, ecosystem Phase S); never roll back a
  single component without compatibility verification.

## After release

- Push tag + main (normal push; no force).
- Update `1144-Trading-OS/COMPATIBILITY_MATRIX.md` with the new EA version
  and its pinned spec state.
