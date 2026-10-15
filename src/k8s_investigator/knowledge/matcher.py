"""Evaluate an already-loaded, already-validated ``KnowledgePack`` against a real
``EvidenceBundle`` (Phase 0.18) -- deterministic, offline, no AI, no network.

Every match is grounded in real evidence via the exact same ``EvidenceLocator``/
``validate_finding_references`` machinery every other ``Finding`` in this project already
uses. A rule's match conditions are boolean existence checks against data already in the
bundle -- never executed code, never an expression language, never anything beyond the three
operators ``knowledge.models.RuleMatch`` declares.
"""

from __future__ import annotations

from ..models.evidence import EvidenceBundle, EvidenceItem, EvidenceStatus
from ..models.locators import EvidenceLocator
from ..models.report import CustomGuidanceMatch
from ..rules.base import field_locator, line_locator, make_finding
from ..models.findings import FindingKind
from .models import KnowledgePack, KnowledgeRule


def _textual_evidence_items(bundle: EvidenceBundle) -> list[EvidenceItem]:
    """The two free-text evidence sources this project already treats as the richest signal
    for substring search: a bounded container log (a bare string payload) or an Event's own
    ``message`` field -- never a structured bookkeeping field (UIDs, resourceVersions, Pod
    spec contents unrelated to the symptom), which would only produce noisy, uninterpretable
    matches."""
    items: list[EvidenceItem] = []
    for item in bundle.items:
        if item.status != EvidenceStatus.PRESENT:
            continue
        if isinstance(item.payload, str):
            items.append(item)
        elif (
            isinstance(item.payload, dict)
            and isinstance(item.payload.get("message"), str)
            and getattr(item.source_ref, "kind", None) == "Event"
        ):
            items.append(item)
    return items


def _match_evidence_contains(bundle: EvidenceBundle, substrings: list[str]) -> list[EvidenceLocator]:
    """One locator per textual item that contains at least one of the listed substrings
    (case-insensitive) -- for a log item, the locator points at the first matching line; for
    an Event, at its ``message`` field."""
    locators: list[EvidenceLocator] = []
    for item in _textual_evidence_items(bundle):
        is_log = isinstance(item.payload, str)
        text = item.payload if is_log else item.payload["message"]
        lowered = text.lower()
        if not any(s.lower() in lowered for s in substrings):
            continue
        if is_log:
            for i, line in enumerate(text.splitlines()):
                if any(s.lower() in line.lower() for s in substrings):
                    locators.append(line_locator(item, i, i))
                    break
        else:
            locators.append(field_locator(item, "message"))
    return locators


def _match_event_reason(bundle: EvidenceBundle, reason: str) -> list[EvidenceLocator]:
    locators: list[EvidenceLocator] = []
    for item in bundle.items:
        if item.status != EvidenceStatus.PRESENT:
            continue
        if getattr(item.source_ref, "kind", None) != "Event":
            continue
        if (item.payload or {}).get("reason") == reason:
            locators.append(field_locator(item, "reason"))
    return locators


def _match_container_waiting_reason(bundle: EvidenceBundle, reason: str) -> list[EvidenceLocator]:
    pod_item = next(
        (i for i in bundle.items if getattr(i.source_ref, "kind", None) == "Pod"), None
    )
    if pod_item is None or pod_item.status != EvidenceStatus.PRESENT:
        return []
    statuses = (pod_item.payload or {}).get("status", {}).get("containerStatuses") or []
    locators: list[EvidenceLocator] = []
    for idx, cs in enumerate(statuses):
        if ((cs.get("state") or {}).get("waiting") or {}).get("reason") == reason:
            locators.append(
                field_locator(pod_item, "status", "containerStatuses", idx, "state", "waiting", "reason")
            )
    return locators


def _dedup(locators: list[EvidenceLocator]) -> list[EvidenceLocator]:
    seen: set[tuple[str, str]] = set()
    out: list[EvidenceLocator] = []
    for loc in locators:
        key = (loc.evidence_id, loc.pointer.model_dump_json())
        if key not in seen:
            seen.add(key)
            out.append(loc)
    return out


def _evaluate_rule(rule: KnowledgeRule, bundle: EvidenceBundle) -> list[EvidenceLocator] | None:
    """All operators declared in one rule's ``match`` block must hold (AND across operators).
    Returns the combined, deduplicated locator list if the rule matches, or ``None`` if any
    declared operator found nothing."""
    collected: list[EvidenceLocator] = []

    if rule.match.evidence_contains is not None:
        found = _match_evidence_contains(bundle, rule.match.evidence_contains)
        if not found:
            return None
        collected.extend(found)

    if rule.match.event_reason is not None:
        found = _match_event_reason(bundle, rule.match.event_reason)
        if not found:
            return None
        collected.extend(found)

    if rule.match.container_waiting_reason is not None:
        found = _match_container_waiting_reason(bundle, rule.match.container_waiting_reason)
        if not found:
            return None
        collected.extend(found)

    return _dedup(collected)


def evaluate_knowledge_pack(pack: KnowledgePack, bundle: EvidenceBundle) -> list[CustomGuidanceMatch]:
    """Evaluate every rule in ``pack`` against ``bundle``. Deterministic: the same pack and
    bundle always produce the same matches (same finding ids, same locators) -- every helper
    here is a pure function of its arguments, same discipline as ``rules.engine``.
    """
    matches: list[CustomGuidanceMatch] = []
    for rule in pack.rules:
        locators = _evaluate_rule(rule, bundle)
        if locators is None:
            continue
        finding = make_finding(
            kind=FindingKind.CUSTOM_GUIDANCE,
            statement=rule.description,
            produced_by=f"knowledge:{rule.id}",
            locators=locators,
        )
        matches.append(
            CustomGuidanceMatch(
                finding=finding,
                summary=rule.guidance.summary,
                next_checks=list(rule.guidance.next_checks),
                runbook=rule.guidance.runbook,
            )
        )
    return matches
