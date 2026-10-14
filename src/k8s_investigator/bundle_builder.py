"""Assemble a frozen EvidenceBundle from the collector's raw CollectionOutcomes.

This is the one place "what the API said" (CollectionOutcome) becomes "what this means for the
investigation" (EvidenceItem, with an explicit EvidenceStatus) -- per
docs/phase-0.8-mvp-blueprint.md §B. A failed or empty collection call never becomes silence: it
becomes a typed, citable EvidenceItem recording exactly what is known.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .canonical import compute_bundle_id, compute_evidence_item_id
from .models.evidence import (
    CollectionOutcome,
    CollectionStatus,
    EvidenceBundle,
    EvidenceItem,
    EvidenceStatus,
)
from .models.refs import ApiCallRef, ObjectRef, TimestampValue
from .timeline import expiry_note, is_plausibly_expired, tag

def _project_sibling_pod_fields(raw_pod: dict[str, Any]) -> dict[str, Any]:
    """R9 follow-up (docs/research/read-only-access-matrix.md §8.3): retain only the fields
    R9's correlation actually needs from a sibling-Pod list entry -- not the full object every
    other call in this project keeps. The investigated Pod's own object (fetched separately)
    is never projected -- only entries from the sibling-list call are."""
    metadata = raw_pod.get("metadata", {})
    return {
        "metadata": {
            "name": metadata.get("name"),
            "namespace": metadata.get("namespace"),
            "uid": metadata.get("uid"),
            "labels": metadata.get("labels"),
            "ownerReferences": metadata.get("ownerReferences"),
            "resourceVersion": metadata.get("resourceVersion"),
        },
        "spec": {"nodeName": raw_pod.get("spec", {}).get("nodeName")},
        "status": {"phase": raw_pod.get("status", {}).get("phase")},
    }


#: Every LIST-shaped call this collector makes, keyed by the final path segment -- its payload
#: is a *list* of raw objects and must be split into one EvidenceItem per entry, unlike every
#: other (single-object) call. (api_group, api_version, kind, apply_expiry_heuristic, projector).
#: Only the events list gets the plausible-expiry heuristic
#: (docs/research/event-timeline-contract.md) -- an empty Node or PersistentVolume list has no
#: TTL-eviction concept to flag. Only the sibling-Pod list gets a projector -- every other list
#: call retains the full raw entry.
_LIST_CALL_SHAPES: dict[str, tuple[str, str, str, bool, Any]] = {
    "events": ("", "v1", "Event", True, None),
    "nodes": ("", "v1", "Node", False, None),
    "persistentvolumes": ("", "v1", "PersistentVolume", False, None),
    "pods": ("", "v1", "Pod", False, _project_sibling_pod_fields),
}


def _list_shape_for_ref(ref: Any) -> tuple[str, str, str, bool, Any] | None:
    if not (isinstance(ref, ApiCallRef) and ref.verb == "list"):
        return None
    last_segment = ref.path.rstrip("/").rsplit("/", 1)[-1]
    return _LIST_CALL_SHAPES.get(last_segment)


def build_bundle(
    target: ObjectRef,
    outcomes: list[CollectionOutcome],
    *,
    collection_started_at: datetime,
    collection_finished_at: datetime,
) -> EvidenceBundle:
    items: list[EvidenceItem] = []
    pod_created_at: TimestampValue | None = None

    # Pass 1: find the Pod's creation timestamp first, if present -- needed by pass 2 for the
    # plausible-expiry check on an empty events result.
    for outcome in outcomes:
        if (
            isinstance(outcome.requested, ObjectRef)
            and outcome.requested.kind == "Pod"
            and outcome.status == CollectionStatus.PRESENT
        ):
            created = (outcome.payload or {}).get("metadata", {}).get("creationTimestamp")
            if created:
                pod_created_at = tag(_parse_timestamp(created), "metadata.creationTimestamp")

    collected_at_tag = tag(collection_finished_at, "collectedAt")

    for outcome in outcomes:
        items.extend(_items_from_outcome(outcome, collected_at_tag, pod_created_at))

    item_ids = [item.id for item in items]
    bundle_id = compute_bundle_id(
        target.model_dump(mode="json"),
        item_ids,
        collection_params={"namespace": target.namespace, "pod_name": target.name},
    )
    return EvidenceBundle(
        id=bundle_id,
        investigation_target=target,
        collected_at_range=(collection_started_at, collection_finished_at),
        items=items,
        queries=outcomes,
        contradictions=[],  # populated later by rules.contradictions, not at build time
    )


def _items_from_outcome(
    outcome: CollectionOutcome,
    collected_at: TimestampValue,
    pod_created_at: TimestampValue | None,
) -> list[EvidenceItem]:
    if outcome.status != CollectionStatus.PRESENT:
        return [_gap_item(outcome, collected_at)]

    ref = outcome.requested
    list_shape = _list_shape_for_ref(ref)
    if list_shape is not None:
        api_group, api_version, kind, apply_expiry_heuristic, projector = list_shape
        entries = outcome.payload or []
        if not entries:
            return [
                _empty_list_item(
                    outcome, collected_at, pod_created_at if apply_expiry_heuristic else None
                )
            ]
        return [
            _list_entry_item(
                projector(entry) if projector else entry,
                api_group, api_version, kind, collected_at,
            )
            for entry in entries
        ]

    # Single-object GET (Pod/ReplicaSet/Deployment/ServiceAccount) or a bounded log fetch
    # (ApiCallRef) -- exactly one item either way.
    item_id = compute_evidence_item_id(ref.model_dump(mode="json"), outcome.payload)
    return [
        EvidenceItem(
            id=item_id,
            source_ref=ref,
            collected_at=collected_at,
            payload=outcome.payload,
            status=EvidenceStatus.PRESENT,
        )
    ]


def _gap_item(outcome: CollectionOutcome, collected_at: TimestampValue) -> EvidenceItem:
    status = (
        EvidenceStatus.NOT_FOUND
        if outcome.status == CollectionStatus.NOT_FOUND
        else EvidenceStatus.UNAVAILABLE
    )
    item_id = compute_evidence_item_id(outcome.requested.model_dump(mode="json"), None)
    return EvidenceItem(
        id=item_id,
        source_ref=outcome.requested,
        collected_at=collected_at,
        payload=None,
        status=status,
        error_detail=outcome.error_detail,
    )


def _empty_list_item(
    outcome: CollectionOutcome,
    collected_at: TimestampValue,
    pod_created_at: TimestampValue | None,
) -> EvidenceItem:
    """A LIST call that succeeded but found nothing -- a citable absence, not silence."""
    item_id = compute_evidence_item_id(outcome.requested.model_dump(mode="json"), None)
    note: str | None = None
    status = EvidenceStatus.NOT_FOUND
    if pod_created_at is not None and is_plausibly_expired(pod_created_at, collected_at):
        status = EvidenceStatus.EXPIRED
        note = expiry_note(pod_created_at, collected_at)
    return EvidenceItem(
        id=item_id,
        source_ref=outcome.requested,
        collected_at=collected_at,
        payload=None,
        status=status,
        expiry_note=note,
    )


def _list_entry_item(
    entry: dict[str, Any], api_group: str, api_version: str, kind: str, collected_at: TimestampValue
) -> EvidenceItem:
    """One EvidenceItem for one entry of a LIST call's payload -- an Event, a Node, or a
    PersistentVolume. ``namespace`` is None for the cluster-scoped kinds (Node,
    PersistentVolume), exactly matching their real, namespace-less metadata."""
    metadata = entry.get("metadata", {})
    ref = ObjectRef(
        api_group=api_group,
        api_version=api_version,
        kind=kind,
        namespace=metadata.get("namespace"),
        name=metadata.get("name", ""),
        resource_version=metadata.get("resourceVersion"),
    )
    item_id = compute_evidence_item_id(ref.model_dump(mode="json"), entry)
    return EvidenceItem(
        id=item_id,
        source_ref=ref,
        collected_at=collected_at,
        payload=entry,
        status=EvidenceStatus.PRESENT,
    )


def _parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
