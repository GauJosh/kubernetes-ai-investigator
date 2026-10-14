"""The deterministic rule engine: R1-R10, owner-chain facts, and contradiction detection.

Every rule here is a pure function of (bundle, relationships) -> list[Finding]: no network
calls, no datetime.now(), no randomness (docs/phase-0.8-mvp-blueprint.md §E's invariants). A
rule whose required evidence is missing or unavailable abstains or downgrades its claim --
it never guesses.

R1-R4 (Startup-fault) and R5-R10 (Scheduling-fault, Milestone 2) are evaluated independently and
are naturally non-overlapping in practice: an unscheduled Pod has not yet started any container,
so its containerStatuses list is empty and the R1-R4 per-container loop below finds nothing to
evaluate; evaluate_scheduling (rules/scheduling.py) internally checks
pod_status.is_scheduling_blocked and returns [] immediately for any pod that is not Pending/
unscheduled, so calling it unconditionally here is safe and does not need its own branch.
"""

from __future__ import annotations

from ..models.evidence import ContradictionEdge, EvidenceBundle, EvidenceItem, EvidenceStatus
from ..models.findings import Finding, FindingKind
from ..graph import RelationshipFacts, resolve_relationships
from .base import field_locator, line_locator, make_finding
from .contradictions import detect_contradictions, detect_scheduling_contradictions
from .patterns import (
    AUTH_FAILURE_SUBSTRINGS,
    CRASH_LOG_SUBSTRINGS,
    CRASH_LOOP_WAITING_REASON,
    DNS_FAILURE_SUBSTRINGS,
    NOT_FOUND_SUBSTRINGS,
    PULL_FAILURE_WAITING_REASONS,
    matches_any,
)
from .scheduling import evaluate_scheduling, find_scheduling_event


def evaluate_bundle(bundle: EvidenceBundle) -> tuple[list[Finding], list[ContradictionEdge]]:
    """Run the full Milestone 1 rule bank against a frozen bundle. Deterministic: the same
    bundle always produces the same findings (same ids, same content) -- see tests/test_rules.py."""
    relationships = resolve_relationships(bundle)
    pod = relationships.pod_item
    container_statuses = (pod.payload or {}).get("status", {}).get("containerStatuses") or []

    findings: list[Finding] = []
    findings.extend(_owner_chain_facts(relationships))
    findings.extend(_container_state_facts(pod, container_statuses))

    for idx, cs in enumerate(container_statuses):
        name = cs.get("name", f"container-{idx}")
        waiting_reason = ((cs.get("state") or {}).get("waiting") or {}).get("reason")

        if waiting_reason in PULL_FAILURE_WAITING_REASONS:
            findings.extend(
                _evaluate_pull_failure(
                    pod,
                    idx,
                    name,
                    waiting_reason,
                    relationships.event_items,
                    relationships.service_account_item,
                )
            )
        else:
            anchor = _crash_loop_anchor(pod, idx, cs)
            if anchor is not None:
                findings.extend(
                    _evaluate_crash_loop(
                        pod,
                        idx,
                        name,
                        anchor,
                        relationships.event_items,
                        relationships.log_items_by_container.get(name),
                    )
                )

    findings.extend(evaluate_scheduling(pod, relationships))

    contradictions = detect_contradictions(pod, container_statuses, relationships.event_items)
    scheduling_event = find_scheduling_event(relationships.event_items)
    if scheduling_event is not None:
        contradictions.extend(
            detect_scheduling_contradictions(pod, scheduling_event, relationships.node_items)
        )
    return findings, contradictions


# --- Owner-chain and baseline state facts ------------------------------------------------


def _owner_chain_facts(rel: RelationshipFacts) -> list[Finding]:
    facts: list[Finding] = []
    pod = rel.pod_item
    owner_refs = (pod.payload or {}).get("metadata", {}).get("ownerReferences") or []
    rs_idx = next((i for i, r in enumerate(owner_refs) if r.get("kind") == "ReplicaSet"), None)

    if rs_idx is not None:
        rs_name = owner_refs[rs_idx]["name"]
        loc = field_locator(pod, "metadata", "ownerReferences", rs_idx, "name")
        facts.append(
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=f"Pod is owned by ReplicaSet '{rs_name}'.",
                produced_by="graph:owner_chain",
                locators=[loc],
            )
        )
        if rel.replica_set_item is not None and rel.replica_set_item.status == EvidenceStatus.PRESENT:
            rs_owner_refs = (rel.replica_set_item.payload or {}).get("metadata", {}).get(
                "ownerReferences"
            ) or []
            d_idx = next(
                (i for i, r in enumerate(rs_owner_refs) if r.get("kind") == "Deployment"), None
            )
            if d_idx is not None:
                d_name = rs_owner_refs[d_idx]["name"]
                loc2 = field_locator(rel.replica_set_item, "metadata", "ownerReferences", d_idx, "name")
                facts.append(
                    make_finding(
                        kind=FindingKind.OBSERVED_FACT,
                        statement=f"ReplicaSet '{rs_name}' is owned by Deployment '{d_name}'.",
                        produced_by="graph:owner_chain",
                        locators=[loc2],
                    )
                )

    sa_name = (pod.payload or {}).get("spec", {}).get("serviceAccountName")
    if sa_name:
        loc = field_locator(pod, "spec", "serviceAccountName")
        facts.append(
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=f"Pod uses ServiceAccount '{sa_name}'.",
                produced_by="graph:service_account_link",
                locators=[loc],
            )
        )
    return facts


def _container_state_facts(pod: EvidenceItem, container_statuses: list[dict]) -> list[Finding]:
    facts: list[Finding] = []
    for idx, cs in enumerate(container_statuses):
        name = cs.get("name", f"container-{idx}")
        state = cs.get("state") or {}
        if (state.get("running") or {}).get("startedAt"):
            started = state["running"]["startedAt"]
            loc = field_locator(pod, "status", "containerStatuses", idx, "state", "running", "startedAt")
            statement = f"Container '{name}': Running since {started} (ready={cs.get('ready')})."
        elif (state.get("waiting") or {}).get("reason"):
            reason = state["waiting"]["reason"]
            loc = field_locator(pod, "status", "containerStatuses", idx, "state", "waiting", "reason")
            statement = f"Container '{name}': Waiting (reason={reason})."
        elif (state.get("terminated") or {}).get("reason"):
            terminated = state["terminated"]
            loc = field_locator(pod, "status", "containerStatuses", idx, "state", "terminated", "reason")
            statement = (
                f"Container '{name}': Terminated (reason={terminated.get('reason')}, "
                f"exitCode={terminated.get('exitCode')})."
            )
        else:
            continue  # nothing citable as a string leaf -- skip rather than fabricate
        facts.append(
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=statement,
                produced_by="graph:container_state",
                locators=[loc],
            )
        )
    return facts


# --- R1 / R2 / R3: image pull failures -----------------------------------------------------


def _evaluate_pull_failure(
    pod: EvidenceItem,
    idx: int,
    container_name: str,
    waiting_reason: str,
    event_items: list[EvidenceItem],
    sa_item: EvidenceItem | None,
) -> list[Finding]:
    reason_loc = field_locator(pod, "status", "containerStatuses", idx, "state", "waiting", "reason")

    for ev in event_items:
        message = (ev.payload or {}).get("message", "")
        if not message:
            continue

        if matches_any(message, DNS_FAILURE_SUBSTRINGS):
            event_loc = field_locator(ev, "message")
            return [
                make_finding(
                    kind=FindingKind.DETERMINISTIC_FINDING,
                    statement=(
                        f"Image pull for container '{container_name}' failed: DNS resolution "
                        f"failure contacting the registry ({message!r})."
                    ),
                    produced_by="rule:R1_dns_failure",
                    locators=[event_loc],
                    limitation=(
                        "Matched a known class of containerd DNS-failure wording ('no such "
                        "host' / 'server misbehaving'); other container runtimes may phrase "
                        "this differently and would not be caught by this rule."
                    ),
                )
            ]

        if matches_any(message, AUTH_FAILURE_SUBSTRINGS):
            return _evaluate_auth_failure(pod, ev, container_name, sa_item)

        if matches_any(message, NOT_FOUND_SUBSTRINGS):
            event_loc = field_locator(ev, "message")
            return [
                make_finding(
                    kind=FindingKind.DETERMINISTIC_FINDING,
                    statement=(
                        f"Image pull for container '{container_name}' failed: the image "
                        f"reference was not found by the registry ({message!r})."
                    ),
                    produced_by="rule:R2_tag_not_found",
                    locators=[event_loc],
                    limitation=(
                        "States only that the registry reports the reference does not exist; "
                        "cannot distinguish a typo from a deleted or never-published image."
                    ),
                )
            ]

    # No event matched a recognized class -- abstain, distinguishing "no events at all" from
    # "events existed but none matched" so the report says exactly what is known.
    if event_items:
        statement = (
            f"Container '{container_name}' is waiting with reason '{waiting_reason}', but none "
            f"of the {len(event_items)} event(s) collected matched a recognized failure "
            "pattern (DNS failure, authorization failure, or image-not-found)."
        )
    else:
        statement = (
            f"Container '{container_name}' is in an image-pull-failure state, but no events "
            "were found for this pod -- the specific pull-failure cause cannot be determined "
            "from the evidence collected."
        )
    return [
        make_finding(
            kind=FindingKind.OBSERVED_FACT,
            statement=statement,
            produced_by="rule:image_pull_unrecognized",
            locators=[reason_loc],
        )
    ]


def _evaluate_auth_failure(
    pod: EvidenceItem, ev: EvidenceItem, container_name: str, sa_item: EvidenceItem | None
) -> list[Finding]:
    event_loc = field_locator(ev, "message")
    pod_secrets = [
        s.get("name") for s in ((pod.payload or {}).get("spec", {}).get("imagePullSecrets") or []) if s.get("name")
    ]
    pod_locators = [
        field_locator(pod, "spec", "imagePullSecrets", i, "name")
        for i, s in enumerate((pod.payload or {}).get("spec", {}).get("imagePullSecrets") or [])
        if s.get("name")
    ]

    if sa_item is None or sa_item.status != EvidenceStatus.PRESENT:
        statement = (
            f"Image pull for container '{container_name}' failed authorization "
            f"({(ev.payload or {}).get('message', '')!r}). The Pod's own imagePullSecrets "
            f"reference(s): {pod_secrets or 'none'}. The ServiceAccount could not be read "
            "(evidence unavailable), so the effective pull-secret set (Pod + ServiceAccount) "
            "cannot be fully established."
        )
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=statement,
                produced_by="rule:R3_auth_failure_sa_unavailable",
                locators=[event_loc, *pod_locators],
                limitation="ServiceAccount read failed; effective pull-secret configuration unknown.",
            )
        ]

    sa_secret_entries = (sa_item.payload or {}).get("imagePullSecrets") or []
    sa_secrets = [s.get("name") for s in sa_secret_entries if s.get("name")]
    sa_locators = [
        field_locator(sa_item, "imagePullSecrets", i, "name")
        for i, s in enumerate(sa_secret_entries)
        if s.get("name")
    ]
    effective = sorted(set(pod_secrets) | set(sa_secrets))
    sa_name = sa_item.source_ref.name if hasattr(sa_item.source_ref, "name") else "?"

    if not effective:
        statement = (
            f"Image pull for container '{container_name}' failed authorization "
            f"({(ev.payload or {}).get('message', '')!r}), and no image pull secret is "
            f"configured on the Pod or on ServiceAccount '{sa_name}'."
        )
        rule_id = "rule:R3_auth_failure_no_secret"
    else:
        statement = (
            f"Image pull for container '{container_name}' failed authorization despite image "
            f"pull secret(s) {effective} being referenced on the Pod and/or ServiceAccount "
            f"'{sa_name}'; possible wrong, expired, or misconfigured credential."
        )
        rule_id = "rule:R3_auth_failure_secret_configured"

    return [
        make_finding(
            kind=FindingKind.DETERMINISTIC_FINDING,
            statement=statement,
            produced_by=rule_id,
            locators=[event_loc, *pod_locators, *sa_locators],
            limitation=(
                "This project never reads Secret object contents; it can confirm only "
                "whether a pull secret is referenced, not whether its credentials are correct."
            ),
        )
    ]


# --- R4: log-assisted crash diagnosis -------------------------------------------------------


def _crash_loop_anchor(pod: EvidenceItem, idx: int, cs: dict):
    state = cs.get("state") or {}
    if (state.get("waiting") or {}).get("reason") == CRASH_LOOP_WAITING_REASON:
        return field_locator(pod, "status", "containerStatuses", idx, "state", "waiting", "reason")
    last_terminated = (cs.get("lastState") or {}).get("terminated") or {}
    if last_terminated.get("reason") and (cs.get("restartCount") or 0) > 0:
        return field_locator(
            pod, "status", "containerStatuses", idx, "lastState", "terminated", "reason"
        )
    return None


def _evaluate_crash_loop(
    pod: EvidenceItem,
    idx: int,
    container_name: str,
    anchor_locator,
    event_items: list[EvidenceItem],
    log_item: EvidenceItem | None,
) -> list[Finding]:
    # Defensive: don't double-fire if a pull-failure pattern already explains this container --
    # R1-R3 and R4 are evaluated on different waiting-reason branches in evaluate_bundle, but a
    # crash-loop can still show up in events that *also* mention a pull failure in rare cases.
    for ev in event_items:
        message = (ev.payload or {}).get("message", "")
        if matches_any(message, DNS_FAILURE_SUBSTRINGS) or matches_any(
            message, AUTH_FAILURE_SUBSTRINGS
        ) or matches_any(message, NOT_FOUND_SUBSTRINGS):
            return []

    if log_item is None or log_item.status != EvidenceStatus.PRESENT or not isinstance(
        log_item.payload, str
    ):
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    f"Container '{container_name}' is crash-looping, but its logs were not "
                    "available to this investigation, so the cause cannot be determined from "
                    "the evidence collected."
                ),
                produced_by="rule:R4_logs_unavailable",
                locators=[anchor_locator],
            )
        ]

    lines = log_item.payload.splitlines()
    for i, line in enumerate(lines):
        match = matches_any(line, CRASH_LOG_SUBSTRINGS)
        if match:
            loc = line_locator(log_item, i, i)
            return [
                make_finding(
                    kind=FindingKind.DETERMINISTIC_FINDING,
                    statement=(
                        f"Container '{container_name}' is crash-looping; its logs contain a "
                        f"recognized fatal-error pattern ('{match}'): {line!r}."
                    ),
                    produced_by="rule:R4_log_pattern_match",
                    locators=[anchor_locator, loc],
                    limitation=(
                        "The matched substring set is small and intentionally "
                        "non-exhaustive -- this rule's purpose is to make 'insufficient "
                        "evidence' a first-class outcome, not to maximize coverage."
                    ),
                )
            ]

    return [
        make_finding(
            kind=FindingKind.OBSERVED_FACT,
            statement=(
                f"Container '{container_name}' is crash-looping, and its logs were checked, "
                "but no recognized fatal-error pattern was found within the bounded log "
                "collection window. The evidence collected is insufficient to determine a "
                "root cause."
            ),
            produced_by="rule:R4_insufficient_evidence",
            locators=[anchor_locator],
            limitation=(
                "Logs are bounded (tail/time-limited); a relevant line outside that window "
                "would not have been seen. Absence of a match is not proof the cause is "
                "unrelated to any of the known patterns."
            ),
        )
    ]
