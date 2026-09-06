# Deployment — Phase 2.4 hosting cutover

**[日本語](DEPLOYMENT.ja.md)** | English

Target topology (zero fixed monthly cost, no public IP on the host):

```
IDE ─ MCP (stdio, or Streamable HTTP via Tailscale) ─▶ AxonRelay backend (home PC / small VPS)
Browser ─▶ app.axonrelay.com  (Vercel / Cloudflare Pages, static dashboard)
            └─ fetches ─▶ api.axonrelay.com  (Cloudflare Tunnel ─▶ backend:8000)
LangGraph Platform (LangSmith Deployment) runs the agent graph.
```

> **Scope.** This repo ships the backend, the graph, the MCP server, and the
> dashboard, plus the tunnel templates below. The account-side actions
> (Cloudflare auth, Tailscale, Vercel, AWS EC2 decommission, DNS) are **operator
> actions** — they touch your accounts and are not automated here. Steps that you
> must run are marked **[you]**.

## 1. Run the backend

On the home PC or a small VPS:

```bash
cp .env.example .env          # fill DATABASE_URL, LANGGRAPH_*, ANTHROPIC_API_KEY
docker compose up -d postgres backend
docker compose exec backend alembic upgrade head   # applies 001..008
```

`alembic upgrade head` must reach `008`. Migrations 002 and 007 fix a chain that
could not apply to a fresh Postgres and an enum-label mismatch that made every
Actor insert fail — an install stopping short of 007 cannot register a session.

## 2. Cloudflare Tunnel  →  api.axonrelay.com

**[you]** Authenticate and create the tunnel (one-time):

```bash
cloudflared tunnel login
cloudflared tunnel create axonrelay
cloudflared tunnel route dns axonrelay api.axonrelay.com
```

Then either:
- **Config-file mode:** copy [`cloudflared.example.yml`](cloudflared.example.yml) to
  `~/.cloudflared/config.yml`, fill in the tunnel id, and `cloudflared tunnel run axonrelay`; or
- **Token mode (sidecar):** put the tunnel token in `.env` as `TUNNEL_TOKEN` and run

  ```bash
  docker compose -f docker-compose.yml -f deploy/docker-compose.tunnel.yml up -d
  ```

The tunnel terminates TLS at Cloudflare and forwards to `backend:8000` — no
inbound ports open on the host.

## 3. Tailscale + the shared MCP endpoint (required for the coordination board)

The coordination board (Phase 3) only means anything if **every device and every
clone talks to the same AxonRelay instance**. One Postgres per laptop gives you
two disconnected boards and no conflict detection at all. stdio cannot do this —
it is one server process per client — so remote devices use Streamable HTTP.

**[you]** Join the host and every device you code on to a Tailscale tailnet.

On the host, run the MCP server on the tailnet interface:

```bash
cd backend
DATABASE_URL="postgresql://axonrelay:axonrelay_dev@localhost:5432/axonrelay" \
  python -m app.mcp.server --http --host 0.0.0.0 --port 8765
```

> **The `DATABASE_URL` differs from the one inside compose.** The containerised
> backend uses `@postgres:5432`, a hostname that only resolves on the Docker
> network. Running the MCP server directly on the host means pointing at the
> published port, `@localhost:5432`, or startup fails with
> `could not translate host name "postgres"`.

> **`--host 0.0.0.0` is only safe behind Tailscale.** By default this transport
> has **no per-caller authentication**: anyone who can reach the port can read
> the ledger and approve tasks. Bind it to the tailnet (or keep the default
> `127.0.0.1` and front it with the tunnel); never expose it to the public
> internet. Do not add `8765` to the Cloudflare Tunnel ingress unless you put
> Cloudflare Access in front of it.

**Second layer (recommended once more than one device is involved):** set
`AXONRELAY_MCP_TOKEN` in the host's environment and every request must carry
`Authorization: Bearer <token>` or gets a 401 before the MCP layer sees it. It
does not replace the tailnet rule; it means a slipped bind or a misrouted
tunnel is not immediately total ([ADR-008](../docs/adr-008-optional-bearer-token.md)).

```bash
export AXONRELAY_MCP_TOKEN="$(openssl rand -hex 32)"   # keep it out of the repository
```

Then point each device's MCP client at the one endpoint. For Claude Code:

```bash
claude mcp add -s user -t http axonrelay http://<host>.<tailnet>.ts.net:8765/mcp \
  -H "Authorization: Bearer <token>"        # omit -H if no token is set
```

Codex CLI takes the same URL (and header) in its own MCP config. Each agent then calls
`register_session` with its own `host` / `clone_path`, and they see each other on
the board.

**Sanity check** from a second device — it should list 26 tools (add
`headers={"Authorization": "Bearer <token>"}` to `streamable_http_client` if
the token is set):

```bash
python - <<'EOF'
import asyncio
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = "http://<host>.<tailnet>.ts.net:8765/mcp"

async def main():
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(streamable_http_client(URL))
        session = await stack.enter_async_context(ClientSession(read, write))
        info = await session.initialize()
        tools = await session.list_tools()
        print(info.server_info.name, len(tools.tools), "tools")

asyncio.run(main())
EOF
```

Keeping it running across reboots is an operator choice — a user launchd agent on
macOS, or a systemd unit on Linux. Nothing in this repo installs one.

The stdio transport still works for a single machine and needs no network:
`python -m app.mcp.server`.

## 3.6 Guarding a shared clone (gitsafe)

If more than one agent works in one clone — or in sibling worktrees of it — put
[`tools/gitsafe`](../tools/gitsafe) on PATH.

**Why:** `refs/stash` is a per-repository ref, so `git worktree add` does *not*
give you a second stash stack. A `git stash pop` in one worktree consumes work
parked in another, and since `git stash pop` names no path, no path claim can
guard it.

```bash
export AXONRELAY_URL="http://<host>.<tailnet>.ts.net:8000"   # needs BACKEND_BIND_ADDR set on the host
export AXONRELAY_SESSION_ID="<id from register_session>"
git() { /path/to/core/tools/gitsafe git "$@"; }
```

> **Reaching the backend.** The port compose publishes for `8000` binds to
> loopback by default. To let another device's `gitsafe` query it, set
> `BACKEND_BIND_ADDR` in the host's `.env` to that host's Tailscale address
> (`100.x.y.z`) and re-run `docker compose up -d backend`. **Never `0.0.0.0`** -
> this API has no per-caller authentication.

When registering the session, pass the clone's git dir so sibling worktrees are
recognised as sharing one stash stack:

```bash
git rev-parse --path-format=absolute --git-common-dir    # -> register_session(git_dir=...)
```

The server resolves a relative `.git` against `clone_path` and normalises the
path; if the checkout sits behind a symlink, run the value through `realpath`.

Read-only git always passes. `stash push` stamps `[axonrelay:s<id>]` into the
message; `pop`/`apply`/`drop` refuse an entry tagged for someone else; `stash
clear` is always refused. `reset --hard`, `clean -f`, a dirty `checkout`,
`rebase`, `branch -D` (the per-clone `refs` resource) and `push --force` /
`+refspec` / `push --delete` (the repo-wide `remote` resource, judged by the push's
actual destination, not by `origin`) consult the board.

The stash tag check works with no network — the tag lives in the stash message —
so it still protects you when AxonRelay is unreachable. An unreachable server
degrades to that layer; a reachable server that answers with an error refuses,
since an error must not read as permission. Bypass once with
`GITSAFE_ALLOW_UNSAFE=1`, which is recorded on stderr.

## 4. Dashboard  →  app.axonrelay.com

**[you]** Deploy `frontend/` to Vercel or Cloudflare Pages:

- Build command: `pnpm build` · Output dir: `dist` · Root: `frontend`
- Env: `VITE_API_BASE=https://api.axonrelay.com`
- Point `app.axonrelay.com` at the deployment.

The dashboard is read-only and same-origin-agnostic; CORS for
`https://app.axonrelay.com` is already allowed by the backend.

## 5. Cut over & decommission EC2

**[you]**, in order, to minimise downtime:

1. Lower the `axonrelay.com` DNS record TTL to ≤300s a day ahead.
2. Bring up the tunnel (step 2) and verify `https://api.axonrelay.com/` returns the health JSON.
3. Repoint `axonrelay.com` / `app.axonrelay.com` to the new dashboard.
4. Verify end-to-end (create a task via MCP, see it in the dashboard).
5. Stop the EC2 instance, snapshot it, then terminate. Cancel the Elastic IP.

## Rollback

Keep the EC2 snapshot until the new stack has run cleanly for a few days. If the
tunnel misbehaves, repoint DNS back to the EC2 IP (TTL is already low).
