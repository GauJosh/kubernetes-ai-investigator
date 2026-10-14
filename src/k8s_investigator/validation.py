"""Evidence-bundle and finding validation.

Two distinct checks, per docs/phase-0.8-mvp-blueprint.md §D.1:

* ``validate_bundle_integrity`` -- does every EvidenceItem's id actually match the content it
  claims to identify? (catches a bundle-builder bug, not a rule-engine bug)
* ``validate_finding_references`` -- does every Finding's locator resolve, in bounds, to the
  exact text it quotes, inside a bundle that really contains that evidence? (catches a
  rule-engine bug, not malformed input)
"""

from __future__ import annotations

from typing import Any

from .canonical import compute_evidence_item_id
from .errors import (
    EvidenceLocatorOutOfBoundsError,
    EvidenceQuoteMismatchError,
    EvidenceReferenceError,
    InvestigatorError,
    UngroundedFindingError,
)
from .models.evidence import EvidenceBundle, EvidenceItem
from .models.findings import Finding, FindingKind
from .models.locators import FieldPath, LineRange, Pointer


class EvidenceItemHashMismatchError(InvestigatorError):
    """An EvidenceItem's id does not match the content hash of its own source_ref/payload."""

    def __init__(self, item_id: str, recomputed: str) -> None:
        self.item_id = item_id
        self.recomputed = recomputed
        super().__init__(
            f"EvidenceItem {item_id!r} does not match its own content hash "
            f"(recomputed {recomputed!r}) -- the bundle was built or edited incorrectly."
        )


def _resolve_line_range(payload: Any, pointer: LineRange) -> str | None:
    if not isinstance(payload, str):
        return None
    lines = payload.splitlines()
    if not (0 <= pointer.start_line <= pointer.end_line < len(lines)):
        return None
    return "\n".join(lines[pointer.start_line : pointer.end_line + 1])


def _resolve_json_pointer(payload: Any, json_pointer: str) -> Any:
    if json_pointer in ("", "/"):
        return payload
    if not json_pointer.startswith("/"):
        return None
    current = payload
    for raw_token in json_pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                idx = int(token)
            except ValueError:
                return None
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
        elif isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        else:
            return None
    return current


def resolve_pointer(payload: Any, pointer: Pointer) -> str | None:
    """Resolve a locator's pointer against a payload. Returns None if it is out of bounds.

    Only string results are returned -- a pointer resolving to a nested object or number is
    treated as unresolved, since nothing in this project's rule bank cites a non-leaf value.
    """
    if isinstance(pointer, LineRange):
        return _resolve_line_range(payload, pointer)
    if isinstance(pointer, FieldPath):
        resolved = _resolve_json_pointer(payload, pointer.json_pointer)
        return resolved if isinstance(resolved, str) else None
    return None  # pragma: no cover -- Pointer is a closed discriminated union


def validate_finding_references(finding: Finding, bundle: EvidenceBundle) -> None:
    """Raise a typed error on the first invalid locator. Returns None if everything checks out."""
    for locator in finding.evidence_locators:
        item = bundle.item_by_id(locator.evidence_id)
        if item is None:
            raise EvidenceReferenceError(finding.id, locator.evidence_id)

        resolved = resolve_pointer(item.payload, locator.pointer)
        if resolved is None:
            raise EvidenceLocatorOutOfBoundsError(finding.id, locator.evidence_id, locator.pointer)
        if resolved != locator.quoted_text:
            raise EvidenceQuoteMismatchError(
                finding.id, locator.evidence_id, locator.quoted_text, resolved
            )

    # Defense in depth: Finding's own model_validator already enforces this at construction
    # time; re-checking here means a bundle-level validation pass catches it too.
    if finding.kind == FindingKind.DETERMINISTIC_FINDING and not finding.evidence_locators:
        raise UngroundedFindingError(finding.id)


def validate_findings(findings: list[Finding], bundle: EvidenceBundle) -> None:
    """Validate every finding; raises on the first failure found, in list order."""
    for finding in findings:
        validate_finding_references(finding, bundle)


def validate_bundle_integrity(bundle: EvidenceBundle) -> None:
    """Recompute each EvidenceItem's content hash and confirm it matches the stored id."""
    for item in bundle.items:
        recomputed = compute_evidence_item_id(_source_ref_as_dict(item), item.payload)
        if recomputed != item.id:
            raise EvidenceItemHashMismatchError(item.id, recomputed)


def _source_ref_as_dict(item: EvidenceItem) -> dict[str, Any]:
    return item.source_ref.model_dump(mode="json")
