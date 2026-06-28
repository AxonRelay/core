# AxonRelay / core

**[日本語](README.ja.md)** | English

**A governance ledger for mixed human + AI teams, exposed as an MCP server.**

AxonRelay records *who* — human or AI — did *what*, on *which draft version*, and
*who approved or rejected it with what comment, and when*. The agent runtime,
the human-in-the-loop pause, and the UI are all delegated to standards
(LangGraph Platform, MCP, AG-UI). What AxonRelay keeps for itself is the
**durable, cross-session, cross-actor approval & revision ledger** — the part
that those standards do *not* provide.

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
IDE (Claude Code / Cursor / Zed)
   │  MCP (stdio locally; Streamable HTTP via tunnel later)
   ▼
AxonRelay Backend (FastAPI + MCP server, co-located)
   │   - MCP: 14 tools + 2 resources  (primary interface)
   │   - REST: /tasks /agents /actors  (read-heavy, for the dashboard)
   │   - Postgres: projection of Platform thread state → the ledger
   │
   └──▶ LangGraph Platform  (agent runtime / checkpoint / observability)
            └──▶ writer → reviewer → [interrupt: human_approval] → finalize
                     └──▶ LLM (Claude / GPT)
```

| Component | Role |
|-----------|------|
| **MCP server** | Primary interface. Drive tasks / approvals from the IDE. Co-located with the backend ([`backend/app/mcp/`](backend/app/mcp/)). |
| **Backend (FastAPI)** | Thin REST layer + the SQLAlchemy service layer that owns the ledger. |
| **PostgreSQL** | The ledger: Actor / TaskAssignment / Draft (versioned) / Approval / ExternalLink. A projection of Platform thread state. |
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

---

## Interfaces

### MCP server (primary)

14 tools (`list_tasks`, `create_task`, `get_task`, `run_task`,
`list_pending_approvals`, `approve_task`, `reject_task`, `review_pending_task`
(interactive approval via MCP elicitation), `verify_task_ledger`, `get_drafts`,
`list_agents`, `create_agent`, `update_agent`, `get_self_actor`) and 2 resources
(`axonrelay://tasks/{id}`, `axonrelay://tasks/{id}/drafts/{version}`).

Full tool reference and Claude Code setup: **[docs/mcp-server.md](docs/mcp-server.md)**.

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

Writes are also exposed via MCP and are the primary path from the IDE.

---

## Quick Start

```bash
cp .env.example .env          # fill in LANGGRAPH_* and ANTHROPIC_API_KEY
docker compose up -d postgres # Postgres only; runtime is on Platform

# backend (venv recommended)
pip install -r backend/requirements.txt
cd backend && alembic upgrade head   # applies migration 003; seeds the "self" Actor

# run the MCP server (stdio) for the IDE
python -m app.mcp.server
```

Then point Claude Code at it — see [docs/mcp-server.md](docs/mcp-server.md).

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

The pivot is **partially complete** — backend done, frontend/infra cleanup pending:

- ✅ **Backend**: Actor-based ledger, MCP server (14 tools / 2 resources), LangGraph Platform client, migrations through 005. Approval ledger is tamper-evident (per-task SHA-256 hash chain, verifiable via `verify_task_ledger`).
- ✅ **Graph**: `axonrelay-graph/` (writer → reviewer → human_approval → finalize) ready for Platform.
- ✅ **Frontend**: the pre-pivot Next.js (NextAuth, `/projects`, old `/task/start` UI) has been removed. A thin read-only AG-UI dashboard is to be rebuilt from scratch (Phase 2.5).
- 🚧 **Infra**: `infra/` (AWS DNS) and the old `Caddyfile` / production setup are slated for removal (cutover to Cloudflare Tunnel + Tailscale, Phase 2.4).
- 🚧 **Tests**: pytest suite covering the ledger invariants and the run-state projection's idempotency (`backend/tests/`, runs in CI on 3.12). Broader coverage still to come.

Roadmap and migration plan: [docs/step2-plan.md](docs/step2-plan.md). Pivot rationale and scope: [docs/delta-mvp-spec.md](docs/delta-mvp-spec.md).

---

## Docs

- [docs/delta-mvp-spec.md](docs/delta-mvp-spec.md) — pivot spec (Actor model, scope, dogfood scenarios)
- [docs/step2-plan.md](docs/step2-plan.md) — migration plan (Phase 2.1–2.6)
- [docs/mcp-server.md](docs/mcp-server.md) — MCP server connection guide & tool reference
- [docs/discord-setup-guide.md](docs/discord-setup-guide.md) — Discord mobile-approval setup
- [SETUP_POSTGRES.md](SETUP_POSTGRES.md) — PostgreSQL setup & migrations
