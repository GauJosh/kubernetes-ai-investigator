"""The collector's abstraction over a Kubernetes client, and the approved call surface.

``KubernetesClient`` is a structural protocol -- it exists specifically so the collector's code
*cannot* issue any call beyond these six methods, which are exactly the Milestone 1 approved
resource/verb rows (docs/research/read-only-access-matrix.md §3; approved per the Phase 0.8
decision). Both the real client (k8s_client.py) and the test fake (tests/fakes.py) implement
this same protocol, so the "application API-call-surface" test (tests/test_api_surface.py) is
checking something structurally true of the collector's code, not merely true by convention.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from ..models.evidence import CollectionOutcome, CollectionStatus
from ..models.refs import SourceRef

# Default collection bounds -- mandatory, per docs/research/read-only-access-matrix.md §4 and
# the Phase 0.8 approval that logs must be bounded, never fetched without limits.
DEFAULT_LOG_TAIL_LINES = 200
DEFAULT_LOG_SINCE_SECONDS = 7200  # 2 hours
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10


class ApiNotFoundError(Exception):
    """The requested object does not exist."""


class ApiForbiddenError(Exception):
    """The request was denied by RBAC or another authorization mechanism."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ApiUnavailableError(Exception):
    """Any other API error -- a non-404, non-403/401 failure."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ApiTimeoutError(Exception):
    """The request exceeded its bound and was abandoned."""


class KubernetesClient(Protocol):
    """Exactly the six approved Milestone 1 calls. No write, exec, attach, or port-forward
    method exists on this protocol -- there is nothing to disable, because there is nothing to
    call."""

    def get_pod(self, namespace: str, name: str) -> dict[str, Any]: ...

    def list_events_for_pod(
        self, namespace: str, pod_name: str, pod_uid: str | None
    ) -> list[dict[str, Any]]: ...

    def get_pod_log(
        self,
        namespace: str,
        name: str,
        container: str,
        *,
        tail_lines: int,
        since_seconds: int,
        previous: bool,
    ) -> str: ...

    def get_replica_set(self, namespace: str, name: str) -> dict[str, Any]: ...

    def get_deployment(self, namespace: str, name: str) -> dict[str, Any]: ...

    def get_service_account(self, namespace: str, name: str) -> dict[str, Any]: ...

    # --- Milestone 2 additions -- see docs/research/read-only-access-matrix.md §7 for the
    # exact RBAC this requires. Added only because R5-R10 require them; `get` on Node/
    # PersistentVolume is approved but never called -- see §7.1 for why only `list` is used.

    def get_persistent_volume_claim(self, namespace: str, name: str) -> dict[str, Any]: ...

    def list_nodes(self) -> list[dict[str, Any]]: ...

    def list_persistent_volumes(self) -> list[dict[str, Any]]: ...


def run_call(requested: SourceRef, call) -> CollectionOutcome:
    """Run one bounded API call, translating any failure into a typed CollectionOutcome.

    Never raises -- a failed call is evidence (an UNAVAILABLE/NOT_FOUND/TIMEOUT record), not an
    exception that aborts the rest of collection (docs/phase-0.8-mvp-blueprint.md §F).
    """
    try:
        payload = call()
        return CollectionOutcome(
            requested=requested,
            status=CollectionStatus.PRESENT,
            payload=payload,
            collected_at=datetime.now(timezone.utc),
        )
    except ApiNotFoundError:
        return CollectionOutcome(
            requested=requested,
            status=CollectionStatus.NOT_FOUND,
            collected_at=datetime.now(timezone.utc),
        )
    except ApiForbiddenError as exc:
        return CollectionOutcome(
            requested=requested,
            status=CollectionStatus.UNAVAILABLE,
            error_detail=f"Forbidden: {exc.detail}",
            collected_at=datetime.now(timezone.utc),
        )
    except ApiTimeoutError:
        return CollectionOutcome(
            requested=requested,
            status=CollectionStatus.TIMEOUT,
            error_detail="request exceeded the bounded timeout",
            collected_at=datetime.now(timezone.utc),
        )
    except ApiUnavailableError as exc:
        return CollectionOutcome(
            requested=requested,
            status=CollectionStatus.UNAVAILABLE,
            error_detail=exc.detail,
            collected_at=datetime.now(timezone.utc),
        )
