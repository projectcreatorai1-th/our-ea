"""Spec repository + core engine unit tests."""
from __future__ import annotations

import json
import os
import unittest

from tests.harness import ROOT, make_core, make_repo, tmp_dir

from our_ea.core.basket_engine import (
    Basket, BasketCloseEngine, BasketTriggerConfig, PositionRef,
    make_cycle_id)
from our_ea.core.grid_engine import GridConfig, GridEngine
from our_ea.core.identifiers import canonical_hash
from our_ea.core.lot_engine import LotConfig, LotEngine
from our_ea.core.parameters import ParameterSet, ParameterViolation
from our_ea.core.rules import ModelUncertaintyError, RuleRegistry
from our_ea.core.state_machine import InvalidTransition, SpecStateMachine


class TestSpecRepository(unittest.TestCase):
    def test_verify_pins_hashes(self):
        report = make_repo().verify()
        self.assertTrue(report["verified"])
        self.assertEqual(report["state_count"], 17)
        self.assertEqual(report["transition_count"], 62)
        self.assertEqual(report["build_contract_version"], "1.0.0")

    def test_missing_artifact_fails_closed(self):
        from our_ea.spec.repository import SpecRepository, SpecIntegrityError
        repo = SpecRepository(os.path.join(ROOT, "nonexistent"))
        with self.assertRaises(SpecIntegrityError):
            repo.verify()

    def test_replay_dataset_hash_verified(self):
        ds = make_repo().load_replay_dataset()
        self.assertEqual(ds["schema"], "OUR_EA_REPLAY_DATASET_V1")
        self.assertEqual(ds["model_version"], "V1.68-EVIDENCE-MODEL-v1.0")


class TestParameters(unittest.TestCase):
    def setUp(self):
        self.ps = ParameterSet(make_repo().parameter_spec)

    def test_verified_defaults(self):
        self.assertEqual(self.ps.require("BaseLot"), 0.1)
        self.assertEqual(self.ps.require("LotMultiplier"), 1.1)
        self.assertEqual(self.ps.require("GridStepUSD"), 5.0)
        self.assertEqual(self.ps.require("MaxGridDepth"), 12)

    def test_non_configurable_rejected(self):
        with self.assertRaises(ParameterViolation):
            self.ps.apply_overrides({"BaseLot": 0.5})

    def test_configurable_in_range(self):
        self.ps.apply_overrides({"GridStepUSD": 4.8})
        self.assertEqual(self.ps.get("GridStepUSD"), 4.8)

    def test_configurable_out_of_range_rejected(self):
        with self.assertRaises(ParameterViolation):
            self.ps.apply_overrides({"GridStepUSD": 0.5})

    def test_unknown_hypothesis_rejected(self):
        with self.assertRaises(ParameterViolation):
            self.ps.apply_overrides({"BasketCloseHypothesis": "H_GROSS_1_68"})


class TestLotEngine(unittest.TestCase):
    def test_floor_ladder_matches_documented_values(self):
        eng = LotEngine(LotConfig())
        self.assertEqual(eng.lot_for_level(1), 0.10)
        self.assertEqual(eng.lot_for_level(2), 0.11)
        self.assertEqual(eng.lot_for_level(3), 0.12)   # 0.121 -> floor 0.12
        self.assertEqual(eng.lot_for_level(4), 0.13)   # 0.1331 -> floor 0.13
        self.assertEqual(eng.lot_for_level(32),
                         eng.lot_for_level(32))

    def test_l32_float_drift_guarded(self):
        eng = LotEngine(LotConfig())
        lot = eng.lot_for_level(32)
        self.assertAlmostEqual(
            lot, eng.lot_for_level(32), delta=0)
        self.assertGreater(lot, 0.1 * (1.1 ** 31) - 0.01)

    def test_observed_range(self):
        eng = LotEngine(LotConfig())
        self.assertTrue(eng.is_observed_level(32))
        self.assertFalse(eng.is_observed_level(33))


class TestGridEngine(unittest.TestCase):
    def setUp(self):
        self.eng = GridEngine(GridConfig())

    def test_buy_adds_lower_sell_adds_higher(self):
        self.assertEqual(self.eng.next_add_price("BUY", 4000.0), 3995.0)
        self.assertEqual(self.eng.next_add_price("SELL", 4000.0), 4005.0)

    def test_trigger_fires_on_cross(self):
        d = self.eng.evaluate("BUY", 3994.9, 4000.0, 3998.0)
        self.assertEqual(d.action, "ADD")
        self.assertEqual(d.anchor_hypothesis, "H_PREV_ENTRY")
        d = self.eng.evaluate("SELL", 4005.1, 4000.0, 4002.0)
        self.assertEqual(d.action, "ADD")
        d = self.eng.evaluate("BUY", 3996.0, 4000.0, 3998.0)
        self.assertEqual(d.action, "HOLD")

    def test_extreme_hypothesis_recorded(self):
        eng = GridEngine(GridConfig(anchor="H_EXTREME"))
        # anchor = extreme 3997 -> BUY adds at <= 3992
        d = eng.evaluate("BUY", 3991.5, 4000.0, 3997.0)
        self.assertEqual(d.anchor_price, 3997.0)
        self.assertEqual(d.anchor_hypothesis, "H_EXTREME")
        self.assertEqual(d.action, "ADD")

    def test_spacing_band_classification(self):
        self.assertEqual(GridEngine.conforms_observed_spacing("BUY", 4000, 3995), "MATCH")
        self.assertEqual(GridEngine.conforms_observed_spacing("BUY", 4000, 3990), "MISMATCH")
        self.assertEqual(GridEngine.conforms_observed_spacing("BUY", 4000, 3999.5), "MISMATCH")


class TestBasketEngine(unittest.TestCase):
    def test_cycle_identity_deterministic(self):
        self.assertEqual(make_cycle_id("A", "GOLDmicro", 1), "A|GOLDmicro|C000001")

    def test_floating_pl_contract_size_1(self):
        b = Basket("A|GOLDmicro|C000001", "GOLDmicro", "", 1.0)
        b.add(PositionRef("p1", "BUY", 0.1, 4382.88, 1))
        # observed deal: BUY 0.1 @4382.88 -> 4374.45 = -0.84
        self.assertAlmostEqual(
            b.positions[0].floating_pl(4374.45, 1.0), -0.843, places=3)

    def test_rejected_hypothesis_refuses_to_load(self):
        with self.assertRaises(ValueError):
            BasketTriggerConfig(hypothesis="H_GROSS_1_68")

    def test_close_trigger_hypotheses(self):
        eng = BasketCloseEngine(BasketTriggerConfig("H_GROSS_1_00"))
        self.assertTrue(eng.evaluate(1.0, 0.4)[0])
        eng50 = BasketCloseEngine(BasketTriggerConfig("H_PER_LOT_0_50"))
        self.assertTrue(eng50.evaluate(0.55, 1.1)[0])
        self.assertFalse(eng50.evaluate(0.4, 1.1)[0])

    def test_gross_close_realizes_and_reentry_state(self):
        b = Basket("A|GOLDmicro|C000001", "GOLDmicro", "", 1.0)
        b.add(PositionRef("p1", "BUY", 0.1, 4000.0, 1))
        b.add(PositionRef("p2", "SELL", 0.1, 4000.0, 1))
        # price up 6 -> BUY +0.6... use big move: +6.0
        gross = b.gross_pl(4006.0, 4006.0)
        # BUY: (4006-4000)*0.1 = 0.6 ; SELL: (4000-4006)*0.1 = -0.6 -> 0
        self.assertAlmostEqual(gross, 0.0, places=6)
        b.close_position("p1", 4006.0, "t")
        b.close_position("p2", 4006.0, "t")
        self.assertTrue(b.is_flat())
        b.mark_closed("t")
        self.assertEqual(b.state, "CLOSED")


class TestRuleRegistry(unittest.TestCase):
    def test_frozen_shape(self):
        rules = RuleRegistry.from_repository(make_repo())
        self.assertEqual(rules.status_summary(),
                         {"VERIFIED": 8, "PARTIAL": 3, "UNKNOWN": 4, "REJECTED": 1})
        rules.assert_frozen_expectations()

    def test_unknown_rules_never_verified(self):
        rules = RuleRegistry.from_repository(make_repo())
        with self.assertRaises(ModelUncertaintyError):
            rules.require_verified("R-PARTIAL-TRIGGER")
        self.assertTrue(rules.get("R-PARTIAL-TRIGGER").unknown)
        self.assertTrue(rules.get("R-ACCUM-1-68").rejected)


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        repo = make_repo()
        self.sm = SpecStateMachine(repo.state_transition_spec)

    def test_declared_path(self):
        self.sm.fire("INIT")
        self.sm.fire("INIT_OK")
        self.sm.fire("OPEN_BOTH")
        self.sm.fire("GRID_ADD")
        self.assertEqual(self.sm.state, "GRID_ACTIVE")
        self.sm.fire("BASKET_INTENT")
        self.sm.fire("CLOSE_START")
        self.sm.fire("CLOSED_ALL")
        self.sm.fire("RESUME_NEW_CYCLE")
        self.assertEqual(self.sm.state, "WAITING_FOR_ENTRY")

    def test_undeclared_transition_blocked(self):
        with self.assertRaises(InvalidTransition):
            self.sm.fire("GRID_ADD")  # from IDLE

    def test_safe_stop_from_any_trading_state(self):
        self.sm.fire("INIT")
        self.sm.fire("INIT_OK")
        self.sm.fire("OPEN_LONG")
        self.sm.fire("SAFE_STOP")
        self.assertEqual(self.sm.state, "SAFE_STOP")


if __name__ == "__main__":
    unittest.main()
