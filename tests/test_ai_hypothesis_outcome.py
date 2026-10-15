"""Phase 0.16: the four-way AIHypothesisOutcome classification and the bounded
AIHypothesisRejectionReason taxonomy generate_ai_hypotheses now returns alongside its accepted
findings -- none of this changes which candidates are accepted or rejected (see
test_ai_hypothesis_pipeline.py and friends, unaffected by this file, for that).

All synthetic fixtures and FakeHypothesisModelClient responses -- no real provider, no network,
no real credential.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from k8s_investigator.ai.hypothesis import generate_ai_hypotheses
from k8s_investigator.models.evidence import ContradictionEdge, EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.refs import ObjectRef, TimestampValue
from k8s_investigator.models.report import AIHypothesisOutcome, AIHypothesisRejectionReason

from .fakes import FakeHypothesisModelClient, make_marker_deterministic_finding

_FAKE_DSN = "postgresql://postgres:postgres@192.168.49.1:5432/devops_genai"


def _ts() -> TimestampValue:
    return TimestampValue(value=datetime.now(timezone.utc), source_field="collectedAt")


def _pod_item(item_id: str = "item-pod") -> EvidenceItem:
    ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    payload = {"status": {"phase": "Pending"}}
    return EvidenceItem(id=item_id, source_ref=ref, collected_at=_ts(), payload=payload, status=EvidenceStatus.PRESENT)


def _bundle(*items: EvidenceItem) -> EvidenceBundle:
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    return EvidenceBundle(
        id="bundle-1",
        investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)),
        items=list(items),
    )


def _candidate(evidence_id: str, confidence: str = "MEDIUM", **overrides) -> dict:
    base = {
        "statement": "The pod may be stuck due to a transient issue.",
        "evidence_locators": [
            {
                "evidence_id": evidence_id,
                "pointer": {"pointer_type": "field_path", "json_pointer": "/status/phase"},
                "quoted_text": "Pending",
            }
        ],
        "confidence": confidence,
    }
    base.update(overrides)
    return base


# --- ABSTAINED ---------------------------------------------------------------------------------


def test_outcome_abstained_when_model_returns_empty_array():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = "[]"

    result = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert result.accepted == []
    assert result.outcome == AIHypothesisOutcome.ABSTAINED
    assert result.rejection_reasons == []


# --- ACCEPTED ------------------------------------------------------------------------------------


def test_outcome_accepted_when_one_valid_candidate_and_nothing_rejected():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(pod.id)])

    result = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert len(result.accepted) == 1
    assert result.outcome == AIHypothesisOutcome.ACCEPTED
    assert result.rejection_reasons == []


def test_outcome_accepted_survives_a_harmless_dedup_drop():
    """A dedup drop is redundancy, not a rejection -- ACCEPTED still holds even though not
    every candidate in the raw response ended up in `accepted`."""
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    same = _candidate(pod.id)
    reworded_duplicate = _candidate(pod.id)
    reworded_duplicate["statement"] = "Differently worded, same evidence, same confidence."
    client.response = json.dumps([same, reworded_duplicate])

    result = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert len(result.accepted) == 1
    assert result.outcome == AIHypothesisOutcome.ACCEPTED
    assert result.rejection_reasons == []


# --- ALL_REJECTED --------------------------------------------------------------------------------


def test_outcome_all_rejected_when_candidates_exist_but_none_survive():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate("does-not-exist-in-bundle")])

    result = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert result.accepted == []
    assert result.outcome == AIHypothesisOutcome.ALL_REJECTED
    assert result.rejection_reasons == [AIHypothesisRejectionReason.EVIDENCE_VALIDATION_FAILED]


def test_outcome_all_rejected_distinct_from_abstained_for_an_all_malformed_response():
    """A non-empty array that is entirely malformed-shape garbage is NOT 'the model explicitly
    returned no candidates' -- ABSTAINED is reserved for a genuinely empty array."""
    client = FakeHypothesisModelClient()
    client.response = json.dumps([{"garbage": True}])
    bundle = _bundle(_pod_item())

    result = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert result.accepted == []
    assert result.outcome == AIHypothesisOutcome.ALL_REJECTED
    assert result.rejection_reasons == [AIHypothesisRejectionReason.MALFORMED_CANDIDATE_SHAPE]


# --- PARTIAL_ACCEPTANCE --------------------------------------------------------------------------


def test_outcome_partial_acceptance_when_some_accepted_and_some_rejected():
    pod = _pod_item()
    other_pod = _pod_item("item-pod-2")
    bundle = _bundle(pod, other_pod)
    client = FakeHypothesisModelClient()
    good = _candidate(pod.id)
    bad = _candidate("does-not-exist-in-bundle")
    client.response = json.dumps([good, bad])

    result = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert len(result.accepted) == 1
    assert result.outcome == AIHypothesisOutcome.PARTIAL_ACCEPTANCE
    assert result.rejection_reasons == [AIHypothesisRejectionReason.EVIDENCE_VALIDATION_FAILED]


# --- Every rejection reason, each grounded in its own existing check --------------------------


def test_rejection_reason_malformed_candidate_shape():
    bundle = _bundle(_pod_item())
    client = FakeHypothesisModelClient()
    client.response = json.dumps([{"statement": "missing required fields"}])

    result = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")
    assert result.rejection_reasons == [AIHypothesisRejectionReason.MALFORMED_CANDIDATE_SHAPE]


def test_rejection_reason_invalid_citation_reference():
    """An unknown line_citations ref -- UnknownCitationReferenceError."""
    log_ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="log-holder")
    log_item = EvidenceItem(
        id="item-log", source_ref=log_ref, collected_at=_ts(), payload="line one\nline two\n",
        status=EvidenceStatus.PRESENT,
    )
    bundle = _bundle(log_item)
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [
            {
                "statement": "Something about the log.",
                "line_citations": [{"evidence_id": log_item.id, "refs": ["ref-that-was-never-issued"]}],
                "confidence": "LOW",
            }
        ]
    )

    result = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")
    assert result.rejection_reasons == [AIHypothesisRejectionReason.INVALID_CITATION_REFERENCE]


def test_rejection_reason_evidence_validation_failed_on_stale_quote():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    bad = _candidate(pod.id)
    bad["evidence_locators"][0]["quoted_text"] = "Running"  # the real value is "Pending"
    client.response = json.dumps([bad])

    result = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")
    assert result.rejection_reasons == [AIHypothesisRejectionReason.EVIDENCE_VALIDATION_FAILED]


def test_rejection_reason_unsupported_finding_statement():
    pod = _pod_item()
    bundle = _bundle(pod)
    existing = make_marker_deterministic_finding()
    client = FakeHypothesisModelClient()
    bad = _candidate(pod.id)
    bad["evidence_locators"][0]["quoted_text"] = existing.statement  # quotes a Finding's prose, not real evidence
    client.response = json.dumps([bad])

    result = generate_ai_hypotheses(bundle, [existing], client, model_identifier="model:test@v1")
    assert result.rejection_reasons == [AIHypothesisRejectionReason.UNSUPPORTED_FINDING_STATEMENT]


def test_rejection_reason_ungrounded_confidence():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(pod.id, confidence="HIGH")])

    # No DETERMINISTIC_FINDING in `findings` -- HIGH confidence has nothing grounding it.
    result = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")
    assert result.rejection_reasons == [AIHypothesisRejectionReason.UNGROUNDED_CONFIDENCE]


def test_rejection_reason_unacknowledged_contradiction():
    pod = _pod_item()
    other = _pod_item("item-pod-2")
    bundle = _bundle(pod, other)
    contradictions = [
        ContradictionEdge(
            evidence_a=pod.id, evidence_b=other.id,
            rule_id="contradiction:test", description="synthetic contradiction for this test",
        )
    ]
    client = FakeHypothesisModelClient()
    candidate = _candidate(pod.id)
    candidate.pop("limitation", None)  # no acknowledgement of the contradiction
    client.response = json.dumps([candidate])

    result = generate_ai_hypotheses(
        bundle, [], client, model_identifier="model:test@v1", contradictions=contradictions
    )
    assert result.rejection_reasons == [AIHypothesisRejectionReason.UNACKNOWLEDGED_CONTRADICTION]


def test_rejection_reason_other_for_a_plain_valueerror_not_otherwise_classified():
    """A candidate with neither evidence_locators nor line_citations parses fine as a
    RawHypothesisCandidate (both default to empty) but fails at Finding construction itself
    (Finding._enforce_closed_contract's own "no evidence_locators" guard) -- a plain ValueError
    raised by no named check in the classification table, so it must fall through to OTHER
    rather than being misclassified as one of the specific reasons."""
    bundle = _bundle(_pod_item())
    client = FakeHypothesisModelClient()
    client.response = json.dumps([{"statement": "no evidence at all", "confidence": "LOW"}])

    result = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")
    assert result.rejection_reasons == [AIHypothesisRejectionReason.OTHER]


# --- Determinism ----------------------------------------------------------------------------


def test_rejection_classification_is_deterministic_across_repeated_calls():
    """The same raw response, parsed and classified twice, must produce the exact same
    outcome and the exact same reason list, in the exact same order, both times."""
    pod = _pod_item()
    bundle = _bundle(pod)
    good = _candidate(pod.id)
    bad_citation = _candidate("does-not-exist-in-bundle")
    bad_confidence = _candidate(pod.id, confidence="HIGH")
    bad_confidence["evidence_locators"][0]["evidence_id"] = pod.id  # distinct statement, same pod

    client = FakeHypothesisModelClient()
    client.response = json.dumps([good, bad_citation, bad_confidence])

    result_a = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")
    result_b = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert result_a.outcome == result_b.outcome
    assert result_a.rejection_reasons == result_b.rejection_reasons
    assert [f.id for f in result_a.accepted] == [f.id for f in result_b.accepted]


# --- Diagnostics never leak sensitive content ------------------------------------------------


def test_rejection_reasons_never_contain_raw_candidate_text_or_credentials():
    """A rejected candidate's own statement (here containing a synthetic credential) must never
    appear anywhere in the result -- rejection_reasons only ever holds bounded enum values."""
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    bad = _candidate("does-not-exist-in-bundle")
    bad["statement"] = f"Likely caused by a bad connection string: {_FAKE_DSN}"
    client.response = json.dumps([bad])

    result = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert result.rejection_reasons == [AIHypothesisRejectionReason.EVIDENCE_VALIDATION_FAILED]
    serialized = str(result)
    assert _FAKE_DSN not in serialized
    assert "postgres:postgres" not in serialized
