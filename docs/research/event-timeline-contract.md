# Event and Timeline Trustworthiness Contract

**Phase:** 0.7 — Investigation C, resolves Q9
**Date:** 2026-10-13
**Scope:** How this project's future investigator may and may not reason about *when* things
happened, using only the evidence already in scope (Kubernetes object state and Events). **This
document does not propose collecting metrics history, audit logs, or any additional telemetry** —
that would expand the MVP's evidence surface, which the brief explicitly excludes. It is a contract
on correct interpretation of what Investigation B already scopes in.

Evidence grading: **[S]** source-inspected, **[D]** primary documentation.

---

## 1. The four timestamps that exist on a Kubernetes Event, defined precisely

Verified directly from the canonical type definition
([`k8s.io/api/core/v1/types.go`](https://github.com/kubernetes/kubernetes/blob/master/staging/src/k8s.io/api/core/v1/types.go),
`type Event struct`) — **[S]**. The type's own doc comment, quoted verbatim, is the single most
important sentence in this document:

> *"Events should be treated as informative, best-effort, supplemental data."*

| Field | Exact meaning (quoted from source) | Behavior on repeat occurrences |
|---|---|---|
| `metadata.creationTimestamp` (`ObjectMeta`) | When the Event *object* was first written to etcd | **Never changes**, even as the same logical event recurs and updates other fields |
| `firstTimestamp` | *"the time at which the event was first recorded. (Time of server receipt is in TypeMeta.)"* | Fixed at creation |
| `lastTimestamp` | *"the time at which the most recent occurrence of this event was recorded."* | **Updates on every repeat** |
| `count` | *"the number of times this event has occurred."* | Increments on every repeat |
| `eventTime` (newer `events.k8s.io/v1`-style field, `MicroTime` precision) | *"the time when this Event was first observed."* | Fixed at creation, analogous to `firstTimestamp` but microsecond-precision |
| `series.count` / `series.lastObservedTime` (`EventSeries`) | *"count is the number of occurrences in this series up to the last heartbeat time"* / *"the time of the last occurrence observed"* | The newer API's replacement for the `lastTimestamp`/`count` repeat-tracking mechanism |

**What `kubectl describe`'s familiar `Age: 2s (x9 over 118s)` format actually encodes** (observed
directly in Phase 0.6's case digests, e.g. `scheduling/137`'s PVC event): the first number is
`lastTimestamp`'s age (how long ago the *most recent* occurrence was recorded); the `x9 over 118s`
is `count`/`series.count` and the span from `firstTimestamp`/`eventTime` to `lastTimestamp`. **A
"young-looking" event age therefore tells you only when it was last repeated — never when the
underlying condition began.** The span (`firstTimestamp` → `lastTimestamp`) is the only field pair
that bounds duration, and even that is only a lower bound (§3).

---

## 2. Why the event TTL does not bound how old a condition can be — verified in source

Phase 0 verified the default: `EventTTL: 1 * time.Hour` (`kube-apiserver` options). This phase
traced the mechanism further — **[S]**,
[`pkg/registry/core/rest/storage_core_generic.go`](https://github.com/kubernetes/kubernetes/blob/master/pkg/registry/core/rest/storage_core_generic.go):

```go
eventStorage, err := eventstore.NewREST(restOptionsGetter, uint64(c.EventTTL.Seconds()))
```

The TTL is wired into the event store as an **etcd lease duration on the stored object**, not as a
fixed expiry from `firstTimestamp`. A lease is refreshed on every write to the object. **Because a
repeat occurrence of the same logical event is a write (updating `lastTimestamp`/`count` or the
`series` block) to the *same* Event object, a continuously-recurring problem's Event object never
expires while it keeps recurring — even though its `firstTimestamp`/`eventTime` may say the
condition began well over an hour ago.** Conversely, a one-off event with no repeats is evicted
roughly one hour after its single write, regardless of how operationally significant it was.

**Consequence for this project's investigator:** the presence of an event with an old
`firstTimestamp` is not surprising or suspicious — it is the expected shape of a persistent,
recurring condition. The *absence* of any event for a condition is the ambiguous case (§4).

---

## 3. `resourceVersion` is not a timestamp and is not comparable across kinds — verified against the API conventions

**[D]**, from the Kubernetes community API conventions document
(`kubernetes/community`, `contributors/devel/sig-architecture/api-conventions.md`), quoted directly:

> *"This value MUST be treated as opaque by clients and passed unmodified back to the server."*
>
> *"Clients should not assume that the resource version has meaning across namespaces, different
> kinds of resources, or different servers."*
>
> *"Currently, the value of resourceVersion is set to match etcd's sequencer. You could think of it
> as a logical clock the API server can use to order requests. However, it's important to note that
> the application should **not** rely on the implementation details of the versioning system
> maintained by Kubernetes."*

**Consequence:** `resourceVersion` may never be used by this project to infer *when* something
happened (it is not a clock) or to order a change on object A relative to a change on object B,
even if both numbers happen to be comparable in the current etcd-backed implementation — the
specification explicitly disclaims that usage, and this project must not rely on an implementation
detail the spec itself warns against relying on.

---

## 4. What a frozen evidence bundle can and cannot establish about chronology

Directly following from Investigation 3's (Phase 0.5) frozen-snapshot design:

**Can establish:**
- A **self-consistent, reproducible point-in-time view**: what every collected object's fields and
  every collected event's fields read *at the moment collection ran*. Re-running analysis against
  the same bundle must reproduce the same conclusions (Investigation 2's reproducibility contract,
  Phase 0.5) — this is a property of the bundle, not of the real cluster.
- **Within one object's own event history**, the relative order of `firstTimestamp` → `lastTimestamp`
  for the *same* Event object (tautological by field definition, not an inference).
- **A lower bound on how long a condition has been occurring**, when `firstTimestamp`/`eventTime` is
  present: the condition has existed at least since then. This is a legitimate, hedged claim.

**Cannot establish:**
- **That nothing relevant happened before the earliest timestamp found.** Earlier occurrences may
  have been evicted (if the event stopped recurring and its lease lapsed) or may never have produced
  the event at all — these are indistinguishable from the collected evidence alone (§5's required
  disambiguation).
- **Wall-clock synchronization across components.** `firstTimestamp`/`lastTimestamp` are
  reported by whichever controller/kubelet emitted the event; `creationTimestamp` is assigned by
  the API server on receipt. Clock skew between a kubelet and the API server is a real, if usually
  small, operational possibility this project has not measured and should not assume away.
- **Causal order between two different objects' changes**, from timestamps alone. Two events on
  different objects with nearby timestamps are *temporally* close; whether one caused the other is
  a question for the deterministic correlation graph (ownership, selector-matching, taint/toleration
  matching — the structural edges Investigation 2/4, Phase 0.5, already define), never a conclusion
  drawn from "A's timestamp is earlier than B's."
- **That the real cluster, right now, still looks like the bundle.** The bundle is evidence of a
  past moment. Nothing in it may be presented as current state after the fact.

---

## 5. The three distinct reasons an expected signal can be missing — must never be collapsed

This is the single most important discipline this contract adds, because the three causes below
produce **identical evidence** (zero matching results) and are routinely conflated:

1. **Never emitted.** The condition genuinely did not occur, or did not produce an event (not every
   state change emits one).
2. **Emitted, then evicted.** The event existed and expired per §2's lease mechanism — most likely
   for a *non-recurring* event older than roughly the TTL window, or any event (recurring or not)
   whose last write is more than the configured TTL in the past.
3. **Scope miss.** The query's `fieldSelector`/`namespace`/time window simply didn't match it — a
   bug or an overly narrow collection parameter, not a fact about the cluster at all.

**Contract rule:** the investigator must never render these as "no events" without qualification.
The correct, typed representation (consistent with Investigation 2's `EvidenceStatus`, Phase 0.5)
distinguishes `NOT_FOUND` (queried, zero matches, no contrary signal) from a reasoned
`EXPIRED`-flagged case (queried, zero matches, **and** other evidence in the same bundle suggests
the condition likely predates the plausible TTL window — e.g., the pod's own age or another
object's older timestamp implies onset more than ~1 hour before collection) from `UNAVAILABLE`
(the query itself failed — a permissions or API error, not a zero-match result). Collapsing any of
these into a bare "nothing found" is exactly the failure mode this contract exists to prevent.

---

## 6. The conservative timeline contract

**Every time value this project's investigator surfaces must carry an explicit tag naming which
field it is** — `lastTimestamp`, `firstTimestamp`/`eventTime`, `creationTimestamp`,
`collectedAt` (the bundle's own collection time) — never a bare "at 14:02" with the source
implicit. This is a direct, schema-level extension of Investigation 2's `EvidenceItem` model
(Phase 0.5): a timestamp without its field identity attached is not a usable evidence value.

### Claims the investigator MAY make, with the evidence that licenses each one

| Permitted claim | Required evidence |
|---|---|
| *"The most recent occurrence of event E was recorded at `lastTimestamp = T`."* | Direct field read. |
| *"Event E has recurred at least N times, spanning `firstTimestamp` to `lastTimestamp`."* | Direct field read of `count`/`series.count` and both timestamps. |
| *"This condition has existed at least since `firstTimestamp`/`eventTime = T`."* | A lower-bound claim, explicitly hedged as a lower bound, never as "began at T." |
| *"No event matching `<selector>` was found in the collected evidence, queried at `collectedAt = T`."* | A fact about the query, phrased as a fact about the query — never as a fact about the cluster's history. |
| *"Object X's owner chain is Pod → ReplicaSet R → Deployment D"* | A structural claim from `ownerReferences`, independent of any timestamp — the correct way to establish relationship, never chronology. |

### Claims the investigator MUST NOT make without stronger evidence than this project collects

| Prohibited claim | Why it is unsupported |
|---|---|
| *"X caused Y because X's timestamp precedes Y's."* | Chronological adjacency is not causation; different components' clocks are not guaranteed synchronized (§4); the correlation graph's structural edges, not timestamps, establish relationship. |
| *"This condition just started"* (inferred from a young `lastTimestamp`). | §2: a young `lastTimestamp` is expected for any still-recurring condition, however old. |
| *"This never happened"* (inferred from zero matching events). | §5: collapses three distinct causes of absence into one false claim. |
| *"Object A changed before Object B"* (inferred from comparing `resourceVersion`). | §3: explicitly disclaimed by the Kubernetes API conventions as an implementation detail, not a guarantee, and never meaningful across different kinds. |
| *"This is the current state of the cluster"* (stated about a frozen bundle after the fact). | §4: a bundle is evidence of a past moment; nothing licenses projecting it forward. |
| Any claim using the **order in which this project's own collector happened to issue its API calls** as evidence of real-world event order. | Collection order is an artifact of this project's code, not of the cluster; it carries zero evidentiary weight and must never be cited as support. |

---

## 7. Mechanically validatable vs. irreducibly uncertain

Directly extending Investigation 2's (Phase 0.5) enforcement-boundary table:

- **Mechanically validatable:** that a surfaced timestamp is correctly tagged with its source field
  (a schema check); that `lastTimestamp ≥ firstTimestamp` on the same object (a tautological
  sanity check against the field definitions, useful for catching a malformed or mis-parsed Event,
  not for inferring anything about the world); whether an `EvidenceQuery` returned zero results
  (a boolean fact); whether the bundle's `collectedAt` is more than ~1 hour after the earliest
  onset-relevant timestamp found elsewhere in the same bundle (a mechanical threshold check that
  *flags* a plausible-TTL-expiry case for §5's `EXPIRED` classification — it cannot *prove*
  expiry occurred, only that it is a live, disclosable possibility).
- **Remains irreducibly uncertain, must be hedged rather than resolved:** whether an absence is
  case 1, 2, or 3 from §5, when the mechanical flag above does not clearly favor one (e.g., the
  gap is well under an hour, ruling out TTL expiry, but the event still wasn't found — genuinely
  ambiguous between "didn't happen" and "scope miss," and the investigator should say so rather than
  guess); whether two temporally-close events on unrelated objects are meaningfully connected absent
  a structural (ownership/selector) edge between them; whether clock skew between the reporting
  component and the API server is large enough to matter in any specific case (not measured by
  anything in this project's evidence scope).

---

## 8. What this document deliberately does not propose

- No audit-log collection, no metrics-history backend, no event-archival/export sidecar. The brief
  is explicit that the MVP's evidence surface stays Kubernetes object state and Events as already
  scoped in Investigation B — this contract is about interpreting that evidence correctly, not
  about collecting more of it.
- No attempt to reconstruct a cluster-wide, cross-object incident timeline as a first-class MVP
  feature. The claims in §6 are deliberately narrow (bounded to what one object's own fields
  support) rather than an ambitious "build the incident timeline" capability — that remains future
  work, explicitly out of scope here, and should not be read into this document's recommendations.
