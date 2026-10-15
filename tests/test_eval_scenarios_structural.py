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


def test_every_scenario_builds_valid_model_input_with_its_own_contradictions():
    """Confirms the real eval harness's own contradiction data (not just synthetic unit
    fixtures) threads through build_model_input without error -- in particular,
    r6_capacity_contradiction_present's real recorded contradiction."""
    for scenario in EVAL_SCENARIOS:
        model_input = build_model_input(
            scenario.bundle, scenario.findings, contradictions=scenario.contradictions
        )
        assert "contradictions" in model_input
        assert len(model_input["contradictions"]) == len(scenario.contradictions)


def test_r6_scenario_contradiction_is_actually_present_and_shown_to_the_model():
    scenario = next(s for s in EVAL_SCENARIOS if s.name == "r6_capacity_contradiction_present")
    assert len(scenario.contradictions) >= 1
    model_input = build_model_input(
        scenario.bundle, scenario.findings, contradictions=scenario.contradictions
    )
    assert len(model_input["contradictions"]) >= 1


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


def test_set_includes_an_underdetermined_partial_evidence_scenario():
    """Distinct from the zero-evidence abstention scenarios: some evidence exists, but the
    deterministic layer explicitly says it does not fully explain the failure -- a different
    uncertainty shape, not just 'nothing was found at all'."""
    scenario = next(s for s in EVAL_SCENARIOS if s.name == "r5_partial_cordon_underdetermined")
    det = [f for f in scenario.findings if f.kind.value == "DETERMINISTIC_FINDING"]
    assert det == []  # no confirmed cause
    assert len(scenario.findings) >= 1  # but evidence/observed facts do exist


def test_set_covers_at_least_three_distinct_deterministic_rules_among_already_explained_cases():
    """Breadth check: 'already explained' coverage must span more than one rule, so a model
    generalizing 'do not contradict a deterministic finding' is tested across causes, not
    just the one rule the original set happened to include."""
    already_explained = [s for s in EVAL_SCENARIOS if "already" in s.name]
    rule_ids = {f.produced_by for s in already_explained for f in s.findings if f.kind.value == "DETERMINISTIC_FINDING"}
    assert len(rule_ids) >= 3
