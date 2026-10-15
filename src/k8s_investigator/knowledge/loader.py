"""Load and validate a knowledge pack from disk (Phase 0.18).

Everything this module reads came from the user's own filesystem, not this project's code --
loaded only when explicitly requested via ``--knowledge PATH`` (see ``cli.py``), never
discovered or read implicitly from the current directory or any other location this project
did not name. No network access of any kind is involved in loading a pack or a runbook.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import MAX_PACK_FILE_BYTES, KnowledgePack


class KnowledgePackError(Exception):
    """A knowledge pack failed to load or validate. The message is always specific and
    actionable (which rule, which field, which path) and never includes a traceback or a
    silently-swallowed cause -- consistent with this project's existing error-reporting
    conventions (see ``errors.py``)."""


def load_knowledge_pack(path: str | Path) -> tuple[KnowledgePack, Path]:
    """Returns ``(pack, pack_dir)`` -- ``pack_dir`` is the resolved, symlink-followed directory
    the pack file itself lives in: the one and only root any referenced runbook path may ever
    resolve inside of. Raises ``KnowledgePackError`` for every failure mode: a missing file, an
    oversized file, invalid YAML, a non-mapping top level, schema validation failure, or an
    unsafe/missing runbook reference.
    """
    pack_path = Path(path)
    if not pack_path.is_file():
        raise KnowledgePackError(f"knowledge pack not found: {pack_path}")

    try:
        size = pack_path.stat().st_size
    except OSError as exc:
        raise KnowledgePackError(f"could not stat knowledge pack {pack_path}: {type(exc).__name__}") from exc
    if size > MAX_PACK_FILE_BYTES:
        raise KnowledgePackError(
            f"knowledge pack {pack_path} is {size} bytes, exceeding the {MAX_PACK_FILE_BYTES}-byte limit"
        )

    try:
        raw_text = pack_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise KnowledgePackError(f"could not read knowledge pack {pack_path}: {type(exc).__name__}") from exc
    except UnicodeDecodeError as exc:
        raise KnowledgePackError(f"knowledge pack {pack_path} is not valid UTF-8 text") from exc

    try:
        raw_data = yaml.safe_load(raw_text)  # safe_load: never constructs arbitrary Python
        # objects or executes tags -- the one YAML-loading function this project ever uses
    except yaml.YAMLError as exc:
        raise KnowledgePackError(f"knowledge pack {pack_path} is not valid YAML: {exc}") from exc

    if not isinstance(raw_data, dict):
        raise KnowledgePackError(
            f"knowledge pack {pack_path} must be a YAML mapping at the top level "
            f"(got {type(raw_data).__name__})"
        )

    try:
        pack = KnowledgePack.model_validate(raw_data)
    except ValidationError as exc:
        raise KnowledgePackError(f"knowledge pack {pack_path} failed schema validation: {exc}") from exc

    pack_dir = pack_path.resolve().parent
    for rule in pack.rules:
        if rule.guidance.runbook is not None:
            _resolve_runbook_path(rule.id, rule.guidance.runbook, pack_dir)

    return pack, pack_dir


def resolve_runbook_path(runbook: str, pack_dir: Path) -> Path:
    """Public wrapper for ``matcher.py``/``cli.py`` to re-resolve an already-validated
    runbook path for display/reading -- re-runs the exact same safety check as load time
    (cheap, and defends against the file being swapped out between load and use)."""
    return _resolve_runbook_path("<reresolve>", runbook, pack_dir)


def _resolve_runbook_path(rule_id: str, runbook: str, pack_dir: Path) -> Path:
    """Resolve a declared runbook path relative to the pack's own directory. Rejects an
    absolute path, a path that escapes that directory via '..' traversal, and a path that
    escapes it via a symlink -- ``Path.resolve()`` follows symlinks to their real target
    before the containment check runs, so a symlink pointing outside ``pack_dir`` is caught
    the same way literal traversal is. A missing runbook is a validation error, not a silent
    omission (Phase 0.18 safety requirement)."""
    declared = Path(runbook)
    if declared.is_absolute():
        raise KnowledgePackError(
            f"rule {rule_id!r}: runbook path {runbook!r} must be relative to the knowledge "
            "pack's own directory, not absolute"
        )

    candidate = (pack_dir / declared).resolve()
    try:
        candidate.relative_to(pack_dir)
    except ValueError:
        raise KnowledgePackError(
            f"rule {rule_id!r}: runbook path {runbook!r} resolves outside the knowledge "
            f"pack's own directory ({pack_dir}) -- rejected"
        ) from None

    if not candidate.is_file():
        raise KnowledgePackError(f"rule {rule_id!r}: runbook {runbook!r} does not exist at {candidate}")
    return candidate
