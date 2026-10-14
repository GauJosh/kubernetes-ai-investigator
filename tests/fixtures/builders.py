"""Reusable, realistic raw-JSON Kubernetes object builders for tests.

These intentionally produce structured JSON (camelCase, nested) matching what the real
collector's ``_preload_content=False`` calls return -- not ``kubectl describe`` text -- so these
fixtures actually exercise the same shape the real collector would produce.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _recent_timestamp(minutes_ago: int = 5) -> str:
    """A timestamp anchored to real test-run time, never hardcoded -- so "not expired" fixtures
    stay correct no matter when the test suite actually runs."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_pod(
    *,
    name: str = "demo-pod",
    namespace: str = "demo-ns",
    container_name: str = "server",
    waiting_reason: str | None = None,
    running: bool = False,
    crash_loop: bool = False,
    last_terminated_reason: str | None = None,
    restart_count: int = 0,
    service_account_name: str = "default",
    pod_image_pull_secrets: list[str] | None = None,
    owner_rs_name: str | None = "demo-rs",
    uid: str = "pod-uid-1",
    creation_timestamp: str | None = None,
    # Milestone 2 (Scheduling-fault, R5-R10) additions -- all unused unless unscheduled=True.
    unscheduled: bool = False,
    node_selector: dict[str, str] | None = None,
    node_affinity_required: tuple[str, str] | None = None,  # (key, value) -- the 'In' operator shape R8 evaluates
    tolerations: list[dict[str, Any]] | None = None,
    container_resource_requests: dict[str, str] | None = None,
    pvc_claim_names: list[str] | None = None,
) -> dict[str, Any]:
    if creation_timestamp is None:
        creation_timestamp = _recent_timestamp()

    owner_refs = []
    if owner_rs_name:
        owner_refs.append({"kind": "ReplicaSet", "name": owner_rs_name, "controller": True, "uid": "rs-uid-1"})

    default_tolerations = [
        {"key": "node.kubernetes.io/not-ready", "operator": "Exists", "effect": "NoExecute", "tolerationSeconds": 300},
        {"key": "node.kubernetes.io/unreachable", "operator": "Exists", "effect": "NoExecute", "tolerationSeconds": 300},
    ]

    volumes = []
    for claim_name in (pvc_claim_names or []):
        volumes.append({"name": f"{claim_name}-vol", "persistentVolumeClaim": {"claimName": claim_name}})

    spec: dict[str, Any] = {
        "serviceAccountName": service_account_name,
        "containers": [
            {
                "name": container_name,
                "image": "example.registry/demo:v1",
                "resources": {"requests": container_resource_requests} if container_resource_requests else {},
            }
        ],
        "imagePullSecrets": [{"name": s} for s in (pod_image_pull_secrets or [])],
        "tolerations": tolerations if tolerations is not None else default_tolerations,
    }
    if node_selector:
        spec["nodeSelector"] = node_selector
    if node_affinity_required:
        key, value = node_affinity_required
        spec["affinity"] = {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {"matchExpressions": [{"key": key, "operator": "In", "values": [value]}]}
                    ]
                }
            }
        }
    if volumes:
        spec["volumes"] = volumes

    metadata = {
        "name": name,
        "namespace": namespace,
        "uid": uid,
        "creationTimestamp": creation_timestamp,
        "ownerReferences": owner_refs,
        "resourceVersion": "1000",
    }

    if unscheduled:
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": metadata,
            "spec": spec,
            "status": {
                "phase": "Pending",
                "conditions": [
                    {"type": "PodScheduled", "status": "False", "reason": "Unschedulable", "message": "0/4 nodes are available"}
                ],
                "containerStatuses": [],  # not yet scheduled -- no container has started
            },
        }

    container_status: dict[str, Any] = {
        "name": container_name,
        "image": "example.registry/demo:v1",
        "restartCount": restart_count,
        "ready": False,
    }
    if crash_loop:
        container_status["state"] = {"waiting": {"reason": "CrashLoopBackOff", "message": "back-off restarting failed container"}}
        if last_terminated_reason:
            container_status["lastState"] = {
                "terminated": {"reason": last_terminated_reason, "exitCode": 1, "finishedAt": "2026-10-13T10:05:00Z"}
            }
    elif waiting_reason:
        container_status["state"] = {
            "waiting": {"reason": waiting_reason, "message": f"{waiting_reason} message"}
        }
    elif running:
        container_status["state"] = {"running": {"startedAt": "2026-10-13T10:05:00Z"}}
        container_status["ready"] = True

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": metadata,
        "spec": spec,
        "status": {
            "phase": "Running" if running else "Pending",
            "containerStatuses": [container_status],
        },
    }


def make_event(
    message: str,
    *,
    reason: str = "Failed",
    event_type: str = "Warning",
    pod_name: str = "demo-pod",
    namespace: str = "demo-ns",
    name: str = "demo-pod.ev1",
    first_timestamp: str = "2026-10-13T10:01:00Z",
    last_timestamp: str = "2026-10-13T10:01:30Z",
    count: int = 1,
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Event",
        "metadata": {"name": name, "namespace": namespace, "resourceVersion": "500"},
        "involvedObject": {"kind": "Pod", "name": pod_name, "namespace": namespace},
        "reason": reason,
        "message": message,
        "type": event_type,
        "firstTimestamp": first_timestamp,
        "lastTimestamp": last_timestamp,
        "count": count,
    }


def make_replica_set(
    *, name: str = "demo-rs", namespace: str = "demo-ns", owner_deploy_name: str | None = "demo-deploy"
) -> dict[str, Any]:
    owner_refs = []
    if owner_deploy_name:
        owner_refs.append({"kind": "Deployment", "name": owner_deploy_name, "controller": True, "uid": "d-uid-1"})
    return {
        "apiVersion": "apps/v1",
        "kind": "ReplicaSet",
        "metadata": {"name": name, "namespace": namespace, "ownerReferences": owner_refs},
    }


def make_deployment(*, name: str = "demo-deploy", namespace: str = "demo-ns") -> dict[str, Any]:
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "namespace": namespace}}


def make_service_account(
    *, name: str = "default", namespace: str = "demo-ns", image_pull_secrets: list[str] | None = None
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": name, "namespace": namespace},
        "imagePullSecrets": [{"name": s} for s in (image_pull_secrets or [])],
    }


def make_node(
    *,
    name: str = "worker-01",
    cordoned: bool = False,
    taints: list[dict[str, Any]] | None = None,
    labels: dict[str, str] | None = None,
    allocatable_cpu: str = "4",
    allocatable_memory: str = "8Gi",
) -> dict[str, Any]:
    effective_taints = list(taints or [])
    spec: dict[str, Any] = {"taints": effective_taints}
    if cordoned:
        effective_taints.append({"key": "node.kubernetes.io/unschedulable", "effect": "NoSchedule"})
        spec["unschedulable"] = True  # real K8s omits this key entirely when false, never sets it explicitly
    return {
        "apiVersion": "v1",
        "kind": "Node",
        "metadata": {"name": name, "labels": labels or {}, "resourceVersion": "2000"},
        "spec": spec,
        "status": {"allocatable": {"cpu": allocatable_cpu, "memory": allocatable_memory}},
    }


def make_pvc(
    *,
    name: str = "demo-pvc",
    namespace: str = "demo-ns",
    phase: str = "Pending",
    storage_class_name: str | None = "standard",
    access_modes: list[str] | None = None,
    requested_storage: str = "1Gi",
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": name, "namespace": namespace, "resourceVersion": "3000"},
        "spec": {
            "storageClassName": storage_class_name,
            "accessModes": access_modes or ["ReadWriteOnce"],
            "resources": {"requests": {"storage": requested_storage}},
        },
        "status": {"phase": phase},
    }


def make_pv(
    *,
    name: str = "demo-pv",
    storage_class_name: str | None = "standard",
    access_modes: list[str] | None = None,
    capacity: str = "1Gi",
    bound_claim_name: str | None = None,
    bound_claim_namespace: str = "demo-ns",
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "storageClassName": storage_class_name,
        "accessModes": access_modes or ["ReadWriteOnce"],
        "capacity": {"storage": capacity},
    }
    if bound_claim_name:
        spec["claimRef"] = {"name": bound_claim_name, "namespace": bound_claim_namespace}
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolume",
        "metadata": {"name": name, "resourceVersion": "4000"},
        "spec": spec,
        "status": {"phase": "Bound" if bound_claim_name else "Available"},
    }
