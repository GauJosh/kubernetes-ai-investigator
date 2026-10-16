"""ai.model_input.build_model_input -- reproducibility, bounding, and projection.

Reuses the same synthetic bundle-building pipeline (tests/fixtures/builders.py +
tests/fakes.py) every other test in this suite uses -- no cluster, no model call.
"""

from __future__ import annotations

from datetime import datetime, timezone

from k8s_investigator.ai.model_input import DEFAULT_MAX_PAYLOAD_CHARS, build_model_input, make_line_ref
from k8s_investigator.canonical import content_hash
from k8s_investigator.models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from k8s_investigator.models.findings import Confidence, Finding, FindingKind
from k8s_investigator.models.locators import EvidenceLocator, FieldPath
from k8s_investigator.models.refs import ApiCallRef, ObjectRef, TimestampValue


def _ts() -> TimestampValue:
    return TimestampValue(value=datetime(2026, 10, 14, 0, 0, 0, tzinfo=timezone.utc), source_field="collectedAt")


def _pod_item() -> EvidenceItem:
    ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    payload = {"status": {"containerStatuses": [{"state": {"waiting": {"reason": "ErrImagePull"}}}]}}
    return EvidenceItem(id="item-pod", source_ref=ref, collected_at=_ts(), payload=payload, status=EvidenceStatus.PRESENT)


def _log_item(text: str = "line0\nline1 denied\nline2\n") -> EvidenceItem:
    ref = ApiCallRef(verb="get", path="/pods/p/log", params={"container": "server"})
    return EvidenceItem(id="item-log", source_ref=ref, collected_at=_ts(), payload=text, status=EvidenceStatus.PRESENT)


def _bundle(*items: EvidenceItem) -> EvidenceBundle:
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    return EvidenceBundle(
        id="bundle-1",
        investigation_target=target,
        collected_at_range=(
            datetime(2026, 10, 14, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 14, 0, 0, 1, tzinfo=timezone.utc),
        ),
        items=list(items),
    )


def _deterministic_finding(pod: EvidenceItem) -> Finding:
    locator = EvidenceLocator(
        evidence_id=pod.id,
        pointer=FieldPath(json_pointer="/status/containerStatuses/0/state/waiting/reason"),
        quoted_text="ErrImagePull",
    )
    return Finding(
        id="f1", kind=FindingKind.DETERMINISTIC_FINDING, statement="image pull failed",
        evidence_locators=[locator], produced_by="rule:R2_tag_not_found",
    )


def _ai_hypothesis(pod: EvidenceItem) -> Finding:
    locator = EvidenceLocator(
        evidence_id=pod.id,
        pointer=FieldPath(json_pointer="/status/containerStatuses/0/state/waiting/reason"),
        quoted_text="ErrImagePull",
    )
    return Finding(
        id="h1", kind=FindingKind.AI_HYPOTHESIS, statement="a prior hypothesis",
        evidence_locators=[locator], produced_by="model:test@v1", confidence=Confidence.LOW,
    )


# --- Reproducibility -------------------------------------------------------------------------


def test_same_bundle_and_findings_produce_byte_identical_output():
    pod = _pod_item()
    bundle = _bundle(pod)
    findings = [_deterministic_finding(pod)]

    first = content_hash(build_model_input(bundle, findings))
    second = content_hash(build_model_input(bundle, findings))

    assert first == second


def test_golden_hash_is_stable_for_a_fixed_bundle():
    """A fixed, hand-built bundle+findings pair always hashes to this exact digest. If this
    test ever needs to change, build_model_input's output shape changed -- that should be a
    deliberate, reviewed decision, not an accident."""
    pod = _pod_item()
    bundle = _bundle(pod)
    findings = [_deterministic_finding(pod)]

    digest = content_hash(build_model_input(bundle, findings))

    # Updated again for the "citable" discriminator (findings=False, evidence=True) added to
    # address the live-retest-observed "quoted a Finding's statement, not real evidence"
    # confusion -- a deliberate, reviewed shape change, not an accident.
    assert digest == "c320db5133ebeb68185e756d11663a44db3569e935170089385cbc9c5b401cf7"


def test_different_findings_change_the_hash():
    pod = _pod_item()
    bundle = _bundle(pod)

    hash_with_finding = content_hash(build_model_input(bundle, [_deterministic_finding(pod)]))
    hash_without_finding = content_hash(build_model_input(bundle, []))

    assert hash_with_finding != hash_without_finding


def test_different_bundle_changes_the_hash():
    pod = _pod_item()
    bundle_a = _bundle(pod)
    bundle_b = _bundle(pod, _log_item())

    hash_a = content_hash(build_model_input(bundle_a, []))
    hash_b = content_hash(build_model_input(bundle_b, []))

    assert hash_a != hash_b


# --- Projection correctness ------------------------------------------------------------------


def test_evidence_is_keyed_by_the_real_evidence_id():
    pod = _pod_item()
    bundle = _bundle(pod)
    model_input = build_model_input(bundle, [])
    assert set(model_input["evidence"].keys()) == {pod.id}
    assert model_input["evidence"][pod.id]["evidence_id"] == pod.id


def test_ai_hypothesis_findings_are_excluded_from_the_findings_section():
    pod = _pod_item()
    bundle = _bundle(pod)
    findings = [_deterministic_finding(pod), _ai_hypothesis(pod)]

    model_input = build_model_input(bundle, findings)

    produced_by_values = {f["produced_by"] for f in model_input["findings"]}
    assert produced_by_values == {"rule:R2_tag_not_found"}


def test_bundle_and_target_identity_are_included():
    pod = _pod_item()
    bundle = _bundle(pod)
    model_input = build_model_input(bundle, [])
    assert model_input["bundle_id"] == bundle.id
    assert model_input["investigation_target"]["name"] == "p"


# --- Bounding ---------------------------------------------------------------------------------


def test_long_single_line_payload_is_truncated_and_flagged():
    """A single line that on its own exceeds the whole budget is still shown (capped), as
    exactly one unambiguous line index, rather than shown as nothing."""
    long_text = "x" * (DEFAULT_MAX_PAYLOAD_CHARS + 500)
    log = _log_item(long_text)
    bundle = _bundle(log)

    model_input = build_model_input(bundle, [])
    entry = model_input["evidence"][log.id]

    assert entry["payload_truncated"] is True
    assert entry["payload_original_chars"] == len(long_text)
    assert entry["payload"]["lines_total"] == 1
    assert entry["payload"]["truncated"] is True
    assert len(entry["payload"]["lines"]) == 1
    assert entry["payload"]["lines"][0] == {
        "line": 0, "text": "x" * DEFAULT_MAX_PAYLOAD_CHARS, "ref": make_line_ref(log.id, 0),
    }


def test_short_string_payload_is_not_truncated():
    log = _log_item("short log\n")
    bundle = _bundle(log)

    model_input = build_model_input(bundle, [])
    entry = model_input["evidence"][log.id]

    assert "payload_truncated" not in entry
    assert entry["payload"] == {
        "lines": [{"line": 0, "text": "short log", "ref": make_line_ref(log.id, 0)}], "lines_total": 1,
    }


def test_custom_max_payload_chars_is_respected():
    log = _log_item("0123456789")
    bundle = _bundle(log)

    model_input = build_model_input(bundle, [], max_payload_chars=5)
    entry = model_input["evidence"][log.id]

    assert entry["payload"]["lines"] == [{"line": 0, "text": "01234", "ref": make_line_ref(log.id, 0)}]
    assert entry["payload_truncated"] is True


def test_multiline_log_each_line_gets_its_own_explicit_zero_indexed_number():
    log = _log_item("alpha\nbeta\ngamma\n")
    bundle = _bundle(log)

    model_input = build_model_input(bundle, [])
    entry = model_input["evidence"][log.id]

    assert entry["payload"]["lines"] == [
        {"line": 0, "text": "alpha", "ref": make_line_ref(log.id, 0)},
        {"line": 1, "text": "beta", "ref": make_line_ref(log.id, 1)},
        {"line": 2, "text": "gamma", "ref": make_line_ref(log.id, 2)},
    ]
    assert entry["payload"]["lines_total"] == 3
    assert "truncated" not in entry["payload"]


def test_each_line_gets_a_distinct_stable_ref():
    """Refs are keyed by (evidence_id, line), not by text -- two identical lines at different
    positions must still get distinct refs (requirement 1), and the same bundle rebuilt twice
    must produce the exact same refs (requirement 2/reproducibility)."""
    log = _log_item("same text\nsame text\n")
    bundle = _bundle(log)

    first = build_model_input(bundle, [])["evidence"][log.id]["payload"]["lines"]
    second = build_model_input(bundle, [])["evidence"][log.id]["payload"]["lines"]

    assert first == second  # fully reproducible
    assert first[0]["text"] == first[1]["text"] == "same text"
    assert first[0]["ref"] != first[1]["ref"]  # distinct despite identical text


def test_multiline_log_truncation_drops_whole_trailing_lines_never_a_partial_line():
    """Truncation must never show a clipped partial line -- the model should never be handed
    a line it could then (correctly) quote in full, but which doesn't actually end there in
    the real bundle."""
    log = _log_item("alpha\nbeta\ngamma\ndelta\n")
    bundle = _bundle(log)

    # Budget fits "alpha" (5) + "beta" (4) = 9 exactly, nothing left for "gamma".
    model_input = build_model_input(bundle, [], max_payload_chars=9)
    entry = model_input["evidence"][log.id]

    assert entry["payload"]["lines"] == [
        {"line": 0, "text": "alpha", "ref": make_line_ref(log.id, 0)},
        {"line": 1, "text": "beta", "ref": make_line_ref(log.id, 1)},
    ]
    assert entry["payload"]["lines_total"] == 4
    assert entry["payload"]["truncated"] is True
    assert entry["payload_truncated"] is True


def test_truncated_away_line_never_gets_a_ref_anywhere_in_the_output():
    """Requirement 2/3: a line cut by truncation must never be referenceable at all -- not
    just absent from "lines", but its would-be ref must not appear anywhere in the output."""
    log = _log_item("alpha\nbeta\ngamma\n")
    bundle = _bundle(log)

    model_input = build_model_input(bundle, [], max_payload_chars=5)  # only "alpha" fits
    serialized = str(model_input)

    assert make_line_ref(log.id, 1) not in serialized  # "beta"'s ref must not appear
    assert make_line_ref(log.id, 2) not in serialized  # "gamma"'s ref must not appear


def test_non_present_evidence_item_has_no_payload_key():
    ref = ApiCallRef(verb="list", path="/events", params={})
    gap = EvidenceItem(id="gap-1", source_ref=ref, collected_at=_ts(), status=EvidenceStatus.NOT_FOUND)
    bundle = _bundle(gap)

    model_input = build_model_input(bundle, [])
    entry = model_input["evidence"][gap.id]

    assert "payload" not in entry
    assert entry["status"] == "NOT_FOUND"
