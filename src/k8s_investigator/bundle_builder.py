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

def _is_events_list_call(ref: Any) -> bool:
    """The events LIST call is an ApiCallRef (see collector/collect.py), not an ObjectRef --
    its payload is a *list* of raw Event dicts and must be split into one item per event,
    unlike every other (single-object) call this collector makes."""
    return isinstance(ref, ApiCallRef) and ref.verb == "list" and ref.path.rstrip("/").endswith("/events")


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
    if _is_events_list_call(ref):
        events = outcome.payload or []
        if not events:
            return [_empty_list_item(outcome, collected_at, pod_created_at)]
        return [_event_item(event, collected_at) for event in events]

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


def _event_item(event: dict[str, Any], collected_at: TimestampValue) -> EvidenceItem:
    metadata = event.get("metadata", {})
    ref = ObjectRef(
        api_group="",
        api_version="v1",
        kind="Event",
        namespace=metadata.get("namespace"),
        name=metadata.get("name", ""),
        resource_version=metadata.get("resourceVersion"),
    )
    item_id = compute_evidence_item_id(ref.model_dump(mode="json"), event)
    return EvidenceItem(
        id=item_id,
        source_ref=ref,
        collected_at=collected_at,
        payload=event,
        status=EvidenceStatus.PRESENT,
    )


def _parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
