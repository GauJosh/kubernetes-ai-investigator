"""Fully-configured FakeKubernetesClient scenarios, one per tested case.

Every scenario is named for the real Phase 0.6 case or rule outcome it corresponds to --
see docs/research/cloud-opsbench-evidence-feasibility.md for the verified source text each
event/log message is drawn from.
"""

from __future__ import annotations

from tests.fakes import ApiForbiddenError, FakeKubernetesClient

from .builders import make_deployment, make_event, make_pod, make_replica_set, make_service_account


def _with_owner_chain(client: FakeKubernetesClient, *, sa_image_pull_secrets=None) -> FakeKubernetesClient:
    client.replica_sets["demo-rs"] = make_replica_set()
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
