# Memory operations — consolidation without rewriting experience

Status: proposed operating model. This defines the state AxonRelay should aim
for and the smallest future structure needed to reach it. Only the Evidence
Clip slice is implemented today. The brain analogy is bounded by the research
synthesis in [memory-design-evidence.md](./memory-design-evidence.md); software
structure follows computer-science constraints rather than neural anatomy.

## Desired state

A healthy memory workspace has these invariants:

- original records remain immutable and attributable;
- facts, source claims, interpretations, and decisions never share an
  unlabeled bucket;
- every derived item has lineage back to original records;
- active context stays small, scoped, and useful while cold history remains
  recoverable;
- contradictions are explicit objects, not silently averaged away;
- stale or superseded memory is visible but does not dominate retrieval;
- semantic indexes are disposable and rebuildable from canonical records;
- private memory cannot become shared memory through background processing;
- every promotion, merge, expiry, publication, and failed delivery is auditable.

## The consolidation cycle (a local-AI “dream cycle”)

The cycle runs per owner and scope, preferably while the device is idle. It is
not continuous training and it never directly mutates canonical memory.

```text
freeze watermark
  → gather new/changed records in one scope
  → extract source claims and interpretations
  → propose duplicates, relations, contradictions, and abstractions
  → replay each proposal against its evidence
  → apply policy gate
  → publish a new bounded memory.md projection
  → measure later usefulness and correction cost
```

Operations fall into two classes:

- **Automatically rebuildable:** tokenize, embed, cluster, rank, create search
  indexes, expire scratch projections. These may run unattended because their
  output can be deleted and rebuilt.
- **Governed:** declare a verified fact, promote durable practice, supersede a
  decision, delete canonical memory, or publish a shared capsule. Local AI may
  propose these; a recorded policy or human approval must authorize them.

Each run uses a fixed input watermark and records model/prompt/index versions.
Failure leaves the previous projection active. A run must be replayable and
must never mix private scopes merely because their vectors are close.

## From repeated experience to a practice

A practice is not a confident summary. It is a versioned derived assertion
with an evidence set and a falsification path:

1. At least two independent experience records exhibit the pattern.
2. The system proposes the narrowest conditional rule, including when it may
   not apply.
3. Supporting and contradicting records are attached separately.
4. A verifier checks provenance, scope, recency, and independence.
5. The practice remains `candidate` until enough later outcomes confirm it.
6. Promotion records who/what authorized it; later counterexamples lower its
   confidence or supersede it without deleting history.

The first trial should record supporting, contradicting, and later-reuse
episodes before selecting a promotion threshold. A tentative threshold may be
preregistered for the experiment, but it is a product hypothesis rather than a
number supplied by neuroscience.

## Smallest future canonical structure

Do not begin with one giant vector table. The conceptual responsibilities are:

| Record | Required role |
|---|---|
| `MemoryRecord` | Immutable original: owner, scope, source, content/hash, time, sensitivity |
| `Assertion` | `source_claim`, `verified_fact`, `interpretation`, `practice`, or `decision`; status and validity window |
| `Lineage` | Typed edge from a derived assertion to supporting/contradicting records and assertions |
| `ConsolidationRun` | Input watermark, local adapter/model/prompt versions, status, measurements |
| `MemoryProposal` | Proposed merge/link/promote/supersede/expire/publish operation and its rationale |
| `Projection` | Rebuildable `memory.md`, Context Pack, lexical index, or model-versioned plane-specific vectors |

Sharing adds `ContextCapsule`, but only after the personal-value gate passes.
Foreign capsules enter a separate namespace and never become verified local
facts automatically.

For implementation, these responsibilities collapse into the four boundaries
defined in [memory-design-evidence.md](./memory-design-evidence.md): canonical
records; assertions/lineage; governance events; and rebuildable projections.

## Operating cadence and budgets

- **Per capture:** validate provenance, classification, size, and scope.
- **Per task close:** propose a task summary, decisions, open questions, and
  reusable candidates.
- **Daily idle run:** deduplicate and link only new/changed material; rebuild
  bounded projections; surface exceptions rather than notifications for every
  successful run.
- **Weekly review:** inspect unresolved contradictions, stale practices,
  misleading retrievals, and publication proposals.
- **Monthly pruning:** expire scratch, compact rebuildable indexes, and review
  whether retained memory is actually reused.

Every run has hard limits for records, elapsed time, local compute, output
bytes, and proposals. Exceeding a limit pauses the run with a visible cursor;
it does not silently drop older memory.

## Forgetting, minimization, and archive

Growth consumes two different resources: storage bytes and the local AI's
limited attention. Lifecycle management must optimize both without confusing
"not shown now" with "never existed."

```text
hot      active task context; eligible for default retrieval
  ↓ cool
warm     useful durable memory; retrieved when scope/query matches
  ↓ archive
cold     compressed/index-light history; explicit or deep retrieval only
  ↓ delete (separate governed operation)
tombstone proof that an identity existed and why it is unavailable
```

The operations have deliberately different semantics:

| Operation | What changes | Reversible | Canonical source |
|---|---|---:|---|
| Forget/suppress | Default ranking and active projection | Yes | Preserved |
| Minimize | Duplicate payloads and derived prose are compacted | Yes when originals remain | Preserved |
| Archive | Content moves to cold storage and leaves ordinary indexes | Yes | Preserved |
| Delete | Content is erased for explicit privacy/retention policy | Usually no | Tombstone only where permitted |

Minimization may be lossless (deduplication, shared blobs, index compaction) or
lossy (summary/practice extraction). A lossy derivative never replaces the
only original record. It carries lineage, compression method, model/version,
coverage limits, and a pointer to the archived originals.

Lifecycle decisions use several signals rather than size alone:

- last use, reuse count, and demonstrated downstream benefit;
- redundancy/novelty and whether a stronger derived practice covers it;
- confidence, unresolved contradiction, and dependency from decisions;
- sensitivity, retention/expiry policy, and publication state;
- storage, embedding, retrieval-latency, and attention cost.

Hard safeguards override ranking scores. Evidence cited by an active decision,
the sole support or contradiction for an assertion, content under retention,
and records needed to reproduce an approval cannot be automatically forgotten,
minimized destructively, or archived beyond reach.

Every lifecycle transition is an append-only `MemoryLifecycleEvent` containing
the target, old/new tier, policy/version, observed signals, proposer, approver,
time, and restoration pointer. The consolidation cycle proposes transitions;
policy applies reversible low-risk transitions, while deletion and destructive
compression require explicit approval.

## Learning from remembering and forgetting

Retrieval and lifecycle outcomes form a useful learning signal, but they must
not become opaque self-training:

- positive signal: retrieved, cited, accepted, reused, or prevented an error;
- negative signal: ignored, corrected, misleading, stale, or repeatedly
  displaced from the attention budget;
- counterfactual signal: an archived item had to be restored, indicating that
  the forgetting policy was too aggressive;
- cost signal: capture, consolidation, retrieval, review, and restoration time.

Use these signals first to tune transparent ranking and lifecycle policies.
Model fine-tuning remains a later, separately consented experiment. Deleted,
expired-sensitive, foreign, or private content is never silently retained as
training data. The system must be able to explain why an item was kept,
suppressed, archived, restored, or deleted.

Initial guardrails for evaluation:

- keep all canonical originals during the first 20 eligible tasks;
- allow only reversible suppression and archive simulation during G0–G2;
- measure bytes, indexed tokens, retrieval candidates, Context Pack occupancy,
  restoration rate, and missed-use incidents;
- introduce real cold storage only when either storage exceeds an operator-set
  budget or retrieval latency/attention quality misses its gate;
- roll back the policy if more than 5% of eligible tasks require restoration or
  one high-impact decision misses its necessary evidence.

## Evaluation before implementation

The consolidation layer earns its existence only if, versus unprocessed
Evidence Clips, it achieves at least one of:

- later decision time improves by another 10%;
- Recall@5 improves by at least 10 percentage points;
- repeated-practice suggestions are accepted and successfully reused in at
  least 30% of eligible cases;
- contradiction detection prevents a documented wrong or stale decision.

Stop or narrow it if false merges exceed 5%, correction time erases saved time,
any private scope crosses a boundary, or most accepted summaries are never
reused. First implement only `ConsolidationRun` plus read-only
`MemoryProposal` after the Evidence Clip G2 gate passes; defer automatic
promotion, embeddings, and capsules until measured failures justify them.
