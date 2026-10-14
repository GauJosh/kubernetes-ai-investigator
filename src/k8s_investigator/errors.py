"""Typed validation errors.

Each one corresponds to a specific, named failure mode in
docs/phase-0.8-mvp-blueprint.md §D.1's validation pseudocode -- raised, never swallowed, and
never rendered into a report as if it were a finding.
"""

from __future__ import annotations


class InvestigatorError(Exception):
    """Base class for all errors this project raises deliberately."""


class EvidenceReferenceError(InvestigatorError):
    """A Finding's locator points at an evidence id that does not exist in the bundle."""

    def __init__(self, finding_id: str, evidence_id: str) -> None:
        self.finding_id = finding_id
        self.evidence_id = evidence_id
        super().__init__(
            f"Finding {finding_id!r} cites unknown evidence id {evidence_id!r} -- "
            "not present in this bundle."
        )


class EvidenceLocatorOutOfBoundsError(InvestigatorError):
    """A locator's pointer does not resolve inside the referenced item's actual payload."""

    def __init__(self, finding_id: str, evidence_id: str, pointer: object) -> None:
        self.finding_id = finding_id
        self.evidence_id = evidence_id
        self.pointer = pointer
        super().__init__(
            f"Finding {finding_id!r}: pointer {pointer!r} on evidence {evidence_id!r} does "
            "not resolve inside that item's payload."
        )


class EvidenceQuoteMismatchError(InvestigatorError):
    """A locator's quoted_text no longer matches what the pointer resolves to."""

    def __init__(self, finding_id: str, evidence_id: str, expected: str, actual: str) -> None:
        self.finding_id = finding_id
        self.evidence_id = evidence_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Finding {finding_id!r}: quoted_text on evidence {evidence_id!r} does not match "
            f"the bundle -- expected {expected!r}, resolved to {actual!r}."
        )


class LineRangeSpanMismatchError(InvestigatorError):
    """A LineRange locator's declared span (end_line - start_line + 1 lines) does not match
    the number of lines its own quoted_text actually spans -- the citation is internally
    inconsistent before even checking whether the content is real. Distinguished from
    EvidenceQuoteMismatchError (right-sized range, wrong content) so the two failure modes
    this project's live-cluster evaluation actually observed -- a too-narrow range for a real
    multiline quote, and a correct quote paired with a wrong single-line index -- are reported
    distinctly rather than collapsed into one generic message.
    """

    def __init__(self, finding_id: str, evidence_id: str, declared_span: int, quoted_span: int) -> None:
        self.finding_id = finding_id
        self.evidence_id = evidence_id
        self.declared_span = declared_span
        self.quoted_span = quoted_span
        super().__init__(
            f"Finding {finding_id!r}: the line_range on evidence {evidence_id!r} declares "
            f"{declared_span} line(s) (end_line - start_line + 1), but quoted_text spans "
            f"{quoted_span} line(s) -- the cited range does not contain the complete quote."
        )


class UngroundedFindingError(InvestigatorError):
    """A DETERMINISTIC_FINDING has no evidence_locators -- a bug, not a result."""

    def __init__(self, finding_id: str) -> None:
        self.finding_id = finding_id
        super().__init__(
            f"Finding {finding_id!r} is a DETERMINISTIC_FINDING with no evidence_locators."
        )


class UngroundedConfidenceError(InvestigatorError):
    """An AI_HYPOTHESIS claims HIGH confidence, but no DETERMINISTIC_FINDING exists anywhere
    in this investigation to ground that level of certainty.

    A structural proxy, not a text judgment: when the deterministic rule bank reached no
    confirmed conclusion for this investigation, the evidence was (by the project's own
    established behavior) judged insufficient for a confident, specific determination. A
    hypothesis may still be offered in that situation -- the AI layer's main value is covering
    gaps the deterministic rules miss -- but not presented with the same certainty as a
    rule-confirmed diagnosis. This check never inspects a hypothesis's own wording; it only
    counts whether any DETERMINISTIC_FINDING exists.
    """

    def __init__(self, finding_id: str) -> None:
        self.finding_id = finding_id
        super().__init__(
            f"Finding {finding_id!r}: HIGH confidence requires at least one "
            "DETERMINISTIC_FINDING already established for this investigation; none exists."
        )


class UnacknowledgedContradictionError(InvestigatorError):
    """An AI_HYPOTHESIS cites evidence on either side of a recorded ContradictionEdge without
    any 'limitation' text at all.

    A structural proxy, not a text judgment: this checks only whether the hypothesis's cited
    evidence_ids overlap a recorded contradiction's evidence_a/evidence_b, never what the
    hypothesis's prose actually says. It cannot verify the limitation *discusses* the
    contradiction -- only that citing contested evidence without any caveat is rejected,
    rather than silently treating one side of an unresolved disagreement as settled fact.
    """

    def __init__(self, finding_id: str) -> None:
        self.finding_id = finding_id
        super().__init__(
            f"Finding {finding_id!r}: cites evidence involved in a recorded contradiction "
            "without any 'limitation' text acknowledging it."
        )


class FindingStatementCitedAsEvidenceError(InvestigatorError):
    """An AI_HYPOTHESIS's quoted_text exactly matches another Finding's own rendered
    'statement' text, rather than a real value present in the evidence it cites.

    A six-scenario live retest showed this precise confusion: a Finding's statement (a
    human/code-written summary, e.g. "Container 'server': Waiting (reason=ErrImagePull).")
    sits next to the real evidence_ids it was derived from in the model input; the model
    sometimes copies that summary verbatim as quoted_text while citing the same evidence_id,
    rather than quoting the actual raw field value there (e.g. just "ErrImagePull"). This
    check is a structural equality comparison against known Finding statements, not a text
    judgment of the hypothesis's own reasoning -- and it is strictly a diagnostic
    improvement: such a quote was already going to fail the real content-match check in
    validate_finding_references (statements and raw field values essentially never coincide),
    this only reports *why* with a specific, actionable reason instead of a generic mismatch.
    """

    def __init__(self, finding_id: str, evidence_id: str, matched_statement: str) -> None:
        self.finding_id = finding_id
        self.evidence_id = evidence_id
        self.matched_statement = matched_statement
        super().__init__(
            f"Finding {finding_id!r}: quoted_text on evidence {evidence_id!r} is exactly "
            f"another Finding's rendered statement ({matched_statement!r}), not a value "
            "actually present in that evidence's payload -- a Finding's statement is a "
            "non-citable summary, never source text."
        )
