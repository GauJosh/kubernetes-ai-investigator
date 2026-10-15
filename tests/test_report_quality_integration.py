"""Phase 0.16: a focused report-quality integration test.

Everything up to this point tests the deterministic rule engine's correctness (tests/
test_rules*.py), the report builder's own mechanics in isolation (tests/test_report_builder.py,
each case built from a single-signal fixture), or that rendered-text section *headers* exist
(tests/test_cli.py). None of that proves the final report -- object and rendered text alike --
correctly keeps a DETERMINISTIC_FINDING, an evidence gap, and a contradiction distinct from one
another when all three are present in the same investigation at once. This file closes that
gap: it exercises the real collection -> rule-evaluation -> report-building -> rendering
pipeline (no hand-written expected string standing in for any of those steps) against one
bundle engineered to carry all three signal types simultaneously, and checks that none is
dropped, merged into the wrong section, or misrepresented as a different kind of signal.
"""

from __future__ import annotations

from k8s_investigator.models.evidence import EvidenceStatus
from k8s_investigator.models.findings import FindingKind
from k8s_investigator.report_builder import build_report
from k8s_investigator.rendering import render_text
from k8s_investigator.rules.engine import evaluate_bundle

from .conftest import collect_and_build
from .fixtures import scenarios

_SECTION_HEADERS = (
    "DETERMINISTIC FINDINGS",
    "OBSERVED FACTS",
    "AI HYPOTHESES",
    "EVIDENCE GAPS",
    "CONTRADICTIONS",
    "NEXT STEPS",
)


def _sections(text: str) -> dict[str, str]:
    """Slices the rendered report into one chunk per section, in rendering.py's own fixed
    header order -- so a check against one section's text cannot accidentally match content
    that actually lives in a different section."""
    indices = [(h, text.index(h)) for h in _SECTION_HEADERS]
    out: dict[str, str] = {}
    for i, (header, start) in enumerate(indices):
        end = indices[i + 1][1] if i + 1 < len(indices) else len(text)
        out[header] = text[start:end]
    return out


def test_report_preserves_finding_gap_and_contradiction_distinctly():
    bundle = collect_and_build(scenarios.report_quality_finding_gap_and_contradiction_together())
    findings, contradictions = evaluate_bundle(bundle)
    report = build_report(bundle, findings, contradictions)

    # --- 1. The typed report object: identity and structure, independent of any rendering ----

    assert len(report.deterministic_findings) == 1
    finding = report.deterministic_findings[0]
    assert finding.produced_by == "rule:R6_resource_capacity"
    assert finding.kind == FindingKind.DETERMINISTIC_FINDING
    assert "insufficient cpu" in finding.statement.lower()

    assert len(report.evidence_gaps) == 1
    gap = report.evidence_gaps[0]
    assert gap.status == EvidenceStatus.UNAVAILABLE
    assert getattr(gap.source_ref, "kind", None) == "ReplicaSet"
    assert gap.error_detail is not None and "forbidden" in gap.error_detail.lower()

    assert len(report.contradictions) == 1
    contradiction = report.contradictions[0]
    assert contradiction.rule_id == "contradiction:insufficient_capacity_vs_node_allocatable"
    assert "insufficient cpu" in contradiction.description.lower()
    assert "allocatable cpu=8" in contradiction.description

    # the gap is a *collection* problem on an unrelated object (ReplicaSet) -- it must not be
    # confused with, or derived from, either the finding or the contradiction's own evidence
    assert gap.id not in finding.evidence_ids
    assert gap.id not in (contradiction.evidence_a, contradiction.evidence_b)
    # nor is the finding's or contradiction's evidence mistaken for a gap
    present_ids = {item.id for item in bundle.items if item.status == EvidenceStatus.PRESENT}
    assert set(finding.evidence_ids).issubset(present_ids)
    assert {contradiction.evidence_a, contradiction.evidence_b}.issubset(present_ids)

    # the owner-chain OBSERVED_FACT about the Pod's own ownerReference still appears (it reads
    # the Pod's own metadata, never the ReplicaSet object itself) -- but nothing claims the
    # ReplicaSet-owned-by-Deployment fact, since that specifically requires the now-unavailable
    # ReplicaSet payload; this is the gap correctly suppressing only the finding that actually
    # depended on it, not everything downstream
    observed_statements = [f.statement for f in report.observed_facts]
    assert any("owned by ReplicaSet" in s for s in observed_statements)
    assert not any("owned by Deployment" in s for s in observed_statements)

    # --- 2. The rendered report: each signal lands in its own section, and only there --------

    text = render_text(report)
    sections = _sections(text)

    assert "rule:R6_resource_capacity" in sections["DETERMINISTIC FINDINGS"]
    assert "insufficient cpu" in sections["DETERMINISTIC FINDINGS"].lower()

    assert "[UNAVAILABLE] ReplicaSet" in sections["EVIDENCE GAPS"]
    assert "forbidden" in sections["EVIDENCE GAPS"].lower()

    assert "contradiction:insufficient_capacity_vs_node_allocatable" in sections["CONTRADICTIONS"]
    assert "allocatable cpu=8" in sections["CONTRADICTIONS"]

    # cross-contamination checks -- the defect class this test exists to catch: a signal
    # silently merged into the wrong section, or duplicated into a section it doesn't belong in
    assert "ReplicaSet" not in sections["DETERMINISTIC FINDINGS"]
    assert "contradiction:" not in sections["DETERMINISTIC FINDINGS"]
    assert "contradiction:" not in sections["EVIDENCE GAPS"]
    assert "UNAVAILABLE" not in sections["CONTRADICTIONS"]
    assert "rule:R6_resource_capacity" not in sections["EVIDENCE GAPS"]
    assert "rule:R6_resource_capacity" not in sections["CONTRADICTIONS"]

    # next steps reflect both independent problems (RBAC gap AND contradiction), not just one
    assert any("ReplicaSet could not be read" in step for step in report.next_steps)
    assert any("Contradictory evidence was recorded" in step for step in report.next_steps)

    # the report is still a single, reproducible, internally-consistent object -- same bundle,
    # same (deterministic) evaluation, produces the same report content on a second pass
    findings_again, contradictions_again = evaluate_bundle(bundle)
    report_again = build_report(bundle, findings_again, contradictions_again)
    assert report.deterministic_findings == report_again.deterministic_findings
    assert report.evidence_gaps == report_again.evidence_gaps
    assert report.contradictions == report_again.contradictions
