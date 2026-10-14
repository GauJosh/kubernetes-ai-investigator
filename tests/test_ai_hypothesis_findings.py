"""Phase 0.11: the two narrow Finding validator tightenings for AI_HYPOTHESIS.

Covers: a valid AI_HYPOTHESIS (locator + confidence present) constructs and validates; an
AI_HYPOTHESIS with no evidence_locators is rejected; an AI_HYPOTHESIS with confidence=None is
rejected; and regression coverage proving DETERMINISTIC_FINDING and OBSERVED_FACT behavior is
unchanged by this tightening (DETERMINISTIC_FINDING still requires locators but never
confidence; OBSERVED_FACT still requires neither).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from k8s_investigator.errors import UngroundedFindingError
from k8s_investigator.models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.findings import Confidence, Finding, FindingKind
from k8s_investigator.models.locators import EvidenceLocator, FieldPath
from k8s_investigator.models.refs import ObjectRef, TimestampValue
from k8s_investigator.validation import validate_finding_references


def _ts() -> TimestampValue:
    return TimestampValue(value=datetime.now(timezone.utc), source_field="collectedAt")


def _pod_item() -> EvidenceItem:
    ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    payload = {"status": {"phase": "Pending"}}
    return EvidenceItem(
        id="item-pod", source_ref=ref, collected_at=_ts(), payload=payload,
        status=EvidenceStatus.PRESENT,
    )


def _bundle(*items: EvidenceItem) -> EvidenceBundle:
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    return EvidenceBundle(
        id="bundle-1",
        investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)),
        items=list(items),
    )


def _locator(item: EvidenceItem) -> EvidenceLocator:
    return EvidenceLocator(
        evidence_id=item.id,
        pointer=FieldPath(json_pointer="/status/phase"),
        quoted_text="Pending",
    )


# --- AI_HYPOTHESIS: valid case --------------------------------------------------------------


def test_valid_ai_hypothesis_constructs_and_validates():
    pod = _pod_item()
    bundle = _bundle(pod)
    finding = Finding(
        id="h1",
        kind=FindingKind.AI_HYPOTHESIS,
        statement="The pod may be stuck because of a transient scheduling issue.",
        evidence_locators=[_locator(pod)],
        produced_by="model:test-model@v1",
        confidence=Confidence.MEDIUM,
    )
    validate_finding_references(finding, bundle)  # must not raise


# --- AI_HYPOTHESIS: invalid cases, newly rejected by this tightening ------------------------


def test_ai_hypothesis_without_evidence_locators_is_rejected_at_construction():
    with pytest.raises(ValueError):
        Finding(
            id="h2",
            kind=FindingKind.AI_HYPOTHESIS,
            statement="An ungrounded guess.",
            evidence_locators=[],
            produced_by="model:test-model@v1",
            confidence=Confidence.LOW,
        )


def test_ai_hypothesis_without_confidence_is_rejected_at_construction():
    pod = _pod_item()
    with pytest.raises(ValueError):
        Finding(
            id="h3",
            kind=FindingKind.AI_HYPOTHESIS,
            statement="A hypothesis with no confidence label.",
            evidence_locators=[_locator(pod)],
            produced_by="model:test-model@v1",
            confidence=None,
        )


def test_ai_hypothesis_missing_both_locators_and_confidence_is_rejected():
    with pytest.raises(ValueError):
        Finding(
            id="h4",
            kind=FindingKind.AI_HYPOTHESIS,
            statement="Neither grounded nor ranked.",
            evidence_locators=[],
            produced_by="model:test-model@v1",
        )


def test_validate_finding_references_defense_in_depth_catches_ungrounded_ai_hypothesis():
    """Mirrors the existing DETERMINISTIC_FINDING defense-in-depth check in validation.py --
    an AI_HYPOTHESIS that somehow reached validate_finding_references with no locators (e.g.
    constructed via model_construct, bypassing __init__ validation) is still caught centrally."""
    pod = _pod_item()
    bundle = _bundle(pod)
    finding = Finding.model_construct(
        id="h5",
        kind=FindingKind.AI_HYPOTHESIS,
        statement="Bypassed construction-time validation.",
        evidence_locators=[],
        produced_by="model:test-model@v1",
        confidence=Confidence.HIGH,
        limitation=None,
        contradicts=None,
    )
    with pytest.raises(UngroundedFindingError):
        validate_finding_references(finding, bundle)


# --- Regression: DETERMINISTIC_FINDING and OBSERVED_FACT behavior is unchanged -------------


def test_deterministic_finding_still_requires_locators_unchanged():
    with pytest.raises(ValueError):
        Finding(
            id="d1", kind=FindingKind.DETERMINISTIC_FINDING, statement="x",
            evidence_locators=[], produced_by="rule:test",
        )


def test_deterministic_finding_still_forbids_confidence_unchanged():
    pod = _pod_item()
    with pytest.raises(ValueError):
        Finding(
            id="d2", kind=FindingKind.DETERMINISTIC_FINDING, statement="x",
            evidence_locators=[_locator(pod)], produced_by="rule:test",
            confidence=Confidence.HIGH,
        )


def test_deterministic_finding_with_locators_and_no_confidence_still_succeeds_unchanged():
    pod = _pod_item()
    finding = Finding(
        id="d3", kind=FindingKind.DETERMINISTIC_FINDING, statement="x",
        evidence_locators=[_locator(pod)], produced_by="rule:test",
    )
    assert finding.confidence is None


def test_observed_fact_still_allows_no_locators_and_no_confidence_unchanged():
    finding = Finding(
        id="o1", kind=FindingKind.OBSERVED_FACT, statement="x",
        evidence_locators=[], produced_by="rule:test",
    )
    assert finding.evidence_locators == []
    assert finding.confidence is None


def test_observed_fact_still_forbids_confidence_unchanged():
    with pytest.raises(ValueError):
        Finding(
            id="o2", kind=FindingKind.OBSERVED_FACT, statement="x",
            evidence_locators=[], produced_by="rule:test",
            confidence=Confidence.LOW,
        )
