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


class UngroundedFindingError(InvestigatorError):
    """A DETERMINISTIC_FINDING has no evidence_locators -- a bug, not a result."""

    def __init__(self, finding_id: str) -> None:
        self.finding_id = finding_id
        super().__init__(
            f"Finding {finding_id!r} is a DETERMINISTIC_FINDING with no evidence_locators."
        )
