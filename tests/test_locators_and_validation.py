"""Evidence-locator resolution and finding validation -- correction 2 of revision 2.

Covers: a valid locator resolving correctly, an unknown evidence id, an out-of-bounds line
range, a quote that no longer matches the bundle, and the STALE EvidenceStatus value (not
produced by Milestone 1's collector, exercised here as a hand-built fixture)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from k8s_investigator.errors import (
    EvidenceLocatorOutOfBoundsError,
    EvidenceQuoteMismatchError,
    EvidenceReferenceError,
)
from k8s_investigator.models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.findings import Finding, FindingKind
from k8s_investigator.models.locators import EvidenceLocator, FieldPath, LineRange
from k8s_investigator.models.refs import ApiCallRef, ObjectRef, TimestampValue
from k8s_investigator.rules.base import field_locator, line_locator
from k8s_investigator.validation import resolve_pointer, validate_finding_references


def _ts() -> TimestampValue:
    return TimestampValue(value=datetime.now(timezone.utc), source_field="collectedAt")


def _pod_item() -> EvidenceItem:
    ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    payload = {"status": {"containerStatuses": [{"state": {"waiting": {"reason": "ErrImagePull"}}}]}}
    return EvidenceItem(id="item-pod", source_ref=ref, collected_at=_ts(), payload=payload, status=EvidenceStatus.PRESENT)


def _log_item() -> EvidenceItem:
    ref = ApiCallRef(verb="get", path="/pods/p/log", params={"container": "server"})
    payload = "line0\nline1 permission denied here\nline2\n"
    return EvidenceItem(id="item-log", source_ref=ref, collected_at=_ts(), payload=payload, status=EvidenceStatus.PRESENT)


def _bundle(*items: EvidenceItem) -> EvidenceBundle:
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    return EvidenceBundle(
        id="bundle-1",
        investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)),
        items=list(items),
    )


def test_field_path_resolves_correctly():
    pod = _pod_item()
    resolved = resolve_pointer(pod.payload, FieldPath(json_pointer="/status/containerStatuses/0/state/waiting/reason"))
    assert resolved == "ErrImagePull"


def test_line_range_resolves_correctly():
    log = _log_item()
    resolved = resolve_pointer(log.payload, LineRange(start_line=1, end_line=1))
    assert resolved == "line1 permission denied here"


def test_field_locator_helper_builds_self_consistent_locator():
    pod = _pod_item()
    loc = field_locator(pod, "status", "containerStatuses", 0, "state", "waiting", "reason")
    bundle = _bundle(pod)
    finding = Finding(
        id="f1", kind=FindingKind.DETERMINISTIC_FINDING, statement="x",
        evidence_locators=[loc], produced_by="rule:test",
    )
    validate_finding_references(finding, bundle)  # must not raise


def test_line_locator_helper_builds_self_consistent_locator():
    log = _log_item()
    loc = line_locator(log, 1, 1)
    bundle = _bundle(log)
    finding = Finding(
        id="f2", kind=FindingKind.DETERMINISTIC_FINDING, statement="x",
        evidence_locators=[loc], produced_by="rule:test",
    )
    validate_finding_references(finding, bundle)  # must not raise


def test_validation_rejects_unknown_evidence_id():
    bundle = _bundle(_pod_item())
    bad_locator = EvidenceLocator(
        evidence_id="does-not-exist",
        pointer=FieldPath(json_pointer="/status"),
        quoted_text="irrelevant",
    )
    finding = Finding(
        id="f3", kind=FindingKind.DETERMINISTIC_FINDING, statement="x",
        evidence_locators=[bad_locator], produced_by="rule:test",
    )
    with pytest.raises(EvidenceReferenceError):
        validate_finding_references(finding, bundle)


def test_validation_rejects_out_of_bounds_line_range():
    log = _log_item()
    bundle = _bundle(log)
    bad_locator = EvidenceLocator(
        evidence_id=log.id,
        pointer=LineRange(start_line=50, end_line=60),  # the log only has 3 lines
        quoted_text="whatever",
    )
    finding = Finding(
        id="f4", kind=FindingKind.DETERMINISTIC_FINDING, statement="x",
        evidence_locators=[bad_locator], produced_by="rule:test",
    )
    with pytest.raises(EvidenceLocatorOutOfBoundsError):
        validate_finding_references(finding, bundle)


def test_validation_rejects_stale_quote():
    pod = _pod_item()
    bundle = _bundle(pod)
    stale_locator = EvidenceLocator(
        evidence_id=pod.id,
        pointer=FieldPath(json_pointer="/status/containerStatuses/0/state/waiting/reason"),
        quoted_text="ImagePullBackOff",  # the real value is "ErrImagePull" -- deliberately wrong
    )
    finding = Finding(
        id="f5", kind=FindingKind.DETERMINISTIC_FINDING, statement="x",
        evidence_locators=[stale_locator], produced_by="rule:test",
    )
    with pytest.raises(EvidenceQuoteMismatchError):
        validate_finding_references(finding, bundle)


def test_deterministic_finding_requires_at_least_one_locator():
    with pytest.raises(ValueError):
        Finding(id="f6", kind=FindingKind.DETERMINISTIC_FINDING, statement="x", evidence_locators=[], produced_by="rule:test")


def test_observed_fact_may_have_no_locators():
    # Not required, unlike DETERMINISTIC_FINDING -- construction must succeed.
    Finding(id="f7", kind=FindingKind.OBSERVED_FACT, statement="x", evidence_locators=[], produced_by="rule:test")


def test_confidence_reserved_for_ai_hypothesis():
    from k8s_investigator.models.findings import Confidence

    with pytest.raises(ValueError):
        Finding(
            id="f8", kind=FindingKind.DETERMINISTIC_FINDING, statement="x",
            evidence_locators=[EvidenceLocator(evidence_id="x", pointer=FieldPath(json_pointer="/a"), quoted_text="y")],
            produced_by="rule:test", confidence=Confidence.HIGH,
        )


def test_stale_evidence_status_is_representable_and_validates():
    """STALE is not produced by Milestone 1's collector (nothing to compare against within one
    snapshot) -- this proves the type and validator still handle it correctly, ahead of a
    future multi-pass design."""
    ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    stale_item = EvidenceItem(
        id="stale-item", source_ref=ref, collected_at=_ts(), payload={"status": {"phase": "Running"}},
        status=EvidenceStatus.STALE,
    )
    assert stale_item.status == EvidenceStatus.STALE
    bundle = _bundle(stale_item)
    assert bundle.item_by_id("stale-item") is stale_item
