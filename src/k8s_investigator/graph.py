"""Structural relationship resolution.

Pure function of the bundle -- no I/O, and per docs/research/event-timeline-contract.md §6,
no timestamp is ever used to infer a relationship here. Every edge below comes from a
structural field (ownerReferences, serviceAccountName), never from "these two things happened
around the same time."

Milestone 1 resolved only the owner chain and the ServiceAccount link. Milestone 2 adds Node,
PersistentVolume, and PersistentVolumeClaim classification for the Scheduling-fault rules
(R5-R10) -- still pure, still no timestamps, and still just classification: whether a given
node/PV/PVC *supports* a rule's claim is R5-R10's own job (rules/engine.py), not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
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

    # Milestone 2 additions -- populated only when docs/research/read-only-access-matrix.md
    # §7.2's scheduling-blocked gate fired during collection; empty/None otherwise.
    node_items: list[EvidenceItem] = field(default_factory=list)
    nodes_gap: EvidenceItem | None = None  # set when the Node list call itself failed or was empty
    persistent_volume_items: list[EvidenceItem] = field(default_factory=list)
    persistent_volumes_gap: EvidenceItem | None = None
    persistent_volume_claims_by_name: dict[str, EvidenceItem] = field(default_factory=dict)
    persistent_volume_claim_gaps: list[EvidenceItem] = field(default_factory=list)


def _ref_kind(item: EvidenceItem) -> str | None:
    return item.source_ref.kind if isinstance(item.source_ref, ObjectRef) else None


def schedulable_nodes(node_items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Nodes not cordoned (``spec.unschedulable`` is not true) -- the relevant candidate pool
    for R6-R9's label/taint/capacity comparisons. R5 (cordon itself) uses the full list, not
    this filtered one -- cordon status is exactly what R5 is checking."""
    return [n for n in node_items if not (n.payload or {}).get("spec", {}).get("unschedulable")]


def resolve_relationships(bundle: EvidenceBundle) -> RelationshipFacts:
    """Classify every item in the bundle by role. Raises if no Pod item is present -- every
    investigation in this project is pod-centric by definition."""
    pod_item: EvidenceItem | None = None
    events: list[EvidenceItem] = []
    logs: dict[str, EvidenceItem] = {}
    rs_item: EvidenceItem | None = None
    deploy_item: EvidenceItem | None = None
    sa_item: EvidenceItem | None = None
    nodes: list[EvidenceItem] = []
    nodes_gap: EvidenceItem | None = None
    pvs: list[EvidenceItem] = []
    pvs_gap: EvidenceItem | None = None
    pvcs: dict[str, EvidenceItem] = {}
    pvc_gaps: list[EvidenceItem] = []

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
        elif kind == "Node":
            nodes.append(item)  # per-entry items from the list are always PRESENT, see bundle_builder.py
        elif kind == "PersistentVolume":
            pvs.append(item)
        elif kind == "PersistentVolumeClaim":
            if item.status == EvidenceStatus.PRESENT:
                pvcs[item.source_ref.name] = item
            else:
                pvc_gaps.append(item)
        elif isinstance(item.source_ref, ApiCallRef) and item.source_ref.path.endswith("/log"):
            container = item.source_ref.params.get("container", "")
            logs[container] = item
        elif isinstance(item.source_ref, ApiCallRef) and item.source_ref.path.rstrip("/").endswith("/nodes"):
            nodes_gap = item  # the list call itself failed or returned zero nodes
        elif (
            isinstance(item.source_ref, ApiCallRef)
            and item.source_ref.path.rstrip("/").endswith("/persistentvolumes")
        ):
            pvs_gap = item

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
        node_items=nodes,
        nodes_gap=nodes_gap,
        persistent_volume_items=pvs,
        persistent_volumes_gap=pvs_gap,
        persistent_volume_claims_by_name=pvcs,
        persistent_volume_claim_gaps=pvc_gaps,
    )
