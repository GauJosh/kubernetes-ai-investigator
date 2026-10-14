"""Event-timeline semantics -- docs/research/event-timeline-contract.md."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from k8s_investigator.timeline import DEFAULT_EVENT_TTL_SECONDS, expiry_note, is_plausibly_expired, tag


def test_tag_always_attaches_source_field():
    value = tag(datetime.now(timezone.utc), "firstTimestamp")
    assert value.source_field == "firstTimestamp"


def test_not_plausibly_expired_within_ttl():
    now = datetime.now(timezone.utc)
    reference = tag(now - timedelta(minutes=5), "metadata.creationTimestamp")
    collected = tag(now, "collectedAt")
    assert is_plausibly_expired(reference, collected) is False


def test_plausibly_expired_beyond_ttl():
    now = datetime.now(timezone.utc)
    reference = tag(now - timedelta(seconds=DEFAULT_EVENT_TTL_SECONDS + 60), "metadata.creationTimestamp")
    collected = tag(now, "collectedAt")
    assert is_plausibly_expired(reference, collected) is True


def test_boundary_exactly_at_ttl_is_not_flagged():
    now = datetime.now(timezone.utc)
    reference = tag(now - timedelta(seconds=DEFAULT_EVENT_TTL_SECONDS), "metadata.creationTimestamp")
    collected = tag(now, "collectedAt")
    assert is_plausibly_expired(reference, collected) is False  # strictly greater-than, not >=


def test_expiry_note_is_hedged_not_asserted():
    now = datetime.now(timezone.utc)
    reference = tag(now - timedelta(hours=2), "metadata.creationTimestamp")
    collected = tag(now, "collectedAt")
    note = expiry_note(reference, collected)
    # must hedge ("may reflect", "not confirmed") -- never assert the event definitely did not happen
    assert "may reflect" in note
    assert "not confirmed" in note
    assert "did not occur" not in note
    assert "never happened" not in note
