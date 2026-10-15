"""Findings: the closed fact/finding/hypothesis distinction, and typed evidence citation."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .locators import EvidenceLocator


class FindingKind(str, Enum):
    """Closed enum, per CLAUDE.md principle 2 -- a renderer that cannot express an unlabeled
    claim cannot ship one."""

    OBSERVED_FACT = "OBSERVED_FACT"
    DETERMINISTIC_FINDING = "DETERMINISTIC_FINDING"
    AI_HYPOTHESIS = "AI_HYPOTHESIS"  # not produced in Milestone 1 -- the kind exists so the
    # schema does not change when the (unimplemented) AI-hypothesis layer arrives.
    CUSTOM_GUIDANCE = "CUSTOM_GUIDANCE"  # Phase 0.18: a team-defined knowledge-pack rule
    # matched real evidence. Evidence-grounded like a DETERMINISTIC_FINDING (same
    # evidence_locators requirement below), but never a built-in, code-reviewed rule's
    # conclusion -- rendered in its own report section, never merged into or confused with
    # DETERMINISTIC FINDINGS, and never carries a `confidence` label (that stays AI_HYPOTHESIS
    # -only, per the existing rule just below).


class Confidence(str, Enum):
    """Categorical, never a bare float presented as false precision. AI_HYPOTHESIS only."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Finding(BaseModel):
    """A fact, a deterministic finding, or (later) an AI hypothesis -- always evidence-bound."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: FindingKind
    statement: str
    evidence_locators: list[EvidenceLocator] = Field(default_factory=list)
    produced_by: str  # e.g. "rule:R1_dns_failure" -- identifies the rule or (later) model call
    limitation: str | None = None  # a structured, honest caveat separate from the main statement
    confidence: Confidence | None = None  # AI_HYPOTHESIS only -- see model_validator below
    contradicts: list[str] | None = None  # other Finding.id values

    @property
    def evidence_ids(self) -> list[str]:
        """Derived, not stored -- one source of truth, per docs/phase-0.8-mvp-blueprint.md §D."""
        return sorted({locator.evidence_id for locator in self.evidence_locators})

    @model_validator(mode="after")
    def _enforce_closed_contract(self) -> "Finding":
        if (
            self.kind in (FindingKind.DETERMINISTIC_FINDING, FindingKind.CUSTOM_GUIDANCE)
            and not self.evidence_locators
        ):
            raise ValueError(
                f"Finding {self.id!r}: a {self.kind.value} with no evidence_locators is a "
                "bug, not a result (docs/phase-0.8-mvp-blueprint.md §D) -- a knowledge-pack "
                "rule must only match when it can cite the real evidence that triggered it."
            )
        if self.confidence is not None and self.kind != FindingKind.AI_HYPOTHESIS:
            raise ValueError(
                f"Finding {self.id!r}: 'confidence' is reserved for AI_HYPOTHESIS findings; "
                "a deterministic finding expresses limitations via 'limitation' text, not a "
                "categorical confidence label (docs/phase-0.8-mvp-blueprint.md §D)."
            )
        if self.kind == FindingKind.AI_HYPOTHESIS:
            # Phase 0.11: an AI hypothesis is evidence-first by construction, same as a
            # DETERMINISTIC_FINDING -- an evidence-free "hypothesis" is just a guess, and a
            # hypothesis with no categorical confidence is not actually a ranked hypothesis.
            if not self.evidence_locators:
                raise ValueError(
                    f"Finding {self.id!r}: an AI_HYPOTHESIS with no evidence_locators is a "
                    "guess, not a grounded hypothesis (docs/phase-0.11-ai-hypothesis-layer-"
                    "design.md §3.4)."
                )
            if self.confidence is None:
                raise ValueError(
                    f"Finding {self.id!r}: an AI_HYPOTHESIS must carry a categorical "
                    "'confidence' label (docs/phase-0.11-ai-hypothesis-layer-design.md §3.4)."
                )
        return self
