"""Safety layer tests: risk guard, idempotency, market data, persistence,
recovery, reconciliation, audit/journal chains."""
from __future__ import annotations

import json
import os
import unittest

from tests.harness import make_core, make_repo, tmp_dir

from our_ea.core.audit import AuditLog
from our_ea.core.broker_profile import BrokerProfile
from our_ea.core.events import EventJournal
from our_ea.core.idempotency import IdempotencyKey, IdempotencyLedger
from our_ea.core.market_data import MarketDataPipeline, Tick, TickRejection
from our_ea.core.persistence import StateCorruptionError, StateStore, is_stale
from our_ea.core.reconciliation import ReconciliationEngine
from our_ea.core.recovery import RecoveryRunner
from our_ea.core.risk_guard import RiskGuard, RiskLimits, RiskRejection


def _guard(**over):
    limits = RiskLimits(**over) if over else RiskLimits()
    return RiskGuard(limits)


class TestRiskGuard(unittest.TestCase):
    def test_happy_path_approves(self):
        g = _guard()
        d = g.check_intent(side="BUY", lot=0.1, spread=0.3,
                           open_positions=2, side_level=2,
                           total_open_lots=0.2, margin_used=1.0,
                           free_margin=500.0)
        self.assertTrue(d.approved)

    def test_blocks_each_limit(self):
        g = _guard()
        cases = [
            dict(side="BUY", lot=0.1, spread=0.9, open_positions=1,
                 side_level=1, total_open_lots=0.1, margin_used=0,
                 free_margin=100, expect="MAX_SPREAD"),
            dict(side="BUY", lot=0.1, spread=0.3, open_positions=12,
                 side_level=1, total_open_lots=0.1, margin_used=0,
                 free_margin=100, expect="MAX_POSITIONS"),
            dict(side="BUY", lot=0.1, spread=0.3, open_positions=1,
                 side_level=12, total_open_lots=0.1, margin_used=0,
                 free_margin=100, expect="MAX_GRID_DEPTH"),
            dict(side="BUY", lot=0.1, spread=0.3, open_positions=1,
                 side_level=1, total_open_lots=1.95, margin_used=0,
                 free_margin=100, expect="MAX_TOTAL_LOT"),
        ]
        for c in cases:
            expect = c.pop("expect")
            with self.assertRaises(RiskRejection) as cm:
                g.check_intent(**c)
            self.assertEqual(cm.exception.rule, expect)

    def test_kill_switch_blocks_everything(self):
        g = _guard()
        g.engage_kill_switch("TEST", "test")
        with self.assertRaises(RiskRejection) as cm:
            g.check_intent(side="BUY", lot=0.1, spread=0.1,
                           open_positions=0, side_level=0,
                           total_open_lots=0.0, margin_used=0, free_margin=1)
        self.assertEqual(cm.exception.rule, "KILL_SWITCH")

    def test_max_loss_engages_kill(self):
        g = _guard()
        self.assertTrue(g.check_loss(-49.0))
        self.assertFalse(g.kill_switch_engaged)
        self.assertFalse(g.check_loss(-50.1))
        self.assertTrue(g.kill_switch_engaged)

    def test_drawdown_engages_kill(self):
        g = _guard()
        g.check_drawdown(500.0)
        self.assertTrue(g.check_drawdown(400.0))     # 20% dd ok
        self.assertFalse(g.check_drawdown(300.0))    # 40% dd -> kill
        self.assertTrue(g.kill_switch_engaged)

    def test_consecutive_failures_engage_kill(self):
        g = _guard()
        for _ in range(3):
            g.record_order_failure("x")
        self.assertTrue(g.kill_switch_engaged)


class TestIdempotency(unittest.TestCase):
    def test_retry_same_key_single_execution(self):
        ledger = IdempotencyLedger()
        key = IdempotencyKey("A", "GOLDmicro", "C1", "OPEN", 1).build()
        calls = []

        def factory():
            calls.append(1)
            return {"status": "FILLED"}

        is_new1, r1 = ledger.check_and_register(key, factory)
        is_new2, r2 = ledger.check_and_register(key, factory)
        self.assertTrue(is_new1)
        self.assertFalse(is_new2)
        self.assertEqual(r1, r2)
        self.assertEqual(len(calls), 1)

    def test_distinct_keys_both_execute(self):
        ledger = IdempotencyLedger()
        k1 = IdempotencyKey("A", "S", "C1", "OPEN", 1).build()
        k2 = IdempotencyKey("A", "S", "C1", "OPEN", 2).build()
        self.assertTrue(ledger.check_and_register(k1, lambda: 1)[0])
        self.assertTrue(ledger.check_and_register(k2, lambda: 2)[0])


class TestMarketData(unittest.TestCase):
    def setUp(self):
        self.pipe = MarketDataPipeline()

    def _tick(self, buy=4000.0, sell=3999.7, ts="2026-09-25T10:00:00+00:00"):
        return Tick(ts=ts, buy_price=buy, sell_price=sell)

    def test_valid_tick_passes(self):
        self.assertEqual(self.pipe.validate(self._tick()).spread, 0.3)

    def test_impossible_price_rejected(self):
        with self.assertRaises(TickRejection) as cm:
            self.pipe.validate(self._tick(buy=-1))
        self.assertEqual(cm.exception.reason, "INVALID_PRICE")

    def test_inverted_quote_rejected(self):
        with self.assertRaises(TickRejection) as cm:
            self.pipe.validate(self._tick(buy=3999.0, sell=4000.0))
        self.assertEqual(cm.exception.reason, "INVERTED_QUOTE")

    def test_spread_anomaly_rejected(self):
        with self.assertRaises(TickRejection) as cm:
            self.pipe.validate(self._tick(buy=4001.0, sell=4000.0))
        self.assertEqual(cm.exception.reason, "SPREAD_ANOMALY")

    def test_volatility_spike_rejected(self):
        self.pipe.validate(self._tick())
        with self.assertRaises(TickRejection) as cm:
            self.pipe.validate(self._tick(buy=4040.0, sell=4039.7))
        self.assertEqual(cm.exception.reason, "VOLATILITY_SPIKE")

    def test_regressing_timestamp_rejected(self):
        self.pipe.validate(self._tick(ts="2026-09-25T10:00:05+00:00"))
        with self.assertRaises(TickRejection) as cm:
            self.pipe.validate(self._tick(ts="2026-09-25T10:00:01+00:00"))
        self.assertEqual(cm.exception.reason, "REGRESSING_TIMESTAMP")


class TestBrokerProfile(unittest.TestCase):
    def setUp(self):
        self.b = BrokerProfile()

    def test_normalize_floors_to_step(self):
        self.assertEqual(self.b.normalize_volume(0.109), 0.10)
        self.assertEqual(self.b.normalize_volume(0.1), 0.10)

    def test_invalid_volume_rejected(self):
        with self.assertRaises(ValueError):
            self.b.normalize_volume(0.0)
        with self.assertRaises(ValueError):
            self.b.normalize_volume(101.0)

    def test_margin(self):
        self.assertAlmostEqual(
            self.b.margin_for(0.1, 4000.0), 0.1 * 4000 * 1.0 * 0.002)


class TestPersistence(unittest.TestCase):
    def test_save_load_roundtrip(self):
        store = StateStore(os.path.join(tmp_dir(), "s.json"))
        store.save({"a": 1, "nested": {"b": [1, 2]}})
        self.assertEqual(store.load()["a"], 1)

    def test_corruption_detected(self):
        d = tmp_dir()
        path = os.path.join(d, "s.json")
        store = StateStore(path)
        store.save({"a": 1})
        with open(path, "r", encoding="utf-8") as fh:
            body = json.load(fh)
        body["a"] = 999  # tamper
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
        with self.assertRaises(StateCorruptionError):
            store.load()

    def test_schema_mismatch_detected(self):
        d = tmp_dir()
        path = os.path.join(d, "s.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": "OLD", "checksum": "x"}, fh)
        with self.assertRaises(StateCorruptionError):
            StateStore(path).load()

    def test_staleness(self):
        self.assertFalse(is_stale({"saved_at": "2026-09-25T00:00:00+00:00"},
                                  now="2026-09-25T01:00:00+00:00"))
        self.assertTrue(is_stale({"saved_at": "2026-09-24T00:00:00+00:00"},
                                 now="2026-09-25T01:00:00+00:00"))


class TestRecovery(unittest.TestCase):
    def _runner(self, state_fn, positions):
        d = tmp_dir()
        store = StateStore(os.path.join(d, "s.json"))
        if state_fn is not None:
            state_fn(store)
        journal = EventJournal("R", os.path.join(d, "e.jsonl"))
        return RecoveryRunner(store, journal), d

    def test_no_state_flat(self):
        runner, _ = self._runner(None, [])
        out = runner.run([])
        self.assertEqual(out.outcome, "RESUMED_FRESH")

    def test_no_state_with_positions_pauses(self):
        runner, _ = self._runner(None, [{"position_id": "x", "side": "BUY",
                                         "lot": 0.1, "entry_price": 1.0}])
        self.assertEqual(runner.run([{"position_id": "x"}]).outcome,
                         "PAUSE_FOR_OPERATOR")

    def test_matching_state_adopted(self):
        positions = [{"position_id": "p1", "side": "BUY", "lot": 0.1,
                      "entry_price": 4000.0, "level": 1}]

        def seed(store):
            store.save({"basket": {"open_positions": positions}})
        runner, _ = self._runner(seed, positions)
        self.assertEqual(runner.run(positions).outcome, "ADOPTED_STATE")

    def test_mismatch_safe_stop(self):
        positions = [{"position_id": "p1", "side": "BUY", "lot": 0.1,
                      "entry_price": 4000.0}]

        def seed(store):
            store.save({"basket": {"open_positions": [
                {"position_id": "p1", "side": "SELL", "lot": 0.2,
                 "entry_price": 4000.0}]}})
        runner, _ = self._runner(seed, positions)
        self.assertEqual(runner.run(positions).outcome, "SAFE_STOP")

    def test_corrupt_state_safe_stop(self):
        def seed(store):
            store.save({"basket": {}})
            # tamper after save
            with open(store.path, "r", encoding="utf-8") as fh:
                body = json.load(fh)
            body["basket"] = {"hacked": True}
            with open(store.path, "w", encoding="utf-8") as fh:
                json.dump(body, fh)
        runner, _ = self._runner(seed, [])
        self.assertEqual(runner.run([]).outcome, "SAFE_STOP")


class TestReconciliation(unittest.TestCase):
    def test_match(self):
        pos = [{"position_id": "1", "side": "BUY", "lot": 0.1,
                "entry_price": 4000.0}]
        res = ReconciliationEngine().reconcile(pos, pos)
        self.assertEqual(res.status, "MATCH")
        self.assertFalse(res.block_new_orders)

    def test_mismatch_blocks(self):
        a = [{"position_id": "1", "side": "BUY", "lot": 0.1,
              "entry_price": 4000.0}]
        b = [{"position_id": "1", "side": "BUY", "lot": 0.2,
              "entry_price": 4000.0}]
        res = ReconciliationEngine().reconcile(a, b)
        self.assertEqual(res.status, "MISMATCH")
        self.assertTrue(res.block_new_orders)

    def test_unexpected_broker_position(self):
        res = ReconciliationEngine().reconcile(
            [], [{"position_id": "9", "side": "SELL", "lot": 0.1,
                  "entry_price": 1.0}])
        self.assertEqual(res.status, "MISMATCH")
        issues = [d["issue"] for d in res.differences]
        self.assertIn("UNEXPECTED_BROKER_POSITION", issues)


class TestJournalAndAuditChains(unittest.TestCase):
    def test_event_chain_verifies(self):
        d = tmp_dir()
        j = EventJournal("RUN-X", os.path.join(d, "e.jsonl"))
        j.append("CYCLE_OPEN", {"x": 1})
        j.append("ENTRY", {"y": 2})
        self.assertTrue(j.verify_chain())
        self.assertEqual(j.last_sequence, 2)

    def test_undeclared_event_type_rejected(self):
        j = EventJournal("R")
        with self.assertRaises(ValueError):
            j.append("NOT_A_TYPE", {})

    def test_audit_chain_verifies(self):
        d = tmp_dir()
        a = AuditLog("1.0.0", os.path.join(d, "a.jsonl"))
        a.record("op", "OPERATOR", "START", "runtime", "test")
        a.record("sys", "SYSTEM", "KILL", "runtime", "test")
        self.assertTrue(a.verify_chain())
        self.assertEqual(len(a.records(action="KILL")), 1)


if __name__ == "__main__":
    unittest.main()
