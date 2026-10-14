"""Milestone 2 collector/bundle-builder behavior: gating, error translation, list-splitting
for Node/PersistentVolume, and PVC-by-name collection."""

from __future__ import annotations

from k8s_investigator.collector.client import ApiForbiddenError
from k8s_investigator.models.evidence import EvidenceStatus
from k8s_investigator.models.refs import ApiCallRef, ObjectRef
from k8s_investigator.pod_status import is_scheduling_blocked

from .conftest import collect_and_build
from .fixtures import scenarios
from .fixtures.builders import make_pod


def test_is_scheduling_blocked_true_for_pending_unscheduled():
    pod = make_pod(unscheduled=True)
    assert is_scheduling_blocked(pod) is True


def test_is_scheduling_blocked_false_for_running_pod():
    pod = make_pod(running=True)
    assert is_scheduling_blocked(pod) is False


def test_is_scheduling_blocked_false_for_pending_without_condition():
    """Pending alone isn't enough -- e.g. a container still ContainerCreating after being
    scheduled is Pending too, but PodScheduled=True by then."""
    pod = make_pod(waiting_reason="ContainerCreating")  # Pending, no PodScheduled condition at all
    assert is_scheduling_blocked(pod) is False


def test_node_list_splits_into_one_item_per_node():
    bundle = collect_and_build(scenarios.r5_all_nodes_cordoned())
    node_items = [i for i in bundle.items if isinstance(i.source_ref, ObjectRef) and i.source_ref.kind == "Node"]
    assert len(node_items) == 4
    assert all(i.status == EvidenceStatus.PRESENT for i in node_items)
    assert all(i.source_ref.namespace is None for i in node_items)  # cluster-scoped


def test_persistent_volume_list_splits_and_is_cluster_scoped():
    bundle = collect_and_build(scenarios.r10_pv_bound_elsewhere())
    pv_items = [i for i in bundle.items if isinstance(i.source_ref, ObjectRef) and i.source_ref.kind == "PersistentVolume"]
    assert len(pv_items) == 1
    assert pv_items[0].source_ref.namespace is None


def test_pvc_gap_recorded_when_unreadable():
    client = scenarios.r10_pv_bound_elsewhere()
    client.persistent_volume_claim_errors["demo-pvc"] = ApiForbiddenError("pvc forbidden")
    bundle = collect_and_build(client)
    pvc_items = [i for i in bundle.items if isinstance(i.source_ref, ObjectRef) and i.source_ref.kind == "PersistentVolumeClaim"]
    assert len(pvc_items) == 1
    assert pvc_items[0].status == EvidenceStatus.UNAVAILABLE


def test_node_list_failure_recorded_as_unavailable_not_silence():
    bundle = collect_and_build(scenarios.scheduling_blocked_nodes_unavailable())
    node_gaps = [
        i for i in bundle.items
        if isinstance(i.source_ref, ApiCallRef) and i.source_ref.path.rstrip("/").endswith("/nodes")
    ]
    assert len(node_gaps) == 1
    assert node_gaps[0].status == EvidenceStatus.UNAVAILABLE
    assert "Forbidden" in node_gaps[0].error_detail


def test_pv_list_not_attempted_when_pod_has_no_pvc():
    """A scheduling-blocked pod with no PVC-backed volume at all should never trigger the
    PersistentVolume list call -- there is nothing for R10 to check against."""
    client = scenarios.r5_all_nodes_cordoned()  # no PVC on this pod
    collect_and_build(client)
    assert not any(c[0] == "list_persistent_volumes" for c in client.calls)
    assert not any(c[0] == "get_persistent_volume_claim" for c in client.calls)


def test_empty_node_list_does_not_apply_expiry_heuristic():
    """Unlike an empty Events list, an empty Node list has no TTL-eviction concept -- it must
    never be flagged EXPIRED."""
    client = scenarios.r5_all_nodes_cordoned()
    client.nodes = []  # force an empty (not failed) node list
    bundle = collect_and_build(client)
    node_gaps = [
        i for i in bundle.items
        if isinstance(i.source_ref, ApiCallRef) and i.source_ref.path.rstrip("/").endswith("/nodes")
    ]
    assert len(node_gaps) == 1
    assert node_gaps[0].status == EvidenceStatus.NOT_FOUND
    assert node_gaps[0].expiry_note is None
