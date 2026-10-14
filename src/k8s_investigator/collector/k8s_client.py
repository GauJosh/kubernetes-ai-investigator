"""The real Kubernetes client: exactly the six approved calls, nothing else.

Uses ``_preload_content=False`` on every call so this project reads the *raw* JSON the API
server actually returned (camelCase keys, every field the server sent) rather than the
official client's generated model objects (which re-key everything to snake_case and can lag
behind newer API fields). This is also what makes the structured-object evidence this project
collects strictly better than the ``kubectl describe``-text evidence Phase 0.6 tested against
(docs/research/evidence-access-options.md §2.1) -- nothing is lost to a rendering step.

Kubeconfig handling uses the official client's normal discovery (in-cluster config first, then
the standard kubeconfig file/context resolution) -- no project-specific override.
"""

from __future__ import annotations

import json
import socket
from typing import Any

from kubernetes import client as k8s_client_lib
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException

from .client import (
    ApiForbiddenError,
    ApiNotFoundError,
    ApiTimeoutError,
    ApiUnavailableError,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
)


def load_kubeconfig(kubeconfig_path: str | None = None, context: str | None = None) -> None:
    """Standard discovery: in-cluster config first, then the usual kubeconfig resolution."""
    try:
        k8s_config.load_incluster_config()
        return
    except k8s_config.ConfigException:
        pass
    k8s_config.load_kube_config(config_file=kubeconfig_path, context=context)


def _translate_errors(exc: Exception) -> Exception:
    if isinstance(exc, ApiException):
        if exc.status == 404:
            return ApiNotFoundError()
        if exc.status in (401, 403):
            return ApiForbiddenError(f"{exc.status} {exc.reason}")
        return ApiUnavailableError(f"{exc.status} {exc.reason}")
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return ApiTimeoutError()
    return ApiUnavailableError(str(exc))


def _raw_json(response: Any) -> Any:
    return json.loads(response.data)


class K8sApiCollectorClient:
    """Implements collector.client.KubernetesClient against a real cluster."""

    def __init__(self, timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds
        self._core = k8s_client_lib.CoreV1Api()
        self._apps = k8s_client_lib.AppsV1Api()

    def get_pod(self, namespace: str, name: str) -> dict[str, Any]:
        try:
            resp = self._core.read_namespaced_pod(
                name, namespace, _preload_content=False, _request_timeout=self._timeout
            )
            return _raw_json(resp)
        except Exception as exc:  # noqa: BLE001 -- translated immediately below, never swallowed
            raise _translate_errors(exc) from exc

    def list_events_for_pod(
        self, namespace: str, pod_name: str, pod_uid: str | None
    ) -> list[dict[str, Any]]:
        field_selector = f"involvedObject.name={pod_name},involvedObject.namespace={namespace}"
        if pod_uid:
            field_selector += f",involvedObject.uid={pod_uid}"
        try:
            resp = self._core.list_namespaced_event(
                namespace,
                field_selector=field_selector,
                _preload_content=False,
                _request_timeout=self._timeout,
            )
            return _raw_json(resp).get("items", [])
        except Exception as exc:  # noqa: BLE001
            raise _translate_errors(exc) from exc

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
        try:
            resp = self._core.read_namespaced_pod_log(
                name,
                namespace,
                container=container,
                tail_lines=tail_lines,
                since_seconds=since_seconds,
                previous=previous,
                _preload_content=False,
                _request_timeout=self._timeout,
            )
            return resp.data.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            raise _translate_errors(exc) from exc

    def get_replica_set(self, namespace: str, name: str) -> dict[str, Any]:
        try:
            resp = self._apps.read_namespaced_replica_set(
                name, namespace, _preload_content=False, _request_timeout=self._timeout
            )
            return _raw_json(resp)
        except Exception as exc:  # noqa: BLE001
            raise _translate_errors(exc) from exc

    def get_deployment(self, namespace: str, name: str) -> dict[str, Any]:
        try:
            resp = self._apps.read_namespaced_deployment(
                name, namespace, _preload_content=False, _request_timeout=self._timeout
            )
            return _raw_json(resp)
        except Exception as exc:  # noqa: BLE001
            raise _translate_errors(exc) from exc

    def get_service_account(self, namespace: str, name: str) -> dict[str, Any]:
        try:
            resp = self._core.read_namespaced_service_account(
                name, namespace, _preload_content=False, _request_timeout=self._timeout
            )
            return _raw_json(resp)
        except Exception as exc:  # noqa: BLE001
            raise _translate_errors(exc) from exc

    # --- Milestone 2 additions -- docs/research/read-only-access-matrix.md §7 ---

    def get_persistent_volume_claim(self, namespace: str, name: str) -> dict[str, Any]:
        try:
            resp = self._core.read_namespaced_persistent_volume_claim(
                name, namespace, _preload_content=False, _request_timeout=self._timeout
            )
            return _raw_json(resp)
        except Exception as exc:  # noqa: BLE001
            raise _translate_errors(exc) from exc

    def list_nodes(self) -> list[dict[str, Any]]:
        try:
            resp = self._core.list_node(_preload_content=False, _request_timeout=self._timeout)
            return _raw_json(resp).get("items", [])
        except Exception as exc:  # noqa: BLE001
            raise _translate_errors(exc) from exc

    def list_persistent_volumes(self) -> list[dict[str, Any]]:
        try:
            resp = self._core.list_persistent_volume(
                _preload_content=False, _request_timeout=self._timeout
            )
            return _raw_json(resp).get("items", [])
        except Exception as exc:  # noqa: BLE001
            raise _translate_errors(exc) from exc
