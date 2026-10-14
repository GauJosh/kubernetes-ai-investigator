"""ai.hypothesis -- parsing, code-controlled identity/provenance, evidence-reference
validation, exact-duplicate handling, and stable confidence sorting.

Every test here uses tests/fakes.py::FakeHypothesisModelClient with a programmed canned
response -- no network, no real model call, consistent with every other test in this suite.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from k8s_investigator.ai.hypothesis import (
    HypothesisParseError,
    RawHypothesisCandidate,
    generate_ai_hypotheses,
    make_ai_hypothesis_finding,
    parse_candidates,
)
from k8s_investigator.models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.findings import Confidence, FindingKind
from k8s_investigator.models.refs import ObjectRef, TimestampValue
from tests.fakes import FakeHypothesisModelClient, make_marker_deterministic_finding


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


def _valid_candidate_json(evidence_id: str, confidence: str = "MEDIUM") -> dict:
    return {
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


# --- parse_candidates: top-level malformed response -------------------------------------------


def test_parse_candidates_rejects_non_json():
    with pytest.raises(HypothesisParseError):
        parse_candidates("not json at all {")


def test_parse_candidates_rejects_a_json_object_instead_of_array():
    with pytest.raises(HypothesisParseError):
        parse_candidates(json.dumps({"statement": "not an array"}))


# --- parse_candidates: per-candidate leniency --------------------------------------------------


def test_parse_candidates_accepts_a_well_formed_array():
    pod = _pod_item()
    raw = json.dumps([_valid_candidate_json(pod.id)])
    candidates = parse_candidates(raw)
    assert len(candidates) == 1
    assert isinstance(candidates[0], RawHypothesisCandidate)
    assert candidates[0].confidence == Confidence.MEDIUM


def test_parse_candidates_drops_one_malformed_candidate_but_keeps_the_rest():
    pod = _pod_item()
    good = _valid_candidate_json(pod.id)
    malformed = {"statement": "missing required fields"}  # no evidence_locators, no confidence
    raw = json.dumps([good, malformed])

    candidates = parse_candidates(raw)

    assert len(candidates) == 1
    assert candidates[0].statement == good["statement"]


def test_parse_candidates_empty_array_is_valid_and_yields_no_candidates():
    assert parse_candidates("[]") == []


# --- make_ai_hypothesis_finding: code-controlled identity/provenance --------------------------


def test_make_ai_hypothesis_finding_sets_kind_id_and_produced_by_from_code():
    pod = _pod_item()
    candidate = RawHypothesisCandidate.model_validate(_valid_candidate_json(pod.id))

    finding = make_ai_hypothesis_finding(candidate, "model:test-model@v1")

    assert finding.kind == FindingKind.AI_HYPOTHESIS
    assert finding.produced_by == "model:test-model@v1"
    assert finding.id.startswith("finding:")


def test_make_ai_hypothesis_finding_id_is_independent_of_statement_wording():
    """The id is derived from {produced_by, evidence_ids, confidence} only -- not statement
    text, since an LLM's prose is not guaranteed byte-identical across calls even at
    temperature 0 (docs/phase-0.11-ai-hypothesis-layer-design.md §3.3)."""
    pod = _pod_item()
    candidate_a = RawHypothesisCandidate.model_validate(_valid_candidate_json(pod.id))
    reworded = _valid_candidate_json(pod.id)
    reworded["statement"] = "Completely different wording for the exact same claim."
    candidate_b = RawHypothesisCandidate.model_validate(reworded)

    finding_a = make_ai_hypothesis_finding(candidate_a, "model:test-model@v1")
    finding_b = make_ai_hypothesis_finding(candidate_b, "model:test-model@v1")

    assert finding_a.id == finding_b.id
    assert finding_a.statement != finding_b.statement


def test_make_ai_hypothesis_finding_id_changes_with_confidence_or_evidence():
    pod = _pod_item()
    other_pod = _pod_item("item-pod-2")
    base = RawHypothesisCandidate.model_validate(_valid_candidate_json(pod.id, "MEDIUM"))
    higher_confidence = RawHypothesisCandidate.model_validate(_valid_candidate_json(pod.id, "HIGH"))
    different_evidence = RawHypothesisCandidate.model_validate(_valid_candidate_json(other_pod.id, "MEDIUM"))

    id_base = make_ai_hypothesis_finding(base, "model:test-model@v1").id
    id_confidence = make_ai_hypothesis_finding(higher_confidence, "model:test-model@v1").id
    id_evidence = make_ai_hypothesis_finding(different_evidence, "model:test-model@v1").id

    assert id_base != id_confidence
    assert id_base != id_evidence


# --- generate_ai_hypotheses: end-to-end via the fake client ------------------------------------


def test_generate_ai_hypotheses_accepts_a_valid_candidate():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_valid_candidate_json(pod.id)])

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert len(results) == 1
    assert results[0].kind == FindingKind.AI_HYPOTHESIS
    assert len(client.calls) == 1  # exactly one model call, no agentic loop


def test_generate_ai_hypotheses_rejects_candidate_citing_unknown_evidence_id():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_valid_candidate_json("does-not-exist-in-bundle")])

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert results == []


def test_generate_ai_hypotheses_rejects_candidate_with_stale_quote():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    bad = _valid_candidate_json(pod.id)
    bad["evidence_locators"][0]["quoted_text"] = "Running"  # the real value is "Pending"
    client.response = json.dumps([bad])

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert results == []


def test_generate_ai_hypotheses_rejects_candidate_with_out_of_bounds_pointer():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    bad = _valid_candidate_json(pod.id)
    bad["evidence_locators"][0]["pointer"] = {"pointer_type": "field_path", "json_pointer": "/status/doesNotExist"}
    client.response = json.dumps([bad])

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert results == []


def test_generate_ai_hypotheses_empty_response_is_abstention_not_error():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = "[]"

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert results == []


def test_generate_ai_hypotheses_propagates_unrecoverable_parse_error():
    """A malformed response as a whole must not be silently collapsed into "no hypothesis" --
    that would be indistinguishable from legitimate abstention
    (docs/phase-0.11-ai-hypothesis-layer-design.md §3, mirroring the R9 "empty vs failed"
    distinction already established for sibling-pod collection)."""
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = "this is not json"

    with pytest.raises(HypothesisParseError):
        generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")


def test_generate_ai_hypotheses_collapses_exact_duplicate_candidates():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    same_evidence_same_confidence = _valid_candidate_json(pod.id)
    reworded_duplicate = _valid_candidate_json(pod.id)
    reworded_duplicate["statement"] = "Differently worded, same evidence, same confidence."
    client.response = json.dumps([same_evidence_same_confidence, reworded_duplicate])

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert len(results) == 1


def test_generate_ai_hypotheses_keeps_distinct_hypotheses_over_different_evidence():
    pod_a = _pod_item("item-pod-a")
    pod_b = _pod_item("item-pod-b")
    bundle = _bundle(pod_a, pod_b)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_valid_candidate_json(pod_a.id), _valid_candidate_json(pod_b.id)])

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert len(results) == 2


def test_generate_ai_hypotheses_sorts_by_confidence_high_first():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    low = _valid_candidate_json(pod.id, "LOW")
    high = _valid_candidate_json(pod.id, "HIGH")
    # Give each a distinct evidence set so they are not exact-duplicate-collapsed.
    pod2 = _pod_item("item-pod-2")
    high["evidence_locators"][0]["evidence_id"] = pod2.id
    bundle = _bundle(pod, pod2)
    client.response = json.dumps([low, high])

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1"
    )

    assert [f.confidence for f in results] == [Confidence.HIGH, Confidence.LOW]


def test_generate_ai_hypotheses_stable_sort_preserves_model_order_within_a_tier():
    pod_a = _pod_item("item-pod-a")
    pod_b = _pod_item("item-pod-b")
    bundle = _bundle(pod_a, pod_b)
    client = FakeHypothesisModelClient()
    first = _valid_candidate_json(pod_a.id, "MEDIUM")
    first["statement"] = "first returned by the model"
    second = _valid_candidate_json(pod_b.id, "MEDIUM")
    second["statement"] = "second returned by the model"
    client.response = json.dumps([first, second])

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert [f.statement for f in results] == [first["statement"], second["statement"]]


def test_generate_ai_hypotheses_calls_the_client_exactly_once_with_the_built_input():
    pod = _pod_item()
    bundle = _bundle(pod)
    client = FakeHypothesisModelClient()
    client.response = "[]"

    generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert len(client.calls) == 1
    assert pod.id in client.calls[0]["evidence"]
