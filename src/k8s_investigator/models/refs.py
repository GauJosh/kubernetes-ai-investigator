"""Identity and provenance types.

These define *where* a piece of evidence came from, independent of what it says.
See docs/research/architecture-alternatives.md (Investigation 2) and
docs/phase-0.8-mvp-blueprint.md (§D) for the design this implements.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# The exact set of timestamp fields this project will ever tag a value with.
# Kept narrow and explicit per docs/research/event-timeline-contract.md §6: a timestamp
# without its field identity attached is not a usable evidence value.
TimestampSourceField = Literal[
    "metadata.creationTimestamp",
    "firstTimestamp",
    "lastTimestamp",
    "eventTime",
    "series.lastObservedTime",
    "collectedAt",
]


class TimestampValue(BaseModel):
    """A timestamp that always carries the name of the field it came from.

    Never construct a bare ``datetime`` for display or reasoning purposes elsewhere in
    this project -- wrap it here first, per the event-timeline contract's rule that every
    surfaced timestamp must identify its source field.
    """

    model_config = ConfigDict(frozen=True)

    value: datetime
    source_field: TimestampSourceField


class ObjectRef(BaseModel):
    """Identifies a specific Kubernetes object this project read."""

    model_config = ConfigDict(frozen=True)

    ref_type: Literal["object"] = "object"
    api_group: str  # "" for core/v1
    api_version: str
    kind: str
    namespace: str | None = None  # None for cluster-scoped kinds (unused in Milestone 1)
    name: str
    # Opaque per docs/research/event-timeline-contract.md §3 -- recorded for provenance only,
    # never compared and never used as a clock or ordering signal.
    resource_version: str | None = None


class ApiCallRef(BaseModel):
    """Identifies a specific API call this project issued (e.g. a bounded log fetch)."""

    model_config = ConfigDict(frozen=True)

    ref_type: Literal["api_call"] = "api_call"
    verb: str
    path: str
    params: dict[str, Any] = Field(default_factory=dict)
    response_status: int | None = None


SourceRef = Annotated[Union[ObjectRef, ApiCallRef], Field(discriminator="ref_type")]
