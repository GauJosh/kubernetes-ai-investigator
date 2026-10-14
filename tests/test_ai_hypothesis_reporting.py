"""Phase 0.12: AIHypothesisRunStatus -- a FAILED AI path must never look like a successful,
empty-hypothesis investigation, in the report model, the renderer, or the CLI.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from k8s_investigator.ai.client_factory import HypothesisClientConfigurationError, build_client_from_env
from k8s_investigator.cli import main
from k8s_investigator.models.report import AIHypothesisRunStatus, InvestigationReport
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


# --- client_factory: no real provider exists yet, fails descriptively, not silently -----------


def test_build_client_from_env_without_provider_raises(monkeypatch):
    monkeypatch.delenv("AI_HYPOTHESIS_PROVIDER", raising=False)
    with pytest.raises(HypothesisClientConfigurationError):
        build_client_from_env()


def test_build_client_from_env_with_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("AI_HYPOTHESIS_PROVIDER", "not-a-real-provider")
    with pytest.raises(HypothesisClientConfigurationError):
        build_client_from_env()


# --- CLI end-to-end: --with-ai-hypotheses without a configured provider is an honest FAILED ---


def test_cli_with_ai_hypotheses_without_provider_reports_failed_not_empty_success(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("AI_HYPOTHESIS_PROVIDER", raising=False)
    bundle = collect_and_build(scenarios.r1_dns_failure(), namespace="demo-ns", name="demo-pod")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    exit_code = main(
        [
            "investigate", "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", str(bundle_path), "--output", "json", "--with-ai-hypotheses",
        ]
    )

    assert exit_code == 0  # the deterministic report still ships successfully
    out = json.loads(capsys.readouterr().out)
    assert out["ai_hypothesis_status"] == "FAILED"
    assert out["ai_hypothesis_error"]
    assert out["ai_hypotheses"] == []
    # The deterministic finding is completely unaffected by the AI-path failure.
    assert out["deterministic_findings"][0]["produced_by"] == "rule:R1_dns_failure"


def test_cli_without_the_flag_is_not_attempted_and_unaffected(tmp_path, capsys):
    bundle = collect_and_build(scenarios.r1_dns_failure(), namespace="demo-ns", name="demo-pod")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    exit_code = main(
        [
            "investigate", "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", str(bundle_path), "--output", "json",
        ]
    )

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ai_hypothesis_status"] == "NOT_ATTEMPTED"
    assert out["ai_hypothesis_error"] is None


def test_cli_with_ai_hypotheses_succeeded_end_to_end(tmp_path, capsys, monkeypatch):
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
            "investigate", "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", str(bundle_path), "--output", "json", "--with-ai-hypotheses",
        ]
    )

    assert exit_code == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["ai_hypothesis_status"] == "SUCCEEDED"
    assert out["ai_hypothesis_error"] is None
    assert len(out["ai_hypotheses"]) == 1
    assert out["ai_hypotheses"][0]["produced_by"] == "model:fake@v1"
    assert len(fake_client.calls) == 1
