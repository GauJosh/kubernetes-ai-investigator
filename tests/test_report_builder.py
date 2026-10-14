"""The report builder: validation-before-inclusion, evidence gaps, next steps."""

from __future__ import annotations

import pytest

from k8s_investigator.errors import EvidenceReferenceError
from k8s_investigator.models.evidence import EvidenceStatus
from k8s_investigator.models.findings import Finding, FindingKind
from k8s_investigator.models.locators import EvidenceLocator, FieldPath
from k8s_investigator.report_builder import build_report
from k8s_investigator.rules.engine import evaluate_bundle

from .conftest import collect_and_build
from .fixtures import scenarios


def test_report_for_healthy_pod_has_no_findings_and_no_gaps():
    bundle = collect_and_build(scenarios.healthy_pod())
    findings, contradictions = evaluate_bundle(bundle)
    report = build_report(bundle, findings, contradictions)
    assert report.deterministic_findings == []
    assert report.ai_hypotheses == []
    assert report.evidence_gaps == []
    assert report.bundle_id == bundle.id


def test_report_records_evidence_gap_for_unavailable_service_account():
    bundle = collect_and_build(scenarios.r3_service_account_unavailable())
    findings, contradictions = evaluate_bundle(bundle)
    report = build_report(bundle, findings, contradictions)
    gap_kinds = [getattr(g.source_ref, "kind", None) for g in report.evidence_gaps]
    assert "ServiceAccount" in gap_kinds
    sa_gap = next(g for g in report.evidence_gaps if getattr(g.source_ref, "kind", None) == "ServiceAccount")
    assert sa_gap.status == EvidenceStatus.UNAVAILABLE
    assert any("ServiceAccount" in step for step in report.next_steps)


def test_report_surfaces_expired_event_gap_with_next_step():
    bundle = collect_and_build(scenarios.events_plausibly_expired())
    findings, contradictions = evaluate_bundle(bundle)
    report = build_report(bundle, findings, contradictions)
    expired_gaps = [g for g in report.evidence_gaps if g.status == EvidenceStatus.EXPIRED]
    assert len(expired_gaps) == 1
    assert any("TTL" in step for step in report.next_steps)


def test_build_report_rejects_a_finding_with_invalid_evidence_reference():
    bundle = collect_and_build(scenarios.healthy_pod())
    bad_finding = Finding(
        id="bad",
        kind=FindingKind.DETERMINISTIC_FINDING,
        statement="fabricated",
        evidence_locators=[EvidenceLocator(evidence_id="not-in-bundle", pointer=FieldPath(json_pointer="/x"), quoted_text="y")],
        produced_by="rule:test",
    )
    with pytest.raises(EvidenceReferenceError):
        build_report(bundle, [bad_finding], [])
