"""Gateway contracts — EA side of SNIPER Gateway contract v1.0.0.

Field-for-field compatible with the Analyzer's core/gateway/contracts.py
(the deployed contract authority). The EA additionally declares the command
set it accepts from the 1144 OS through the gateway (command routing per
master contract §3.3) and its own capabilities tuple.

Hard rules:
    * EA NEVER accepts an order request from SNIPER (signals are research
      input only; execution authority stays with the EA).
    * EA emits EXECUTION_RESULT feedback + gateway events for the Analyzer.
    * Contract handshake fails closed on version mismatch (major.minor).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

GATEWAY_CONTRACT_VERSION = "1.0.0"
SIGNAL_SCHEMA_VERSION = "1.0.0"
EVENT_SCHEMA_VERSION = "1.0.0"

EA_CLIENT_ID = "OUR-EA-RUNTIME"
EA_SUPPORTED_FEATURES = (
    "signal.subscribe",      # receive research signals
    "event.publish",         # publish EA events
    "execution.feedback",    # EXECUTION_RESULT to analyzer
    "heartbeat",
    "command.accept",        # OS command routing
    "state.snapshot",
    "reconciliation",
)
EA_CAPABILITIES = ("strategy-enforcement", "risk-gate", "execution",
                   "paper", "simulation")

# Commands the EA accepts via gateway (1144 OS -> Gateway -> EA)
EA_COMMANDS = (
    "REGISTER", "INITIALIZE", "START", "STOP", "PAUSE", "RESUME",
    "HALT", "KILL", "RECONCILE", "SYNC", "DEPLOY", "ROLLBACK",
    "UPDATE_CONFIG", "REQUEST_STATUS", "REQUEST_HEALTH",
)

# Gateway events the EA may publish (subset of GATEWAY_EVENTS relevant to EA)
EA_PUBLISHABLE_EVENTS = (
    "EA_CONNECTED", "EA_DISCONNECTED", "EA_HEARTBEAT",
    "ORDER_CREATED", "ORDER_SENT", "ORDER_FILLED", "ORDER_REJECTED",
    "ORDER_CANCELLED", "ORDER_PARTIALLY_FILLED",
    "POSITION_OPENED", "POSITION_CLOSED",
    "RISK_UPDATED",
    "SIGNAL_ACCEPTED", "SIGNAL_REJECTED",
    "EXECUTION_RESULT",
    "RECONCILIATION_STARTED", "RECONCILIATION_COMPLETED",
    "RECONCILIATION_FAILED",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def canonical_hash(data) -> str:
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str).encode()).hexdigest().upper()


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    event_type: str
    schema_version: str
    created_at: str
    source: str                 # "OUR-EA"
    target: str                 # "1144-GATEWAY"
    sequence: int
    correlation_id: str
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "EventEnvelope":
        return EventEnvelope(
            event_id=d["event_id"], event_type=d["event_type"],
            schema_version=d.get("schema_version", EVENT_SCHEMA_VERSION),
            created_at=d["created_at"], source=d.get("source", ""),
            target=d.get("target", ""), sequence=int(d.get("sequence", 0)),
            correlation_id=d.get("correlation_id", ""),
            payload=dict(d.get("payload", {})),
        )


@dataclass(frozen=True)
class GatewaySignal:
    """Research signal received from SNIPER via the gateway (research input
    only — never an order; fields mirror Signal schema v1.0.0)."""
    signal_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    timeframe: str
    direction: str             # BUY | SELL | NEUTRAL
    signal_type: str           # ENTRY | EXIT | FILTER | ALERT | ANALYSIS
    entry_reference: float
    stop_reference: float
    target_reference: float
    confidence: float
    created_at: str
    expires_at: str
    analysis_version: str
    dataset_version: str
    correlation_id: str = ""

    @staticmethod
    def from_dict(d: dict) -> "GatewaySignal":
        return GatewaySignal(
            signal_id=d["signal_id"], strategy_id=d.get("strategy_id", ""),
            strategy_version=d.get("strategy_version", ""),
            symbol=d.get("symbol", ""), timeframe=d.get("timeframe", ""),
            direction=d.get("direction", ""), signal_type=d.get("signal_type", ""),
            entry_reference=float(d.get("entry_reference", 0.0)),
            stop_reference=float(d.get("stop_reference", 0.0)),
            target_reference=float(d.get("target_reference", 0.0)),
            confidence=float(d.get("confidence", 0.0)),
            created_at=d.get("created_at", ""), expires_at=d.get("expires_at", ""),
            analysis_version=d.get("analysis_version", ""),
            dataset_version=d.get("dataset_version", ""),
            correlation_id=d.get("correlation_id", ""),
        )

    def is_expired(self, now: str | None = None) -> bool:
        return (now or utc_now_iso()) > self.expires_at

    def validate(self) -> list[str]:
        errors = []
        if self.direction not in ("BUY", "SELL", "NEUTRAL"):
            errors.append(f"direction {self.direction!r}")
        if self.signal_type not in ("ENTRY", "EXIT", "FILTER", "ALERT", "ANALYSIS"):
            errors.append(f"signal_type {self.signal_type!r}")
        if not (0.0 <= self.confidence <= 1.0):
            errors.append(f"confidence {self.confidence}")
        if not self.signal_id:
            errors.append("signal_id empty")
        return errors


@dataclass(frozen=True)
class ExecutionFeedback:
    """Execution quality feedback published to the Analyzer (TCA input)."""
    execution_id: str
    signal_id: str
    correlation_id: str
    symbol: str
    requested_price: float
    executed_price: float
    volume: float
    fill_count: int
    slippage: float
    spread: float
    latency_ms: float
    created_at: str
    executed_at: str
    status: str
    rejection_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
