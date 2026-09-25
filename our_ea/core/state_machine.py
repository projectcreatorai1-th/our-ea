"""State machine — built FROM STRATEGY_SPEC/state_transition_spec.json.

17 states, 62 declared transitions. Any transition not declared in the spec
raises InvalidTransition (INV-004: explicit transitions only). Every taken
transition is recorded with rule/model/trace provenance and mirrored to the
event journal by the runtime.
"""
from __future__ import annotations

from dataclasses import dataclass

from our_ea.core.identifiers import utc_now_iso


class InvalidTransition(RuntimeError):
    def __init__(self, state: str, event: str):
        self.state = state
        self.event = event
        super().__init__(f"invalid transition: {state} --{event}--> (not declared)")


@dataclass(frozen=True)
class Transition:
    state_from: str
    event: str
    state_to: str
    status: str
    ts: str
    trace_id: str


class SpecStateMachine:
    """Spec-driven state machine; the transition table is data, not code."""

    def __init__(self, state_transition_spec: dict, initial_state: str = "IDLE"):
        self.version = state_transition_spec.get("state_machine_version", "")
        self.states: tuple = tuple(state_transition_spec.get("states", []))
        self._table: dict[tuple[str, str], dict] = {}
        for t in state_transition_spec.get("transitions", []):
            self._table[(t["from"], t["event"])] = t
        if initial_state not in self.states:
            raise ValueError(f"initial state {initial_state!r} not in spec states")
        self._state = initial_state
        self._initial = initial_state
        self.history: list[Transition] = []

    @classmethod
    def from_repository(cls, repo, initial_state: str | None = None) -> "SpecStateMachine":
        init = initial_state
        if init is None:
            # PARAM-STATE-INITIAL (default IDLE)
            for p in repo.parameter_spec.get("parameters", []):
                if p["parameter_id"] == "PARAM-STATE-INITIAL":
                    init = p["value"]
                    break
        return cls(repo.state_transition_spec, initial_state=init or "IDLE")

    # ------------------------------------------------------------------ api
    @property
    def state(self) -> str:
        return self._state

    def can(self, event: str, state: str | None = None) -> bool:
        return ((state or self._state), event) in self._table

    def allowed_events(self, state: str | None = None) -> list[str]:
        s = state or self._state
        return sorted({ev for (f, ev) in self._table if f == s})

    def fire(self, event: str, trace_id: str = "", reason: str = "") -> Transition:
        key = (self._state, event)
        entry = self._table.get(key)
        if entry is None:
            raise InvalidTransition(self._state, event)
        target = entry["to"]
        tr = Transition(self._state, event, target,
                        entry.get("status", "CONFIGURED"),
                        utc_now_iso(), trace_id)
        self._state = target
        self.history.append(tr)
        return tr

    def reset(self) -> None:
        self._state = self._initial
        self.history.clear()

    def snapshot(self) -> dict:
        return {"state": self._state, "version": self.version,
                "transitions_taken": len(self.history)}
