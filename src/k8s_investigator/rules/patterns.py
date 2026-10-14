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


# --- Milestone 2 (R5-R10, Scheduling-fault category) -----------------------------------------
#
# A single FailedScheduling event message is a comma-separated list of per-node reasons, e.g.
# "0/4 nodes are available: 1 node(s) were unschedulable, 3 Insufficient cpu." Verified in
# Phase 0.6: the "N node(s) were unschedulable" clause (referring to a cordoned control-plane
# node) is present in almost every scheduling-failure message, EVEN WHEN that is not the real
# cause -- so it must be checked LAST, after every more specific pattern, never first. The
# priority order below (R7 -> R9 -> R8 -> R6 -> R5) is deliberate, not arbitrary, and matches
# exactly this evidence.

# R10 -- PVC unbound. Verified verbatim in Phase 0.6 (scheduling/137):
# "no persistent volumes available for this claim and no storage class is set" /
# "pod has unbound immediate PersistentVolumeClaims"
PVC_UNBOUND_SUBSTRINGS = (
    "no persistent volumes available",
    "unbound immediate persistentvolumeclaims",
)

# R7 -- taint/toleration mismatch. Verified verbatim (scheduling/141):
# "1 node(s) had untolerated taint {critical: true}"
TAINT_SUBSTRING = "untolerated taint"

# R9 -- pod anti-affinity conflict. Verified verbatim (scheduling/112):
# "3 node(s) didn't match pod anti-affinity rules"
ANTI_AFFINITY_SUBSTRING = "didn't match pod anti-affinity rules"

# R8 -- node selector/affinity mismatch. Verified verbatim (scheduling/56, scheduling/80) --
# the Kubernetes scheduler uses the *same* wording for both nodeSelector- and affinity-based
# mismatches (docs/research/cloud-opsbench-evidence-feasibility.md §7), so this one substring
# covers both; which field actually governs is resolved from the Pod spec, not this text.
NODE_AFFINITY_OR_SELECTOR_SUBSTRING = "didn't match pod's node affinity/selector"

# R6 -- resource capacity mismatch. Verified verbatim (scheduling/17, scheduling/35):
# "3 Insufficient cpu." / "3 Insufficient memory."
INSUFFICIENT_CPU_SUBSTRING = "insufficient cpu"
INSUFFICIENT_MEMORY_SUBSTRING = "insufficient memory"

# R5 -- node cordon. Verified verbatim (scheduling/1): "4 node(s) were unschedulable."
# Checked LAST among the node-related patterns -- see the module note above.
UNSCHEDULABLE_SUBSTRING = "were unschedulable"


def parse_cpu_quantity(value: str | None) -> float | None:
    """Kubernetes CPU quantities: a bare number is cores; a trailing 'm' is millicores.
    Returns cores as a float, or None if unparseable. Deliberately minimal -- covers the two
    forms actually seen in Phase 0.6's fixtures, not the full Kubernetes quantity grammar."""
    if not value:
        return None
    value = value.strip()
    try:
        if value.endswith("m"):
            return float(value[:-1]) / 1000.0
        return float(value)
    except ValueError:
        return None


_MEMORY_SUFFIXES = {
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
    "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
}


def parse_memory_quantity(value: str | None) -> int | None:
    """Kubernetes memory quantities: a bare number is bytes; a binary (Ki/Mi/Gi/Ti) or decimal
    (K/M/G/T) suffix scales it. Returns bytes as an int, or None if unparseable. Minimal, same
    scope note as parse_cpu_quantity."""
    if not value:
        return None
    value = value.strip()
    for suffix, multiplier in sorted(_MEMORY_SUFFIXES.items(), key=lambda kv: -len(kv[0])):
        if value.endswith(suffix):
            try:
                return int(float(value[: -len(suffix)]) * multiplier)
            except ValueError:
                return None
    try:
        return int(value)
    except ValueError:
        return None
