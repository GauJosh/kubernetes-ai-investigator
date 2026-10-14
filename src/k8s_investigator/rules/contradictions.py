"""Contradiction detectors -- deliberately narrow, well-justified checks, not a general
contradiction framework. Each fires on one specific, named disagreement between two pieces of
collected evidence and is recorded as a ContradictionEdge, never silently resolved by picking
one side, per CLAUDE.md principle 4.
"""

from __future__ import annotations

from ..graph import schedulable_nodes
from ..models.evidence import ContradictionEdge, EvidenceItem
from .patterns import (
    AUTH_FAILURE_SUBSTRINGS,
    DNS_FAILURE_SUBSTRINGS,
    NOT_FOUND_SUBSTRINGS,
    matches_any,
    parse_cpu_quantity,
    parse_memory_quantity,
)

_FAILURE_EVENT_REASONS = frozenset({"Failed", "BackOff"})


def detect_scheduling_contradictions(
    pod: EvidenceItem, event: EvidenceItem, node_items: list[EvidenceItem]
) -> list[ContradictionEdge]:
    """R6's companion check: the scheduler's event claims insufficient cpu/memory, but a known
    schedulable node's own allocatable capacity is actually enough for the Pod's *own stated
    request* -- a genuine disagreement between the event (which may be stale, or reflect other
    pods' usage this project does not see) and the Node object (a fresher read, but also
    incomplete -- allocatable is capacity, not availability). Neither side is assumed correct;
    both are recorded. Compares against the Pod's actual request, not a guessed threshold --
    a fixed threshold would itself produce false contradictions whenever a node is merely
    larger than some arbitrary cutoff but still smaller than what was actually requested."""
    message = (event.payload or {}).get("message", "")
    lowered = message.lower()
    edges: list[ContradictionEdge] = []

    if "insufficient cpu" not in lowered and "insufficient memory" not in lowered:
        return edges

    resource = "cpu" if "insufficient cpu" in lowered else "memory"
    parser = parse_cpu_quantity if resource == "cpu" else parse_memory_quantity

    requested_raw = None
    for container in (pod.payload or {}).get("spec", {}).get("containers") or []:
        requested_raw = (container.get("resources", {}).get("requests") or {}).get(resource)
        if requested_raw is not None:
            break
    requested = parser(requested_raw)
    if requested is None:
        return edges  # nothing to compare against -- no contradiction can be established

    # Only schedulable nodes are relevant -- a cordoned node's allocatable capacity is not a
    # contradiction with "insufficient capacity among nodes the scheduler could actually use."
    for node in schedulable_nodes(node_items):
        allocatable_raw = (node.payload or {}).get("status", {}).get("allocatable", {}).get(resource)
        allocatable = parser(allocatable_raw)
        if allocatable is not None and allocatable >= requested:
            node_name = getattr(node.source_ref, "name", "?")
            edges.append(
                ContradictionEdge(
                    evidence_a=event.id,
                    evidence_b=node.id,
                    rule_id="contradiction:insufficient_capacity_vs_node_allocatable",
                    description=(
                        f"Event reports insufficient {resource} ({message!r}), but node "
                        f"'{node_name}' shows allocatable {resource}={allocatable_raw}, which "
                        f"is at least the Pod's requested {resource}={requested_raw}. This may "
                        "reflect other pods already consuming that capacity (not visible to "
                        "this project) or stale event data; it is not resolved automatically."
                    ),
                )
            )
    return edges


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
