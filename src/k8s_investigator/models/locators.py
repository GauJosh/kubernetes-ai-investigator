"""Typed evidence locators.

Correction 2 of docs/phase-0.8-mvp-blueprint.md (revision 2): a Finding citing "the pod" or
"the log chunk" is not precise enough to enforce the evidence-first principle mechanically -- a
multi-line log EvidenceItem supports a claim only at one line range within it. EvidenceLocator
makes the cited sub-region of an EvidenceItem a typed, validated value, not prose.

Resolution and validation of a locator against a real bundle live in validation.py, not here --
this module holds pure data shapes only.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class LineRange(BaseModel):
    """Addressing into an unstructured text payload (e.g. a log chunk).

    0-indexed, inclusive on both ends, into ``payload.splitlines()`` of the referenced item.
    """

    model_config = ConfigDict(frozen=True)

    pointer_type: Literal["line_range"] = "line_range"
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=0)


class FieldPath(BaseModel):
    """Addressing into a structured JSON payload (e.g. a Pod, Event, or ServiceAccount object).

    An RFC 6901 JSON Pointer, e.g. "/status/containerStatuses/0/state/waiting/reason".
    """

    model_config = ConfigDict(frozen=True)

    pointer_type: Literal["field_path"] = "field_path"
    json_pointer: str


Pointer = Annotated[Union[LineRange, FieldPath], Field(discriminator="pointer_type")]


class EvidenceLocator(BaseModel):
    """A precise, validated citation: one EvidenceItem, one sub-region of it, one quote."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str  # must resolve to an EvidenceItem.id actually in the bundle -- validated, not assumed
    pointer: Pointer
    quoted_text: str  # the excerpt as captured; re-checked against the bundle at validation time
