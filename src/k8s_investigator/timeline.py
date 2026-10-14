"""Event and timeline interpretation helpers.

Implements the conservative contract in docs/research/event-timeline-contract.md. This module
exists specifically so that no other module has to re-derive the rules about what a timestamp
means or when an absence may be flagged as plausibly explained by TTL expiry.
"""

from __future__ import annotations

from datetime import datetime

from .models.refs import TimestampSourceField, TimestampValue

# Verified default (docs/research/landscape-research.md §6.1; kube-apiserver's EventTTL option
# defaults to 1 hour). This is a *default*, not a guarantee -- managed clusters may configure a
# different value, which this project has no way to observe. It is used only to decide whether
# an absence is worth flagging as *plausibly* expiry-related, never to assert that it is.
DEFAULT_EVENT_TTL_SECONDS = 3600


def tag(value: datetime, source_field: TimestampSourceField) -> TimestampValue:
    """Wrap a raw datetime with the field it came from. Never pass a bare datetime around."""
    return TimestampValue(value=value, source_field=source_field)


def is_plausibly_expired(
    reference_time: TimestampValue,
    collected_at: TimestampValue,
    ttl_seconds: int = DEFAULT_EVENT_TTL_SECONDS,
) -> bool:
    """Would an event onset at ``reference_time`` plausibly have been evicted by ``collected_at``?

    This is a *flag*, not a proof -- per docs/research/event-timeline-contract.md §7, mechanical
    validation can only ever say "this is a live, disclosable possibility," never "this is why
    the event is missing." Callers must phrase the resulting evidence_gap/finding text
    accordingly (see rules/engine.py's gap annotation).
    """
    elapsed = (collected_at.value - reference_time.value).total_seconds()
    return elapsed > ttl_seconds


def expiry_note(reference_time: TimestampValue, collected_at: TimestampValue) -> str:
    """The exact, hedged wording used whenever ``is_plausibly_expired`` is true."""
    return (
        "no matching event was found; the investigated pod's "
        f"{reference_time.source_field} ({reference_time.value.isoformat()}) is more than "
        f"{DEFAULT_EVENT_TTL_SECONDS}s before collection "
        f"({collected_at.value.isoformat()}), so absence may reflect the ~1h event TTL "
        "window rather than an event that never occurred -- not confirmed, flagged only."
    )
