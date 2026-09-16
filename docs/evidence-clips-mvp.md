# Evidence Clips MVP — Local AI × AxonRelay

Status: experimental. This does not change AxonRelay into a general memory,
RAG, or knowledge-graph product.

This is the first bounded slice of the broader
[Memory Workspace vision](./memory-workspace-vision.md): a high-function,
`memory.md`-like working surface with private, temporary, durable, and
explicitly shared layers.

## Hypothesis

An operator can make a later governed design decision faster, without reducing
quality, when the draft can retrieve and cite small source excerpts captured
earlier. Optional on-device annotations should improve discovery over raw clips
without becoming trusted facts.

The value is not "the model remembered a page." It is:

> A Draft and its Approval can point back to the exact evidence the operator
> selected, using stable `[E-id]` citations.

## Trust boundary

The current AxonRelay instance remains single-operator. Retrieval is always
scoped to one Task; there is no global context search.

This does not foreclose collaboration. A future collective topology should be
federated rather than merge private context into one shared memory:

```text
private operator node A ── approved Evidence Package ──▶ shared task space
private operator node B ── approved Evidence Package ──▶ shared task space
```

Only an explicitly exported package may cross the boundary. A package must
carry issuer, source classification, purpose, audience, provenance hash,
expiry/withdrawal policy, and approval. Raw browsing context and private
retrieval indexes stay at the originating node. The MVP deliberately does not
implement export or multi-user storage; it preserves the seam by making clips
task-scoped, attributable, source-classified, and independently addressable.

The decisive boundary is therefore **not single user versus group**. It is
**private context versus deliberately published evidence**.

## Included

- Explicit text selection from the active HTTP(S) Chrome tab.
- A preview before every save.
- Optional local-AI annotations with structured output. Chrome Prompt API is
  the first reference adapter, not a core dependency.
- Raw-only fallback when built-in AI is unavailable.
- `EvidenceClip`, append-only feedback, and stable `E-id` references.
- A hard separation between verbatim `quote` and derived `annotations`.
- Task-scoped deterministic retrieval with item and character budgets.
- `relevant`, `irrelevant`, and `misleading` feedback.
- Validation of `[E-id]` citations before a draft is approved.
- Approval-ledger binding of the approved Draft hash and cited Evidence
  manifest (`E-id`, quote hash, sanitized source URL).
- Idempotent decision delivery: the immutable decision and its Platform
  delivery state are recorded separately. `pending` and definite pre-acceptance
  `failed` states are retryable; an atomic claim moves one sender to
  `delivering`; `delivered` is terminal; and `unknown` requires human
  reconciliation because Platform may already have accepted the run.
  Platform run IDs are stored as soon as observed; repeat calls join that run
  and repair the local projection instead of resending. If no run ID was
  observed, `resolve_approval_delivery` requires the operator to confirm the
  Platform outcome before the decision can become retryable or terminal.
- A decision key binds the decision to one approval episode (normally a
  Platform checkpoint/interrupt), not only to the draft bytes. Retrying one
  episode reuses its ledger event, while a later identical approval remains a
  distinct event. REST and MCP decision calls must echo the
  `approval_episode_id` returned with the reviewed Task; stale retries are
  rejected rather than applied to a later, unseen draft.
- A dedicated bearer token for the private Evidence REST surface.

## Excluded

- Background browsing/history capture.
- Workplace data.
- Global or cross-user search.
- Node/edge knowledge graphs and visualization.
- Vector databases, fine-tuning, and continuous learning.
- LLM-generated Context Packs.
- Cloud-model fallback. Falling back to a cloud model silently changes the
  privacy boundary.
- Server-side URL fetching.

## Security properties

Browser-owned fields (`task_id`, URL, title, quote, source type) are not taken
from model output. The Prompt API receives no tools and can only produce the
optional `annotations` object. Page instructions are treated as untrusted data.
The operator reviews annotations before saving.

`AXONRELAY_CAPTURE_TOKEN` must be configured or every Evidence REST endpoint
returns 503. Evidence is never included in the general unauthenticated Task
resource; callers must explicitly request a bounded Context Pack over the
private MCP transport or authenticated REST endpoint. The unpacked extension requests only `activeTab`, `scripting`, and
local storage, and only has host access to loopback HTTP endpoints. Incognito,
non-HTTP(S), and configured blocked-domain capture are refused.

The token is stored in `chrome.storage.local`, which is acceptable only for
this personal PoC. Production requires a pairing flow or OS-backed secret.

## Local inference adapter boundary

AxonRelay does not load, host, select, or call an AI model. A capture client
may enrich the selected quote through any explicitly configured local adapter:

- a browser-managed API such as Chrome Prompt API;
- a same-device MCP server;
- a loopback OpenAI-compatible endpoint, Ollama, llama.cpp, or MLX service;
- an authenticated service reachable only inside the operator's trusted LAN or
  tailnet.

Every adapter receives only the selected quote and the annotation schema. It
returns separately labelled source claims and interpretations plus
provider/model/prompt metadata. A source claim means "the source says this";
it is not independently verified fact. The capture client
shows those annotations to the operator and then sends the reviewed result to
AxonRelay. Model output never controls Task ID, source URL, source type,
visibility, approval, or tool calls.

The stored `inference_location` is `none`, `device`, or `lan`. Device and LAN
results must be measured separately because LAN inference has a different
latency and privacy boundary. A future adapter contract may use MCP, local HTTP,
or a native API; the Evidence Clip schema remains the same.

## Evaluation design

Run 12–20 real design/research tasks, with a 1–7 day delay between capture and
reuse. Rotate three conditions:

- **A — baseline:** no saved clip.
- **B — raw:** Evidence Clip with quote/title only.
- **C — annotated:** the same flow with reviewed on-device annotations.

Pre-register the expected relevant sources for each task, then record:

| Metric | Go threshold |
|---|---:|
| Decision completion time | 15% faster than A |
| Citation-to-source integrity | 100% |
| Fabricated/missing Evidence IDs | 0 |
| Blind review quality | no worse than A |
| Recall@5 | at least 80% |
| Relevant result rate | at least 70% |
| Median capture interaction | at most 5 seconds, model download excluded |
| Local algorithm regression p95 at 10,000 short clips | at most 300 ms |
| MCP response p95, no model work | at most 500 ms |
| Context Pack evidence-item payload | at most 8,000 JSON characters by default |
| Prompt-injection side effects | 0 |
| Prohibited workplace captures | 0 |

If neither completion time, quality, nor later reuse improves, stop before
adding semantic graphs. Keep only evidence citation if that alone proves useful.

### Expected benefit bands

The experiment has two independent bets. Do not average them together.

**Bet 1 — Evidence Clip versus no saved evidence**

| Outcome | Decision-time change | Net time saved per eligible task | Decision quality | Interpretation |
|---|---:|---:|---:|---|
| Breakthrough | 30%+ faster | 10+ minutes | measurably better | Invest in workflow integration |
| Target | 15–30% faster | 5+ minutes | no worse | Continue and improve retrieval |
| Marginal | 5–15% faster | 1–5 minutes | no worse | Narrow to high-value task types |
| No value | under 5% faster | under 1 minute | unchanged or worse | Stop the feature beyond citation |

Net time saved includes capture, annotation review, retrieval, and correction
time. Fast retrieval that required more capture work is not a benefit.

**Bet 2 — reviewed local-AI annotations versus raw Evidence Clips**

Keep local-AI enrichment only if it provides at least one of:

- Recall@5 improves by 10 percentage points or more;
- median decision time improves by another 10%;
- blind-review quality improves without increasing total time.

Otherwise remove annotation from the default path. Raw evidence citation can
still succeed independently.

### Staged gates

| Gate | Sample | Question | Pass |
|---|---:|---|---|
| G0 Safety/feasibility | 5 captures | Can it operate without crossing trust boundaries? | 0 cross-task leaks, 0 prohibited captures, 0 injection side effects, 100% citation integrity |
| G1 Interaction | 20 captures | Is capture light enough to become a habit? | median ≤5s without enrichment; abandonment ≤20%; correction time recorded |
| G2 Personal value | 12–20 eligible tasks | Does saved evidence improve later decisions? | target band above, relevant@5 ≥70%, Recall@5 ≥80%, quality non-inferior |
| G3 Annotation value | matched B/C tasks | Does local AI add value over raw clips? | one of the Bet 2 thresholds, evaluated separately for device and LAN adapters |
| G4 Federation discovery | after G2 only | Is intentionally shared evidence repeatedly useful? | at least 30% of useful clips are deliberately marked shareable and two or more real collaboration episodes request reuse |

Do not build federation at G0–G3. G4 measures demand before implementing a
multi-user or package transport.

### Stop, narrow, or pivot rules

Stop immediately on any of:

- private/personal evidence appears in another Task without explicit action;
- a page instruction causes a tool call, approval, or status change;
- a missing or hash-tampered Evidence ID is accepted as valid;
- workplace data is captured;
- a backend URL fetch or silent cloud-model fallback is introduced.

At G2:

- **Stop annotation, keep raw clips** if B beats A but C does not beat B.
- **Keep citation only** if users cite clips but retrieval does not save time.
- **Narrow to high-stakes decisions** if quality improves but net time does not.
- **Improve retrieval once** if capture/reuse is healthy but Recall@5 misses;
  re-run G2 before adding a graph or vector database.
- **Stop the experiment** if time improves by less than 5%, blind quality does
  not improve, and fewer than 20% of clips are reused after 20 eligible tasks.

### Agile review cadence

- Review every five eligible tasks, not every calendar week.
- Threshold changes are allowed, but record the old value, new value, date, and
  observed reason before looking at the next batch.
- Make only one retrieval or UX change per batch so its effect is attributable.
- Keep failed examples, especially false-positive retrieval and annotation
  corrections; do not train on them during this MVP.
- The operator makes the go/pivot/stop decision. An AI review may summarize the
  measurements but must not move the gate.

Use
[`experiments/chrome-evidence-clip/evaluation-template.csv`](../experiments/chrome-evidence-clip/evaluation-template.csv)
for one row per eligible decision task.

The bundled SQLite benchmark is an algorithm-regression gate, not proof of a
Postgres production SLA. Query length is capped at 500 characters and one
retrieval considers at most 10,000 recent clips. If G2 passes and real task
collections approach that ceiling, benchmark representative quote sizes on
Postgres and add a normalized search column plus `pg_trgm`/GIN candidate
filtering before claiming a production latency SLO.

## MCP flow

1. A human-confirmed capture adapter writes the Evidence Clip through the
   token-protected REST endpoint and receives `E-123`.
2. `get_context_pack(task_id, query, limit=5, char_budget=8000)` returns exact
   quotes, sources, and match reasons; it never generates a summary.
3. The writer cites evidence as `[E-123]`.
4. `validate_evidence_references(task_id, draft)` must report no missing or
   hash-tampered IDs.
5. The operator records `evaluate_evidence_clip(...)` after actual use.

Chrome capture setup is in
[`experiments/chrome-evidence-clip/README.md`](../experiments/chrome-evidence-clip/README.md).

`E-123` is stable only inside one operator node. A future federated package ID
must be namespaced by issuer and provenance hash; importing a package must map
that identity to a local Evidence ref without pretending local numeric IDs are
globally unique.
