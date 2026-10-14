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


__all__ = ["FakeKubernetesClient", "ApiNotFoundError", "ApiForbiddenError", "ApiTimeoutError"]
