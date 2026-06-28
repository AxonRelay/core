# AxonRelay Dashboard (Phase 2.5)

A thin, **read-only** web view of the governance ledger: tasks, draft history,
the approval timeline, and the tamper-evidence verdict for each task's hash
chain. All write actions (approve / reject / create) happen through the MCP
server in the IDE — this dashboard never mutates state.

Stack: Vite + React + TypeScript. Package manager: **pnpm**.

> AG-UI / CopilotKit are intentionally **not** used here: those standardize
> agent↔UI streaming for interactive copilots, which a read-only audit viewer
> does not need. If an interactive surface is added later, revisit this.

## Develop

```bash
pnpm install
pnpm dev        # proxies /api -> http://localhost:8000 (override: VITE_API_PROXY)
```

## Build / check

```bash
pnpm typecheck  # tsc --noEmit
pnpm lint       # eslint
pnpm build      # tsc --noEmit && vite build -> dist/
```

## Config

| Env | Default | Purpose |
|-----|---------|---------|
| `VITE_API_BASE` | `/api` | REST base the app fetches from (build-time) |
| `VITE_API_PROXY` | `http://localhost:8000` | Dev-server proxy target for `/api` |

## Data source

Reads only: `GET /tasks`, `/tasks/{id}`, `/tasks/{id}/drafts`,
`/tasks/{id}/approvals`, `/tasks/{id}/ledger/verify`, `/agents`.
