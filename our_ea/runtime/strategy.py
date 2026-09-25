"""Strategy core — the spec-driven decision pipeline (Phase 13-14).

    Tick -> Validate -> MarketState -> Signal(basis) -> Rule evaluation
        -> Decision -> Risk Gate -> Order Intent -> Adapter -> Events

Every decision produces a replayable DecisionRecord (input_hash/output_hash).
Semantics come from the frozen strategy spec only:
    * cycle opens flat: BUY base + SELL base (R-BOTH-SIDES, VERIFIED)
    * grid adds per side by step/hypothesis (R-GRID-*, VERIFIED/PARTIAL)
    * basket close by declared hypothesis (R-BASKET-TRIGGER, PARTIAL)
    * partial close: UNKNOWN -> MODEL_UNCERTAINTY + skip (R-PARTIAL-*)
    * re-entry after close: immediate next tick (R-NORMAL-RESUME, VERIFIED)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from our_ea import MODEL_ID, RUNTIME_VERSION, STRATEGY_ID, STRATEGY_SPEC_VERSION
from our_ea.core.audit import AuditLog
from our_ea.core.basket_engine import (
    Basket, BasketCloseEngine, PositionRef, make_cycle_id,
)
from our_ea.core.broker_profile import BrokerProfile
from our_ea.core.events import EAEvent, EventJournal
from our_ea.core.grid_engine import GridEngine
from our_ea.core.identifiers import canonical_hash, new_id, utc_now_iso
from our_ea.core.lot_engine import LotEngine
from our_ea.core.market_data import MarketDataPipeline, Tick, TickRejection
from our_ea.core.parameters import ParameterSet
from our_ea.core.partial_engine import PartialDecisionUnavailable, PartialEngine
from our_ea.core.persistence import StateCorruptionError, StateStore
from our_ea.core.risk_guard import RiskGuard, RiskRejection
from our_ea.core.rules import RuleRegistry
from our_ea.core.state_machine import InvalidTransition, SpecStateMachine
from our_ea.execution.base import ExecutionIntent
from our_ea.spec.repository import SpecRepository


@dataclass
class DecisionRecord:
    decision_id: str
    ts: str
    decision: str                 # OPEN_BOTH | OPEN_LONG | OPEN_SHORT | GRID_ADD
                                  # | BASKET_CLOSE | NO_TRADE | NO_OP
    side: str
    lot: float
    price: float
    state_from: str
    state_to: str
    reason: str
    rules: list = field(default_factory=list)
    evidence_refs: list = field(default_factory=list)
    risk_decision_id: str = ""
    intent_ids: list = field(default_factory=list)
    input_hash: str = ""
    output_hash: str = ""
    cycle_id: str = ""

    def stable(self) -> dict:
        """Projection without ids/timestamps — deterministic replay digest."""
        return {
            "decision": self.decision, "side": self.side, "lot": self.lot,
            "price": self.price, "state_from": self.state_from,
            "state_to": self.state_to, "reason": self.reason,
            "rules": sorted(self.rules), "cycle_seq": self.cycle_id,
        }


class StrategyCore:
    """One strategy instance over one symbol/account. Spec-pinned."""

    def __init__(self, repo: SpecRepository, journal: EventJournal,
                 audit: AuditLog, adapter, state_store: StateStore | None = None,
                 account_id: str = "PAPER-001", symbol: str = "GOLDmicro",
                 parameter_overrides: dict | None = None,
                 execute_enabled: bool = True):
        report = repo.verify()
        self.repo = repo
        self.journal = journal
        self.audit = audit
        self.adapter = adapter
        self.state_store = state_store
        self.account_id = account_id
        self.symbol = symbol
        self.execute_enabled = execute_enabled

        self.params = ParameterSet(repo.parameter_spec, parameter_overrides)
        self.rules = RuleRegistry.from_repository(repo)
        self.rules.assert_frozen_expectations()
        self.lot_engine = LotEngine.from_parameters(self.params)
        self.grid_engine = GridEngine.from_parameters(self.params)
        self.close_engine = BasketCloseEngine.from_parameters(self.params)
        self.partial_engine = PartialEngine()
        self.broker = BrokerProfile.from_parameters(self.params)
        self.sm = SpecStateMachine.from_repository(repo)
        self.risk = RiskGuard.from_parameters(self.params, journal, audit)
        self.market = MarketDataPipeline(
            max_spread_usd=float(self.params.require("MaxSpreadUSD")),
            max_tick_volatility_usd=float(self.params.require("MaxTickVolatilityUSD")),
        )
        self.contract_size = float(self.params.require("ContractSize"))

        self.spec_hashes = {
            "strategy_spec_hash": report["strategy_spec_hash"],
            "evidence_model_hash": report["evidence_model_hash"],
            "spec_version": report["spec_version"],
        }
        self.cycle_seq = 0
        self.basket: Basket | None = None
        self.decisions: list[DecisionRecord] = []
        self.safe_stopped = False
        self._fire("INIT")
        self._fire("INIT_OK")

    # ------------------------------------------------------------- helpers
    def _fire(self, event: str, reason: str = ""):
        try:
            tr = self.sm.fire(event, trace_id=self.journal.run_id, reason=reason)
        except InvalidTransition as exc:
            self.journal.append("ERROR", {"error": str(exc), "event": event})
            self._safe_stop(f"invalid transition attempted: {exc}")
            raise
        self.journal.append("STATE_TRANSITION", {
            "from": tr.state_from, "event": event, "to": tr.state_to,
            "status": tr.status, "reason": reason,
        })
        return tr

    def _safe_stop(self, reason: str) -> None:
        self.safe_stopped = True
        if self.sm.can("SAFE_STOP"):
            try:
                self.sm.fire("SAFE_STOP", trace_id=self.journal.run_id)
                self.journal.append("SAFE_STOP", {"reason": reason})
            except InvalidTransition:
                pass
        else:
            self.journal.append("SAFE_STOP", {"reason": reason,
                                              "note": "state has no SAFE_STOP arc; "
                                                      "flag set anyway"})
        self.audit.record("strategy-core", "SYSTEM", "SAFE_STOP", "runtime", reason)

    def _decision(self, **kwargs) -> DecisionRecord:
        d = DecisionRecord(
            decision_id=new_id("DEC"), ts=utc_now_iso(),
            state_from=self.sm.state, state_to=self.sm.state, **kwargs)
        self.decisions.append(d)
        return d

    def _submit(self, intent: ExecutionIntent):
        """Idempotent adapter submission; journal ORDER_* events."""
        self.journal.append("ORDER_SUBMIT", intent.to_dict(),
                            cycle_id=intent.cycle_id,
                            decision_id=intent.decision_id)
        if not self.execute_enabled:
            from our_ea.execution.base import ExecutionResult
            result = ExecutionResult(
                intent.intent_id, intent.idempotency_key, "SKIPPED",
                reason="OBSERVATION mode — no real order")
        else:
            try:
                result = self.adapter.submit(intent)
            except Exception as exc:  # adapter failure -> surveillance handles
                self.risk.record_order_failure(str(exc))
                self.journal.append("ORDER_REJECTED", {
                    "intent_id": intent.intent_id, "error": str(exc)})
                raise
        etype = "ORDER_FILLED" if result.status == "FILLED" else "ORDER_REJECTED"
        self.journal.append(etype, result.to_dict(), cycle_id=intent.cycle_id,
                            decision_id=intent.decision_id)
        if result.status == "FILLED":
            self.risk.record_order_success()
        else:
            self.risk.record_order_failure(result.reason)
        return result

    def _margin_context(self, price: float) -> dict:
        acct = self.adapter.account_snapshot()
        balance = float(acct.get("balance", 0.0) or 0.0)
        total_lots = self.basket.total_open_lots() if self.basket else 0.0
        margin_used = self.broker.margin_for(total_lots, price) if price else 0.0
        return {"balance": balance, "margin_used": margin_used,
                "free_margin": balance}

    # ------------------------------------------------------------ main loop
    def on_tick(self, buy_price: float, sell_price: float,
                ts: str = "") -> list[DecisionRecord]:
        """One market event -> zero or more decision records."""
        if self.safe_stopped:
            return []
        tick = Tick(ts=ts or utc_now_iso(), buy_price=buy_price,
                    sell_price=sell_price)
        try:
            self.market.validate(tick)
        except TickRejection as exc:
            self.journal.append("TICK_REJECTED",
                                {"reason": exc.reason, **exc.detail})
            return []

        made: list[DecisionRecord] = []
        spread = tick.spread

        # ---- emergency surveillance (OUR_EA_POLICY, independent of strategy)
        if self.basket and not self.basket.is_flat():
            gross = self.basket.gross_pl(buy_price, sell_price)
            if not self.risk.check_loss(gross):
                made.append(self._decision(
                    decision="NO_TRADE", side="", lot=0.0,
                    price=(buy_price + sell_price) / 2,
                    reason=f"emergency: cycle gross {gross}",
                    rules=["R-EMERGENCY/UNKNOWN->OUR_EA_POLICY"]))
                self._safe_stop(f"max loss exceeded (gross {gross})")
                return made
            acct = self.adapter.account_snapshot()
            self.risk.check_drawdown(float(acct.get("balance", 0.0) or 0.0))
            if self.risk.kill_switch_engaged:
                self._safe_stop("risk kill switch engaged")
                return made

        # ---- partial close policy gate (UNKNOWN -> never executed)
        if bool(self.params.get("PartialCloseEnabled", False)):
            try:
                self.partial_engine.decide_trigger(gross=None)
            except PartialDecisionUnavailable as exc:
                self.journal.append("MODEL_UNCERTAINTY", {
                    "rule_id": exc.rule_id, "what": exc.what,
                    "handling": "skip partial close (disabled policy)"})

        # ---- basket close evaluation (before adds)
        if self.basket and not self.basket.is_flat():
            gross = self.basket.gross_pl(buy_price, sell_price)
            lots = self.basket.total_open_lots()
            should, thr, reason = self.close_engine.evaluate(gross, lots)
            if should:
                made.append(self._close_basket(buy_price, sell_price,
                                               gross, thr, reason))
                self._checkpoint()
                return made

        # ---- cycle open (flat -> both sides)
        if self.basket is None or self.basket.is_flat():
            d = self._open_cycle(buy_price, sell_price, spread, tick.ts)
            if d is not None:
                made.append(d)
                if self.basket is None or self.basket.is_flat():
                    self._checkpoint()
                    return made

        # ---- grid adds per side
        if self.basket and not self.basket.is_flat():
            made.extend(self._evaluate_grid(buy_price, sell_price, spread,
                                            tick.ts))

        self._checkpoint()
        return made

    # ------------------------------------------------------------- cycle
    def _open_cycle(self, buy: float, sell: float, spread: float,
                    ts: str) -> DecisionRecord | None:
        if self.sm.state != "WAITING_FOR_ENTRY":
            return None
        if self.basket is not None and not self.basket.is_flat():
            return None
        self.cycle_seq += 1
        self.basket = Basket(
            cycle_id=make_cycle_id(self.account_id, self.symbol, self.cycle_seq),
            symbol=self.symbol, opened_at=ts,
            contract_size=self.contract_size)
        d = self._decision(decision="OPEN_CYCLE", side="BOTH", lot=0.0,
                           price=round((buy + sell) / 2, 2),
                           reason="flat state -> open BUY+SELL base lots",
                           rules=["R-BOTH-SIDES"],
                           evidence_refs=["E015"], cycle_id=self.basket.cycle_id)
        margin = self._margin_context(buy)
        base_lot = self.lot_engine.lot_for_level(1)
        opened: list[PositionRef] = []
        for side, price in (("BUY", buy), ("SELL", sell)):
            try:
                self.risk.check_intent(
                    side=side, lot=base_lot, spread=spread,
                    open_positions=len(self.basket.open_positions()),
                    side_level=self.basket.side_level(side),
                    total_open_lots=self.basket.total_open_lots(),
                    margin_used=margin["margin_used"],
                    free_margin=margin["free_margin"], cycle_id=d.decision_id)
            except RiskRejection as exc:
                self.journal.append("RISK_BLOCK", {
                    "side": side, "rule": exc.rule, "reason": exc.reason,
                    "decision_id": d.decision_id})
                continue
            lot = self.broker.normalize_volume(base_lot)
            intent = ExecutionIntent(
                intent_id=new_id("ORD"), action="OPEN", side=side, lot=lot,
                price=price, idempotency_key=new_id("REQ"),
                cycle_id=self.basket.cycle_id, decision_id=d.decision_id,
                reason="initial both-sides entry")
            try:
                result = self._submit(intent)
            except Exception:
                continue
            if result.status == "FILLED":
                self.basket.add(PositionRef(
                    position_id=result.position_id, side=side, lot=result.filled_lot,
                    entry_price=result.price, level=1, opened_at=ts))
                opened.append(self.basket.positions[-1])

        if not opened:
            d.decision = "NO_TRADE"
            d.reason = "all initial entries blocked/failed"
            if self.sm.can("ALL_ENTRIES_FAILED"):
                self._fire("ALL_ENTRIES_FAILED", "no initial entry filled")
            return d

        event = "OPEN_BOTH" if len(opened) == 2 else (
            "OPEN_LONG" if opened[0].side == "BUY" else "OPEN_SHORT")
        if self.sm.can(event):
            self._fire(event, "initial entries filled")
        d.decision = "OPEN_BOTH" if len(opened) == 2 else d.decision
        d.state_to = self.sm.state
        d.lot = round(sum(p.lot for p in opened), 2)
        d.intent_ids = [p.position_id for p in opened]
        d.cycle_id = self.basket.cycle_id
        self.journal.append("CYCLE_OPEN", {
            "cycle_id": self.basket.cycle_id, "positions": len(opened),
            "base_lot": base_lot, "decision_id": d.decision_id},
            cycle_id=self.basket.cycle_id, decision_id=d.decision_id)
        for p in opened:
            self.journal.append("ENTRY", {
                "position_id": p.position_id, "side": p.side, "lot": p.lot,
                "price": p.entry_price, "level": p.level,
                "decision_id": d.decision_id},
                cycle_id=self.basket.cycle_id, decision_id=d.decision_id)
        return d

    # ------------------------------------------------------------- grid
    def _evaluate_grid(self, buy: float, sell: float, spread: float,
                       ts: str) -> list[DecisionRecord]:
        out: list[DecisionRecord] = []
        for side, price in (("BUY", buy), ("SELL", sell)):
            last = self.basket.last_entry_price(side)
            extreme = self.basket.side_extreme(side)
            gd = self.grid_engine.evaluate(side, price, last, extreme)
            if gd.action != "ADD":
                continue
            level = self.basket.side_level(side) + 1
            lot = self.lot_engine.lot_for_level(level)
            margin = self._margin_context(price)
            d = self._decision(
                decision="ADD_GRID", side=side, lot=lot, price=price,
                reason=gd.reason,
                rules=["R-GRID-DIRECTION", "R-GRID-SPACING", "R-GRID-TRIGGER"],
                evidence_refs=["E013", "E014", "E026"], cycle_id=self.basket.cycle_id)
            try:
                self.risk.check_intent(
                    side=side, lot=lot, spread=spread,
                    open_positions=len(self.basket.open_positions()),
                    side_level=self.basket.side_level(side),
                    total_open_lots=self.basket.total_open_lots(),
                    margin_used=margin["margin_used"],
                    free_margin=margin["free_margin"], cycle_id=self.basket.cycle_id)
            except RiskRejection as exc:
                self.journal.append("RISK_BLOCK", {
                    "side": side, "rule": exc.rule, "reason": exc.reason,
                    "level": level, "decision_id": d.decision_id},
                    cycle_id=self.basket.cycle_id, decision_id=d.decision_id)
                d.decision = "NO_TRADE"
                d.reason = f"risk blocked: {exc.rule}"
                out.append(d)
                continue
            lot = self.broker.normalize_volume(lot)
            intent = ExecutionIntent(
                intent_id=new_id("ORD"), action="OPEN", side=side, lot=lot,
                price=price, idempotency_key=new_id("REQ"),
                cycle_id=self.basket.cycle_id, decision_id=d.decision_id,
                reason=f"grid add L{level}")
            try:
                result = self._submit(intent)
            except Exception:
                d.decision = "NO_TRADE"
                out.append(d)
                continue
            if result.status != "FILLED":
                d.decision = "NO_TRADE"
                d.reason = "grid add not filled"
                out.append(d)
                continue
            self.basket.add(PositionRef(
                position_id=result.position_id, side=side,
                lot=result.filled_lot, entry_price=result.price,
                level=level, opened_at=ts))
            if self.sm.can("GRID_ADD"):
                self._fire("GRID_ADD", f"{side} L{level}")
            d.state_to = self.sm.state
            d.intent_ids = [result.position_id]
            self.journal.append("GRID_ADD", {
                "side": side, "level": level, "lot": result.filled_lot,
                "price": result.price, "anchor": gd.anchor_price,
                "anchor_hypothesis": gd.anchor_hypothesis,
                "gap_usd": gd.gap_usd, "position_id": result.position_id,
                "decision_id": d.decision_id},
                cycle_id=self.basket.cycle_id, decision_id=d.decision_id)
            out.append(d)
        return out

    # ------------------------------------------------------------- close
    def _close_basket(self, buy: float, sell: float, gross: float,
                      threshold: float, reason: str) -> DecisionRecord:
        d = self._decision(
            decision="BASKET_CLOSE", side="BOTH", lot=0.0,
            price=round((buy + sell) / 2, 2), reason=reason,
            rules=["R-BASKET-TRIGGER", "R-NORMAL-RESUME"],
            evidence_refs=["E016", "E027", "E018"],
            cycle_id=self.basket.cycle_id)
        if self.sm.can("BASKET_INTENT"):
            self._fire("BASKET_INTENT", reason)
        self.journal.append("BASKET_CLOSE_INTENT", {
            "gross": gross, "threshold": threshold, "reason": reason,
            "hypothesis": self.close_engine.config.hypothesis,
            "decision_id": d.decision_id},
            cycle_id=self.basket.cycle_id, decision_id=d.decision_id)
        if self.sm.can("CLOSE_START"):
            self._fire("CLOSE_START", "executing basket close")

        closed_ids: list[str] = []
        for pos in list(self.basket.open_positions()):
            exit_px = sell if pos.side == "BUY" else buy
            intent = ExecutionIntent(
                intent_id=new_id("ORD"), action="CLOSE", side=pos.side,
                lot=pos.lot, price=exit_px, idempotency_key=new_id("REQ"),
                cycle_id=self.basket.cycle_id, decision_id=d.decision_id,
                position_id=pos.position_id, reason="basket close")
            try:
                result = self._submit(intent)
            except Exception:
                break
            if result.status == "FILLED":
                realized = self.basket.close_position(pos.position_id,
                                                      result.price,
                                                      utc_now_iso())
                closed_ids.append(pos.position_id)
                self.journal.append("BASKET_CLOSE", {
                    "position_id": pos.position_id, "side": pos.side,
                    "lot": pos.lot, "exit": result.price,
                    "realized": realized, "decision_id": d.decision_id},
                    cycle_id=self.basket.cycle_id, decision_id=d.decision_id)

        if self.basket.is_flat():
            self.basket.mark_closed(utc_now_iso())
            if self.sm.can("CLOSED_ALL"):
                self._fire("CLOSED_ALL", "all positions closed")
            if self.sm.can("RESUME_NEW_CYCLE"):
                self._fire("RESUME_NEW_CYCLE", "normal resume (E018)")
            d.state_to = self.sm.state
            d.intent_ids = closed_ids
            self.journal.append("CYCLE_END", {
                "cycle_id": self.basket.cycle_id,
                "realized_pl": self.basket.realized_pl,
                "positions": len(self.basket.positions),
                "decision_id": d.decision_id},
                cycle_id=self.basket.cycle_id, decision_id=d.decision_id)
        else:
            d.decision = "NO_TRADE"
            d.reason = "basket close incomplete"
            if self.sm.can("CLOSE_FAIL"):
                self._fire("CLOSE_FAIL", "basket close incomplete")
        return d

    # -------------------------------------------------------- persistence
    def _checkpoint(self) -> None:
        if not self.state_store:
            return
        state = self.persisted_state()
        try:
            self.state_store.save(state)
        except OSError as exc:
            self.journal.append("ERROR", {"error": f"checkpoint failed: {exc}"})

    def persisted_state(self) -> dict:
        basket = None
        if self.basket is not None:
            basket = {
                "cycle_id": self.basket.cycle_id,
                "opened_at": self.basket.opened_at,
                "state": self.basket.state,
                "realized_pl": self.basket.realized_pl,
                "closed_at": self.basket.closed_at,
                "open_positions": [
                    {"position_id": p.position_id, "side": p.side, "lot": p.lot,
                     "entry_price": p.entry_price, "level": p.level,
                     "opened_at": p.opened_at}
                    for p in self.basket.open_positions()
                ],
            }
        return {
            "run_id": self.journal.run_id,
            "account_id": self.account_id,
            "symbol": self.symbol,
            "cycle_seq": self.cycle_seq,
            "state_machine": {"state": self.sm.state},
            "basket": basket if basket else {},
            "risk": self.risk.snapshot(),
            "parameters": self.params.snapshot(),
            "last_event_seq": self.journal.last_sequence,
            "last_event_hash": self.journal.last_hash,
            "spec": self.spec_hashes,
            "runtime_version": RUNTIME_VERSION,
        }

    def restore(self, state: dict) -> None:
        """Rebuild from a verified persisted state (restart never re-mints)."""
        spec = state.get("spec", {})
        if spec.get("strategy_spec_hash") not in ("", self.spec_hashes["strategy_spec_hash"]):
            raise StateCorruptionError(
                f"persisted spec {spec.get('strategy_spec_hash')} != pinned")
        self.cycle_seq = int(state.get("cycle_seq", 0))
        b = state.get("basket") or {}
        if b.get("cycle_id"):
            basket = Basket(b["cycle_id"], self.symbol, b.get("opened_at", ""),
                            contract_size=self.contract_size)
            basket.realized_pl = float(b.get("realized_pl", 0.0))
            basket.state = b.get("state", "ACTIVE")
            basket.closed_at = b.get("closed_at", "")
            for p in b.get("open_positions", []):
                basket.add(PositionRef(
                    position_id=p["position_id"], side=p["side"],
                    lot=float(p["lot"]), entry_price=float(p["entry_price"]),
                    level=int(p.get("level", 1)), opened_at=p.get("opened_at", "")))
            self.basket = basket if not basket.is_flat() else basket
        sm_state = state.get("state_machine", {}).get("state", "")
        if sm_state in self.sm.states:
            self.sm._state = sm_state  # recovery restore (audited below)
        risk = state.get("risk", {})
        self.risk.consecutive_failures = int(risk.get("consecutive_failures", 0))
        self.risk.peak_balance = float(risk.get("peak_balance", 0.0))
        self.journal.append("RECOVERY", {
            "restored_state": sm_state, "cycle_seq": self.cycle_seq,
            "basket": b.get("cycle_id", "")})
        self.audit.record("strategy-core", "RECOVERY", "STATE_RESTORED",
                          "runtime", f"state={sm_state}")

    # ---------------------------------------------------------- replay aid
    def decision_digest(self) -> str:
        return canonical_hash([d.stable() for d in self.decisions])
