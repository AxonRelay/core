# AxonRelay / core

**[日本語](README.ja.md)** | English

**A governance ledger and coordination board for mixed human + AI teams, exposed as an MCP server.**

AxonRelay records *who* — human or AI — did *what*, on *which draft version*, and
*who approved or rejected it with what comment, and when*. It also answers the
question that comes *before* the decision, once several agents work at once
across several machines and several clones of one repository: **who is working
where, on what, and are we about to collide?**

The agent runtime, the human-in-the-loop pause, and the UI are all delegated to
standards (LangGraph Platform, MCP, AG-UI). What AxonRelay keeps for itself is
the **durable, cross-session, cross-actor ledger and the shared board** — the
parts those standards do *not* provide.

> **Status: personal PoC (post-pivot).** Originally a general-purpose "AI Agent
> Orchestration" stack; that layer has been commoditized by LangGraph Platform +
> MCP + AG-UI, so AxonRelay was re-scoped to the one thing still worth owning:
> the governance ledger. The backend (Actor model, ledger, MCP server, Platform
> client) is migrated. The old Next.js auth/projects UI and AWS infra are being
> removed — see [Current state](#current-state).

---

## Why this exists (mid-2026 context)

By mid-2026 the agentic stack settled into three layers — **MCP** for tools,
**A2A** for agent-to-agent, **AG-UI** for agent↔UI — and "pause to ask a human"
became a native MCP primitive (`elicitation`). Pausing for approval is no longer
a differentiator.

What none of those layers give you is a **persistent record of accountability**:
elicitation is ephemeral and per-session; observability tools log runs, not
*who approved what*. Meanwhile the EU AI Act's high-risk obligations reach full
enforcement on 2026-08-02, and their core asks are exactly: an immutable action
log, a human approval gate for high-impact actions, and attribution of every
action to a responsible identity (human **or** agent).

AxonRelay treats the human as a **first-class Actor** in that ledger, not an
exception at the interrupt boundary. That is the residual value this repo
preserves and dogfoods.

---

## Architecture

```
  Claude @ clone A        Codex @ clone B        Claude @ another repo
  (laptop)                (desktop)              (laptop)
      │                       │                       │
      └───────────────────────┼───────────────────────┘
           MCP (stdio locally · Streamable HTTP over Tailscale/Tunnel)
                              ▼
              AxonRelay Backend (FastAPI + MCP server, co-located)
                 │   - MCP: 24 tools + 3 resources  (primary interface)
                 │   - REST: /tasks /agents /coordination  (read-heavy)
                 │   - Postgres: the ledger + the coordination board
                 │
                 └──▶ LangGraph Platform  (runtime / checkpoint / observability)
                          └──▶ writer → reviewer → [interrupt] → finalize
                                   └──▶ LLM (Claude / GPT)
```

| Component | Role |
|-----------|------|
| **MCP server** | Primary interface. Drive tasks / approvals from the IDE. Co-located with the backend ([`backend/app/mcp/`](backend/app/mcp/)). |
| **Backend (FastAPI)** | Thin REST layer + the SQLAlchemy service layer that owns the ledger. |
| **PostgreSQL** | The ledger (Actor / TaskAssignment / Draft / Approval / ExternalLink — a projection of Platform thread state) **and** the coordination board (Workspace / Session / Claim / Relay). |
| **Coordination board** | Presence, advisory territory claims, and durable messages between agents ([`app/coordination.py`](backend/app/coordination.py)). Pull-based: nothing interrupts a peer. |
| **LangGraph Platform** | Agent runtime, checkpointing, observability. Graph lives in [`axonrelay-graph/`](axonrelay-graph/). (Now branded *LangSmith Deployment*; self-hostable, e.g. via Aegra.) |

The backend is **read-heavy by design**: writes (create / approve / reject) come
through MCP; the REST API is mostly for reading the ledger from a dashboard.

---

## Data model — the ledger

| Model | Purpose |
|-------|---------|
| `Actor` | Unified abstraction for humans and AI. A single human Actor (`name="self"`) is the operator; AI actors are 1:1 with `AgentDefinition`. |
| `TaskAssignment` | Binds an Actor to a Task with a role: `executor` / `reviewer` / `approver` / `observer`. |
| `Draft` | Versioned history of a task's output. |
| `Approval` | Append-only record: `action` (approved/rejected), `comment`, `reviewer_actor_id`, timestamp. Tamper-evident via a per-task SHA-256 hash chain (`prev_hash` / `entry_hash`, see [`app/ledger.py`](backend/app/ledger.py)). |
| `ExternalLink` | Link to an external artifact (e.g. a future MCP resource URI). |

State machine:

```
DRAFT → WAITING_REVIEW → WAITING_APPROVAL → APPROVED → COMPLETED
            │                    │
            └─► NEEDS_REVISION ◄─┘ ─► DRAFT / CANCELLED
```

The ledger is safe for concurrent writers: `record_approval` locks the task row
before reading the chain head, so two agents approving the same task cannot fork
its hash chain.

---

## Data model — the coordination board

| Model | Purpose |
|-------|---------|
| `Workspace` | One checkout of one repo on one machine, identified by `(host, repo, clone_path)`. Two clones of the same repo are two workspaces. |
| `Session` | An Actor working inside a Workspace over a stretch of time. Holds claims, receives relays. Re-registering resumes it, so an agent restart loses nothing. |
| `Claim` | An **advisory, expiring** lease on paths within a repo. Overlapping claims are refused by default (`force` overrides, and the override is recorded). |
| `Relay` | A durable message addressed by audience — one actor, one clone, one repo, or the whole fleet. Delivered by pull. |
| `RelayReceipt` | Per-recipient read/ack state, so one peer acking a broadcast does not hide it from the others. |

Design, semantics, and the per-turn protocol agents follow:
**[docs/coordination-spec.md](docs/coordination-spec.md)**.

---

## Interfaces

### MCP server (primary)

**Ledger — 14 tools**: `list_tasks`, `create_task`, `get_task`, `run_task`,
`list_pending_approvals`, `approve_task`, `reject_task`, `review_pending_task`
(interactive approval via MCP elicitation), `verify_task_ledger`, `get_drafts`,
`list_agents`, `create_agent`, `update_agent`, `get_self_actor`.

**Coordination — 10 tools**: `register_session`, `heartbeat_session`,
`end_session`, `get_board`, `check_conflicts`, `claim_territory`,
`release_territory`, `send_relay`, `read_inbox`, `ack_relay`.

**3 resources**: `axonrelay://board`, `axonrelay://tasks/{id}`,
`axonrelay://tasks/{id}/drafts/{version}`.

Full tool reference and Claude Code setup: **[docs/mcp-server.md](docs/mcp-server.md)**.

Requires `mcp>=2.1,<3` — the 2.0 release renamed `FastMCP` to `MCPServer`, so the
dependency is pinned.

### REST API (read-heavy, for the dashboard)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `GET` | `/actors` `/actors/{id}` `/actors/me` | Actors |
| `GET`/`POST`/`PUT`/`DELETE` | `/agents` `/agents/{id}` | AI agent definitions |
| `GET`/`POST`/`PUT`/`DELETE` | `/tasks` `/tasks/{id}` | Tasks |
| `POST` | `/tasks/{id}/run` | Run on Platform until interrupt/completion |
| `GET` | `/tasks/pending/approvals` | The unified approval inbox |
| `POST` | `/tasks/{id}/approve` `/tasks/{id}/reject` | Approve / reject |
| `GET` | `/tasks/{id}/drafts` | Draft history |
| `GET` | `/tasks/{id}/ledger/verify` | Verify the tamper-evident approval hash chain |
| `GET`/`POST`/`DELETE` | `/tasks/{id}/assignments` | Task assignments |
| `GET` | `/coordination/board` | Active sessions, live claims, open relays |
| `GET` | `/coordination/sessions` `/coordination/claims` | Who is working where; what is claimed |
| `GET` | `/coordination/sessions/{id}/inbox` | A session's relay inbox |

Writes are also exposed via MCP and are the primary path from the IDE.

---

## Quick Start

```bash
cp .env.example .env          # fill in LANGGRAPH_* and ANTHROPIC_API_KEY
docker compose up -d postgres # Postgres only; runtime is on Platform

# backend (venv recommended)
pip install -r backend/requirements.txt
cd backend && alembic upgrade head   # applies migrations through 006; seeds the "self" Actor

# run the MCP server for the IDE
python -m app.mcp.server                      # stdio — one machine
python -m app.mcp.server --http --port 8765   # Streamable HTTP — several devices
```

Then point Claude Code at it — see [docs/mcp-server.md](docs/mcp-server.md).

Several devices must share **one** instance for the coordination board to mean
anything. Serve `--http` and reach it over Tailscale or a Cloudflare Tunnel; the
transport has no per-caller auth, so it must stay on a private network
([deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md)).

To run the LangGraph graph locally before deploying to Platform:

```bash
cd axonrelay-graph && pip install -e . && langgraph dev
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://axonrelay:axonrelay_dev@postgres:5432/axonrelay` | Postgres connection string |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | `axonrelay` / `axonrelay` / `axonrelay_dev` | Postgres bootstrap (change password outside local) |
| `LANGGRAPH_API_URL` | — | LangGraph Platform endpoint (required to run tasks) |
| `LANGGRAPH_API_KEY` | — | Platform API key |
| `LANGGRAPH_ASSISTANT_ID` | `axonrelay` | Deployed graph / assistant id |
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` (used by the graph) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | LLM credentials |
| `WRITER_MODEL` / `REVIEWER_MODEL` | `claude-sonnet-4-6` / `claude-haiku-4-5-20251001` | Per-role models |
| `DISCORD_*` | — | Mobile approval via Discord (Phase 2.6, not yet wired) |

See [SETUP_POSTGRES.md](SETUP_POSTGRES.md) for database setup and migrations.

---

## Current state

The pivot is complete; Phase 3 (coordination) is in:

- ✅ **Backend**: Actor-based ledger, MCP server (24 tools / 3 resources), LangGraph Platform client, migrations through 006. Approval ledger is tamper-evident (per-task SHA-256 hash chain, verifiable via `verify_task_ledger`) and safe under concurrent writers — the single-writer limitation recorded in [delta-mvp-spec §11.6](docs/delta-mvp-spec.md) is lifted.
- ✅ **Coordination (Phase 3)**: Workspace / Session / Claim / Relay, driven from MCP, read via `/coordination/*`. Lets several agents across machines, repos and sibling clones see each other, avoid editing the same paths, and leave each other durable messages — [docs/coordination-spec.md](docs/coordination-spec.md).
- ✅ **Graph**: `axonrelay-graph/` (writer → reviewer → human_approval → finalize) ready for Platform.
- ✅ **Frontend**: a thin **read-only** dashboard (Vite + React + TS, [`frontend/`](frontend/)) — task list with status filter, draft history, the approval timeline, and a per-task ledger-verification badge. Write actions stay in the MCP/IDE path. (CopilotKit/AG-UI deferred — a read-only audit viewer doesn't need agent↔UI streaming.)
- 🚧 **Infra**: legacy `infra/` (AWS EC2 DNS) and `Caddyfile` removed. The cutover to Cloudflare Tunnel + Tailscale + Vercel/Pages is templated and documented in [deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md); the account-side steps (DNS switch, EC2 decommission) remain a manual operator action.
- 🚧 **Tests**: 107 SQLite cases (ledger invariants, concurrent-writer safety, projection idempotency, the coordination layer, path-overlap rules, the MCP tool surface) plus 16 Postgres schema-parity cases that apply the real migration chain and race real connections on the approval ledger. Both run in CI; the Postgres job uses a `postgres:16` service. Run it locally with `AXONRELAY_TEST_POSTGRES_URL=... pytest tests/test_postgres_schema.py`.

Roadmap and migration plan: [docs/step2-plan.md](docs/step2-plan.md). Pivot rationale and scope: [docs/delta-mvp-spec.md](docs/delta-mvp-spec.md). Coordination design: [docs/coordination-spec.md](docs/coordination-spec.md).

---

## Docs

- [docs/delta-mvp-spec.md](docs/delta-mvp-spec.md) — pivot spec (Actor model, scope, dogfood scenarios)
- [docs/coordination-spec.md](docs/coordination-spec.md) — Phase 3: presence, territory claims, relays
- [docs/adr-006-no-message-broker.md](docs/adr-006-no-message-broker.md) — why the coordination layer has no message broker
- [docs/step2-plan.md](docs/step2-plan.md) — migration plan (Phase 2.1–2.6)
- [docs/mcp-server.md](docs/mcp-server.md) — MCP server connection guide & tool reference
- [docs/discord-setup-guide.md](docs/discord-setup-guide.md) — Discord mobile-approval setup
- [deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md) — Phase 2.4 hosting cutover (Cloudflare Tunnel / Tailscale / Vercel)
- [SETUP_POSTGRES.md](SETUP_POSTGRES.md) — PostgreSQL setup & migrations
