"""Phase 0.12 (live-cluster follow-up): citation reliability for multiline log evidence.

The real bug, reproduced here with a synthetic fixture shaped like the actual Minikube
traceback: a model correctly read and quoted the exact right log line, but supplied a
LineRange pointing at a different line -- it had to count unnumbered lines itself and
miscounted. The fix (ai.model_input._project_line_indexed_text) makes line indices explicit --
every line the model is shown carries its own code-computed "line" number, copied directly
from validation._resolve_line_range's own 0-indexed .splitlines() addressing. Nothing about
validation itself changes; this file proves that explicitly, end to end, through
generate_ai_hypotheses and build_report, exactly as the task requires.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from k8s_investigator.ai.hypothesis import generate_ai_hypotheses
from k8s_investigator.ai.model_input import build_model_input
from k8s_investigator.models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.findings import FindingKind
from k8s_investigator.models.refs import ApiCallRef, ObjectRef, TimestampValue
from k8s_investigator.models.report import AIHypothesisRunStatus
from k8s_investigator.report_builder import build_report
from k8s_investigator.validation import resolve_pointer, validate_finding_references
from .fakes import make_marker_deterministic_finding
from k8s_investigator.models.locators import LineRange

from .fakes import FakeHypothesisModelClient

_FAKE_DSN = "postgresql://postgres:postgres@192.168.49.1:5432/devops_genai"

# Shaped exactly like the real Minikube rag-service traceback: the diagnostically useful line
# is NOT the first line, and a generic-looking "Traceback (most recent call last):" line (the
# real model's wrong citation target) sits well before it.
_TRACEBACK_LINES = [
    "starting up",
    "connecting to database",
    "Traceback (most recent call last):",
    '  File "/app/app/main.py", line 2, in <module>',
    "    from .routes import router",
    '  File "/app/app/routes.py", line 577, in <module>',
    "    _ensure_pgvector_table()",
    'psycopg.OperationalError: connection failed: connection to server at "192.168.49.1", port 5432 failed: Connection refused',
]
_TARGET_LINE_INDEX = 7  # the psycopg error -- what a correct hypothesis should cite
_WRONG_LINE_INDEX = 2  # "Traceback (most recent call last):" -- the real bug's wrong target
_TARGET_TEXT = _TRACEBACK_LINES[_TARGET_LINE_INDEX]


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


def _candidate(evidence_id: str, start_line: int, end_line: int, quoted_text: str, confidence: str = "HIGH") -> dict:
    return {
        "statement": "The connection to PostgreSQL is refused.",
        "confidence": confidence,
        "limitation": None,
        "contradicts": None,
        "evidence_locators": [
            {
                "evidence_id": evidence_id,
                "pointer": {"pointer_type": "line_range", "start_line": start_line, "end_line": end_line},
                "quoted_text": quoted_text,
            }
        ],
    }


# --- The model-input projection shows the exact index validation will use ---------------------


def test_model_input_line_index_matches_what_validation_will_actually_resolve():
    """The explicit 'line' number shown to the model for the target line must equal the real
    index validation._resolve_line_range would independently compute against the real
    bundle -- this is the entire basis of the fix: the model copies a number, it does not
    count one."""
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    model_input = build_model_input(bundle, [])

    shown_lines = model_input["evidence"][log_item.id]["payload"]["lines"]
    shown_entry = next(line for line in shown_lines if line["text"] == _TARGET_TEXT)
    assert shown_entry["line"] == _TARGET_LINE_INDEX

    independently_resolved = resolve_pointer(
        log_item.payload, LineRange(start_line=_TARGET_LINE_INDEX, end_line=_TARGET_LINE_INDEX)
    )
    assert independently_resolved == _TARGET_TEXT == shown_entry["text"]


# --- Regression 1: the exact PostgreSQL traceback scenario, correct citation accepted ---------


def test_postgresql_traceback_scenario_correct_citation_is_accepted():
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [_candidate(log_item.id, _TARGET_LINE_INDEX, _TARGET_LINE_INDEX, _TARGET_TEXT)]
    )

    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1")

    assert len(results) == 1
    assert results[0].kind == FindingKind.AI_HYPOTHESIS
    assert results[0].evidence_locators[0].quoted_text == _TARGET_TEXT


# --- Regression 2: the exact real-world bug (correct quote, wrong line) is still rejected -----


def test_postgresql_traceback_scenario_reproduces_and_rejects_the_real_observed_bug():
    """Reproduces the actual live-cluster failure: the right quote, paired with the wrong
    line index. Validation is unchanged and still catches this -- requirement 4 (do not
    weaken validation) is satisfied by construction, proven here directly."""
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [_candidate(log_item.id, _WRONG_LINE_INDEX, _WRONG_LINE_INDEX, _TARGET_TEXT)]
    )

    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1")

    assert results == []  # rejected: line 2's real text is "Traceback...", not the quote given


# --- Valid references (non-traceback, simple case) and incorrect references (generic) --------


def test_valid_single_line_reference_is_accepted():
    bundle, log_item = _log_bundle(["alpha", "beta", "gamma"])
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(log_item.id, 1, 1, "beta")])

    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1")

    assert len(results) == 1


def test_incorrect_line_index_with_mismatched_text_is_rejected():
    bundle, log_item = _log_bundle(["alpha", "beta", "gamma"])
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(log_item.id, 0, 0, "gamma")])  # line 0 is "alpha"

    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1")

    assert results == []


def test_out_of_bounds_line_index_is_rejected():
    bundle, log_item = _log_bundle(["alpha", "beta", "gamma"])
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(log_item.id, 10, 10, "alpha")])

    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1")

    assert results == []


def test_valid_multi_line_range_is_accepted():
    bundle, log_item = _log_bundle(["alpha", "beta", "gamma"])
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(log_item.id, 0, 1, "alpha\nbeta")])

    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1")

    assert len(results) == 1


# --- Redacted evidence: line indexing and redaction interact safely ---------------------------


def test_redacted_line_keeps_the_correct_index_but_can_never_be_validly_cited():
    """A credential-bearing line is redacted in place (same line count, same index) -- the
    model sees the placeholder at the correct index, but citing it (even with the right line
    number) still fails validation, since the real bundle never contains the placeholder."""
    lines = ["starting up", f"connecting: dsn={_FAKE_DSN}", "Connection refused"]
    bundle, log_item = _log_bundle(lines)

    model_input = build_model_input(bundle, [])
    shown = model_input["evidence"][log_item.id]
    assert shown["redacted"] is True
    assert "credential_url" in shown["redaction_categories"]
    redacted_line_entry = shown["payload"]["lines"][1]
    assert redacted_line_entry["line"] == 1
    assert _FAKE_DSN not in redacted_line_entry["text"]

    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(log_item.id, 1, 1, redacted_line_entry["text"])])

    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1")

    assert results == []  # the real line 1 is the unredacted DSN text, not the placeholder


def test_non_sensitive_line_near_a_redacted_one_still_validates_normally():
    """Redaction of one line must not disturb citation of an adjacent, non-sensitive line --
    proves line count/boundaries are preserved exactly."""
    lines = ["starting up", f"connecting: dsn={_FAKE_DSN}", "Connection refused"]
    bundle, log_item = _log_bundle(lines)

    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(log_item.id, 2, 2, "Connection refused")])

    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1")

    assert len(results) == 1


# --- Mixed valid/invalid batch, end to end through build_report -------------------------------


def test_mixed_valid_and_invalid_citations_only_the_valid_one_survives_into_the_report():
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [
            _candidate(log_item.id, _WRONG_LINE_INDEX, _WRONG_LINE_INDEX, _TARGET_TEXT),  # bad
            _candidate(log_item.id, _TARGET_LINE_INDEX, _TARGET_LINE_INDEX, _TARGET_TEXT),  # good
        ]
    )

    ai_findings = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1")
    assert len(ai_findings) == 1

    report = build_report(bundle, ai_findings, [], ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED)
    assert len(report.ai_hypotheses) == 1
    assert report.ai_hypotheses[0].evidence_locators[0].quoted_text == _TARGET_TEXT


# --- Regression: FieldPath/dict-shaped evidence citation behavior is completely unaffected ----


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
