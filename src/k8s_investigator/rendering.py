"""Human-readable rendering of an InvestigationReport. The JSON form needs no rendering --
``report.model_dump_json(indent=2)`` is already the machine-readable artifact."""

from __future__ import annotations

from .models.findings import Finding
from .models.report import (
    AIHypothesisOutcome,
    AIHypothesisRunStatus,
    CustomGuidanceMatch,
    InvestigationReport,
)


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
        if any(f.produced_by == "engine:no_rule_matched" for f in report.observed_facts):
            lines.append(
                "  (none -- no rule in this project's bank (R1-R11) recognized this failure "
                "pattern; see OBSERVED FACTS and NEXT STEPS below. This is not the same as "
                "confirming there is no cause.)"
            )
        else:
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
        lines.append("  (not attempted -- run with --with-ai to enable)")
    elif report.ai_hypothesis_status == AIHypothesisRunStatus.FAILED:
        lines.append(f"  (AI hypothesis generation FAILED: {report.ai_hypothesis_error})")
        lines.append("  (the deterministic findings above are unaffected by this failure)")
    elif not report.ai_hypotheses:
        # ai_hypothesis_outcome is None for any report serialized before Phase 0.16 (or any
        # report that otherwise never set it) -- falls through to the original, unchanged
        # message so an old saved report still renders exactly as it always has.
        if report.ai_hypothesis_outcome == AIHypothesisOutcome.ABSTAINED:
            lines.append("  (none -- the model explicitly returned no candidates)")
        elif report.ai_hypothesis_outcome == AIHypothesisOutcome.ALL_REJECTED:
            reasons = ", ".join(r.value for r in report.ai_hypothesis_rejection_reasons)
            lines.append(
                f"  (none -- {len(report.ai_hypothesis_rejection_reasons)} candidate(s) "
                f"rejected: {reasons})"
            )
        else:
            lines.append("  (none -- the model produced no hypothesis meeting the evidence bar)")
    else:
        for finding in report.ai_hypotheses:
            lines.extend(_render_finding(finding))
        if report.ai_hypothesis_outcome == AIHypothesisOutcome.PARTIAL_ACCEPTANCE:
            reasons = ", ".join(r.value for r in report.ai_hypothesis_rejection_reasons)
            lines.append(
                f"  ({len(report.ai_hypothesis_rejection_reasons)} additional candidate(s) "
                f"rejected: {reasons})"
            )
    lines.append("")

    lines.append("CUSTOM KNOWLEDGE GUIDANCE")
    lines.append("-" * 25)
    if report.knowledge_pack_source is None:
        lines.append("  (not supplied -- use --knowledge PATH to enable)")
    elif not report.custom_guidance:
        lines.append(f"  (none -- no custom rule in {report.knowledge_pack_source!r} matched this evidence)")
    else:
        lines.append(f"  (source: {report.knowledge_pack_source})")
        for match in report.custom_guidance:
            lines.extend(_render_custom_guidance(match))
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


def _render_custom_guidance(match: CustomGuidanceMatch) -> list[str]:
    """Renders the matched rule id/description (via the real, evidence-validated Finding,
    exactly like any other finding) plus the rest of the knowledge pack's own guidance block
    -- never merged into or confused with a built-in rule's output."""
    lines = _render_finding(match.finding)
    lines.append(f"      summary: {match.summary}")
    if match.next_checks:
        lines.append("      next checks:")
        for check in match.next_checks:
            lines.append(f"        - {check}")
    if match.runbook:
        lines.append(f"      runbook: {match.runbook}")
    return lines
