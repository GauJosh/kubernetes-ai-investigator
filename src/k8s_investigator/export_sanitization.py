"""Export-time sanitization for saved ``EvidenceBundle`` files (``--save-bundle``).

This is a sibling of ``ai/model_input.py``'s redaction, not a replacement for it, and reuses the
exact same detectors from ``ai/redaction.py`` -- no second secret-detection system. The two exist
for different consumers and must stay independent:

* ``ai/model_input.py`` redacts a throwaway projection sent to an external model. It never
  touches the real ``EvidenceBundle``, and nothing it does is ever written to disk as a bundle.
* This module redacts a **real, valid, reloadable** ``EvidenceBundle`` -- the thing
  ``--save-bundle`` writes to disk and ``--from-bundle`` later reads back.

**The deterministic rule engine and report pipeline never see the output of this module.**
Evaluation always runs against the original, full-fidelity bundle collected from the cluster;
sanitization happens only at the export boundary, exactly once, right before serialization. This
mirrors ``ai/model_input.py``'s own stated invariant ("the real EvidenceBundle... is completely
unaware this module exists") and avoids the worse failure mode: a future rule silently going
blind because its own input was pre-redacted before it ever ran.

**Identity, stated precisely.** ``EvidenceItem.id`` and ``EvidenceBundle.id`` are content
hashes that include the payload (see ``canonical.py``). Redacting a payload necessarily changes
its hash -- there is no way to redact content and keep the same content-identity hash, because
the content is genuinely different now. This module recomputes every changed item's id and the
bundle's id from the sanitized content, using the project's existing canonical hashing functions
(the same pattern ``bundle_builder._project_sibling_pod_fields`` already uses: project the
payload down *before* computing its id, not after). The result is internally self-consistent
(``validate_bundle_integrity`` passes against it) but its id, and any changed item's id, will
differ from the raw bundle it was derived from -- that is a correct, expected consequence of the
content actually differing, not a bug to hide.

**Coverage, stated precisely.** Every ``EvidenceItem.payload`` in ``bundle.items`` *and* every
``CollectionOutcome.payload`` in ``bundle.queries`` is walked -- ``queries`` matters because it
holds the raw, pre-projection payload for every collector call (e.g. R9's sibling-Pod ``list``,
which ``items`` deliberately projects down to five safe fields per
``docs/research/read-only-access-matrix.md`` §8.3, but ``queries`` does not). ``error_detail``
strings on both item and query shapes are also scanned, in case a server error ever echoes
request content back.

**``bundle.contradictions`` is rejected outright if non-empty, never silently rewritten.** A
``ContradictionEdge`` references two ``EvidenceItem`` ids by value; sanitization can
legitimately change an item's id (it's a content hash of the now-different payload), which
would leave a pre-existing contradiction edge pointing at a stale or nonexistent item. In this
project's current design ``bundle.contradictions`` is always empty at the point a bundle is
saved (contradictions are a separate, report-time artifact computed by ``rules.contradictions``
and never persisted onto the bundle -- see ``models/evidence.py``), so this should never fire
today. It exists as a safety rail, not a feature: see
``BundleExportUnsupportedContradictionsError``.

**Not a comprehensive scanner.** Inherits every limitation already disclosed in
``ai/redaction.py``'s own docstring verbatim: no detection for bearer tokens or API keys without
a recognizable prefix, cloud access-key-ID formats, PEM/SSH private key blocks, high-entropy
strings with no contextual marker, or a secret split across multiple lines. This is a secondary
safeguard, not a security boundary.
"""

from __future__ import annotations

from .ai.redaction import redact_payload, redact_string_value
from .canonical import compute_bundle_id, compute_evidence_item_id
from .errors import BundleExportUnsupportedContradictionsError
from .models.evidence import CollectionOutcome, EvidenceBundle, EvidenceItem


def _sanitize_item(item: EvidenceItem) -> EvidenceItem:
    new_payload = redact_payload(item.payload)[0] if item.payload is not None else item.payload
    new_error_detail = (
        redact_string_value(item.error_detail)[0] if item.error_detail is not None else item.error_detail
    )
    if new_payload == item.payload and new_error_detail == item.error_detail:
        return item  # nothing changed -- keep the original object, same id

    new_id = compute_evidence_item_id(item.source_ref.model_dump(mode="json"), new_payload)
    return item.model_copy(update={"payload": new_payload, "error_detail": new_error_detail, "id": new_id})


def _sanitize_outcome(outcome: CollectionOutcome) -> CollectionOutcome:
    new_payload = redact_payload(outcome.payload)[0] if outcome.payload is not None else outcome.payload
    new_error_detail = (
        redact_string_value(outcome.error_detail)[0]
        if outcome.error_detail is not None
        else outcome.error_detail
    )
    if new_payload == outcome.payload and new_error_detail == outcome.error_detail:
        return outcome  # nothing changed -- keep the original object

    return outcome.model_copy(update={"payload": new_payload, "error_detail": new_error_detail})


def sanitize_bundle_for_export(bundle: EvidenceBundle) -> EvidenceBundle:
    """Return a new, independently valid ``EvidenceBundle`` with known-sensitive content
    replaced in every item and query payload. Never mutates ``bundle`` -- every Pydantic model
    involved is frozen, and every changed node is rebuilt via ``model_copy``, never edited in
    place. When nothing in ``bundle`` matches any detector, the result is equal to ``bundle``,
    including its id (a pure function of unchanged inputs produces the same hash).

    Raises ``BundleExportUnsupportedContradictionsError`` if ``bundle.contradictions`` is
    non-empty -- see the module docstring for why this is a safety rail, not a feature.
    """
    if bundle.contradictions:
        raise BundleExportUnsupportedContradictionsError(len(bundle.contradictions))

    sanitized_items = [_sanitize_item(item) for item in bundle.items]
    sanitized_queries = [_sanitize_outcome(outcome) for outcome in bundle.queries]

    new_bundle_id = compute_bundle_id(
        bundle.investigation_target.model_dump(mode="json"),
        [item.id for item in sanitized_items],
        collection_params={
            "namespace": bundle.investigation_target.namespace,
            "pod_name": bundle.investigation_target.name,
        },
    )

    return EvidenceBundle(
        id=new_bundle_id,
        investigation_target=bundle.investigation_target,
        collected_at_range=bundle.collected_at_range,
        items=sanitized_items,
        queries=sanitized_queries,
        contradictions=bundle.contradictions,
    )
