"""Human-readable rendering of an InvestigationReport. The JSON form needs no rendering --
``report.model_dump_json(indent=2)`` is already the machine-readable artifact."""

from __future__ import annotations

from .models.evidence import EvidenceStatus
from .models.findings import Finding
from .models.report import AIHypothesisRunStatus, InvestigationReport


def render_text(report: InvestigationReport) -> str:
    lines: list[str] = []
    target = report.target
    lines.append(f"Investigation {report.investigation_id}")
    lines.append(f"Target: Pod {target.namespace}/{target.name}")
    lines.append(f"Evidence bundle: {report.bundle_id}")
    lines.append(f"Generated at: {report.generated_at.value.isoformat()} ({report.generated_at.source_field})")
    lines.append("")

    lines.append("DETERMINISTIC FINDINGS")
    lines.append("-" * 22)
    if not report.deterministic_findings:
        lines.append("  (none)")
    for finding in report.deterministic_findings:
        lines.extend(_render_finding(finding))
    lines.append("")

    lines.append("OBSERVED FACTS")
    lines.append("-" * 14)
    if not report.observed_facts:
        lines.append("  (none)")
    for finding in report.observed_facts:
        lines.extend(_render_finding(finding))
    lines.append("")

    lines.append("AI HYPOTHESES")
    lines.append("-" * 13)
    if report.ai_hypothesis_status == AIHypothesisRunStatus.NOT_ATTEMPTED:
        lines.append("  (not attempted -- run with --with-ai-hypotheses to enable)")
    elif report.ai_hypothesis_status == AIHypothesisRunStatus.FAILED:
        lines.append(f"  (AI hypothesis generation FAILED: {report.ai_hypothesis_error})")
        lines.append("  (the deterministic findings above are unaffected by this failure)")
    elif not report.ai_hypotheses:
        lines.append("  (none -- the model produced no hypothesis meeting the evidence bar)")
    else:
        for finding in report.ai_hypotheses:
            lines.extend(_render_finding(finding))
    lines.append("")

    lines.append("EVIDENCE GAPS")
    lines.append("-" * 13)
    if not report.evidence_gaps:
        lines.append("  (none -- every requested piece of evidence was collected)")
    for gap in report.evidence_gaps:
        kind = getattr(gap.source_ref, "kind", getattr(gap.source_ref, "path", "?"))
        detail = gap.expiry_note or gap.error_detail or ""
        lines.append(f"  - [{gap.status.value}] {kind}: {detail}")
    lines.append("")

    lines.append("CONTRADICTIONS")
    lines.append("-" * 14)
    if not report.contradictions:
        lines.append("  (none)")
    for edge in report.contradictions:
        lines.append(f"  - [{edge.rule_id}] {edge.description}")
    lines.append("")

    lines.append("NEXT STEPS")
    lines.append("-" * 10)
    if not report.next_steps:
        lines.append("  (none)")
    for step in report.next_steps:
        lines.append(f"  - {step}")

    return "\n".join(lines)


def _render_finding(finding: Finding) -> list[str]:
    lines = [f"  [{finding.produced_by}] {finding.statement}"]
    if finding.confidence:
        lines.append(f"      confidence: {finding.confidence.value}")
    for locator in finding.evidence_locators:
        lines.append(f"      evidence: {locator.evidence_id[:12]}... -> {locator.quoted_text!r}")
    if finding.limitation:
        lines.append(f"      limitation: {finding.limitation}")
    return lines
