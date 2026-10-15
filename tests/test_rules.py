"""R1-R4 trigger and abstention behavior, negative controls, reproducibility, contradictions."""

from __future__ import annotations

from k8s_investigator.models.findings import FindingKind
from k8s_investigator.rules.engine import evaluate_bundle

from .conftest import collect_and_build
from .fixtures import scenarios


def _deterministic(findings):
    return [f for f in findings if f.kind == FindingKind.DETERMINISTIC_FINDING]


def _observed(findings):
    return [f for f in findings if f.kind == FindingKind.OBSERVED_FACT]


def test_healthy_pod_produces_no_deterministic_findings():
    bundle = collect_and_build(scenarios.healthy_pod())
    findings, contradictions = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    assert contradictions == []
    # but it should still produce baseline observed facts (owner chain, container state)
    assert len(_observed(findings)) > 0


def test_r1_dns_failure_fires():
    bundle = collect_and_build(scenarios.r1_dns_failure())
    findings, _ = evaluate_bundle(bundle)
    det = _deterministic(findings)
    assert len(det) == 1
    assert det[0].produced_by == "rule:R1_dns_failure"
    assert "DNS resolution failure" in det[0].statement


def test_r2_tag_not_found_fires():
    bundle = collect_and_build(scenarios.r2_tag_not_found())
    findings, _ = evaluate_bundle(bundle)
    det = _deterministic(findings)
    assert len(det) == 1
    assert det[0].produced_by == "rule:R2_tag_not_found"
    assert "not found" in det[0].statement


def test_r1_r2_r3_unrecognized_pull_failure_abstains_explicitly():
    """Phase 0.15: an ImagePullBackOff event IS present, but matches none of R1/R2/R3's
    recognized patterns -- the shared fallback behind the entire image-pull-failure family,
    previously untested. Must abstain with an honest OBSERVED_FACT, never a guessed cause,
    and never silently drop the event."""
    bundle = collect_and_build(scenarios.r1_r2_r3_unrecognized_pull_failure())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    matching = [f for f in _observed(findings) if f.produced_by == "rule:image_pull_unrecognized"]
    assert len(matching) == 1
    assert "none of the 1 event(s) collected matched a recognized failure pattern" in matching[0].statement
    assert "toomanyrequests" not in matching[0].statement  # the statement describes, doesn't invent a cause


def test_r3_no_secret_anywhere_high_confidence_wording():
    bundle = collect_and_build(scenarios.r3_no_secret_anywhere())
    findings, _ = evaluate_bundle(bundle)
    det = _deterministic(findings)
    assert len(det) == 1
    assert det[0].produced_by == "rule:R3_auth_failure_no_secret"
    assert "no image pull secret is configured" in det[0].statement


def test_r3_secret_on_service_account_produces_weaker_finding():
    bundle = collect_and_build(scenarios.r3_secret_configured_on_service_account())
    findings, _ = evaluate_bundle(bundle)
    det = _deterministic(findings)
    assert len(det) == 1
    assert det[0].produced_by == "rule:R3_auth_failure_secret_configured"
    assert "sa-registry-secret" in det[0].statement
    assert det[0].limitation is not None  # the Secret-exclusion honesty caveat must be present


def test_r3_correction_1_pod_level_secret_is_not_missed():
    """The exact bug correction 1 fixes: a secret referenced only on the Pod, not the
    ServiceAccount, must NOT produce a 'no secret configured' finding."""
    bundle = collect_and_build(scenarios.r3_secret_configured_on_pod_only())
    findings, _ = evaluate_bundle(bundle)
    det = _deterministic(findings)
    assert len(det) == 1
    assert det[0].produced_by == "rule:R3_auth_failure_secret_configured"
    assert "pod-level-secret" in det[0].statement
    assert "no image pull secret" not in det[0].statement


def test_r3_service_account_unavailable_abstains_with_limitation_not_a_guess():
    bundle = collect_and_build(scenarios.r3_service_account_unavailable())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []  # must NOT claim "no secret configured"
    observed = _observed(findings)
    matching = [f for f in observed if f.produced_by == "rule:R3_auth_failure_sa_unavailable"]
    assert len(matching) == 1
    assert "cannot be fully established" in matching[0].statement


def test_r4_log_pattern_match_cites_specific_line():
    bundle = collect_and_build(scenarios.r4_log_pattern_match())
    findings, _ = evaluate_bundle(bundle)
    det = _deterministic(findings)
    assert len(det) == 1
    assert det[0].produced_by == "rule:R4_log_pattern_match"
    assert "Permission denied" in det[0].statement
    # the log-line locator's quoted_text must be the specific matching line, not the whole chunk
    log_locators = [loc for loc in det[0].evidence_locators if loc.pointer.pointer_type == "line_range"]
    assert len(log_locators) == 1
    assert "Permission denied" in log_locators[0].quoted_text
    assert log_locators[0].quoted_text.count("\n") == 0  # exactly one line, not the whole chunk


def test_r4_insufficient_evidence_is_explicit_not_silent():
    bundle = collect_and_build(scenarios.r4_insufficient_evidence())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    observed = _observed(findings)
    matching = [f for f in observed if f.produced_by == "rule:R4_insufficient_evidence"]
    assert len(matching) == 1
    assert "insufficient" in matching[0].statement.lower()


def test_r4_logs_unavailable_does_not_guess():
    bundle = collect_and_build(scenarios.r4_logs_unavailable())
    findings, _ = evaluate_bundle(bundle)
    assert _deterministic(findings) == []
    observed = _observed(findings)
    matching = [f for f in observed if f.produced_by == "rule:R4_logs_unavailable"]
    assert len(matching) == 1
    assert "not available" in matching[0].statement


def test_contradiction_detected_for_running_vs_failure_event():
    bundle = collect_and_build(scenarios.contradiction_running_vs_failure_event())
    findings, contradictions = evaluate_bundle(bundle)
    assert len(contradictions) == 1
    assert contradictions[0].rule_id == "contradiction:running_vs_failure_event"


def test_partial_failure_does_not_block_unrelated_rule():
    """R1 doesn't need the ServiceAccount at all -- an unavailable SA must not stop it firing."""
    bundle = collect_and_build(scenarios.service_account_unavailable_but_r1_still_resolves())
    findings, _ = evaluate_bundle(bundle)
    det = _deterministic(findings)
    assert len(det) == 1
    assert det[0].produced_by == "rule:R1_dns_failure"


def test_reproducibility_identical_bundle_identical_findings():
    """The deterministic-reproducibility contract: running the rule engine twice against the
    same bundle object must produce identical findings (same ids, same content) every time."""
    bundle = collect_and_build(scenarios.r1_dns_failure())
    findings_a, contradictions_a = evaluate_bundle(bundle)
    findings_b, contradictions_b = evaluate_bundle(bundle)
    assert [f.id for f in findings_a] == [f.id for f in findings_b]
    assert findings_a == findings_b
    assert contradictions_a == contradictions_b


# --- R11 (Runtime-health-fault, Phase 0.13) ---------------------------------------------------


def _r11(findings):
    return [f for f in findings if f.produced_by.startswith("rule:R11_")]


def test_r11_liveness_probe_failure_confirmed_fires():
    bundle = collect_and_build(scenarios.r11_liveness_probe_failure_confirmed())
    findings, _ = evaluate_bundle(bundle)
    r11 = _r11(findings)
    assert len(r11) == 1
    assert r11[0].kind == FindingKind.DETERMINISTIC_FINDING
    assert r11[0].produced_by == "rule:R11_liveness_probe_failure"
    assert "liveness-probe-failure" in r11[0].statement.lower()
    assert "restartcount of 3" in r11[0].statement.lower()
    assert r11[0].limitation is not None
    assert "does not establish why the probe is failing" in r11[0].limitation


def test_r11_readiness_probe_failure_confirmed_fires():
    bundle = collect_and_build(scenarios.r11_readiness_probe_failure_confirmed())
    findings, _ = evaluate_bundle(bundle)
    r11 = _r11(findings)
    assert len(r11) == 1
    assert r11[0].kind == FindingKind.DETERMINISTIC_FINDING
    assert r11[0].produced_by == "rule:R11_readiness_probe_failure"
    assert "readiness-probe-failure" in r11[0].statement.lower()
    assert r11[0].limitation is not None


def test_r11_unrecognized_probe_message_abstains():
    bundle = collect_and_build(scenarios.r11_probe_type_unrecognized())
    findings, _ = evaluate_bundle(bundle)
    r11 = _r11(findings)
    assert len(r11) == 1
    assert r11[0].kind == FindingKind.OBSERVED_FACT
    assert r11[0].produced_by == "rule:R11_probe_failure_unrecognized"


def test_r11_container_not_identified_abstains():
    bundle = collect_and_build(scenarios.r11_container_not_identified())
    findings, _ = evaluate_bundle(bundle)
    r11 = _r11(findings)
    assert len(r11) == 1
    assert r11[0].kind == FindingKind.OBSERVED_FACT
    assert r11[0].produced_by == "rule:R11_container_not_identified"


def test_r11_status_not_corroborating_is_observed_not_deterministic():
    bundle = collect_and_build(scenarios.r11_status_not_corroborating())
    findings, _ = evaluate_bundle(bundle)
    r11 = _r11(findings)
    assert len(r11) == 1
    assert r11[0].kind == FindingKind.OBSERVED_FACT
    assert r11[0].produced_by == "rule:R11_status_not_corroborating"


def test_r11_unrelated_events_produce_no_finding():
    bundle = collect_and_build(scenarios.r11_unrelated_events_ignored())
    findings, _ = evaluate_bundle(bundle)
    assert _r11(findings) == []


def test_r11_liveness_and_readiness_both_fire_independently():
    bundle = collect_and_build(scenarios.r11_liveness_and_readiness_both_present())
    findings, _ = evaluate_bundle(bundle)
    r11 = _r11(findings)
    assert len(r11) == 2
    rule_ids = {f.produced_by for f in r11}
    assert rule_ids == {"rule:R11_liveness_probe_failure", "rule:R11_readiness_probe_failure"}
    assert all(f.kind == FindingKind.DETERMINISTIC_FINDING for f in r11)


def test_r11_coexists_with_r4_log_pattern():
    """R11 and R4 are complementary, not competing explanations -- both must fire together."""
    bundle = collect_and_build(scenarios.r11_coexists_with_r4_log_pattern())
    findings, _ = evaluate_bundle(bundle)
    det = _deterministic(findings)
    rule_ids = {f.produced_by for f in det}
    assert "rule:R4_log_pattern_match" in rule_ids
    assert "rule:R11_liveness_probe_failure" in rule_ids
