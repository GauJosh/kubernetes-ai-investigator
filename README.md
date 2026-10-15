# Kubernetes AI Investigator

An evidence-first, read-only Kubernetes investigation engine. It collects a bounded, frozen
snapshot of a Pod's evidence, correlates it deterministically, and produces a report whose
findings are always traceable to specific, validated evidence — no AI model is required for any
of this.

**Current scope: 11 deterministic rules across three fault categories, plus optional AI
hypotheses.** Rules R1–R4 diagnose Startup-fault failures (image pull: DNS failure, tag/reference
not found, registry authorization failure; and a bounded, log-assisted crash-diagnosis rule with
an explicit "insufficient evidence" outcome). Rules R5–R10 diagnose Scheduling-fault failures
(node cordon, resource capacity, taint/toleration, node selector/affinity, pod anti-affinity —
confirmed against real sibling-Pod placement, not just the scheduler's message — unbound
PersistentVolumeClaim) for Pods that are `Pending` and unscheduled. R11 diagnoses liveness/
readiness probe failures, correlated with the container's actual status. AI hypotheses
(`--with-ai-hypotheses`) are entirely optional, off by default, and never required to get a
useful report. See `docs/phase-0.8-mvp-blueprint.md` for the approved architecture and
`docs/phase-0.9-milestone-1-notes.md`/`docs/phase-0.10-milestone-2-notes.md`/
`docs/phase-0.13-probe-failure-rule-design.md` for what each milestone built.

**This is read-only by construction**: no write, `exec`, `attach`, `port-forward`, or eviction
call exists anywhere in this codebase, and `Secret` objects are never read. Saved evidence
bundles are sanitized by default before being written to disk.

## Installation

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

This installs the `kai` console script (and `investigate`, kept only as a trivial,
backward-compatible alias for the same command) plus the `k8s_investigator` package. Installing
the package does **not** require a Kubernetes cluster — the `kubernetes` client library is a
dependency, but nothing at import or install time contacts a cluster.

## Quick start

A copy-paste walkthrough of the full workflow — install, discover the commands, run a real
investigation, optionally add AI, export safely, and reproduce offline. Steps 5 and 6 (offline
replay) are exactly what this project's own automated tests run and assert on
(`tests/test_cli.py::test_cli_documented_offline_example_against_the_committed_bundle`); steps 3
and 4 show the live-collection command shape this project's CLI implements and its
live-collection code paths are unit-tested with a fake client, but — consistent with "no live
cluster was accessed to build, test, or verify this project" elsewhere in this document —
running them against a *real* cluster is not something this project's own test suite can
exercise.

```bash
# 1. Install (see Installation above)
pip install -e ".[dev]"

# 2. Discover what the CLI can do
kai --help
kai pod --help

# 3. Investigate a real cluster Pod, saving a sanitized evidence bundle and a report
kai pod my-pod -n my-ns \
    --kubeconfig ~/.kube/config --context my-context \
    --save-bundle my-investigation-bundle.json \
    --out my-investigation-report.txt

# 4. (Optional) re-run the same investigation with AI hypotheses enabled -- requires
#    `pip install -e ".[ai]"` plus AI_HYPOTHESIS_PROVIDER=openai, OPENAI_API_KEY, and
#    AI_HYPOTHESIS_MODEL in the environment (see "Enabling AI hypotheses" below); omitting
#    this step is a complete, useful workflow on its own.
export AI_HYPOTHESIS_PROVIDER=openai
export OPENAI_API_KEY=sk-...
export AI_HYPOTHESIS_MODEL=gpt-4o-mini
kai pod my-pod -n my-ns \
    --kubeconfig ~/.kube/config --context my-context \
    --with-ai-hypotheses

# 5. Reproduce the investigation entirely offline from the bundle saved in step 3 -- no
#    cluster access, no AI provider, same deterministic findings every time
kai pod my-pod -n my-ns --from-bundle my-investigation-bundle.json

# 6. Don't have a live cluster handy? Try the same offline step against the committed example
#    bundle (step 5's command, pointed at a real, synthetic, credential-free fixture instead):
kai pod demo-pod -n demo-ns \
    --from-bundle tests/fixtures/bundles/r1_dns_failure_bundle.json
```

What to expect: step 3/6 print a report with `DETERMINISTIC FINDINGS`, `OBSERVED FACTS`,
`EVIDENCE GAPS`, and `NEXT STEPS` sections — every finding cites the exact evidence it came from.
If something in your cluster isn't covered by a rule, you'll see an explicit "insufficient
evidence" result, never a silent guess. If a genuine usage/collection problem occurs (a bad
path, a cluster permission problem), the CLI prints a one-line `error:` message and exits
non-zero; if only the optional AI step is misconfigured (step 4), it prints a `warning:` and
still exits `0` with the full deterministic report intact — see "Understanding failures" below.

## Usage

### Offline, fixture-based (no cluster required)

This is the primary, fully-tested path for this milestone. Point the CLI at an already-saved
`EvidenceBundle` JSON file — either one `kai` saved earlier (`--save-bundle`, on a live
collection only — see below), or the real, committed example bundle at
`tests/fixtures/bundles/r1_dns_failure_bundle.json` (a small, synthetic DNS-image-pull-failure
case; nothing in it was collected from a real cluster, and it contains no credentials of any
kind). This exact command works from a fresh clone with no cluster access, no AI provider
configured, and no internal test helper imported — verified by
`tests/test_cli.py::test_cli_documented_offline_example_against_the_committed_bundle`:

```bash
kai pod demo-pod --namespace demo-ns \
    --from-bundle tests/fixtures/bundles/r1_dns_failure_bundle.json
```

(`demo-pod`/`demo-ns` only need to match this particular bundle's own recorded target for the
report to describe itself accurately — `--from-bundle` loads the bundle exactly as saved and
does not re-collect, so these two values are not otherwise used to filter or re-fetch anything.)

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
kai pod my-pod --namespace my-ns \
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

### Enabling AI hypotheses (optional)

`--with-ai-hypotheses` is off by default and never required — the deterministic findings above
are already a complete, useful report on their own. To turn it on, install the optional `openai`
dependency and set three environment variables before running `kai` (only the `openai` provider
is implemented so far):

```bash
pip install -e ".[ai]"   # or add [dev,ai] to the Installation step above

export AI_HYPOTHESIS_PROVIDER=openai
export OPENAI_API_KEY=sk-...          # your own OpenAI API key -- never printed or logged
export AI_HYPOTHESIS_MODEL=gpt-4o-mini # any OpenAI chat model that supports structured output

kai pod my-pod --namespace my-ns --with-ai-hypotheses \
    --kubeconfig ~/.kube/config --context my-context
```

If any of the three variables is missing, or the provider name isn't recognized, the CLI
reports `ai_hypothesis_status: FAILED` with a specific, actionable message and still ships the
full deterministic report — a misconfigured AI layer never blocks or hides a real
investigation. A hypothesis is only ever accepted into the report if its citations pass the
same evidence-validation every deterministic finding passes; an invalid or unsupported claim is
rejected outright, never silently repaired. One call is made per investigation, never retried
automatically.

### Understanding failures

Every error the CLI can report is a single, specific line starting `error:` (a problem with
*what you asked for* — exit code `2` for a usage conflict like combining `--save-bundle` with
`--from-bundle`, `1` for a collection failure like a missing bundle file or an unreachable
cluster, `3` for an internal validation failure, `4` for a bundle-export failure) or `warning:`
(something degraded gracefully and the rest of the report still shipped, e.g. AI hypothesis
generation failing). None of these messages ever include a credential, a raw evidence payload,
or (for an AI-path failure) a rejected candidate's own text — only enough detail to fix the
problem. If a deterministic rule simply doesn't recognize your evidence, that is not an error at
all: the report says so explicitly (an `OBSERVED_FACT` naming exactly what was and wasn't
checked), rather than guessing at a cause or staying silent.

## kubectl plugin integration (optional)

`kai` works standalone and needs nothing in this section to be useful. If you'd rather reach
for it the same way you reach for any other `kubectl` subcommand, `integrations/kubectl/kubectl-kai`
makes that possible — a single, ~30-line shell script, not a plugin framework. kubectl discovers
plugins natively: any executable file on your `PATH` named `kubectl-<name>` becomes runnable as
`kubectl <name>`, with every argument forwarded verbatim — no registration, no manifest. This
script does exactly one thing: it `exec`s the already-installed `kai` (falling back to
`investigate`) with the exact same arguments, unchanged.

**Requires:** a POSIX shell (`bash`) to *run the plugin form* — on Windows, use WSL or Git Bash
for `kubectl kai ...`, or simply call `kai`/`investigate` directly, which needs no shell script
at all and already works natively there. `kai` itself must already be installed (see
Installation above); the plugin does nothing on its own.

**Install:**

```bash
cp integrations/kubectl/kubectl-kai /usr/local/bin/kubectl-kai
chmod +x /usr/local/bin/kubectl-kai
kubectl plugin list   # should now list kubectl-kai
```

**End-to-end example** (identical to calling `kai` directly, just through `kubectl`):

```bash
kubectl kai pod rag-service -n devops-genai
kubectl kai pod my-pod -n my-ns --from-bundle saved-bundle.json
```

This also composes naturally with an existing diagnostic shell pipeline — e.g. investigate
every non-`Running` Pod in a namespace:

```bash
kubectl get pods -n my-ns --field-selector=status.phase!=Running -o name \
  | sed 's#pod/##' \
  | xargs -I{} kubectl kai pod {} -n my-ns
```

Everything this project guarantees elsewhere still holds through the plugin: read-only
collection, AI only runs if you pass `--with-ai-hypotheses` yourself, `--save-bundle` is
sanitized by default, and `--from-bundle` replays fully offline — the plugin changes none of
that, it only changes how you spell the command. See `tests/test_kubectl_plugin.py` for the
automated tests covering argument forwarding, namespace/Pod-name handling, the AI-not-invoked
guarantee, and the error message when `kai` isn't installed.

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
  quotes itself) — all tested offline with mocked clients (see Testing below for the current
  total). A small number of live, read-only evaluation calls against real Minikube evidence
  have since been made (never
  modifying the cluster) and verified the layer's safety properties — citations are rejected
  without repair when invalid, and a successful API call is never confused with an accepted
  hypothesis — though no AI hypothesis has yet been confirmed reaching a final report through
  live testing. A live call that completes successfully but ends with zero accepted hypotheses
  used to be a single, ambiguous `SUCCEEDED` with an empty list — Phase 0.16 added
  `ai_hypothesis_outcome` (`ABSTAINED`/`ACCEPTED`/`PARTIAL_ACCEPTANCE`/`ALL_REJECTED`, set only
  alongside `SUCCEEDED`) and a bounded `ai_hypothesis_rejection_reasons` list (e.g.
  `INVALID_CITATION_REFERENCE`, `EVIDENCE_VALIDATION_FAILED`, `UNGROUNDED_CONFIDENCE` — never
  raw candidate text or evidence content) so a report can finally say *why* nothing was
  accepted, without changing what gets accepted or rejected. Both fields default to
  `None`/`[]` and are absent from any report saved before this change — a report that predates
  them still parses and renders exactly as it always has. See
  `docs/phase-0.11-ai-hypothesis-layer-design.md` and
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
(`tests/fakes.py`) — none require a cluster, and none are skipped or marked pending. 342 tests
pass as of this entry (no live Kubernetes access, no paid model call in that count). See
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
    engine.py        R1-R4 (Startup-fault) and wiring for R5-R10/R11
    scheduling.py     R5-R10 (Scheduling-fault, including R9's sibling-Pod correlation)
    health.py         R11 (Runtime-health-fault: liveness/readiness probe failures)
    contradictions.py  the two contradiction detectors
    patterns.py        the named, non-exhaustive substring/quantity-parsing rule bank
  bundle_builder.py   collector output -> typed, content-hashed EvidenceBundle
  export_sanitization.py  sanitized-by-default --save-bundle export
  report_builder.py   validated findings -> InvestigationReport
  rendering.py     human-readable report rendering
  collector/       the narrow Kubernetes client and orchestration (read-only, bounded)
  ai/              optional AI-hypothesis layer (off by default) -- model input projection,
                   candidate parsing/validation, redaction, the OpenAI adapter
  cli.py           `kai pod <name> ...` (also installed as `investigate`, a trivial alias)
tests/             synthetic fixtures + a fake Kubernetes client; no live cluster
docs/              the full research, design, and decision record for this project
integrations/
  kubectl/kubectl-kai  optional kubectl plugin -- forwards to kai unchanged (see above)
```

## Documentation

The complete research and design history lives in `docs/` — `docs/open-questions.md` tracks every
material decision's status (approved / recommended / open / deferred);
`docs/phase-0.8-mvp-blueprint.md` is the approved architecture; `docs/phase-0.9-milestone-1-notes.md`
and `docs/phase-0.10-milestone-2-notes.md` record what each milestone actually built, deviated
on, and tested; `docs/research/read-only-access-matrix.md` §7-§8 documents the exact Milestone 2
API calls and RBAC.
