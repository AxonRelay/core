# AxonRelay / core

**[日本語](README.ja.md)** | English

**A governance ledger and coordination board for mixed human + AI teams, exposed as an MCP server.**

[![CI](https://github.com/AxonRelay/core/actions/workflows/ci.yml/badge.svg)](https://github.com/AxonRelay/core/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776ab.svg)](.python-version)

**In thirty seconds**

- **What it is** - an MCP server your coding agent talks to. It keeps a
  tamper-evident record of *who approved what*, and a shared board of *who is
  editing what right now* - across machines, repositories and clones.
- **Who it is for** - one person running several agents (Claude Code, Codex,
  ...) in parallel, who wants them to stop overwriting each other and wants a
  paper trail a human actually signed.
- **What it needs** - Docker and Python 3.12 for the board and the ledger's
  read side. A LangGraph Platform deployment only if agents should *run* tasks
  through it. No API keys for the first part - see [Quick Start](#quick-start).
- **What it is not** - an agent runtime, an approval UI, or a message bus.
  Those are delegated to LangGraph Platform, MCP elicitation and AG-UI on
  purpose - [why](#why-this-exists-mid-2026-context).

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
*who approved what*.

Regulation points the same way, but later and more narrowly than is often
stated. The EU AI Act's requirements for *high-risk* systems include automatic
event logging (Article 12) and human oversight (Article 14), which overlap in
theme with what an approval ledger records. Those high-risk obligations were
**not** in force on 2026-08-02: that date is the Act's general application
date and the start of its transparency rules (Article 50), while the
Digital Omnibus on AI (Regulation (EU) 2026/1744, in force 2026-07-27)
deferred the high-risk obligations to **2027-12-02** (Annex III systems) and
**2028-08-02** (Annex I systems). Dates, primary sources and the day they were
last checked are in [docs/regulatory-positioning.md](docs/regulatory-positioning.md).

**AxonRelay is not a compliance product and not a certification mechanism.**
It certifies nothing and makes nothing conform to the EU AI Act or any other
regulation; the ledger is tamper-evident, not legally probative. What it
preserves and dogfoods is one idea those requirements share with plain good
engineering: the human is a **first-class Actor** in a durable ledger, not an
exception at the interrupt boundary.

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
                 │   - MCP: 26 tools + 3 resources  (primary interface)
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
| `Draft` | Versioned history of a task's output — the **artifacts** approvals bind to. `(task_id, version)` is unique; each version carries a `commitment` (SHA-256 of its bytes) and, when known, the `producer_actor_id`. |
| `Approval` | Append-only record: `action` (approved/rejected), `comment`, `reviewer_actor_id`, timestamp, **and the artifact it decided on** (`artifact_ref` / `artifact_version` / `artifact_commitment` / producer). Tamper-evident via a per-task SHA-256 hash chain (`prev_hash` / `entry_hash`) that covers the binding too — see [`app/ledger.py`](backend/app/ledger.py) and [ADR-009](docs/adr-009-artifact-commitment.md). |
| `ExternalLink` | Link to an external artifact (e.g. a future MCP resource URI). |

State machine:

```
DRAFT → WAITING_REVIEW → WAITING_APPROVAL → APPROVED → COMPLETED
            │                    │
            └─► NEEDS_REVISION ◄─┘ ─► DRAFT / CANCELLED
```

The ledger is safe for concurrent writers: `record_approval` locks the task row
before reading the chain head, so two agents approving the same task cannot fork
its hash chain. The same lock covers the draft append, so an approval that
carries an edited draft creates the new version first and binds to it.

Three guarantees that are easy to conflate:

- **Tamper-evident events** — an edited or reordered approval, including any of
  its artifact-binding fields, fails `verify_task_ledger`.
- **Artifact identity** — each new approval names the exact draft version and
  content commitment it decided on; a decision made against a superseded draft
  is refused (`409` / `stale_decision`). Whether the stored draft bytes still
  match that commitment is a separate check; the ledger never stores external
  artifact contents. Entries recorded before this binding existed verify as
  what they are and are reported as `not artifact-bound`.
- **Regulatory-grade signing, timestamping and non-repudiation** — out of scope;
  see [docs/regulatory-positioning.md](docs/regulatory-positioning.md).

---

## Data model — the coordination board

| Model | Purpose |
|-------|---------|
| `Workspace` | One checkout of one repo on one machine, identified by `(host, repo, clone_path)`. Two clones of the same repo are two workspaces. |
| `Session` | An Actor working inside a Workspace over a stretch of time. Holds claims, receives relays. Re-registering resumes it, so an agent restart loses nothing. |
| `Claim` | An **advisory, expiring** lease — on paths within a repo, or on a shared git resource (`worktree` / `stash` / `refs` / `remote`) that no path pattern can describe. Overlapping claims are refused by default (`force` overrides, and the override is recorded). |
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

**Coordination — 12 tools**: `register_session`, `heartbeat_session`,
`end_session`, `get_board`, `check_conflicts`, `claim_territory`,
`release_territory`, `claim_git_resource`, `check_git_resource`, `send_relay`,
`read_inbox`, `ack_relay`.

`refs/stash` is a per-repository ref, so sibling git worktrees share one stash
stack and `git stash pop` in one can consume work parked in another — with no
path to guard. [`tools/gitsafe`](tools/gitsafe) enforces the guard mechanically:
it tags stashes with their owning session (which works offline) and consults the
board before destructive git.

**3 resources**: `axonrelay://board`, `axonrelay://tasks/{id}`,
`axonrelay://tasks/{id}/drafts/{version}`.

**Safe Envelope — 2 tools**: `ingest_safe_envelope`, `list_safe_events`. With
`AXONRELAY_SAFE_MODE=1` the instance is content-blind: every tool above that
accepts free text refuses with a fixed message, and the envelope — opaque
identifiers, closed enums, a source-produced artifact commitment, timestamps,
nothing else — is the only write path ([ADR-010](docs/adr-010-safe-envelope.md),
[schema](docs/schemas/safe-envelope-v1.json)). Without the flag this is the
full-text local PoC.

**Identity and scopes.** With `AXONRELAY_REQUIRE_AUTH=1` every HTTP caller
presents a credential (`python -m app.credentials issue`) that names the Actor
the server records for it and carries scopes; every tool and every REST route is
mapped to one, and a structural test refuses an unmapped surface
([ADR-011](docs/adr-011-caller-identity.md)). Unset, and over stdio, calls run as
the operator.

Full tool reference and Claude Code setup: **[docs/mcp-server.md](docs/mcp-server.md)**.

Requires `mcp>=2.1,<3` — the 2.0 release renamed `FastMCP` to `MCPServer`, so the
dependency is pinned.

### REST API (read-heavy, for the dashboard)

Interactive OpenAPI docs at `http://localhost:8000/docs` once the backend is up
(`docker compose up -d backend`, or `uvicorn app.main:app --reload` from `backend/`).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `GET` | `/actors` `/actors/{id}` `/actors/me` | Actors |
| `GET`/`POST`/`PUT`/`DELETE` | `/agents` `/agents/{id}` | AI agent definitions |
| `GET`/`POST`/`PUT`/`DELETE` | `/tasks` `/tasks/{id}` | Tasks |
| `POST`/`GET` | `/envelopes` | Safe Envelope ingestion and listing (content-blind; the only write path under `AXONRELAY_SAFE_MODE`) |
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

**What you need**

| To do this | You need |
|---|---|
| The coordination board and the ledger's read side - `register_session`, `claim_territory`, `send_relay`, `list_tasks`, `verify_task_ledger`, the dashboard | Docker (for Postgres) and Python 3.12. **No API keys.** |
| Create and run tasks, approve and reject drafts - `create_task`, `run_task`, `approve_task`, `reject_task` | A LangGraph Platform deployment of [`axonrelay-graph/`](axonrelay-graph/) plus an LLM key: `LANGGRAPH_*` and `ANTHROPIC_API_KEY` in `.env` |

Creating a task allocates a Platform thread and approving one resumes it, so
those four tools fail with an explicit `PlatformNotConfiguredError` until
Platform is set up. Everything else works from the first minute.

**Five commands**

```bash
cp .env.example .env                    # works as-is on a laptop; add LANGGRAPH_* later
docker compose up -d postgres           # Postgres on 127.0.0.1:5432
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
(cd backend && alembic upgrade head)    # migrations through 011; seeds the "self" Actor
```

Or, with the venv active, `make dev` runs the last three and starts the MCP
server; `make help` lists the rest (`api`, `test`, `lint`, `frontend`).

`.env` is read automatically. Its `DATABASE_URL` points at `localhost`; the
compose network's `postgres` hostname is only used inside the containers.

**Connect Claude Code**

The repository ships a project-scoped [`.mcp.json`](.mcp.json). Start Claude
Code from the repository root with the venv active, and it offers to enable the
`axonrelay` server:

```bash
claude
```

Other clients, the Streamable HTTP transport for several devices, and the full
tool reference: [docs/mcp-server.md](docs/mcp-server.md). Several devices must
share **one** instance for the coordination board to mean anything; serve
`--http` and reach it over Tailscale. The transport has no per-caller auth, so
it must stay on a private network ([deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md)).

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
| `AXONRELAY_REQUIRE_AUTH` | — | `1` requires a per-caller credential on every HTTP call and records the credential's Actor ([ADR-011](docs/adr-011-caller-identity.md)); stdio stays loopback-trusted |
| `AXONRELAY_SAFE_MODE` | — | `1` runs the instance content-blind: free-text surfaces refuse, the Safe Envelope is the only write path ([ADR-010](docs/adr-010-safe-envelope.md)) |
| `AXONRELAY_SAFE_PUBLIC_IDENTIFIERS` | — | `1` accepts envelopes with `identifier_policy: public` (`owner/repo` slugs); otherwise identifiers must be opaque |

See [SETUP_POSTGRES.md](SETUP_POSTGRES.md) for database setup and migrations.

---

## Current state

The pivot is complete; Phase 3 (coordination) is in:

- ✅ **Backend**: Actor-based ledger, MCP server (28 tools / 3 resources), LangGraph Platform client, migrations through 011. Approval ledger is tamper-evident (per-task SHA-256 hash chain, verifiable via `verify_task_ledger`) and safe under concurrent writers — the single-writer limitation recorded in [delta-mvp-spec §11.6](docs/delta-mvp-spec.md) is lifted.
- ✅ **Coordination (Phase 3)**: Workspace / Session / Claim / Relay, driven from MCP, read via `/coordination/*`. Lets several agents across machines, repos and sibling clones see each other, avoid editing the same paths, and leave each other durable messages — [docs/coordination-spec.md](docs/coordination-spec.md).
- ✅ **Graph**: `axonrelay-graph/` (writer → reviewer → human_approval → finalize) ready for Platform.
- ✅ **Frontend**: a thin **read-only** dashboard (Vite + React + TS, [`frontend/`](frontend/)) — task list with status filter, draft history, the approval timeline, and a per-task ledger-verification badge. Write actions stay in the MCP/IDE path. (CopilotKit/AG-UI deferred — a read-only audit viewer doesn't need agent↔UI streaming.)
- 🚧 **Infra**: legacy `infra/` (AWS EC2 DNS) and `Caddyfile` removed. The cutover to Cloudflare Tunnel + Tailscale + Vercel/Pages is templated and documented in [deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md); the account-side steps (DNS switch, EC2 decommission) remain a manual operator action.
- 🚧 **Tests**: 339 SQLite cases (ledger invariants, concurrent-writer safety, projection idempotency, the coordination layer, path-overlap rules, git resource claims, the MCP tool surface) plus 29 Postgres schema-parity cases that apply the real migration chain and race real connections on the approval ledger and on resource claims. Both run in CI; the Postgres job uses a `postgres:16` service. Run it locally with `AXONRELAY_TEST_POSTGRES_URL=... pytest tests/test_postgres_schema.py`.

Roadmap and migration plan: [docs/step2-plan.md](docs/step2-plan.md). Pivot rationale and scope: [docs/delta-mvp-spec.md](docs/delta-mvp-spec.md). Coordination design: [docs/coordination-spec.md](docs/coordination-spec.md).

---

## Docs

- [docs/delta-mvp-spec.md](docs/delta-mvp-spec.md) — pivot spec (Actor model, scope, dogfood scenarios)
- [docs/regulatory-positioning.md](docs/regulatory-positioning.md) — what AxonRelay is *not* (no compliance or certification claims), the EU AI Act application dates with primary sources and the date they were checked, and the docs review checklist
- [docs/coordination-spec.md](docs/coordination-spec.md) — Phase 3: presence, territory claims, relays
- [docs/adr-006-no-message-broker.md](docs/adr-006-no-message-broker.md) — why the coordination layer has no message broker
- [docs/adr-007-gitsafe-enforcement-path.md](docs/adr-007-gitsafe-enforcement-path.md) — why `gitsafe` stays opt-in instead of shadowing `git` on PATH
- [docs/adr-009-artifact-commitment.md](docs/adr-009-artifact-commitment.md) — why every approval names the draft version and content commitment it decided on, and what that does not prove
- [docs/adr-010-safe-envelope.md](docs/adr-010-safe-envelope.md) — the content-blind mode: what the Safe Envelope admits, what every other surface refuses, and how rejected values stay out of storage, logs and errors
- [docs/adr-011-caller-identity.md](docs/adr-011-caller-identity.md) — per-caller credentials and the six scopes: how the recorded Actor stops being a request parameter, and why stdio stays loopback-trusted
- [docs/step2-plan.md](docs/step2-plan.md) — migration plan (Phase 2.1–2.6)
- [docs/mcp-server.md](docs/mcp-server.md) — MCP server connection guide & tool reference
- [docs/discord-setup-guide.md](docs/discord-setup-guide.md) — Discord mobile-approval setup (account side only; the backend side is not built yet)
- [deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md) ([日本語](deploy/DEPLOYMENT.ja.md)) — hosting cutover and the shared MCP endpoint the coordination board needs (Cloudflare Tunnel / Tailscale / Vercel)
- [SETUP_POSTGRES.md](SETUP_POSTGRES.md) — PostgreSQL setup & migrations

---

## License

MIT - see [LICENSE](LICENSE). This is a personal proof of concept: the MCP
transport has no per-caller authentication and is meant to stay on a private
network. Run it accordingly.
