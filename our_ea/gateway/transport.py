"""Gateway transports — pluggable, mirroring the Analyzer Transport port.

    Transport (ABC): connect/disconnect/send/recv/is_alive
    LoopbackTransport: in-process gateway simulation for tests (speaks the
        CONTRACT_REQUEST/RESPONSE + ACK protocol; no real server implied).
    FileTransport: JSONL inter-process bridge for integration testing with
        the ecosystem without pretending a gateway server exists.

The production WebSocket transport ships when the 1144 Gateway server is
deployed; until then real endpoints are honestly ENVIRONMENT_BLOCKED.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque

from our_ea.gateway.contracts import (
    GATEWAY_CONTRACT_VERSION, EVENT_SCHEMA_VERSION, utc_now_iso, new_id)


class Transport:
    def connect(self, endpoint: str) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    def send(self, data: bytes) -> None:
        raise NotImplementedError

    def recv(self, timeout: float = 5.0) -> bytes | None:
        raise NotImplementedError

    def is_alive(self) -> bool:
        raise NotImplementedError


class LoopbackTransport(Transport):
    """Test transport simulating gateway-side responses."""

    def __init__(self, contract_version: str = GATEWAY_CONTRACT_VERSION):
        self.contract_version = contract_version
        self._inbox: deque[bytes] = deque()
        self._connected = False
        self.sent: list[dict] = []

    def connect(self, endpoint: str) -> None:
        if not endpoint:
            raise ConnectionError("endpoint required")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_alive(self) -> bool:
        return self._connected

    def send(self, data: bytes) -> None:
        msg = json.loads(data.decode("utf-8"))
        self.sent.append(msg)
        mtype = msg.get("type", "")
        if mtype == "CONTRACT_REQUEST":
            if not self.contract_version.startswith("1."):
                self._inbox.append(json.dumps({
                    "type": "CONTRACT_REJECTED",
                    "reason": f"gateway contract {self.contract_version}",
                }).encode())
                return
            self._inbox.append(json.dumps({
                "type": "CONTRACT_RESPONSE",
                "gateway_version": "1144-GATEWAY-SIM",
                "contract_version": self.contract_version,
                "accepted": True,
            }).encode())
        elif mtype == "HEARTBEAT":
            self._inbox.append(json.dumps({
                "type": "HEARTBEAT_ACK", "timestamp": utc_now_iso(),
            }).encode())
        elif mtype == "EVENT_PUBLISH":
            self._inbox.append(json.dumps({
                "type": "EVENT_ACK",
                "event_id": msg.get("event", {}).get("event_id", ""),
                "accepted": True,
                "timestamp": utc_now_iso(),
            }).encode())
        elif mtype == "EXECUTION_FEEDBACK":
            self._inbox.append(json.dumps({
                "type": "FEEDBACK_ACK",
                "execution_id": msg.get("feedback", {}).get("execution_id", ""),
                "accepted": True,
            }).encode())
        elif mtype == "STATE_REQUEST":
            self._inbox.append(json.dumps({
                "type": "STATE_SNAPSHOT",
                "state": {"ea_registered": True},
                "sequence": int(time.time() * 1000) % 10**9,
                "timestamp": utc_now_iso(),
            }).encode())

    def recv(self, timeout: float = 5.0) -> bytes | None:
        if self._inbox:
            return self._inbox.popleft()
        return None

    def inject(self, msg: dict) -> None:
        """Test helper: gateway -> EA message (signal, command, event)."""
        self._inbox.append(json.dumps(msg).encode())


class FileTransport(Transport):
    """JSONL file bridge: <dir>/ea_to_gateway.jsonl and gateway_to_ea.jsonl.

    Integration bridge only — the real gateway server remains the
    production path. Polls inbound file for messages.
    """

    def __init__(self, poll_interval: float = 0.05):
        self.poll_interval = poll_interval
        self._dir = ""
        self._out_path = ""
        self._in_path = ""
        self._in_pos = 0
        self._connected = False

    def connect(self, endpoint: str) -> None:
        self._dir = endpoint
        os.makedirs(self._dir, exist_ok=True)
        self._out_path = os.path.join(self._dir, "ea_to_gateway.jsonl")
        self._in_path = os.path.join(self._dir, "gateway_to_ea.jsonl")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_alive(self) -> bool:
        return self._connected and os.path.isdir(self._dir)

    def send(self, data: bytes) -> None:
        with open(self._out_path, "a", encoding="utf-8") as fh:
            fh.write(data.decode("utf-8") + "\n")

    def recv(self, timeout: float = 5.0) -> bytes | None:
        if not os.path.isfile(self._in_path):
            return None
        with open(self._in_path, "r", encoding="utf-8") as fh:
            fh.seek(self._in_pos)
            line = fh.readline()
            self._in_pos = fh.tell()
        if not line.strip():
            return None
        return line.strip().encode("utf-8")
