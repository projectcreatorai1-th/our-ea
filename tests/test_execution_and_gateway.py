"""Execution adapters + gateway client tests."""
from __future__ import annotations

import json
import os
import unittest

from tests.harness import tmp_dir

from our_ea.execution.base import (
    ExecutionIntent, LiveLockError, adapter_for, resolve_mode)
from our_ea.execution.mt5_gateway import MT5GatewayAdapter
from our_ea.execution.paper import PaperAdapter
from our_ea.execution.simulation import SimulationAdapter
from our_ea.gateway.client import (
    ConnectionState, ContractRejected, EAGatewayClient)
from our_ea.gateway.contracts import GatewaySignal, new_id, utc_now_iso
from our_ea.gateway.transport import FileTransport, LoopbackTransport


def _intent(**over):
    base = dict(
        intent_id="ORD-1", action="OPEN", side="BUY", lot=0.1, price=4000.0,
        idempotency_key="K1", cycle_id="C1")
    base.update(over)
    return ExecutionIntent(**base)


class TestPaperAdapter(unittest.TestCase):
    def test_open_close_roundtrip_with_pl(self):
        ad = PaperAdapter(balance=500.0)
        res = ad.submit(_intent())
        self.assertEqual(res.status, "FILLED")
        self.assertEqual(len(ad.open_positions()), 1)
        close = ad.submit(_intent(
            intent_id="ORD-2", action="CLOSE", price=4006.0,
            idempotency_key="K2", position_id=res.position_id))
        self.assertEqual(close.status, "FILLED")
        # BUY 0.1 lot, +6.0 move, contract 1.0 -> +0.6
        self.assertAlmostEqual(ad.balance, 500.6, places=4)

    def test_idempotent_retry_same_key(self):
        ad = PaperAdapter()
        r1 = ad.submit(_intent())
        r2 = ad.submit(_intent())
        self.assertEqual(r1, r2)
        self.assertEqual(len(ad.open_positions()), 1)

    def test_close_all_uses_side_prices(self):
        ad = PaperAdapter()
        b = ad.submit(_intent())
        s = ad.submit(_intent(intent_id="ORD-2", side="SELL", price=3999.7,
                              idempotency_key="K2"))
        res = ad.submit(_intent(intent_id="ORD-3", action="CLOSE_ALL",
                                side="", lot=0.0, price=None,
                                idempotency_key="K3",
                                exit_buy_price=4006.0, exit_sell_price=4005.7))
        self.assertEqual(res.status, "FILLED")
        self.assertEqual(len(ad.open_positions()), 0)
        # BUY: +5.7*0.1=0.57 ; SELL: -6.3*0.1=-0.63 -> -0.06
        self.assertAlmostEqual(ad.balance, 499.94, places=4)

    def test_partial_close_never_executes(self):
        ad = PaperAdapter()
        res = ad.submit(_intent(action="CLOSE_PARTIAL", lot=0.05,
                                idempotency_key="KP"))
        self.assertEqual(res.status, "SKIPPED")

    def test_slippage_adverse(self):
        ad = PaperAdapter(slippage=0.2)
        res = ad.submit(_intent(side="BUY", price=4000.0))
        self.assertEqual(res.price, 4000.2)


class TestSimulationAdapter(unittest.TestCase):
    def test_failure_injection(self):
        ad = SimulationAdapter(fail_intents=1)
        self.assertEqual(ad.submit(_intent()).status, "REJECTED")
        self.assertEqual(ad.submit(_intent(idempotency_key="K2",
                                           intent_id="ORD-2")).status, "FILLED")


class TestModeGate(unittest.TestCase):
    def test_live_locked_everywhere(self):
        with self.assertRaises(LiveLockError):
            resolve_mode("LIVE")
        with self.assertRaises(LiveLockError):
            resolve_mode("live")
        with self.assertRaises(LiveLockError):
            adapter_for("LIVE")

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            resolve_mode("SHADOW_TRADE")


class TestMT5GatewayAdapter(unittest.TestCase):
    def test_env_blocked_without_bridge(self):
        ad = MT5GatewayAdapter(mode="DEMO")
        snap = ad.account_snapshot()
        self.assertEqual(snap["mt5"], "NOT_CONNECTED")
        from our_ea.execution.base import ExecutionUnavailable
        with self.assertRaises(ExecutionUnavailable) as cm:
            ad.submit(_intent())
        self.assertIn("ENVIRONMENT_BLOCKED", str(cm.exception))

    def test_bridge_contract_enforced(self):
        ad = MT5GatewayAdapter(mode="DEMO")
        from our_ea.execution.base import ExecutionUnavailable
        with self.assertRaises(ExecutionUnavailable):
            ad.connect_bridge(object())  # no send/recv

    def test_live_constructor_refused(self):
        with self.assertRaises(LiveLockError):
            MT5GatewayAdapter(mode="LIVE")


class TestGatewayClient(unittest.TestCase):
    def _client(self):
        transport = LoopbackTransport()
        return EAGatewayClient(transport=transport), transport

    def test_contract_handshake(self):
        c, _ = self._client()
        resp = c.connect("loopback://test")
        self.assertTrue(resp["accepted"])
        self.assertEqual(c.state, ConnectionState.CONNECTED)

    def test_contract_version_mismatch_fails_closed(self):
        transport = LoopbackTransport(contract_version="2.0.0")
        c = EAGatewayClient(transport=transport)
        with self.assertRaises(ContractRejected):
            c.connect("loopback://test")
        self.assertEqual(c.state, ConnectionState.ERROR)

    def test_heartbeat(self):
        c, _ = self._client()
        c.connect()
        self.assertTrue(c.send_heartbeat())

    def test_publish_event_and_feedback(self):
        c, _ = self._client()
        c.connect()
        out = c.publish_event("ORDER_FILLED", {"lot": 0.1})
        self.assertTrue(out["ack"].get("accepted"))
        with self.assertRaises(ValueError):
            c.publish_event("SECRET_EVENT", {})

    def test_signal_ingest_dedup_and_validation(self):
        c, t = self._client()
        c.connect()
        got = []
        c.signal_handlers.append(lambda s: got.append(s))
        sig = {
            "type": "SIGNAL_PUBLISH", "signal_id": "SIG-1",
            "strategy_id": "SNIPER-V168", "strategy_version": "1.0",
            "symbol": "GOLDmicro", "timeframe": "M15",
            "direction": "NEUTRAL", "signal_type": "ANALYSIS",
            "entry_reference": 0.0, "stop_reference": 0.0,
            "target_reference": 0.0, "confidence": 0.9,
            "created_at": utc_now_iso(),
            "expires_at": "2999-01-01T00:00:00+00:00",
            "analysis_version": "v1", "dataset_version": "d1",
            "correlation_id": "COR-1",
        }
        t.inject(sig)
        t.inject(dict(sig))  # duplicate dropped
        msgs = c.poll()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(len(got), 1)
        self.assertEqual(GatewaySignal.from_dict(sig).validate(), [])

    def test_invalid_signal_schema(self):
        sig = GatewaySignal(
            signal_id="", strategy_id="", strategy_version="", symbol="",
            timeframe="", direction="SIDEWAYS", signal_type="MAGIC",
            entry_reference=0, stop_reference=0, target_reference=0,
            confidence=1.5, created_at="", expires_at="",
            analysis_version="", dataset_version="")
        self.assertEqual(len(sig.validate()), 4)


class TestFileTransport(unittest.TestCase):
    def test_jsonl_bridge_roundtrip(self):
        d = tmp_dir()
        t = FileTransport()
        t.connect(d)
        t.send(json.dumps({"type": "HEARTBEAT"}).encode())
        with open(os.path.join(d, "gateway_to_ea.jsonl"), "w",
                  encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "HEARTBEAT_ACK"}) + "\n")
        msg = t.recv()
        self.assertIsNotNone(msg)
        self.assertEqual(json.loads(msg)["type"], "HEARTBEAT_ACK")


if __name__ == "__main__":
    unittest.main()
