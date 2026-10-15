"""Build the final InvestigationReport -- a pure assembly step with one real responsibility:
every finding is validated against the bundle before it is allowed into the report."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .models.evidence import ContradictionEdge, EvidenceBundle, EvidenceStatus
from .models.findings import Finding, FindingKind
from .models.report import (
    AIHypothesisOutcome,
    AIHypothesisRejectionReason,
    AIHypothesisRunStatus,
    CustomGuidanceMatch,
    InvestigationReport,
)
from .timeline import tag
from .validation import validate_findings


def build_report(
    bundle: EvidenceBundle,
    findings: list[Finding],
    contradictions: list[ContradictionEdge],
    *,
    ai_hypothesis_status: AIHypothesisRunStatus = AIHypothesisRunStatus.NOT_ATTEMPTED,
    ai_hypothesis_error: str | None = None,
    ai_hypothesis_outcome: AIHypothesisOutcome | None = None,
    ai_hypothesis_rejection_reasons: list[AIHypothesisRejectionReason] | None = None,
    custom_guidance: list[CustomGuidanceMatch] | None = None,
    knowledge_pack_source: str | None = None,
) -> InvestigationReport:
    """Validates every finding's evidence locators before building the report. Raises the
    underlying typed error (see errors.py) if any finding does not survive validation --
    an invalid finding is never shown to a user as if it were a result. This now includes
    every ``custom_guidance`` match's own ``finding`` -- a knowledge-pack rule's citation is
    held to the exact same evidence-reference standard as a built-in rule's, never a weaker,
    parallel check (Phase 0.18).

    ``ai_hypothesis_status``/``ai_hypothesis_error``/``ai_hypothesis_outcome``/
    ``ai_hypothesis_rejection_reasons``/``custom_guidance``/``knowledge_pack_source`` are all
    supplied by the caller (``cli.py``), which is the only place that knows whether AI
    hypothesis generation or a knowledge pack were even requested -- this function only
    assembles what it is given. Every existing call site that omits all six keyword arguments
    gets exactly Milestone 1-2/Phase 0.11 behavior (NOT_ATTEMPTED, no error, no outcome, no
    custom guidance), unchanged.
    """
    custom_guidance = custom_guidance or []
    validate_findings(findings + [match.finding for match in custom_guidance], bundle)

    observed = [f for f in findings if f.kind == FindingKind.OBSERVED_FACT]
    deterministic = [f for f in findings if f.kind == FindingKind.DETERMINISTIC_FINDING]
    ai_hypotheses = [f for f in findings if f.kind == FindingKind.AI_HYPOTHESIS]

    evidence_gaps = [item for item in bundle.items if item.status != EvidenceStatus.PRESENT]

    return InvestigationReport(
        investigation_id=str(uuid.uuid4()),
        target=bundle.investigation_target,
        bundle_id=bundle.id,
        generated_at=tag(datetime.now(timezone.utc), "collectedAt"),
        observed_facts=observed,
        deterministic_findings=deterministic,
        ai_hypotheses=ai_hypotheses,
        ai_hypothesis_status=ai_hypothesis_status,
        ai_hypothesis_error=ai_hypothesis_error,
        ai_hypothesis_outcome=ai_hypothesis_outcome,
        ai_hypothesis_rejection_reasons=ai_hypothesis_rejection_reasons or [],
        evidence_gaps=evidence_gaps,
        contradictions=contradictions,
        next_steps=_derive_next_steps(deterministic, observed, evidence_gaps, contradictions),
        custom_guidance=custom_guidance,
        knowledge_pack_source=knowledge_pack_source,
    )


_INSUFFICIENT_EVIDENCE_RULE_IDS = frozenset(
    {
        "rule:R4_insufficient_evidence",
        "rule:R4_logs_unavailable",
        "rule:image_pull_unrecognized",
        "rule:R3_auth_failure_sa_unavailable",
    }
)


def _derive_next_steps(
    deterministic: list[Finding],
    observed: list[Finding],
    evidence_gaps: list,
    contradictions: list[ContradictionEdge],
) -> list[str]:
    """Small, honest, human-readable suggestions -- never auto-executable, never a command."""
    steps: list[str] = []

    for fact in observed:
        if fact.produced_by in _INSUFFICIENT_EVIDENCE_RULE_IDS:
            steps.append(
                f"Evidence was insufficient to reach a deterministic finding for this "
                f"container ({fact.produced_by}) -- manual inspection is recommended; see the "
                "corresponding observed fact for exactly what was and was not checked."
            )
        elif fact.produced_by == "engine:no_rule_matched":
            steps.append(
                "No deterministic rule recognized this failure pattern -- review the observed "
                "facts and evidence gaps above directly, and consider re-running with --with-ai "
                "(requires AI_HYPOTHESIS_PROVIDER/OPENAI_API_KEY/AI_HYPOTHESIS_MODEL configured) "
                "for a hypothesis grounded in this same evidence."
            )

    for gap in evidence_gaps:
        kind = getattr(gap.source_ref, "kind", None)
        if gap.status == EvidenceStatus.UNAVAILABLE and kind == "ServiceAccount":
            steps.append(
                "The investigating credential could not read the Pod's ServiceAccount -- "
                "verify RBAC for that specific object before re-running this investigation."
            )
        elif gap.status == EvidenceStatus.EXPIRED:
            steps.append(
                "No matching events were found and the gap is consistent with the ~1h event "
                "TTL window -- if this investigation started well after the symptom appeared, "
                "consider re-collecting sooner next time; nothing can recover already-expired "
                "events now."
            )
        elif gap.status == EvidenceStatus.UNAVAILABLE:
            steps.append(
                f"{kind or 'A resource'} could not be read ({gap.error_detail or 'no detail'}) "
                "-- check RBAC and connectivity for the investigating credential."
            )

    if contradictions:
        steps.append(
            "Contradictory evidence was recorded (see 'contradictions') -- re-collecting after "
            "the pod's state has settled may resolve the disagreement; it is not resolved "
            "automatically by this report."
        )

    if not deterministic and not steps:
        steps.append(
            "No deterministic finding was reached from the collected evidence. If the "
            "investigated pod is not actually healthy, consider broadening log collection "
            "bounds or re-running once more diagnostic evidence (e.g. events) may be present."
        )

    return steps
