"""Phase 0.12 step 3: fully offline, no-model-call checks that the evaluation set itself is
well-formed, reproducible, and ready to be sent to a real model -- no model call happens here.
"""

from __future__ import annotations

from k8s_investigator.ai.model_input import build_model_input
from k8s_investigator.canonical import content_hash
from k8s_investigator.rules.engine import evaluate_bundle
from k8s_investigator.validation import validate_bundle_integrity, validate_findings

from .eval_scenarios import EVAL_SCENARIOS


def test_eval_scenarios_set_is_non_empty_and_named_uniquely():
    assert len(EVAL_SCENARIOS) >= 5
    names = [s.name for s in EVAL_SCENARIOS]
    assert len(names) == len(set(names))


def test_every_scenario_bundle_has_integrity():
    for scenario in EVAL_SCENARIOS:
        validate_bundle_integrity(scenario.bundle)  # must not raise


def test_every_scenario_findings_validate_against_their_own_bundle():
    for scenario in EVAL_SCENARIOS:
        validate_findings(scenario.findings, scenario.bundle)  # must not raise


def test_every_scenario_builds_valid_model_input():
    for scenario in EVAL_SCENARIOS:
        model_input = build_model_input(scenario.bundle, scenario.findings)
        assert "evidence" in model_input
        assert "findings" in model_input
        assert model_input["bundle_id"] == scenario.bundle.id


def test_every_scenario_model_input_is_reproducible():
    for scenario in EVAL_SCENARIOS:
        first = content_hash(build_model_input(scenario.bundle, scenario.findings))
        second = content_hash(build_model_input(scenario.bundle, scenario.findings))
        assert first == second


def test_set_includes_at_least_two_explicit_abstention_required_scenarios():
    """Phase 0.12 step 3 explicitly requires: 'Include cases where the evidence is
    insufficient and the correct behavior is abstention.'"""
    abstention_scenarios = [s for s in EVAL_SCENARIOS if "abstention_required" in s.name]
    assert len(abstention_scenarios) >= 2
    for scenario in abstention_scenarios:
        lowered = scenario.expected_ai_behavior.lower()
        assert "abstain" in lowered or "abstention" in lowered


def test_set_includes_at_least_two_already_explained_scenarios():
    """A scenario where the deterministic layer already fully explains the failure -- a good
    AI hypothesis must not contradict or duplicate it."""
    already_explained = [s for s in EVAL_SCENARIOS if "already" in s.name]
    assert len(already_explained) >= 2
    for scenario in already_explained:
        assert len(scenario.findings) >= 1


def test_set_includes_a_healthy_no_issue_baseline():
    healthy = [s for s in EVAL_SCENARIOS if s.name == "healthy_pod_no_issue"]
    assert len(healthy) == 1


def test_set_includes_a_recorded_contradiction_scenario():
    contradiction_scenarios = [s for s in EVAL_SCENARIOS if "contradiction" in s.name]
    assert len(contradiction_scenarios) >= 1
    for scenario in contradiction_scenarios:
        _, contradictions = evaluate_bundle(scenario.bundle)
        assert len(contradictions) >= 1
