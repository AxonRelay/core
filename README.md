# AxonRelay / core

**[日本語](README.ja.md)** | English

AI Agent Orchestration with Human-in-the-Loop — Core System

---

## Phase 1 Roadmap

> 🔴 Not Started  🟡 In Progress  🟢 Done

```mermaid
flowchart LR
    subgraph M1["M1: Project Foundation"]
        I1["#1 .gitignore\n.env.example 🟢"]
        I2["#2 README.md 🟢"]
        I3["#3 docker-compose.yml 🟢"]
        I4["#4 Caddyfile 🟢"]
    end

    subgraph M2["M2: Backend"]
        I5["#5 Dockerfile 🟢"]
        I6["#6 Python Dependencies 🟢"]
        I7["#7 FastAPI Scaffold 🟢"]
        I8["#8 State Schema 🟢"]
        I9["#9 LangGraph Graph 🟢"]
        I10["#10 Redis Integration 🟢"]
        I11["#11 API: start/status 🟢"]
        I12["#12 API: approve 🟢"]
    end

    subgraph M3["M3: Frontend"]
        I13["#13 Next.js Setup 🟢"]
        I14["#14 Task Input UI 🟢"]
        I15["#15 Approval UI 🟢"]
    end

    subgraph M4["M4: Docker Integration"]
        I16["#16 Full Stack Launch 🟢"]
        I17["#17 E2E Flow Validation 🟢"]
    end

    subgraph M5["M5: AWS Deployment"]
        I18["#18 EC2 Setup 🟢"]
        I19["#19 DNS Configuration 🟢"]
        I20["#20 Production Deploy 🟢"]
    end

    %% Dependencies
    I1 --> I3
    I1 --> I5
    I1 --> I13
    I3 --> I16
    I4 --> I16

    I5 --> I7
    I6 --> I7
    I7 --> I8
    I8 --> I9
    I9 --> I10
    I10 --> I11
    I11 --> I12

    I13 --> I14
    I14 --> I15

    I12 --> I16
    I15 --> I16
    I16 --> I17

    I17 --> I18
    I18 --> I19
    I19 --> I20

    %% Styles
    style M1 fill:#1e293b,stroke:#facc15,color:#fef9c3
    style M2 fill:#1e293b,stroke:#ef4444,color:#fecaca
    style M3 fill:#1e293b,stroke:#a855f7,color:#e9d5ff
    style M4 fill:#1e293b,stroke:#3b82f6,color:#bfdbfe
    style M5 fill:#1e293b,stroke:#22c55e,color:#bbf7d0
```

### Progress

**M1 → M2/M3 (parallel) → M4 → M5** in this order.

| Milestone | Description | Issue | Status |
|-----------|-------------|-------|--------|
| **M1: Project Foundation** | Docker/Reverse Proxy/Environment Variables | #1 #2 #3 #4 | 🟢 |
| **M2: Backend** | FastAPI + LangGraph + Redis | #5 #6 #7 #8 #9 #10 #11 #12 | 🟢 |
| **M3: Frontend** | Next.js Human-in-the-Loop Dashboard | #13 #14 #15 | 🟢 |
| **M4: Docker Integration** | Full Stack Launch + E2E Validation | #16 #17 | 🟢 |
| **M5: AWS Deployment** | EC2 + DNS + SSL + Production | #18 #19 #20 | 🟢 |

### Current Status

> **Phase 1 Complete. Running in production at https://axonrelay.com. Moving to Phase 2.**

---

## Quick Start

```bash
git clone https://github.com/AxonRelay/core.git
cd core
cp .env.example .env
docker compose up --build
```

Dashboard will be available at http://localhost

---

## Architecture

```
Browser ──▶ Caddy (:80) ──┬──▶ /api/* ──▶ Backend (FastAPI :8000)
                           │                    │
                           │                    ▼
                           │               Redis (checkpoint)
                           │               PostgreSQL (database)
                           │
                           └──▶ /*     ──▶ Frontend (Next.js :3000)
```

| Service | Role |
|---------|------|
| **Caddy** | Reverse proxy. HTTP for local, auto-HTTPS for production |
| **Backend** | FastAPI + LangGraph. Task management and Human-in-the-Loop |
| **Frontend** | Next.js dashboard. Task submission and approval UI |
| **Redis** | LangGraph state persistence (checkpoint) |
| **PostgreSQL** | User, project, and task data storage |

---

## API

### Core API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/` | Health check |
| `POST` | `/api/task/start` | Start task. AI generates draft and waits for approval |
| `GET` | `/api/task/{thread_id}` | Get task status |
| `POST` | `/api/task/approve` | Approve (optionally modify) draft and resume processing |

### Authentication API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/sync` | Sync OAuth user to database |

### Project API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/projects` | List user's projects |
| `POST` | `/api/projects` | Create new project (creates owner) |
| `GET` | `/api/projects/{id}` | Get project details with members |
| `PUT` | `/api/projects/{id}` | Update project (requires OWNER/ADMIN) |
| `DELETE` | `/api/projects/{id}` | Delete project (requires OWNER) |
| `POST` | `/api/projects/{id}/members` | Add member (requires OWNER/ADMIN) |
| `PATCH` | `/api/projects/{id}/members/{user_id}` | Update member role (requires OWNER/ADMIN) |
| `DELETE` | `/api/projects/{id}/members/{user_id}` | Remove member (requires OWNER/ADMIN) |

### Flow

```
POST /task/start  ──▶  AI generates draft  ──▶  status: waiting_approval
                                                        │
                                              Human reviews & edits
                                                        │
POST /task/approve ──▶  Resume with updated draft ──▶  status: completed
```

---

## Environment Variables

### Core System

| Variable | Default | Description |
|----------|---------|-------------|
| `CADDY_SITE_ADDRESS` | `:80` | Local: `:80`, Production: `yourdomain.com` (auto-HTTPS) |
| `OPENAI_API_KEY` | — | Required for LLM functionality (currently using dummy response) |
| `REDIS_URL` | `redis://redis:6379` | Auto-connected in docker-compose |

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://axonrelay:axonrelay_dev@postgres:5432/axonrelay` | PostgreSQL connection string |
| `POSTGRES_DB` | `axonrelay` | Database name |
| `POSTGRES_USER` | `axonrelay` | Database user |
| `POSTGRES_PASSWORD` | `axonrelay_dev` | Database password (change in production!) |

### Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_SECRET` | — | NextAuth.js secret (generate with `openssl rand -base64 32`) |
| `NEXTAUTH_URL` | `http://localhost` | NextAuth.js base URL |
| `GOOGLE_CLIENT_ID` | — | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | — | Google OAuth client secret |

See [SETUP_GOOGLE_OAUTH.md](SETUP_GOOGLE_OAUTH.md) for Google OAuth setup instructions.

---

## Database Setup

See [SETUP_POSTGRES.md](SETUP_POSTGRES.md) for PostgreSQL setup and migration instructions.

### Running Migrations

```bash
# Inside backend container
docker compose exec backend alembic upgrade head
```

---

## Links

- [Project Board](https://github.com/orgs/AxonRelay/projects/1)
