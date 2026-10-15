"""Phase 0.12 (second live-cluster follow-up): line-range span consistency.

The second live OpenAI evaluation confirmed the line-counting fix worked (the model copied the
correct start index), but surfaced a narrower, different bug: the model's quoted_text spanned
two real lines (58-59, a Python traceback error message continuing onto an indented line) while
it declared end_line=58 -- a one-line range for a two-line quote. The unchanged validator
correctly rejected this (the declared range's resolved content didn't match the quote), but
with a generic mismatch message.

This file tests the new, explicit ``LineRangeSpanMismatchError`` check
(``validation._check_line_range_span``), which fires earlier and more specifically for exactly
this class of error -- while proving, directly, that nothing about what gets accepted or
rejected has changed: the new check is strictly additive, and the original "right quote, wrong
single-line index" protection (from the FIRST live-cluster follow-up) still rejects exactly as
before, now via the unchanged content-match path (since a correctly-sized single-line range
passes the new span check and falls through to it).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from .fakes import make_marker_deterministic_finding

from k8s_investigator.ai.hypothesis import generate_ai_hypotheses
from k8s_investigator.ai.model_input import build_model_input
from k8s_investigator.errors import EvidenceQuoteMismatchError, LineRangeSpanMismatchError
from k8s_investigator.models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.findings import Finding, FindingKind
from k8s_investigator.models.locators import EvidenceLocator, LineRange
from k8s_investigator.models.refs import ApiCallRef, ObjectRef, TimestampValue
from k8s_investigator.models.report import AIHypothesisRunStatus
from k8s_investigator.report_builder import build_report
from k8s_investigator.validation import validate_finding_references

from .fakes import FakeHypothesisModelClient

_FAKE_DSN = "postgresql://postgres:postgres@192.168.49.1:5432/devops_genai"

# The exact real-world shape: a Python traceback whose final error message continues onto an
# indented continuation line -- two real lines, one logical message.
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


def _finding(evidence_id: str, start_line: int, end_line: int, quoted_text: str) -> Finding:
    locator = EvidenceLocator(
        evidence_id=evidence_id,
        pointer=LineRange(start_line=start_line, end_line=end_line),
        quoted_text=quoted_text,
    )
    return Finding(
        id=f"finding-test-{start_line}-{end_line}", kind=FindingKind.AI_HYPOTHESIS,
        statement="x", evidence_locators=[locator], produced_by="model:test@v1",
        confidence="MEDIUM",
    )


def _refs_for_lines(bundle: EvidenceBundle, evidence_id: str, lines_wanted: list[int]) -> list[str]:
    """The model no longer constructs line_range citations for line-indexed text -- it selects
    refs. This reads the real refs for the given 0-indexed lines straight from the same
    build_model_input output the model itself would be shown."""
    model_input = build_model_input(bundle, [])
    all_lines = model_input["evidence"][evidence_id]["payload"]["lines"]
    by_line = {entry["line"]: entry["ref"] for entry in all_lines}
    return [by_line[n] for n in lines_wanted]


def _ref_candidate(bundle: EvidenceBundle, evidence_id: str, lines_wanted: list[int], confidence: str = "HIGH") -> dict:
    """A correctly-constructed line_citations candidate -- the new, only way to cite
    line-indexed text. Always span-consistent by construction; there is no "declared range"
    for the model to get wrong anymore."""
    return {
        "statement": "The connection to PostgreSQL is refused.",
        "confidence": confidence, "limitation": None, "contradicts": None,
        "evidence_locators": [],
        "line_citations": [{"evidence_id": evidence_id, "refs": _refs_for_lines(bundle, evidence_id, lines_wanted)}],
    }


def _candidate(evidence_id: str, start_line: int, end_line: int, quoted_text: str) -> dict:
    return {
        "statement": "The connection to PostgreSQL is refused.",
        "confidence": "HIGH", "limitation": None, "contradicts": None,
        "evidence_locators": [
            {
                "evidence_id": evidence_id,
                "pointer": {"pointer_type": "line_range", "start_line": start_line, "end_line": end_line},
                "quoted_text": quoted_text,
            }
        ],
    }


# --- 1. The exact PostgreSQL traceback quote spanning lines 58-59 (here, 7-8) -----------------


def test_exact_traceback_quote_with_correctly_widened_range_is_accepted():
    """This is the fix's whole point: a model that correctly sets end_line to cover its own
    multi-line quote must succeed, not just be rejected more clearly."""
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    finding = _finding(log_item.id, _ERROR_LINE, _CONTINUATION_LINE, _FULL_QUOTE)
    validate_finding_references(finding, bundle)  # must not raise


def test_exact_traceback_quote_with_correctly_widened_range_accepted_end_to_end():
    """Updated for the citation-reference increment: the model no longer constructs a
    start_line/end_line range for line-indexed text at all -- it selects the two refs
    covering the real two-line message, and code resolves the exact same quote."""
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [_ref_candidate(bundle, log_item.id, [_ERROR_LINE, _CONTINUATION_LINE])]
    )

    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1").accepted

    assert len(results) == 1
    assert results[0].evidence_locators[0].quoted_text == _FULL_QUOTE


# --- 2. A correct quote with an undersized range (the exact second-follow-up bug) -------------


def test_correct_quote_with_undersized_range_raises_the_specific_span_error():
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    # end_line=7 (not 8) -- the exact bug: real quote, range one line too narrow.
    finding = _finding(log_item.id, _ERROR_LINE, _ERROR_LINE, _FULL_QUOTE)

    with pytest.raises(LineRangeSpanMismatchError) as excinfo:
        validate_finding_references(finding, bundle)
    assert excinfo.value.declared_span == 1
    assert excinfo.value.quoted_span == 2


def test_correct_quote_with_undersized_range_rejected_end_to_end():
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    client = FakeHypothesisModelClient()
    client.response = json.dumps([_candidate(log_item.id, _ERROR_LINE, _ERROR_LINE, _FULL_QUOTE)])

    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1").accepted

    assert results == []  # rejected, not repaired/widened -- requirement 3


def test_undersized_range_is_not_silently_widened_to_match_the_quote():
    """Explicitly proves requirement 3: the system never expands an invalid citation on the
    model's behalf. If it did, this exact finding would validate; it must not."""
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    finding = _finding(log_item.id, _ERROR_LINE, _ERROR_LINE, _FULL_QUOTE)
    with pytest.raises(LineRangeSpanMismatchError):
        validate_finding_references(finding, bundle)
    # And confirm the Finding's own declared range was NOT silently corrected to (7, 8).
    assert finding.evidence_locators[0].pointer.end_line == _ERROR_LINE


# --- 3. An incorrect starting line (the FIRST follow-up's original bug, still protected) ------


def test_wrong_starting_line_with_correctly_sized_range_still_rejected_by_content_check():
    """The span-consistency check does NOT catch this -- span is 1==1 -- so it must fall
    through to the unchanged content-match check, which still rejects it. Proves requirement 4
    (preserve the original line-counting protection) directly."""
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    correct_text = _TRACEBACK_LINES[_ERROR_LINE]
    finding = _finding(log_item.id, _WRONG_LINE, _WRONG_LINE, correct_text)

    with pytest.raises(EvidenceQuoteMismatchError):  # NOT LineRangeSpanMismatchError
        validate_finding_references(finding, bundle)


def test_wrong_starting_line_rejected_end_to_end():
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [_candidate(log_item.id, _WRONG_LINE, _WRONG_LINE, _TRACEBACK_LINES[_ERROR_LINE])]
    )

    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1").accepted

    assert results == []


# --- 4. Multiline quotes with indentation and newline-boundary edge cases ---------------------


def test_multiline_quote_with_leading_indentation_is_accepted_when_exact():
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    quote = _TRACEBACK_LINES[3] + "\n" + _TRACEBACK_LINES[4]  # '  File "...", ...' + indented continuation
    finding = _finding(log_item.id, 3, 4, quote)
    validate_finding_references(finding, bundle)  # must not raise


def test_multiline_quote_missing_leading_indentation_on_one_line_is_rejected():
    """Same lines, but with the continuation line's leading whitespace stripped off -- still
    span-consistent (2 declared, 2 quoted) but now fails the real content check."""
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    wrong_quote = _TRACEBACK_LINES[3] + "\n" + _TRACEBACK_LINES[4].strip()
    finding = _finding(log_item.id, 3, 4, wrong_quote)

    with pytest.raises(EvidenceQuoteMismatchError):
        validate_finding_references(finding, bundle)


def test_quote_with_extra_trailing_newline_is_rejected_by_span_check():
    """A trailing "\\n" makes the quote look like it spans one more line than declared --
    exactly the self-consistency check's job, independent of content correctness."""
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    quote_with_trailing_newline = _TRACEBACK_LINES[_ERROR_LINE] + "\n"
    finding = _finding(log_item.id, _ERROR_LINE, _ERROR_LINE, quote_with_trailing_newline)

    with pytest.raises(LineRangeSpanMismatchError):
        validate_finding_references(finding, bundle)


def test_single_line_citation_is_unaffected_by_the_new_check():
    """Regression: the overwhelmingly common case (a correct single-line citation) must keep
    working exactly as before."""
    bundle, log_item = _log_bundle(["alpha", "beta", "gamma"])
    finding = _finding(log_item.id, 1, 1, "beta")
    validate_finding_references(finding, bundle)  # must not raise


# --- 5. Redacted evidence and mixed valid/invalid hypotheses -----------------------------------


def test_redacted_multiline_span_still_cannot_be_validly_cited():
    lines = ["starting up", f"connecting: dsn={_FAKE_DSN}", "retry pending", "giving up"]
    bundle, log_item = _log_bundle(lines)
    model_input = build_model_input(bundle, [])
    redacted_text_line1 = model_input["evidence"][log_item.id]["payload"]["lines"][1]["text"]
    assert _FAKE_DSN not in redacted_text_line1

    # A correctly-SIZED two-line range (span-consistent) citing the redacted line plus its
    # neighbor, using exactly what the model was shown -- still must fail against the real,
    # unredacted bundle.
    quote = redacted_text_line1 + "\n" + "retry pending"
    finding = _finding(log_item.id, 1, 2, quote)

    with pytest.raises(EvidenceQuoteMismatchError):
        validate_finding_references(finding, bundle)


def test_redacted_evidence_end_to_end_via_generate_ai_hypotheses():
    lines = ["starting up", f"connecting: dsn={_FAKE_DSN}", "retry pending", "giving up"]
    bundle, log_item = _log_bundle(lines)
    model_input = build_model_input(bundle, [])
    redacted_text_line1 = model_input["evidence"][log_item.id]["payload"]["lines"][1]["text"]

    client = FakeHypothesisModelClient()
    quote = redacted_text_line1 + "\n" + "retry pending"
    client.response = json.dumps([_candidate(log_item.id, 1, 2, quote)])

    results = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1").accepted

    assert results == []


def test_mixed_batch_span_mismatch_wrong_line_and_valid_citation_only_valid_survives():
    """A richer mixed-batch test than the first follow-up's. The two old-shaped,
    raw-line_range candidates no longer parse at all under the citation-reference increment
    (evidence_locators now accepts field_path only) and are dropped at parse time instead of
    validation time -- same ultimate outcome, different mechanism; this is itself a
    regression check that a malformed legacy-shaped candidate never resurrects the retired
    line_range-via-evidence_locators path. Only the ref-based, valid candidate survives."""
    bundle, log_item = _log_bundle(_TRACEBACK_LINES)
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [
            _candidate(log_item.id, _ERROR_LINE, _ERROR_LINE, _FULL_QUOTE),  # legacy shape, dropped at parse
            _candidate(log_item.id, _WRONG_LINE, _WRONG_LINE, _TRACEBACK_LINES[_ERROR_LINE]),  # legacy shape, dropped at parse
            _ref_candidate(bundle, log_item.id, [_ERROR_LINE, _CONTINUATION_LINE]),  # valid
        ]
    )

    ai_findings = generate_ai_hypotheses(bundle, [make_marker_deterministic_finding()], client, model_identifier="model:test@v1").accepted
    assert len(ai_findings) == 1
    assert ai_findings[0].evidence_locators[0].quoted_text == _FULL_QUOTE

    report = build_report(bundle, ai_findings, [], ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED)
    assert len(report.ai_hypotheses) == 1


# --- FieldPath citations are completely unaffected (the new check is LineRange-specific) ------


def test_field_path_citations_are_unaffected_by_the_new_line_range_check():
    ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    pod_item = EvidenceItem(
        id="item-pod", source_ref=ref, collected_at=_ts(),
        payload={"status": {"phase": "Pending"}}, status=EvidenceStatus.PRESENT,
    )
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    bundle = EvidenceBundle(
        id="bundle-1", investigation_target=target,
        collected_at_range=(datetime.now(timezone.utc), datetime.now(timezone.utc)), items=[pod_item],
    )
    from k8s_investigator.models.locators import FieldPath

    locator = EvidenceLocator(
        evidence_id=pod_item.id, pointer=FieldPath(json_pointer="/status/phase"), quoted_text="Pending",
    )
    finding = Finding(
        id="f-fp", kind=FindingKind.AI_HYPOTHESIS, statement="x", evidence_locators=[locator],
        produced_by="model:test@v1", confidence="LOW",
    )
    validate_finding_references(finding, bundle)  # must not raise
