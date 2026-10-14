"""Collector output -> typed EvidenceBundle, including partial failures and expiry flagging."""

from __future__ import annotations

from k8s_investigator.models.evidence import EvidenceStatus
from k8s_investigator.models.refs import ApiCallRef, ObjectRef
from k8s_investigator.validation import validate_bundle_integrity

from .conftest import collect_and_build
from .fixtures import scenarios


def test_healthy_pod_bundle_has_all_present_items():
    bundle = collect_and_build(scenarios.healthy_pod())
    statuses = {item.status for item in bundle.items}
    assert statuses == {EvidenceStatus.PRESENT}
    validate_bundle_integrity(bundle)  # every item's id matches its own content


def test_empty_events_recent_pod_is_not_found_not_expired():
    bundle = collect_and_build(scenarios.events_recent_absence())
    event_gaps = [i for i in bundle.items if isinstance(i.source_ref, ApiCallRef) and "events" in i.source_ref.path]
    assert len(event_gaps) == 1
    assert event_gaps[0].status == EvidenceStatus.NOT_FOUND
    assert event_gaps[0].expiry_note is None


def test_empty_events_old_pod_is_flagged_expired():
    bundle = collect_and_build(scenarios.events_plausibly_expired())
    event_gaps = [i for i in bundle.items if isinstance(i.source_ref, ApiCallRef) and "events" in i.source_ref.path]
    assert len(event_gaps) == 1
    assert event_gaps[0].status == EvidenceStatus.EXPIRED
    assert event_gaps[0].expiry_note is not None
    assert "may reflect" in event_gaps[0].expiry_note  # hedged, not asserted


def test_service_account_unavailable_recorded_as_gap_not_silence():
    bundle = collect_and_build(scenarios.r3_service_account_unavailable())
    sa_items = [i for i in bundle.items if isinstance(i.source_ref, ObjectRef) and i.source_ref.kind == "ServiceAccount"]
    assert len(sa_items) == 1
    assert sa_items[0].status == EvidenceStatus.UNAVAILABLE
    assert sa_items[0].error_detail is not None
    assert "Forbidden" in sa_items[0].error_detail


def test_bundle_id_reproducible_for_two_builds_of_same_outcomes():
    client_a = scenarios.r1_dns_failure()
    client_b = scenarios.r1_dns_failure()  # a fresh, independently-constructed but equivalent client
    bundle_a = collect_and_build(client_a)
    bundle_b = collect_and_build(client_b)
    assert bundle_a.id == bundle_b.id
    assert {i.id for i in bundle_a.items} == {i.id for i in bundle_b.items}


def test_log_requests_are_always_bounded():
    bundle = collect_and_build(scenarios.r4_log_pattern_match())
    log_items = [i for i in bundle.items if isinstance(i.source_ref, ApiCallRef) and i.source_ref.path.endswith("/log")]
    assert len(log_items) == 1
    params = log_items[0].source_ref.params
    assert isinstance(params.get("tailLines"), int) and params["tailLines"] > 0
    assert isinstance(params.get("sinceSeconds"), int) and params["sinceSeconds"] > 0
