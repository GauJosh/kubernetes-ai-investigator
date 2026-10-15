"""R11: Runtime-health-fault category (Phase 0.13) -- liveness/readiness probe-failure events.

Follows the same invariants as every other rule in this bank (rules/engine.py, rules/
scheduling.py): pure function of (pod, event_items) -> list[Finding]; no network calls, no
datetime.now(), no randomness; abstains or downgrades rather than guesses when required evidence
is missing or ambiguous.

R11 is deliberately a separate category from R4 (rules/engine.py's crash-loop log diagnosis),
not folded into it: R4 triggers on container *state* and reasons from *log text*; R11 triggers on
an *Event* and reasons from Pod/container *status fields* -- a different evidence shape, and a
genuinely earlier signal (a liveness-probe failure can occur before a container ever reaches
CrashLoopBackOff). The two are complementary, not competing explanations for the same symptom --
unlike R1/R2/R3, which are mutually exclusive causes of the same ImagePullBackOff state and must
not double-fire, R11 and R4 are intentionally allowed to fire together
(see tests/test_rules.py's test_r11_coexists_with_r4_log_pattern).

The only claim R11 ever makes: a recorded probe-failure Event is consistent with the container's
*current* status field. It never asserts why the probe is failing -- see each finding's
``limitation``.
"""

from __future__ import annotations

import re

from ..models.evidence import EvidenceItem
from ..models.findings import Finding, FindingKind
from .base import field_locator, make_finding
from .patterns import (
    LIVENESS_PROBE_FAILURE_SUBSTRING,
    READINESS_PROBE_FAILURE_SUBSTRING,
    UNHEALTHY_EVENT_REASON,
)

_FIELD_PATH_CONTAINER_PATTERN = re.compile(r"^spec\.containers\{(.+)\}$")


def evaluate_health(pod: EvidenceItem, event_items: list[EvidenceItem]) -> list[Finding]:
    """Entry point wired into rules/engine.py's evaluate_bundle. Returns [] when no Unhealthy
    event exists -- absence of the trigger is not itself a finding, same convention as R1-R10."""
    container_statuses = (pod.payload or {}).get("status", {}).get("containerStatuses") or []
    unhealthy_events = [
        ev for ev in event_items if (ev.payload or {}).get("reason") == UNHEALTHY_EVENT_REASON
    ]
    if not unhealthy_events:
        return []

    liveness_event = _first_matching(unhealthy_events, LIVENESS_PROBE_FAILURE_SUBSTRING)
    readiness_event = _first_matching(unhealthy_events, READINESS_PROBE_FAILURE_SUBSTRING)

    findings: list[Finding] = []
    if liveness_event is not None:
        findings.extend(_evaluate_probe_class(pod, liveness_event, container_statuses, "liveness"))
    if readiness_event is not None:
        findings.extend(_evaluate_probe_class(pod, readiness_event, container_statuses, "readiness"))

    if liveness_event is None and readiness_event is None:
        event = unhealthy_events[0]
        message = (event.payload or {}).get("message", "")
        findings.append(
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    f"An 'Unhealthy' event was found ({message!r}), but it did not match a "
                    "recognized liveness- or readiness-probe-failure pattern."
                ),
                produced_by="rule:R11_probe_failure_unrecognized",
                locators=[field_locator(event, "message")],
            )
        )
    return findings


def _first_matching(events: list[EvidenceItem], substring: str) -> EvidenceItem | None:
    for ev in events:
        message = (ev.payload or {}).get("message", "")
        if substring in message.lower():
            return ev
    return None


def _evaluate_probe_class(
    pod: EvidenceItem, event: EvidenceItem, container_statuses: list[dict], probe_kind: str
) -> list[Finding]:
    event_loc = field_locator(event, "message")
    message = (event.payload or {}).get("message", "")

    container_idx, container_name = _resolve_container_from_field_path(event, container_statuses)
    if container_idx is None:
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    f"A {probe_kind}-probe-failure event was found ({message!r}), but the "
                    "affected container could not be determined from the evidence collected."
                ),
                produced_by="rule:R11_container_not_identified",
                locators=[event_loc],
            )
        ]

    count = (event.payload or {}).get("count") or 1
    repetition_note = f" (recorded {count} times)" if count > 1 else ""

    if probe_kind == "liveness":
        restart_count = container_statuses[container_idx].get("restartCount") or 0
        status_loc = field_locator(
            pod, "status", "containerStatuses", container_idx, "restartCount"
        )
        if restart_count > 0:
            return [
                make_finding(
                    kind=FindingKind.DETERMINISTIC_FINDING,
                    statement=(
                        f"Container '{container_name}' has a recorded liveness-probe-failure "
                        f"event{repetition_note} ({message!r}), consistent with its current "
                        f"restartCount of {restart_count}."
                    ),
                    produced_by="rule:R11_liveness_probe_failure",
                    locators=[event_loc, status_loc],
                    limitation=(
                        "This finding confirms a liveness-probe-failure event was recorded and "
                        "is consistent with the container's current restart count; it does not "
                        "establish why the probe is failing (application bug, slow dependency, "
                        "misconfigured probe timeout/endpoint) -- that would require evidence "
                        "this rule does not interpret."
                    ),
                )
            ]
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    f"A liveness-probe-failure event was found for container '{container_name}' "
                    f"({message!r}), but its restartCount is {restart_count} -- the event and "
                    "the container's current status do not corroborate each other."
                ),
                produced_by="rule:R11_status_not_corroborating",
                locators=[event_loc, status_loc],
            )
        ]

    # readiness
    ready = container_statuses[container_idx].get("ready")
    status_loc = field_locator(pod, "status", "containerStatuses", container_idx, "ready")
    if ready is False:
        return [
            make_finding(
                kind=FindingKind.DETERMINISTIC_FINDING,
                statement=(
                    f"Container '{container_name}' has a recorded readiness-probe-failure "
                    f"event{repetition_note} ({message!r}), consistent with its current "
                    "ready=false status."
                ),
                produced_by="rule:R11_readiness_probe_failure",
                locators=[event_loc, status_loc],
                limitation=(
                    "This finding confirms a readiness-probe-failure event was recorded and is "
                    "consistent with the container's current ready status; it does not "
                    "establish why the probe is failing -- that would require evidence this "
                    "rule does not interpret."
                ),
            )
        ]
    return [
        make_finding(
            kind=FindingKind.OBSERVED_FACT,
            statement=(
                f"A readiness-probe-failure event was found for container '{container_name}' "
                f"({message!r}), but its ready status is {ready!r} -- the event and the "
                "container's current status do not corroborate each other."
            ),
            produced_by="rule:R11_status_not_corroborating",
            locators=[event_loc, status_loc],
        )
    ]


def _resolve_container_from_field_path(
    event: EvidenceItem, container_statuses: list[dict]
) -> tuple[int | None, str | None]:
    """Strict resolution only: the event's involvedObject.fieldPath must match the exact
    "spec.containers{<name>}" shape Kubernetes uses for a container-scoped event, and that name
    must be present among the collected containerStatuses. No single-container fallback --
    identification either succeeds on real evidence or the rule abstains (approved design,
    docs/phase-0.13-probe-failure-rule-design.md)."""
    field_path = ((event.payload or {}).get("involvedObject") or {}).get("fieldPath") or ""
    match = _FIELD_PATH_CONTAINER_PATTERN.match(field_path)
    if not match:
        return None, None
    name = match.group(1)
    for idx, cs in enumerate(container_statuses):
        if cs.get("name") == name:
            return idx, name
    return None, None
