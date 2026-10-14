"""A fake Kubernetes client implementing exactly k8s_investigator.collector.client.KubernetesClient.

Used by every collector-layer test so nothing in this test suite requires a real or ephemeral
cluster. Records every call made to it (method name, args, kwargs) so tests can assert on the
exact API-call surface and on log-request boundedness.
"""

from __future__ import annotations

from typing import Any

from k8s_investigator.collector.client import ApiForbiddenError, ApiNotFoundError, ApiTimeoutError


class FakeKubernetesClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

        self.pod: dict[str, Any] | None = None
        self.pod_error: Exception | None = None

        self.events: list[dict[str, Any]] = []
        self.events_error: Exception | None = None

        self.logs: dict[str, str] = {}
        self.log_errors: dict[str, Exception] = {}

        self.replica_sets: dict[str, dict[str, Any]] = {}
        self.replica_set_errors: dict[str, Exception] = {}

        self.deployments: dict[str, dict[str, Any]] = {}

        self.service_accounts: dict[str, dict[str, Any]] = {}
        self.service_account_errors: dict[str, Exception] = {}

        # Milestone 2 additions
        self.persistent_volume_claims: dict[str, dict[str, Any]] = {}
        self.persistent_volume_claim_errors: dict[str, Exception] = {}
        self.nodes: list[dict[str, Any]] = []
        self.nodes_error: Exception | None = None
        self.persistent_volumes: list[dict[str, Any]] = []
        self.persistent_volumes_error: Exception | None = None

        # R9 follow-up
        self.sibling_pods: list[dict[str, Any]] = []
        self.sibling_pods_error: Exception | None = None

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((method, args, kwargs))

    def get_pod(self, namespace: str, name: str) -> dict[str, Any]:
        self._record("get_pod", namespace, name)
        if self.pod_error:
            raise self.pod_error
        if self.pod is None:
            raise ApiNotFoundError()
        return self.pod

    def list_events_for_pod(self, namespace: str, pod_name: str, pod_uid: str | None) -> list[dict[str, Any]]:
        self._record("list_events_for_pod", namespace, pod_name, pod_uid=pod_uid)
        if self.events_error:
            raise self.events_error
        return self.events

    def get_pod_log(
        self,
        namespace: str,
        name: str,
        container: str,
        *,
        tail_lines: int,
        since_seconds: int,
        previous: bool,
    ) -> str:
        self._record(
            "get_pod_log",
            namespace,
            name,
            container,
            tail_lines=tail_lines,
            since_seconds=since_seconds,
            previous=previous,
        )
        if container in self.log_errors:
            raise self.log_errors[container]
        if container not in self.logs:
            raise ApiNotFoundError()
        return self.logs[container]

    def get_replica_set(self, namespace: str, name: str) -> dict[str, Any]:
        self._record("get_replica_set", namespace, name)
        if name in self.replica_set_errors:
            raise self.replica_set_errors[name]
        if name not in self.replica_sets:
            raise ApiNotFoundError()
        return self.replica_sets[name]

    def get_deployment(self, namespace: str, name: str) -> dict[str, Any]:
        self._record("get_deployment", namespace, name)
        if name not in self.deployments:
            raise ApiNotFoundError()
        return self.deployments[name]

    def get_service_account(self, namespace: str, name: str) -> dict[str, Any]:
        self._record("get_service_account", namespace, name)
        if name in self.service_account_errors:
            raise self.service_account_errors[name]
        if name not in self.service_accounts:
            raise ApiNotFoundError()
        return self.service_accounts[name]

    # --- Milestone 2 additions ---

    def get_persistent_volume_claim(self, namespace: str, name: str) -> dict[str, Any]:
        self._record("get_persistent_volume_claim", namespace, name)
        if name in self.persistent_volume_claim_errors:
            raise self.persistent_volume_claim_errors[name]
        if name not in self.persistent_volume_claims:
            raise ApiNotFoundError()
        return self.persistent_volume_claims[name]

    def list_nodes(self) -> list[dict[str, Any]]:
        self._record("list_nodes")
        if self.nodes_error:
            raise self.nodes_error
        return self.nodes

    def list_persistent_volumes(self) -> list[dict[str, Any]]:
        self._record("list_persistent_volumes")
        if self.persistent_volumes_error:
            raise self.persistent_volumes_error
        return self.persistent_volumes

    # --- R9 follow-up ---

    def list_pods_by_label_selector(self, namespace: str, label_selector: str) -> list[dict[str, Any]]:
        self._record("list_pods_by_label_selector", namespace, label_selector)
        if self.sibling_pods_error:
            raise self.sibling_pods_error
        return self.sibling_pods


class FakeHypothesisModelClient:
    """A fake implementing exactly k8s_investigator.ai.model_client.HypothesisModelClient.

    Mirrors FakeKubernetesClient's pattern: records every call (the exact model_input dict it
    was given) and returns a programmed canned response, or raises a programmed error -- no
    network, no real model, ever. Used by every ai-layer test in this suite.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response: str = "[]"
        self.error: Exception | None = None

    def generate(self, model_input: dict[str, Any]) -> str:
        self.calls.append(model_input)
        if self.error:
            raise self.error
        return self.response


def make_marker_deterministic_finding():
    """A minimal, valid DETERMINISTIC_FINDING used only to satisfy
    ai.hypothesis.generate_ai_hypotheses's HIGH-confidence grounding check in tests that are
    not testing that check specifically. Its own evidence_id need not exist in any particular
    test's bundle -- the check only counts which finding *kinds* are present, never validates
    this marker's own citation against any bundle.
    """
    from k8s_investigator.models.findings import Finding, FindingKind
    from k8s_investigator.models.locators import EvidenceLocator, FieldPath

    return Finding(
        id="finding:marker-deterministic",
        kind=FindingKind.DETERMINISTIC_FINDING,
        statement="marker finding for tests that are not about confidence grounding",
        evidence_locators=[
            EvidenceLocator(
                evidence_id="marker-evidence", pointer=FieldPath(json_pointer="/x"), quoted_text="y",
            )
        ],
        produced_by="rule:marker",
    )


__all__ = [
    "FakeKubernetesClient",
    "FakeHypothesisModelClient",
    "make_marker_deterministic_finding",
    "ApiNotFoundError",
    "ApiForbiddenError",
    "ApiTimeoutError",
]
