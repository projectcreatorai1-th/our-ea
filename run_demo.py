#!/usr/bin/env python3
"""OUR EA demo run — paper-mode E2E with loopback gateway.

Produces verifiable artifacts under runs/<name>/:
    events.jsonl   — hash-chained event journal (Analyzer event feed)
    audit.jsonl    — hash-chained audit records
    runtime_state.json — crash-safe checkpoint
    deployment_manifest.json
    summary.json   — run summary (health, decisions, conformance pointer)
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from our_ea.core.identifiers import default_analyzer_root  # noqa: E402
from our_ea.gateway.client import EAGatewayClient           # noqa: E402
from our_ea.gateway.transport import LoopbackTransport      # noqa: E402
from our_ea.runtime.service import RuntimeService           # noqa: E402
from our_ea.spec.repository import SpecRepository           # noqa: E402


def feed(svc):
    """Deterministic price path: open, add grids, close basket, reopen."""
    ticks = [
        (4000.0, 3999.7, "2026-09-25T10:00:00+00:00"),
        (3994.0, 3993.7, "2026-09-25T10:01:00+00:00"),
        (3988.5, 3988.2, "2026-09-25T10:02:00+00:00"),
        (4001.5, 4001.2, "2026-09-25T10:03:00+00:00"),
        (4003.0, 4002.7, "2026-09-25T10:04:00+00:00"),
        (3997.0, 3996.7, "2026-09-25T10:05:00+00:00"),
    ]
    n = 0
    for buy, sell, ts in ticks:
        n += len(svc.on_tick(buy, sell, ts))
    return n


def main() -> int:
    repo = SpecRepository(default_analyzer_root())
    report = repo.verify()
    name = f"runs/demo_{report['spec_version']}"
    out_dir = os.path.join(HERE, name)
    os.makedirs(out_dir, exist_ok=True)

    gw = EAGatewayClient(transport=LoopbackTransport())
    gw.connect("loopback://test")
    svc = RuntimeService(repo, out_dir, account_id="DEMO-001",
                         symbol="GOLDmicro", gateway=gw)
    svc.handle_command("REGISTER")
    svc.handle_command("INITIALIZE")
    svc.promote_mode("PAPER")
    svc.handle_command("START", {"balance": 500.0})

    decisions = feed(svc)
    recon = svc.handle_command("RECONCILE")
    status = svc.handle_command("REQUEST_STATUS")
    health = svc.handle_command("REQUEST_HEALTH")

    manifest = svc.build_deployment_manifest()
    with open(os.path.join(out_dir, "deployment_manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    summary = {
        "run_id": svc.run_id,
        "mode": svc.mode,
        "decisions": decisions,
        "journal": svc.journal.snapshot(),
        "audit": svc.audit.snapshot(),
        "account": svc.adapter.account_snapshot(),
        "reconciliation": recon.to_dict(),
        "status": status.detail,
        "health": health.detail,
        "gateway": gw.status(),
        "deployment_hash": manifest["deployment_hash"],
        "spec": report,
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)

    print(json.dumps({
        "run_id": svc.run_id,
        "mode": svc.mode,
        "decisions": decisions,
        "events": summary["journal"]["event_count"],
        "chain_valid": summary["journal"]["chain_valid"],
        "audit_chain_valid": summary["audit"]["chain_valid"],
        "balance": summary["account"]["balance"],
        "health": summary["health"]["dimensions"],
        "artifacts": name,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
