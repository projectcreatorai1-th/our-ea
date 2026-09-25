# VERSIONING.md — OUR-EA

| Layer | Form | Current |
| --- | --- | --- |
| Code version | git commit SHA + tag | `5cb5a06` (tag `ea/v1.0.0`) |
| Product (runtime) | semver | 1.0.0 (`runtime_version` in deployment manifest) |
| Contract versions | semver | Build Contract 1.0.0 · Gateway 1.0.0 · Strategy Spec 1.0.0 |
| Artifact hashes | SHA256 | spec `4648DFBB…` · model `124F0898…` · replay `994955EA…` |

## Tag scheme

- `ea/vX.Y.Z` — annotated tag on the baseline/verified commit of an EA
  release. `ea/v1.0.0` → `5cb5a06` (baseline commit).

## Compatibility rule

An EA release is compatible with exactly the Strategy Spec MAJOR it was
validated against (see the pinned hashes in `CONTRACTS.md`). The canonical
ecosystem component/version table lives in
`1144-Trading-OS/COMPATIBILITY_MATRIX.md`.

## Version sources (no conflicts permitted)

- Runtime version: `runs/<v>/deployment_manifest.json` → `runtime_version`.
- Git version: commit SHA / tag.
- Never store a second copy of a contract version inside this repo that can
  drift from the artifact that owns it.
