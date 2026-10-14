"""Collection outcomes, evidence items, and the evidence bundle.

See docs/phase-0.8-mvp-blueprint.md §D and §D.1 for the design this implements, and
docs/research/event-timeline-contract.md for why PRESENT / NOT_FOUND / UNAVAILABLE / EXPIRED /
STALE must stay distinct rather than collapsing into a generic null or error.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .refs import ObjectRef, SourceRef, TimestampValue


class CollectionStatus(str, Enum):
    """The outcome of one API call the collector actually issued."""

    PRESENT = "PRESENT"
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"  # e.g. Forbidden, or any non-404 API error
    TIMEOUT = "TIMEOUT"


class CollectionOutcome(BaseModel):
    """One collector call, recorded regardless of success -- never discarded on failure."""

    model_config = ConfigDict(frozen=True)

    requested: SourceRef
    status: CollectionStatus
    error_detail: str | None = None  # e.g. "Forbidden: pods is forbidden: User ... cannot list"
    payload: Any = None  # the raw structured object/list/log chunk, only when status == PRESENT
    collected_at: datetime  # this project's own clock -- provenance, tagged explicitly as such


class EvidenceStatus(str, Enum):
    """The status of one typed evidence record the rule engine can reason about.

    Unlike CollectionStatus (one API call's outcome), an EvidenceItem can represent an
    explicit *absence* as a first-class, citable record -- e.g. "no events were found for
    this pod" is itself evidence a rule or contradiction-detector can reference.
    """

    PRESENT = "PRESENT"
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"
    EXPIRED = "EXPIRED"  # a plausible-expiry-flagged NOT_FOUND, per event-timeline-contract.md §5/§7
    STALE = "STALE"  # not produced by Milestone 1's single-pass collector; reserved for a future
    # multi-pass/hybrid design (docs/research/architecture-alternatives.md Investigation 3) --
    # exercised only by hand-constructed fixtures in this milestone's tests.


class EvidenceItem(BaseModel):
    """One typed, citable evidence record -- present content, or an explicit, reasoned absence."""

    model_config = ConfigDict(frozen=True)

    id: str  # content_hash(source_ref, payload) -- see canonical.py. EXCLUDES collected_at.
    source_ref: SourceRef
    collected_at: TimestampValue  # provenance -- never part of the content-identity hash
    payload: Any = None
    status: EvidenceStatus
    expiry_note: str | None = None  # populated only when EXPIRED, e.g. "pod age exceeds ~1h event TTL window"
    error_detail: str | None = None  # populated only when UNAVAILABLE


class ContradictionEdge(BaseModel):
    """Two evidence items that disagree -- recorded, never silently resolved by picking one."""

    model_config = ConfigDict(frozen=True)

    evidence_a: str  # EvidenceItem.id
    evidence_b: str  # EvidenceItem.id
    rule_id: str
    description: str


class EvidenceBundle(BaseModel):
    """The frozen, reproducible input to everything downstream of collection."""

    model_config = ConfigDict(frozen=True)

    id: str  # content_hash(target, sorted(item ids), collection params) -- see canonical.py
    investigation_target: ObjectRef  # the Pod
    collected_at_range: tuple[datetime, datetime]  # provenance only, excluded from bundle.id
    items: list[EvidenceItem] = Field(default_factory=list)
    queries: list[CollectionOutcome] = Field(default_factory=list)  # every attempt, raw
    contradictions: list[ContradictionEdge] = Field(default_factory=list)

    def item_by_id(self, item_id: str) -> EvidenceItem | None:
        for item in self.items:
            if item.id == item_id:
                return item
        return None
