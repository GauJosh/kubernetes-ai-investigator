"""Collector-level behavior: error translation, bounded calls, kubeconfig discovery order --
all against the fake client or mocked config loaders. No live cluster is touched anywhere here."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from k8s_investigator.collector.client import (
    ApiForbiddenError,
    ApiNotFoundError,
    ApiTimeoutError,
    DEFAULT_LOG_SINCE_SECONDS,
    DEFAULT_LOG_TAIL_LINES,
    run_call,
)
from k8s_investigator.models.evidence import CollectionStatus
from k8s_investigator.models.refs import ObjectRef

from .conftest import collect_and_build
from .fixtures import scenarios


def _ref() -> ObjectRef:
    return ObjectRef(api_group="", api_version="v1", kind="Pod", namespace="ns", name="p")


def test_run_call_maps_not_found():
    def call():
        raise ApiNotFoundError()

    outcome = run_call(_ref(), call)
    assert outcome.status == CollectionStatus.NOT_FOUND


def test_run_call_maps_forbidden_to_unavailable_with_detail():
    def call():
        raise ApiForbiddenError("pods is forbidden for user X")

    outcome = run_call(_ref(), call)
    assert outcome.status == CollectionStatus.UNAVAILABLE
    assert "Forbidden" in outcome.error_detail
    assert "pods is forbidden" in outcome.error_detail


def test_run_call_maps_timeout():
    def call():
        raise ApiTimeoutError()

    outcome = run_call(_ref(), call)
    assert outcome.status == CollectionStatus.TIMEOUT


def test_run_call_never_raises_on_failure():
    """A failed call is evidence, not an exception that aborts collection."""

    def call():
        raise ApiForbiddenError("nope")

    outcome = run_call(_ref(), call)  # must not raise
    assert outcome.payload is None


def test_run_call_success_carries_payload():
    outcome = run_call(_ref(), lambda: {"status": "ok"})
    assert outcome.status == CollectionStatus.PRESENT
    assert outcome.payload == {"status": "ok"}


def test_logs_requested_with_default_bounds_when_unspecified():
    client = scenarios.r4_log_pattern_match()
    collect_and_build(client)
    _, _, kwargs = next(c for c in client.calls if c[0] == "get_pod_log")
    assert kwargs["tail_lines"] == DEFAULT_LOG_TAIL_LINES
    assert kwargs["since_seconds"] == DEFAULT_LOG_SINCE_SECONDS


def test_logs_use_previous_only_when_container_has_restarted():
    client = scenarios.r4_log_pattern_match()  # restart_count=4 in this fixture
    collect_and_build(client)
    log_call = next(c for c in client.calls if c[0] == "get_pod_log")
    assert log_call[2]["previous"] is True

    healthy_client = scenarios.healthy_pod()  # restart_count=0 -- logs are still collected
    # (for context), but `previous` must be False since the container has never restarted
    collect_and_build(healthy_client)
    healthy_log_call = next(c for c in healthy_client.calls if c[0] == "get_pod_log")
    assert healthy_log_call[2]["previous"] is False


def test_missing_pod_stops_dependent_calls_without_fabricating_them():
    """If the Pod itself cannot be read, there is nothing to derive a ServiceAccount name,
    container list, or owner chain from -- those calls must simply never be attempted."""
    client = scenarios.healthy_pod()
    client.pod = None  # forces ApiNotFoundError in the fake client
    collect_and_build(client)
    called = {c[0] for c in client.calls}
    assert called == {"get_pod", "list_events_for_pod"}


def test_load_kubeconfig_tries_incluster_before_kubeconfig_file():
    from k8s_investigator.collector.k8s_client import load_kubeconfig

    with patch("k8s_investigator.collector.k8s_client.k8s_config") as mock_config:
        load_kubeconfig("/some/path", "some-context")
        mock_config.load_incluster_config.assert_called_once()
        mock_config.load_kube_config.assert_not_called()


def test_load_kubeconfig_falls_back_to_kube_config_file():
    from k8s_investigator.collector.k8s_client import load_kubeconfig

    with patch("k8s_investigator.collector.k8s_client.k8s_config") as mock_config:
        mock_config.ConfigException = Exception
        mock_config.load_incluster_config.side_effect = Exception("not in cluster")
        load_kubeconfig("/some/path", "some-context")
        mock_config.load_kube_config.assert_called_once_with(config_file="/some/path", context="some-context")
