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
) -> dict[str, Any]:
    if creation_timestamp is None:
        creation_timestamp = _recent_timestamp()
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

    owner_refs = []
    if owner_rs_name:
        owner_refs.append({"kind": "ReplicaSet", "name": owner_rs_name, "controller": True, "uid": "rs-uid-1"})

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "uid": uid,
            "creationTimestamp": creation_timestamp,
            "ownerReferences": owner_refs,
            "resourceVersion": "1000",
        },
        "spec": {
            "serviceAccountName": service_account_name,
            "containers": [{"name": container_name, "image": "example.registry/demo:v1"}],
            "imagePullSecrets": [{"name": s} for s in (pod_image_pull_secrets or [])],
        },
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
