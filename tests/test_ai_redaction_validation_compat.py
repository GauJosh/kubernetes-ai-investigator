"""Phase 0.12 (live-cluster follow-up) requirement 5: the redacted model-input projection must
remain compatible with evidence citations and application-side reference validation.

The resolution this project takes (stated explicitly, not left implicit): redaction never
touches surrounding diagnostic text, so a hypothesis citing *anything the model was shown
unredacted* validates exactly as before (no behavior change). A hypothesis citing a redacted
span can never validate -- validate_finding_references always re-resolves against the real,
unredacted EvidenceBundle, which does not contain the redaction placeholder text, so such a
citation is rejected the same way any other hallucinated citation already is (see
ai.hypothesis.generate_ai_hypotheses's existing per-candidate rejection). This file proves that
resolution is correct and safe end-to-end, rather than leaving it as an assumption -- and proves
it does NOT silently weaken validate_finding_references, which is untouched by this change.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from k8s_investigator.ai.hypothesis import generate_ai_hypotheses
from k8s_investigator.ai.model_input import build_model_input
from k8s_investigator.ai.redaction import _FULL_VALUE_PLACEHOLDER
from k8s_investigator.models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.findings import FindingKind
from k8s_investigator.models.refs import ObjectRef, TimestampValue
from k8s_investigator.report_builder import build_report

from .fakes import FakeHypothesisModelClient

_FAKE_DSN = "postgresql://postgres:postgres@192.168.49.1:5432/devops_genai"


def _ts() -> TimestampValue:
    return TimestampValue(value=datetime.now(timezone.utc), source_field="collectedAt")


def _bundle_with_secret_and_clean_evidence() -> tuple[EvidenceBundle, EvidenceItem, EvidenceItem]:
    ref_pod = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    secret_item = EvidenceItem(
        id="item-secret", source_ref=ref_pod, collected_at=_ts(),
        payload={"spec": {"containers": [{"env": [{"name": "PGVECTOR_DSN", "value": _FAKE_DSN}]}]}},
        status=EvidenceStatus.PRESENT,
    )
    ref_state = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    clean_item = EvidenceItem(
        id="item-clean", source_ref=ref_state, collected_at=_ts(),
        payload={"status": {"containerStatuses": [{"state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}},
        status=EvidenceStatus.PRESENT,
    )
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    bundle = EvidenceBundle(
        id="bundle-1", investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)),
        items=[secret_item, clean_item],
    )
    return bundle, secret_item, clean_item


def _candidate(evidence_id: str, pointer: dict, quoted_text: str) -> dict:
    return {
        "statement": "A hypothesis.",
        "confidence": "MEDIUM",
        "limitation": None,
        "contradicts": None,
        "evidence_locators": [{"evidence_id": evidence_id, "pointer": pointer, "quoted_text": quoted_text}],
    }


def test_citing_the_non_redacted_field_still_validates_normally():
    """Regression: redaction must not break citations against content it never touched."""
    bundle, _secret_item, clean_item = _bundle_with_secret_and_clean_evidence()
    client = FakeHypothesisModelClient()
    pointer = {"pointer_type": "field_path", "json_pointer": "/status/containerStatuses/0/state/waiting/reason"}
    client.response = json.dumps([_candidate(clean_item.id, pointer, "CrashLoopBackOff")])

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert len(results) == 1
    assert results[0].kind == FindingKind.AI_HYPOTHESIS


def test_citing_exactly_the_redaction_placeholder_the_model_was_shown_is_safely_rejected():
    """The conflict this requirement calls out, resolved explicitly: the model can only ever
    see the placeholder for a redacted field. If it faithfully quotes that placeholder, the
    citation still fails validate_finding_references (the real bundle holds the real secret,
    not the placeholder) -- and the candidate is dropped, not raised, not crashing anything."""
    bundle, secret_item, _clean_item = _bundle_with_secret_and_clean_evidence()

    model_input = build_model_input(bundle, [])
    assert model_input["evidence"][secret_item.id]["redacted"] is True
    shown_value = model_input["evidence"][secret_item.id]["payload"]["spec"]["containers"][0]["env"][0]["value"]
    assert shown_value == _FULL_VALUE_PLACEHOLDER  # exactly what the model was actually shown

    client = FakeHypothesisModelClient()
    pointer = {"pointer_type": "field_path", "json_pointer": "/spec/containers/0/env/0/value"}
    client.response = json.dumps([_candidate(secret_item.id, pointer, shown_value)])

    results = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")

    assert results == []  # rejected, exactly like any other hallucinated/stale citation


def test_a_hallucinated_redacted_citation_never_reaches_or_breaks_build_report():
    """End-to-end: even if generate_ai_hypotheses's own rejection somehow didn't happen, prove
    the full pipeline (deterministic findings + accepted AI hypotheses) through build_report
    never crashes because of this -- here, confirming it simply never gets that far."""
    bundle, secret_item, clean_item = _bundle_with_secret_and_clean_evidence()
    model_input = build_model_input(bundle, [])
    shown_value = model_input["evidence"][secret_item.id]["payload"]["spec"]["containers"][0]["env"][0]["value"]

    client = FakeHypothesisModelClient()
    pointer_secret = {"pointer_type": "field_path", "json_pointer": "/spec/containers/0/env/0/value"}
    pointer_clean = {"pointer_type": "field_path", "json_pointer": "/status/containerStatuses/0/state/waiting/reason"}
    client.response = json.dumps(
        [
            _candidate(secret_item.id, pointer_secret, shown_value),  # must be rejected
            _candidate(clean_item.id, pointer_clean, "CrashLoopBackOff"),  # must be accepted
        ]
    )

    ai_findings = generate_ai_hypotheses(bundle, [], client, model_identifier="model:test@v1")
    assert len(ai_findings) == 1  # only the valid one survived

    from k8s_investigator.models.report import AIHypothesisRunStatus

    report = build_report(
        bundle, ai_findings, [], ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED,
    )
    assert len(report.ai_hypotheses) == 1
