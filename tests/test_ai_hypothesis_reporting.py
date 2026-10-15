"""Phase 0.12: AIHypothesisRunStatus -- a FAILED AI path must never look like a successful,
empty-hypothesis investigation, in the report model, the renderer, or the CLI.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from k8s_investigator.ai.client_factory import HypothesisClientConfigurationError, build_client_from_env
from k8s_investigator.cli import main
from k8s_investigator.models.findings import Confidence, Finding, FindingKind
from k8s_investigator.models.locators import EvidenceLocator, FieldPath
from k8s_investigator.models.report import (
    AIHypothesisOutcome,
    AIHypothesisRejectionReason,
    AIHypothesisRunStatus,
    InvestigationReport,
)
from k8s_investigator.models.refs import ObjectRef, TimestampValue
from k8s_investigator.rendering import render_text
from k8s_investigator.report_builder import build_report

from .conftest import collect_and_build
from .fixtures import scenarios


def _ts() -> TimestampValue:
    return TimestampValue(value=datetime.now(timezone.utc), source_field="collectedAt")


def _minimal_report(**overrides) -> InvestigationReport:
    target = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")
    kwargs = dict(
        investigation_id="inv-1", target=target, bundle_id="bundle-1", generated_at=_ts(),
    )
    kwargs.update(overrides)
    return InvestigationReport(**kwargs)


# --- Report-model invariant ------------------------------------------------------------------


def test_default_status_is_not_attempted_with_no_error():
    report = _minimal_report()
    assert report.ai_hypothesis_status == AIHypothesisRunStatus.NOT_ATTEMPTED
    assert report.ai_hypothesis_error is None


def test_failed_status_without_an_error_message_is_rejected():
    with pytest.raises(ValueError):
        _minimal_report(ai_hypothesis_status=AIHypothesisRunStatus.FAILED, ai_hypothesis_error=None)


def test_non_failed_status_with_an_error_message_is_rejected():
    with pytest.raises(ValueError):
        _minimal_report(ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED, ai_hypothesis_error="oops")


def test_failed_status_with_an_error_message_is_valid():
    report = _minimal_report(
        ai_hypothesis_status=AIHypothesisRunStatus.FAILED, ai_hypothesis_error="no provider configured"
    )
    assert report.ai_hypothesis_status == AIHypothesisRunStatus.FAILED
    assert report.ai_hypothesis_error == "no provider configured"


# --- Phase 0.16: ai_hypothesis_outcome / ai_hypothesis_rejection_reasons pairing invariants -----


def _ai_hypothesis_finding(id_suffix: str = "1") -> Finding:
    return Finding(
        id=f"finding:ai-{id_suffix}",
        kind=FindingKind.AI_HYPOTHESIS,
        statement="A grounded hypothesis.",
        evidence_locators=[
            EvidenceLocator(
                evidence_id="item-1", pointer=FieldPath(json_pointer="/status/phase"), quoted_text="Pending"
            )
        ],
        produced_by="model:test@v1",
        confidence=Confidence.MEDIUM,
    )


def test_outcome_set_without_succeeded_status_is_rejected():
    with pytest.raises(ValueError):
        _minimal_report(
            ai_hypothesis_status=AIHypothesisRunStatus.NOT_ATTEMPTED,
            ai_hypothesis_outcome=AIHypothesisOutcome.ABSTAINED,
        )


def test_rejection_reasons_without_an_outcome_is_rejected():
    with pytest.raises(ValueError):
        _minimal_report(
            ai_hypothesis_rejection_reasons=[AIHypothesisRejectionReason.OTHER],
        )


def test_succeeded_with_no_outcome_is_still_valid_for_backward_compatibility():
    """Deliberately permitted: a SUCCEEDED report with no outcome recorded at all must not be
    rejected -- this is exactly the shape a report serialized before this field existed has."""
    report = _minimal_report(ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED)
    assert report.ai_hypothesis_outcome is None
    assert report.ai_hypothesis_rejection_reasons == []


def test_abstained_outcome_requires_empty_hypotheses_and_empty_reasons():
    report = _minimal_report(
        ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED,
        ai_hypothesis_outcome=AIHypothesisOutcome.ABSTAINED,
    )
    assert report.ai_hypotheses == []
    with pytest.raises(ValueError):
        _minimal_report(
            ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED,
            ai_hypothesis_outcome=AIHypothesisOutcome.ABSTAINED,
            ai_hypothesis_rejection_reasons=[AIHypothesisRejectionReason.OTHER],
        )


def test_accepted_outcome_requires_hypotheses_present_and_no_rejections():
    report = _minimal_report(
        ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED,
        ai_hypothesis_outcome=AIHypothesisOutcome.ACCEPTED,
        ai_hypotheses=[_ai_hypothesis_finding()],
    )
    assert len(report.ai_hypotheses) == 1
    with pytest.raises(ValueError):
        _minimal_report(
            ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED,
            ai_hypothesis_outcome=AIHypothesisOutcome.ACCEPTED,
            ai_hypotheses=[],  # ACCEPTED with nothing accepted is inconsistent
        )


def test_all_rejected_outcome_requires_no_hypotheses_and_some_reasons():
    report = _minimal_report(
        ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED,
        ai_hypothesis_outcome=AIHypothesisOutcome.ALL_REJECTED,
        ai_hypothesis_rejection_reasons=[AIHypothesisRejectionReason.EVIDENCE_VALIDATION_FAILED],
    )
    assert report.ai_hypotheses == []
    with pytest.raises(ValueError):
        _minimal_report(
            ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED,
            ai_hypothesis_outcome=AIHypothesisOutcome.ALL_REJECTED,
            ai_hypothesis_rejection_reasons=[],  # ALL_REJECTED with no reasons is inconsistent
        )


def test_partial_acceptance_outcome_requires_both_hypotheses_and_reasons():
    report = _minimal_report(
        ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED,
        ai_hypothesis_outcome=AIHypothesisOutcome.PARTIAL_ACCEPTANCE,
        ai_hypotheses=[_ai_hypothesis_finding()],
        ai_hypothesis_rejection_reasons=[AIHypothesisRejectionReason.UNGROUNDED_CONFIDENCE],
    )
    assert len(report.ai_hypotheses) == 1
    assert len(report.ai_hypothesis_rejection_reasons) == 1
    with pytest.raises(ValueError):
        _minimal_report(
            ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED,
            ai_hypothesis_outcome=AIHypothesisOutcome.PARTIAL_ACCEPTANCE,
            ai_hypotheses=[_ai_hypothesis_finding()],
            ai_hypothesis_rejection_reasons=[],  # PARTIAL with no rejections is inconsistent
        )


# --- build_report: existing call sites are unaffected -----------------------------------------


def test_build_report_default_matches_pre_phase_0_12_behavior():
    client = scenarios.healthy_pod()
    bundle = collect_and_build(client, namespace="demo-ns", name="demo-pod")
    report = build_report(bundle, [], [])
    assert report.ai_hypothesis_status == AIHypothesisRunStatus.NOT_ATTEMPTED
    assert report.ai_hypothesis_error is None
    assert report.ai_hypotheses == []


# --- Rendering: three states render distinctly -------------------------------------------------


def test_rendering_not_attempted():
    report = _minimal_report()
    text = render_text(report)
    assert "not attempted" in text
    assert "FAILED" not in text


def test_rendering_failed_shows_the_error_and_not_a_silent_empty_list():
    report = _minimal_report(
        ai_hypothesis_status=AIHypothesisRunStatus.FAILED, ai_hypothesis_error="model timed out"
    )
    text = render_text(report)
    assert "FAILED" in text
    assert "model timed out" in text
    assert "(none -- the model produced no hypothesis" not in text


def test_rendering_succeeded_with_no_hypotheses_is_distinct_from_failed():
    report = _minimal_report(ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED)
    text = render_text(report)
    assert "the model produced no hypothesis meeting the evidence bar" in text
    assert "FAILED" not in text


def test_rendering_abstained_outcome_is_distinct_from_the_generic_fallback_message():
    report = _minimal_report(
        ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED,
        ai_hypothesis_outcome=AIHypothesisOutcome.ABSTAINED,
    )
    text = render_text(report)
    assert "explicitly returned no candidates" in text
    assert "the model produced no hypothesis meeting the evidence bar" not in text


def test_rendering_all_rejected_outcome_names_the_reasons():
    report = _minimal_report(
        ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED,
        ai_hypothesis_outcome=AIHypothesisOutcome.ALL_REJECTED,
        ai_hypothesis_rejection_reasons=[
            AIHypothesisRejectionReason.EVIDENCE_VALIDATION_FAILED,
            AIHypothesisRejectionReason.UNGROUNDED_CONFIDENCE,
        ],
    )
    text = render_text(report)
    assert "2 candidate(s) rejected" in text
    assert "EVIDENCE_VALIDATION_FAILED" in text
    assert "UNGROUNDED_CONFIDENCE" in text


def test_rendering_partial_acceptance_shows_both_the_hypothesis_and_the_rejection_count():
    report = _minimal_report(
        ai_hypothesis_status=AIHypothesisRunStatus.SUCCEEDED,
        ai_hypothesis_outcome=AIHypothesisOutcome.PARTIAL_ACCEPTANCE,
        ai_hypotheses=[_ai_hypothesis_finding()],
        ai_hypothesis_rejection_reasons=[AIHypothesisRejectionReason.INVALID_CITATION_REFERENCE],
    )
    text = render_text(report)
    assert "A grounded hypothesis." in text
    assert "1 additional candidate(s) rejected" in text
    assert "INVALID_CITATION_REFERENCE" in text


# --- Backward compatibility: a report serialized before Phase 0.16 still parses and renders ----


def test_old_report_json_without_the_new_fields_still_parses_and_renders_unchanged():
    """Exactly the shape a report saved by a prior version of this project has: no
    ai_hypothesis_outcome or ai_hypothesis_rejection_reasons key at all, not even null/empty.
    Must still deserialize (defaults fill in) and render identically to before this change."""
    old_style_json = json.dumps(
        {
            "investigation_id": "inv-old",
            "target": {
                "ref_type": "object", "api_group": "", "api_version": "v1",
                "kind": "Pod", "namespace": "ns", "name": "p",
            },
            "bundle_id": "bundle-old",
            "generated_at": {"value": datetime.now(timezone.utc).isoformat(), "source_field": "collectedAt"},
            "ai_hypothesis_status": "SUCCEEDED",
            # no ai_hypothesis_outcome, no ai_hypothesis_rejection_reasons key at all
        }
    )

    report = InvestigationReport.model_validate_json(old_style_json)

    assert report.ai_hypothesis_outcome is None
    assert report.ai_hypothesis_rejection_reasons == []
    text = render_text(report)
    assert "the model produced no hypothesis meeting the evidence bar" in text


# --- client_factory: no real provider exists yet, fails descriptively, not silently -----------


def test_build_client_from_env_without_provider_raises(monkeypatch):
    monkeypatch.delenv("AI_HYPOTHESIS_PROVIDER", raising=False)
    with pytest.raises(HypothesisClientConfigurationError):
        build_client_from_env()


def test_build_client_from_env_with_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("AI_HYPOTHESIS_PROVIDER", "not-a-real-provider")
    with pytest.raises(HypothesisClientConfigurationError):
        build_client_from_env()


# --- CLI end-to-end: --with-ai without a configured provider is an honest FAILED -------------


def test_cli_with_ai_without_provider_reports_failed_not_empty_success(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("AI_HYPOTHESIS_PROVIDER", raising=False)
    bundle = collect_and_build(scenarios.r1_dns_failure(), namespace="demo-ns", name="demo-pod")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    exit_code = main(
        [
            "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", str(bundle_path), "--output", "json", "--with-ai",
        ]
    )

    assert exit_code == 0  # the deterministic report still ships successfully
    out = json.loads(capsys.readouterr().out)
    assert out["ai_hypothesis_status"] == "FAILED"
    assert out["ai_hypothesis_error"]
    assert out["ai_hypotheses"] == []
    assert out["ai_hypothesis_outcome"] is None  # a FAILED run never carries an outcome
    assert out["ai_hypothesis_rejection_reasons"] == []
    # The deterministic finding is completely unaffected by the AI-path failure.
    assert out["deterministic_findings"][0]["produced_by"] == "rule:R1_dns_failure"


def test_cli_with_ai_hypotheses_deprecated_alias_still_works_identically(tmp_path, capsys, monkeypatch):
    """`--with-ai-hypotheses` is kept as a trivial, behavior-identical compatibility alias for
    `--with-ai` -- same dest, same code path, nothing to keep in sync. This test exercises the
    OLD spelling specifically (every other test in this file already uses the new one)."""
    monkeypatch.delenv("AI_HYPOTHESIS_PROVIDER", raising=False)
    bundle = collect_and_build(scenarios.r1_dns_failure(), namespace="demo-ns", name="demo-pod")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    exit_code = main(
        [
            "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", str(bundle_path), "--output", "json", "--with-ai-hypotheses",
        ]
    )

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ai_hypothesis_status"] == "FAILED"  # identical outcome to the --with-ai test above
    assert out["ai_hypothesis_error"]
    assert out["deterministic_findings"][0]["produced_by"] == "rule:R1_dns_failure"


def test_cli_without_the_flag_is_not_attempted_and_unaffected(tmp_path, capsys):
    bundle = collect_and_build(scenarios.r1_dns_failure(), namespace="demo-ns", name="demo-pod")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    exit_code = main(
        [
            "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", str(bundle_path), "--output", "json",
        ]
    )

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ai_hypothesis_status"] == "NOT_ATTEMPTED"
    assert out["ai_hypothesis_error"] is None


def test_cli_with_ai_succeeded_end_to_end(tmp_path, capsys, monkeypatch):
    """With a (faked) configured client, the full opt-in path renders real hypotheses and a
    SUCCEEDED status -- proving the wiring end-to-end without needing a real provider yet."""
    import json as _json

    from k8s_investigator.ai import client_factory
    from tests.fakes import FakeHypothesisModelClient

    bundle = collect_and_build(scenarios.r1_dns_failure(), namespace="demo-ns", name="demo-pod")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    # Find the real deterministic finding's one evidence id to cite legitimately.
    from k8s_investigator.rules.engine import evaluate_bundle

    findings, _ = evaluate_bundle(bundle)
    deterministic = [f for f in findings if f.produced_by == "rule:R1_dns_failure"][0]
    evidence_id = deterministic.evidence_ids[0]

    fake_client = FakeHypothesisModelClient()
    fake_client.response = _json.dumps(
        [
            {
                "statement": "A hypothesis grounded in the same cited evidence.",
                "evidence_locators": [loc.model_dump(mode="json") for loc in deterministic.evidence_locators],
                "confidence": "MEDIUM",
            }
        ]
    )

    def _fake_builder():
        return fake_client, "model:fake@v1"

    monkeypatch.setattr(client_factory, "build_client_from_env", _fake_builder)

    exit_code = main(
        [
            "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", str(bundle_path), "--output", "json", "--with-ai",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    out = _json.loads(captured.out)
    assert out["ai_hypothesis_status"] == "SUCCEEDED"
    assert out["ai_hypothesis_error"] is None
    assert len(out["ai_hypotheses"]) == 1
    assert out["ai_hypotheses"][0]["produced_by"] == "model:fake@v1"
    assert out["ai_hypothesis_outcome"] == "ACCEPTED"
    assert out["ai_hypothesis_rejection_reasons"] == []
    assert len(fake_client.calls) == 1
    # The new Phase 0.16 stderr diagnostic line -- counts only, never raw candidate text.
    assert "AI hypothesis generation: ACCEPTED (1 accepted, 0 rejected)" in captured.err


def test_cli_with_ai_abstained_outcome_end_to_end(tmp_path, capsys, monkeypatch):
    from k8s_investigator.ai import client_factory
    from tests.fakes import FakeHypothesisModelClient

    bundle = collect_and_build(scenarios.r1_dns_failure(), namespace="demo-ns", name="demo-pod")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    fake_client = FakeHypothesisModelClient()
    fake_client.response = "[]"
    monkeypatch.setattr(client_factory, "build_client_from_env", lambda: (fake_client, "model:fake@v1"))

    exit_code = main(
        [
            "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", str(bundle_path), "--output", "json", "--with-ai",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["ai_hypothesis_status"] == "SUCCEEDED"
    assert out["ai_hypothesis_outcome"] == "ABSTAINED"
    assert out["ai_hypotheses"] == []
    assert out["ai_hypothesis_rejection_reasons"] == []
    assert "AI hypothesis generation: ABSTAINED (0 accepted, 0 rejected)" in captured.err


def test_cli_with_ai_all_rejected_outcome_end_to_end(tmp_path, capsys, monkeypatch):
    from k8s_investigator.ai import client_factory
    from tests.fakes import FakeHypothesisModelClient

    bundle = collect_and_build(scenarios.r1_dns_failure(), namespace="demo-ns", name="demo-pod")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    fake_client = FakeHypothesisModelClient()
    fake_client.response = json.dumps(
        [
            {
                "statement": "x",
                "evidence_locators": [
                    {
                        "evidence_id": "does-not-exist-in-bundle",
                        "pointer": {"pointer_type": "field_path", "json_pointer": "/status/phase"},
                        "quoted_text": "Pending",
                    }
                ],
                "confidence": "LOW",
            }
        ]
    )
    monkeypatch.setattr(client_factory, "build_client_from_env", lambda: (fake_client, "model:fake@v1"))

    exit_code = main(
        [
            "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", str(bundle_path), "--output", "json", "--with-ai",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["ai_hypothesis_status"] == "SUCCEEDED"
    assert out["ai_hypothesis_outcome"] == "ALL_REJECTED"
    assert out["ai_hypotheses"] == []
    assert out["ai_hypothesis_rejection_reasons"] == ["EVIDENCE_VALIDATION_FAILED"]
    assert "AI hypothesis generation: ALL_REJECTED (0 accepted, 1 rejected)" in captured.err


def test_cli_with_ai_rejected_candidate_credential_never_appears_in_output_or_stderr(
    tmp_path, capsys, monkeypatch
):
    """A rejected candidate's own statement, even if it happens to contain a synthetic
    credential-shaped string, must never reach stdout (the report) or stderr (the diagnostic
    summary) -- only the bounded rejection-reason enum value may."""
    from k8s_investigator.ai import client_factory
    from tests.fakes import FakeHypothesisModelClient

    fake_dsn = "postgresql://postgres:postgres@192.168.49.1:5432/devops_genai"
    bundle = collect_and_build(scenarios.r1_dns_failure(), namespace="demo-ns", name="demo-pod")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    fake_client = FakeHypothesisModelClient()
    fake_client.response = json.dumps(
        [
            {
                "statement": f"Likely caused by a bad connection string: {fake_dsn}",
                "evidence_locators": [
                    {
                        "evidence_id": "does-not-exist-in-bundle",
                        "pointer": {"pointer_type": "field_path", "json_pointer": "/status/phase"},
                        "quoted_text": "Pending",
                    }
                ],
                "confidence": "LOW",
            }
        ]
    )
    monkeypatch.setattr(client_factory, "build_client_from_env", lambda: (fake_client, "model:fake@v1"))

    exit_code = main(
        [
            "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", str(bundle_path), "--output", "both", "--with-ai",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert fake_dsn not in captured.out
    assert fake_dsn not in captured.err
    assert "postgres:postgres" not in captured.out
    assert "postgres:postgres" not in captured.err
    assert "EVIDENCE_VALIDATION_FAILED" in captured.out  # the bounded reason is still visible
