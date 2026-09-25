"""OUR EA — Trading Runtime / Execution Component.

Architecture authority (ea_build_contract.json v1.0.0):
    SNIPER -> Strategy Spec -> EA Build Contract -> OUR EA -> 1144 Gateway -> MT5

    SNIPER   = Research/Backtest/Evidence/Signal authority (external project)
    Gateway  = Transport/Event bus (external, no server deployed yet)
    OUR_EA   = Strategy enforcement / Risk / Execution authority (this project)
    MT5      = Trading terminal (via Gateway bridge only)

Boundary rules enforced by tests/test_boundary.py:
    * No import of Analyzer runtime code — only frozen JSON artifacts
      (STRATEGY_SPEC/*.json, evidence model, replay dataset) read via
      our_ea.spec.repository, with SHA256 verification.
    * LIVE execution permanently locked (human decision required).
    * UNKNOWN model behavior -> MODEL_UNCERTAINTY + safe state, never guessed.
"""
from __future__ import annotations

RUNTIME_ID = "OUR-EA"
RUNTIME_VERSION = "1.0.0"
RUNTIME_NAME = "OUR EA — SNIPER V1.68 Strategy Runtime"

# Strategy identity (must match STRATEGY_SPEC/strategy_spec.json)
STRATEGY_ID = "SNIPER-V168"
STRATEGY_SPEC_VERSION = "1.0.0"
MODEL_ID = "V1.68-EVIDENCE-MODEL-v1.0"

# Safety barrier (INV-007): LIVE permanently locked until explicit human decision.
LIVE_LOCKED = True

__version__ = RUNTIME_VERSION
