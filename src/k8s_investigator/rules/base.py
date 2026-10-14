"""Shared helpers for building self-consistent, validated Findings.

Every helper here resolves its own locator against the item it just cited *before* returning,
so a rule can never construct a Finding whose evidence_locators would fail
validation.validate_finding_references later -- defense in depth, checked twice (here at
construction, and again centrally before a report is built).
"""

from __future__ import annotations

from typing import Any

from ..canonical import content_hash
from ..models.evidence import EvidenceItem
from ..models.findings import Finding, FindingKind
from ..models.locators import EvidenceLocator, FieldPath, LineRange
from ..validation import resolve_pointer


def _json_pointer(path_parts: tuple[Any, ...]) -> str:
    def escape(part: Any) -> str:
        return str(part).replace("~", "~0").replace("/", "~1")

    return "/" + "/".join(escape(part) for part in path_parts)


def field_locator(item: EvidenceItem, *path_parts: Any) -> EvidenceLocator:
    """A FieldPath locator into ``item``'s structured payload. Raises if the path doesn't
    resolve -- a rule citing a field that isn't there is a bug to catch immediately, not a
    finding to ship."""
    pointer = FieldPath(json_pointer=_json_pointer(path_parts))
    resolved = resolve_pointer(item.payload, pointer)
    if resolved is None:
        raise ValueError(
            f"field_locator: {pointer.json_pointer!r} does not resolve on item {item.id!r} "
            f"(kind={getattr(item.source_ref, 'kind', None)!r}) -- rule bug, not a finding."
        )
    return EvidenceLocator(evidence_id=item.id, pointer=pointer, quoted_text=resolved)


def line_locator(item: EvidenceItem, start_line: int, end_line: int) -> EvidenceLocator:
    """A LineRange locator into ``item``'s log-text payload. Raises if out of bounds."""
    pointer = LineRange(start_line=start_line, end_line=end_line)
    resolved = resolve_pointer(item.payload, pointer)
    if resolved is None:
        raise ValueError(
            f"line_locator: lines [{start_line}, {end_line}] do not resolve on item "
            f"{item.id!r} -- rule bug, not a finding."
        )
    return EvidenceLocator(evidence_id=item.id, pointer=pointer, quoted_text=resolved)


def make_finding_id(produced_by: str, statement: str, evidence_ids: list[str]) -> str:
    """Deterministic, content-derived finding id -- the same inputs always produce the same id,
    which is what makes "run the rule engine twice, get identical findings" a real, checkable
    property rather than an accident of a random id generator."""
    digest = content_hash(
        {"produced_by": produced_by, "statement": statement, "evidence_ids": sorted(evidence_ids)}
    )
    return f"finding:{digest}"


def make_finding(
    *,
    kind: FindingKind,
    statement: str,
    produced_by: str,
    locators: list[EvidenceLocator],
    limitation: str | None = None,
) -> Finding:
    finding_id = make_finding_id(
        produced_by, statement, [loc.evidence_id for loc in locators]
    )
    return Finding(
        id=finding_id,
        kind=kind,
        statement=statement,
        evidence_locators=locators,
        produced_by=produced_by,
        limitation=limitation,
    )
