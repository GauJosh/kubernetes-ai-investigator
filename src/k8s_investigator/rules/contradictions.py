"""The one Milestone 1 contradiction detector.

A deliberately narrow, well-justified check -- not a general contradiction framework. Fires only
when a container's *current* state (from the same collection pass) is Running while a collected
event reports a pull or crash failure for that pod. This can genuinely happen (the container
recovered between the event being recorded and the object being read) or can indicate
inconsistent evidence; either way, it is recorded as a ContradictionEdge and never silently
resolved by picking one side, per CLAUDE.md principle 4.
"""

from __future__ import annotations

from ..models.evidence import ContradictionEdge, EvidenceItem
from .patterns import AUTH_FAILURE_SUBSTRINGS, DNS_FAILURE_SUBSTRINGS, NOT_FOUND_SUBSTRINGS, matches_any

_FAILURE_EVENT_REASONS = frozenset({"Failed", "BackOff"})


def detect_contradictions(
    pod: EvidenceItem, container_statuses: list[dict], event_items: list[EvidenceItem]
) -> list[ContradictionEdge]:
    edges: list[ContradictionEdge] = []
    for idx, cs in enumerate(container_statuses):
        if "running" not in (cs.get("state") or {}):
            continue
        name = cs.get("name", f"container-{idx}")
        for ev in event_items:
            payload = ev.payload or {}
            message = payload.get("message", "")
            reason = payload.get("reason", "")
            if reason not in _FAILURE_EVENT_REASONS:
                continue
            if not (
                matches_any(message, DNS_FAILURE_SUBSTRINGS)
                or matches_any(message, AUTH_FAILURE_SUBSTRINGS)
                or matches_any(message, NOT_FOUND_SUBSTRINGS)
                or "crashloopbackoff" in message.lower()
            ):
                continue
            edges.append(
                ContradictionEdge(
                    evidence_a=pod.id,
                    evidence_b=ev.id,
                    rule_id="contradiction:running_vs_failure_event",
                    description=(
                        f"Container '{name}' is currently Running, but a collected event "
                        f"(reason={reason!r}) reports a pull/crash failure: {message!r}. This "
                        "may reflect recovery between the event and collection, or "
                        "inconsistent evidence; it is not resolved automatically."
                    ),
                )
            )
    return edges
