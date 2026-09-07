# Security

## What this project does and does not protect

AxonRelay is a personal proof of concept. Its threat model is deliberately
narrow, and you should know where the edges are before running it.

**Authentication is a network boundary, plus one optional secret.** The
REST API (`:8000`) does not check who is calling. The MCP server's Streamable
HTTP transport (`:8765`) does not either, *unless* `AXONRELAY_MCP_TOKEN` is set,
in which case every request must carry `Authorization: Bearer <token>`
([ADR-008](docs/adr-008-optional-bearer-token.md)). Without the token, anyone
who can reach a port can read the ledger, approve tasks, claim territory and
send relays. This is a documented design decision
([coordination-spec §10](docs/coordination-spec.md)): the boundary is drawn at
the network layer, and the token is a second layer for the MCP transport, not
a replacement for the first.

So the rules are:

- Run it on loopback (the default), or inside a private network such as a
  Tailscale tailnet. `docker compose` binds Postgres to loopback and binds the
  backend to `BACKEND_BIND_ADDR` (loopback unless you set it).
- **Never** bind to `0.0.0.0` on a machine with a public interface, and never
  add the MCP port to a public tunnel without an access layer (for example
  Cloudflare Access) in front of it.
- Change the Postgres password from the documented development default
  (`axonrelay_dev`) anywhere other than a laptop.

**Per-caller identity (opt-in).** With `AXONRELAY_REQUIRE_AUTH=1` every HTTP
caller - REST and the MCP Streamable HTTP transport - must present a credential
issued by `python -m app.credentials issue`
([ADR-011](docs/adr-011-caller-identity.md)). The credential names the Actor the
server records for that caller's approvals, sessions and tasks, so a request
parameter cannot claim somebody else's identity, and it carries scopes
(`ledger:read`, `ledger:write`, `coordination:read`, `coordination:write`,
`export:read`, `administration`) that every tool and every route is mapped to.
A scope is necessary, not sufficient: a credential may only drive its own
coordination sessions, and may only approve a task it holds the `approver`
role on (or, for a human Actor, any task). Only the SHA-256 of a token is
stored; tokens never appear in a log, an error or a response. The health check
and FastAPI's schema routes stay open by decision - the schema describes shapes
this repository already publishes. Unset, the process behaves as before and every call runs as the
operator. **stdio is always loopback-trusted**, with or without the flag: it has
no request to carry a credential and the caller is a process you started.

**Content-blind mode.** A shared instance can be run with
`AXONRELAY_SAFE_MODE=1` ([ADR-010](docs/adr-010-safe-envelope.md)). Every
surface that accepts free text then refuses with a fixed message, and the
only write path is a versioned, allowlisted envelope of opaque identifiers,
enums, a source-produced artifact commitment and timestamps
([schema](docs/schemas/safe-envelope-v1.json)). Rejected values are never
stored, logged, echoed in errors or sent onward; canary tests enforce that.
Without the flag the instance is the full-text local PoC and should be treated
as holding whatever was sent to it.

What the application *does* defend:

- The approval ledger is tamper-evident: a per-task SHA-256 hash chain, verified
  by `verify_task_ledger` / `GET /tasks/{id}/ledger/verify`, and serialised
  under a row lock so concurrent approvals cannot fork it.
- All database access goes through the SQLAlchemy ORM; there is no
  string-assembled SQL.
- The REST API has a restrictive CORS allowlist and per-endpoint rate limiting.
- Inputs are validated with Pydantic schemas.

## Reporting a vulnerability

If you find something that breaks the guarantees above - a way to forge or
rewrite a ledger entry, a way past the hash-chain verification, an injection,
a leak of data the model says is private - please report it privately rather
than in a public issue:

- Use **GitHub's private vulnerability reporting** on this repository
  (*Security* tab → *Report a vulnerability*).

You can expect an acknowledgement within a few days. This is a one-person
project, so there is no formal SLA, but reports that come with a reproduction
get fixed first.

Reports about the *absence* of authentication are appreciated but are not
vulnerabilities - see above. If you have a design for adding it that stays
within the project's scope (delegate to standards, do not add infrastructure),
open a discussion instead.

## Supported versions

Only `main`. There are no releases yet; run the tip.
