# Evidence-grounded memory design

Status: design synthesis, not a claim that AxonRelay reproduces a brain.

## Method

Neuroscience is used to identify useful functional constraints, not software
components. Computer-science patterns determine the implementation. Claims are
classified as established enough to guide design, plausible analogy requiring
evaluation, or deliberately not adopted.

## Neuroscience: what transfers and what does not

| Research-level observation | Design implication | What it does **not** justify |
|---|---|---|
| Complementary Learning Systems distinguishes rapid episode acquisition from slower extraction of shared structure. | Preserve exact experience separately from gradually promoted practices. Interleave new and prior examples when proposing generalization. | Calling database tables “hippocampus” and “cortex,” or automatically treating repeated text as truth. |
| Replay/reactivation is associated with consolidation during sleep and quiet wakefulness; mechanisms and selectivity remain active research topics. | A bounded offline process may revisit recent records and propose links/generalizations. | Copying a sleep schedule, replaying everything, or allowing an unattended model to overwrite canonical memory. |
| Retrieval can make memories plastic again (reconsolidation literature). | Version interpretations and decisions when reused; preserve the earlier state and the retrieval event. | Editing the original observation to match the latest interpretation. |
| Forgetting can be adaptive and can alter accessibility rather than simply erase all traces. | Separate retrieval suppression, archive, restoration, and deletion; measure restoration and missed-use rates. | Assuming every inaccessible biological memory is recoverable, or using the analogy to evade deletion obligations. |
| Generalized/schema-like memory loses episodic detail. | Keep a practice's supporting and contradicting episodes reachable; summaries are lossy derivatives. | Replacing all originals with a concise summary merely because it reads well. |

The practical synthesis is therefore **fast immutable capture + slow proposed
generalization + reversible accessibility changes**. “Dream cycle” remains a
product metaphor for this workflow, not a scientific mechanism claim.

## Computer science: implementation anchors

| Established pattern/standard | AxonRelay use | Restraint |
|---|---|---|
| W3C PROV models entities, activities, and agents for provenance. | Record source/derived entity, producing activity, responsible actor, and lineage. | Start with a small relational subset; do not require RDF/OWL for the MVP. |
| Event sourcing retains intent/history and rebuilds materialized views, but adds concurrency, deletion, schema, and operational complexity. | Use append-only events selectively for approvals, lifecycle transitions, publication, and consolidation runs. | Do not event-source ordinary configuration or every read interaction. |
| Materialized views separate canonical history from query-optimized representations. | Treat `memory.md`, lexical indexes, embeddings, and Context Packs as rebuildable projections. | A projection never becomes the sole canonical record. |
| Sparse and dense retrieval have different strengths and must be evaluated on the actual corpus. | Begin with scoped lexical retrieval; test plane-specific dense/hybrid retrieval only against a preregistered baseline. | Do not assume semantic vectors universally outperform lexical search. |
| Tiered storage promotes hot data and demotes cold data to balance latency and cost. | Use hot/warm/cold as an operational storage/retrieval policy with restoration metrics. | Storage temperature is not epistemic confidence or importance. |
| RAG keeps retrievable knowledge outside model weights. | Supply bounded, cited Context Packs to replaceable local models. | Do not call retrieval “learning,” and do not silently fine-tune on private memory. |

## Recommended middle architecture

The earlier six conceptual records collapse into four implementation
boundaries for the first post-MVP version:

1. **Canonical record store** — immutable Evidence/experience content, source,
   owner, scope, sensitivity, and content hash.
2. **Assertion and lineage store** — source claims, verified facts,
   interpretations, decisions, and practices with typed support/contradiction
   edges and validity state.
3. **Governance event ledger** — approval, promotion, supersession, lifecycle,
   publication, restoration, and consolidation-run events.
4. **Projection/index store** — `memory.md`, Context Packs, lexical index, and
   optional model-versioned vectors; all disposable and rebuildable.

A local consolidation worker reads a fixed scope/watermark and emits proposals.
It may automatically rebuild projections. It cannot create verified facts,
promote practices, cross a sharing boundary, or delete canonical content.

This is intentionally between two extremes:

- more structured and auditable than one mutable `memory.md` or an embedding
  database;
- materially smaller than a full knowledge graph, brain simulation, continuous
  learner, or organization-wide shared memory.

## Testable hypotheses, not borrowed biological constants

Sampling cadence, evidence counts, confidence thresholds, storage budgets, and
forgetting thresholds are product hypotheses. Neuroscience does not supply
values such as “three examples” or “daily consolidation” for this software.
AxonRelay must estimate them from task outcomes and publish threshold changes.

The initial experiment should compare:

- raw Evidence Clips;
- scoped structured assertions without embeddings;
- the same structure plus plane-specific dense/hybrid retrieval.

Measure decision time, Recall@5, citation integrity, correction effort,
false-merge rate, missed-use/restoration rate, and privacy-boundary violations.
Adopt the more complex condition only when its incremental value exceeds its
capture, review, compute, and operational cost.

## Sources

- McClelland, McNaughton & Lampinen, *Integration of new information in memory: new insights from a complementary learning systems perspective* (2020): https://pmc.ncbi.nlm.nih.gov/articles/PMC7209926/
- Carr, Jadhav & Frank, *Hippocampal replay in the awake state* (2011): https://www.nature.com/articles/nn.2732
- Nader & Hardt, *A single standard for memory: the case for reconsolidation* (2009): https://www.nature.com/articles/nrn2590
- Ryan & Frankland, *Forgetting as a form of adaptive engram cell plasticity* (2022): https://www.nature.com/articles/s41583-021-00548-3
- W3C, *PROV Overview*: https://www.w3.org/TR/prov-overview/
- Microsoft Azure Architecture Center, *Event Sourcing pattern*: https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing
- Karpukhin et al., *Dense Passage Retrieval for Open-Domain Question Answering* (2020): https://aclanthology.org/2020.emnlp-main.550/
- NIST, *Retrieval-Augmented Generation*: https://csrc.nist.gov/glossary/term/retrieval_augmented_generation
- Qiu et al., *HotRAP: Hot Record Retention and Promotion for LSM-trees with Tiered Storage* (2025): https://www.usenix.org/system/files/atc25-qiu.pdf
