"""The application API-call-surface test -- correction 4 of revision 2.

Named precisely for what it proves: that *this project's own code* cannot issue a call outside
the approved surface. It proves nothing about what RBAC grant is actually bound to a deployed
ServiceAccount -- that is a separate, later validation step (RBAC policy review, then live
authorization testing), named but not performed here, per docs/phase-0.8-mvp-blueprint.md §H
revision 2.

Milestone 2 extends the approved surface by exactly three methods (documented, with their
required RBAC, in docs/research/read-only-access-matrix.md §7, written before this collector
code was enabled); the R9 follow-up adds one more (§8) -- each tier is kept as its own set below
so a future reviewer can see exactly what each phase added.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import k8s_investigator.collector.collect as collect_module
import k8s_investigator.collector.k8s_client as k8s_client_module
from k8s_investigator.collector.client import KubernetesClient

from .conftest import collect_and_build
from .fixtures import scenarios

MILESTONE_1_METHODS = {
    "get_pod",
    "list_events_for_pod",
    "get_pod_log",
    "get_replica_set",
    "get_deployment",
    "get_service_account",
}

MILESTONE_2_METHODS = {
    "get_persistent_volume_claim",
    "list_nodes",
    "list_persistent_volumes",
}

R9_FOLLOWUP_METHODS = {
    "list_pods_by_label_selector",
}

APPROVED_METHODS = MILESTONE_1_METHODS | MILESTONE_2_METHODS | R9_FOLLOWUP_METHODS

# Any of these appearing in the collector's source would mean a write, exec, attach,
# port-forward, or eviction call exists -- none should, anywhere in this codebase.
FORBIDDEN_SOURCE_SUBSTRINGS = (
    "create_namespaced",
    "patch_namespaced",
    "replace_namespaced",
    "delete_namespaced",
    "delete_collection_namespaced",
    "_exec",
    "connect_post_namespaced_pod_exec",
    "connect_get_namespaced_pod_attach",
    "connect_post_namespaced_pod_portforward",
    "create_namespaced_pod_eviction",
    ".create(",
    ".patch(",
    ".delete(",
    ".replace(",
)


def test_protocol_defines_exactly_the_approved_methods():
    """Structural proof, not convention: the Protocol itself has no write-shaped method to call."""
    protocol_methods = {
        name
        for name, value in inspect.getmembers(KubernetesClient)
        if not name.startswith("_") and callable(value)
    }
    assert protocol_methods == APPROVED_METHODS


def test_collector_only_calls_approved_methods_end_to_end():
    for build_client in (
        scenarios.healthy_pod,
        scenarios.r1_dns_failure,
        scenarios.r3_service_account_unavailable,
        scenarios.r4_log_pattern_match,
        scenarios.events_plausibly_expired,
        scenarios.r5_all_nodes_cordoned,
        scenarios.r10_pv_bound_elsewhere,
        scenarios.scheduling_blocked_nodes_unavailable,
        scenarios.r9_anti_affinity_confirmed,
        scenarios.r9_no_selector_on_replica_set,
        scenarios.r9_siblings_unavailable,
    ):
        client = build_client()
        collect_and_build(client)
        called_methods = {call[0] for call in client.calls}
        assert called_methods <= APPROVED_METHODS, f"unapproved call(s): {called_methods - APPROVED_METHODS}"
        assert len(client.calls) > 0  # sanity -- the scenario actually exercised the collector


def test_milestone_2_calls_are_gated_not_unconditional():
    """docs/research/read-only-access-matrix.md §7.2: Node/PersistentVolume(Claim) must only
    be called when the Pod is actually scheduling-blocked -- never for a healthy or
    already-running pod's investigation."""
    for build_client in (scenarios.healthy_pod, scenarios.r1_dns_failure, scenarios.r4_log_pattern_match):
        client = build_client()
        collect_and_build(client)
        called = {c[0] for c in client.calls}
        assert called.isdisjoint(MILESTONE_2_METHODS), (
            f"{build_client.__name__}: unexpected Milestone 2 call(s) {called & MILESTONE_2_METHODS} "
            "for a non-scheduling-blocked pod"
        )

    # and the inverse: a scheduling-blocked pod *does* reach the gated calls
    client = scenarios.r5_all_nodes_cordoned()
    collect_and_build(client)
    called = {c[0] for c in client.calls}
    assert "list_nodes" in called


def test_r9_followup_call_is_gated_on_usable_selector_too():
    """docs/research/read-only-access-matrix.md §8.5: the sibling-pod call requires BOTH
    scheduling-blocked AND a ReplicaSet with a usable selector -- not scheduling-blocked alone."""
    for build_client in (scenarios.healthy_pod, scenarios.r1_dns_failure):
        client = build_client()
        collect_and_build(client)
        assert "list_pods_by_label_selector" not in {c[0] for c in client.calls}

    # scheduling-blocked, but no usable selector on the ReplicaSet -- still must not call it
    client = scenarios.r9_no_selector_on_replica_set()
    collect_and_build(client)
    assert "list_pods_by_label_selector" not in {c[0] for c in client.calls}

    # scheduling-blocked AND a usable selector -- now it must be called
    client = scenarios.r9_anti_affinity_confirmed()
    collect_and_build(client)
    assert "list_pods_by_label_selector" in {c[0] for c in client.calls}


def test_r9_followup_selector_is_a_query_filter_not_passed_as_rbac():
    """docs/research/read-only-access-matrix.md §8.2: confirms the call actually receives a
    labelSelector built from the ReplicaSet's own matchLabels -- a query argument, not any kind
    of access-control parameter."""
    client = scenarios.r9_anti_affinity_confirmed()
    collect_and_build(client)
    call = next(c for c in client.calls if c[0] == "list_pods_by_label_selector")
    _, args, _ = call
    assert args[1] == "app=demo-pod"


def test_no_write_or_interactive_call_in_collector_source():
    for module in (collect_module, k8s_client_module):
        source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_SOURCE_SUBSTRINGS:
            assert forbidden not in source, (
                f"forbidden call pattern {forbidden!r} found in {module.__name__} -- "
                "the collector must remain strictly read-only"
            )


def test_no_write_or_interactive_method_name_in_collector_package():
    """Belt-and-suspenders: scan every .py file under collector/ for the same patterns, not
    just the two modules checked above."""
    collector_dir = Path(inspect.getfile(collect_module)).parent
    for path in collector_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_SOURCE_SUBSTRINGS:
            assert forbidden not in source, f"forbidden pattern {forbidden!r} found in {path.name}"
