# Kubernetes AI Investigator

An evidence-first, read-only Kubernetes investigation engine. It collects a bounded, frozen
snapshot of a Pod's evidence, correlates it deterministically, and produces a report whose
findings are always traceable to specific, validated evidence — no AI model is required for any
of this.

**Current scope: Milestone 1.** Rules R1–R4 diagnose Startup-fault–category failures
(image pull: DNS failure, tag/reference not found, registry authorization failure; and a
bounded, log-assisted crash-diagnosis rule with an explicit "insufficient evidence" outcome) from
namespace-scoped evidence only. See `docs/phase-0.8-mvp-blueprint.md` for the full design and
`docs/phase-0.9-milestone-1-notes.md` for what was actually implemented and tested.

**This is a design-and-research project's first working slice, not a production tool.** It is
read-only by construction: no write, `exec`, `attach`, `port-forward`, or eviction call exists
anywhere in this codebase, and `Secret` objects are never read.

## Installation

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

This installs the `investigate` console script and the `k8s_investigator` package. Installing
the package does **not** require a Kubernetes cluster — the `kubernetes` client library is a
dependency, but nothing at import or install time contacts a cluster.

## Usage

### Offline, fixture-based (no cluster required)

This is the primary, fully-tested path for this milestone. Point the CLI at an already-saved
`EvidenceBundle` JSON file — either one `investigate` saved earlier (`--save-bundle`), or one
hand-built as a test fixture (see `tests/fixtures/`):

```bash
investigate investigate pod my-pod --namespace my-ns --from-bundle tests/fixtures/r1_dns_failure.json
```

(The repeated `investigate investigate` is intentional: `investigate` is the installed command;
`investigate` is also the CLI's own subcommand name, matching the `investigate pod/<name>`
phrasing used throughout this project's design documents.)

Add `--output json`, `--output text` (default), or `--output both`; add `--out PATH` to write to
a file instead of stdout.

### Live collection (requires a cluster and RBAC — not exercised by this project's own tests)

```bash
investigate investigate pod my-pod --namespace my-ns \
    --kubeconfig ~/.kube/config --context my-context \
    --save-bundle my-investigation-bundle.json
```

Kubeconfig discovery follows the official Kubernetes client's normal resolution (in-cluster
config first, then the standard kubeconfig file/context lookup) — no project-specific override.

**Live collection prerequisites**, per `docs/research/read-only-access-matrix.md` (approved,
Milestone 1 scope only): the credential used needs `get` on `Pod`, `get` on the `pods/log`
subresource, `list` on `Event`, `get` on `ReplicaSet`, `get` on `Deployment`, and `get` on
`ServiceAccount`, all namespace-scoped. **No manifest for this grant is created by this project**
— creating the actual `Role`/`RoleBinding`/`ServiceAccount` objects is a deployment-time decision
outside this milestone's scope, per the approved Phase 0.7/0.8/0.9 decision record.

**No live cluster was accessed to build, test, or verify this milestone.** Every test in this
repository runs against synthetic evidence bundles and a fake Kubernetes client — see Testing,
below.

## What this tool does not do (by design, this milestone)

- No write, mutation, `exec`, `attach`, `port-forward`, or eviction capability exists anywhere in
  this codebase.
- No `Secret` object is ever read. Image-pull-secret *references* (names only) are read from the
  Pod and ServiceAccount objects; their contents are never inspected.
- No `Node` or `PersistentVolume` access — Scheduling-fault rules (R5–R10) and the forced
  cluster-scoped grant they require are deferred to Milestone 2 (see `docs/open-questions.md` Q23).
- No AI/LLM call of any kind. The deterministic pipeline is complete and useful on its own; the
  AI-hypothesis layer is an unimplemented, typed interface only (`FindingKind.AI_HYPOTHESIS`
  exists in the schema so it will not require a breaking change later, but nothing produces it).
- No database, message broker, or web service — this is a CLI that reads evidence and writes
  files.

## Limitations, stated plainly

- The rule bank's substring-matching patterns (R1/R2/R3's event-text classes, R4's log-text
  classes) are small and explicitly non-exhaustive — verified against real cases in
  `docs/research/cloud-opsbench-evidence-feasibility.md`, not claimed to cover every real-world
  phrasing. An unmatched case produces an honest "insufficient evidence" result, not a guess.
- Container logs are treated as potentially sensitive. Collection is bounded (tail lines and a
  time window), but no automated redaction is applied — redaction is explicitly a secondary
  safeguard, never a security boundary, per `docs/research/read-only-access-matrix.md` §4. Do not
  assume a saved bundle or report is safe to share outside the same trust boundary as the cluster
  itself.
- Passing tests in this repository prove the application's own code only calls the approved API
  surface (`tests/test_api_surface.py`) and behaves correctly against synthetic evidence. They
  prove **nothing** about what RBAC grant is actually bound to a deployed ServiceAccount — that is
  a separate, later validation step (RBAC policy review, then live authorization testing), not
  performed by this milestone's test suite. See `docs/phase-0.8-mvp-blueprint.md` §H.
- `STALE` is a defined `EvidenceStatus` value that this milestone's single-pass collector never
  produces (there is nothing to compare against within one snapshot). It is exercised only by a
  hand-constructed fixture in the test suite, to prove the type and validator handle it correctly
  ahead of a future multi-pass design.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

All tests run against synthetic `EvidenceBundle` fixtures and a fake Kubernetes client
(`tests/fakes.py`) — none require a cluster, and none are skipped or marked pending. See
`docs/phase-0.9-milestone-1-notes.md` for the actual test run output from this implementation.

## Project layout

```
src/k8s_investigator/
  models/        typed Pydantic contracts (evidence, locators, findings, report)
  canonical.py   canonical serialization + SHA-256 content-identity hashing
  validation.py  evidence-reference and bundle-integrity validation
  timeline.py    event-timeline interpretation (TTL/expiry flagging, timestamp tagging)
  graph.py       structural relationship resolution (owner chain, ServiceAccount link)
  rules/         the deterministic rule bank (R1-R4) and the one contradiction detector
  bundle_builder.py   collector output -> typed, content-hashed EvidenceBundle
  report_builder.py   validated findings -> InvestigationReport
  rendering.py   human-readable report rendering
  collector/     the narrow, six-call-surface Kubernetes client and orchestration
  cli.py         `investigate pod <name> ...`
tests/           synthetic fixtures + a fake Kubernetes client; no live cluster
docs/            the full research, design, and decision record for this project
```

## Documentation

The complete research and design history lives in `docs/` — `docs/open-questions.md` tracks every
material decision's status (approved / recommended / open / deferred);
`docs/phase-0.8-mvp-blueprint.md` is the approved architecture; `docs/phase-0.9-milestone-1-notes.md`
records what Milestone 1 actually built, deviated on, and tested.
