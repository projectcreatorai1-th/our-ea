"""Partial close engine — EXISTS verified, rules UNKNOWN.

R-PARTIAL-EXISTS (E025, VERIFIED): V1.68 performs intra-position partial
closes (492 observed events).
R-PARTIAL-TRIGGER / R-PARTIAL-VOLUME: UNKNOWN -> runtime MUST emit
MODEL_UNCERTAINTY and skip; guessing is forbidden (INV-008).
Parameter PARAM-PARTIAL-ENABLED defaults to false (OUR_EA_POLICY).

The engine intentionally provides NO decision functions. Any attempt to ask
it for a decision raises PartialDecisionUnavailable so callers record the
uncertainty event and continue deterministically.
"""
from __future__ import annotations

from our_ea.core.rules import ModelUncertaintyError


class PartialDecisionUnavailable(ModelUncertaintyError):
    def __init__(self):
        super().__init__(
            "R-PARTIAL-TRIGGER",
            "partial close trigger/volume",
            "UNKNOWN in frozen model (E025); disabled by policy and never guessed")


class PartialEngine:
    """No-decision engine: documents UNKNOWN and refuses to decide."""

    exists_verified = True   # R-PARTIAL-EXISTS (E025)
    observed_events = 492

    def decide_trigger(self, *args, **kwargs) -> None:
        raise PartialDecisionUnavailable()

    def decide_volume(self, *args, **kwargs) -> None:
        raise PartialDecisionUnavailable()

    def decide_level(self, *args, **kwargs) -> None:
        raise PartialDecisionUnavailable()
