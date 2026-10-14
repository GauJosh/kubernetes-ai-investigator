"""The final investigation report -- a pure assembly of validated findings and evidence."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .evidence import ContradictionEdge, EvidenceItem
from .findings import Finding
from .refs import ObjectRef, TimestampValue


class InvestigationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    investigation_id: str
    target: ObjectRef
    bundle_id: str  # ties the report back to the exact reproducible EvidenceBundle
    generated_at: TimestampValue
    observed_facts: list[Finding] = Field(default_factory=list)
    deterministic_findings: list[Finding] = Field(default_factory=list)
    ai_hypotheses: list[Finding] = Field(default_factory=list)  # always empty in Milestone 1
    evidence_gaps: list[EvidenceItem] = Field(default_factory=list)  # status != PRESENT
    contradictions: list[ContradictionEdge] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)  # human-readable, never auto-executable
