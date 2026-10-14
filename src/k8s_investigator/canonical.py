"""Canonical serialization and content-identity hashing.

Implements docs/phase-0.8-mvp-blueprint.md §D.1 exactly: content identity (this module) is kept
strictly separate from collection provenance (``collected_at`` / ``collected_at_range``, which
never participate in a hash computed here).

Canonicalization rules, applied before any hash is computed:
1. Recursively sort all JSON object keys lexicographically.
2. Encode as UTF-8 with no insignificant whitespace.
3. A ``None``-valued dict key is omitted, consistently, so a value that was never set and a
   value explicitly cleared canonicalize identically.
4. ``resourceVersion`` and ``managedFields`` are stripped wherever they appear nested, because
   (a) docs/research/event-timeline-contract.md §3 already establishes resourceVersion must
   never be treated as meaningful identity information, and (b) both are whole-object
   bookkeeping fields that can change for reasons this project never collects or cares about.

The rule engine's own "run twice against the identical in-memory bundle, get identical findings"
reproducibility test (see tests/test_rules.py) does not depend on any of this hashing -- it is a
separate, already-satisfied guarantee. Hashing here answers a different question: whether two
*independently produced* bundles (a live collection vs. a hand-built fixture) describe the same
evidence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_EXCLUDED_KEYS = frozenset({"resourceVersion", "managedFields"})


def _strip(obj: Any) -> Any:
    """Recursively drop ``None``-valued keys and the excluded bookkeeping keys."""
    if isinstance(obj, dict):
        return {
            key: _strip(value)
            for key, value in obj.items()
            if value is not None and key not in _EXCLUDED_KEYS
        }
    if isinstance(obj, list):
        return [_strip(item) for item in obj]
    return obj


def canonical_bytes(obj: Any) -> bytes:
    """Canonical UTF-8 JSON bytes for ``obj``, per the rules above."""
    cleaned = _strip(obj)
    return json.dumps(
        cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(obj: Any) -> str:
    """SHA-256 hex digest of ``obj``'s canonical form."""
    return sha256_hex(canonical_bytes(obj))


def compute_evidence_item_id(source_ref: dict[str, Any], payload: Any) -> str:
    """Content identity for one EvidenceItem. Deliberately excludes collected_at (provenance)."""
    return content_hash({"source_ref": source_ref, "payload": payload})


def compute_bundle_id(
    target: dict[str, Any], item_ids: list[str], collection_params: dict[str, Any]
) -> str:
    """Content identity for one EvidenceBundle. Deliberately excludes collected_at_range."""
    return content_hash(
        {
            "investigation_target": target,
            "item_ids": sorted(item_ids),
            "collection_params": collection_params,
        }
    )
