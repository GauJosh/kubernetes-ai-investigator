"""Redaction for the AI-hypothesis layer's model input only.

This module never touches the original ``EvidenceBundle`` -- every function here takes a
payload (or a string) and returns a **new** value; nothing is mutated in place. It exists
specifically because ``ai.model_input.build_model_input`` is the one place in this project that
hands evidence to something outside the local trust boundary (a model API). The deterministic
pipeline, the real ``EvidenceBundle``, and ``validation.validate_finding_references`` are all
completely unaware this module exists and are unaffected by it.

Two independent, combined detectors (named, bounded, and deliberately NOT a general secret
scanner -- see the limitations note below, mirroring this project's existing "small, named,
non-exhaustive pattern bank" convention for R1-R4):

1. **Value-pattern detection** (``redact_string_value``) -- fires on the *content* of any
   string, regardless of field name: a URL with embedded ``user:pass@`` userinfo (covers
   database/connection-string DSNs), and ``password=``/``token=``/``secret=``-style key=value
   assignments anywhere in free text (covers log lines and event messages). This is what
   catches a credential whose field name gives no hint at all -- e.g. a DSN embedded directly
   in a log line, or sitting in an env var named something generic.
2. **Name-based detection** (inside ``redact_payload``) -- an env-entry-shaped dict
   (``{"name": ..., "value": ...}``) whose ``name`` matches a known sensitive-keyword list gets
   its entire ``value`` replaced, regardless of whether that value's *content* happens to match
   pattern (1). This catches a bare secret value (no URL shape, no "key=value" wording) that a
   value-only scan would miss.

Both run on every evidence payload independently of where it appears in the JSON tree, so the
exact same env var repeated in a Pod's own spec and in its owning ReplicaSet's/Deployment's pod
template (duplication this project's own live-cluster testing confirmed happens) is redacted
consistently in every copy -- there is no hardcoded path to a specific object kind.

**Explicitly not comprehensive.** This does not detect: bearer tokens or API keys with no
recognizable "token="/"key="-style prefix and no URL-userinfo shape, cloud-provider
access-key-ID formats, PEM/SSH private key blocks, high-entropy strings with no contextual
marker, or anything split across multiple log lines. Treat this the same way the project treats
log redaction generally (docs/research/read-only-access-matrix.md §4): a secondary safeguard,
never a security boundary. The actual security boundary is: don't put live credentials in a Pod
spec as plain text in the first place.
"""

from __future__ import annotations

import re
from typing import Any

# Category name -> compiled pattern. Each pattern's replacement preserves everything except the
# credential-shaped span itself, so surrounding diagnostic text survives redaction untouched.
_URL_USERINFO_PATTERN = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^/\s:@]+):(?P<pass>[^/\s@]+)@"
)
_KEYWORD_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?P<key>password|passwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"auth[_-]?token|token)\b(?P<sep>\s*[:=]\s*)(?P<value>\S+)"
)

# Env-var *name* keywords -- a secondary, non-exclusive signal (see module docstring). Matches
# the field's name, not its value; combined with the value-pattern detectors above so neither
# signal alone has to catch everything.
_SENSITIVE_ENV_NAME_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"dsn|conn(ection)?[_-]?str(ing)?|database[_-]?url|db[_-]?url|credential|auth)"
)

_URL_CATEGORY = "credential_url"
_KEYWORD_CATEGORY = "credential_assignment"
_ENV_NAME_CATEGORY = "sensitive_env_name"

_FULL_VALUE_PLACEHOLDER = "[REDACTED:sensitive-env-value]"


def redact_string_value(text: str) -> tuple[str, set[str]]:
    """Apply both value-pattern detectors to one string. Returns (possibly-modified text,
    categories that fired). Never touches text that doesn't match either pattern."""
    categories: set[str] = set()

    def _url_sub(match: re.Match) -> str:
        categories.add(_URL_CATEGORY)
        return f"{match.group('scheme')}***REDACTED***@"

    text = _URL_USERINFO_PATTERN.sub(_url_sub, text)

    def _keyword_sub(match: re.Match) -> str:
        categories.add(_KEYWORD_CATEGORY)
        return f"{match.group('key')}{match.group('sep')}[REDACTED]"

    text = _KEYWORD_ASSIGNMENT_PATTERN.sub(_keyword_sub, text)

    return text, categories


def _is_env_entry(node: dict) -> bool:
    return isinstance(node.get("name"), str) and isinstance(node.get("value"), str)


def redact_payload(payload: Any) -> tuple[Any, set[str]]:
    """Recursively rebuild ``payload`` with sensitive content replaced. Returns (new payload,
    categories that fired anywhere in it). ``payload`` itself is never mutated -- every dict
    and list encountered is rebuilt into a new one."""
    categories: set[str] = set()

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if _is_env_entry(node) and _SENSITIVE_ENV_NAME_PATTERN.search(node["name"]):
                categories.add(_ENV_NAME_CATEGORY)
                new_node = dict(node)
                new_node["value"] = _FULL_VALUE_PLACEHOLDER
                return new_node
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, str):
            redacted, hit = redact_string_value(node)
            categories.update(hit)
            return redacted
        return node

    return walk(payload), categories
