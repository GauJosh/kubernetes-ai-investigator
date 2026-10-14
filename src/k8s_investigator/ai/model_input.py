"""The AI-hypothesis layer's input contract: a pure, bounded, reproducible projection of an
already-validated EvidenceBundle and the deterministic findings already produced over it.

No parallel evidence system -- every field here is derived directly from EvidenceBundle,
EvidenceItem, and Finding (docs/phase-0.11-ai-hypothesis-layer-design.md §2). The only new
behavior is bounding (large string payloads, e.g. logs, are capped), projection (only
OBSERVED_FACT/DETERMINISTIC_FINDING findings are included -- never a prior AI_HYPOTHESIS, which
should not exist yet at this point in the pipeline, but is filtered out defensively in case a
caller passes one anyway), redaction (``redaction.redact_payload`` -- see that module; applied
before truncation, and only to this projection, never to the EvidenceBundle itself), and
line-indexing for multiline text evidence (see ``_project_line_indexed_text`` below).

**Line-indexing, and why it exists.** A real live-cluster evaluation (the Minikube
rag-service/Postgres case) showed a model correctly reading and quoting the exact right line
of a multiline log, but supplying a wrong ``LineRange.start_line``/``end_line`` -- it had to
count unnumbered lines itself and miscounted. ``validation._resolve_line_range`` addresses a
string payload by ``payload.splitlines()``, 0-indexed; this module now makes that indexing
scheme *explicit* to the model instead of implicit, by rendering any string-typed evidence
payload as ``{"lines": [{"line": i, "text": ...}, ...], "lines_total": N}`` -- the model copies
the ``line`` value it was shown directly into its citation instead of counting. This is a
presentation change only: it is computed from the exact same ``.splitlines()`` call
``_resolve_line_range`` itself uses, on the same (redacted, not yet truncated) string, so a
``line`` value shown to the model always equals the real index ``_resolve_line_range`` will use
against the real ``EvidenceBundle`` -- nothing about validation changes, and a citation is
exactly as easy or hard to forge as it was before; it is only easier to get *right*.

**Citation references, the next increment.** Even with explicit line numbers, two more
mechanical mistakes still occurred live: a correct quote with a too-narrow range, and (this
module's own contribution to fixing) nothing stopped the model from mis-transcribing a line's
text when asked to reproduce it itself. Each line now also carries a ``"ref"``
(``make_line_ref``) -- a stable, content-derived id keyed by ``(evidence_id, line)``, issued
only for lines that survived both redaction and truncation untouched. ``ai.hypothesis`` lets
the model *select* one or more refs instead of constructing a ``LineRange`` and transcribing
``quoted_text`` itself for line-indexed evidence; code resolves the selection into the exact
line text, so there is nothing left for the model to mis-transcribe. Structured (``field_path``)
citation is unchanged by this -- the model still constructs those itself, exactly as before.

``build_model_input`` is a pure function: the same ``bundle``/``findings`` always produce the
same returned dict, independent of any model call -- this is the one part of the AI-hypothesis
layer that keeps the project's full reproducibility guarantee (see canonical.py's
docstring for the two other kinds of reproducibility this project already relies on). Pass the
result through ``canonical.canonical_bytes``/``content_hash`` for a byte-for-byte, hashable
form (used by this module's golden-file tests).
"""

from __future__ import annotations

from typing import Any

from ..canonical import content_hash
from ..models.evidence import ContradictionEdge, EvidenceBundle, EvidenceItem, EvidenceStatus
from ..models.findings import Finding, FindingKind
from .redaction import redact_payload, redact_string_value

# A tuning constant, not a hard requirement -- documented, not hidden, per
# docs/phase-0.11-ai-hypothesis-layer-design.md §8 item 3.
DEFAULT_MAX_PAYLOAD_CHARS = 4000


def make_line_ref(evidence_id: str, line: int) -> str:
    """The stable, content-derived reference id for one line of one evidence item.

    Keyed by (evidence_id, line) -- deliberately never by the line's own text -- so two
    occurrences of identical text (duplicate log lines, or the same text at different lines)
    always get distinct refs (requirement 1), and a ref for a given line never changes due to
    truncation or how many other lines happen to be included (requirement 2): line 5's ref is
    always ``make_line_ref(evidence_id, 5)`` regardless of whether lines 6+ made the cut.
    """
    return content_hash({"evidence_id": evidence_id, "line": line})[:16]


def _project_line_indexed_text(
    original_text: str, max_payload_chars: int, evidence_id: str
) -> tuple[dict[str, Any], set[str]]:
    """Render ``original_text`` (the real, unredacted payload) as explicit, code-numbered,
    code-referenced lines. Returns ``(projection, redaction_categories)``.

    0-indexed via ``.splitlines()`` -- exactly ``validation._resolve_line_range``'s own
    addressing, computed here rather than left for the model to infer. Bounded at line
    granularity, not character granularity: a line is included whole or not at all, so the
    model is never shown a clipped partial line it might then mis-cite. The one exception is a
    single line whose own text exceeds the entire remaining budget -- that line's text is
    capped so at least one line is always shown when there is any content at all, still as one
    unambiguous index.

    Redaction happens *per line*, here, rather than once over the whole string beforehand --
    deliberately, so this function knows exactly which individual lines were touched. A line
    with nothing redacted gets a ``"ref"`` (``make_line_ref``); a line with something redacted
    does NOT -- it is still shown (with its placeholder text, for context), but is never
    assigned or exposed as a citable reference at all (requirement 3). None of this changes
    *what* gets redacted: every pattern in ``redaction.py`` already excludes whitespace
    (including newlines) from its matched spans, so applying them per line instead of once
    over the whole string catches exactly the same content, line-confined either way.

    Refs are computed after both redaction and this function's own truncation decision
    (requirement 2): a line's ref always corresponds to its final, as-shown form, and a line
    cut by truncation is never assigned one in the first place.
    """
    all_lines = original_text.splitlines()
    included: list[dict[str, Any]] = []
    budget_used = 0
    truncated = False
    all_categories: set[str] = set()
    for i, raw_line_text in enumerate(all_lines):
        line_text, line_categories = redact_string_value(raw_line_text)
        all_categories.update(line_categories)
        remaining = max_payload_chars - budget_used
        if remaining <= 0:
            truncated = True
            break
        if len(line_text) > remaining:
            line_text = line_text[:remaining]
            truncated = True
            entry: dict[str, Any] = {"line": i, "text": line_text}
            if not line_categories:
                entry["ref"] = make_line_ref(evidence_id, i)
            included.append(entry)
            break
        entry = {"line": i, "text": line_text}
        if not line_categories:
            entry["ref"] = make_line_ref(evidence_id, i)
        included.append(entry)
        budget_used += len(line_text)
    if len(included) < len(all_lines):
        truncated = True
    result: dict[str, Any] = {"lines": included, "lines_total": len(all_lines)}
    if truncated:
        result["truncated"] = True
    return result, all_categories


def _project_evidence_item(item: EvidenceItem, max_payload_chars: int) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "evidence_id": item.id,
        "source_ref": item.source_ref.model_dump(mode="json"),
        "status": item.status.value,
    }
    if item.status == EvidenceStatus.PRESENT:
        # Explicit, symmetric with _project_finding's "citable": False -- a six-scenario live
        # retest showed the model sometimes quoting a Finding's own rendered prose as if it
        # were literally present in the evidence it cites. Labeling both sides removes the
        # need to infer "which of these two sections is actual source text" from context.
        # Only set where there is a "payload" to cite at all -- a gap has nothing to quote.
        entry["citable"] = True
        if isinstance(item.payload, str):
            # Explicit discriminator, not left for the model to infer from shape: a live
            # evaluation showed the model sometimes using "line_range" against a structured
            # (dict) evidence item instead of "field_path" -- this names which pointer_type
            # applies, structurally, independent of any instruction text.
            entry["payload_shape"] = "line_indexed_text"
            # Redaction happens per line, inside _project_line_indexed_text, not here -- see
            # that function's docstring for why (it needs to know which specific lines were
            # touched, to withhold a "ref" for exactly those and no others).
            line_projection, categories = _project_line_indexed_text(item.payload, max_payload_chars, item.id)
            entry["payload"] = line_projection
            if categories:
                entry["redacted"] = True
                entry["redaction_categories"] = sorted(categories)
            if line_projection.get("truncated"):
                entry["payload_truncated"] = True
                entry["payload_original_chars"] = len(item.payload)
        else:
            # Redact BEFORE serialization: structured (dict) payloads are redacted as a
            # whole here, unchanged from before this increment -- field_path citation
            # behavior is explicitly out of scope for this increment.
            payload, categories = redact_payload(item.payload)
            if categories:
                entry["redacted"] = True
                entry["redaction_categories"] = sorted(categories)
            entry["payload_shape"] = "structured"
            entry["payload"] = payload
    if item.expiry_note:
        entry["expiry_note"] = item.expiry_note
    if item.error_detail:
        entry["error_detail"] = item.error_detail
    return entry


def _project_contradiction(edge: ContradictionEdge) -> dict[str, Any]:
    description, _ = redact_string_value(edge.description)
    return {
        "evidence_a": edge.evidence_a,
        "evidence_b": edge.evidence_b,
        "rule_id": edge.rule_id,
        "description": description,
    }


def _project_finding(finding: Finding) -> dict[str, Any]:
    statement, _ = redact_string_value(finding.statement)
    limitation = finding.limitation
    if limitation is not None:
        limitation, _ = redact_string_value(limitation)
    return {
        "id": finding.id,
        "kind": finding.kind.value,
        "statement": statement,
        # A six-scenario live retest showed the model quoting THIS field verbatim as if it
        # were literal content present in the evidence named by evidence_ids -- it is a
        # rendered, human/code-written summary, never a substring of any evidence payload.
        # "citable": False marks that explicitly; see _project_evidence_item's "citable": True
        # for the one place quoted_text may actually come from.
        "citable": False,
        "evidence_ids": finding.evidence_ids,
        "produced_by": finding.produced_by,
        "limitation": limitation,
    }


def build_model_input(
    bundle: EvidenceBundle,
    findings: list[Finding],
    *,
    contradictions: list[ContradictionEdge] | None = None,
    max_payload_chars: int = DEFAULT_MAX_PAYLOAD_CHARS,
) -> dict[str, Any]:
    """Build the exact JSON-serializable object sent to the model.

    ``findings`` is expected to be the OBSERVED_FACT/DETERMINISTIC_FINDING list already
    produced by ``rules.engine.evaluate_bundle`` -- any AI_HYPOTHESIS entries it might contain
    are excluded, since a hypothesis must never be shown to the model as if it were a fact or a
    deterministic result (and none should exist yet at this point in the pipeline).

    ``contradictions`` is ``evaluate_bundle``'s own second return value. A live evaluation
    found the model never acknowledging a recorded contradiction -- tracing it back, the
    model was never shown one in the first place, since this field did not exist here before.
    Omitting it (``None``) is equivalent to an empty list, not an error -- most investigations
    have no recorded contradiction.

    Every ``evidence_id`` referenced in ``findings``/``contradictions`` and keyed in the
    returned ``"evidence"`` mapping is exactly a real ``EvidenceItem.id`` in ``bundle`` -- the
    only identifier space a model-generated citation may ever name
    (docs/phase-0.11-ai-hypothesis-layer-design.md §2).
    """
    grounded_findings = [f for f in findings if f.kind != FindingKind.AI_HYPOTHESIS]
    return {
        "investigation_target": bundle.investigation_target.model_dump(mode="json"),
        "bundle_id": bundle.id,
        "evidence": {
            item.id: _project_evidence_item(item, max_payload_chars) for item in bundle.items
        },
        "findings": [_project_finding(f) for f in grounded_findings],
        "contradictions": [_project_contradiction(e) for e in (contradictions or [])],
    }
