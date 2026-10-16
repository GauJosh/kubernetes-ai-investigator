"""R5-R10: Scheduling-fault rules (Milestone 2).

Trigger priority is deliberate, not arbitrary: a FailedScheduling event's message is a
comma-separated list of per-node reasons, and the generic "N node(s) were unschedulable" clause
(referring to a permanently-cordoned control-plane node) is present in almost every message --
so every *more specific* pattern is checked first, and "unschedulable" (R5) is checked last.
See rules/patterns.py's module note for the exact verified evidence this ordering is based on.

Every rule here follows the same invariants as R1-R4 (rules/engine.py): pure function of
(pod, event, relationships) -> list[Finding]; no network calls, no datetime.now(), no
randomness; abstains or downgrades rather than guesses when required evidence is missing.
"""

from __future__ import annotations

from ..models.evidence import EvidenceItem, EvidenceStatus
from ..models.findings import Finding, FindingKind
from ..pod_status import is_scheduling_blocked
from .base import field_locator, make_finding
from .patterns import (
    ANTI_AFFINITY_SUBSTRING,
    INSUFFICIENT_CPU_SUBSTRING,
    INSUFFICIENT_MEMORY_SUBSTRING,
    NODE_AFFINITY_OR_SELECTOR_SUBSTRING,
    PVC_UNBOUND_SUBSTRINGS,
    TAINT_SUBSTRING,
    UNSCHEDULABLE_SUBSTRING,
    matches_any,
)

_SCHEDULING_FAILURE_EVENT_REASONS = frozenset({"FailedScheduling"})


def evaluate_scheduling(pod: EvidenceItem, relationships) -> list[Finding]:
    """Entry point wired into rules/engine.py's evaluate_bundle. Returns [] immediately for any
    pod that isn't scheduling-blocked -- R5-R10 only ever apply to a Pending, unscheduled pod."""
    pod_payload = pod.payload or {}
    if not is_scheduling_blocked(pod_payload):
        return []

    scheduling_event = find_scheduling_event(relationships.event_items)
    if scheduling_event is None:
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    "The Pod is unscheduled (PodScheduled=False), but no FailedScheduling "
                    "event was found among the events collected -- the specific scheduling "
                    "obstacle cannot be determined from the evidence collected."
                ),
                produced_by="rule:scheduling_no_event",
                locators=[field_locator(pod, "status", "phase")],
            )
        ]

    message = (scheduling_event.payload or {}).get("message", "")
    lowered = message.lower()

    if matches_any(message, PVC_UNBOUND_SUBSTRINGS):
        return _evaluate_r10(pod, scheduling_event, relationships)
    if TAINT_SUBSTRING in lowered:
        return _evaluate_r7(pod, scheduling_event, relationships)
    if ANTI_AFFINITY_SUBSTRING in lowered:
        return _evaluate_r9(pod, scheduling_event, relationships)
    if NODE_AFFINITY_OR_SELECTOR_SUBSTRING in lowered:
        return _evaluate_r8(pod, scheduling_event, relationships)
    if INSUFFICIENT_CPU_SUBSTRING in lowered or INSUFFICIENT_MEMORY_SUBSTRING in lowered:
        return _evaluate_r6(pod, scheduling_event, relationships)
    if UNSCHEDULABLE_SUBSTRING in lowered:
        return _evaluate_r5(pod, scheduling_event, relationships)

    return [
        make_finding(
            kind=FindingKind.OBSERVED_FACT,
            statement=(
                f"The Pod is unscheduled; a FailedScheduling event was found "
                f"({message!r}), but it did not match any of this rule bank's recognized "
                "patterns (cordon, resource capacity, taint, node affinity/selector, pod "
                "anti-affinity, unbound PVC)."
            ),
            produced_by="rule:scheduling_unrecognized",
            locators=[field_locator(scheduling_event, "message")],
        )
    ]


def find_scheduling_event(event_items: list[EvidenceItem]) -> EvidenceItem | None:
    for ev in event_items:
        payload = ev.payload or {}
        if payload.get("reason") in _SCHEDULING_FAILURE_EVENT_REASONS:
            return ev
    return None


# --- R5: Node cordon -------------------------------------------------------------------------


def _evaluate_r5(pod: EvidenceItem, event: EvidenceItem, rel) -> list[Finding]:
    event_loc = field_locator(event, "message")

    if not rel.node_items:
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    "The scheduler reported nodes as unschedulable, but Node data could not "
                    "be collected (list failed or returned nothing) to confirm which, or how "
                    "many, nodes are actually cordoned."
                ),
                produced_by="rule:R5_nodes_unavailable",
                locators=[event_loc],
            )
        ]

    cordoned = [n for n in rel.node_items if (n.payload or {}).get("spec", {}).get("unschedulable")]
    total = len(rel.node_items)

    if len(cordoned) == total:
        locators = [event_loc] + [
            field_locator(n, "spec", "unschedulable") for n in cordoned
        ]
        return [
            make_finding(
                kind=FindingKind.DETERMINISTIC_FINDING,
                statement=f"All {total} known node(s) are cordoned (spec.unschedulable=true).",
                produced_by="rule:R5_all_nodes_cordoned",
                locators=locators,
                limitation=(
                    "States only that every known node is cordoned, not why -- cordoning may "
                    "be intentional maintenance rather than a misconfiguration."
                ),
            )
        ]

    if cordoned:
        locators = [event_loc] + [field_locator(n, "spec", "unschedulable") for n in cordoned]
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    f"{len(cordoned)} of {total} known node(s) are cordoned, but not all -- "
                    "this alone does not fully explain the scheduling failure; another, "
                    "unmatched cause may also be present."
                ),
                produced_by="rule:R5_partial_cordon",
                locators=locators,
            )
        ]

    return [
        make_finding(
            kind=FindingKind.OBSERVED_FACT,
            statement=(
                "The scheduler reported nodes as unschedulable, but none of the known nodes "
                "show spec.unschedulable=true -- the specific cause could not be confirmed "
                "from the Node evidence collected."
            ),
            produced_by="rule:R5_no_cordoned_node_found",
            locators=[event_loc],
        )
    ]


# --- R6: Resource capacity mismatch ------------------------------------------------------------


def _evaluate_r6(pod: EvidenceItem, event: EvidenceItem, rel) -> list[Finding]:
    """Cites the pod's request and nodes' *allocatable* capacity as supporting context --
    deliberately does not re-derive "insufficient" itself: allocatable capacity is not the
    same as *available* capacity (other pods' requests on each node are not evidence this
    project collects), so only the scheduler's own authoritative report is treated as the
    claim; the numbers are cited alongside it, not instead of it."""
    event_loc = field_locator(event, "message")
    containers = (pod.payload or {}).get("spec", {}).get("containers") or []

    is_cpu = "insufficient cpu" in (event.payload or {}).get("message", "").lower()
    resource = "cpu" if is_cpu else "memory"

    locators = [event_loc]
    request_descriptions = []
    for idx, container in enumerate(containers):
        raw = (container.get("resources", {}).get("requests") or {}).get(resource)
        if raw is None:
            continue
        locators.append(field_locator(pod, "spec", "containers", idx, "resources", "requests", resource))
        request_descriptions.append(f"{container.get('name', f'container-{idx}')}={raw}")

    node_descriptions = []
    if not rel.node_items:
        node_gap_note = " Node allocatable data could not be collected."
    else:
        node_gap_note = ""
        for n in rel.node_items:
            allocatable = (n.payload or {}).get("status", {}).get("allocatable", {}).get(resource)
            if allocatable is None:
                continue
            locators.append(field_locator(n, "status", "allocatable", resource))
            node_name = n.source_ref.name if hasattr(n.source_ref, "name") else "?"
            node_descriptions.append(f"{node_name}={allocatable}")

    statement = (
        f"The scheduler reported insufficient {resource} ({(event.payload or {}).get('message', '')!r}). "
        f"Pod container {resource} request(s): {request_descriptions or 'not found'}. "
        f"Node allocatable {resource} (total capacity, not accounting for other pods already "
        f"scheduled): {node_descriptions or 'not found'}.{node_gap_note}"
    )
    return [
        make_finding(
            kind=FindingKind.DETERMINISTIC_FINDING,
            statement=statement,
            produced_by="rule:R6_resource_capacity",
            locators=locators,
            limitation=(
                "Allocatable capacity is cited as context, not as independent proof of "
                "insufficiency -- this project does not collect other pods' resource usage, "
                "so it cannot independently verify *available* capacity; the scheduler's own "
                "report is the authoritative claim here."
            ),
        )
    ]


# --- R7: Taint/toleration mismatch --------------------------------------------------------------


def _evaluate_r7(pod: EvidenceItem, event: EvidenceItem, rel) -> list[Finding]:
    event_loc = field_locator(event, "message")
    schedulable = _schedulable(rel.node_items)

    for node in schedulable:
        taints = (node.payload or {}).get("spec", {}).get("taints") or []
        for idx, taint in enumerate(taints):
            if taint.get("effect") in ("NoSchedule", "NoExecute") and not _pod_tolerates(pod, taint):
                locators = [
                    event_loc,
                    field_locator(node, "spec", "taints", idx, "key"),
                    field_locator(node, "spec", "taints", idx, "effect"),
                ]
                if taint.get("value") is not None:
                    locators.append(field_locator(node, "spec", "taints", idx, "value"))
                return [
                    make_finding(
                        kind=FindingKind.DETERMINISTIC_FINDING,
                        statement=(
                            f"Node '{getattr(node.source_ref, 'name', '?')}' has taint "
                            f"{taint.get('key')}={taint.get('value')}:{taint.get('effect')}, "
                            "which the Pod's tolerations do not match."
                        ),
                        produced_by="rule:R7_taint_toleration_mismatch",
                        locators=locators,
                    )
                ]

    return [
        make_finding(
            kind=FindingKind.OBSERVED_FACT,
            statement=(
                "The scheduler reported an untolerated taint, but no schedulable node's taint "
                "could be matched against the Pod's tolerations from the evidence collected."
            ),
            produced_by="rule:R7_taint_not_identified",
            locators=[event_loc],
        )
    ]


def _pod_tolerates(pod: EvidenceItem, taint: dict) -> bool:
    tolerations = (pod.payload or {}).get("spec", {}).get("tolerations") or []
    for tol in tolerations:
        if tol.get("operator") == "Exists" and tol.get("key") == taint.get("key"):
            return True
        if tol.get("key") == taint.get("key") and tol.get("value") == taint.get("value"):
            return True
    return False


# --- R8: Node selector / affinity mismatch ------------------------------------------------------


def _evaluate_r8(pod: EvidenceItem, event: EvidenceItem, rel) -> list[Finding]:
    event_loc = field_locator(event, "message")
    node_selector = (pod.payload or {}).get("spec", {}).get("nodeSelector") or {}
    schedulable = _schedulable(rel.node_items)

    if node_selector:
        key = next(iter(node_selector))
        required_value = node_selector[key]
        locators = [event_loc, field_locator(pod, "spec", "nodeSelector", key)]
        contradicting = _first_label_mismatch(schedulable, key, required_value, locators)
        return [
            make_finding(
                kind=FindingKind.DETERMINISTIC_FINDING,
                statement=(
                    f"Pod requires nodeSelector '{key}={required_value}'; no known schedulable "
                    f"node has a matching label.{contradicting}"
                ),
                produced_by="rule:R8_node_selector_mismatch",
                locators=locators,
            )
        ]

    affinity_key, affinity_value, affinity_locators = _first_required_node_affinity_term(pod)
    if affinity_key is not None:
        locators = [event_loc, *affinity_locators]
        contradicting = _first_label_mismatch(schedulable, affinity_key, affinity_value, locators)
        return [
            make_finding(
                kind=FindingKind.DETERMINISTIC_FINDING,
                statement=(
                    f"Pod requires node affinity '{affinity_key} In [{affinity_value}, ...]'; "
                    f"no known schedulable node has a matching label.{contradicting}"
                ),
                produced_by="rule:R8_node_affinity_mismatch",
                locators=locators,
                limitation=(
                    "Only the 'In' operator on the first required matchExpression is "
                    "evaluated -- NotIn/Exists/DoesNotExist/Gt/Lt and additional terms are not "
                    "independently checked by this rule."
                ),
            )
        ]

    return [
        make_finding(
            kind=FindingKind.OBSERVED_FACT,
            statement=(
                "The scheduler reported a node affinity/selector mismatch, but no "
                "spec.nodeSelector or spec.affinity.nodeAffinity rule was found on the Pod -- "
                "the cause could not be independently confirmed."
            ),
            produced_by="rule:R8_no_requirement_found",
            locators=[event_loc],
        )
    ]


def _first_required_node_affinity_term(pod: EvidenceItem) -> tuple[str | None, str | None, list]:
    affinity = (
        (pod.payload or {})
        .get("spec", {})
        .get("affinity", {})
        .get("nodeAffinity", {})
        .get("requiredDuringSchedulingIgnoredDuringExecution", {})
        .get("nodeSelectorTerms")
        or []
    )
    for term_idx, term in enumerate(affinity):
        for expr_idx, expr in enumerate(term.get("matchExpressions") or []):
            if expr.get("operator") == "In" and expr.get("values"):
                key_loc = field_locator(
                    pod, "spec", "affinity", "nodeAffinity",
                    "requiredDuringSchedulingIgnoredDuringExecution", "nodeSelectorTerms",
                    term_idx, "matchExpressions", expr_idx, "key",
                )
                value_loc = field_locator(
                    pod, "spec", "affinity", "nodeAffinity",
                    "requiredDuringSchedulingIgnoredDuringExecution", "nodeSelectorTerms",
                    term_idx, "matchExpressions", expr_idx, "values", 0,
                )
                return expr["key"], expr["values"][0], [key_loc, value_loc]
    return None, None, []


def _first_label_mismatch(schedulable_nodes: list[EvidenceItem], key: str, required_value: str, locators: list) -> str:
    """Appends a contradicting-evidence locator (a node whose label for the same key differs)
    when one exists, mutating ``locators`` in place -- mirrors Phase 0.6's own evidence shape
    (pod wants 'production', nodes show 'staging'). Returns descriptive text for the statement."""
    for node in schedulable_nodes:
        labels = (node.payload or {}).get("metadata", {}).get("labels") or {}
        if key in labels and labels[key] != required_value:
            locators.append(field_locator(node, "metadata", "labels", key))
            node_name = getattr(node.source_ref, "name", "?")
            return f" Node '{node_name}' has '{key}={labels[key]}' instead."
    return ""


def _schedulable(node_items: list[EvidenceItem]) -> list[EvidenceItem]:
    return [n for n in node_items if not (n.payload or {}).get("spec", {}).get("unschedulable")]


# --- R9: Pod anti-affinity conflict --------------------------------------------------------------
#
# Completed (R9 follow-up, docs/research/read-only-access-matrix.md §8): now correlates against
# actual sibling-Pod placement, collected via a Pod LIST scoped to the owning ReplicaSet's own
# selector. A DETERMINISTIC_FINDING requires real, positive evidence at every step -- ownership
# resolved, a usable selector, sibling data actually collected, and at least one sibling placed
# on a currently-known-schedulable node. Any missing link abstains with the specific reason,
# per the same discipline as every other rule in this bank; the scheduler's event message alone
# is never treated as sufficient on its own.


def _evaluate_r9(pod: EvidenceItem, event: EvidenceItem, rel) -> list[Finding]:
    event_loc = field_locator(event, "message")

    if rel.replica_set_item is None or rel.replica_set_item.status != EvidenceStatus.PRESENT:
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    "The scheduler reported a pod anti-affinity conflict, but the owning "
                    "ReplicaSet could not be read, so the selector that defines 'sibling' for "
                    "this workload is unknown -- the conflict cannot be independently "
                    "confirmed from the evidence collected."
                ),
                produced_by="rule:R9_replica_set_unavailable",
                locators=[event_loc],
            )
        ]

    rs_selector = (rel.replica_set_item.payload or {}).get("spec", {}).get("selector") or {}
    if not rs_selector.get("matchLabels") and not rs_selector.get("matchExpressions"):
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    "The scheduler reported a pod anti-affinity conflict, but the owning "
                    "ReplicaSet has no usable selector (no matchLabels or matchExpressions), "
                    "so sibling pods cannot be identified."
                ),
                produced_by="rule:R9_no_selector",
                locators=[event_loc, field_locator(rel.replica_set_item, "metadata", "name")],
            )
        ]

    # A gap item exists both when the list call genuinely failed (UNAVAILABLE/TIMEOUT) and when
    # it succeeded but found zero pods (NOT_FOUND) -- these are different facts and must not be
    # conflated: only the former is "could not collect," the latter is handled below as a real,
    # positive (if inconsistent) result.
    if rel.sibling_pods_gap is not None and rel.sibling_pods_gap.status != EvidenceStatus.NOT_FOUND:
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    "The scheduler reported a pod anti-affinity conflict, but sibling-pod data "
                    "could not be collected (the list call failed), so the conflict cannot be "
                    "independently confirmed."
                ),
                produced_by="rule:R9_siblings_unavailable",
                locators=[event_loc],
            )
        ]

    if not rel.sibling_pod_items:
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    "The scheduler reported a pod anti-affinity conflict, but no sibling pods "
                    "matching the owning ReplicaSet's selector were found -- this is itself "
                    "inconsistent with an anti-affinity conflict (which requires an existing "
                    "sibling), and the specific cause could not be confirmed from the evidence "
                    "collected."
                ),
                produced_by="rule:R9_no_siblings_found",
                locators=[event_loc],
            )
        ]

    placed_siblings = [
        s for s in rel.sibling_pod_items if (s.payload or {}).get("spec", {}).get("nodeName")
    ]
    if not placed_siblings:
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    f"The scheduler reported a pod anti-affinity conflict; "
                    f"{len(rel.sibling_pod_items)} sibling pod(s) matching the selector were "
                    "found, but none is currently placed on any node -- the conflict cannot be "
                    "independently confirmed from the evidence collected."
                ),
                produced_by="rule:R9_no_placed_siblings",
                locators=[event_loc],
            )
        ]

    schedulable_names = {
        getattr(n.source_ref, "name", None) for n in _schedulable(rel.node_items)
    }
    occupied = [s for s in placed_siblings if (s.payload or {}).get("spec", {}).get("nodeName") in schedulable_names]

    if occupied:
        locators = [event_loc] + [field_locator(s, "spec", "nodeName") for s in occupied]
        occupied_nodes = sorted({(s.payload or {}).get("spec", {}).get("nodeName") for s in occupied})
        return [
            make_finding(
                kind=FindingKind.DETERMINISTIC_FINDING,
                statement=(
                    f"{len(occupied)} of {len(schedulable_names)} known schedulable node(s) "
                    f"({occupied_nodes}) already host a sibling Pod matching the owning "
                    "ReplicaSet's selector, consistent with the scheduler's reported pod "
                    "anti-affinity conflict."
                ),
                produced_by="rule:R9_anti_affinity_confirmed",
                locators=locators,
                limitation=(
                    "Confirms that a selector-matching sibling occupies a schedulable node, "
                    "which is consistent with the reported conflict -- it does not itself "
                    "prove the pod's specific anti-affinity term is what matched, only that "
                    "the evidence is consistent with the scheduler's own report."
                ),
            )
        ]

    return [
        make_finding(
            kind=FindingKind.OBSERVED_FACT,
            statement=(
                f"{len(placed_siblings)} sibling pod(s) are placed on a node, but none of "
                "those nodes is among the known schedulable nodes (they may be cordoned, or "
                "not among the nodes read) -- the conflict cannot be confirmed against known "
                "candidate nodes from the evidence collected."
            ),
            produced_by="rule:R9_siblings_not_on_known_schedulable_node",
            locators=[event_loc] + [field_locator(s, "spec", "nodeName") for s in placed_siblings],
        )
    ]


# --- R10: PV binding occupied ---------------------------------------------------------------------


def _evaluate_r10(pod: EvidenceItem, event: EvidenceItem, rel) -> list[Finding]:
    event_loc = field_locator(event, "message")

    if not rel.persistent_volume_claims_by_name:
        return [
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    "The scheduler reported an unbound PersistentVolumeClaim, but no PVC "
                    "evidence was collected to identify or confirm which claim is affected."
                ),
                produced_by="rule:R10_pvc_unavailable",
                locators=[event_loc],
            )
        ]

    findings: list[Finding] = []
    for pvc_name, pvc_item in rel.persistent_volume_claims_by_name.items():
        pvc_status = (pvc_item.payload or {}).get("status", {}).get("phase")
        if pvc_status != "Pending":
            continue

        pvc_spec = (pvc_item.payload or {}).get("spec", {})
        requested_storage_class = pvc_spec.get("storageClassName")
        requested_access_modes = set(pvc_spec.get("accessModes") or [])

        matching_pv = None
        for pv in rel.persistent_volume_items:
            pv_spec = (pv.payload or {}).get("spec", {})
            if pv_spec.get("storageClassName") != requested_storage_class:
                continue
            if requested_access_modes and not requested_access_modes.issubset(set(pv_spec.get("accessModes") or [])):
                continue
            matching_pv = pv
            break

        if matching_pv is not None:
            bound_claim = (
                (matching_pv.payload or {}).get("spec", {}).get("claimRef", {}).get("name")
            )
            locators = [
                event_loc,
                field_locator(pvc_item, "status", "phase"),
                field_locator(matching_pv, "spec", "claimRef", "name"),
            ]
            findings.append(
                make_finding(
                    kind=FindingKind.DETERMINISTIC_FINDING,
                    statement=(
                        f"PVC '{pvc_name}' is Pending. The matching PersistentVolume "
                        f"'{getattr(matching_pv.source_ref, 'name', '?')}' is already bound to "
                        f"a different claim ('{bound_claim}')."
                    ),
                    produced_by="rule:R10_pv_bound_elsewhere",
                    locators=locators,
                )
            )
        else:
            locators = [event_loc, field_locator(pvc_item, "status", "phase")]
            findings.append(
                make_finding(
                    kind=FindingKind.DETERMINISTIC_FINDING,
                    statement=(
                        f"PVC '{pvc_name}' is Pending, and no PersistentVolume in the cluster "
                        "matches its requirements (storage class / access modes) at all."
                    ),
                    produced_by="rule:R10_no_matching_pv",
                    locators=locators,
                )
            )

    if not findings:
        findings.append(
            make_finding(
                kind=FindingKind.OBSERVED_FACT,
                statement=(
                    "The scheduler reported an unbound PersistentVolumeClaim, but none of the "
                    "Pod's collected PVC(s) show status Pending -- the cause could not be "
                    "confirmed from the evidence collected."
                ),
                produced_by="rule:R10_no_pending_pvc_found",
                locators=[event_loc],
            )
        )
    return findings
