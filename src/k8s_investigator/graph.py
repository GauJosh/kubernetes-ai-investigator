"""Structural relationship resolution.

Pure function of the bundle -- no I/O, and per docs/research/event-timeline-contract.md §6,
no timestamp is ever used to infer a relationship here. Every edge below comes from a
structural field (ownerReferences, serviceAccountName), never from "these two things happened
around the same time."

Deliberately small: Milestone 1 has no node-candidacy graph (that is Milestone 2's concern, once
Node/PersistentVolume access is approved) -- this module only resolves the owner chain and the
ServiceAccount link the current rule bank (R1-R4) and report actually use.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models.evidence import EvidenceBundle, EvidenceItem
from .models.refs import ApiCallRef, ObjectRef


@dataclass(frozen=True)
class RelationshipFacts:
    """The bundle's items, organized by role, plus the structural edges resolved from them."""

    pod_item: EvidenceItem
    event_items: list[EvidenceItem] = field(default_factory=list)
    log_items_by_container: dict[str, EvidenceItem] = field(default_factory=dict)
    replica_set_item: EvidenceItem | None = None
    deployment_item: EvidenceItem | None = None
    service_account_item: EvidenceItem | None = None


def _ref_kind(item: EvidenceItem) -> str | None:
    return item.source_ref.kind if isinstance(item.source_ref, ObjectRef) else None


def resolve_relationships(bundle: EvidenceBundle) -> RelationshipFacts:
    """Classify every PRESENT item in the bundle by role. Raises if no Pod item is present --
    every investigation in this milestone is pod-centric by definition."""
    pod_item: EvidenceItem | None = None
    events: list[EvidenceItem] = []
    logs: dict[str, EvidenceItem] = {}
    rs_item: EvidenceItem | None = None
    deploy_item: EvidenceItem | None = None
    sa_item: EvidenceItem | None = None

    for item in bundle.items:
        kind = _ref_kind(item)
        if kind == "Pod":
            pod_item = item
        elif kind == "Event":
            events.append(item)
        elif kind == "ReplicaSet":
            rs_item = item
        elif kind == "Deployment":
            deploy_item = item
        elif kind == "ServiceAccount":
            sa_item = item
        elif isinstance(item.source_ref, ApiCallRef) and item.source_ref.path.endswith("/log"):
            container = item.source_ref.params.get("container", "")
            logs[container] = item

    if pod_item is None:
        raise ValueError(
            "RelationshipFacts requires a Pod EvidenceItem in the bundle -- "
            "every Milestone 1 investigation is pod-centric."
        )

    return RelationshipFacts(
        pod_item=pod_item,
        event_items=events,
        log_items_by_container=logs,
        replica_set_item=rs_item,
        deployment_item=deploy_item,
        service_account_item=sa_item,
    )
