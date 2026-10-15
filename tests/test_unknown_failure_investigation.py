"""Phase 0.17: unknown-failure investigations -- a failure no deterministic rule (R1-R11)
recognizes must still produce an honest, useful report, and AI (when enabled) must be able to
propose a hypothesis for it without requiring a corresponding deterministic rule to exist.

All synthetic fixtures and FakeHypothesisModelClient responses -- no real provider, no network,
no live cluster.
"""

from __future__ import annotations

import json

from k8s_investigator.ai.hypothesis import generate_ai_hypotheses
from k8s_investigator.cli import main
from k8s_investigator.models.findings import FindingKind
from k8s_investigator.models.report import AIHypothesisOutcome, AIHypothesisRejectionReason
from k8s_investigator.report_builder import build_report
from k8s_investigator.rules.engine import evaluate_bundle

from .conftest import collect_and_build
from .fakes import FakeHypothesisModelClient
from .fixtures import scenarios


def _unfamiliar_failure_bundle():
    return collect_and_build(scenarios.unfamiliar_failure_no_matching_rule(), namespace="demo-ns", name="demo-pod")


# --- Deterministic-only: the honest "no rule matched" result ---------------------------------


def test_unfamiliar_failure_produces_no_deterministic_finding_but_names_the_gap_explicitly():
    bundle = _unfamiliar_failure_bundle()
    findings, _ = evaluate_bundle(bundle)

    deterministic = [f for f in findings if f.kind == FindingKind.DETERMINISTIC_FINDING]
    assert deterministic == []

    catch_all = [f for f in findings if f.produced_by == "engine:no_rule_matched"]
    assert len(catch_all) == 1
    assert catch_all[0].kind == FindingKind.OBSERVED_FACT
    assert "No deterministic rule" in catch_all[0].statement
    assert "does not mean no cause exists" in catch_all[0].statement
    assert len(catch_all[0].evidence_locators) >= 1  # grounded, like every other fact here


def test_unfamiliar_failure_next_steps_mention_ai_as_an_option():
    bundle = _unfamiliar_failure_bundle()
    findings, contradictions = evaluate_bundle(bundle)
    report = build_report(bundle, findings, contradictions)

    assert any("--with-ai" in step for step in report.next_steps)
    assert report.deterministic_findings == []
    assert report.evidence_gaps  # the container's logs were never collected for this scenario


def test_catch_all_never_fires_for_a_genuinely_healthy_pod():
    """The exact false-positive this check must never produce: a healthy pod running and
    ready needs no explanation, and must not be told "no rule matched a failure" when there
    is no failure."""
    bundle = collect_and_build(scenarios.healthy_pod(), namespace="demo-ns", name="demo-pod")
    findings, _ = evaluate_bundle(bundle)
    assert [f for f in findings if f.produced_by == "engine:no_rule_matched"] == []


def test_catch_all_does_not_fire_when_a_deterministic_rule_already_explains_the_failure():
    bundle = collect_and_build(scenarios.r1_dns_failure(), namespace="demo-ns", name="demo-pod")
    findings, _ = evaluate_bundle(bundle)
    assert [f for f in findings if f.produced_by == "engine:no_rule_matched"] == []
    assert any(f.produced_by == "rule:R1_dns_failure" for f in findings)


# --- AI enabled: a hypothesis CAN be accepted with no deterministic rule behind it -----------


def test_ai_hypothesis_accepted_for_an_unfamiliar_failure_with_no_deterministic_rule():
    """The core Part 1 requirement: AI must not require a corresponding deterministic rule to
    propose a plausible, evidence-grounded explanation for a pattern R1-R11 doesn't cover."""
    bundle = _unfamiliar_failure_bundle()
    findings, _ = evaluate_bundle(bundle)
    assert [f for f in findings if f.kind == FindingKind.DETERMINISTIC_FINDING] == []

    # Find the real configmap-missing event's evidence_id directly from the bundle.
    event_item = next(
        item for item in bundle.items
        if item.payload and isinstance(item.payload, dict) and "configmap" in str(item.payload.get("message", ""))
    )

    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [
            {
                "statement": "The container cannot start because a referenced ConfigMap is missing.",
                "evidence_locators": [
                    {
                        "evidence_id": event_item.id,
                        "pointer": {"pointer_type": "field_path", "json_pointer": "/message"},
                        "quoted_text": event_item.payload["message"],
                    }
                ],
                "confidence": "MEDIUM",
            }
        ]
    )

    result = generate_ai_hypotheses(bundle, findings, client, model_identifier="model:test@v1")

    assert result.outcome == AIHypothesisOutcome.ACCEPTED
    assert len(result.accepted) == 1
    assert result.accepted[0].confidence.value == "MEDIUM"
    assert result.rejection_reasons == []


def test_ai_high_confidence_still_rejected_without_any_deterministic_grounding():
    """Confirms the existing, unmodified confidence-grounding guard still applies here --
    adding unknown-failure support must never weaken it."""
    bundle = _unfamiliar_failure_bundle()
    findings, _ = evaluate_bundle(bundle)
    event_item = next(
        item for item in bundle.items
        if item.payload and isinstance(item.payload, dict) and "configmap" in str(item.payload.get("message", ""))
    )

    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [
            {
                "statement": "The container cannot start because a referenced ConfigMap is missing.",
                "evidence_locators": [
                    {
                        "evidence_id": event_item.id,
                        "pointer": {"pointer_type": "field_path", "json_pointer": "/message"},
                        "quoted_text": event_item.payload["message"],
                    }
                ],
                "confidence": "HIGH",
            }
        ]
    )

    result = generate_ai_hypotheses(bundle, findings, client, model_identifier="model:test@v1")

    assert result.outcome == AIHypothesisOutcome.ALL_REJECTED
    assert result.accepted == []
    assert result.rejection_reasons == [AIHypothesisRejectionReason.UNGROUNDED_CONFIDENCE]


# --- AI enabled: abstention and rejection are still distinguished -----------------------------


def test_ai_abstains_cleanly_for_the_unfamiliar_failure_scenario():
    bundle = _unfamiliar_failure_bundle()
    findings, _ = evaluate_bundle(bundle)
    client = FakeHypothesisModelClient()
    client.response = "[]"

    result = generate_ai_hypotheses(bundle, findings, client, model_identifier="model:test@v1")

    assert result.outcome == AIHypothesisOutcome.ABSTAINED
    assert result.accepted == []
    assert result.rejection_reasons == []


def test_ai_invalid_citation_is_rejected_not_repaired_for_the_unfamiliar_failure_scenario():
    bundle = _unfamiliar_failure_bundle()
    findings, _ = evaluate_bundle(bundle)
    client = FakeHypothesisModelClient()
    client.response = json.dumps(
        [
            {
                "statement": "A guess citing evidence that does not exist in this bundle.",
                "evidence_locators": [
                    {
                        "evidence_id": "does-not-exist-in-bundle",
                        "pointer": {"pointer_type": "field_path", "json_pointer": "/message"},
                        "quoted_text": "fabricated",
                    }
                ],
                "confidence": "LOW",
            }
        ]
    )

    result = generate_ai_hypotheses(bundle, findings, client, model_identifier="model:test@v1")

    assert result.outcome == AIHypothesisOutcome.ALL_REJECTED
    assert result.accepted == []
    assert result.rejection_reasons == [AIHypothesisRejectionReason.EVIDENCE_VALIDATION_FAILED]


# --- Deterministic reporting is identical whether AI is enabled or disabled ------------------


def test_cli_deterministic_findings_observed_facts_gaps_and_next_steps_unaffected_by_ai_toggle(
    tmp_path, capsys, monkeypatch
):
    bundle = _unfamiliar_failure_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")

    exit_code_without_ai = main(
        ["pod", "demo-pod", "--namespace", "demo-ns", "--from-bundle", str(bundle_path), "--output", "json"]
    )
    assert exit_code_without_ai == 0
    without_ai = json.loads(capsys.readouterr().out)

    from k8s_investigator.ai import client_factory

    fake_client = FakeHypothesisModelClient()
    fake_client.response = "[]"
    monkeypatch.setattr(client_factory, "build_client_from_env", lambda: (fake_client, "model:fake@v1"))

    exit_code_with_ai = main(
        [
            "pod", "demo-pod", "--namespace", "demo-ns",
            "--from-bundle", str(bundle_path), "--output", "json", "--with-ai",
        ]
    )
    assert exit_code_with_ai == 0
    with_ai = json.loads(capsys.readouterr().out)

    assert without_ai["deterministic_findings"] == with_ai["deterministic_findings"]
    assert without_ai["observed_facts"] == with_ai["observed_facts"]
    assert without_ai["evidence_gaps"] == with_ai["evidence_gaps"]
    assert without_ai["next_steps"] == with_ai["next_steps"]
    # Only the AI-specific fields differ between the two runs.
    assert without_ai["ai_hypothesis_status"] == "NOT_ATTEMPTED"
    assert with_ai["ai_hypothesis_status"] == "SUCCEEDED"
    assert with_ai["ai_hypothesis_outcome"] == "ABSTAINED"
