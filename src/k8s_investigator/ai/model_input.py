"""The AI-hypothesis layer's input contract: a pure, bounded, reproducible projection of an
already-validated EvidenceBundle and the deterministic findings already produced over it.

No parallel evidence system -- every field here is derived directly from EvidenceBundle,
EvidenceItem, and Finding (docs/phase-0.11-ai-hypothesis-layer-design.md §2). The only new
behavior is bounding (large string payloads, e.g. logs, are capped), projection (only
OBSERVED_FACT/DETERMINISTIC_FINDING findings are included -- never a prior AI_HYPOTHESIS, which
should not exist yet at this point in the pipeline, but is filtered out defensively in case a
caller passes one anyway), and redaction (``redaction.redact_payload`` -- see that module;
applied before truncation, and only to this projection, never to the EvidenceBundle itself).

``build_model_input`` is a pure function: the same ``bundle``/``findings`` always produce the
same returned dict, independent of any model call -- this is the one part of the AI-hypothesis
layer that keeps the project's full reproducibility guarantee (see canonical.py's
docstring for the two other kinds of reproducibility this project already relies on). Pass the
result through ``canonical.canonical_bytes``/``content_hash`` for a byte-for-byte, hashable
form (used by this module's golden-file tests).
"""

from __future__ import annotations

from typing import Any

from ..models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from ..models.findings import Finding, FindingKind
from .redaction import redact_payload, redact_string_value

# A tuning constant, not a hard requirement -- documented, not hidden, per
# docs/phase-0.11-ai-hypothesis-layer-design.md §8 item 3.
DEFAULT_MAX_PAYLOAD_CHARS = 4000


def _project_evidence_item(item: EvidenceItem, max_payload_chars: int) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "evidence_id": item.id,
        "source_ref": item.source_ref.model_dump(mode="json"),
        "status": item.status.value,
    }
    if item.status == EvidenceStatus.PRESENT:
        # Redact BEFORE truncation: truncating first could split a credential-shaped span
        # across the cut point and leave a partial fragment un-redacted on one side.
        payload, categories = redact_payload(item.payload)
        if categories:
            entry["redacted"] = True
            entry["redaction_categories"] = sorted(categories)
        if isinstance(payload, str) and len(payload) > max_payload_chars:
            entry["payload"] = payload[:max_payload_chars]
            entry["payload_truncated"] = True
            entry["payload_original_chars"] = len(item.payload)
        else:
            entry["payload"] = payload
    if item.expiry_note:
        entry["expiry_note"] = item.expiry_note
    if item.error_detail:
        entry["error_detail"] = item.error_detail
    return entry


def _project_finding(finding: Finding) -> dict[str, Any]:
    statement, _ = redact_string_value(finding.statement)
    limitation = finding.limitation
    if limitation is not None:
        limitation, _ = redact_string_value(limitation)
    return {
        "id": finding.id,
        "kind": finding.kind.value,
        "statement": statement,
        "evidence_ids": finding.evidence_ids,
        "produced_by": finding.produced_by,
        "limitation": limitation,
    }


def build_model_input(
    bundle: EvidenceBundle,
    findings: list[Finding],
    *,
    max_payload_chars: int = DEFAULT_MAX_PAYLOAD_CHARS,
) -> dict[str, Any]:
    """Build the exact JSON-serializable object sent to the model.

    ``findings`` is expected to be the OBSERVED_FACT/DETERMINISTIC_FINDING list already
    produced by ``rules.engine.evaluate_bundle`` -- any AI_HYPOTHESIS entries it might contain
    are excluded, since a hypothesis must never be shown to the model as if it were a fact or a
    deterministic result (and none should exist yet at this point in the pipeline).

    Every ``evidence_id`` referenced in ``findings`` and keyed in the returned ``"evidence"``
    mapping is exactly a real ``EvidenceItem.id`` in ``bundle`` -- the only identifier space a
    model-generated citation may ever name (docs/phase-0.11-ai-hypothesis-layer-design.md §2).
    """
    grounded_findings = [f for f in findings if f.kind != FindingKind.AI_HYPOTHESIS]
    return {
        "investigation_target": bundle.investigation_target.model_dump(mode="json"),
        "bundle_id": bundle.id,
        "evidence": {
            item.id: _project_evidence_item(item, max_payload_chars) for item in bundle.items
        },
        "findings": [_project_finding(f) for f in grounded_findings],
    }
