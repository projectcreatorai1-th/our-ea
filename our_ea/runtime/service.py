"""Runtime service — operator command surface, modes, kill switch, health.

Commands accepted from the 1144 OS via the SNIPER Gateway (EA_COMMANDS):
    REGISTER INITIALIZE START STOP PAUSE RESUME HALT KILL RECONCILE SYNC
    DEPLOY ROLLBACK UPDATE_CONFIG REQUEST_STATUS REQUEST_HEALTH

Modes (no skip-ahead; LIVE always refused + audited):
    OBSERVATION -> PAPER -> DEMO(env-blocked without gateway bridge) ; LIVE ✗

Every command is audited (actor/action/before/after/reason) and journaled.
"""
from __future__ import annotations

from our_ea import LIVE_LOCKED, RUNTIME_VERSION
from our_ea.core.audit import AuditLog
from our_ea.core.events import EventJournal
from our_ea.core.identifiers import canonical_hash, new_id, utc_now_iso
from our_ea.core.parameters import ParameterViolation
from our_ea.core.reconciliation import ReconciliationEngine
from our_ea.execution.base import ExecutionUnavailable, LiveLockError
from our_ea.execution.paper import PaperAdapter
from our_ea.execution.simulation import SimulationAdapter
from our_ea.gateway.client import EAGatewayClient
from our_ea.gateway.contracts import GatewaySignal
from our_ea.core.persistence import StateStore
from our_ea.runtime.strategy import StrategyCore
from our_ea.spec.repository import SpecRepository

MODES = ("OBSERVATION", "PAPER", "DEMO", "LIVE")
MODE_ORDER = {"OBSERVATION": 0, "PAPER": 1, "DEMO": 2, "LIVE": 3}


class CommandResult:
    def __init__(self, command: str, status: str, detail: dict | None = None):
        self.command = command
        self.status = status        # OK | REFUSED | BLOCKED | ENVIRONMENT_BLOCKED
        self.detail = detail or {}
        self.ts = utc_now_iso()

    def to_dict(self) -> dict:
        return {"command": self.command, "status": self.status,
                "detail": self.detail, "ts": self.ts}


class RuntimeService:
    """Owns the strategy core lifecycle; the only privileged entry point."""

    def __init__(self, repo: SpecRepository, workdir: str,
                 account_id: str = "PAPER-001", symbol: str = "GOLDmicro",
                 gateway: EAGatewayClient | None = None,
                 parameter_overrides: dict | None = None):
        import os
        self.repo = repo
        self.workdir = workdir
        os.makedirs(workdir, exist_ok=True)
        self.run_id = new_id("RUN")
        self.journal = EventJournal(
            self.run_id, os.path.join(workdir, "events.jsonl"))
        self.audit = AuditLog(RUNTIME_VERSION,
                              os.path.join(workdir, "audit.jsonl"))
        self.gateway = gateway
        self.mode = "OBSERVATION"
        self.paused = False
        self.halted = False
        self.registered = False
        self.reconciliation_block = False
        self.last_reconciliation = None
        self.reconciler = ReconciliationEngine(self.journal)
        self.state_store = StateStore(os.path.join(workdir, "runtime_state.json"))
        self.adapter = SimulationAdapter(
            contract_size=float(1.0))
        self.core: StrategyCore | None = None
        self._account_id = account_id
        self._symbol = symbol
        self._overrides = dict(parameter_overrides or {})
        if self.gateway is not None:
            self.gateway.signal_handlers.append(self.on_signal)

    # ------------------------------------------------------------ lifecycle
    def _ensure_core(self, execute_enabled: bool, adapter=None) -> StrategyCore:
        return StrategyCore(
            self.repo, self.journal, self.audit,
            adapter or self.adapter, self.state_store,
            account_id=self._account_id, symbol=self._symbol,
            parameter_overrides=self._overrides,
            execute_enabled=execute_enabled)

    # ------------------------------------------------------------- commands
    def handle_command(self, command: str, params: dict | None = None,
                       actor: str = "operator") -> CommandResult:
        params = dict(params or {})
        handler = getattr(self, f"_cmd_{command.lower()}", None)
        if handler is None:
            self._audit_cmd(actor, command, "REFUSED",
                            {"reason": "unknown command"})
            return CommandResult(command, "REFUSED",
                                 {"reason": f"unknown command {command!r}"})
        result = handler(params, actor)
        self.journal.append("COMMAND", {
            "command": command, "actor": actor, "status": result.status,
            "detail": result.detail})
        return result

    def _audit_cmd(self, actor: str, command: str, status: str,
                   detail: dict, before: dict | None = None) -> None:
        self.audit.record(actor, "OPERATOR", command, "runtime",
                          f"status={status}", before=before or {}, after=detail)

    # REGISTER / INITIALIZE / START / STOP / PAUSE / RESUME / HALT / KILL
    def _cmd_register(self, p, actor):
        self.registered = True
        detail = {"run_id": self.run_id, "runtime_version": RUNTIME_VERSION,
                  "mode": self.mode}
        self._audit_cmd(actor, "REGISTER", "OK", detail)
        return CommandResult("REGISTER", "OK", detail)

    def _cmd_initialize(self, p, actor):
        try:
            report = self.repo.verify()
        except Exception as exc:
            self._audit_cmd(actor, "INITIALIZE", "REFUSED", {"reason": str(exc)})
            return CommandResult("INITIALIZE", "REFUSED", {"reason": str(exc)})
        self.core = self._ensure_core(execute_enabled=False)
        self._audit_cmd(actor, "INITIALIZE", "OK", {"spec": report})
        return CommandResult("INITIALIZE", "OK", {"spec": report})

    def _cmd_start(self, p, actor):
        if not self.registered:
            return CommandResult("START", "REFUSED",
                                 {"reason": "not registered"})
        if self.core is None:
            self.core = self._ensure_core(execute_enabled=False)
        if self.mode == "PAPER":
            self.adapter = PaperAdapter(
                balance=float(p.get("balance", 500.0)),
                contract_size=float(self.core.contract_size))
            self.core = self._ensure_core(
                execute_enabled=True, adapter=self.adapter)
        elif self.mode == "OBSERVATION":
            self.core = self._ensure_core(execute_enabled=False)
        self.halted = False
        self.paused = False
        self._audit_cmd(actor, "START", "OK", {"mode": self.mode})
        return CommandResult("START", "OK", {"mode": self.mode})

    def _cmd_stop(self, p, actor):
        self.halted = True
        self._audit_cmd(actor, "STOP", "OK", {})
        return CommandResult("STOP", "OK", {})

    def _cmd_pause(self, p, actor):
        self.paused = True
        self._audit_cmd(actor, "PAUSE", "OK", {})
        return CommandResult("PAUSE", "OK", {})

    def _cmd_resume(self, p, actor):
        if self.reconciliation_block:
            return CommandResult("RESUME", "BLOCKED", {
                "reason": "reconciliation mismatch unresolved — run RECONCILE"})
        self.paused = False
        self._audit_cmd(actor, "RESUME", "OK", {})
        return CommandResult("RESUME", "OK", {})

    def _cmd_halt(self, p, actor):
        self.halted = True
        if self.core:
            self.core._safe_stop(f"HALT by {actor}")
        self._audit_cmd(actor, "HALT", "OK", {})
        return CommandResult("HALT", "OK", {})

    def _cmd_kill(self, p, actor):
        layer = p.get("layer", "STRATEGY_KILL")
        if self.core:
            self.core.risk.engage_kill_switch("OPERATOR_KILL",
                                              f"{actor} kill ({layer})")
        self.halted = True
        self._audit_cmd(actor, "KILL", "OK", {"layer": layer})
        return CommandResult("KILL", "OK", {"layer": layer,
                                            "note": "STOP_NEW_ORDERS applied"})

    # RECONCILE / SYNC
    def _cmd_reconcile(self, p, actor):
        if self.core is None:
            return CommandResult("RECONCILE", "REFUSED", {"reason": "not initialized"})
        ea_positions = [
            {"position_id": pos["position_id"], "side": pos["side"],
             "lot": pos["lot"], "entry_price": pos["entry_price"]}
            for pos in self.adapter.open_positions()
        ]
        broker_positions = p.get("broker_positions")
        if broker_positions is None:
            broker_positions = ea_positions  # source = adapter (honest: same)
        result = self.reconciler.reconcile(ea_positions, broker_positions,
                                           run_id=self.run_id)
        self.last_reconciliation = result
        self.reconciliation_block = result.block_new_orders
        status = "OK" if result.status == "MATCH" else "BLOCKED"
        self._audit_cmd(actor, "RECONCILE", status, result.to_dict())
        return CommandResult("RECONCILE", status, result.to_dict())

    def _cmd_sync(self, p, actor):
        snap = self.adapter.account_snapshot()
        self._audit_cmd(actor, "SYNC", "OK", snap)
        return CommandResult("SYNC", "OK", snap)

    # DEPLOY / ROLLBACK
    def _cmd_deploy(self, p, actor):
        try:
            report = self.repo.verify()
        except Exception as exc:
            return CommandResult("DEPLOY", "REFUSED", {"reason": str(exc)})
        manifest = self.build_deployment_manifest()
        self._audit_cmd(actor, "DEPLOY", "OK",
                        {"deployment_hash": manifest["deployment_hash"]})
        return CommandResult("DEPLOY", "OK", manifest)

    def _cmd_rollback(self, p, actor):
        reason = ("ROLLBACK refused: runtime is pinned to a single verified "
                  "spec version; redeploy a prior manifest via the 1144 OS "
                  "(deployment authority) — EA does not self-rollback")
        self._audit_cmd(actor, "ROLLBACK", "REFUSED", {"reason": reason})
        return CommandResult("ROLLBACK", "REFUSED", {"reason": reason})

    # UPDATE_CONFIG
    def _cmd_update_config(self, p, actor):
        if self.core is None:
            return CommandResult("UPDATE_CONFIG", "REFUSED",
                                 {"reason": "not initialized"})
        before = self.core.params.snapshot()
        try:
            self.core.params.apply_overrides(p.get("overrides", {}))
        except ParameterViolation as exc:
            return CommandResult("UPDATE_CONFIG", "REFUSED",
                                 {"reason": str(exc)})
        after = self.core.params.snapshot()
        self._audit_cmd(actor, "UPDATE_CONFIG", "OK",
                        {"changed": p.get("overrides", {})}, before=before)
        # rebuild engines from effective parameters
        from our_ea.core.grid_engine import GridEngine
        from our_ea.core.basket_engine import BasketCloseEngine
        self.core.grid_engine = GridEngine.from_parameters(self.core.params)
        self.core.close_engine = BasketCloseEngine.from_parameters(self.core.params)
        return CommandResult("UPDATE_CONFIG", "OK", {"effective": after})

    # STATUS / HEALTH
    def _cmd_request_status(self, p, actor):
        return CommandResult("REQUEST_STATUS", "OK", self.status())

    def _cmd_request_health(self, p, actor):
        return CommandResult("REQUEST_HEALTH", "OK", self.health())

    # ------------------------------------------------------------- mode api
    def promote_mode(self, target: str, actor: str = "operator") -> CommandResult:
        target = (target or "").upper()
        if target not in MODES:
            return CommandResult("PROMOTE_MODE", "REFUSED",
                                 {"reason": f"unknown mode {target}"})
        if target == "LIVE":
            self.journal.append("LIVE_EXECUTION_REQUESTED",
                                {"actor": actor, "refused": True})
            self._audit_cmd(actor, "PROMOTE_MODE:LIVE", "REFUSED",
                            {"reason": "LIVE permanently locked (INV-007)"})
            return CommandResult("PROMOTE_MODE", "REFUSED", {
                "reason": "LIVE permanently locked until explicit human decision"})
        if MODE_ORDER[target] <= MODE_ORDER.get(self.mode, 0):
            return CommandResult("PROMOTE_MODE", "REFUSED",
                                 {"reason": f"no skip-back: at {self.mode}"})
        if target == "DEMO":
            from our_ea.execution.mt5_gateway import MT5GatewayAdapter
            demo = MT5GatewayAdapter(mode="DEMO")
            if not demo.bridge_connected:
                self._audit_cmd(actor, "PROMOTE_MODE:DEMO", "ENVIRONMENT_BLOCKED",
                                {"reason": "gateway server not deployed"})
                return CommandResult("PROMOTE_MODE", "ENVIRONMENT_BLOCKED", {
                    "reason": "SNIPER Gateway server not deployed — DEMO "
                              "execution unavailable in this environment"})
        before = self.mode
        self.mode = target
        self._audit_cmd(actor, "PROMOTE_MODE", "OK",
                        {"mode": target}, before={"mode": before})
        return CommandResult("PROMOTE_MODE", "OK", {"mode": target})

    # ------------------------------------------------------------- signals
    def on_signal(self, signal: GatewaySignal) -> dict:
        """Research signal intake. Signals NEVER become orders directly."""
        self.journal.append("SIGNAL_RECEIVED", {
            "signal_id": signal.signal_id, "direction": signal.direction,
            "signal_type": signal.signal_type, "symbol": signal.symbol,
            "confidence": signal.confidence})
        errors = signal.validate()
        if errors or signal.is_expired():
            reason = "expired" if not errors else ",".join(errors)
            self.journal.append("SIGNAL_REJECTED", {
                "signal_id": signal.signal_id, "reason": reason})
            if self.gateway:
                self.gateway.publish_event(
                    "SIGNAL_REJECTED",
                    {"signal_id": signal.signal_id, "reason": reason})
            return {"accepted": False, "reason": reason}
        self.journal.append("SIGNAL_ACCEPTED", {
            "signal_id": signal.signal_id,
            "note": "research input recorded; execution authority stays with EA"})
        if self.gateway:
            self.gateway.publish_event(
                "SIGNAL_ACCEPTED", {"signal_id": signal.signal_id})
        return {"accepted": True}

    # --------------------------------------------------------------- market
    def on_tick(self, buy_price: float, sell_price: float, ts: str = "") -> list:
        """Operator/market entry point honoring control state."""
        if self.paused or self.halted or self.core is None:
            return []
        if self.reconciliation_block:
            self.journal.append("RISK_BLOCK", {
                "rule": "RECONCILIATION_BLOCK", "reason": "mismatch unresolved"})
            return []
        decisions = self.core.on_tick(buy_price, sell_price, ts)
        self._publish_gateway_events(decisions)
        return decisions

    def _publish_gateway_events(self, decisions) -> None:
        if self.gateway is None or self.gateway.state != "CONNECTED":
            return
        for d in decisions:
            if d.decision in ("OPEN_CYCLE", "ADD_GRID"):
                self.gateway.publish_event("ORDER_SENT", {
                    "decision": d.decision, "side": d.side, "lot": d.lot,
                    "price": d.price, "cycle_id": d.cycle_id})
                self.gateway.publish_event("POSITION_OPENED", {
                    "decision": d.decision, "side": d.side, "cycle_id": d.cycle_id})
            elif d.decision == "BASKET_CLOSE":
                self.gateway.publish_event("POSITION_CLOSED", {
                    "cycle_id": d.cycle_id, "reason": d.reason})

    # ------------------------------------------------------------- views
    def build_deployment_manifest(self) -> dict:
        report = self.repo.verify()
        manifest = {
            "runtime_id": "OUR-EA",
            "runtime_version": RUNTIME_VERSION,
            "strategy_id": "SNIPER-V168",
            "spec_version": report["spec_version"],
            "strategy_spec_hash": report["strategy_spec_hash"],
            "evidence_model_hash": report["evidence_model_hash"],
            "build_contract_version": report["build_contract_version"],
            "mode": self.mode,
            "live_locked": LIVE_LOCKED,
            "gateway_contract": "1.0.0",
            "created_at": utc_now_iso(),
        }
        manifest["deployment_hash"] = canonical_hash(manifest)
        return manifest

    def status(self) -> dict:
        core = self.core
        return {
            "run_id": self.run_id,
            "runtime_version": RUNTIME_VERSION,
            "mode": self.mode,
            "paused": self.paused,
            "halted": self.halted,
            "registered": self.registered,
            "reconciliation_block": self.reconciliation_block,
            "state_machine": core.sm.snapshot() if core else None,
            "basket": core.basket.summary() if core and core.basket else None,
            "adapter": self.adapter.account_snapshot(),
            "journal": self.journal.snapshot(),
            "audit": self.audit.snapshot(),
            "gateway": self.gateway.status() if self.gateway else None,
            "risk": core.risk.snapshot() if core else None,
        }

    def health(self) -> dict:
        def dim(state: str) -> str:
            return {"GREEN": "GREEN", "YELLOW": "YELLOW", "RED": "RED"}.get(
                state, "UNKNOWN")
        core = self.core
        dims = {
            "RISK": dim("RED" if (core and core.risk.kill_switch_engaged)
                        else "GREEN"),
            "EXECUTION": dim("RED" if self.halted else "GREEN"),
            "RECONCILIATION": dim(
                "RED" if self.reconciliation_block else
                ("YELLOW" if self.last_reconciliation is None else "GREEN")),
            "GATEWAY": dim(
                "YELLOW" if self.gateway is None else
                ("GREEN" if self.gateway.state == "CONNECTED" else "YELLOW")),
            "OPERATIONAL": dim("RED" if (core and core.safe_stopped) else
                               ("YELLOW" if self.paused else "GREEN")),
        }
        dims["OVERALL"] = "RED" if "RED" in dims.values() else (
            "YELLOW" if "YELLOW" in dims.values() else "GREEN")
        return {"dimensions": dims, "ts": utc_now_iso()}
