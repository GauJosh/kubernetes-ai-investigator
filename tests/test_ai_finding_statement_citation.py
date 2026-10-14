"""Phase 0.12 (six-call targeted retest follow-up): a model quoting a Finding's own rendered
statement, instead of the real evidence value it cites, must still be rejected -- and now with
a specific, named reason instead of a generic quote mismatch.

The retest showed this exact pattern in three scenarios (r1_dns_failure_already_explained,
r4_insufficient_evidence_abstention_required, r6_capacity_contradiction_present): a
graph:container_state OBSERVED_FACT's statement ("Container 'server': Waiting
(reason=ErrImagePull).") sits next to the real evidence_id it was derived from in the model
input; the model copied that rendered sentence verbatim as quoted_text while citing the same
evidence_id, instead of quoting the real raw field value ("ErrImagePull"). Both already failed
validate_finding_references's content check (unchanged, still the sole authority) -- this
file proves the new, more specific diagnostic (and the "citable" input labeling behind it)
changes nothing about what is accepted, only names this one mistake precisely when it occurs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from k8s_investigator.ai.hypothesis import (
    _check_quote_not_a_finding_statement,
    generate_ai_hypotheses,
    make_ai_hypothesis_finding,
)
from k8s_investigator.ai.model_input import build_model_input
from k8s_investigator.errors import FindingStatementCitedAsEvidenceError
from k8s_investigator.models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.findings import Finding, FindingKind
from k8s_investigator.models.locators import EvidenceLocator, FieldPath
from k8s_investigator.models.refs import ApiCallRef, ObjectRef, TimestampValue
from k8s_investigator.models.report import AIHypothesisRunStatus
from k8s_investigator.report_builder import build_report

from .fakes import FakeHypothesisModelClient, make_marker_deterministic_finding


def _ts() -> TimestampValue:
    return TimestampValue(value=datetime.now(timezone.utc), source_field="collectedAt")


def _pod_item() -> EvidenceItem:
    ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    return EvidenceItem(
        id="item-pod", source_ref=ref, collected_at=_ts(),
        payload={"status": {"containerStatuses": [{"state": {"waiting": {"reason": "ErrImagePull"}}}]}},
        status=EvidenceStatus.PRESENT,
    )


def _log_item() -> EvidenceItem:
    ref = ApiCallRef(verb="get", path="/pods/p/log", params={})
    return EvidenceItem(
        id="item-log", source_ref=ref, collected_at=_ts(),
        payload="starting up\nexiting\n", status=EvidenceStatus.PRESENT,
    )


def _bundle(*items: EvidenceItem) -> EvidenceBundle:
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    return EvidenceBundle(
        id="bundle-1", investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)), items=list(items),
    )


_CONTAINER_STATE_STATEMENT = "Container 'server': Waiting (reason=ErrImagePull)."
_REAL_REASON_VALUE = "ErrImagePull"


def _observed_fact(pod: EvidenceItem) -> Finding:
    """Mirrors graph.py's real graph:container_state OBSERVED_FACT exactly -- the rendered
    sentence the three retest scenarios showed the model copying verbatim."""
    return Finding(
        id="finding:observed-container-state", kind=FindingKind.OBSERVED_FACT,
        statement=_CONTAINER_STATE_STATEMENT,
        evidence_locators=[
            EvidenceLocator(
                evidence_id=pod.id,
                pointer=FieldPath(json_pointer="/status/containerStatuses/0/state/waiting/reason"),
                quoted_text=_REAL_REASON_VALUE,
            )
        ],
        produced_by="graph:container_state",
    )


def _candidate(evidence_id: str, quoted_text: str, confidence: str = "MEDIUM") -> dict:
    return {
        "statement": "A hypothesis.", "confidence": confidence, "limitation": None, "contradicts": None,
        "evidence_locators": [
            {"evidence_id": evidence_id, "pointer": {"pointer_type": "field_path", "json_pointer": "/status/containerStatuses/0/state/waiting/reason"}, "quoted_text": quoted_text},
        ],
    }


# --- Input representation: the "citable" discriminator ----------------------------------------


def test_evidence_entries_are_marked_citable_true():
    pod = _bundle(_pod_item())
    model_input = build_model_input(pod, [])
    assert model_input["evidence"]["item-pod"]["citable"] is True


def test_finding_entries_are_marked_citable_false():
    pod = _pod_item()
    bundle = _bundle(pod)
    model_input = build_model_input(bundle, [_observed_fact(pod)])
    assert model_input["findings"][0]["citable"] is False
    assert model_input["findings"][0]["statement"] == _CONTAINER_STATE_STATEMENT


def test_non_present_evidence_has_no_citable_key():
    ref = ApiCallRef(verb="list", path="/events", params={})
    gap = EvidenceItem(id="gap-1", source_ref=ref, collected_at=_ts(), status=EvidenceStatus.NOT_FOUND)
    model_input = build_model_input(_bundle(gap), [])
    assert "citable" not in model_input["evidence"]["gap-1"]


# --- The direct check -----------------------------------------------------------------------


def test_quoting_a_findings_statement_verbatim_raises_the_specific_error():
    pod = _pod_item()
    observed = _observed_fact(pod)
    stub = _CandidateStub(pod.id, _CONTAINER_STATE_STATEMENT)
    candidate_finding = make_ai_hypothesis_finding(stub, "model:test@v1", stub.evidence_locators)
    with pytest.raises(FindingStatementCitedAsEvidenceError):
        _check_quote_not_a_finding_statement(candidate_finding, [observed])


def test_quoting_the_real_raw_value_does_not_raise():
    pod = _pod_item()
    observed = _observed_fact(pod)
    stub = _CandidateStub(pod.id, _REAL_REASON_VALUE)
    candidate_finding = make_ai_hypothesis_finding(stub, "model:test@v1", stub.evidence_locators)
    _check_quote_not_a_finding_statement(candidate_finding, [observed])  # must not raise


class _CandidateStub:
    """Minimal stand-in for RawHypothesisCandidate, just enough for make_ai_hypothesis_finding."""

    def __init__(self, evidence_id: str, quoted_text: str) -> None:
        from k8s_investigator.models.findings import Confidence

        self.statement = "stub"
        self.evidence_locators = [
            EvidenceLocator(
                evidence_id=evidence_id,
                pointer=FieldPath(json_pointer="/status/containerStatuses/0/state/waiting/reason"),
                quoted_text=quoted_text,
            )
        ]
        self.confidence = Confidence.MEDIUM
        self.limitation = None
        self.contradicts = None


# --- End-to-end: the three observed-shape cases, rejected with the specific reason ------------


def test_r1_shaped_case_quoting_container_state_statement_is_rejected_end_to_end():
    pod = _pod_item()
    observed = _observed_fact(pod)
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(pod.id, _CONTAINER_STATE_STATEMENT, "HIGH")])

    results = generate_ai_hypotheses(
        bundle, [observed, make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert results == []


def test_r4_shaped_case_quoting_an_observed_fact_statement_about_a_log_is_rejected():
    pod = _pod_item()
    log = _log_item()
    insufficient_evidence_fact = Finding(
        id="finding:insufficient", kind=FindingKind.OBSERVED_FACT,
        statement="Container 'server' is crash-looping, and its logs were checked, but no recognized fatal-error pattern was found.",
        evidence_locators=[
            EvidenceLocator(evidence_id=pod.id, pointer=FieldPath(json_pointer="/status/containerStatuses/0/state/waiting/reason"), quoted_text=_REAL_REASON_VALUE)
        ],
        produced_by="rule:R4_insufficient_evidence",
    )
    bundle = _bundle(pod, log)
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [_candidate(pod.id, "Container 'server' is crash-looping, and its logs were checked, but no recognized fatal-error pattern was found.", "MEDIUM")]
    )

    results = generate_ai_hypotheses(bundle, [insufficient_evidence_fact], client, model_identifier="model:test@v1")

    assert results == []


def test_r6_shaped_case_quoting_a_capacity_statement_with_contradiction_present_is_rejected():
    from k8s_investigator.models.evidence import ContradictionEdge

    pod = _pod_item()
    capacity_fact = Finding(
        id="finding:capacity", kind=FindingKind.DETERMINISTIC_FINDING,
        statement="Pod container cpu request(s): ['server: 100m'] -- 3 node(s) report insufficient cpu.",
        evidence_locators=[
            EvidenceLocator(evidence_id=pod.id, pointer=FieldPath(json_pointer="/status/containerStatuses/0/state/waiting/reason"), quoted_text=_REAL_REASON_VALUE)
        ],
        produced_by="rule:R6_insufficient_cpu",
    )
    bundle = _bundle(pod)
    contradictions = [ContradictionEdge(evidence_a=pod.id, evidence_b=pod.id, rule_id="rule:R6_contradiction", description="d")]
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [_candidate(pod.id, "Pod container cpu request(s): ['server: 100m'] -- 3 node(s) report insufficient cpu.", "MEDIUM")]
    )

    results = generate_ai_hypotheses(
        bundle, [capacity_fact], client, model_identifier="model:test@v1", contradictions=contradictions,
    )

    assert results == []


# --- Negative case: a real, correct source quote remains accepted -----------------------------


def test_a_genuine_source_quote_that_happens_to_differ_from_any_statement_is_still_accepted():
    pod = _pod_item()
    observed = _observed_fact(pod)
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(pod.id, _REAL_REASON_VALUE, "MEDIUM")])

    results = generate_ai_hypotheses(
        bundle, [observed, make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert len(results) == 1
    assert results[0].evidence_locators[0].quoted_text == _REAL_REASON_VALUE


def test_high_confidence_genuine_quote_accepted_when_a_deterministic_finding_exists():
    pod = _pod_item()
    observed = _observed_fact(pod)
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(pod.id, _REAL_REASON_VALUE, "HIGH")])

    results = generate_ai_hypotheses(
        bundle, [observed, make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert len(results) == 1


# --- Mixed batch through the full pipeline ------------------------------------------------------


def test_mixed_batch_statement_quote_and_genuine_quote_only_the_genuine_one_survives():
    pod = _pod_item()
    observed = _observed_fact(pod)
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [
            _candidate(pod.id, _CONTAINER_STATE_STATEMENT, "MEDIUM"),  # rejected: statement, not evidence
            _candidate(pod.id, _REAL_REASON_VALUE, "MEDIUM"),  # accepted: genuine source quote
        ]
    )

    ai_findings = generate_ai_hypotheses(
        bundle, [observed, make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )
    assert len(ai_findings) == 1
    assert ai_findings[0].evidence_locators[0].quoted_text == _REAL_REASON_VALUE

    report = build_report(bundle, ai_findings, [], ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED)
    assert len(report.ai_hypotheses) == 1
