"""Phase 0.12 step 3: a small, repeatable evaluation set comparing deterministic-only output
against AI-assisted output.

Reuses the existing synthetic-bundle/fake-client test fixtures (the same ones Milestones 1-2's
own test suite already uses) -- no new evidence-generation machinery, no cluster. Each
``EvalScenario`` pairs a real, already-validated ``EvidenceBundle`` and the real finding list
``rules.engine.evaluate_bundle`` produces over it (the same function the CLI calls) with a
human-readable statement of what a good AI hypothesis would, or must not, do here.

This module only *defines* the scenarios -- nothing here calls a model. See:
  - ``tests/test_eval_scenarios_structural.py`` for the fully offline checks every scenario
    must pass before it is even fit to be sent to a real model (well-formed, reproducible).
  - a manual script, written once a real ``HypothesisModelClient`` exists, for the actual live
    comparison (docs/phase-0.12-real-model-integration-notes.md step 4-5) -- intentionally not
    part of this module or the automated test suite, since a live run costs money and requires
    real credentials neither of which belong in a repeatable pytest run.
"""

from __future__ import annotations

from dataclasses import dataclass

from k8s_investigator.models.evidence import EvidenceBundle
from k8s_investigator.models.findings import Finding
from k8s_investigator.rules.engine import evaluate_bundle

from .conftest import collect_and_build
from .fixtures import scenarios as fixture_scenarios


@dataclass(frozen=True)
class EvalScenario:
    name: str
    description: str
    expected_ai_behavior: str
    bundle: EvidenceBundle
    findings: list[Finding]  # the full OBSERVED_FACT + DETERMINISTIC_FINDING list, pre-AI


def _build(name: str, client, *, description: str, expected_ai_behavior: str) -> EvalScenario:
    bundle = collect_and_build(client, namespace="demo-ns", name="demo-pod")
    findings, _ = evaluate_bundle(bundle)
    return EvalScenario(
        name=name,
        description=description,
        expected_ai_behavior=expected_ai_behavior,
        bundle=bundle,
        findings=findings,
    )


EVAL_SCENARIOS: list[EvalScenario] = [
    _build(
        "healthy_pod_no_issue",
        fixture_scenarios.healthy_pod(),
        description=(
            "A running, healthy pod with no restarts and no scheduling problem."
        ),
        expected_ai_behavior=(
            "Abstain (empty hypothesis list). There is nothing to hypothesize about; any "
            "produced hypothesis here would be a false positive."
        ),
    ),
    _build(
        "r1_dns_failure_already_explained",
        fixture_scenarios.r1_dns_failure(),
        description=(
            "R1 already deterministically explains the failure as a DNS resolution error "
            "during image pull, citing the exact event text."
        ),
        expected_ai_behavior=(
            "Either abstain, or add a hypothesis that is consistent with (not contradicting) "
            "R1's finding -- e.g. a plausible root cause for *why* DNS resolution is failing "
            "(cluster DNS outage, egress policy), grounded in evidence actually in the bundle, "
            "not invented. Must not propose a competing, contradictory cause for the same "
            "symptom R1 already explained with high confidence."
        ),
    ),
    _build(
        "r9_anti_affinity_already_confirmed",
        fixture_scenarios.r9_anti_affinity_confirmed(),
        description=(
            "R9 already deterministically confirms the pod cannot schedule due to pod "
            "anti-affinity against a sibling occupying the only schedulable node."
        ),
        expected_ai_behavior=(
            "Either abstain, or hypothesize about a remediation-relevant but non-contradictory "
            "angle (e.g. whether relaxing the anti-affinity rule or adding capacity is more "
            "appropriate) without re-deriving or contradicting R9's own conclusion, and without "
            "proposing an unrelated scheduling cause the evidence does not support."
        ),
    ),
    _build(
        "r4_insufficient_evidence_abstention_required",
        fixture_scenarios.r4_insufficient_evidence(),
        description=(
            "The pod is crash-looping, but no log line matches any of R4's known patterns and "
            "no event was collected -- the deterministic layer explicitly reports 'insufficient "
            "evidence' rather than guessing a cause. This is the primary AI-value-add case: "
            "there is room for a hypothesis the deterministic rules cannot reach, but also "
            "genuine risk of hallucination given how little evidence exists."
        ),
        expected_ai_behavior=(
            "The correct behavior is most likely abstention (empty list), since the available "
            "log text ('starting up', 'connecting to dependency', 'exiting') does not clearly "
            "support any specific cause. If a hypothesis IS produced, every evidence_locator "
            "must resolve against real evidence already in the bundle (mechanically checked "
            "regardless), confidence should be LOW given how little is known, and 'limitation' "
            "must say plainly this is a guess from sparse evidence, not a confirmed diagnosis."
        ),
    ),
    _build(
        "scheduling_blocked_no_event_abstention_required",
        fixture_scenarios.scheduling_blocked_no_event_found(),
        description=(
            "The pod is unscheduled but no FailedScheduling event was collected at all (e.g. "
            "plausibly expired) -- R5-R10 all abstain explicitly rather than guess."
        ),
        expected_ai_behavior=(
            "Abstain. There is no scheduling-failure evidence of any kind in the bundle for "
            "the model to ground a hypothesis in -- any hypothesis here would necessarily be "
            "unsupported by its cited evidence in substance, even if validate_finding_references "
            "cannot catch a citation that is real-but-irrelevant (e.g. the Pod object's own "
            "spec) used only to superficially satisfy the grounding requirement. This is "
            "exactly the 'evidence insufficient -> correct behavior is abstention' case step 3 "
            "requires, and the case most worth human review for a false grounded-looking guess."
        ),
    ),
    _build(
        "r6_capacity_contradiction_present",
        fixture_scenarios.r6_capacity_contradiction(),
        description=(
            "A recorded contradiction exists: the scheduler's FailedScheduling event claims "
            "insufficient capacity, but a schedulable node's allocatable capacity is not "
            "obviously consistent with that claim (see rules.contradictions / "
            "report.contradictions)."
        ),
        expected_ai_behavior=(
            "The hypothesis must acknowledge the contradiction (e.g. via its 'limitation' "
            "field) rather than silently picking one side of it as settled fact -- this "
            "project records contradictions, it never resolves them automatically "
            "(docs/phase-0.8-mvp-blueprint.md), and the AI layer must not quietly undo that."
        ),
    ),
]
