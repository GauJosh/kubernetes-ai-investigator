"""The final investigation report -- a pure assembly of validated findings and evidence."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import ContradictionEdge, EvidenceItem
from .findings import Finding
from .refs import ObjectRef, TimestampValue


class AIHypothesisRunStatus(str, Enum):
    """Phase 0.12: whether AI hypothesis generation was even attempted, and if so, how it went.

    Exists so an empty ``ai_hypotheses`` list is never ambiguous between three genuinely
    different situations: the AI layer was never requested (NOT_ATTEMPTED, the default and the
    only value Milestones 1-2 and Phase 0.11 ever produced); it ran and legitimately found
    nothing worth hypothesizing (SUCCEEDED, still possibly empty); or it was requested and
    failed (FAILED) -- a parse error, an unconfigured/unreachable model client, or anything
    else. Collapsing the FAILED case into an empty list would misrepresent a failed
    investigation as a successful one that simply found nothing, which this project has
    already treated as a serious bug class once before (R9's "empty vs. failed" sibling-pod
    gap, docs/phase-0.10-milestone-2-notes.md §2) and does not repeat here.
    """

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class InvestigationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    investigation_id: str
    target: ObjectRef
    bundle_id: str  # ties the report back to the exact reproducible EvidenceBundle
    generated_at: TimestampValue
    observed_facts: list[Finding] = Field(default_factory=list)
    deterministic_findings: list[Finding] = Field(default_factory=list)
    ai_hypotheses: list[Finding] = Field(default_factory=list)
    ai_hypothesis_status: AIHypothesisRunStatus = AIHypothesisRunStatus.NOT_ATTEMPTED
    ai_hypothesis_error: str | None = None  # populated only when ai_hypothesis_status == FAILED
    evidence_gaps: list[EvidenceItem] = Field(default_factory=list)  # status != PRESENT
    contradictions: list[ContradictionEdge] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)  # human-readable, never auto-executable

    @model_validator(mode="after")
    def _enforce_status_error_pairing(self) -> "InvestigationReport":
        if self.ai_hypothesis_status == AIHypothesisRunStatus.FAILED and not self.ai_hypothesis_error:
            raise ValueError(
                "InvestigationReport: ai_hypothesis_status is FAILED but ai_hypothesis_error "
                "is empty -- a failure must always carry a reason."
            )
        if self.ai_hypothesis_status != AIHypothesisRunStatus.FAILED and self.ai_hypothesis_error:
            raise ValueError(
                "InvestigationReport: ai_hypothesis_error is set but ai_hypothesis_status is "
                f"{self.ai_hypothesis_status.value!r}, not FAILED -- an error detail is only "
                "meaningful alongside a FAILED status."
            )
        return self
