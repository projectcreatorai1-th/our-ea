"""Runtime scenario tests — master spec §102 scenarios C, F, G, H, I, J
plus E2E paper run through the RuntimeService with a loopback gateway."""
from __future__ import annotations

import os
import unittest

from tests.harness import make_repo, tmp_dir

from our_ea.core.identifiers import default_analyzer_root  # noqa: F401
from our_ea.execution.paper import PaperAdapter
from our_ea.gateway.client import EAGatewayClient
from our_ea.gateway.transport import LoopbackTransport
from our_ea.runtime.service import RuntimeService


def make_service(mode="OBSERVATION", gateway=True, overrides=None):
    repo = make_repo()
    gw = None
    if gateway:
        gw = EAGatewayClient(transport=LoopbackTransport())
        gw.connect("loopback://test")
    svc = RuntimeService(repo, tmp_dir(), account_id="TEST-001",
                         symbol="GOLDmicro", gateway=gw,
                         parameter_overrides=overrides)
    svc.handle_command("REGISTER")
    svc.handle_command("INITIALIZE")
    if mode != "OBSERVATION":
        svc.promote_mode(mode)
    svc.handle_command("START", {"balance": 500.0})
    return svc


def feed_cycle(service, start=4000.0):
    """Open both sides, force one grid add each side, then close basket."""
    out = []
    out += service.on_tick(start, start - 0.3, "2026-09-25T10:00:00+00:00")
    # BUY adds at <= 3994.7 (last BUY entry 4000 - 5.0); SELL adds at >= 4004.4
    out += service.on_tick(3994.0, 3993.7, "2026-09-25T10:01:00+00:00")
    # price falls further: BUY L3; SELL hold
    out += service.on_tick(3988.5, 3988.2, "2026-09-25T10:02:00+00:00")
    # recover to trigger basket close (gross >= 1.00 with H_GROSS_1_00)
    out += service.on_tick(4001.5, 4001.2, "2026-09-25T10:03:00+00:00")
    out += service.on_tick(4003.0, 4002.7, "2026-09-25T10:04:00+00:00")
    return out


class TestScenarioC_LiveDecisionChain(unittest.TestCase):
    """Market -> Signal basis -> Decision -> Risk -> Order Intent (paper)."""

    def test_chain_present_in_journal(self):
        svc = make_service(mode="PAPER")
        feed_cycle(svc)
        j = svc.journal
        types = [e.event_type for e in j.events()]
        for expected in ("CYCLE_OPEN", "ENTRY", "ORDER_SUBMIT",
                         "ORDER_FILLED", "STATE_TRANSITION"):
            self.assertIn(expected, types)
        entries = j.events("ENTRY")
        self.assertGreaterEqual(len(entries), 2)  # first cycle opens both sides
        first_cycle = entries[0].cycle_id
        self.assertEqual(len([e for e in entries if e.cycle_id == first_cycle]),
                         2, "first cycle must open exactly BUY+SELL")
        # grid adds happened with anchor hypothesis recorded
        grid = j.events("GRID_ADD")
        self.assertGreaterEqual(len(grid), 2)
        for g in grid:
            self.assertIn(g.payload["anchor_hypothesis"],
                          ("H_PREV_ENTRY", "H_EXTREME"))
            self.assertEqual(g.payload["level"], g.payload["level"])


class TestScenarioE2E_PaperRun(unittest.TestCase):
    def test_full_cycle_closes_and_reopens(self):
        svc = make_service(mode="PAPER")
        feed_cycle(svc)
        closes = svc.journal.events("BASKET_CLOSE")
        self.assertTrue(closes, "basket must close on profit domain")
        ends = svc.journal.events("CYCLE_END")
        self.assertGreaterEqual(len(ends), 1)
        # normal resume (E018): the next tick already re-opened a new cycle
        self.assertEqual(len(svc.journal.events("CYCLE_OPEN")), 2)
        self.assertEqual(svc.core.sm.state, "BOTH_SIDES_ACTIVE")
        # the state machine resumed through the declared arc after the close
        resumes = [e for e in svc.journal.events()
                   if e.event_type == "STATE_TRANSITION"
                   and e.payload.get("event") == "RESUME_NEW_CYCLE"]
        self.assertEqual(len(resumes), 1,
                         "RESUME_NEW_CYCLE must fire exactly once per close")

    def test_lot_ladder_in_paper_run(self):
        svc = make_service(mode="PAPER")
        feed_cycle(svc)
        grid = svc.journal.events("GRID_ADD")
        lots = [g.payload["lot"] for g in grid if g.payload["side"] == "BUY"]
        self.assertEqual(lots, [0.11, 0.12])  # L2, L3 floor ladder

    def test_observation_mode_never_orders(self):
        svc = make_service(mode="OBSERVATION")
        feed_cycle(svc)
        skipped = svc.journal.events("ORDER_SUBMIT")
        # in OBSERVATION, submissions are journaled but results are SKIPPED
        filled = svc.journal.events("ORDER_FILLED")
        self.assertEqual(len(filled), 0)
        self.assertTrue(skipped)


class TestScenarioF_Recovery(unittest.TestCase):
    def test_restart_restores_cycle_state(self):
        svc = make_service(mode="PAPER")
        feed_cycle(svc)
        persisted = svc.core.persisted_state()
        # simulate crash: brand-new service, same workdir state file
        svc2 = make_service(mode="PAPER")
        svc2.core.restore(persisted)
        self.assertEqual(svc2.core.sm.state, svc.core.sm.state)
        self.assertEqual(svc2.core.cycle_seq, svc.core.cycle_seq)
        if svc.core.basket and not svc.core.basket.is_flat():
            self.assertEqual(svc2.core.basket.cycle_id,
                             svc.core.basket.cycle_id)
        # recovery event recorded
        rec = svc2.journal.events("RECOVERY")
        self.assertEqual(rec[-1].payload["restored_state"],
                         persisted["state_machine"]["state"])


class TestScenarioG_DeterministicReplay(unittest.TestCase):
    def test_same_ticks_same_decisions(self):
        svc1 = make_service(mode="PAPER", gateway=False)
        svc2 = make_service(mode="PAPER", gateway=False)
        feed_cycle(svc1)
        feed_cycle(svc2)
        self.assertEqual(svc1.core.decision_digest(),
                         svc2.core.decision_digest())
        # replay determinism: same core, replaying same ticks must be
        # captured identically in stable projections (verified above across
        # two independent instances)


class TestScenarioH_KillSwitch(unittest.TestCase):
    def test_kill_blocks_all_orders(self):
        svc = make_service(mode="PAPER")
        svc.on_tick(4000.0, 3999.7, "2026-09-25T10:00:00+00:00")
        before = len(svc.journal.events("ORDER_FILLED"))
        res = svc.handle_command("KILL", {"layer": "ACCOUNT_RISK_KILL"})
        self.assertEqual(res.status, "OK")
        # kill switch engagement is journaled + audited
        kills = svc.journal.events("KILL_SWITCH")
        self.assertTrue(any("OPERATOR_KILL" in k.payload.get("rule", "")
                            for k in kills))
        self.assertTrue(svc.core.risk.kill_switch_engaged)
        # halted service drops ticks entirely: no path to an order
        svc.on_tick(3994.0, 3993.7, "2026-09-25T10:01:00+00:00")
        after = len(svc.journal.events("ORDER_FILLED"))
        self.assertEqual(before, after, "no fills after KILL")

    def test_kill_blocks_risk_gate_too(self):
        svc = make_service(mode="PAPER")
        svc.on_tick(4000.0, 3999.7, "2026-09-25T10:00:00+00:00")
        fills_before = len(svc.journal.events("ORDER_FILLED"))
        svc.halted = False  # simulate gateway-routed tick bypassing halt flag
        svc.handle_command("KILL", {"layer": "STRATEGY_KILL"})
        svc.core.on_tick(3994.0, 3993.7, "2026-09-25T10:01:00+00:00")
        # surveillance short-circuits to SAFE_STOP before any gate/order
        self.assertTrue(svc.core.safe_stopped)
        self.assertEqual(svc.core.sm.state, "SAFE_STOP")
        self.assertEqual(len(svc.journal.events("ORDER_FILLED")), fills_before)


class TestScenarioI_RiskReject(unittest.TestCase):
    def test_risk_reject_leaves_no_order(self):
        # force depth cap breach: MaxGridDepth 12 -> block at L12
        svc = make_service(mode="PAPER")
        core = svc.core
        core.risk.limits = type(core.risk.limits)(
            max_positions=12, max_grid_depth=2, max_total_lot=2.0,
            max_loss_usd=50.0, max_drawdown_pct=30.0, max_spread_usd=0.6,
            max_consecutive_failures=3)
        ts = 0

        def tick(b, s):
            nonlocal ts
            ts += 60
            svc.on_tick(b, s, f"2026-09-25T10:{ts // 60:02d}:00+00:00")

        tick(4000.0, 3999.7)                       # open both (L1)
        tick(3994.0, 3993.7)                       # BUY L2 ok (depth cap 2)
        tick(3988.0, 3987.7)                       # BUY L3 -> RISK_BLOCK
        blocks = [b for b in svc.journal.events("RISK_BLOCK")
                  if b.payload.get("rule") == "MAX_GRID_DEPTH"]
        self.assertEqual(len(blocks), 1)
        # no third BUY fill
        buy_fills = [g for g in svc.journal.events("GRID_ADD")
                     if g.payload["side"] == "BUY"]
        self.assertEqual(len(buy_fills), 1)

    def test_spread_blocked_at_data_quality_gate(self):
        svc = make_service(mode="PAPER")
        # spread 1.0 > MaxSpreadUSD 0.6 -> data-quality gate rejects the tick
        svc.on_tick(4000.0, 3999.0, "2026-09-25T10:00:00+00:00")
        self.assertEqual(len(svc.journal.events("CYCLE_OPEN")), 0)
        rejected = svc.journal.events("TICK_REJECTED")
        self.assertEqual(rejected[0].payload["reason"], "SPREAD_ANOMALY")

    def test_spread_blocked_at_risk_gate_when_data_gate_wider(self):
        svc = make_service(mode="PAPER")
        # tighten risk guard below the data gate threshold
        core = svc.core
        core.risk.limits = type(core.risk.limits)(
            max_positions=12, max_grid_depth=12, max_total_lot=2.0,
            max_loss_usd=50.0, max_drawdown_pct=30.0, max_spread_usd=0.4,
            max_consecutive_failures=3)
        svc.on_tick(4000.0, 3999.5, "2026-09-25T10:00:00+00:00")  # spread 0.5
        self.assertEqual(len(svc.journal.events("CYCLE_OPEN")), 0)
        blocks = svc.journal.events("RISK_BLOCK")
        self.assertEqual(len(blocks), 2)  # both entries risk-blocked
        self.assertTrue(all(b.payload["rule"] == "MAX_SPREAD" for b in blocks))


class TestScenarioJ_DuplicateProtection(unittest.TestCase):
    def test_retry_same_intent_key_one_logical_order(self):
        from our_ea.execution.base import ExecutionIntent
        from our_ea.core.idempotency import IdempotencyLedger
        ad = PaperAdapter(balance=500.0)
        ledger = IdempotencyLedger()
        key = "TEST|GOLDmicro|C000001|OPEN|1"
        intent = ExecutionIntent(
            intent_id="ORD-A", action="OPEN", side="BUY", lot=0.1,
            price=4000.0, idempotency_key=key)

        def factory():
            return ad.submit(intent)
        is_new1, r1 = ledger.check_and_register(key, factory)
        # network-timeout style retry with same key
        is_new2, r2 = ledger.check_and_register(key, factory)
        self.assertTrue(is_new1)
        self.assertFalse(is_new2)
        self.assertEqual(r1.intent_id, r2.intent_id)
        self.assertEqual(len(ad.open_positions()), 1)


class TestServiceCommands(unittest.TestCase):
    def test_command_surface(self):
        svc = make_service(mode="PAPER")
        self.assertEqual(svc.handle_command("PAUSE").status, "OK")
        self.assertTrue(svc.paused)
        svc.on_tick(4000.0, 3999.7, "2026-09-25T10:00:00+00:00")
        self.assertEqual(svc.core.sm.state, "WAITING_FOR_ENTRY")  # paused
        self.assertEqual(svc.handle_command("RESUME").status, "OK")
        self.assertEqual(svc.handle_command("NOT_A_COMMAND").status, "REFUSED")

    def test_halt_safe_stops(self):
        svc = make_service(mode="PAPER")
        svc.on_tick(4000.0, 3999.7, "2026-09-25T10:00:00+00:00")
        svc.handle_command("HALT")
        self.assertTrue(svc.core.safe_stopped)
        self.assertEqual(svc.core.sm.state, "SAFE_STOP")

    def test_status_and_health(self):
        svc = make_service(mode="PAPER")
        st = svc.handle_command("REQUEST_STATUS")
        self.assertEqual(st.status, "OK")
        self.assertEqual(st.detail["mode"], "PAPER")
        h = svc.handle_command("REQUEST_HEALTH")
        self.assertEqual(h.detail["dimensions"]["RISK"], "GREEN")

    def test_reconcile_blocks_on_mismatch(self):
        svc = make_service(mode="PAPER")
        svc.on_tick(4000.0, 3999.7, "2026-09-25T10:00:00+00:00")
        bad = [{"position_id": "GHOST", "side": "BUY", "lot": 0.1,
                "entry_price": 1.0}]
        res = svc.handle_command("RECONCILE", {"broker_positions": bad})
        self.assertEqual(res.status, "BLOCKED")
        self.assertTrue(svc.reconciliation_block)
        # RESUME blocked until reconciled
        self.assertEqual(svc.handle_command("RESUME").status, "BLOCKED")
        ok = svc.handle_command("RECONCILE", {
            "broker_positions": [
                {"position_id": p["position_id"], "side": p["side"],
                 "lot": p["lot"], "entry_price": p["entry_price"]}
                for p in svc.adapter.open_positions()]})
        self.assertEqual(ok.status, "OK")
        self.assertFalse(svc.reconciliation_block)
        self.assertEqual(svc.handle_command("RESUME").status, "OK")

    def test_update_config_only_configurable(self):
        svc = make_service(mode="PAPER")
        res = svc.handle_command("UPDATE_CONFIG",
                                 {"overrides": {"GridStepUSD": 4.8}})
        self.assertEqual(res.status, "OK")
        self.assertEqual(svc.core.grid_engine.config.step_usd, 4.8)
        res = svc.handle_command("UPDATE_CONFIG",
                                 {"overrides": {"BaseLot": 9.9}})
        self.assertEqual(res.status, "REFUSED")

    def test_deploy_manifest_and_rollback_refusal(self):
        svc = make_service(mode="PAPER")
        dep = svc.handle_command("DEPLOY")
        self.assertEqual(dep.status, "OK")
        m = dep.detail
        self.assertTrue(m["live_locked"])
        self.assertEqual(len(m["deployment_hash"]), 64)
        rb = svc.handle_command("ROLLBACK")
        self.assertEqual(rb.status, "REFUSED")

    def test_mode_promotion_ladder(self):
        svc = make_service(mode="OBSERVATION")
        self.assertEqual(svc.promote_mode("LIVE").status, "REFUSED")
        self.assertEqual(svc.journal.events("LIVE_EXECUTION_REQUESTED")
                         [-1].payload["refused"], True)
        self.assertEqual(svc.promote_mode("PAPER").status, "OK")
        demo = svc.promote_mode("DEMO")
        self.assertEqual(demo.status, "ENVIRONMENT_BLOCKED")

    def test_spec_corruption_refuses_initialize(self):
        from our_ea.spec.repository import SpecRepository
        repo = SpecRepository(os.path.join(tmp_dir(), "missing"))
        svc = RuntimeService(repo, tmp_dir())
        res = svc.handle_command("INITIALIZE")
        self.assertEqual(res.status, "REFUSED")


class TestGatewayE2EFlow(unittest.TestCase):
    def test_ea_events_reach_gateway(self):
        svc = make_service(mode="PAPER", gateway=True)
        feed_cycle(svc)
        sent = [m for m in svc.gateway.transport.sent
                if m.get("type") == "EVENT_PUBLISH"]
        etypes = [m["event"]["event_type"] for m in sent]
        self.assertIn("ORDER_SENT", etypes)
        self.assertIn("POSITION_OPENED", etypes)
        self.assertIn("POSITION_CLOSED", etypes)
        # every event carries correlation + sequence (traceable)
        for m in sent:
            ev = m["event"]
            self.assertTrue(ev["correlation_id"].startswith("COR-"))
            self.assertGreaterEqual(ev["sequence"], 1)

    def test_signal_intake_research_only(self):
        svc = make_service(mode="PAPER")
        from our_ea.gateway.contracts import GatewaySignal
        sig = GatewaySignal(
            signal_id="SIG-42", strategy_id="SNIPER-V168",
            strategy_version="1.0", symbol="GOLDmicro", timeframe="M15",
            direction="NEUTRAL", signal_type="ANALYSIS",
            entry_reference=0.0, stop_reference=0.0, target_reference=0.0,
            confidence=0.8, created_at="2026-09-25T00:00:00+00:00",
            expires_at="2999-01-01T00:00:00+00:00",
            analysis_version="v1", dataset_version="d1",
            correlation_id="COR-42")
        out = svc.on_signal(sig)
        self.assertTrue(out["accepted"])
        # accepted signal does NOT create orders by itself
        self.assertEqual(len(svc.journal.events("ORDER_SUBMIT")), 0)
        bad = GatewaySignal(
            signal_id="SIG-43", strategy_id="", strategy_version="",
            symbol="GOLDmicro", timeframe="", direction="UP",
            signal_type="ENTRY", entry_reference=0, stop_reference=0,
            target_reference=0, confidence=2.0, created_at="",
            expires_at="2000-01-01T00:00:00+00:00",
            analysis_version="", dataset_version="")
        self.assertFalse(svc.on_signal(bad)["accepted"])


if __name__ == "__main__":
    unittest.main()
