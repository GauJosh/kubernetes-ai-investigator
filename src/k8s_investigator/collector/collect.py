"""Collection orchestration: the approved calls, bounded, partial-failure-safe.

docs/research/read-only-access-matrix.md §3's collection order is followed directly: the Pod
first (everything else is derived from it), then events (always attempted -- useful even when
the Pod itself could not be read), then logs/ReplicaSet/ServiceAccount (derived from the Pod's
own fields), then Deployment (derived from the ReplicaSet). A call that was never attempted
because its prerequisite was missing is not recorded as a failure -- there is nothing dishonest
about not having tried something that had no input to try with.

Milestone 2 additions (Node, PersistentVolumeClaim, PersistentVolume -- see §7 of the matrix
document) are gated, not unconditional: they are only attempted when the Pod's own status
already shows a scheduling-blocked signal (``phase == "Pending"`` and a ``PodScheduled: False``
condition). A healthy or already-running pod's investigation never issues the two cluster-scoped
list calls at all.
"""

from __future__ import annotations

from ..models.evidence import CollectionOutcome, CollectionStatus
from ..models.refs import ApiCallRef, ObjectRef
from ..pod_status import is_scheduling_blocked
from .client import (
    DEFAULT_LOG_SINCE_SECONDS,
    DEFAULT_LOG_TAIL_LINES,
    KubernetesClient,
    run_call,
)


def collect_pod_evidence(
    k8s: KubernetesClient,
    namespace: str,
    pod_name: str,
    *,
    log_tail_lines: int = DEFAULT_LOG_TAIL_LINES,
    log_since_seconds: int = DEFAULT_LOG_SINCE_SECONDS,
) -> list[CollectionOutcome]:
    outcomes: list[CollectionOutcome] = []

    pod_ref = ObjectRef(api_group="", api_version="v1", kind="Pod", namespace=namespace, name=pod_name)
    pod_outcome = run_call(pod_ref, lambda: k8s.get_pod(namespace, pod_name))
    outcomes.append(pod_outcome)
    pod_payload = pod_outcome.payload if pod_outcome.status == CollectionStatus.PRESENT else None

    # Events -- always attempted, scoped to this pod by name+namespace (+uid when known).
    pod_uid = (pod_payload or {}).get("metadata", {}).get("uid")
    events_field_selector = f"involvedObject.name={pod_name}"
    events_ref = ApiCallRef(
        verb="list",
        path=f"/api/v1/namespaces/{namespace}/events",
        params={"fieldSelector": events_field_selector, "involvedObjectUid": pod_uid},
    )
    outcomes.append(run_call(events_ref, lambda: k8s.list_events_for_pod(namespace, pod_name, pod_uid)))

    if pod_payload is None:
        # Nothing else can be derived without the Pod's own fields -- stop here rather than
        # issuing calls with no real input (e.g. guessing a ServiceAccount name).
        return outcomes

    # Logs -- one bounded call per container the Pod reports. previous=True only when the
    # container has actually restarted, per the approved bound.
    for cs in (pod_payload.get("status", {}).get("containerStatuses") or []):
        container_name = cs.get("name")
        if not container_name:
            continue
        previous = bool(cs.get("restartCount"))
        log_ref = ApiCallRef(
            verb="get",
            path=f"/api/v1/namespaces/{namespace}/pods/{pod_name}/log",
            params={
                "container": container_name,
                "tailLines": log_tail_lines,
                "sinceSeconds": log_since_seconds,
                "previous": previous,
            },
        )
        outcomes.append(
            run_call(
                log_ref,
                lambda cn=container_name, p=previous: k8s.get_pod_log(
                    namespace,
                    pod_name,
                    cn,
                    tail_lines=log_tail_lines,
                    since_seconds=log_since_seconds,
                    previous=p,
                ),
            )
        )

    # ReplicaSet -- from the Pod's own ownerReferences, if present.
    rs_name = next(
        (r.get("name") for r in (pod_payload.get("metadata", {}).get("ownerReferences") or [])
         if r.get("kind") == "ReplicaSet"),
        None,
    )
    rs_payload = None
    if rs_name:
        rs_ref = ObjectRef(api_group="apps", api_version="v1", kind="ReplicaSet", namespace=namespace, name=rs_name)
        rs_outcome = run_call(rs_ref, lambda: k8s.get_replica_set(namespace, rs_name))
        outcomes.append(rs_outcome)
        rs_payload = rs_outcome.payload if rs_outcome.status == CollectionStatus.PRESENT else None

    # Deployment -- from the ReplicaSet's own ownerReferences, if present.
    if rs_payload is not None:
        deploy_name = next(
            (r.get("name") for r in (rs_payload.get("metadata", {}).get("ownerReferences") or [])
             if r.get("kind") == "Deployment"),
            None,
        )
        if deploy_name:
            deploy_ref = ObjectRef(
                api_group="apps", api_version="v1", kind="Deployment", namespace=namespace, name=deploy_name
            )
            outcomes.append(run_call(deploy_ref, lambda: k8s.get_deployment(namespace, deploy_name)))

    # ServiceAccount -- from the Pod's own spec.serviceAccountName, defaulting to "default"
    # exactly as the Kubernetes admission controller would.
    sa_name = pod_payload.get("spec", {}).get("serviceAccountName") or "default"
    sa_ref = ObjectRef(api_group="", api_version="v1", kind="ServiceAccount", namespace=namespace, name=sa_name)
    outcomes.append(run_call(sa_ref, lambda: k8s.get_service_account(namespace, sa_name)))

    # --- Milestone 2: Node / PersistentVolumeClaim / PersistentVolume, gated -------------------
    if is_scheduling_blocked(pod_payload):
        # PersistentVolumeClaim(s) -- by name, from the Pod's own volumes, one GET per claim.
        pvc_names = [
            v["persistentVolumeClaim"]["claimName"]
            for v in (pod_payload.get("spec", {}).get("volumes") or [])
            if v.get("persistentVolumeClaim", {}).get("claimName")
        ]
        for pvc_name in pvc_names:
            pvc_ref = ObjectRef(
                api_group="", api_version="v1", kind="PersistentVolumeClaim",
                namespace=namespace, name=pvc_name,
            )
            outcomes.append(
                run_call(pvc_ref, lambda n=pvc_name: k8s.get_persistent_volume_claim(namespace, n))
            )

        # Node -- cluster-scoped list, only `list` is ever called (see matrix §7.1).
        nodes_ref = ApiCallRef(verb="list", path="/api/v1/nodes", params={})
        outcomes.append(run_call(nodes_ref, lambda: k8s.list_nodes()))

        # PersistentVolume -- cluster-scoped list, only when the pod actually references a PVC
        # (no PVC means R10 cannot apply, so there is nothing to check PVs against).
        if pvc_names:
            pv_ref = ApiCallRef(verb="list", path="/api/v1/persistentvolumes", params={})
            outcomes.append(run_call(pv_ref, lambda: k8s.list_persistent_volumes()))

    return outcomes
