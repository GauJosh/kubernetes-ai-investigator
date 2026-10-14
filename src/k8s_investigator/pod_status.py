"""Small, shared predicates over a raw Pod payload.

Exists so the collector's collection-gating check (docs/research/read-only-access-matrix.md
§7.2) and the rule engine's trigger check use the *same* function, not two independently
written copies that could silently drift apart.
"""

from __future__ import annotations

from typing import Any


def is_scheduling_blocked(pod_payload: dict[str, Any]) -> bool:
    """True when the Pod's own evidence already shows it has not been scheduled.

    ``status.phase == "Pending"`` and a ``status.conditions[]`` entry with
    ``type == "PodScheduled"`` and ``status == "False"``. Uses only fields already present on
    a collected Pod object -- issues no API call of its own.
    """
    status = pod_payload.get("status", {})
    if status.get("phase") != "Pending":
        return False
    conditions = status.get("conditions") or []
    return any(c.get("type") == "PodScheduled" and c.get("status") == "False" for c in conditions)
