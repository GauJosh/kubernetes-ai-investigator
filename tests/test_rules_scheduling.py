"""R5-R10 (Milestone 2, Scheduling-fault category): positive cases, negative/underdetermined
controls, missing evidence, partial collection failure, and contradictions.

Preserves the same test shape as tests/test_rules.py (R1-R4) -- see that file for the
reproducibility and healthy-pod-negative-control tests, which are not repeated here since
rules/engine.py's evaluate_bundle is one shared pipeline for both rule families.
"""

from __future__ import annotations

from k8s_investigator.models.findings import FindingKind
from k8s_investigator.rules.engine import evaluate_bundle

from .conftest import collect_and_build
from .fixtures import scenarios


def _deterministic(findings):
    return [f for f in findings if f.kind == FindingKind.DETERMINISTIC_FINDING]


def _observed(findings):
    return [f for f in findings if f.kind == FindingKind.OBSERVED_FACT]


# --- R5: Node cordon --------------------------------------------------------------------------


def test_r5_all_nodes_cordoned_fires_high_confidence():
    bundle = collect_and_build(scenarios.r5_all_nodes_cordoned())
    findings, _ = evaluate_bundle(bundle)
    det = [f for f in _deterministic(findings) if f.produced_by == "rule:R5_all_nodes_cordoned"]
    assert len(det) == 1
    assert "All 4 known node(s) are cordoned" in det[0].statement


def test_r5_partial_cordon_is_underdetermined_not_confident():
    """Only the always-cordoned control-plane node is cordoned -- must not claim this alone
    explains the failure."""
    bundle = collect_and_build(scenarios.r5_partial_cordon_underdetermined())
    findings, _ = evaluate_bundle(bundle)
    assert not any(f.produced_by == "rule:R5_all_nodes_cordoned" for f in _deterministic(findings))
    matching = [f for f in _observed(findings) if f.produced_by == "rule:R5_partial_cordon"]
    assert len(matching) == 1
    assert "not all" in matching[0].statement


def test_r5_abstains_when_node_collection_unavailable():
    """Partial collection failure: the event exists, but the Node list call itself failed."""
    bundle = collect_and_build(scenarios.scheduling_blocked_nodes_unavailable())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    matching = [f for f in _observed(findings) if f.produced_by == "rule:R5_nodes_unavailable"]
    assert len(matching) == 1
    assert "could not be collected" in matching[0].statement


# --- R6: Resource capacity mismatch -------------------------------------------------------------


def test_r6_insufficient_cpu_cites_pod_request_and_node_allocatable():
    bundle = collect_and_build(scenarios.r6_insufficient_cpu())
    findings, contradictions = evaluate_bundle(bundle)
    det = [f for f in _deterministic(findings) if f.produced_by == "rule:R6_resource_capacity"]
    assert len(det) == 1
    assert "server=16" in det[0].statement
    assert "worker-0=4" in det[0].statement or "4" in det[0].statement
    # genuinely insufficient (16 requested vs. 4 allocatable) -- no contradiction should fire
    assert contradictions == []


def test_r6_insufficient_memory_fires():
    bundle = collect_and_build(scenarios.r6_insufficient_memory())
    findings, _ = evaluate_bundle(bundle)
    det = [f for f in _deterministic(findings) if f.produced_by == "rule:R6_resource_capacity"]
    assert len(det) == 1
    assert "memory" in det[0].statement.lower()


def test_r6_does_not_claim_independent_proof_of_insufficiency():
    """The rule must cite the scheduler's own report as the authoritative claim, not re-derive
    'insufficient' itself from allocatable-vs-requested (allocatable != available)."""
    bundle = collect_and_build(scenarios.r6_insufficient_cpu())
    findings, _ = evaluate_bundle(bundle)
    det = [f for f in _deterministic(findings) if f.produced_by == "rule:R6_resource_capacity"][0]
    assert det.limitation is not None
    assert "available* capacity" in det.limitation


def test_r6_capacity_contradiction_detected_and_not_silently_resolved():
    bundle = collect_and_build(scenarios.r6_capacity_contradiction())
    findings, contradictions = evaluate_bundle(bundle)
    # the rule still fires (citing the scheduler's own claim) ...
    assert len(_deterministic(findings)) == 1
    # ... but the contradiction is also recorded, not hidden
    assert len(contradictions) == 1
    assert contradictions[0].rule_id == "contradiction:insufficient_capacity_vs_node_allocatable"


# --- R7: Taint/toleration mismatch --------------------------------------------------------------


def test_r7_taint_mismatch_quotes_exact_taint():
    bundle = collect_and_build(scenarios.r7_taint_toleration_mismatch())
    findings, _ = evaluate_bundle(bundle)
    det = [f for f in _deterministic(findings) if f.produced_by == "rule:R7_taint_toleration_mismatch"]
    assert len(det) == 1
    assert "critical=true:NoSchedule" in det[0].statement


# --- R8: Node selector / affinity mismatch -------------------------------------------------------


def test_r8_node_selector_mismatch_cites_contradicting_node_label():
    bundle = collect_and_build(scenarios.r8_node_selector_mismatch())
    findings, _ = evaluate_bundle(bundle)
    det = [f for f in _deterministic(findings) if f.produced_by == "rule:R8_node_selector_mismatch"]
    assert len(det) == 1
    assert "environment=production" in det[0].statement
    assert "environment=staging" in det[0].statement  # the contradicting node label


def test_r8_node_affinity_mismatch_resolves_what_phase_0_6_could_not():
    """Phase 0.6 (docs/research/cloud-opsbench-evidence-feasibility.md §4) found this case only
    partially resolvable from kubectl-describe text; this project's structured-JSON collector
    should resolve it fully -- the design expectation stated in the blueprint, confirmed here."""
    bundle = collect_and_build(scenarios.r8_node_affinity_mismatch())
    findings, _ = evaluate_bundle(bundle)
    det = [f for f in _deterministic(findings) if f.produced_by == "rule:R8_node_affinity_mismatch"]
    assert len(det) == 1
    assert "environment In [production" in det[0].statement


def test_r8_abstains_when_no_requirement_found_on_pod():
    """The scheduler claims a mismatch, but neither nodeSelector nor affinity exists on the
    Pod -- must not fabricate a requirement."""
    bundle = collect_and_build(scenarios.r8_neither_selector_nor_affinity_present())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    matching = [f for f in _observed(findings) if f.produced_by == "rule:R8_no_requirement_found"]
    assert len(matching) == 1


# --- R9: Pod anti-affinity conflict (completed -- R9 follow-up) ---------------------------------


def test_r9_confirmed_when_sibling_occupies_known_schedulable_node():
    """The positive case: now a confident DETERMINISTIC_FINDING, grounded in real sibling-pod
    placement evidence, not the scheduler's message alone."""
    bundle = collect_and_build(scenarios.r9_anti_affinity_confirmed())
    findings, _ = evaluate_bundle(bundle)
    det = [f for f in _deterministic(findings) if f.produced_by == "rule:R9_anti_affinity_confirmed"]
    assert len(det) == 1
    assert "worker-0" in det[0].statement
    # the evidence must include the sibling's own nodeName field, not just the event
    assert len(det[0].evidence_locators) >= 2


def test_r9_abstains_without_replica_set():
    """Missing evidence: ownership (the selector source) could not be read."""
    bundle = collect_and_build(scenarios.r9_replica_set_unavailable())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    matching = [f for f in _observed(findings) if f.produced_by == "rule:R9_replica_set_unavailable"]
    assert len(matching) == 1


def test_r9_abstains_without_usable_selector():
    bundle = collect_and_build(scenarios.r9_no_selector_on_replica_set())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    matching = [f for f in _observed(findings) if f.produced_by == "rule:R9_no_selector"]
    assert len(matching) == 1


def test_r9_abstains_when_sibling_collection_fails():
    """Partial collection failure: the selector is known, but the LIST call itself failed."""
    bundle = collect_and_build(scenarios.r9_siblings_unavailable())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    matching = [f for f in _observed(findings) if f.produced_by == "rule:R9_siblings_unavailable"]
    assert len(matching) == 1


def test_r9_flags_inconsistency_when_no_siblings_found():
    """Zero matching siblings is itself inconsistent with an anti-affinity conflict claim --
    must be named as such, not silently treated as 'confirmed' or 'unknown.'"""
    bundle = collect_and_build(scenarios.r9_no_siblings_found())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    matching = [f for f in _observed(findings) if f.produced_by == "rule:R9_no_siblings_found"]
    assert len(matching) == 1
    assert "inconsistent" in matching[0].statement


def test_r9_abstains_when_no_sibling_is_placed():
    bundle = collect_and_build(scenarios.r9_siblings_not_placed())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    matching = [f for f in _observed(findings) if f.produced_by == "rule:R9_no_placed_siblings"]
    assert len(matching) == 1


def test_r9_abstains_when_sibling_on_unknown_node():
    """A sibling is placed, but not on any node this project has evidence about as
    schedulable -- must not be silently counted as confirming or refuting the conflict."""
    bundle = collect_and_build(scenarios.r9_siblings_not_on_known_schedulable_node())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    matching = [f for f in _observed(findings) if f.produced_by == "rule:R9_siblings_not_on_known_schedulable_node"]
    assert len(matching) == 1


def test_r9_evidence_locators_cite_sibling_node_name_field():
    """The evidence-first requirement, explicit: a confirmed R9 finding's locators must
    resolve to real, validated fields on the actual sibling-pod evidence item, not just the
    triggering event."""
    bundle = collect_and_build(scenarios.r9_anti_affinity_confirmed())
    findings, _ = evaluate_bundle(bundle)
    det = [f for f in _deterministic(findings) if f.produced_by == "rule:R9_anti_affinity_confirmed"][0]
    from k8s_investigator.validation import validate_finding_references
    validate_finding_references(det, bundle)  # must not raise
    node_name_locators = [loc for loc in det.evidence_locators if loc.quoted_text == "worker-0"]
    assert len(node_name_locators) == 1


# --- R10: PV binding occupied --------------------------------------------------------------------


def test_r10_pv_bound_elsewhere_distinguishes_from_no_match():
    bundle = collect_and_build(scenarios.r10_pv_bound_elsewhere())
    findings, _ = evaluate_bundle(bundle)
    det = [f for f in _deterministic(findings) if f.produced_by == "rule:R10_pv_bound_elsewhere"]
    assert len(det) == 1
    assert "other-pvc" in det[0].statement


def test_r10_no_matching_pv_is_a_distinct_outcome():
    bundle = collect_and_build(scenarios.r10_no_matching_pv())
    findings, _ = evaluate_bundle(bundle)
    det = [f for f in _deterministic(findings) if f.produced_by == "rule:R10_no_matching_pv"]
    assert len(det) == 1
    assert "no persistentvolume" in det[0].statement.lower()
    # must not be conflated with the "bound elsewhere" outcome
    assert not any(f.produced_by == "rule:R10_pv_bound_elsewhere" for f in det)


# --- Missing evidence: no FailedScheduling event at all -------------------------------------------


def test_scheduling_abstains_entirely_when_no_event_found():
    bundle = collect_and_build(scenarios.scheduling_blocked_no_event_found())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    matching = [f for f in _observed(findings) if f.produced_by == "rule:scheduling_no_event"]
    assert len(matching) == 1
    assert "cannot be determined" in matching[0].statement


# --- Reproducibility, same contract as R1-R4 ------------------------------------------------------


def test_scheduling_findings_are_reproducible():
    bundle = collect_and_build(scenarios.r7_taint_toleration_mismatch())
    findings_a, contradictions_a = evaluate_bundle(bundle)
    findings_b, contradictions_b = evaluate_bundle(bundle)
    assert [f.id for f in findings_a] == [f.id for f in findings_b]
    assert findings_a == findings_b
    assert contradictions_a == contradictions_b


# --- R1-R4 behavior must remain intact for a scheduling-blocked pod (no container started) --------


def test_scheduling_blocked_pod_produces_no_r1_r4_findings():
    """An unscheduled pod has no containerStatuses -- R1-R4's per-container loop must find
    nothing to evaluate, not error or produce a spurious finding."""
    bundle = collect_and_build(scenarios.r5_all_nodes_cordoned())
    findings, _ = evaluate_bundle(bundle)
    r1_to_r4_rule_ids = {
        "rule:R1_dns_failure", "rule:R2_tag_not_found",
        "rule:R3_auth_failure_no_secret", "rule:R3_auth_failure_secret_configured",
        "rule:R4_log_pattern_match", "rule:R4_insufficient_evidence",
    }
    assert not any(f.produced_by in r1_to_r4_rule_ids for f in findings)
