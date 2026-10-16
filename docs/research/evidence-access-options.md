# Evidence Access Options: Consume kubernetes-mcp-server vs. Build a Narrow Collector

**Phase:** 0.7 — Investigation A, resolves Q6
**Date:** 2026-10-13
**Scope:** Compares two concrete alternatives for how this project reads Kubernetes evidence. No
application code is written here. MCP is evaluated on its merits, not adopted because this is an
AI-related project — the comparison below would be the same if the calling code were a plain script.

Evidence grading follows prior phases: **[S]** source-inspected, **[D]** primary documentation,
**[I]** inference.

---

## 1. What was actually inspected

**Repository:** [containers/kubernetes-mcp-server](https://github.com/containers/kubernetes-mcp-server)
· Apache-2.0 · Go · 2,159 stars · created 2025-02-11 · **last push 2026-10-13T06:28:40Z** (hours
before this research, by timestamp — very actively maintained).

Files read in full or in substantial part this phase: `pkg/kubernetes/accesscontrol_round_tripper.go`,
`pkg/kubernetes/rbac_validator.go`, `pkg/config/config.go`, `pkg/mcp/mcp.go`,
`pkg/toolsets/core/{pods,events,resources}.go`, `docs/tool-rbac-metadata.md`,
`docs/getting-started-kubernetes.md`, `charts/kubernetes-mcp-server/templates/rbac.yaml`, `go.mod`.
Phase 0.5 had already inspected the README's documented configuration surface at a higher level;
this phase goes to the implementation.

---

## 2. Option 1: Consume kubernetes-mcp-server

### 2.1 Structured object access and preservation of source fields — [S], strong

`resources_get`/`resources_list` (`pkg/toolsets/core/resources.go`) and `events_list`
(`pkg/toolsets/core/events.go`) fetch objects through the standard `client-go` dynamic/typed client
and serialize them with `output.MarshalYaml(...)` — **this returns the full structured Kubernetes
object (YAML), not a `kubectl describe`-style flattened text summary.** This is a materially
different, and better, evidence form than what Phase 0.6 found in Cloud-OpsBench's
`k8s_states.json`: that dataset captured `kubectl describe` text, and Phase 0.6 §7 found this
specifically loses `.spec.affinity.nodeAffinity` (describe doesn't render it) while preserving
`.spec.nodeSelector` (describe does). **kubernetes-mcp-server's structured-object approach does not
have this gap** — every field the API server returns is present in the YAML, including affinity
rules. This directly resolves the implementation note Phase 0.6 flagged for whichever collector
approach is chosen.

### 2.2 Supported resources, API groups, relationships, events, logs — [S], broad but generic

- `pods_list`, `pods_list_in_namespace`, `pods_get`, `pods_log`, `pods_top` are purpose-built tools
  with their own typed parameters (label/field selectors, `tail`, `previous` for terminated
  containers — `pods_log`'s `tail` defaults to `kubernetes.DefaultTailLines`, confirmed bounded by
  default, not unbounded).
- `events_list` takes a `fieldSelector` (documented supported fields: `involvedObject.kind/name/
  namespace/uid/apiVersion/resourceVersion/fieldPath`, `reason`, `reportingComponent`, `source`,
  `type`) and an optional `namespace`; **no `limit`/pagination parameter is exposed on this tool** —
  it returns everything matching the selector in one call.
- `resources_get`/`resources_list` are **fully generic**: any `apiVersion`/`kind` the backing
  ServiceAccount's RBAC allows, resolved through API discovery. This is powerful (one tool reaches
  Deployments, ReplicaSets, Services, Nodes, PVCs, PVs, EndpointSlices, anything) but means the
  **tool's own RBAC declaration is necessarily argument-shaped, not resource-specific** — see §2.3.
- Relationships (owner chains, Service↔Pod selector matching, PVC↔PV binding) are **not computed
  by any tool** — the server returns objects; correlating them into a graph remains entirely this
  project's own work either way, under both options.

### 2.3 Read-only enforcement — [S], precisely layered, not a single control

This is the most important finding of this investigation, and it must be stated precisely because
the brief explicitly asks for this distinction.

**Four distinct layers exist, with different strength guarantees:**

| Layer | Mechanism (source-verified) | What it guarantees | What it cannot guarantee |
|---|---|---|---|
| **1. Kubernetes RBAC** (API server) | The real `Role`/`ClusterRoleBinding` on the ServiceAccount token presented | The only layer independent of this project's or the server's code; the API server rejects any verb/resource the token lacks, regardless of what the server or any tool does | Nothing about *which* permissions were granted — that is this project's own design job (Investigation B) |
| **2. `AccessControlRoundTripper` + `denied_resources`** | `pkg/kubernetes/accesscontrol_round_tripper.go`: an `http.RoundTripper` wrapped around the client-go transport. Every outgoing HTTP request is parsed to a GVK via the REST mapper; `isAllowed()` rejects it before it leaves the process if the GVK (or `Group+Version` wildcard) is in `DeniedResources` | **Transport-wide, verb-agnostic**: blocks get/list/watch/create/delete uniformly for a denied kind (e.g. `Secret`), for *any* code path that uses this HTTP client, not just a specific tool | It is application code, not RBAC — a bug in `isAllowed()`, an empty `deniedResources` list, or a client that bypasses this transport would not be caught by it. It is defense-in-depth *on top of* RBAC, not a substitute. |
| **3. `RBACValidator`** (optional, `ValidationEnabled`) | `pkg/kubernetes/rbac_validator.go`: issues a `SelfSubjectAccessReview` before the real call, for a friendlier pre-flight error | A nicer error message, earlier | **Nothing if disabled or if the auth client is nil — it silently no-ops** (confirmed in source: `if authClient == nil { return nil }`); the real decision is always made by layer 1 regardless |
| **4. `read_only` + `ReadOnlyHint` tool filtering** | `pkg/config/config.go` comment, verbatim: *"When true, expose only tools annotated with readOnlyHint=true."* `pkg/mcp/mcp.go`'s `isToolApplicable`: `if c.ReadOnly.Get() && !ReadOnlyHint { return false }` | Hides write-shaped tools (`pods_delete`, `pods_exec`, `resources_create_or_update`, …) from the list of tools **offered** to an MCP client | This is a **tool-advertisement filter**, not a transport block. It trusts each tool's own self-declared annotation (`pods_exec` correctly has no `ReadOnlyHint`, confirmed in source) and provides zero protection against a direct Go call to the underlying handler, or against the backing ServiceAccount's RBAC simply allowing the write regardless |

The project's own documentation states this distinction for a fifth, related mechanism — the
per-tool RBAC metadata published via `tools/list` — in terms this project should adopt verbatim as
its own operating principle: *"The metadata is a planning aid. It does not grant access, replace
Kubernetes authorization, or guarantee that a call will succeed. The Kubernetes API server remains
authoritative."* ([`docs/tool-rbac-metadata.md`](https://github.com/containers/kubernetes-mcp-server/blob/main/docs/tool-rbac-metadata.md))

**Conclusion for this sub-question:** layer 1 (real RBAC) is the only layer this project can rely
on as a security boundary. Layers 2–4 are good engineering (especially layer 2, which is genuinely
structural) but are correctly described by the upstream project itself as aids, not guarantees —
this project must adopt the same honesty rather than describing any in-process control as "secure."

### 2.4 Secret access controls — [S]

Confirmed directly in `pods.go` and the Phase 0.5-inspected `README.md`: a `[[denied_resources]]`
entry with `kind = "Secret"` blocks it at layer 2 above, project-wide, for any verb. This is a real
mechanism, not a documentation claim — `isAllowed()` is a 15-line function this phase read in full.

### 2.5 RBAC requirements and least-privilege feasibility — [S], exceptional

Every tool declares a **machine-readable RBAC requirement** as Go struct metadata attached to the
tool definition itself (`api.RBACBounded(api.RBACRequirement{Verbs, Target, Namespace,
ResourceName})`), published to clients under `tools/list`'s `_meta["io.kubernetes-mcp-server/rbac"]`
key (`docs/tool-rbac-metadata.md`). Three declaration states exist: `none` (doesn't touch the API),
`bounded` (a finite, conservative requirement list), `unbounded` (can't be determined from the
tool's schema — e.g., a Helm-chart-driven tool whose resources depend on chart content). The
project's own upstream `charts/kubernetes-mcp-server/templates/rbac.yaml` and
`docs/getting-started-kubernetes.md` recommend, for production, a **dedicated ServiceAccount bound
to the built-in `view` ClusterRole or `view` Role**, with a short-lived token minted via the
`TokenRequest` API (`kubectl create token ... --duration=2h`) — a genuinely least-privilege-minded
default, not an afterthought.

### 2.6 Pagination, errors, timeouts, partial failures — [S], mixed

- `resources_list`/`pods_list` go through the standard client-go `List` call; Phase 0.5 noted
  (HolmesGPT comparison) that pagination for *very* large result sets is a real concern for any
  Kubernetes list call — this server does not appear to expose a `limit`/`continue` parameter on
  its list tools (not found in `pods.go`/`events.go`/`resources.go`'s input schemas), meaning a
  caller cannot bound an individual tool call's result size the way HolmesGPT's `kubectl` wrapper
  does. For a single `investigate pod/<name>` scoped by name/namespace this is unlikely to matter;
  it would matter for a cluster-wide `pods_list` on a very large cluster.
- Timeout/retry behavior is inherited from the standard `client-go` HTTP client configuration — not
  specially hardened in the files read this phase.
- No explicit partial-collection-failure contract was found (e.g., "if events succeed but logs
  fail, return what succeeded with a typed gap marker") — each tool call either succeeds or returns
  a Go error; composing several tool calls into one coherent "here is what I have and here is what
  I'm missing" evidence bundle (Investigation 2's `EvidenceStatus` model, Phase 0.5) is left entirely
  to the calling code either way.

### 2.7 Snapshot/export support for deterministic offline replay — [S], **none found**

Searched explicitly (`gh api search/code` for `snapshot`, `export bundle`) and found no feature
matching this description — all matches were unrelated (a KubeVirt VM "snapshot" feature, files
named `state.go` for OAuth/config state). **kubernetes-mcp-server is a live-query-only tool, by
design — it has no concept of a frozen evidence bundle.** This is not a point against it so much as
a point of *no difference* from the alternative: `docs/research/architecture-alternatives.md`
Investigation 3 already committed this project to a frozen-snapshot MVP, and **that serialization
layer must be built by this project regardless of which option is chosen** — neither option ships
it.

### 2.8 Dependency footprint, maintenance, extensibility, testing — [S]

`go.mod` pulls in, among others: the full Helm SDK (`helm.sh/helm/v3`), Tekton Pipeline types
(`github.com/tektoncd/pipeline`), a complete OpenTelemetry SDK + 7 exporter packages, an MCP SDK,
OIDC/OAuth2, `controller-runtime`, `cli-runtime`, `kubectl`, and `kube-openapi` — because the single
binary supports many optional toolsets (core, helm, kiali, kubevirt, netobserv, tekton, kcp)
simultaneously, not because `core` alone needs all of this. **This bears on integration pattern, not
Go-import bloat for this project**, because the realistic way to "consume" an MCP server is to run
its published binary/container image as a subprocess or sidecar and speak MCP (JSON-RPC over
stdio/HTTP) to it — not to `go get` and import its packages into a non-Go codebase, and this
project's implementation language is explicitly undecided (`docs/open-questions.md` Q21 territory).
Consuming it this way means: one more process/binary/container image to version, trust, and operate;
one more protocol/serialization layer (MCP/JSON-RPC) between this project's reasoning and the
Kubernetes API; and release-cadence coupling to an upstream project with many unrelated features
(Tekton, KubeVirt, Kiali, netobserv) this project will never use. Extensibility for this project's
purposes (a typed evidence-correctness model, Investigation 2 of Phase 0.5) would mean post-processing
the server's YAML output into that model — straightforward, but still work this project owns either
way.

---

## 3. Option 2: Build a narrow collector with a Kubernetes client library

A narrow collector means: directly calling a Kubernetes client (e.g. `client-go` if Go, or any
language's generated/typed client, or plain REST calls against the API server) for the specific
~8–12 GVKs `investigate pod/<name>` needs (Investigation B's matrix), with no MCP/JSON-RPC layer in
between.

| Dimension | Assessment |
|---|---|
| Structured object access | Identical to Option 1 in principle — any Kubernetes client returns the same structured JSON the API server serves; this is a property of the Kubernetes API, not of either tool |
| Supported resources | Exactly and only what this project implements — narrower by construction, which is the point: no generic `resources_get` surface that can reach anything RBAC allows, only the specific calls this project has reviewed and intends |
| Read-only enforcement | **Simpler, not weaker:** issue only `Get`/`List`/`Watch` calls in the collector's code — there is no write call to accidentally expose, so there is no tool-filtering-layer (Option 1's layer 4) to get right or wrong. Real security is still layer 1 (RBAC) either way. |
| Secret access | Simplest possible control: never write a code path that calls `CoreV1().Secrets(...)`. No denylist to maintain or verify. |
| RBAC requirements | Must be hand-derived by this project (Investigation B does exactly this) rather than read off of published tool metadata — more upfront work, but tailored precisely to what is actually called, with no risk of an `unbounded`-declared dependency |
| Pagination/errors/partial failure | Full control — this project can implement exactly the `EvidenceStatus`-typed partial-failure contract Investigation 2 (Phase 0.5) specifies, which neither option ships today |
| Snapshot/export | Same as Option 1 — must be built either way; building it alongside a narrow, already-controlled call surface is arguably less work than building it as a post-processing layer over a generic server's output |
| Dependency footprint | One Kubernetes client library, directly — no MCP SDK, no OpenTelemetry exporter stack, no Helm/Tekton types pulled in transitively |
| Maintenance | This project owns 100% of the collector code — more to maintain, but all of it serves this project's exact needs; no upstream feature drift to track |
| Extensibility | Adding a new GVK is a small, reviewable, self-contained change — consistent with `CLAUDE.md` principle 5 ("prefer simple, testable components") |
| Testing | A hand-rolled collector with ~8–12 calls is straightforward to unit-test against fixture objects/fake clients; no MCP protocol layer to mock |

---

## 4. Licensing, project activity, upstream dependency risk

- **kubernetes-mcp-server:** Apache-2.0 (confirmed via `gh api` license field, not just README
  text — contrast with the Phase 0.5 finding that the auditable-graph paper's artifact repo claims a
  license in its README but ships no `LICENSE` file; this repo genuinely has one). 2,159 stars,
  pushed hours before this research. Backed by the `containers` GitHub organization (Red
  Hat/Podman-adjacent). Low risk of abandonment by current signal; **upstream dependency risk is
  about scope creep and release coupling, not abandonment** — new toolsets, config schema changes,
  or MCP protocol version bumps happen on upstream's schedule, not this project's.
- **A narrow collector:** depends only on whatever Kubernetes client library is chosen, which is a
  far more stable, narrowly-scoped dependency surface (Kubernetes API conventions change slowly and
  with long deprecation cycles).

---

## 5. Recommendation

**Build a narrow collector for the MVP. Do not consume kubernetes-mcp-server as the evidence-access
layer at this stage.**

**Why, weighing the findings above:**

1. **The MVP's evidence surface is small and known** (Investigation B: ~8–12 GVKs for
   `investigate pod/<name>`). A generic server built to support Helm, Tekton, KubeVirt, Kiali,
   netobserv, and arbitrary-GVK `resources_get` calls is solving a materially bigger problem than
   this project has. Matching tool to task favors the narrow option, directly per `CLAUDE.md`
   principle 5.
2. **The project's real security boundary is Kubernetes RBAC either way** (§2.3) — consuming
   kubernetes-mcp-server does not relax the amount of RBAC design work this project must do
   (Investigation B), it only adds three more in-process layers on top, each clearly and correctly
   described by its own authors as a non-substitute for RBAC. Building narrow does not lose any
   *real* security relative to consuming the server — it loses only the convenience layers, which
   this project can forgo for an MVP this size.
3. **No snapshot/export capability exists in either option** — this removes what would otherwise be
   the strongest argument for adopting a mature upstream tool (avoiding reinventing hard
   infrastructure). There is no hard infrastructure here to avoid reinventing; both options require
   the same net-new serialization work.
4. **Dependency and operational footprint**: running an unrelated-feature-laden Go binary as a
   subprocess, speaking MCP/JSON-RPC, to reach ~10 GVKs this project could call directly, is added
   complexity without a corresponding benefit at this scope. This is consistent with the brief's
   explicit instruction not to assume MCP is required merely because this is an AI-related project —
   on inspection, it is not required for the evidence-access layer specifically, independent of
   whatever the eventual AI-hypothesis layer's tool-calling interface turns out to be (a separate,
   still-open design question, not resolved by this document).

**Conditions that would justify switching to kubernetes-mcp-server later — stated concretely so
this isn't revisited on vibes:**

- The MVP's evidence needs grow to genuinely require several of its other toolsets (Helm release
  state, Tekton PipelineRun status, Kiali/Istio topology) rather than core Kubernetes objects —
  i.e., the project's own scope grows to match what the server already solves.
- This project's AI-hypothesis layer (Investigation 4, Phase 0.5) is built as an agent that needs a
  **standard, swappable tool-calling protocol** across multiple backends (not just Kubernetes) —
  MCP's value proposition is interoperability across tools/clients, which only pays off once there
  is more than one tool surface to standardize.
- Operating the collector as a separately deployable, separately privileged process (rather than an
  in-process library call) becomes a deliberate architectural goal — kubernetes-mcp-server already
  solves "a hardened, separately-deployable K8s access service" well, including its ServiceAccount/
  token-minting guide (§2.5).
- A future audit finds that hand-maintaining RBAC-requirement tracking (Investigation B) does not
  scale as more resource kinds are added — at that point, adopting the published
  `tools/list`/RBAC-metadata pattern (§2.5), either by depending on the project or by copying its
  approach, becomes attractive on its own terms.
