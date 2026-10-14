"""Phase 0.12 (12-call evaluation follow-up): structural guards for the two qualitative failure
classes the live evaluation surfaced -- high-confidence claims with no deterministic grounding,
and unacknowledged contradictions -- plus the payload_shape discriminator addressing the
structured-evidence citation-mechanics confusions (wrong pointer type, invented "/payload"
prefix, non-scalar FieldPath).

Every check here is structural (finding kinds present, evidence-id set membership), never a
judgment of a hypothesis's own wording -- consistent with the instruction to avoid brittle
keyword-based rules and prefer rules that generalize to evidence this suite has never seen.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from k8s_investigator.ai.hypothesis import generate_ai_hypotheses
from k8s_investigator.ai.model_input import build_model_input
from k8s_investigator.errors import UnacknowledgedContradictionError, UngroundedConfidenceError
from k8s_investigator.models.evidence import ContradictionEdge, EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.findings import Confidence, Finding, FindingKind
from k8s_investigator.models.locators import EvidenceLocator, FieldPath
from k8s_investigator.models.refs import ApiCallRef, ObjectRef, TimestampValue
from k8s_investigator.models.report import AIHypothesisRunStatus
from k8s_investigator.report_builder import build_report

from .fakes import FakeHypothesisModelClient, make_marker_deterministic_finding


def _ts() -> TimestampValue:
    return TimestampValue(value=datetime.now(timezone.utc), source_field="collectedAt")


def _pod_item(item_id: str = "item-pod") -> EvidenceItem:
    ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    return EvidenceItem(
        id=item_id, source_ref=ref, collected_at=_ts(),
        payload={"status": {"phase": "Pending"}}, status=EvidenceStatus.PRESENT,
    )


def _log_item(item_id: str = "item-log") -> EvidenceItem:
    ref = ApiCallRef(verb="get", path="/pods/p/log", params={})
    return EvidenceItem(
        id=item_id, source_ref=ref, collected_at=_ts(),
        payload="line one\nline two\n", status=EvidenceStatus.PRESENT,
    )


def _bundle(*items: EvidenceItem) -> EvidenceBundle:
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    return EvidenceBundle(
        id="bundle-1", investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)),
        items=list(items),
    )


def _candidate(evidence_id: str, confidence: str, limitation: str | None = None) -> dict:
    return {
        "statement": "A hypothesis.", "confidence": confidence, "limitation": limitation,
        "contradicts": None,
        "evidence_locators": [
            {"evidence_id": evidence_id, "pointer": {"pointer_type": "field_path", "json_pointer": "/status/phase"}, "quoted_text": "Pending"},
        ],
    }


def _finding(evidence_id: str, confidence: Confidence, limitation: str | None = None) -> Finding:
    return Finding(
        id=f"finding-test-{confidence.value}", kind=FindingKind.AI_HYPOTHESIS, statement="x",
        evidence_locators=[
            EvidenceLocator(evidence_id=evidence_id, pointer=FieldPath(json_pointer="/status/phase"), quoted_text="Pending")
        ],
        produced_by="model:test@v1", confidence=confidence, limitation=limitation,
    )


# --- payload_shape discriminator ---------------------------------------------------------------


def test_structured_dict_payload_gets_payload_shape_structured():
    pod = _pod_item()
    bundle = _bundle(pod)
    model_input = build_model_input(bundle, [])
    assert model_input["evidence"][pod.id]["payload_shape"] == "structured"


def test_string_payload_gets_payload_shape_line_indexed_text():
    log = _log_item()
    bundle = _bundle(log)
    model_input = build_model_input(bundle, [])
    assert model_input["evidence"][log.id]["payload_shape"] == "line_indexed_text"


def test_non_present_item_has_no_payload_shape_key():
    ref = ApiCallRef(verb="list", path="/events", params={})
    gap = EvidenceItem(id="gap-1", source_ref=ref, collected_at=_ts(), status=EvidenceStatus.NOT_FOUND)
    bundle = _bundle(gap)
    model_input = build_model_input(bundle, [])
    assert "payload_shape" not in model_input["evidence"][gap.id]


# --- contradictions appear in model input, are redacted, and are reproducible -----------------


def test_contradictions_defaults_to_empty_list():
    bundle = _bundle(_pod_item())
    model_input = build_model_input(bundle, [])
    assert model_input["contradictions"] == []


def test_contradictions_are_projected_into_model_input():
    pod_a, pod_b = _pod_item("item-a"), _pod_item("item-b")
    bundle = _bundle(pod_a, pod_b)
    edge = ContradictionEdge(
        evidence_a=pod_a.id, evidence_b=pod_b.id, rule_id="rule:R6_capacity",
        description="Scheduler claims insufficient CPU, but allocatable capacity is sufficient.",
    )
    model_input = build_model_input(bundle, [], contradictions=[edge])
    assert model_input["contradictions"] == [
        {"evidence_a": pod_a.id, "evidence_b": pod_b.id, "rule_id": "rule:R6_capacity",
         "description": "Scheduler claims insufficient CPU, but allocatable capacity is sufficient."}
    ]


def test_contradiction_description_is_redacted_like_any_other_free_text():
    pod_a, pod_b = _pod_item("item-a"), _pod_item("item-b")
    bundle = _bundle(pod_a, pod_b)
    edge = ContradictionEdge(
        evidence_a=pod_a.id, evidence_b=pod_b.id, rule_id="rule:x",
        description="dsn=postgresql://postgres:postgres@192.168.49.1:5432/devops_genai",
    )
    model_input = build_model_input(bundle, [], contradictions=[edge])
    assert "postgres:postgres" not in model_input["contradictions"][0]["description"]


def test_model_input_reproducible_with_contradictions_present():
    from k8s_investigator.canonical import content_hash

    pod_a, pod_b = _pod_item("item-a"), _pod_item("item-b")
    bundle = _bundle(pod_a, pod_b)
    edge = ContradictionEdge(evidence_a=pod_a.id, evidence_b=pod_b.id, rule_id="rule:x", description="d")
    first = content_hash(build_model_input(bundle, [], contradictions=[edge]))
    second = content_hash(build_model_input(bundle, [], contradictions=[edge]))
    assert first == second


# --- HIGH confidence requires a DETERMINISTIC_FINDING -------------------------------------------


def test_high_confidence_rejected_with_no_deterministic_finding_present():
    from k8s_investigator.ai.hypothesis import _check_confidence_requires_deterministic_grounding

    finding = _finding("item-pod", Confidence.HIGH)
    with pytest.raises(UngroundedConfidenceError):
        _check_confidence_requires_deterministic_grounding(finding, [])


def test_high_confidence_accepted_when_a_deterministic_finding_exists():
    from k8s_investigator.ai.hypothesis import _check_confidence_requires_deterministic_grounding

    finding = _finding("item-pod", Confidence.HIGH)
    _check_confidence_requires_deterministic_grounding(finding, [make_marker_deterministic_finding()])  # must not raise


def test_medium_confidence_accepted_with_no_deterministic_finding():
    from k8s_investigator.ai.hypothesis import _check_confidence_requires_deterministic_grounding

    finding = _finding("item-pod", Confidence.MEDIUM)
    _check_confidence_requires_deterministic_grounding(finding, [])  # must not raise


def test_low_confidence_accepted_with_no_deterministic_finding():
    from k8s_investigator.ai.hypothesis import _check_confidence_requires_deterministic_grounding

    finding = _finding("item-pod", Confidence.LOW)
    _check_confidence_requires_deterministic_grounding(finding, [])  # must not raise


def test_high_confidence_with_no_deterministic_finding_rejected_end_to_end():
    """Reproduces the exact shape of the 12-call evaluation's scheduling_blocked_no_event and
    r4_insufficient_evidence failures: a real-but-insufficiently-supportive citation, HIGH
    confidence, zero deterministic findings."""
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(pod.id, "HIGH")])

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert results == []


def test_medium_confidence_with_no_deterministic_finding_accepted_end_to_end():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(pod.id, "MEDIUM")])

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert len(results) == 1
    assert results[0].confidence == Confidence.MEDIUM


def test_high_confidence_accepted_end_to_end_when_a_deterministic_finding_exists():
    """The r1_dns_failure_already_explained case: a real deterministic finding backs a HIGH
    AI hypothesis -- must still succeed, proving this check is not overly broad."""
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(pod.id, "HIGH")])

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert len(results) == 1
    assert results[0].confidence == Confidence.HIGH


# --- Contradiction acknowledgment ---------------------------------------------------------------


def test_citing_contested_evidence_without_limitation_is_rejected():
    pod_a, pod_b = _pod_item("item-a"), _pod_item("item-b")
    bundle = _bundle(pod_a, pod_b)
    contradictions = [ContradictionEdge(evidence_a=pod_a.id, evidence_b=pod_b.id, rule_id="rule:x", description="d")]
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(pod_a.id, "MEDIUM", limitation=None)])

    results = generate_ai_hypotheses(
        bundle, [], client, model_identifier="model:test@v1", contradictions=contradictions,
    )

    assert results == []


def test_citing_contested_evidence_with_limitation_is_accepted():
    pod_a, pod_b = _pod_item("item-a"), _pod_item("item-b")
    bundle = _bundle(pod_a, pod_b)
    contradictions = [ContradictionEdge(evidence_a=pod_a.id, evidence_b=pod_b.id, rule_id="rule:x", description="d")]
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [_candidate(pod_a.id, "MEDIUM", limitation="A recorded contradiction exists over this evidence.")]
    )

    results = generate_ai_hypotheses(
        bundle, [], client, model_identifier="model:test@v1", contradictions=contradictions,
    )

    assert len(results) == 1


def test_citing_uncontested_evidence_is_unaffected_by_unrelated_contradictions():
    """A contradiction that doesn't involve the cited evidence must not affect acceptance --
    the check is scoped to actual evidence-id overlap, not "any contradiction exists"."""
    pod_a, pod_b, pod_c = _pod_item("item-a"), _pod_item("item-b"), _pod_item("item-c")
    bundle = _bundle(pod_a, pod_b, pod_c)
    contradictions = [ContradictionEdge(evidence_a=pod_a.id, evidence_b=pod_b.id, rule_id="rule:x", description="d")]
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(pod_c.id, "MEDIUM", limitation=None)])

    results = generate_ai_hypotheses(
        bundle, [], client, model_identifier="model:test@v1", contradictions=contradictions,
    )

    assert len(results) == 1


def test_no_contradictions_at_all_is_unaffected_regression():
    """Regression: the overwhelmingly common case (no recorded contradiction) must behave
    exactly as before this phase -- omitting the argument entirely still works."""
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(pod.id, "MEDIUM", limitation=None)])

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert len(results) == 1


def test_unacknowledged_contradiction_check_directly_raises_the_specific_error():
    from k8s_investigator.ai.hypothesis import _check_contradiction_acknowledged

    finding = _finding("item-a", Confidence.MEDIUM, limitation=None)
    contradictions = [ContradictionEdge(evidence_a="item-a", evidence_b="item-b", rule_id="rule:x", description="d")]
    with pytest.raises(UnacknowledgedContradictionError):
        _check_contradiction_acknowledged(finding, contradictions)


# --- Mixed batch: both new checks combined, plus a fully valid one, through build_report ------


def test_mixed_batch_overconfident_unacknowledged_and_valid_only_valid_survives_into_report():
    pod_a, pod_b = _pod_item("item-a"), _pod_item("item-b")
    bundle = _bundle(pod_a, pod_b)
    contradictions = [ContradictionEdge(evidence_a=pod_a.id, evidence_b=pod_b.id, rule_id="rule:x", description="d")]
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [
            _candidate(pod_a.id, "HIGH"),  # rejected: no deterministic finding
            _candidate(pod_a.id, "MEDIUM", limitation=None),  # rejected: unacknowledged contradiction
            _candidate(pod_b.id, "LOW", limitation="Hedged and unrelated to the contradiction's other side."),  # valid
        ]
    )

    ai_findings = generate_ai_hypotheses(
        bundle, [], client, model_identifier="model:test@v1", contradictions=contradictions,
    )
    assert len(ai_findings) == 1
    assert ai_findings[0].confidence == Confidence.LOW

    report = build_report(bundle, ai_findings, [], ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED)
    assert len(report.ai_hypotheses) == 1
