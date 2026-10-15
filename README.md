# Kubernetes AI Investigator

An evidence-first, read-only Kubernetes investigation engine. It collects a bounded, frozen
snapshot of a Pod's evidence, correlates it deterministically, and produces a report whose
findings are always traceable to specific, validated evidence — no AI model is required for any
of this.

**Current scope: Milestone 2 (complete, including the R9 follow-up).** Rules R1–R4 diagnose
Startup-fault–category failures (image pull: DNS failure, tag/reference not found, registry
authorization failure; and a bounded, log-assisted crash-diagnosis rule with an explicit
"insufficient evidence" outcome) from namespace-scoped evidence only. Rules R5–R10 diagnose
Scheduling-fault–category failures (node cordon, resource capacity, taint/toleration, node
selector/affinity, pod anti-affinity — now confirmed against real sibling-Pod placement, not
just the scheduler's message — unbound PersistentVolumeClaim) for Pods that are `Pending` and
unscheduled, using `Node`/`PersistentVolume`/`PersistentVolumeClaim`/sibling-`Pod` evidence
collected **only** when that scheduling-blocked condition (and, for siblings, a usable
ReplicaSet selector) is already visible on the Pod's own evidence. See
`docs/phase-0.8-mvp-blueprint.md` for the approved architecture, `docs/phase-0.9-milestone-1-notes.md`
for Milestone 1, and `docs/phase-0.10-milestone-2-notes.md` for Milestone 2 and the R9 follow-up.

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
`EvidenceBundle` JSON file — either one `investigate` saved earlier (`--save-bundle`, on a live
collection only — see below), or the real, committed example bundle at
`tests/fixtures/bundles/r1_dns_failure_bundle.json` (a small, synthetic DNS-image-pull-failure
case; nothing in it was collected from a real cluster, and it contains no credentials of any
kind). This exact command works from a fresh clone with no cluster access, no AI provider
configured, and no internal test helper imported — verified by
`tests/test_cli.py::test_cli_documented_offline_example_against_the_committed_bundle`:

```bash
investigate investigate pod demo-pod --namespace demo-ns \
    --from-bundle tests/fixtures/bundles/r1_dns_failure_bundle.json
```

(The repeated `investigate investigate` is intentional: `investigate` is the installed command;
`investigate` is also the CLI's own subcommand name, matching the `investigate pod/<name>`
phrasing used throughout this project's design documents. `demo-pod`/`demo-ns` only need to
match this particular bundle's own recorded target for the report to describe itself
accurately — `--from-bundle` loads the bundle exactly as saved and does not re-collect, so these
two values are not otherwise used to filter or re-fetch anything.)

This should print a report whose `DETERMINISTIC FINDINGS` section includes a `rule:R1_dns_failure`
finding, and whose `EVIDENCE GAPS` section honestly shows the one piece of evidence this
scenario's collection did not find (the container's logs).

A hand-built fixture is also supported the same way — see `tests/fixtures/scenarios.py` for how
the committed example above was produced, if you want to build your own for a different
scenario.

Add `--output json`, `--output text` (default), or `--output both`; add `--out PATH` to write to
a file instead of stdout. `--save-bundle` and `--from-bundle` cannot be combined (there is
nothing freshly collected to save when loading an already-saved bundle); the CLI rejects that
combination immediately with a usage error rather than silently doing nothing.

### Live collection (requires a cluster and RBAC — not exercised by this project's own tests)

```bash
investigate investigate pod my-pod --namespace my-ns \
    --kubeconfig ~/.kube/config --context my-context \
    --save-bundle my-investigation-bundle.json
```

Kubeconfig discovery follows the official Kubernetes client's normal resolution (in-cluster
config first, then the standard kubeconfig file/context lookup) — no project-specific override.

**`--save-bundle` writes a *sanitized* bundle by default** (Phase 0.16). A live cluster can
return genuinely sensitive content that isn't a Kubernetes `Secret` at all — e.g. a plaintext
database connection string sitting in a Pod's own `env` entry, exactly as this project's own
live evaluation against a real Minikube workload found. Before writing, the collected bundle is
passed through `export_sanitization.sanitize_bundle_for_export`, which reuses the same detectors
the AI-hypothesis layer already uses on its model input (`ai/redaction.py`: URL-userinfo
credentials, `password=`/`token=`/`secret=`-style assignments in free text, and sensitive
env-var names) — applied here to every item's payload *and* every raw per-call payload in the
bundle's `queries` field, not just the parts a Finding happens to cite. If you need the raw,
unsanitized bundle (e.g. for deep debugging), pass `--allow-unsanitized-bundle-export` alongside
`--save-bundle`; doing so prints a clear warning to stderr every time, and is only valid together
with `--save-bundle`. **This redaction is best-effort, not a security boundary** — see
Limitations below for exactly what it does and does not catch. **A sanitized bundle's id, and
the id of any item it changed, will differ from the raw bundle's** — ids in this project are
content hashes (`canonical.py`) that include the payload, so redacted content is, correctly,
different content. The sanitized bundle is still fully valid and loadable via `--from-bundle`;
replaying it re-runs the real deterministic rule engine, which never sees sanitized evidence in
its own evaluation path regardless of what gets written to disk.

**Live collection prerequisites**, per `docs/research/read-only-access-matrix.md` (approved):
namespace-scoped `get`+`list` on `Pod` (`list` is the R9-follow-up addition — §8 of that
document; the label selector it uses is a query filter only, never an RBAC boundary), `get` on
the `pods/log` subresource, `list` on `Event`, `get` on `ReplicaSet`, `get` on `Deployment`,
`get` on `ServiceAccount`, and `get` on `PersistentVolumeClaim`; plus cluster-scoped `list` on
`Node` and `PersistentVolume` (§7 — only `list` is ever called, `get` is approved but unused,
and both calls, along with the sibling-`Pod` list, are issued only when the investigated Pod is
already `Pending` and unscheduled). **No manifest for this grant is created by this project** —
creating the actual `Role`/`ClusterRole`/`RoleBinding`/`ServiceAccount` objects is a
deployment-time decision outside this project's scope, per the approved decision record.

**No live cluster was accessed to build, test, or verify this milestone.** Every test in this
repository runs against synthetic evidence bundles and a fake Kubernetes client — see Testing,
below.

## What this tool does not do (by design, this milestone)

- No write, mutation, `exec`, `attach`, `port-forward`, or eviction capability exists anywhere in
  this codebase.
- No `Secret` object is ever read. Image-pull-secret *references* (names only) are read from the
  Pod and ServiceAccount objects; their contents are never inspected.
- `Service`, `EndpointSlice`, `Namespace`, and `ResourceQuota` remain deferred — no evidence
  collected in this project's testing has needed them.
- The deterministic pipeline remains complete and useful entirely on its own; the AI-hypothesis
  layer is opt-in (`--with-ai-hypotheses`, off by default) and still never runs in this
  project's own test suite or by default. Phase 0.11 built the offline machinery
  (`src/k8s_investigator/ai/`: input projection, candidate parsing, evidence-reference
  validation, deduplication, confidence ranking) and two narrow `Finding` validator rules.
  Phase 0.12 added honest failure reporting (`ai_hypothesis_status`:
  `NOT_ATTEMPTED`/`SUCCEEDED`/`FAILED`, independent of whether any hypothesis was actually
  accepted), a real `HypothesisModelClient` adapter for the OpenAI API (an optional
  `pip install -e ".[ai]"` dependency), and a citation-reference mechanism for multiline log
  evidence (the model selects pre-issued, code-resolved references instead of constructing
  quotes itself). 267 tests pass offline (mocked clients only). A small number of live,
  read-only evaluation calls against real Minikube evidence have since been made (never
  modifying the cluster) and verified the layer's safety properties — citations are rejected
  without repair when invalid, and a successful API call is never confused with an accepted
  hypothesis — though no AI hypothesis has yet been confirmed reaching a final report through
  live testing. See `docs/phase-0.11-ai-hypothesis-layer-design.md` and
  `docs/phase-0.12-citation-reference-increment-notes.md` for the full record.
- No database, message broker, or web service — this is a CLI that reads evidence and writes
  files.

## Limitations, stated plainly

- The rule bank's substring-matching patterns (R1/R2/R3's event-text classes, R4's log-text
  classes, R5–R10's scheduling-event-text classes) are small and explicitly non-exhaustive —
  verified against real cases in `docs/research/cloud-opsbench-evidence-feasibility.md`, not
  claimed to cover every real-world phrasing. An unmatched case produces an honest "insufficient
  evidence" result, not a guess.
- R8's node-affinity matching covers only the `In` operator on the first required
  matchExpression; R6's resource-quantity parser is minimal (no exponent notation); R9 confirms
  that a selector-matching sibling occupies a schedulable node (consistent with the scheduler's
  reported conflict), but does not independently parse the Pod's own `podAntiAffinity` term to
  prove that specific term is what matched.
- Container logs are treated as potentially sensitive. Collection is bounded (tail lines and a
  time window). `--save-bundle` applies best-effort redaction by default (see Usage above) —
  but this is explicitly a secondary safeguard, never a security boundary, per
  `docs/research/read-only-access-matrix.md` §4. Stated limits of what it does **not** catch:
  bearer tokens or API keys with no recognizable `token=`/`key=`-style prefix, cloud
  access-key-ID formats, PEM/SSH private key blocks, high-entropy strings with no contextual
  marker, and a secret split across multiple log lines. **Rendered reports are a separate
  surface this redaction does not touch at all** — a report only ever contains text a rule
  actually quoted, and today no rule in this bank quotes an `env` value, but that is a property
  of the current rule bank, not something this redaction layer enforces. Do not assume a saved
  bundle or report — sanitized or not — is safe to share outside the same trust boundary as the
  cluster itself; review before sharing, always.
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
`docs/phase-0.9-milestone-1-notes.md` and `docs/phase-0.10-milestone-2-notes.md` for the actual
test run output from each implementation phase.

## Project layout

```
src/k8s_investigator/
  models/          typed Pydantic contracts (evidence, locators, findings, report)
  canonical.py     canonical serialization + SHA-256 content-identity hashing
  validation.py    evidence-reference and bundle-integrity validation
  timeline.py      event-timeline interpretation (TTL/expiry flagging, timestamp tagging)
  pod_status.py    the scheduling-blocked predicate shared by the collector and rule engine
  selectors.py     builds a label-selector query string from a ReplicaSet's own spec.selector
                   (a query filter only -- never an RBAC boundary, see matrix §8.2)
  graph.py         structural relationship resolution (owner chain, ServiceAccount link,
                   Node/PersistentVolume/PersistentVolumeClaim/sibling-Pod classification)
  rules/
    engine.py        R1-R4 (Startup-fault) and wiring for R5-R10
    scheduling.py     R5-R10 (Scheduling-fault, including R9's sibling-Pod correlation)
    contradictions.py  the two contradiction detectors
    patterns.py        the named, non-exhaustive substring/quantity-parsing rule bank
  bundle_builder.py   collector output -> typed, content-hashed EvidenceBundle
  report_builder.py   validated findings -> InvestigationReport
  rendering.py     human-readable report rendering
  collector/       the narrow, ten-call-surface Kubernetes client and orchestration
  cli.py           `investigate pod <name> ...`
tests/             synthetic fixtures + a fake Kubernetes client; no live cluster
docs/              the full research, design, and decision record for this project
```

## Documentation

The complete research and design history lives in `docs/` — `docs/open-questions.md` tracks every
material decision's status (approved / recommended / open / deferred);
`docs/phase-0.8-mvp-blueprint.md` is the approved architecture; `docs/phase-0.9-milestone-1-notes.md`
and `docs/phase-0.10-milestone-2-notes.md` record what each milestone actually built, deviated
on, and tested; `docs/research/read-only-access-matrix.md` §7-§8 documents the exact Milestone 2
API calls and RBAC.
