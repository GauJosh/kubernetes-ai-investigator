"""The application API-call-surface test -- correction 4 of revision 2.

Named precisely for what it proves: that *this project's own code* cannot issue a call outside
the approved six-method surface. It proves nothing about what RBAC grant is actually bound to a
deployed ServiceAccount -- that is a separate, later validation step (RBAC policy review, then
live authorization testing), named but not performed here, per
docs/phase-0.8-mvp-blueprint.md §H revision 2.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import k8s_investigator.collector.collect as collect_module
import k8s_investigator.collector.k8s_client as k8s_client_module
from k8s_investigator.collector.client import KubernetesClient

from .conftest import collect_and_build
from .fixtures import scenarios

APPROVED_METHODS = {
    "get_pod",
    "list_events_for_pod",
    "get_pod_log",
    "get_replica_set",
    "get_deployment",
    "get_service_account",
}

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
    ):
        client = build_client()
        collect_and_build(client)
        called_methods = {call[0] for call in client.calls}
        assert called_methods <= APPROVED_METHODS, f"unapproved call(s): {called_methods - APPROVED_METHODS}"
        assert len(client.calls) > 0  # sanity -- the scenario actually exercised the collector


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
