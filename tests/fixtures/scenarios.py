"""Fully-configured FakeKubernetesClient scenarios, one per tested case.

Every scenario is named for the real Phase 0.6 case or rule outcome it corresponds to --
see docs/research/cloud-opsbench-evidence-feasibility.md for the verified source text each
event/log message is drawn from.
"""

from __future__ import annotations

from tests.fakes import ApiForbiddenError, FakeKubernetesClient

from .builders import (
    make_deployment,
    make_event,
    make_node,
    make_pod,
    make_pv,
    make_pvc,
    make_replica_set,
    make_service_account,
    make_sibling_pod,
)


def _with_owner_chain(
    client: FakeKubernetesClient, *, sa_image_pull_secrets=None, rs_match_labels=None, rs_unavailable=False
) -> FakeKubernetesClient:
    if rs_unavailable:
        client.replica_set_errors["demo-rs"] = ApiForbiddenError("ReplicaSet is forbidden for this user")
    else:
        client.replica_sets["demo-rs"] = make_replica_set(match_labels=rs_match_labels)
    client.deployments["demo-deploy"] = make_deployment()
    client.service_accounts["default"] = make_service_account(image_pull_secrets=sa_image_pull_secrets)
    return client


def healthy_pod() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.pod = make_pod(running=True)
    c.events = [
        make_event("Successfully assigned demo-ns/demo-pod to worker-01", reason="Scheduled", event_type="Normal"),
        make_event("Started container server", reason="Started", event_type="Normal"),
    ]
    c.logs["server"] = "server started\nlistening on :8080\n"
    return _with_owner_chain(c)


def r1_dns_failure() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.pod = make_pod(waiting_reason="ErrImagePull")
    c.events = [
        make_event(
            "failed to pull and unpack image \"ghost-registry.example.net/demo/adservice:v0.10.3\": "
            "failed to resolve reference: failed to do request: Head "
            "\"https://ghost-registry.example.net/v2/demo/adservice/manifests/v0.10.3\": dial tcp: "
            "lookup ghost-registry.example.net on 127.0.0.53:53: no such host"
        )
    ]
    return _with_owner_chain(c)


def r2_tag_not_found() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.pod = make_pod(waiting_reason="ErrImagePull")
    c.events = [
        make_event(
            "rpc error: code = NotFound desc = failed to pull and unpack image "
            "\"example.registry/demo:v0.10.3x\": failed to resolve reference "
            "\"example.registry/demo:v0.10.3x\": example.registry/demo:v0.10.3x: not found"
        )
    ]
    return _with_owner_chain(c)


def r3_no_secret_anywhere() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.pod = make_pod(waiting_reason="ImagePullBackOff")
    c.events = [
        make_event(
            "failed to pull and unpack image \"ghcr.io/example/demo:v1\": failed to resolve "
            "reference: failed to authorize: failed to fetch anonymous token: unexpected status "
            "from GET request: 403 Forbidden"
        )
    ]
    return _with_owner_chain(c, sa_image_pull_secrets=[])


def r3_secret_configured_on_service_account() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.pod = make_pod(waiting_reason="ImagePullBackOff")
    c.events = [make_event("failed to authorize: failed to fetch anonymous token: 403 Forbidden")]
    return _with_owner_chain(c, sa_image_pull_secrets=["sa-registry-secret"])


def r3_secret_configured_on_pod_only() -> FakeKubernetesClient:
    """The case correction 1 exists for: the Pod references a pull secret the ServiceAccount
    does not. A rule reading only the ServiceAccount would wrongly report 'no secret
    configured'."""
    c = FakeKubernetesClient()
    c.pod = make_pod(waiting_reason="ImagePullBackOff", pod_image_pull_secrets=["pod-level-secret"])
    c.events = [make_event("failed to authorize: failed to fetch anonymous token: 403 Forbidden")]
    return _with_owner_chain(c, sa_image_pull_secrets=[])


def r3_service_account_unavailable() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.pod = make_pod(waiting_reason="ImagePullBackOff")
    c.events = [make_event("failed to authorize: failed to fetch anonymous token: 403 Forbidden")]
    c.replica_sets["demo-rs"] = make_replica_set()
    c.deployments["demo-deploy"] = make_deployment()
    c.service_account_errors["default"] = ApiForbiddenError("ServiceAccount is forbidden for this user")
    return c


def r4_log_pattern_match() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.pod = make_pod(crash_loop=True, last_terminated_reason="Error", restart_count=4)
    c.events = []
    c.logs["server"] = (
        "1:M 10 Jan 2026 09:11:41.089 * Server initialized\n"
        "1:M 10 Jan 2026 09:11:41.089 # Fatal error: can't open the RDB file dump.rdb for "
        "reading: Permission denied\n"
        "1:M 10 Jan 2026 09:11:41.089 # Fatal error loading the DB, check server logs. Exiting.\n"
    )
    return _with_owner_chain(c)


def r4_insufficient_evidence() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.pod = make_pod(crash_loop=True, last_terminated_reason="Error", restart_count=2)
    c.events = []
    c.logs["server"] = "starting up\nconnecting to dependency\nexiting\n"
    return _with_owner_chain(c)


def r4_logs_unavailable() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.pod = make_pod(crash_loop=True, last_terminated_reason="Error", restart_count=1)
    c.events = []
    c.log_errors["server"] = ApiForbiddenError("pods/log is forbidden for this user")
    return _with_owner_chain(c)


def events_plausibly_expired() -> FakeKubernetesClient:
    """Pod created well over an hour ago (fixed, unambiguously-past timestamp); zero events
    returned -- must be flagged EXPIRED, not plain NOT_FOUND."""
    c = FakeKubernetesClient()
    c.pod = make_pod(waiting_reason="ErrImagePull", creation_timestamp="2020-01-01T00:00:00Z")
    c.events = []
    return _with_owner_chain(c)


def events_recent_absence() -> FakeKubernetesClient:
    """Pod created moments ago; zero events returned -- plain NOT_FOUND, not EXPIRED."""
    c = FakeKubernetesClient()
    c.pod = make_pod(waiting_reason="ErrImagePull")  # default creation_timestamp is "just now"
    c.events = []
    return _with_owner_chain(c)


def contradiction_running_vs_failure_event() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.pod = make_pod(running=True)
    c.events = [make_event("dial tcp: lookup ghost-registry.example.net: no such host", reason="Failed")]
    return _with_owner_chain(c)


def service_account_unavailable_but_r1_still_resolves() -> FakeKubernetesClient:
    """Partial-failure case: ServiceAccount is unreadable, but R1 (DNS failure) doesn't need it
    at all -- the engine should still reach a confident deterministic finding for R1 while the
    ServiceAccount gap is recorded separately in evidence_gaps."""
    c = FakeKubernetesClient()
    c.pod = make_pod(waiting_reason="ErrImagePull")
    c.events = [make_event("dial tcp: lookup ghost-registry.example.net: no such host")]
    c.replica_sets["demo-rs"] = make_replica_set()
    c.deployments["demo-deploy"] = make_deployment()
    c.service_account_errors["default"] = ApiForbiddenError("ServiceAccount is forbidden")
    return c


# ============================================================================================
# Milestone 2: Scheduling-fault scenarios (R5-R10)
# ============================================================================================


def _unscheduled(
    c: FakeKubernetesClient,
    *,
    nodes=None,
    pvcs=None,
    pvs=None,
    rs_match_labels=None,
    rs_unavailable=False,
    sibling_pods=None,
    sibling_pods_error=None,
    **pod_kwargs,
) -> FakeKubernetesClient:
    c.pod = make_pod(unscheduled=True, **pod_kwargs)
    c.nodes = nodes or []
    for pvc in (pvcs or []):
        c.persistent_volume_claims[pvc["metadata"]["name"]] = pvc
    c.persistent_volumes = pvs or []
    if sibling_pods is not None:
        c.sibling_pods = sibling_pods
    if sibling_pods_error is not None:
        c.sibling_pods_error = sibling_pods_error
    return _with_owner_chain(c, rs_match_labels=rs_match_labels, rs_unavailable=rs_unavailable)


def r5_all_nodes_cordoned() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.events = [make_event("0/4 nodes are available: 4 node(s) were unschedulable.", reason="FailedScheduling")]
    return _unscheduled(
        c,
        nodes=[make_node(name=f"node-{i}", cordoned=True) for i in range(4)],
    )


def r5_partial_cordon_underdetermined() -> FakeKubernetesClient:
    """Only the always-cordoned control-plane node is cordoned; the event's generic
    'unschedulable' clause does not, by itself, explain the failure -- must downgrade to an
    OBSERVED_FACT, not a confident DETERMINISTIC_FINDING."""
    c = FakeKubernetesClient()
    c.events = [make_event("0/4 nodes are available: 1 node(s) were unschedulable.", reason="FailedScheduling")]
    return _unscheduled(
        c,
        nodes=[make_node(name="master", cordoned=True)] + [make_node(name=f"worker-{i}") for i in range(3)],
    )


def r6_insufficient_cpu() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.events = [
        make_event(
            "0/4 nodes are available: 1 node(s) were unschedulable, 3 Insufficient cpu.",
            reason="FailedScheduling",
        )
    ]
    return _unscheduled(
        c,
        container_resource_requests={"cpu": "16"},
        nodes=[make_node(name="master", cordoned=True)]
        + [make_node(name=f"worker-{i}", allocatable_cpu="4") for i in range(3)],
    )


def r6_insufficient_memory() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.events = [
        make_event(
            "0/4 nodes are available: 1 node(s) were unschedulable, 3 Insufficient memory.",
            reason="FailedScheduling",
        )
    ]
    return _unscheduled(
        c,
        container_resource_requests={"memory": "20Gi"},
        nodes=[make_node(name="master", cordoned=True)]
        + [make_node(name=f"worker-{i}", allocatable_memory="8Gi") for i in range(3)],
    )


def r6_capacity_contradiction() -> FakeKubernetesClient:
    """The event claims insufficient cpu, but a known node's allocatable cpu is actually
    sufficient for the pod's request -- a genuine data disagreement, not resolved silently."""
    c = FakeKubernetesClient()
    c.events = [make_event("0/4 nodes are available: 3 Insufficient cpu.", reason="FailedScheduling")]
    return _unscheduled(
        c,
        container_resource_requests={"cpu": "2"},
        nodes=[make_node(name="worker-1", allocatable_cpu="8")],  # 8 cores >= 2 requested -- contradicts "insufficient"
    )


def r7_taint_toleration_mismatch() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.events = [
        make_event(
            "0/4 nodes are available: 1 node(s) were unschedulable, 3 node(s) had untolerated taint {critical: true}.",
            reason="FailedScheduling",
        )
    ]
    return _unscheduled(
        c,
        nodes=[make_node(name="master", cordoned=True)]
        + [
            make_node(name=f"worker-{i}", taints=[{"key": "critical", "value": "true", "effect": "NoSchedule"}])
            for i in range(3)
        ],
    )


def r8_node_selector_mismatch() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.events = [
        make_event(
            "0/4 nodes are available: 1 node(s) were unschedulable, 3 node(s) didn't match Pod's node affinity/selector.",
            reason="FailedScheduling",
        )
    ]
    return _unscheduled(
        c,
        node_selector={"environment": "production"},
        nodes=[make_node(name="master", cordoned=True)]
        + [make_node(name=f"worker-{i}", labels={"environment": "staging"}) for i in range(3)],
    )


def r8_node_affinity_mismatch() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.events = [
        make_event(
            "0/4 nodes are available: 1 node(s) were unschedulable, 3 node(s) didn't match Pod's node affinity/selector.",
            reason="FailedScheduling",
        )
    ]
    return _unscheduled(
        c,
        node_affinity_required=("environment", "production"),
        nodes=[make_node(name="master", cordoned=True)]
        + [make_node(name=f"worker-{i}", labels={"environment": "staging"}) for i in range(3)],
    )


def r8_neither_selector_nor_affinity_present() -> FakeKubernetesClient:
    """The scheduler claims a mismatch, but the Pod spec has neither a nodeSelector nor an
    affinity rule -- must abstain, not fabricate one."""
    c = FakeKubernetesClient()
    c.events = [
        make_event(
            "0/4 nodes are available: 3 node(s) didn't match Pod's node affinity/selector.",
            reason="FailedScheduling",
        )
    ]
    return _unscheduled(c, nodes=[make_node(name="worker-1")])


_R9_EVENT = (
    "0/4 nodes are available: 1 node(s) were unschedulable, 3 node(s) didn't match pod "
    "anti-affinity rules."
)
_R9_NODES = [make_node(name="master", cordoned=True)] + [make_node(name=f"worker-{i}") for i in range(3)]


def r9_no_selector_on_replica_set() -> FakeKubernetesClient:
    """The owning ReplicaSet exists but has no selector at all -- genuinely missing evidence,
    not a collection failure."""
    c = FakeKubernetesClient()
    c.events = [make_event(_R9_EVENT, reason="FailedScheduling")]
    return _unscheduled(c, nodes=_R9_NODES)  # rs_match_labels=None -- no selector, by default


def r9_replica_set_unavailable() -> FakeKubernetesClient:
    """Missing evidence: the owning ReplicaSet itself could not be read."""
    c = FakeKubernetesClient()
    c.events = [make_event(_R9_EVENT, reason="FailedScheduling")]
    return _unscheduled(c, nodes=_R9_NODES, rs_unavailable=True)


def r9_siblings_unavailable() -> FakeKubernetesClient:
    """Partial collection failure: the selector is known, but the sibling-pod LIST call itself
    failed."""
    c = FakeKubernetesClient()
    c.events = [make_event(_R9_EVENT, reason="FailedScheduling")]
    return _unscheduled(
        c, nodes=_R9_NODES, rs_match_labels={"app": "demo-pod"},
        sibling_pods_error=ApiForbiddenError("pods is forbidden for this user"),
    )


def r9_no_siblings_found() -> FakeKubernetesClient:
    """The selector is known and the LIST call succeeded, but it found zero matching pods --
    itself inconsistent with an anti-affinity conflict."""
    c = FakeKubernetesClient()
    c.events = [make_event(_R9_EVENT, reason="FailedScheduling")]
    return _unscheduled(c, nodes=_R9_NODES, rs_match_labels={"app": "demo-pod"}, sibling_pods=[])


def r9_siblings_not_placed() -> FakeKubernetesClient:
    """Sibling pods matching the selector exist, but none has a node assigned yet."""
    c = FakeKubernetesClient()
    c.events = [make_event(_R9_EVENT, reason="FailedScheduling")]
    return _unscheduled(
        c, nodes=_R9_NODES, rs_match_labels={"app": "demo-pod"},
        sibling_pods=[
            make_sibling_pod(name="demo-sibling-1", labels={"app": "demo-pod"}, node_name=None, phase="Pending")
        ],
    )


def r9_siblings_not_on_known_schedulable_node() -> FakeKubernetesClient:
    """A sibling is placed, but on a node this project has no record of as schedulable (e.g.
    a node outside the known/cordoned set) -- cannot confirm against known candidates."""
    c = FakeKubernetesClient()
    c.events = [make_event(_R9_EVENT, reason="FailedScheduling")]
    return _unscheduled(
        c, nodes=_R9_NODES, rs_match_labels={"app": "demo-pod"},
        sibling_pods=[
            make_sibling_pod(name="demo-sibling-1", labels={"app": "demo-pod"}, node_name="unknown-node-99")
        ],
    )


def r9_anti_affinity_confirmed() -> FakeKubernetesClient:
    """The positive case: a sibling matching the ReplicaSet's own selector is placed on a
    known schedulable node -- consistent with the scheduler's reported conflict."""
    c = FakeKubernetesClient()
    c.events = [make_event(_R9_EVENT, reason="FailedScheduling")]
    return _unscheduled(
        c, nodes=_R9_NODES, rs_match_labels={"app": "demo-pod"},
        sibling_pods=[
            make_sibling_pod(name="demo-sibling-1", labels={"app": "demo-pod"}, node_name="worker-0")
        ],
    )


def r10_pv_bound_elsewhere() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.events = [
        make_event("0/4 nodes are available: pod has unbound immediate PersistentVolumeClaims.", reason="FailedScheduling")
    ]
    return _unscheduled(
        c,
        pvc_claim_names=["demo-pvc"],
        pvcs=[make_pvc(name="demo-pvc", phase="Pending", storage_class_name="standard")],
        pvs=[make_pv(name="only-pv", storage_class_name="standard", bound_claim_name="other-pvc")],
    )


def r10_no_matching_pv() -> FakeKubernetesClient:
    c = FakeKubernetesClient()
    c.events = [
        make_event("0/4 nodes are available: pod has unbound immediate PersistentVolumeClaims.", reason="FailedScheduling")
    ]
    return _unscheduled(
        c,
        pvc_claim_names=["demo-pvc"],
        pvcs=[make_pvc(name="demo-pvc", phase="Pending", storage_class_name="fast-ssd")],
        pvs=[make_pv(name="only-pv", storage_class_name="standard")],  # no PV matches "fast-ssd" at all
    )


def scheduling_blocked_no_event_found() -> FakeKubernetesClient:
    """Missing-evidence case: the pod is unscheduled, but no FailedScheduling event was
    collected at all (e.g. expired) -- must abstain explicitly, not guess a cause."""
    c = FakeKubernetesClient()
    c.events = []
    return _unscheduled(c, nodes=[make_node(name="worker-1")])


def scheduling_unrecognized_pattern() -> FakeKubernetesClient:
    """Phase 0.14: a FailedScheduling event IS present, but its message matches none of
    R5-R10's recognized patterns (here: a port-conflict message, a real, different scheduler
    failure reason never covered by this rule bank) -- must abstain explicitly
    (rule:scheduling_unrecognized), never silently drop the event or guess a cause."""
    c = FakeKubernetesClient()
    c.events = [
        make_event(
            "0/4 nodes are available: 4 node(s) didn't have free ports for the requested pod ports.",
            reason="FailedScheduling",
        )
    ]
    return _unscheduled(c, nodes=[make_node(name="worker-1")])


def scheduling_blocked_nodes_unavailable() -> FakeKubernetesClient:
    """Partial-collection-failure case: the FailedScheduling event is present, but the Node
    list call itself fails (RBAC denial) -- R5 must abstain citing the gap, not claim a count."""
    c = FakeKubernetesClient()
    c.events = [make_event("0/4 nodes are available: 4 node(s) were unschedulable.", reason="FailedScheduling")]
    c.pod = make_pod(unscheduled=True)
    c.nodes_error = ApiForbiddenError("nodes is forbidden for this user")
    return _with_owner_chain(c)


# --- R11 (Runtime-health-fault, Phase 0.13) ---------------------------------------------------
#
# Message text verified verbatim against docs/research/cloud-opsbench-evidence-feasibility.md
# (startup/60): "Liveness probe failed: ... connection refused" / "Readiness probe failed: ...
# connection refused".

_R11_LIVENESS_MESSAGE = (
    'Liveness probe failed: Get "http://10.244.0.5:8080/healthz": dial tcp '
    "10.244.0.5:8080: connect: connection refused"
)
_R11_READINESS_MESSAGE = (
    'Readiness probe failed: Get "http://10.244.0.5:8080/healthz": dial tcp '
    "10.244.0.5:8080: connect: connection refused"
)


def r11_liveness_probe_failure_confirmed() -> FakeKubernetesClient:
    """Positive case: a liveness-probe-failure event, a container not yet crash-looping (proves
    R11 fires on an earlier signal than R4), and a restartCount consistent with the event."""
    c = FakeKubernetesClient()
    c.pod = make_pod(running=True, restart_count=3)
    c.events = [
        make_event(
            _R11_LIVENESS_MESSAGE, reason="Unhealthy", count=3,
            field_path="spec.containers{server}",
        )
    ]
    return _with_owner_chain(c)


def r11_readiness_probe_failure_confirmed() -> FakeKubernetesClient:
    """Positive case: a readiness-probe-failure event and a container currently not ready."""
    c = FakeKubernetesClient()
    c.pod = make_pod(running=True, ready=False)
    c.events = [
        make_event(
            _R11_READINESS_MESSAGE, reason="Unhealthy",
            field_path="spec.containers{server}",
        )
    ]
    return _with_owner_chain(c)


def r11_probe_type_unrecognized() -> FakeKubernetesClient:
    """An 'Unhealthy' event whose message matches neither the liveness nor readiness
    substring -- must abstain, not guess which probe class it belongs to."""
    c = FakeKubernetesClient()
    c.pod = make_pod(running=True)
    c.events = [
        make_event(
            "Startup probe failed: container is not yet ready", reason="Unhealthy",
            field_path="spec.containers{server}",
        )
    ]
    return _with_owner_chain(c)


def r11_container_not_identified() -> FakeKubernetesClient:
    """A liveness-probe-failure event whose involvedObject.fieldPath does not resolve to any
    known container -- strict identification only, no single-container fallback."""
    c = FakeKubernetesClient()
    c.pod = make_pod(running=True, restart_count=3)
    c.events = [
        make_event(
            _R11_LIVENESS_MESSAGE, reason="Unhealthy", count=3,
            field_path="spec.containers{does-not-exist}",
        )
    ]
    return _with_owner_chain(c)


def r11_status_not_corroborating() -> FakeKubernetesClient:
    """A liveness-probe-failure event is recorded, but the container's restartCount is 0 --
    event and status disagree; R11 must not assert a DETERMINISTIC_FINDING from this alone."""
    c = FakeKubernetesClient()
    c.pod = make_pod(running=True)  # restart_count defaults to 0
    c.events = [
        make_event(
            _R11_LIVENESS_MESSAGE, reason="Unhealthy",
            field_path="spec.containers{server}",
        )
    ]
    return _with_owner_chain(c)


def r11_unrelated_events_ignored() -> FakeKubernetesClient:
    """A healthy pod with only unrelated (non-Unhealthy) events -- R11 must produce nothing."""
    c = FakeKubernetesClient()
    c.pod = make_pod(running=True)
    c.events = [
        make_event("Successfully assigned demo-ns/demo-pod to worker-01", reason="Scheduled", event_type="Normal"),
        make_event("Started container server", reason="Started", event_type="Normal"),
    ]
    return _with_owner_chain(c)


def r11_liveness_and_readiness_both_present() -> FakeKubernetesClient:
    """Both probe classes fail for the same container at once -- R11 must evaluate each
    independently rather than merging or arbitrarily picking one."""
    c = FakeKubernetesClient()
    c.pod = make_pod(running=True, restart_count=2, ready=False)
    c.events = [
        make_event(
            _R11_LIVENESS_MESSAGE, reason="Unhealthy", count=2,
            field_path="spec.containers{server}",
        ),
        make_event(
            _R11_READINESS_MESSAGE, reason="Unhealthy",
            field_path="spec.containers{server}", name="demo-pod.ev2",
        ),
    ]
    return _with_owner_chain(c)


def r11_coexists_with_r4_log_pattern() -> FakeKubernetesClient:
    """A crash-looping container with both a recognized R4 log pattern and a liveness-probe-
    failure event -- R11 and R4 are complementary, not competing, and must both fire."""
    c = FakeKubernetesClient()
    c.pod = make_pod(crash_loop=True, last_terminated_reason="Error", restart_count=3)
    c.events = [
        make_event(
            _R11_LIVENESS_MESSAGE, reason="Unhealthy", count=3,
            field_path="spec.containers{server}",
        )
    ]
    c.logs["server"] = "starting up\nFatal error: can't open the RDB file dump.rdb: Permission denied\n"
    return _with_owner_chain(c)


# --- Phase 0.15: five previously-untested abstention/fallback branches -------------------------
#
# Each of these reaches a real, named outcome branch in rules/engine.py or rules/scheduling.py
# that until now had zero fixture or test coverage -- see docs' coverage audit. Every fixture
# below is built to reach ONE specific branch realistically, not merely to satisfy a rule id.


def r1_r2_r3_unrecognized_pull_failure() -> FakeKubernetesClient:
    """An ImagePullBackOff event IS present, but its message matches none of R1 (DNS)/R2
    (not-found)/R3 (auth)'s recognized patterns -- a real, different registry failure (Docker
    Hub's rate-limit message) that this rule bank does not claim to cover."""
    c = FakeKubernetesClient()
    c.pod = make_pod(waiting_reason="ErrImagePull")
    c.events = [
        make_event(
            "toomanyrequests: You have reached your pull rate limit. "
            "https://www.docker.com/increase-rate-limit"
        )
    ]
    return _with_owner_chain(c)


def r5_no_cordoned_node_found() -> FakeKubernetesClient:
    """The scheduler reports nodes as unschedulable, Node evidence WAS collected successfully,
    but none of the known nodes actually show spec.unschedulable=true -- distinct from both
    R5_nodes_unavailable (collection failed) and R5_partial_cordon (some are cordoned)."""
    c = FakeKubernetesClient()
    c.events = [make_event("0/2 nodes are available: 2 node(s) were unschedulable.", reason="FailedScheduling")]
    return _unscheduled(c, nodes=[make_node(name="worker-1"), make_node(name="worker-2")])


def r7_taint_not_identified() -> FakeKubernetesClient:
    """The scheduler reports an untolerated taint, but the only schedulable node's evidence
    shows no taints at all -- the Node evidence does not corroborate the scheduler's claim."""
    c = FakeKubernetesClient()
    c.events = [
        make_event("0/1 nodes are available: 1 node(s) had untolerated taint {critical: true}.", reason="FailedScheduling")
    ]
    return _unscheduled(c, nodes=[make_node(name="worker-1", taints=[])])


def r10_pvc_unavailable() -> FakeKubernetesClient:
    """The scheduler reports an unbound PVC, but the Pod's referenced PVC itself could not be
    collected (RBAC denial) -- R10 must abstain citing the gap, not claim a specific PVC phase."""
    c = FakeKubernetesClient()
    c.events = [
        make_event("0/4 nodes are available: pod has unbound immediate PersistentVolumeClaims.", reason="FailedScheduling")
    ]
    result = _unscheduled(c, pvc_claim_names=["demo-pvc"])
    c.persistent_volume_claim_errors["demo-pvc"] = ApiForbiddenError(
        "persistentvolumeclaims is forbidden for this user"
    )
    return result


def r10_no_pending_pvc_found() -> FakeKubernetesClient:
    """The scheduler reports an unbound PVC, PVC evidence WAS collected, but it already shows
    phase=Bound (e.g. the scheduler's event is stale) -- none of the Pod's PVCs are Pending, so
    R10 cannot confirm the reported cause against the PVC evidence it actually has."""
    c = FakeKubernetesClient()
    c.events = [
        make_event("0/4 nodes are available: pod has unbound immediate PersistentVolumeClaims.", reason="FailedScheduling")
    ]
    return _unscheduled(
        c,
        pvc_claim_names=["demo-pvc"],
        pvcs=[make_pvc(name="demo-pvc", phase="Bound", storage_class_name="standard")],
    )


def report_quality_finding_gap_and_contradiction_together() -> FakeKubernetesClient:
    """Phase 0.16: composes the existing R6 capacity-contradiction case (a DETERMINISTIC_FINDING
    plus a recorded contradiction) with a genuine, independent collection-error evidence gap
    (the owning ReplicaSet is forbidden) -- three distinct signal types in one bundle, none of
    which should affect whether the others are produced or how they are reported."""
    c = FakeKubernetesClient()
    c.events = [make_event("0/4 nodes are available: 3 Insufficient cpu.", reason="FailedScheduling")]
    return _unscheduled(
        c,
        container_resource_requests={"cpu": "2"},
        nodes=[make_node(name="worker-1", allocatable_cpu="8")],  # 8 >= 2 requested -- contradicts "insufficient"
        rs_unavailable=True,  # independent evidence gap: ReplicaSet collection forbidden
    )
