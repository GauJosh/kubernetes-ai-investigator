"""Build a Kubernetes label-selector query string from a selector spec (e.g. a ReplicaSet's
own ``spec.selector``).

Used only to narrow a LIST *query* -- per
docs/research/read-only-access-matrix.md §8.2, this is never an RBAC or security boundary.
Supports ``matchLabels`` (equality) and the four standard ``matchExpressions`` operators
(``In``, ``NotIn``, ``Exists``, ``DoesNotExist``) -- the complete, standard
LabelSelectorRequirement operator set, not a narrowed subset.
"""

from __future__ import annotations

from typing import Any


def build_label_selector_string(selector_spec: dict[str, Any] | None) -> str | None:
    """Returns None when the selector has nothing usable to query by -- callers must treat
    that as "no selector available," not as "match everything.\""""
    if not selector_spec:
        return None

    clauses: list[str] = []

    for key, value in (selector_spec.get("matchLabels") or {}).items():
        clauses.append(f"{key}={value}")

    for expr in selector_spec.get("matchExpressions") or []:
        key = expr.get("key")
        operator = expr.get("operator")
        values = expr.get("values") or []
        if not key or not operator:
            continue
        if operator == "In" and values:
            clauses.append(f"{key} in ({','.join(values)})")
        elif operator == "NotIn" and values:
            clauses.append(f"{key} notin ({','.join(values)})")
        elif operator == "Exists":
            clauses.append(key)
        elif operator == "DoesNotExist":
            clauses.append(f"!{key}")
        # An unrecognized operator is skipped, not guessed at -- a selector built from a
        # partially-understood spec would be worse than no selector at all.

    if not clauses:
        return None
    return ",".join(clauses)
