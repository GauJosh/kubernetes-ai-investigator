"""Turning an untrusted model response into validated AI_HYPOTHESIS Findings.

The model's output is never trusted directly as a ``Finding``. It is first parsed into
``RawHypothesisCandidate`` -- reusing ``EvidenceLocator``/``Confidence`` verbatim, no parallel
schema -- then promoted into a real ``Finding`` with code, never the model, assigning
``kind``/``id``/``produced_by`` (docs/phase-0.11-ai-hypothesis-layer-design.md §3.2). Every
candidate is then run through the exact same ``validate_finding_references`` gate every other
Finding in this project goes through before being accepted; a candidate that fails is dropped,
never surfaced as if it were trustworthy -- the same discipline
``report_builder.build_report`` already applies to rule-produced findings.

**Citation references (line-indexed text only).** For structured evidence, the model still
constructs a complete ``FieldPathCitation`` itself, unchanged from before. For line-indexed
text (logs), the model instead selects one or more ``"ref"`` ids
(``model_input.make_line_ref``) via ``LineRefCitation`` -- it is no longer asked to construct
a ``LineRange`` or transcribe ``quoted_text`` for that evidence shape at all.
``_resolve_line_citation`` turns a selection into the exact same ``EvidenceLocator`` shape any
other citation produces, by looking up each ref in a catalog built from the exact model input
that was actually sent (``_build_ref_catalog``) -- never by re-deriving anything from the real
bundle directly, so a reference always resolves to precisely what the model was shown, and
``validate_finding_references`` (completely unchanged) remains the final, independent
authority once the real ``EvidenceLocator`` exists.

``generate_ai_hypotheses`` is the only entry point a caller needs. It is never called by any
default pipeline path -- a caller must explicitly invoke it with a real
``HypothesisModelClient`` (none exists in this phase; see ``model_client.py``).
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..canonical import content_hash
from ..errors import (
    CitationReferenceEvidenceMismatchError,
    FindingStatementCitedAsEvidenceError,
    InvestigatorError,
    MalformedCitationReferenceSelectionError,
    UnacknowledgedContradictionError,
    UngroundedConfidenceError,
    UnknownCitationReferenceError,
)
from ..models.evidence import ContradictionEdge, EvidenceBundle
from ..models.findings import Confidence, Finding, FindingKind
from ..models.locators import EvidenceLocator, FieldPath, LineRange
from ..validation import validate_finding_references
from .model_client import HypothesisModelClient
from .model_input import build_model_input


class HypothesisParseError(Exception):
    """The model's raw response, as a whole, could not be understood at all (not JSON, or not
    a JSON array) -- distinct from one malformed candidate within an otherwise-good array,
    which is dropped individually rather than voiding the whole response (see
    ``parse_candidates``). Deliberately not caught inside ``generate_ai_hypotheses`` -- a
    caller must decide how to degrade (e.g. ship the deterministic report with a visible
    warning), the same way ``cli.py`` already decides how to handle an ``InvestigatorError`` at
    its own boundary, rather than this layer silently collapsing "the response was broken" into
    the same empty-list signal as "the model legitimately found nothing."""


class FieldPathCitation(BaseModel):
    """A structured-evidence citation, unchanged from before the citation-reference increment:
    the model constructs the complete citation itself, and ``validate_finding_references``
    compares its ``quoted_text`` against the real bundle exactly as it always has. Narrowed
    from the general ``EvidenceLocator`` to ``FieldPath`` only -- a ``line_range`` pointer is
    no longer an accepted shape here at all; see ``LineRefCitation`` for line-indexed text.
    """

    model_config = ConfigDict(frozen=True)

    evidence_id: str
    pointer: FieldPath
    quoted_text: str


class LineRefCitation(BaseModel):
    """A line-indexed-text citation: the model selects one or more pre-issued ``"ref"`` ids
    (``model_input.make_line_ref``) instead of constructing a ``LineRange`` and transcribing
    ``quoted_text`` itself. ``evidence_id`` is the model's own explicit claim about which
    evidence this citation is about -- checked against what the references actually resolve
    to, never trusted on its own (see ``_resolve_line_citation``).
    """

    model_config = ConfigDict(frozen=True)

    evidence_id: str
    refs: list[str] = Field(min_length=1)


class RawHypothesisCandidate(BaseModel):
    """The untrusted shape the model is asked to produce.

    Deliberately narrower than ``Finding``: no ``id``, ``kind``, or ``produced_by`` field
    exists here at all, because those three are always code-assigned in
    ``make_ai_hypothesis_finding``, never model-assigned (docs/phase-0.11-ai-hypothesis-layer-
    design.md §3.2) -- there is nothing for a model response to fill in that could impersonate
    a deterministic finding or claim a false identity.

    ``evidence_locators`` (structured/``field_path`` only) and ``line_citations``
    (line-indexed text only, by reference) are two independent channels -- a candidate may use
    either, both, or neither (an empty candidate is simply rejected downstream for having no
    evidence at all, exactly as before this increment).
    """

    model_config = ConfigDict(frozen=True)

    statement: str
    evidence_locators: list[FieldPathCitation] = Field(default_factory=list)
    line_citations: list[LineRefCitation] = Field(default_factory=list)
    confidence: Confidence
    limitation: str | None = None
    contradicts: list[str] | None = None


def _build_ref_catalog(model_input: dict) -> dict[str, tuple[str, int, str]]:
    """Rebuild the ref -> (evidence_id, line, text) catalog from the exact model input that
    was actually sent to the model. Never re-derives anything from the real EvidenceBundle
    directly -- a reference resolves to precisely what the model was shown (already redacted,
    already truncated), which is the only thing a selection of it can honestly mean.
    """
    catalog: dict[str, tuple[str, int, str]] = {}
    for evidence_id, entry in model_input.get("evidence", {}).items():
        payload = entry.get("payload")
        if isinstance(payload, dict) and "lines" in payload:
            for line_entry in payload["lines"]:
                ref = line_entry.get("ref")
                if ref is not None:
                    catalog[ref] = (evidence_id, line_entry["line"], line_entry["text"])
    return catalog


def _resolve_line_citation(
    citation: LineRefCitation, catalog: dict[str, tuple[str, int, str]]
) -> EvidenceLocator:
    """Turn a reference selection into a real ``EvidenceLocator`` -- the only place
    ``quoted_text`` for a line-indexed citation comes from code, never the model. Every
    reference id is treated as untrusted input: unknown, cross-item, duplicated, or
    non-contiguous selections are all rejected outright, never repaired or substituted.
    """
    resolved: list[tuple[int, str]] = []
    for ref in citation.refs:
        entry = catalog.get(ref)
        if entry is None:
            raise UnknownCitationReferenceError(ref)
        actual_evidence_id, line, text = entry
        if actual_evidence_id != citation.evidence_id:
            raise CitationReferenceEvidenceMismatchError(ref, citation.evidence_id, actual_evidence_id)
        resolved.append((line, text))

    lines_selected = [line for line, _ in resolved]
    if len(lines_selected) != len(set(lines_selected)):
        raise MalformedCitationReferenceSelectionError(citation.refs, "duplicate line in selection")

    resolved.sort(key=lambda pair: pair[0])
    sorted_lines = [line for line, _ in resolved]
    if sorted_lines != list(range(sorted_lines[0], sorted_lines[-1] + 1)):
        raise MalformedCitationReferenceSelectionError(
            citation.refs, "selected lines are not a contiguous, ascending run"
        )

    quoted_text = "\n".join(text for _, text in resolved)
    return EvidenceLocator(
        evidence_id=citation.evidence_id,
        pointer=LineRange(start_line=sorted_lines[0], end_line=sorted_lines[-1]),
        quoted_text=quoted_text,
    )


def _resolve_candidate_locators(
    candidate: RawHypothesisCandidate, catalog: dict[str, tuple[str, int, str]]
) -> list[EvidenceLocator]:
    """Combine both citation channels into the single, real ``EvidenceLocator`` list a
    ``Finding`` actually carries. Raises on the first invalid ``line_citations`` entry --
    caught by the same per-candidate ``try/except`` ``generate_ai_hypotheses`` already uses
    for every other rejection reason, so a bad reference drops the whole candidate exactly
    like an unknown evidence_id or a quote mismatch always has.
    """
    locators = [
        EvidenceLocator(evidence_id=fp.evidence_id, pointer=fp.pointer, quoted_text=fp.quoted_text)
        for fp in candidate.evidence_locators
    ]
    locators.extend(_resolve_line_citation(lc, catalog) for lc in candidate.line_citations)
    return locators


def parse_candidates(raw_response: str) -> list[RawHypothesisCandidate]:
    """Parse the model's raw text into typed candidates.

    Raises ``HypothesisParseError`` only when the response as a whole cannot be understood
    (invalid JSON, or valid JSON that is not an array). An individual array element that does
    not match ``RawHypothesisCandidate`` is dropped and the rest of the array is still
    processed -- one malformed candidate must not void an otherwise-usable response.
    """
    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise HypothesisParseError(f"model response is not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise HypothesisParseError(
            f"model response must be a JSON array of candidates, got {type(data).__name__}"
        )

    candidates: list[RawHypothesisCandidate] = []
    for raw_candidate in data:
        try:
            candidates.append(RawHypothesisCandidate.model_validate(raw_candidate))
        except ValidationError:
            continue
    return candidates


def make_ai_hypothesis_finding(
    candidate: RawHypothesisCandidate, model_identifier: str, resolved_locators: list[EvidenceLocator]
) -> Finding:
    """Promote one parsed candidate into a real Finding, given its already-resolved
    ``EvidenceLocator`` list (``_resolve_candidate_locators`` -- both citation channels
    combined; a line-indexed-text reference has already become a real ``LineRange`` +
    code-derived ``quoted_text`` by this point, never the model's own transcription).

    ``kind``/``id``/``produced_by`` are assigned here, by code -- ``RawHypothesisCandidate``
    has no such fields to read them from in the first place. ``id`` is derived from
    ``{produced_by, evidence_ids, confidence}`` only -- deliberately *not* from ``statement``
    text, unlike ``rules.base.make_finding_id``. An LLM's prose is not guaranteed
    byte-identical across calls even at temperature 0, so hashing it would make ``id`` look
    like a stable, content-addressed identifier while actually being arbitrary. What *is*
    reproducible and is hashed: which model produced the hypothesis, over exactly which
    evidence, at what confidence (docs/phase-0.11-ai-hypothesis-layer-design.md §3.3).
    """
    evidence_ids = sorted({locator.evidence_id for locator in resolved_locators})
    digest = content_hash(
        {
            "produced_by": model_identifier,
            "evidence_ids": evidence_ids,
            "confidence": candidate.confidence.value,
        }
    )
    return Finding(
        id=f"finding:{digest}",
        kind=FindingKind.AI_HYPOTHESIS,
        statement=candidate.statement,
        evidence_locators=resolved_locators,
        produced_by=model_identifier,
        limitation=candidate.limitation,
        confidence=candidate.confidence,
        contradicts=candidate.contradicts,
    )


# HIGH ranks first, LOW last -- the sort is stable, so hypotheses within the same confidence
# tier keep the model's own returned order (docs/phase-0.11-ai-hypothesis-layer-design.md §3.4
# "ranking" row). No separate numeric score is introduced.
_CONFIDENCE_RANK = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}


def _check_confidence_requires_deterministic_grounding(
    finding: Finding, findings: list[Finding]
) -> None:
    """A HIGH-confidence AI_HYPOTHESIS requires at least one DETERMINISTIC_FINDING already
    established for this investigation. A live evaluation showed the model producing
    HIGH-confidence causal claims from a single piece of generic evidence precisely in the
    scenarios where the deterministic rule bank itself found nothing definite -- a mechanically
    valid citation (real evidence, correctly quoted) is not the same as a substantively
    sufficient one, and this is a structural proxy for that distinction, not a judgment of the
    hypothesis's own wording. See errors.UngroundedConfidenceError.
    """
    if finding.confidence != Confidence.HIGH:
        return
    if not any(f.kind == FindingKind.DETERMINISTIC_FINDING for f in findings):
        raise UngroundedConfidenceError(finding.id)


def _check_contradiction_acknowledged(
    finding: Finding, contradictions: list[ContradictionEdge]
) -> None:
    """A hypothesis citing evidence on either side of a recorded contradiction must carry a
    non-empty 'limitation' -- checked by evidence_id membership only, never by what the
    hypothesis's prose says, so this generalizes to any future contradiction rather than
    recognizing specific wording. See errors.UnacknowledgedContradictionError.
    """
    if not contradictions:
        return
    contested_ids = {edge.evidence_a for edge in contradictions} | {
        edge.evidence_b for edge in contradictions
    }
    if set(finding.evidence_ids) & contested_ids and not finding.limitation:
        raise UnacknowledgedContradictionError(finding.id)


def _check_quote_not_a_finding_statement(finding: Finding, findings: list[Finding]) -> None:
    """A diagnostic-clarity check, not a new acceptance rule: if a locator's quoted_text is
    exactly some existing Finding's own rendered statement, flag that specifically rather
    than letting it fall through to a generic quote-mismatch error. Such a quote was already
    going to fail validate_finding_references's real content check -- a Finding's statement
    is rendered prose, essentially never identical to a raw evidence field value -- so this
    changes nothing about which candidates are accepted; it only names the mistake precisely.
    A structural equality comparison against known statements, not a judgment of what the
    hypothesis's own reasoning says. See errors.FindingStatementCitedAsEvidenceError.
    """
    statements = {f.statement for f in findings}
    for locator in finding.evidence_locators:
        if locator.quoted_text in statements:
            raise FindingStatementCitedAsEvidenceError(finding.id, locator.evidence_id, locator.quoted_text)


def _dedup_key(finding: Finding) -> tuple[str, frozenset[str]]:
    """Exact-duplicate key: the same model call citing the exact same evidence set.

    Deliberately narrow -- two differently-worded hypotheses making the same underlying claim
    (a near-duplicate, not an exact one) are NOT collapsed here; semantic deduplication is an
    out-of-scope, measured-not-solved evaluation dimension
    (docs/phase-0.11-ai-hypothesis-layer-design.md §3.4, §5).
    """
    return (finding.produced_by, frozenset(finding.evidence_ids))


def generate_ai_hypotheses(
    bundle: EvidenceBundle,
    findings: list[Finding],
    client: HypothesisModelClient,
    *,
    model_identifier: str,
    contradictions: list[ContradictionEdge] | None = None,
) -> list[Finding]:
    """The full, opt-in orchestration: build input -> one model call -> parse -> promote ->
    validate -> dedup -> sort.

    ``contradictions`` is ``evaluate_bundle``'s own second return value -- pass it through so
    the model is actually shown what it is expected to acknowledge (see
    ``model_input.build_model_input``'s docstring). Omitting it is equivalent to no recorded
    contradictions, not an error.

    A candidate that fails reference resolution (``_resolve_candidate_locators`` -- an unknown,
    cross-item, duplicated, or non-contiguous ``line_citations`` selection), ``Finding``
    construction (e.g. no evidence_locators at all), ``validate_finding_references`` (e.g. an
    unknown evidence id, an out-of-bounds pointer, a stale quote), quotes a Finding's own
    rendered statement instead of real evidence content
    (``_check_quote_not_a_finding_statement``), a HIGH confidence claim with no deterministic
    grounding (``_check_confidence_requires_deterministic_grounding``), or citing contested
    evidence with no acknowledgement (``_check_contradiction_acknowledged``) is dropped
    silently -- rejected, not corrected and not surfaced, exactly like a rule-engine bug is
    never allowed to reach a report. An empty return value is this layer's abstention
    representation: a legitimate "the model produced no hypothesis meeting the bar" result,
    not an error.

    Raises ``HypothesisParseError`` if the model's response as a whole could not be understood
    at all -- deliberately not caught here; see that class's docstring for why.
    """
    contradictions = contradictions or []
    model_input = build_model_input(bundle, findings, contradictions=contradictions)
    raw_response = client.generate(model_input)
    candidates = parse_candidates(raw_response)
    catalog = _build_ref_catalog(model_input)

    accepted: list[Finding] = []
    seen: set[tuple[str, frozenset[str]]] = set()
    for candidate in candidates:
        try:
            resolved_locators = _resolve_candidate_locators(candidate, catalog)
            finding = make_ai_hypothesis_finding(candidate, model_identifier, resolved_locators)
            # Checked before the generic content-match gate so this specific, diagnosable
            # mistake is reported precisely rather than falling through to a generic
            # mismatch -- it would fail that gate anyway (see the function's own docstring),
            # so this changes nothing about what is accepted, only why a rejection is named.
            _check_quote_not_a_finding_statement(finding, findings)
            validate_finding_references(finding, bundle)
            _check_confidence_requires_deterministic_grounding(finding, findings)
            _check_contradiction_acknowledged(finding, contradictions)
        except (ValueError, InvestigatorError):
            continue

        key = _dedup_key(finding)
        if key in seen:
            continue
        seen.add(key)
        accepted.append(finding)

    accepted.sort(key=lambda f: _CONFIDENCE_RANK[f.confidence])
    return accepted
