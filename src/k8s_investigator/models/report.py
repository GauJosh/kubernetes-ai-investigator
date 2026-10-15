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


class AIHypothesisOutcome(str, Enum):
    """Phase 0.16: a finer-grained breakdown of a SUCCEEDED run, never used for NOT_ATTEMPTED
    or FAILED (see ``InvestigationReport._enforce_ai_hypothesis_outcome_consistency`` for the
    exact pairing rule this enforces).

    A live call can complete successfully and still produce zero accepted hypotheses for two
    genuinely different reasons that ``AIHypothesisRunStatus.SUCCEEDED`` alone cannot tell
    apart: the model explicitly returned no candidates at all (``ABSTAINED``), or it returned
    one or more candidates that were all rejected by the existing validation pipeline
    (``ALL_REJECTED``). ``PARTIAL_ACCEPTANCE`` and ``ACCEPTED`` cover the cases where at least
    one candidate survives. This value is purely descriptive of *what happened to the
    candidates* -- it never changes which candidates are accepted or rejected.
    """

    ABSTAINED = "ABSTAINED"  # a valid response explicitly contained no candidates
    ACCEPTED = "ACCEPTED"  # at least one candidate accepted, none rejected
    PARTIAL_ACCEPTANCE = "PARTIAL_ACCEPTANCE"  # at least one accepted, at least one rejected
    ALL_REJECTED = "ALL_REJECTED"  # candidates were produced, but none survived validation


class AIHypothesisRejectionReason(str, Enum):
    """Phase 0.16: a bounded, fixed set of reasons a candidate did not survive
    ``ai.hypothesis.generate_ai_hypotheses``'s pipeline -- grounded directly in that pipeline's
    own existing checks (see that module for exactly which exception maps to which value here).
    Never includes raw candidate text, evidence payloads, or an exception's own message --
    only one of these fixed enum values is ever recorded for a given rejection.

    One rejected candidate always maps to exactly one reason: each check in the existing
    pipeline raises at most one exception per candidate, and the first one raised stops
    processing of that candidate (ordinary Python control flow) -- there is no case where two
    reasons could apply to the same candidate, so no precedence rule is needed to pick between
    them.
    """

    MALFORMED_CANDIDATE_SHAPE = "MALFORMED_CANDIDATE_SHAPE"  # dropped by ai.hypothesis.parse_candidates
    # before ever becoming a RawHypothesisCandidate (e.g. missing a required field)
    INVALID_CITATION_REFERENCE = "INVALID_CITATION_REFERENCE"  # UnknownCitationReferenceError,
    # CitationReferenceEvidenceMismatchError, or MalformedCitationReferenceSelectionError
    EVIDENCE_VALIDATION_FAILED = "EVIDENCE_VALIDATION_FAILED"  # EvidenceReferenceError,
    # EvidenceLocatorOutOfBoundsError, EvidenceQuoteMismatchError, LineRangeSpanMismatchError,
    # UngroundedFindingError, or Finding's own "no evidence_locators"/"no confidence" ValueError
    UNSUPPORTED_FINDING_STATEMENT = "UNSUPPORTED_FINDING_STATEMENT"  # FindingStatementCitedAsEvidenceError
    UNGROUNDED_CONFIDENCE = "UNGROUNDED_CONFIDENCE"  # UngroundedConfidenceError
    UNACKNOWLEDGED_CONTRADICTION = "UNACKNOWLEDGED_CONTRADICTION"  # UnacknowledgedContradictionError
    OTHER = "OTHER"  # any other InvestigatorError/ValueError not covered above -- a safety net,
    # expected to rarely or never fire against the pipeline's current, closed set of checks


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
    # Phase 0.16 additions -- both default to their "absent" value, so any report serialized by
    # a prior version of this project (missing these keys entirely) still parses unchanged: a
    # missing key becomes None / [] exactly as if it had been explicitly set that way here.
    ai_hypothesis_outcome: AIHypothesisOutcome | None = None  # set only when status == SUCCEEDED
    ai_hypothesis_rejection_reasons: list[AIHypothesisRejectionReason] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def _enforce_ai_hypothesis_outcome_consistency(self) -> "InvestigationReport":
        """Phase 0.16: whenever ``ai_hypothesis_outcome`` is set, it must be alongside a
        SUCCEEDED status, and it must agree with the actual counts already on this report
        (``ai_hypotheses`` and ``ai_hypothesis_rejection_reasons``) -- never an independent,
        possibly-contradictory claim.

        Deliberately does **not** require ``ai_hypothesis_outcome`` to be present just because
        ``ai_hypothesis_status == SUCCEEDED`` -- doing so would reject every report serialized
        before this field existed (a real SUCCEEDED report with no ``ai_hypothesis_outcome``
        key at all, which defaults to ``None``), breaking exactly the backward compatibility
        this field was required to preserve. Every *newly built* report is still guaranteed to
        carry an outcome whenever the AI layer actually ran -- that guarantee lives in
        ``cli.py``/``ai.hypothesis.generate_ai_hypotheses`` always computing one, not in this
        validator refusing to construct a report without it.
        """
        has_outcome = self.ai_hypothesis_outcome is not None
        if has_outcome and self.ai_hypothesis_status != AIHypothesisRunStatus.SUCCEEDED:
            raise ValueError(
                "InvestigationReport: ai_hypothesis_outcome is set but ai_hypothesis_status is "
                f"{self.ai_hypothesis_status.value!r}, not SUCCEEDED -- an outcome is only "
                "meaningful alongside a SUCCEEDED status."
            )

        if not has_outcome and self.ai_hypothesis_rejection_reasons:
            raise ValueError(
                "InvestigationReport: ai_hypothesis_rejection_reasons is non-empty but "
                "ai_hypothesis_outcome is not set -- rejection reasons are only meaningful "
                "alongside a recorded outcome."
            )

        if has_outcome:
            accepted = bool(self.ai_hypotheses)
            rejected = bool(self.ai_hypothesis_rejection_reasons)
            expected = {
                AIHypothesisOutcome.ABSTAINED: (False, False),
                AIHypothesisOutcome.ACCEPTED: (True, False),
                AIHypothesisOutcome.PARTIAL_ACCEPTANCE: (True, True),
                AIHypothesisOutcome.ALL_REJECTED: (False, True),
            }[self.ai_hypothesis_outcome]
            if (accepted, rejected) != expected:
                raise ValueError(
                    f"InvestigationReport: ai_hypothesis_outcome is "
                    f"{self.ai_hypothesis_outcome.value!r}, but ai_hypotheses "
                    f"({'non-empty' if accepted else 'empty'}) and "
                    f"ai_hypothesis_rejection_reasons "
                    f"({'non-empty' if rejected else 'empty'}) are inconsistent with it."
                )
        return self
