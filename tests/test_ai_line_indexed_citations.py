"""Phase 0.12 (citation-reference increment): citation reliability for multiline log evidence.

The first live-cluster follow-up fixed a miscounted line index by making line numbers
explicit. The second fixed a too-narrow range for a multi-line quote with an explicit span
check. Both were representation/validation improvements -- the model still constructed the
final (pointer, quoted_text) itself. This increment retires that construction step entirely
for line-indexed text: the model selects one or more pre-issued, stable "ref" ids
(``model_input.make_line_ref``) instead, and code resolves the selection into the exact
``LineRange`` + quote (``ai.hypothesis._resolve_line_citation``). Both original bug classes
(miscounted index, undersized range) become structurally impossible to express through this
path; this file proves the new mechanism reproduces the correct result for the exact same
traceback shape that originally exposed them, without ever constructing a pointer by hand.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from k8s_investigator.ai.hypothesis import generate_ai_hypotheses
from k8s_investigator.ai.model_input import build_model_input, make_line_ref
from k8s_investigator.models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.findings import FindingKind
from k8s_investigator.models.locators import LineRange
from k8s_investigator.models.refs import ApiCallRef, ObjectRef, TimestampValue
from k8s_investigator.models.report import AIHypothesisRunStatus
from k8s_investigator.report_builder import build_report
from k8s_investigator.validation import resolve_pointer

from .fakes import FakeHypothesisModelClient, make_marker_deterministic_finding

_FAKE_DSN = "postgresql://postgres:postgres@192.168.49.1:5432/devops_genai"

# Shaped exactly like the real Minikube rag-service traceback: the diagnostically useful
# message spans two real lines (an exception message, then an indented continuation line),
# well after a generic-looking "Traceback (most recent call last):" line.
_TRACEBACK_LINES = [
    "starting up",
    "connecting to database",
    "Traceback (most recent call last):",
    '  File "/app/app/main.py", line 2, in <module>',
    "    from .routes import router",
    '  File "/app/app/routes.py", line 577, in <module>',
    "    _ensure_pgvector_table()",
    'psycopg.OperationalError: connection failed: connection to server at "192.168.49.1", port 5432 failed: Connection refused',
    "\tIs the server running on that host and accepting TCP/IP connections?",
]
_ERROR_LINE = 7
_CONTINUATION_LINE = 8
_WRONG_LINE = 2  # "Traceback (most recent call last):" -- the first follow-up's observed bug
_TARGET_TEXT = _TRACEBACK_LINES[_ERROR_LINE]
_FULL_QUOTE = _TRACEBACK_LINES[_ERROR_LINE] + "\n" + _TRACEBACK_LINES[_CONTINUATION_LINE]


def _ts() -> TimestampValue:
    return TimestampValue(value=datetime.now(timezone.utc), source_field="collectedAt")


def _log_bundle(lines: list[str]) -> tuple[EvidenceBundle, EvidenceItem]:
    ref = ApiCallRef(verb="get", path="/pods/p/log", params={})
    log_item = EvidenceItem(
        id="item-log", source_ref=ref, collected_at=_ts(),
        payload="\n".join(lines) + "\n", status=EvidenceStatus.PRESENT,
    )
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    bundle = EvidenceBundle(
        id="bundle-1", investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)),
        items=[log_item],
    )
    return bundle, log_item


def _real_refs(bundle: EvidenceBundle, evidence_id: str) -> dict[int, str]:
    """The exact refs the model would actually be shown for this bundle. A redacted or
    truncated-away line has no "ref" key at all and is simply absent from this mapping."""
    model_input = build_model_input(bundle, [])
    return {
        entry["line"]: entry["ref"]
        for entry in model_input["evidence"][evidence_id]["payload"]["lines"]
        if "ref" in entry
    }


def _ref_candidate(evidence_id: str, refs: list[str], confidence: str = "HIGH") -> dict:
    return {
        "statement": "The connection to PostgreSQL is refused.",
        "confidence": confidence, "limitation": None, "contradicts": None,
        "evidence_locators": [],
        "line_citations": [{"evidence_id": evidence_id, "refs": refs}],
    }


# --- The model-input projection: refs match what validation will actually resolve -------------


def test_model_input_ref_resolves_to_the_exact_index_validation_will_use():
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    real_refs = _real_refs(bundle, log_item.id)
    model_input = build_model_input(bundle, [])

    shown_lines = model_input["evidence"][log_item.id]["payload"]["lines"]
    shown_entry = next(line for line in shown_lines if line["text"] == _TARGET_TEXT)
    assert shown_entry["line"] == _ERROR_LINE
    assert shown_entry["ref"] == real_refs[_ERROR_LINE] == make_line_ref(log_item.id, _ERROR_LINE)

    independently_resolved = resolve_pointer(
        log_item.payload, LineRange(start_line=_ERROR_LINE, end_line=_ERROR_LINE)
    )
    assert independently_resolved == _TARGET_TEXT == shown_entry["text"]


# --- Regression 1: the miscounted-line bug class is now structurally impossible ---------------


def test_postgresql_traceback_single_line_ref_is_accepted():
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    refs = _real_refs(bundle, log_item.id)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_ref_candidate(log_item.id, [refs[_ERROR_LINE]])])

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert len(results) == 1
    assert results[0].kind == FindingKind.AI_HYPOTHESIS
    assert results[0].evidence_locators[0].quoted_text == _TARGET_TEXT
    assert results[0].evidence_locators[0].pointer == LineRange(start_line=_ERROR_LINE, end_line=_ERROR_LINE)


def test_a_ref_for_the_wrong_line_cites_that_lines_real_content_not_the_target():
    """There is no way to "select the wrong line but claim the target's text" anymore -- a ref
    always resolves to its own real line's content. Selecting the wrong-line ref simply cites
    the wrong (but accurately quoted) content; the model can no longer mismatch an index
    against a quote it independently transcribed, because it no longer transcribes one."""
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    refs = _real_refs(bundle, log_item.id)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_ref_candidate(log_item.id, [refs[_WRONG_LINE]])])

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert len(results) == 1  # accepted -- it genuinely cites line 2's own real text
    assert results[0].evidence_locators[0].quoted_text == _TRACEBACK_LINES[_WRONG_LINE]
    assert results[0].evidence_locators[0].quoted_text != _TARGET_TEXT


# --- Regression 2: the undersized-range bug class is now structurally impossible --------------


def test_postgresql_traceback_two_line_refs_resolve_the_full_two_line_quote():
    """The exact original multi-line scenario: instead of declaring end_line and hoping it
    matches the quote's own length, the model selects both refs and code computes the span
    and the joined quote -- always self-consistent, nothing to get wrong."""
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    refs = _real_refs(bundle, log_item.id)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_ref_candidate(log_item.id, [refs[_ERROR_LINE], refs[_CONTINUATION_LINE]])])

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert len(results) == 1
    assert results[0].evidence_locators[0].quoted_text == _FULL_QUOTE
    assert results[0].evidence_locators[0].pointer == LineRange(start_line=_ERROR_LINE, end_line=_CONTINUATION_LINE)


def test_two_refs_given_out_of_order_still_resolve_correctly():
    """The model naming refs out of order must not produce a backwards or wrong range -- code
    sorts by the refs' real line numbers, never trusting the order given."""
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    refs = _real_refs(bundle, log_item.id)
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [_ref_candidate(log_item.id, [refs[_CONTINUATION_LINE], refs[_ERROR_LINE]])]  # reversed
    )

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert len(results) == 1
    assert results[0].evidence_locators[0].quoted_text == _FULL_QUOTE


# --- Duplicate lines remain distinguishable (requirement 1) -----------------------------------


def test_duplicate_text_at_different_lines_gets_distinct_refs_and_resolves_correctly():
    lines = ["unique line", "same text", "unique middle", "same text"]
    bundle, log_item = _log_bundle(lines)
    refs = _real_refs(bundle, log_item.id)
    assert refs[1] != refs[3]  # distinct despite identical text

    client = FakeHypothesisModelClient()
    client.response = json.dumps([_ref_candidate(log_item.id, [refs[3]])])  # the second "same text"

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert len(results) == 1
    assert results[0].evidence_locators[0].pointer == LineRange(start_line=3, end_line=3)


# --- Untrusted reference ids: unknown, cross-item, malformed selections -----------------------


def test_unknown_reference_id_is_rejected_not_substituted():
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_ref_candidate(log_item.id, ["totally-fabricated-ref"])])

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert results == []


def test_reference_belonging_to_another_evidence_item_is_rejected():
    ref_a = ApiCallRef(verb="get", path="/pods/p/log-a", params={})
    ref_b = ApiCallRef(verb="get", path="/pods/p/log-b", params={})
    item_a = EvidenceItem(id="item-log-a", source_ref=ref_a, collected_at=_ts(), payload="alpha\n", status=EvidenceStatus.PRESENT)
    item_b = EvidenceItem(id="item-log-b", source_ref=ref_b, collected_at=_ts(), payload="beta\n", status=EvidenceStatus.PRESENT)
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    bundle = EvidenceBundle(
        id="bundle-1", investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)),
        items=[item_a, item_b],
    )
    model_input = build_model_input(bundle, [])
    ref_from_b = model_input["evidence"][item_b.id]["payload"]["lines"][0]["ref"]

    # Claims evidence_id=item_a but supplies a ref that really belongs to item_b.
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_ref_candidate(item_a.id, [ref_from_b])])

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert results == []


def test_non_contiguous_reference_selection_is_rejected():
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    refs = _real_refs(bundle, log_item.id)
    client = FakeHypothesisModelClient()
    # Lines 0 and 2 -- skips line 1, not a contiguous run.
    client.response = json.dumps([_ref_candidate(log_item.id, [refs[0], refs[2]])])

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert results == []


def test_duplicate_reference_in_one_selection_is_rejected():
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    refs = _real_refs(bundle, log_item.id)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_ref_candidate(log_item.id, [refs[0], refs[0]])])

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert results == []


def test_empty_refs_list_is_rejected_by_schema():
    """RawHypothesisCandidate.line_citations[].refs requires at least one ref -- an empty
    selection never even reaches the resolution step."""
    import pydantic
    import pytest as _pytest

    from k8s_investigator.ai.hypothesis import RawHypothesisCandidate

    with _pytest.raises(pydantic.ValidationError):
        RawHypothesisCandidate.model_validate(
            {
                "statement": "x", "confidence": "LOW", "limitation": None, "contradicts": None,
                "evidence_locators": [], "line_citations": [{"evidence_id": "item-log", "refs": []}],
            }
        )


# --- Redacted lines are never exposed as references at all (requirement 3) --------------------


def test_redacted_line_has_no_ref_at_all_and_cannot_be_selected():
    lines = ["starting up", f"connecting: dsn={_FAKE_DSN}", "Connection refused"]
    bundle, log_item = _log_bundle(lines)

    model_input = build_model_input(bundle, [])
    shown = model_input["evidence"][log_item.id]
    assert shown["redacted"] is True
    redacted_line_entry = shown["payload"]["lines"][1]
    assert redacted_line_entry["line"] == 1
    assert "ref" not in redacted_line_entry  # withheld, not just a placeholder value
    assert _FAKE_DSN not in redacted_line_entry["text"]

    # There is no real ref for the redacted line to even construct a (fabricated) selection
    # from -- using a plausible-looking but made-up ref must still be rejected as unknown.
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_ref_candidate(log_item.id, [make_line_ref(log_item.id, 1)])])

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert results == []


def test_non_redacted_line_adjacent_to_a_redacted_one_is_still_fully_citable():
    lines = ["starting up", f"connecting: dsn={_FAKE_DSN}", "Connection refused"]
    bundle, log_item = _log_bundle(lines)
    refs = _real_refs(bundle, log_item.id)
    assert 1 not in refs  # the redacted line has no ref to find

    client = FakeHypothesisModelClient()
    client.response = json.dumps([_ref_candidate(log_item.id, [refs[2]])])

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert len(results) == 1
    assert results[0].evidence_locators[0].quoted_text == "Connection refused"


# --- Truncation boundary: a truncated-away line has no ref to select ---------------------------


def test_line_cut_by_truncation_has_no_ref_and_cannot_be_selected():
    """Uses the DEFAULT budget (exactly what generate_ai_hypotheses's own internal
    build_model_input call uses) -- a line 0 that alone fills it pushes line 1 out entirely."""
    from k8s_investigator.ai.model_input import DEFAULT_MAX_PAYLOAD_CHARS

    log_text = ("x" * DEFAULT_MAX_PAYLOAD_CHARS) + "\nbeta\ngamma\n"
    ref = ApiCallRef(verb="get", path="/pods/p/log", params={})
    log_item = EvidenceItem(id="item-log", source_ref=ref, collected_at=_ts(), payload=log_text, status=EvidenceStatus.PRESENT)
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    bundle = EvidenceBundle(
        id="bundle-1", investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)), items=[log_item],
    )

    model_input = build_model_input(bundle, [])  # the exact default budget
    shown_lines = model_input["evidence"][log_item.id]["payload"]["lines"]
    assert len(shown_lines) == 1  # only the giant line 0 fit; "beta" (line 1) did not

    client = FakeHypothesisModelClient()
    client.response = json.dumps([_ref_candidate(log_item.id, [make_line_ref(log_item.id, 1)])])  # "beta", truncated away

    results = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )

    assert results == []


# --- Mixed valid/invalid batch, end to end through build_report -------------------------------


def test_mixed_batch_unknown_cross_item_and_valid_refs_only_valid_survives_into_the_report():
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    refs = _real_refs(bundle, log_item.id)
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [
            _ref_candidate(log_item.id, ["fabricated-ref"]),  # unknown
            _ref_candidate(log_item.id, [refs[0], refs[2]]),  # non-contiguous
            _ref_candidate(log_item.id, [refs[_ERROR_LINE], refs[_CONTINUATION_LINE]]),  # valid
        ]
    )

    ai_findings = generate_ai_hypotheses(
        bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1",
    )
    assert len(ai_findings) == 1

    report = build_report(bundle, ai_findings, [], ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED)
    assert len(report.ai_hypotheses) == 1
    assert report.ai_hypotheses[0].evidence_locators[0].quoted_text == _FULL_QUOTE


# --- Regression: FieldPath/structured-evidence citation behavior is completely unaffected -----


def test_field_path_citation_against_structured_evidence_is_unaffected():
    ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    pod_item = EvidenceItem(
        id="item-pod", source_ref=ref, collected_at=_ts(),
        payload={"status": {"phase": "Pending"}}, status=EvidenceStatus.PRESENT,
    )
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    bundle = EvidenceBundle(
        id="bundle-1", investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)),
        items=[pod_item],
    )
    model_input = build_model_input(bundle, [])
    assert model_input["evidence"][pod_item.id]["payload"] == {"status": {"phase": "Pending"}}

    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [
            {
                "statement": "x", "confidence": "LOW", "limitation": None, "contradicts": None,
                "evidence_locators": [
                    {
                        "evidence_id": pod_item.id,
                        "pointer": {"pointer_type": "field_path", "json_pointer": "/status/phase"},
                        "quoted_text": "Pending",
                    }
                ],
            }
        ]
    )
    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1")
    assert len(results) == 1
