# Read-Only Access Matrix for `investigate pod/<name>`

**Phase:** 0.7 — Investigation B, resolves Q10
**Date:** 2026-10-13
**Scope:** A design proposal, not an implementation. **No `Role`, `ClusterRole`, `RoleBinding`,
`ClusterRoleBinding`, or `ServiceAccount` is created in this phase.** Every row below is justified
individually, per the brief's explicit instruction — this is a candidate list, not a grant request.

Grounded directly in: `CLAUDE.md`'s safety section; the verified Kubernetes RBAC bootstrap-policy
source (`kubernetes/kubernetes`, `plugin/pkg/auth/authorizer/rbac/bootstrappolicy/policy.go`,
checked this phase); and Phase 0.6's empirical finding of which resource kinds the 15 tested
Cloud-OpsBench cases actually needed to reach a correct diagnosis.

---

## 1. RBAC versus application-layer enforcement — what this project can and cannot rely on

Restated precisely here because the access matrix below is only meaningful in light of it (full
detail and source citations in `docs/research/evidence-access-options.md` §2.3):

- **Kubernetes RBAC, enforced by the API server, is the only real security boundary.** Every verb
  this project's collector is capable of issuing must be assumed executable unless RBAC denies it —
  "the code never calls Delete" is a correctness property of the code, not a security guarantee,
  because code changes. The matrix below is the thing that must be correct.
- **Nothing this project writes in its own collector — a denylist, a "read-only mode" flag, an
  allow-list of GVKs in code — is a substitute for the RBAC grant actually bound to the
  ServiceAccount token.** kubernetes-mcp-server's own three in-process layers (a transport-level
  GVK denylist, an optional RBAC pre-check, and a tool-advertisement filter) are each useful
  defense-in-depth but are explicitly described by their own authors as non-substitutes for RBAC
  (`docs/research/evidence-access-options.md` §2.3) — this project adopts that same framing for any
  equivalent in-process controls it builds.
- **Therefore:** the grants below should be the *complete* set of verbs the ServiceAccount is
  capable of — not a superset trimmed only by application logic. If a verb is not in this matrix,
  the ServiceAccount should not have it, regardless of whether the collector code happens not to
  call it today.

---

## 2. Namespace-scoped vs. cluster-scoped — the rule and its two forced exceptions

**Default rule:** for every namespaced resource kind below, grant via a `Role` bound by a
`RoleBinding` scoped to the one namespace under investigation (or, equivalently, a reusable
`ClusterRole` rule-set bound per-namespace via `RoleBinding`s, never via a `ClusterRoleBinding`) —
this bounds the blast radius of the ServiceAccount's credential to exactly the namespace being
investigated at the time, and is tightenable to "one namespace at a time" if the deployment model
ever wants that.

**Two resources in the candidate list are inherently cluster-scoped, and Kubernetes RBAC has no
finer-grained scoping available for them — a `Role`/`RoleBinding` cannot grant access to a
cluster-scoped resource at all; this is a hard rule of the Kubernetes RBAC model, not a design
choice this project is making:**

- **`Node`** (core/v1) — required to see taints, labels, `unschedulable`, and allocatable capacity.
  **Verified this phase: the built-in `view` ClusterRole does *not* include `nodes`** (confirmed by
  reading `viewRules()` in `kubernetes/kubernetes`'s bootstrap policy source — no `"nodes"` resource
  appears anywhere in that function). This project must request this specific additional
  cluster-scoped grant on top of (or instead of) `view`; it is not something `view` already covers.
  `get`+`list` on `nodes` is, by necessity, cluster-wide once granted — `resourceNames` restriction
  on `get` is not practically usable here because the collector does not know which node a pod
  landed on (or why it *didn't* land on one) until after reading the pod, so it cannot be known in
  advance. **Mitigating factor:** Node objects carry infrastructure metadata (taints, labels,
  capacity, addresses) and no application-level Secret data — this is the single broadest grant in
  this matrix, and that breadth is disclosed here rather than left implicit.
- **`PersistentVolume`** (core/v1) — required for the `pv_binding_occupied` class of failure (Phase
  0.6, `scheduling/137`: the pod's PVC was pending because the cluster's one PV was bound to a
  *different* PVC — invisible without reading the PV directly). **Also verified absent from `view`**
  (only `persistentvolumeclaims` appears in `viewRules()`, not `persistentvolumes`). Same forced
  cluster-scope as Node, but lower-sensitivity data (capacity, reclaim policy, backing storage path
  — no Secret-equivalent content).

Everything else in this matrix is namespace-scoped and bindable per-namespace.

---

## 3. The matrix

Every "Essential" row below was tested and confirmed necessary against real evidence in Phase 0.6
(not assumed); every "Deferred" row is deferred because Phase 0.6's 15-case sample never needed it
for the Scheduling/Startup categories that phase validated, consistent with the brief's instruction
to defer rather than grant speculatively.

| Resource | API group / version | Scope | Verbs | Why | Secret/sensitive exposure | MVP status |
|---|---|---|---|---|---|---|
| `Pod` | core (`""`) / v1 | Namespaced | `get`, `list` | Pod phase, container states/`lastState`/restart counts, `ownerReferences`, scheduling conditions (`PodScheduled`), `nodeSelector`, `tolerations`, `spec.affinity` (via structured JSON — see `docs/research/evidence-access-options.md` §2.1 on why this matters) | No — pod spec/status carries no Secret values (volume *mounts* reference Secret names, not contents) | **Essential** |
| `Event` | core (`""`) / v1 | Namespaced | `list` (scoped by `fieldSelector` on `involvedObject.name/kind/uid`) | The single richest signal in Phase 0.6: 10 of 15 cases were resolvable directly from event `message`/`reason` text alone (`FailedScheduling`, `ErrImagePull`, `Unhealthy`, …) | No | **Essential** — see `docs/research/event-timeline-contract.md` for how to interpret these correctly |
| `pods/log` subresource | core (`""`) / v1 | Namespaced | `get` | Phase 0.6 proved this is **not optional**: `volume_mount_permission_denied` was unresolvable from object state + events alone — the diagnostic line (`"Permission denied"`) existed only in container stdout | **Yes, in principle** — see §4 below, this is the one source in this entire matrix this project cannot inspect in advance for sensitive content | **Essential**, with bounded collection (§4) |
| `ReplicaSet` | `apps` / v1 | Namespaced | `get` | Resolves `Pod.ownerReferences` one hop up the chain `CLAUDE.md` names explicitly | No | **Essential** |
| `Deployment` | `apps` / v1 | Namespaced | `get` | Resolves the chain's named terminus (`ReplicaSet.ownerReferences` → `Deployment`); `CLAUDE.md` names this explicitly | No | **Essential** |
| `ServiceAccount` | core (`""`) / v1 | Namespaced | `get` | Phase 0.6 (`startup/42`, `startup/43`): confirming `missing_image_pull_secret` requires reading the pod's `serviceAccountName` → `ServiceAccount.imagePullSecrets`. **Reading this object never requires touching the `Secret` API at all** — the pull-secret *reference* (a name) lives on the ServiceAccount object itself | No — only Secret *names* are referenced here, never contents, and this project never follows that reference to a `Secret` object | **Essential** |
| `PersistentVolumeClaim` | core (`""`) / v1 | Namespaced | `get` | Phase 0.6 (`scheduling/137`): resolves the Pod→PVC step of the `pv_binding_occupied` chain | No | **Essential** (if Scheduling-category coverage is in the first demonstrable scenario set, which Phase 0.6's "proceed" recommendation supports) |
| `Node` | core (`""`) / v1 | **Cluster** | `get`, `list` | Phase 0.6: 6 of 8 Scheduling cases needed Node `taints`/`unschedulable`/`labels`/capacity to confirm the diagnosis (cordon, taint-toleration, node-selector, CPU/memory-capacity mismatches) | No (see §2) | **Essential** — but see §2 for the cluster-scope caveat |
| `PersistentVolume` | core (`""`) / v1 | **Cluster** | `get`, `list` | Phase 0.6 (`scheduling/137`): resolves the PVC→PV step; needed to see the PV is bound to a *different* claim | No | **Essential** (same conditional as PVC above) |
| `Service` | core (`""`) / v1 | Namespaced | `list` | Correlate whether a Service's selector matches this pod's labels | No | **Deferred** — not needed by any of the 15 Phase 0.6 cases; `docs/research/evaluation-strategy.md` §4 already scopes Service-Routing-category faults out of the MVP |
| `EndpointSlice` | `discovery.k8s.io` / v1 | Namespaced | `list` | Confirm whether a healthy pod is actually registered as a serving endpoint | No | **Deferred**, same reasoning as `Service` |
| `Namespace` | core (`""`) / v1 | Cluster (self-read only when bound via `Role`, per the bootstrap-policy source comment) | `get` | Namespace-level labels/annotations, admission context | No | **Deferred** — not needed for any tested case; relevant only if Admission-Control-category faults (out of scope per `docs/research/evaluation-strategy.md` §4) are added later |
| `ResourceQuota` | core (`""`) / v1 | Namespaced | `get`, `list` | Admission-Control fault diagnosis (quota exceeded) | No | **Deferred**, same reasoning as `Namespace` |
| `Secret` | core (`""`) / v1 | — | **none** | — | **Yes — hard deny, no exception** | **Excluded.** Every Phase 0.6 case that looked secret-related (`missing_image_pull_secret`) was fully resolved via the `ServiceAccount` object's own fields, without ever reading a `Secret`. No MVP scenario requires this grant; none should be requested. |
| Any write verb (`create`, `update`, `patch`, `delete`, `deletecollection`) on any resource | — | — | **none** | — | N/A | **Excluded**, per `CLAUDE.md`'s safety section |
| `pods/exec`, `pods/attach`, `pods/portforward`, `pods/eviction` subresources | core (`""`) / v1 | Namespaced | **none** | — | These are grouped with `secrets` in Kubernetes' own `editRules()` (verified this phase, `kubernetes/kubernetes` bootstrap policy source) as "escalating" permissions — upstream Kubernetes' own security model treats pod-exec-level access as equivalent in sensitivity to reading Secrets | **Excluded** |
| `watch` verb, on any resource | — | — | **none** | Investigation 3 (Phase 0.5) committed the MVP to a bounded, point-in-time snapshot collection, not a long-lived streaming connection | N/A | **Excluded for the MVP** — revisit only if a future phase adopts a live-watch or hybrid design |

---

## 4. Logs: bounded collection and the honest limit on redaction

`CLAUDE.md` requires avoiding Secret *values* and collecting only necessary metadata — but container
logs are the one evidence source in this matrix that is **not a Kubernetes object**: it is arbitrary
text an application chose to write to stdout/stderr, and nothing in the Kubernetes API model governs
or labels its sensitivity. This must be stated as a limitation, not solved away:

- **Design requirements for the MVP, proposed:**
  - **Bound every log fetch by both lines and time.** `kubernetes-mcp-server`'s `pods_log` tool
    defaults `tail` to a fixed constant rather than unbounded retrieval (`docs/research/evidence-access-options.md`
    §2.2) — this project should do the same: a default tail-line cap and, where the client
    supports it, a `sinceTime`/`sinceSeconds` bound tied to the investigation's own time window
    (see `docs/research/event-timeline-contract.md`).
  - **Prefer `previous: true` (the terminated container's prior run) only when the container is
    actually in a restart/crash state** — Phase 0.6's `volume_mount_permission_denied` case needed
    exactly this (the fatal error was logged once, immediately before the crash).
  - **Do not attempt automated secret-pattern redaction as a security control.** A regex for
    `AKIA[0-9A-Z]{16}`-shaped strings or similar can catch *some* accidentally-logged credentials,
    but this project must not claim or rely on this as a guarantee — it is best-effort hygiene for
    display, not a substitute for the actual boundary (namespace/RBAC scoping, and the application's
    own responsibility not to log secrets). This mirrors `CLAUDE.md`'s own instinct (avoid Secret
    *values*) but logs are fundamentally outside what RBAC-based Secret exclusion can protect —
    stating this honestly is itself a design requirement, not optional polish.
  - **Scope log collection to the specific container(s) being investigated**, never a
    namespace-wide log sweep, keeping the exposure proportional to the one pod under investigation.

---

## 5. Defense-in-depth boundaries, layered explicitly

Consistent with §1's framing, stated as concrete layers this project should build, each labeled
with what it actually guarantees:

1. **RBAC (the real boundary).** The matrix in §3, bound to a ServiceAccount via `Role`/`RoleBinding`
   per namespace plus the two forced `ClusterRole`/`ClusterRoneBinding` grants for `Node`/`PersistentVolume`.
2. **In-process GVK allow-list, mirroring kubernetes-mcp-server's `denied_resources` pattern but
   inverted as an allow-list** (only the kinds in §3's "Essential" rows are ever requested) — a
   defense-in-depth measure matching `docs/research/evidence-access-options.md` §2.3's layer 2,
   explicitly understood as a backstop against this project's own future bugs, not a replacement for
   layer 1.
3. **No write-verb code paths exist at all** in the collector — simpler than kubernetes-mcp-server's
   tool-filtering approach (§1), because this project's narrow collector (Phase 0.7 Investigation A
   recommendation) never implements a write call in the first place, so there is no filter to get
   right.
4. **Bounded, bundle-scoped collection** (Investigation 3, Phase 0.5) — a time-boxed collection pass
   per investigation, not a standing watch, which limits how long any single credential/connection
   is live.

---

## 6. What remains for implementation (not resolved here)

- The exact `Role`/`ClusterRole` YAML, `ServiceAccount`, and binding objects — explicitly deferred to
  implementation and explicitly not created in this phase.
- Whether `get`+`list` on `Node`/`PersistentVolume` should be requested unconditionally or only when
  a Scheduling-shaped investigation is actually underway (a possible future refinement: request a
  narrower, shorter-lived credential per investigation type) — noted as a design idea, not decided.
- The specific bounded `tail`/`sinceSeconds` defaults for log collection (§4) — a tuning decision for
  implementation, informed by but not fixed by this document.

---

## 7. Milestone 2 activation record

Written **before** the corresponding collector code was enabled, per the Milestone 2 instruction
to document exact API calls and required RBAC ahead of implementation. This section records
exactly what Milestone 2 activates — `Node`, `PersistentVolume`, and `PersistentVolumeClaim` — and
resolves §6's two open items for this project's actual implementation (not as new design, as the
concrete instantiation of what §6 already anticipated).

### 7.1 Exact calls made

| Resource | Verb(s) actually called | Why not the full approved verb set |
|---|---|---|
| `Node` | `list` only | §3 approved `get`+`list`; the collector never knows a specific node name in advance (a pod's `spec.nodeName` is empty while unscheduled, which is exactly when this evidence is needed), so only `list` is ever issued. `get` is approved but unused. |
| `PersistentVolume` | `list` only | Same reasoning — the collector does not know which PV (if any) is relevant until it has read the pod's PVC(s); it lists all PVs once and checks membership, rather than guessing a name to `get`. |
| `PersistentVolumeClaim` | `get`, by name | The pod's own `spec.volumes[].persistentVolumeClaim.claimName` gives the exact name directly — no `list` is needed or requested. |

### 7.2 §6's "unconditional or gated" question — resolved for this implementation

**Gated, not unconditional.** `Node`/`PersistentVolumeClaim`/`PersistentVolume` are collected only
when the investigated Pod's own evidence already shows a scheduling-blocked signal: `status.phase
== "Pending"` **and** a `status.conditions[]` entry with `type == "PodScheduled"` and `status ==
"False"`. This is a structural check on evidence already collected in Milestone 1's first call
(`get_pod`) — it adds no new API call of its own, and it means a healthy or already-running pod's
investigation never triggers the cluster-scoped `Node`/`PersistentVolume` list calls at all. This
answers §6's open question in favor of the narrower option it named, consistent with the
Milestone 1 approval's least-privilege sequencing rationale (`docs/open-questions.md` Q23).

### 7.3 Required RBAC, precisely

```
# Cluster-scoped (ClusterRole + ClusterRoleBinding — no narrower option exists, per §2)
- apiGroups: [""]
  resources: ["nodes", "persistentvolumes"]
  verbs: ["list"]

# Namespace-scoped (Role + RoleBinding, as for the Milestone 1 rows)
- apiGroups: [""]
  resources: ["persistentvolumeclaims"]
  verbs: ["get"]
```

No `Role`/`ClusterRole`/`ServiceAccount`/binding object is created by this project at this or any
prior phase — this is the exact rule text a future deployment-time grant would need, not a
manifest.

### 7.4 What this does not change

`Secret` remains hard-excluded; no write verb, `exec`, `attach`, `port-forward`, or eviction call
exists for any of these three resources (or any other); the Milestone 1 six-call surface is
unchanged. `Service`/`EndpointSlice`/`Namespace`/`ResourceQuota` remain deferred, per §3.

---

## 8. R9 follow-up: Pod `list`, scoped to the owning ReplicaSet's selector

Written before the corresponding collector code was enabled, per the same discipline as §7.
Approved by the user specifically to complete R9's sibling-Pod anti-affinity correlation
(`docs/phase-0.10-milestone-2-notes.md` §2 identified this as a flagged, unresolved design
question; this section records the approval and the exact grant it requires).

### 8.1 The call

One additional `list` call on `Pod`, namespace-scoped to the investigated Pod's own namespace,
with a `labelSelector` query parameter built from the owning `ReplicaSet`'s own
`spec.selector` (`matchLabels` and/or `matchExpressions`).

### 8.2 The selector is a query filter, not an RBAC boundary — stated precisely because this is
easy to get backwards

**The label selector narrows what the client asks for. It does not narrow what the credential is
authorized to see.** Kubernetes RBAC has no mechanism to grant "list Pods matching label X" —
`list` on `pods` in a namespace authorizes listing *every* Pod in that namespace, full stop,
regardless of what `labelSelector` a particular call happens to pass. A credential with this
grant could list all pods in the namespace with or without a selector; the selector is this
project's own code choosing to narrow its *query*, not a security property the RBAC grant
provides. This is the same distinction §1 already draws for every other control in this
document (application logic is not a substitute for RBAC) — restated here because a selector
*looks* like a scoping mechanism and is not one.

### 8.3 What is actually collected and retained

The real API response is a full Pod object per match, same as every other object this project
reads — Kubernetes has no server-side mechanism to request only a subset of a Pod's fields.
**This project narrows what it *retains*, not what the server returns**: before a sibling Pod
entry becomes an `EvidenceItem`, it is projected down to exactly the fields R9's correlation
needs — `metadata.{name,namespace,uid,labels,ownerReferences}`, `spec.nodeName`,
`status.phase` — and nothing else (no container specs, no full status, no environment
variables). The investigated Pod's own object (fetched separately, by name, for R1–R8/R10) is
never projected down — only entries returned by this new list call are.

### 8.4 Required RBAC, precisely

```
# Namespace-scoped (Role + RoleBinding) -- added to the existing Pod row
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list"]   # "get" was already approved (Milestone 1); "list" is the addition
```

No `Role`/`ClusterRole`/`ServiceAccount`/binding object is created by this project. `get` and
`list` are distinct RBAC verbs — the Milestone 1 `get`-only grant does not implicitly include
`list`; this is the one row in the whole matrix where an existing resource's verb set grows,
rather than a new resource being added.

### 8.5 Gating — same discipline as §7.2

This call is issued only when **both** conditions hold: the investigated Pod is already
scheduling-blocked (§7.2's existing gate), **and** its owning `ReplicaSet` was successfully read
and has a non-empty `spec.selector`. A healthy pod, a pod with no owning ReplicaSet, or a
ReplicaSet with an empty/unreadable selector never triggers this call at all.

### 8.6 What this does not change

Still no write verb, `exec`, `attach`, `port-forward`, or eviction call for `Pod` or any other
resource. `Secret` remains hard-excluded. §7's `Node`/`PersistentVolume`/`PersistentVolumeClaim`
grants are unaffected.
