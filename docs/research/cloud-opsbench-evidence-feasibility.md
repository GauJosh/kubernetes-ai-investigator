# Cloud-OpsBench Evidence-Only Feasibility Test

**Phase:** 0.6 — resolves Q20 from `docs/open-questions.md`, raised in `docs/gap-analysis.md` §7 and
`docs/research/evaluation-strategy.md` §4 as the condition that could revise the Phase 0.5 "proceed"
recommendation.
**Date:** 2026-10-13
**Type:** Bounded research experiment. No application code, no production schema, no collector, no
LLM framework, no cluster access. Dataset case files only.

**Question:** Can Cloud-OpsBench Scheduling and Startup failures be diagnosed using Kubernetes
object state and Kubernetes events alone, without metrics or alert data?

---

## 1. Dataset version and case-selection method

- **Repository:** [LLM4Ops/Cloud-OpsBench](https://github.com/LLM4Ops/Cloud-OpsBench), branch
  `main`, commit **`6b986be4e2915347393eb10e35d2fc864726071a`** (2026-10-13T13:32:09Z) — pinned via
  `gh api repos/LLM4Ops/Cloud-OpsBench/commits/main` at the start of this experiment.
- **Selection method, stated precisely so it is checkable:** first downloaded `metadata.json` for
  **all 164 Scheduling cases and all 62 `boutique/startup` cases** (the 24 remaining Startup cases
  live under `trainticket/startup` and were not sampled — scoped out for this bounded experiment,
  noted as a limitation in §7). All 226 cases returned `"difficulty": "easy"` — there is no
  "Easy-only" filtering decision to make within this scope; the entire available Easy/Scheduling and
  Easy/Startup(boutique) population was surveyed at the metadata level.
- From that survey, **one case per distinct `root_cause` value** was picked, taking the
  **lowest-numbered case ID** for each value (a deterministic rule, not a hand-picked favorable
  outcome). This maximizes root-cause diversity within the 5–10 budget rather than oversampling one
  fault shape.
- **8 Scheduling cases** (of 12 distinct root-cause values in the full 164; the 4 excluded are the
  closely related `pvc_access_mode_mismatch`/`pvc_selector_mismatch`/`pvc_storage_class_mismatch`/
  `pvc_capacity_mismatch` — all structurally similar to the included `pv_binding_occupied`, so
  excluding them is a diversity/budget tradeoff, not evidence avoidance).
- **7 Startup cases** (of 4 distinct root-cause values in the 62 surveyed; 2 cases were taken for
  `incorrect_image_reference` and `missing_image_pull_secret` and `image_registry_dns_failure` each,
  plus the sole available instance of `volume_mount_permission_denied`).

## 2. Permitted evidence — defined before the per-case diagnosis read

Per the brief's constraints, fixed **before** reading any case's reasoning in detail:

- **Permitted:** Kubernetes object state and Kubernetes events, in whatever form Cloud-OpsBench
  captures them. Verified this phase: `raw_data/k8s_states.json` is a flat map from literal `kubectl`
  command strings (e.g. `"kubectl describe pods X -n boutique"`) to their captured text output.
  **Kubernetes Events are present, but only embedded inside `kubectl describe` output's trailing
  `Events:` block** — there is no separate `k8s_events_raw.tsv`-equivalent file in this dataset's
  layout (unlike the ITBench layout referenced in Phase 0.5). This is itself a finding: this
  dataset's "events" evidence is exactly as rich as `kubectl describe`'s Events section and no
  richer (e.g., it does not include event `count`/`firstTimestamp` fields separately from the
  rendered "Age (xN over Ys)" text).
- **Excluded by instruction:** `raw_data/metrics.csv`, `raw_data/alert.json`.
- **Excluded from the primary test, logs checked only as an explicit, separately labeled sensitivity
  check:** `raw_data/logs.json`.
- **Never used as diagnostic input:** `metadata.json`'s `result` object (fault taxonomy, root
  cause, fault object), `code/`, `tool_cache.json`, any ChaosMesh/fault-injection CRD (none were
  present in any of these 15 cases — see §6).

**A methodological limitation to disclose plainly rather than gloss over:** Cloud-OpsBench stores
the ground-truth label in the *same small `metadata.json` file* as the case's `namespace`/`query`
fields, and that file was fetched first, for case selection. **True blinding was not achievable
within this file layout** — the label was visible before the evidence was read. The mitigation
applied: every classification below is justified by **quoting the specific field or event-message
text** that supports it, so the reasoning is checkable independently of whether the label was seen
first. Where a case's resolution depended on textual pattern-matching against the label rather than
an independently identifiable signal, that is called out explicitly (see §6 and the
`image_registry_dns_failure` discussion).

## 3. Unexpected, important finding about this dataset's own evidence design

For **both** Scheduling and Startup categories, `alert.json` is the literal two-byte string `{}` in
**all 15 sampled cases** (verified by direct inspection, not assumed), and **no `metrics.csv` file
exists at all** for any Scheduling or Startup case (verified via `gh api .../raw_data` listings —
only `alert.json`, `k8s_states.json`, `logs.json` are present). **Cloud-OpsBench itself does not
generate metric or alert evidence for these two fault categories.** This means the instructed
exclusion of metrics/alerts was, for this specific slice of the dataset, not a constraint this
experiment imposed on top of richer available evidence — it reflects what the dataset's authors
already chose to provide for config/scheduling-shaped faults. This narrows what this experiment can
claim: it is evidence that Scheduling/Startup are solvable from object-state-plus-events (a strong,
useful result), not evidence that metrics/alerts were available-but-unnecessary for them.

## 4. Per-case results

Evidence quoted verbatim from `k8s_states.json`. Classification follows the brief's four
categories.

### Scheduling (8 cases)

| Case | Root cause | Classification | Key evidence |
|---|---|---|---|
| `scheduling/1` | `node_cordon_mismatch` | **Directly observable** | Pod event: *"0/4 nodes are available: 4 node(s) were unschedulable."* All 4 `Node` objects independently show `Unschedulable: true` and `Taints: node.kubernetes.io/unschedulable:NoSchedule` — the standard signature of `kubectl cordon`. |
| `scheduling/17` | `cpu_capacity_mismatch` | **Directly observable** | Event names the mechanism explicitly: *"0/4 nodes are available: 1 node(s) were unschedulable, 3 Insufficient cpu."* Pod's own `Requests: cpu: 16` (16 full cores) is visible in the same `describe pods` block. |
| `scheduling/35` | `memory_capacity_mismatch` | **Directly observable** | Event: *"...3 Insufficient memory."* Pod `Requests: memory: 20Gi` visible in the same block. |
| `scheduling/56` | `node_affinity_mismatch` | **Correlation required, partially underdetermined** | Event: *"...3 node(s) didn't match Pod's node affinity/selector"* — generic wording the Kubernetes scheduler uses for **both** affinity and selector mismatches, so the event text alone does not distinguish this case from `scheduling/80` below. The pod's `Node-Selectors:` field reads `<none>`, and no affinity rule appears anywhere in the `describe pods` text (confirmed by grep — only the generic event sentence mentions "affinity"). Node labels show only `env=dev` on all three workers. **A human/system reading this evidence can correctly conclude "a scheduling-constraint mismatch exists and nodes lack some expected label," but cannot recover the specific required key/value from this evidence form.** See §7 for why — this is a `kubectl describe`-text rendering gap, not a Kubernetes API limitation. |
| `scheduling/80` | `node_selector_mismatch` | **Directly observable, correlation-confirmed** | Pod's own field: `Node-Selectors: environment=production`. All three schedulable nodes' labels show `environment=staging` (confirmed by inspecting each node's `Labels:` block). The requirement and the contradicting fact sit in two directly-quotable fields. |
| `scheduling/112` | `pod_anti_affinity_conflict` | **Directly observable, correlation-confirmed** | Event (distinct wording from the affinity/selector case): *"...3 node(s) didn't match pod anti-affinity rules."* Correlation across sibling pods in the same `describe` dump confirms it: of 5 `adservice` replicas, exactly 3 are `Running` — one each on `worker-01`, `worker-02`, `worker-03` — and the remaining 2 are `Pending`, consistent with a one-pod-per-node anti-affinity rule and a 3-node schedulable pool. |
| `scheduling/137` | `pv_binding_occupied` | **Correlation required, fully resolvable** | Pod's PVC (`redis-cart-pvc`) event: *"no persistent volumes available for this claim and no storage class is set."* The only `PersistentVolume` in the namespace (`redis-cart-pv`) describes as `Status: Bound`, `Claim: boutique/redis-a-pvc` — bound to a **different** PVC. Requires joining Pod → PVC → PV across three separate `describe` blocks, but each step is unambiguous. |
| `scheduling/141` | `taint_toleration_mismatch` | **Directly observable, correlation-confirmed** | Event: *"...3 node(s) had untolerated taint {critical: true}."* All three workers independently show `Taints: critical=true:NoSchedule`; the pod's `Tolerations:` field lists only the two default system tolerations, not `critical`. |

**Scheduling subtotal: 6 of 8 directly observable; 2 of 8 require correlation (both fully
resolvable at the level of "a constraint mismatch exists"); 0 of 8 unavailable; 1 of 8
(`node_affinity_mismatch`) underdetermined on the *specific* required value given this evidence
form.**

### Startup (7 cases)

| Case | Root cause | Classification | Key evidence |
|---|---|---|---|
| `startup/1` | `image_registry_dns_failure` (adservice) | **Directly observable** | Kubelet event, quoted in full because the exact wording matters: *"...dial tcp: lookup ghost-registry.example.net on 127.0.0.53:53: no such host."* `"no such host"` is an unambiguous DNS-resolution-failure signature, independent of the registry hostname's naming. |
| `startup/2` | `image_registry_dns_failure` (cartservice) | **Directly observable** | Same error-class signature (`failed to resolve reference ... lookup ... no such host`) on a different service/image. |
| `startup/14` | `incorrect_image_reference` (adservice, tag `v0.10.3x`) | **Directly observable** | Event: *"...rpc error: code = NotFound ... adservice:v0.10.3x: not found."* A `NotFound`/manifest-not-found signature, textually and semantically distinct from the DNS-failure signature above — the registry resolved and answered, the specific tag does not exist. |
| `startup/15` | `incorrect_image_reference` (currencyservice, tag `v0.10.33`) | **Directly observable** | Same `NotFound` signature on a different image/tag. |
| `startup/42` | `missing_image_pull_secret` (adservice, `ghcr.io/testuser177/...`) | **Correlation required, fully resolvable** | Event: *"...failed to authorize: failed to fetch anonymous token: ... 403 Forbidden"* — an authorization-failure signature, distinct from both the DNS and NotFound classes above. The 403 alone proves "pull requires and lacks credentials." Joining to the pod's `Service Account: adservice` and that ServiceAccount's own `describe` output — *"Image pull secrets: <none>"* — confirms the specific mechanism (no pull secret configured at all, not merely a wrong one). |
| `startup/43` | `missing_image_pull_secret` (checkoutservice) | **Correlation required, fully resolvable** | Same 403-Forbidden + ServiceAccount-has-no-pull-secret pattern. |
| `startup/60` | `volume_mount_permission_denied` (redis-cart) | **Unavailable from permitted evidence** | Object state + events show only: container `State: Waiting, Reason: CrashLoopBackOff`, `Last State: Terminated, Reason: Error, Exit Code: 1`, and kubelet events `Liveness probe failed: ... connection refused` / `Readiness probe failed: ... connection refused` / `Back-off restarting failed container`. **Nothing in this evidence names "permission" or "denied."** Exit code 1 and a generic `Error` reason are consistent with dozens of different crash causes. **Sensitivity check (§5): the actual cause is only visible in the excluded `logs.json`.** |

**Startup subtotal: 4 of 7 directly observable; 2 of 7 require correlation (fully resolvable); 1 of
7 unavailable from the permitted evidence boundary.**

### Overall across both categories (15 cases)

| Classification | Count | Share |
|---|---|---|
| Directly observable | 10 | 67% |
| Correlation required, fully resolvable | 3 | 20% |
| Correlation required, partially underdetermined | 1 | 7% |
| Unavailable from permitted evidence | 1 | 7% |

**This is a small, deliberately diverse convenience sample of 15 cases out of 754 in the full
dataset (and out of 226 Easy Scheduling/Startup(boutique) cases specifically) — it is not
statistically representative and is not presented as such.** It is representative of the **range of
root-cause types**, since every distinct root-cause value in the surveyed population is covered at
least once (12 of 12 Scheduling types either included or structurally subsumed; 4 of 4 Startup
types included).

## 5. Sensitivity check: logs, for the one `Unavailable` case

As instructed, logs were excluded from the primary test and checked only as a separate,
explicitly-labeled sensitivity probe — and only once a case's primary classification already came
out `Unavailable`. `startup/60`'s `logs.json` (`redis-cart` key, container stdout) ends:

```
... * Server initialized
# Fatal error: can't open the RDB file dump.rdb for reading: Permission denied
# Fatal error loading the DB, check server logs. Exiting.
```

This **confirms** the `Unavailable` classification was correct, not merely a failure to look hard
enough: the specific, diagnostic string is a container-internal application log line, structurally
absent from both the Kubernetes Events stream and every object-state field captured for this pod
(no `securityContext`, `fsGroup`, or permission-relevant field is rendered in the pod's `describe`
output — the `HostPath` volume's on-disk permissions are not part of the Kubernetes object model at
all). **For this fault type, logs are not an optional enrichment — they are necessary.** This is
consistent with, not contrary to, `CLAUDE.md`'s own MVP evidence list, which already names logs
alongside pod status/events/ownership; it is a reason to keep logs in scope, not a reason to
reconsider the architecture.

## 6. Independent check for prohibited shortcuts

None of the 15 cases contain a ChaosMesh (or any other) fault-injection CRD, a benchmark-specific
label, or an annotation naming the fault — all 15 are config/state-level faults (a changed image
field, a changed `nodeSelector`, a cordoned node, a bound-elsewhere PV), not fault-injector-object
scenarios of the kind `docs/gap-analysis.md` §2.6 flagged as the auditable-graph paper's own
documented weak spot. There is therefore **no injector-visibility shortcut available to exploit in
this sample at all** — a structural difference from that paper's ChaosMesh-coupled subset, and a
reason this result is not subject to the same specific criticism.

**One case deserves explicit scrutiny for a *different* kind of shortcut: `startup/1`/`startup/2`'s
registry hostname, `ghost-registry.example.net`.** The word "ghost" invites the concern that the
diagnosis might rest on a suggestive name rather than genuine evidence. Checked directly: the
diagnostic signal used above is the kubelet's own error text — *"lookup ghost-registry.example.net
on 127.0.0.53:53: no such host"* — which is Linux's actual DNS-resolver error class (`no such host`
is the standard `getaddrinfo` failure message), identical in form regardless of what the hostname is
named. **The classification would be unchanged if the hostname were a random, non-suggestive string**
— it was quoted that way (full string, full error class) specifically so this is checkable. This is
recorded as a considered-and-rejected shortcut concern, not an unexamined one.

## 7. Implementation-relevant finding, not part of the yes/no answer but important to carry forward

The one `Correlation required, partially underdetermined` case (`scheduling/56`,
`node_affinity_mismatch`) and the directly-observable `scheduling/80` (`node_selector_mismatch`)
share the **identical generic scheduler event text** (*"node(s) didn't match Pod's node
affinity/selector"*) — the Kubernetes scheduler does not distinguish these two mismatch types in its
event message. The two cases are solvable to different degrees **only because `kubectl describe
pod`'s human-readable text renders `.spec.nodeSelector` but does not render
`.spec.affinity.nodeAffinity`'s match expressions.** This is a property of the text-rendering
`describe` command, not of the Kubernetes API or object model — `kubectl get pod -o json`/`-o yaml`
would expose the full affinity rule. **This dataset captures `describe`-style text, not structured
object JSON, for every command in `k8s_states.json`** (confirmed in §2) — a real MVP collector
should fetch structured objects (JSON), not shell out to `describe`, specifically to avoid
reproducing this gap. This is a concrete, evidence-grounded input to the collector design question
left open in `docs/research/architecture-alternatives.md` Investigation 2/3, not a new open
question — flagged here so it isn't lost before implementation.

## 8. Recommendation

**Proceed.** Per the decision rule: a useful, diverse subset of Scheduling and Startup cases (14 of
15 sampled, 93%) is diagnosable with reasonable confidence from Kubernetes object state and events
alone, with the diagnosis traceable to specific, quotable fields and event messages in every one of
those 14 cases — not to a benchmark shortcut (§6). This is not a borderline result requiring
"revise or narrow," and nothing found rises to "the intended failure categories generally cannot be
investigated from the proposed Kubernetes evidence" — the opposite was observed.

**Narrowing to retain, stated precisely rather than left implicit:**

- **Retain:** `node_cordon_mismatch`, `cpu_capacity_mismatch`, `memory_capacity_mismatch`,
  `node_selector_mismatch`, `pod_anti_affinity_conflict`, `taint_toleration_mismatch`,
  `pv_binding_occupied` (Scheduling); `image_registry_dns_failure`, `incorrect_image_reference`,
  `missing_image_pull_secret` (Startup) — all directly observable or fully resolvable by
  correlation from object state + events alone.
- **Retain with a stated caveat:** `node_affinity_mismatch` — retain in the evaluation suite
  (it is a real, common failure mode), but score it knowing the *fault class* is correlatable while
  the *specific required value* is not recoverable unless the collector fetches structured pod
  specs rather than `describe`-text (§7). Do not expect a system under test to name the exact
  required node label from `describe`-equivalent evidence; expect it to name the mismatch's
  *existence* and flag the specific value as evidence it could not obtain if that is genuinely all
  it has access to.
- **Retain, explicitly as a logs-dependent case, not an object-state/events case:**
  `volume_mount_permission_denied` — keep it in the suite for Tier-2-style scenarios that
  deliberately test the logs-inclusive path, but do not count it toward "solvable from object state
  and events alone," and do not select it (or its sibling PVC-flavoured categories not sampled here)
  as evidence that this narrower evidence boundary suffices in general.
- **Exclude nothing wholesale from Scheduling/Startup on the basis of this experiment** — no
  category tested came back `Unavailable` at the *category* level; `volume_mount_permission_denied`
  is one root cause within Startup, not a reason to exclude Startup.

## 9. Limitations of this experiment — what it does not establish

- **Sample size and scope.** 15 of 754 total cases; only `boutique` Startup cases were surveyed (not
  `trainticket`'s 24 Startup cases); only Scheduling and Startup were tested — Runtime, Service
  Routing, Admission Control, Performance, Infrastructure, and Application Code Defect categories
  (the remaining 592 cases) were explicitly out of scope for this experiment and are not addressed
  by this result, including the "partial Runtime subset" `docs/research/evaluation-strategy.md` §1.3
  speculated might also be resolvable this way — that speculation remains untested.
- **Not blinded.** As disclosed in §2, the ground-truth label was visible before the evidence was
  read, because Cloud-OpsBench stores them in the same file used for case selection. The mitigation
  (quoting the specific supporting field/text for every classification) reduces but does not
  eliminate confirmation-bias risk; an independent reviewer re-deriving these classifications from
  the quoted evidence without having read this report's conclusions first would strengthen this
  result further and is recommended before treating these 15 cases as validated fixtures.
- **`describe`-text evidence, not structured objects.** As found in §7, this dataset's evidence form
  is `kubectl describe` text, which is a strict subset of what a structured-JSON Kubernetes
  collector would capture. The one `Underdetermined` result here is a **lower bound** on what a
  properly structured evidence collector could resolve for `node_affinity_mismatch` — the real MVP
  collector, fetching JSON, may do strictly better than this experiment shows, not worse.
- **This experiment used one case's reasoning per case, by one reviewer, once.** It does not
  establish inter-rater reliability, does not establish that an actual deterministic rule bank
  (`docs/research/architecture-alternatives.md` Investigation 4) correctly implementing this
  reasoning is easy to write, and does not establish anything about the AI-hypothesis layer's
  behavior on this evidence — it establishes only that the *evidence itself* contains the signal a
  correct diagnosis needs, for 14 of 15 sampled cases.
- **Does not test negative controls.** No healthy-pod or expired-evidence case was included here —
  those remain to be built per `docs/research/evaluation-strategy.md` §4 Tier 2 and are not
  substituted for by this experiment.
