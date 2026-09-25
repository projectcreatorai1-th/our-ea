"""EA gateway client — handshake, heartbeat, event publish, signal ingest,
execution feedback, OS command intake, reconnect (Phase 26-30, EA side).

Safety:
    * Contract handshake fails closed on major/minor mismatch.
    * Dedup by event_id / signal_id (idempotency).
    * Missed heartbeats degrade -> reconnect -> state snapshot re-request.
    * A signal is RESEARCH INPUT: client validates schema, never converts a
      signal into an order; acceptance/rejection is decided by the runtime.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from our_ea.gateway.contracts import (
    EA_CAPABILITIES, EA_CLIENT_ID, EA_COMMANDS, EA_PUBLISHABLE_EVENTS,
    EA_SUPPORTED_FEATURES, EVENT_SCHEMA_VERSION, GATEWAY_CONTRACT_VERSION,
    EventEnvelope, ExecutionFeedback, GatewaySignal,
    canonical_hash, new_id, utc_now_iso,
)
from our_ea.gateway.transport import LoopbackTransport, Transport


class ContractRejected(RuntimeError):
    pass


class ConnectionState:
    OFFLINE = "OFFLINE"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"


class EAGatewayClient:
    def __init__(self, transport: Transport | None = None,
                 client_id: str = EA_CLIENT_ID,
                 heartbeat_interval: float = 30.0,
                 heartbeat_timeout: float = 10.0):
        self.transport = transport or LoopbackTransport()
        self.client_id = client_id
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.state = ConnectionState.OFFLINE
        self.contract: dict = {}
        self.session_id = ""
        self.sequence = 0
        self._seen_event_ids: set[str] = set()
        self._seen_signal_ids: set[str] = set()
        self._last_heartbeat_ack = ""
        self.audit_trail: list[dict] = []
        self.signal_handlers: list = []
        self.command_handlers: list = []

    # --------------------------------------------------------------- low io
    def _send(self, msg: dict) -> dict | None:
        self.transport.send(json.dumps(msg).encode("utf-8"))
        raw = self.transport.recv(timeout=self.heartbeat_timeout)
        if raw is None:
            return None
        return json.loads(raw.decode("utf-8"))

    def _audit(self, action: str, detail: dict) -> None:
        self.audit_trail.append({"ts": utc_now_iso(), "action": action,
                                 **detail})

    # ------------------------------------------------------------ lifecycle
    def connect(self, endpoint: str = "loopback://test") -> dict:
        self.state = ConnectionState.CONNECTING
        try:
            self.transport.connect(endpoint)
        except Exception as exc:
            self.state = ConnectionState.ERROR
            raise ConnectionError(f"gateway connect failed: {exc}") from exc
        resp = self._send({
            "type": "CONTRACT_REQUEST",
            "protocol_version": "1.0",
            "contract_version": GATEWAY_CONTRACT_VERSION,
            "client_version": self.client_id,
            "client_capabilities": list(EA_CAPABILITIES),
            "supported_features": list(EA_SUPPORTED_FEATURES),
            "accepted_commands": list(EA_COMMANDS),
        })
        if resp is None or resp.get("type") != "CONTRACT_RESPONSE":
            self.state = ConnectionState.ERROR
            raise ContractRejected("no CONTRACT_RESPONSE from gateway")
        gw_version = str(resp.get("contract_version", ""))
        if not gw_version.startswith(GATEWAY_CONTRACT_VERSION.rsplit(".", 1)[0]):
            self.state = ConnectionState.ERROR
            raise ContractRejected(
                f"gateway contract {gw_version} incompatible with "
                f"{GATEWAY_CONTRACT_VERSION}")
        if not resp.get("accepted"):
            self.state = ConnectionState.ERROR
            raise ContractRejected("gateway rejected contract")
        self.contract = resp
        self.session_id = new_id("SESS")
        self.state = ConnectionState.CONNECTED
        self._audit("CONNECTED", {"session_id": self.session_id,
                                  "gateway_version": resp.get("gateway_version")})
        return resp

    def disconnect(self) -> None:
        self.transport.disconnect()
        self.state = ConnectionState.OFFLINE
        self._audit("DISCONNECTED", {})

    def reconnect(self) -> dict:
        self.state = ConnectionState.RECONNECTING
        self.disconnect()
        return self.connect()

    def send_heartbeat(self) -> bool:
        resp = self._send({"type": "HEARTBEAT", "client_id": self.client_id,
                           "timestamp": utc_now_iso()})
        ok = bool(resp and resp.get("type") == "HEARTBEAT_ACK")
        if ok:
            self._last_heartbeat_ack = utc_now_iso()
            self.state = ConnectionState.CONNECTED
        else:
            self.state = ConnectionState.DEGRADED
        return ok

    # ---------------------------------------------------------------- events
    def publish_event(self, event_type: str, payload: dict,
                      correlation_id: str = "") -> dict:
        if event_type not in EA_PUBLISHABLE_EVENTS:
            raise ValueError(f"EA cannot publish {event_type!r}")
        if not correlation_id:
            correlation_id = new_id("COR")
        self.sequence += 1
        env = EventEnvelope(
            event_id=new_id("EVT"), event_type=event_type,
            schema_version=EVENT_SCHEMA_VERSION, created_at=utc_now_iso(),
            source=self.client_id, target="1144-GATEWAY",
            sequence=self.sequence, correlation_id=correlation_id,
            payload=payload,
        )
        resp = self._send({"type": "EVENT_PUBLISH", "event": env.to_dict()})
        self._audit("EVENT_PUBLISHED", {
            "event_id": env.event_id, "event_type": event_type})
        return {"event": env.to_dict(), "ack": resp or {}}

    def publish_execution_feedback(self, fb: ExecutionFeedback) -> dict:
        resp = self._send({"type": "EXECUTION_FEEDBACK",
                           "feedback": fb.to_dict()})
        self._audit("EXECUTION_FEEDBACK", {"execution_id": fb.execution_id,
                                           "status": fb.status})
        return resp or {}

    # ------------------------------------------------------------- inbound
    def poll(self) -> list[dict]:
        """Drain inbound messages (signals, events, commands). Dedup by id."""
        out: list[dict] = []
        deadline_loops = 8
        for _ in range(deadline_loops):
            raw = self.transport.recv(timeout=0.05)
            if raw is None:
                break
            msg = json.loads(raw.decode("utf-8"))
            if not self._accept_inbound(msg):
                continue
            out.append(msg)
            self._dispatch(msg)
        return out

    def _accept_inbound(self, msg: dict) -> bool:
        mtype = msg.get("type", "")
        eid = msg.get("event", {}).get("event_id") if mtype == "EVENT_PUBLISH" \
            else msg.get("signal_id")
        if mtype == "SIGNAL_PUBLISH":
            sid = msg.get("signal_id", "")
            if sid and sid in self._seen_signal_ids:
                self._audit("DUPLICATE_SIGNAL_DROPPED", {"signal_id": sid})
                return False
            if sid:
                self._seen_signal_ids.add(sid)
            return True
        if eid:
            if eid in self._seen_event_ids:
                self._audit("DUPLICATE_EVENT_DROPPED", {"event_id": eid})
                return False
            self._seen_event_ids.add(eid)
            return True
        return mtype in ("COMMAND", "STATE_SNAPSHOT")

    def _dispatch(self, msg: dict) -> None:
        mtype = msg.get("type", "")
        if mtype == "SIGNAL_PUBLISH":
            for h in self.signal_handlers:
                h(GatewaySignal.from_dict(msg))
        elif mtype == "COMMAND":
            for h in self.command_handlers:
                h(msg)

    # ------------------------------------------------------------- status
    def status(self) -> dict:
        return {
            "client_id": self.client_id,
            "state": self.state,
            "session_id": self.session_id,
            "contract_version": self.contract.get("contract_version", ""),
            "gateway_version": self.contract.get("gateway_version", ""),
            "sequence": self.sequence,
            "last_heartbeat_ack": self._last_heartbeat_ack,
            "audit_entries": len(self.audit_trail),
        }
