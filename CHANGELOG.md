# Changelog

All notable changes to this project are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project does not yet follow strict Semantic Versioning pre-1.0 (see `pyproject.toml`'s
`version`); breaking changes are still possible between minor releases until 1.0.

## [Unreleased]

- Public open-source release preparation: CI (GitHub Actions, Linux + Windows, Python
  3.10-3.14), CodeQL scanning, dependency review on PRs, Dependabot, Apache-2.0 license,
  CONTRIBUTING.md/SECURITY.md, and a published subset of the pre-implementation research
  record under `docs/research/`.

## [0.1.0] - initial public history

The MVP and its subsequent increments, in rough chronological order:

### Added
- Evidence-first, read-only investigation core: `kai pod <name> -n <namespace>`, a bounded
  Kubernetes collector, a content-hashed `EvidenceBundle`, and `InvestigationReport` rendering
  (text and JSON).
- Deterministic rules R1-R4 (Startup-fault: image-pull DNS failure, tag/reference not found,
  registry authorization failure, bounded log-assisted crash diagnosis).
- Deterministic rules R5-R10 (Scheduling-fault: node cordon, resource capacity, taint/
  toleration, node selector/affinity, pod anti-affinity confirmed against real sibling-Pod
  placement, unbound PersistentVolumeClaim).
- R11 (Runtime-health-fault: liveness/readiness probe failures).
- An engine-level, bank-wide `engine:no_rule_matched` observed fact for failures outside every
  known category, so an unrecognized pattern is reported honestly instead of staying silent.
- Optional AI-hypothesis layer (`--with-ai`, off by default): model input projection, citation-
  reference-based evidence grounding, confidence-vs-grounding validation, redaction, and an
  OpenAI adapter (`pip install -e ".[ai]"`). Transparent outcome reporting
  (`ai_hypothesis_status`/`ai_hypothesis_outcome`/`ai_hypothesis_rejection_reasons`) so a
  SUCCEEDED-but-empty run is never confused with a failed one.
- Safe-by-default evidence bundle export (`--save-bundle`), sanitized unless
  `--allow-unsanitized-bundle-export` is explicitly passed.
- Offline replay (`--from-bundle`) against an already-saved or committed synthetic bundle, with
  no cluster access at all.
- `kubectl kai` plugin integration (`integrations/kubectl/kubectl-kai`), a thin shell script
  forwarding to the already-installed `kai`/`investigate` console scripts.
- Custom knowledge packs (`--knowledge PATH`): a versioned YAML schema (3 deterministic match
  operators -- `evidence_contains`, `event_reason`, `container_waiting_reason`) and optional
  Markdown runbooks, letting a team extend `kai` with its own operational guidance in plain
  YAML/Markdown, no Python and no AI required. Ships with a small example pack at
  `.kai/knowledge.yaml`.
- `investigate` console script retained as a trivial, backward-compatible alias for `kai`;
  `--with-ai-hypotheses` retained as a deprecated alias for `--with-ai`.

### Changed
- CLI flattened from `investigate investigate pod <name> ...` to `kai pod <name> ...`.
