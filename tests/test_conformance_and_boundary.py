"""Replay conformance vs the observed V1.68 dataset + boundary rules."""
from __future__ import annotations

import os
import re
import unittest

from tests.harness import ROOT, make_repo

from our_ea.core.replay import ReplayConformance


class TestReplayConformance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo = make_repo()
        cls.dataset = repo.load_replay_dataset()
        from our_ea.core.parameters import ParameterSet
        cls.params = ParameterSet(repo.parameter_spec)
        cls.result = ReplayConformance.from_parameters(
            cls.params).run(cls.dataset)

    def test_dataset_integrity(self):
        self.assertEqual(self.dataset["schema"], "OUR_EA_REPLAY_DATASET_V1")
        self.assertEqual(len(self.dataset["cycles"]), 812)
        self.assertEqual(self.dataset["model_version"],
                         "V1.68-EVIDENCE-MODEL-v1.0")
        # observed dataset accounts are present with dataset hashes
        for account in ("335504856", "391629843", "411173797", "440192945"):
            self.assertIn(account, self.dataset["dataset_hashes"])

    def test_lot_ladder_high_match(self):
        r = self.result.by_rule["R-LOT-FLOOR"]
        total = r["MATCH"] + r["MISMATCH"]
        self.assertGreater(r["MATCH"] / total, 0.95,
                           f"lot conformance too low: {r}")

    def test_direction_high_match(self):
        r = self.result.by_rule["R-GRID-DIRECTION"]
        total = r["MATCH"] + r["MISMATCH"]
        self.assertGreater(r["MATCH"] / total, 0.98,
                           f"direction conformance too low: {r}")

    def test_spacing_band_majority(self):
        r = self.result.by_rule["R-GRID-SPACING"]
        total = r["MATCH"] + r["MISMATCH"]
        # E013: 85.2% of observed gaps inside 4.5..5.5; our wider observed
        # band 4.5..6.0 must capture at least 90%
        self.assertGreater(r["MATCH"] / total, 0.90,
                           f"spacing conformance too low: {r}")

    def test_both_sides_initial(self):
        r = self.result.by_rule["R-BOTH-SIDES"]
        total = r["MATCH"] + r["MISMATCH"]
        self.assertGreater(r["MATCH"] / total, 0.95)

    def test_deterministic_result_hash(self):
        res2 = ReplayConformance.from_parameters(
            self.params).run(self.dataset)
        self.assertEqual(self.result.result_hash, res2.result_hash)

    def test_overall_verdict(self):
        v = ReplayConformance.from_parameters(
            self.params).verdict(self.result)
        self.assertEqual(v, "PASS")


class TestBoundaryRules(unittest.TestCase):
    """EA must not import Analyzer runtime code; LIVE stays locked; hashes
    pinned. These mirror the Analyzer's own hardening boundary."""

    def _sources(self):
        for dirpath, _dirnames, filenames in os.walk(
                os.path.join(ROOT, "our_ea")):
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)

    def test_no_analyzer_runtime_imports(self):
        banned = re.compile(
            r"^\s*(from|import)\s+(core|desktop|web|tools|tester)\b",
            re.MULTILINE)
        for path in self._sources():
            with open(path, "r", encoding="utf-8") as fh:
                src = fh.read()
            self.assertIsNone(
                banned.search(src),
                f"analyzer runtime import found in {path}")

    def test_no_hardcoded_secrets_or_endpoints(self):
        secret = re.compile(r"(api[_-]?key|password|secret|token)\s*=\s*[\"']"
                            r"?[A-Za-z0-9+/]{16,}", re.IGNORECASE)
        for path in self._sources():
            with open(path, "r", encoding="utf-8") as fh:
                src = fh.read()
            self.assertIsNone(secret.search(src), f"secret-like literal in {path}")

    def test_live_lock_constant(self):
        import our_ea
        self.assertTrue(our_ea.LIVE_LOCKED)

    def test_frozen_model_hash_pinned_in_repository(self):
        from our_ea.spec import repository as rep
        self.assertEqual(rep.EVIDENCE_MODEL_PINNED_HASH,
                         "124F08984284E880F268C37E6A4583A8653B5EB1B80EB5C45"
                         "BBB64F58AC3D4AE")
        self.assertEqual(rep.REPLAY_DATASET_PINNED_HASH,
                         "994955EA1889409A3B71C15C799B2802E0D9CF5E42DB277D42"
                         "0FA0835B3C0705")

    def test_tampered_spec_fails_closed(self):
        import json
        import shutil
        import tempfile
        from our_ea.spec.repository import SpecRepository, SpecIntegrityError
        src = make_repo().root
        work = tempfile.mkdtemp(prefix="tamper_")
        try:
            shutil.copytree(os.path.join(src, "STRATEGY_SPEC"),
                            os.path.join(work, "STRATEGY_SPEC"))
            os.makedirs(os.path.join(work, "data", "evidence_model"))
            shutil.copy(
                os.path.join(src, "data", "evidence_model",
                             "V1.68-EVIDENCE-MODEL-v1.0.json"),
                os.path.join(work, "data", "evidence_model"))
            path = os.path.join(work, "STRATEGY_SPEC", "strategy_spec.json")
            with open(path, "r", encoding="utf-8") as fh:
                spec = json.load(fh)
            spec["lot_model"]["base_lot"]["value"] = 99.0  # tamper
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(spec, fh)
            with self.assertRaises(SpecIntegrityError):
                SpecRepository(work).verify()
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
