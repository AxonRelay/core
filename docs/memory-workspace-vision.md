# Memory Workspace vision

Status: product direction, not an implementation commitment. Evidence Clips
are the first bounded experiment toward this direction.

This document uses memory metaphors for communication. The evidence boundary
and computer-science translation are explicit in
[memory-design-evidence.md](./memory-design-evidence.md); AxonRelay does not
claim to simulate biological memory.

## Idea

AxonRelay can expose a high-function `memory.md`-like workspace that is at once:

- readable context for a person or local AI;
- a temporary working surface and secondary memory;
- a place where useful knowledge can be promoted into durable memory;
- a controlled boundary for sharing selected context with other people or
  agents.

The important constraint is that this must not become one global document in
which every person's context is silently mixed. `memory.md` is a **view over
scoped memory**, not the universal source of truth.

## Epistemic planes: never flatten fact and interpretation

Each memory layer is split again by what kind of knowledge it contains. These
planes remain addressable independently even when a local AI renders them into
one convenient view:

| Plane | Meaning | Authority |
|---|---|---|
| Original record | Verbatim quote, event, artifact hash, time, and provenance | Immutable evidence of what was observed |
| Source claim | What a named source asserts | Attributed, not automatically true |
| Verified factual assertion | A narrowly stated assertion with verification method and supporting records | Confirmed only within its recorded scope and time |
| Interpretation | AI or human summary, relation, hypothesis, contradiction | Derived and revisable |
| Decision | An explicitly approved conclusion and rationale | Governed commitment, not universal fact |

Every derived item points back to one or more original records and records its
producer, model/prompt version, time, and confidence. Editing an interpretation
never rewrites its source. A source claim can conflict with another source
claim; AxonRelay preserves both attributions instead of manufacturing a single
"fact." Only an explicit verification operation can create a factual
assertion; local-AI output starts as interpretation regardless of confidence.

The local AI may use a multidimensional semantic representation, but an
embedding is a **retrieval coordinate, not memory or truth**. Useful dimensions
include owner, task/workspace, time, provenance, audience, sensitivity,
epistemic plane, confidence, validity, supersession, and semantic vector. Each
vector is namespaced by embedding model/version and can be rebuilt without
changing the underlying record. Exact provenance and symbolic filters run
before similarity search.

## Memory layers

```text
L0 session scratch       disposable notes, short TTL, private
        │ promote
L1 task context          Evidence Clips, open questions, working summaries
        │ confirm
L2 durable memory        approved decisions, reusable rules, provenance
        │ publish explicitly
L3 shared capsule        minimized context for a named audience and purpose
```

Demotion matters as much as promotion. Scratch expires. Durable memory can be
suppressed from ordinary retrieval, minimized with lineage, or moved to a cold
archive before deletion is considered. Shared capsules can expire or be
withdrawn without deleting the originating private record. Lifecycle outcomes,
including mistaken forgetting and later restoration, become policy-learning
signals rather than permission for opaque model training.

## `memory.md` as a projection

A generated Markdown view keeps the interaction portable and understandable:

```markdown
---
owner: actor:self
scope: task:42
audience: private
generated_at: 2026-09-16T...
context_version: sha256:...
---

# Current focus
...

# Working memory
- [M-local-id] candidate observation [E-123]

# Source claims
- [C-...] Source X states ... [E-123]

# Verified facts
- [F-...] Assertion ...; verified by method ... at time ... [E-123]

# Interpretations and hypotheses
- [I-...] Possible implication ...; confidence: low; derived from [E-123]

# Confirmed decisions
- [D-...] Decision and rationale; approved by ...

# Open questions
- [Q-...] ...
```

The database/event history remains authoritative for identity, provenance,
versions, approval, and sharing policy. Direct edits to the Markdown view are
parsed into proposed operations; an AI cannot silently promote, publish, or
delete memory by rewriting the file.

## Isolation and collaboration

Every retrieval requires an explicit scope. There is no implicit `all users`
or `all memories` search.

Minimum scope dimensions:

- owner / issuer;
- Task or Workspace;
- `private`, `task`, or `shared` visibility;
- source classification;
- intended audience and purpose;
- validity period and supersession state.

Collaboration uses a federated Context Capsule. It shares a selected,
human-approved projection rather than the sender's raw memory or retrieval
index. A capsule carries:

- globally namespaced ID (`issuer + provenance hash`);
- issuer and approving Actor;
- audience and purpose;
- minimal content and Evidence references;
- created/expiry timestamps;
- lineage, version, and withdrawal/supersession information.

Recipients import a capsule into a separate foreign-context namespace. Their
local AI may use it for the declared purpose, but it does not become the
recipient's confirmed memory automatically. Contradictory capsules coexist as
attributed claims rather than being merged into a false consensus.

This makes AxonRelay closer to a **federated synapse** than a shared brain.

## Role of local AI

Local AI is a replaceable memory gardener. It may:

- extract candidate annotations from selected material;
- summarize scratch notes;
- suggest links, duplicates, contradictions, and promotion candidates;
- compress a scoped view into a Context Pack;
- propose a shareable capsule.

It may not decide ownership, visibility, audience, approval, deletion, or
cross-boundary publication. Those remain explicit AxonRelay operations.

Adapters may use a browser API, a same-device MCP server, a loopback model, or
an authenticated LAN/tailnet service. The adapter records where inference ran
(`none`, `device`, or `lan`) and which model/prompt produced the proposal.

## Incremental product path

1. **Evidence Clip MVP** — test whether source-grounded recall improves real
   decisions. No generic memory graph.
2. **Task memory view** — generate a bounded `memory.md` from clips, decisions,
   open questions, and current work. Still single-operator.
3. **Promotion/demotion** — explicit scratch → durable rules with approval,
   expiry, and supersession.
4. **Capsule export/import pilot** — only after personal value is proven and
   real collaboration episodes show demand.
5. **Semantic relations** — only if measured failures show that scoped lexical
   recall and structured sections are insufficient. Add model-versioned vectors
   and typed relations as disposable indexes over the planes above, never as a
   replacement for them.

The operational state, consolidation cycle, and minimal future records are
specified in [memory-operations-spec.md](./memory-operations-spec.md).

## Decision gates

Do not proceed from Evidence Clips to a Task memory view unless the personal
G2 gate in [evidence-clips-mvp.md](./evidence-clips-mvp.md) passes.

Do not implement shared capsules unless:

- at least 30% of useful memories are intentionally marked shareable;
- two or more real collaboration episodes need the same context;
- zero private-context leakage has occurred in the personal trial;
- owner, audience, purpose, expiry, and withdrawal semantics are agreed first.

Do not build multi-user global retrieval. If capsule exchange cannot create
collaborative value without mixing private indexes, stop the collective branch
and keep AxonRelay personal-first.
