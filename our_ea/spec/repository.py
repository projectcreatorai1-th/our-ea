"""Spec repository — the ONLY sanctioned consumption path of SNIPER artifacts.

OUR EA never imports Analyzer runtime code (boundary rule). It reads frozen
machine contracts produced by the Analyzer:

    STRATEGY_SPEC/strategy_spec.json         (canonical-JSON sha256 pinned)
    STRATEGY_SPEC/parameter_spec.json
    STRATEGY_SPEC/state_transition_spec.json
    STRATEGY_SPEC/invariant_spec.json
    STRATEGY_SPEC/ea_build_contract.json     (build gate: hash compatibility)
    data/evidence_model/<model>.json         (frozen rules, file sha256 sidecar)

Any hash mismatch -> SpecIntegrityError -> the runtime refuses to start
(INV-009 evidence hash integrity; fail closed, never silent).
"""
from __future__ import annotations

import json
import os

from our_ea.core.identifiers import canonical_hash, file_sha256

# Semantic SSOT: the frozen evidence model is hash-pinned forever.
EVIDENCE_MODEL_PINNED_HASH = (
    "124F08984284E880F268C37E6A4583A8653B5EB1B80EB5C45BBB64F58AC3D4AE"
)
# The strategy spec is a GENERATED VIEW of the frozen model. Provenance-only
# regenerations (e.g. generated_from_commit) change its canonical hash without
# semantic change; the ea_build_contract re-pins it each time. OUR EA therefore
# requires THREE-WAY consistency instead of a hard-coded spec hash:
#   canonical_hash(strategy_spec) == spec_version.strategy_spec_hash
#                                   == ea_build_contract.compatibility hash
# plus spec_version MAJOR must match the build contract major (semver rule:
# any VERIFIED-rule change = MAJOR), and the evidence model hash must equal
# the pinned value above.
STRATEGY_SPEC_COMPAT_MAJOR = 1
REPLAY_DATASET_PINNED_HASH = (
    "994955EA1889409A3B71C15C799B2802E0D9CF5E42DB277D420FA0835B3C0705"
)


class SpecIntegrityError(RuntimeError):
    """Frozen artifact missing / hash mismatch / contract incompatible."""


class SpecRepository:
    """Loads and hash-verifies the frozen strategy contract set."""

    def __init__(self, analyzer_root: str):
        self.root = os.path.abspath(analyzer_root)
        self.spec_dir = os.path.join(self.root, "STRATEGY_SPEC")
        self.model_dir = os.path.join(self.root, "data", "evidence_model")
        self._cache: dict = {}

    # ------------------------------------------------------------------ paths
    @property
    def paths(self) -> dict:
        return {
            "strategy_spec": os.path.join(self.spec_dir, "strategy_spec.json"),
            "parameter_spec": os.path.join(self.spec_dir, "parameter_spec.json"),
            "state_transition_spec": os.path.join(self.spec_dir, "state_transition_spec.json"),
            "invariant_spec": os.path.join(self.spec_dir, "invariant_spec.json"),
            "ea_build_contract": os.path.join(self.spec_dir, "ea_build_contract.json"),
            "evidence_model": os.path.join(self.model_dir, "V1.68-EVIDENCE-MODEL-v1.0.json"),
            "replay_dataset": os.path.join(self.root, "data", "our_ea", "replay_dataset_v1.json"),
        }

    # ------------------------------------------------------------------ load
    def _load_json(self, key: str) -> dict:
        if key in self._cache:
            return self._cache[key]
        path = self.paths[key]
        if not os.path.isfile(path):
            raise SpecIntegrityError(f"missing frozen artifact: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self._cache[key] = data
        return data

    def _load_spec_version(self) -> dict:
        path = os.path.join(self.spec_dir, "spec_version.json")
        if not os.path.isfile(path):
            raise SpecIntegrityError(f"missing frozen artifact: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    # ------------------------------------------------------------- verification
    def verify(self) -> dict:
        """Verify all artifact hashes; raise SpecIntegrityError on any mismatch."""
        report: dict = {}

        for key in ("strategy_spec", "parameter_spec", "state_transition_spec",
                    "invariant_spec", "ea_build_contract"):
            self._load_json(key)  # raises if missing/malformed
        spec_version = self._load_spec_version()

        # strategy_spec: canonical-JSON hash must match BOTH spec_version and
        # the ea_build_contract compatibility pin (three-way consistency)
        actual = canonical_hash(self._load_json("strategy_spec"))
        sv_hash = str(spec_version.get("strategy_spec_hash", "")).upper()
        contract = self._load_json("ea_build_contract")
        compat = contract.get("compatibility_requirements", {})
        bc_hash = str(compat.get("strategy_spec_hash", "")).upper()
        if sv_hash != actual or bc_hash != actual:
            raise SpecIntegrityError(
                f"strategy_spec hash three-way mismatch: spec={actual} "
                f"spec_version={sv_hash} build_contract={bc_hash}")
        if str(contract.get("strategy_spec_version", "")) != \
                str(spec_version.get("spec_version", "")):
            raise SpecIntegrityError("spec version disagreement between "
                                     "spec_version.json and ea_build_contract")
        major = str(spec_version.get("spec_version", "0")).split(".")[0]
        if major != str(STRATEGY_SPEC_COMPAT_MAJOR):
            raise SpecIntegrityError(
                f"strategy spec MAJOR {major} != compatible "
                f"{STRATEGY_SPEC_COMPAT_MAJOR} (semantic change requires EA "
                f"revalidation)")
        report["strategy_spec_hash"] = actual
        report["spec_version"] = spec_version.get("spec_version")

        # evidence model: raw file hash + sidecar
        model_path = self.paths["evidence_model"]
        actual_model = file_sha256(model_path)
        sidecar = model_path + ".sha256.txt"
        sidecar_hash = ""
        if os.path.isfile(sidecar):
            with open(sidecar, "r", encoding="utf-8") as fh:
                sidecar_hash = fh.read().strip().upper()
        if actual_model != EVIDENCE_MODEL_PINNED_HASH or (
                sidecar_hash and sidecar_hash != actual_model):
            raise SpecIntegrityError(
                f"evidence model hash mismatch: {actual_model} / sidecar {sidecar_hash}")
        report["evidence_model_hash"] = actual_model

        # cross-check: strategy_spec identity vs frozen model
        spec = self._load_json("strategy_spec")
        ident = spec.get("spec_identity", {})
        if ident.get("evidence_model_hash", "").upper() != actual_model:
            raise SpecIntegrityError("strategy_spec evidence_model_hash != frozen model hash")
        report["spec_identity"] = ident.get("spec_id")

        # build contract compatibility gate (Phase 12: EA loads only valid contracts)
        if compat.get("evidence_model_hash", "").upper() != EVIDENCE_MODEL_PINNED_HASH:
            raise SpecIntegrityError("ea_build_contract evidence_model_hash mismatch")
        report["build_contract_version"] = contract.get("contract_version")
        report["build_contract_compatible"] = True

        # state machine spec sanity
        st = self._load_json("state_transition_spec")
        if st.get("state_count") != len(st.get("states", [])):
            raise SpecIntegrityError("state_transition_spec state_count mismatch")
        report["state_count"] = st.get("state_count")
        report["transition_count"] = st.get("transition_count")
        report["verified"] = True
        return report

    # ------------------------------------------------------------- accessors
    @property
    def strategy_spec(self) -> dict:
        return self._load_json("strategy_spec")

    @property
    def parameter_spec(self) -> dict:
        return self._load_json("parameter_spec")

    @property
    def state_transition_spec(self) -> dict:
        return self._load_json("state_transition_spec")

    @property
    def invariant_spec(self) -> dict:
        return self._load_json("invariant_spec")

    @property
    def ea_build_contract(self) -> dict:
        return self._load_json("ea_build_contract")

    @property
    def evidence_model(self) -> dict:
        return self._load_json("evidence_model")

    def load_replay_dataset(self, verify_hash: bool = True) -> dict:
        path = self.paths["replay_dataset"]
        if not os.path.isfile(path):
            raise SpecIntegrityError(f"missing replay dataset: {path}")
        if verify_hash and file_sha256(path) != REPLAY_DATASET_PINNED_HASH:
            raise SpecIntegrityError("replay dataset hash mismatch (mutable evidence)")
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)


def load_repository(analyzer_root: str | None = None) -> SpecRepository:
    from our_ea.core.identifiers import default_analyzer_root
    repo = SpecRepository(analyzer_root or default_analyzer_root())
    repo.verify()
    return repo
