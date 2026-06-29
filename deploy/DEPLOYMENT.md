# Deployment — Phase 2.4 hosting cutover

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
docker compose exec backend alembic upgrade head
```

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

## 3. Tailscale (private MCP path)

**[you]** Join the home PC and your laptop to a Tailscale tailnet so the IDE can
reach the backend / MCP server privately (e.g. for the Streamable HTTP MCP
transport added in a later phase). The stdio MCP transport stays local and needs
no network.

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
