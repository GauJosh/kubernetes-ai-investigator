"""The small, named, intentionally non-exhaustive substring sets R1-R4 match against.

Every substring here is taken verbatim from a real case examined in
docs/research/cloud-opsbench-evidence-feasibility.md, not invented. Each rule's design note in
docs/phase-0.8-mvp-blueprint.md §E states explicitly that these sets are not claimed to be
exhaustive -- an unmatched case must fall through to the next rule (R4's insufficiency fallback
at the end), never be silently dropped.
"""

from __future__ import annotations

# Container waiting reasons that mean "an image pull failed" -- the shape R1/R2/R3 all trigger on.
PULL_FAILURE_WAITING_REASONS = frozenset({"ErrImagePull", "ImagePullBackOff"})

# R1 -- registry DNS failure. Verified verbatim in Phase 0.6 (startup/1, startup/2):
# "... dial tcp: lookup ghost-registry.example.net on 127.0.0.53:53: no such host"
DNS_FAILURE_SUBSTRINGS = (
    "no such host",
    "server misbehaving",
)

# R3 -- registry authorization failure. Verified verbatim in Phase 0.6 (startup/42, startup/43):
# "... 403 Forbidden" / "failed to authorize: failed to fetch anonymous token"
AUTH_FAILURE_SUBSTRINGS = (
    "403 forbidden",
    "401 unauthorized",
    "failed to authorize",
    "unauthorized",
)

# R2 -- image tag/reference not found. Verified verbatim in Phase 0.6 (startup/14, startup/15):
# "rpc error: code = NotFound desc = failed to pull and unpack image ...: ... not found"
NOT_FOUND_SUBSTRINGS = (
    "not found",
    "manifest unknown",
)

# Container waiting/terminated shapes that mean "this container is crash-looping" -- R4's trigger.
CRASH_LOOP_WAITING_REASON = "CrashLoopBackOff"

# R4 -- log-assisted crash diagnosis. The first substring is verified verbatim in Phase 0.6's
# sensitivity check (startup/60): "Fatal error: can't open the RDB file dump.rdb for reading:
# Permission denied". The other two are small, named, equally non-exhaustive additions for the
# same failure class (filesystem/resource exhaustion), not independently verified against a
# Cloud-OpsBench case -- flagged here, not hidden.
CRASH_LOG_SUBSTRINGS = (
    "permission denied",
    "out of memory",
    "no space left on device",
)


def matches_any(haystack: str, substrings: tuple[str, ...]) -> str | None:
    """Case-insensitive substring match. Returns the matched substring, or None."""
    lowered = haystack.lower()
    for substring in substrings:
        if substring in lowered:
            return substring
    return None
