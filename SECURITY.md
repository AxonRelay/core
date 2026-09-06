# Security

## What this project does and does not protect

AxonRelay is a personal proof of concept. Its threat model is deliberately
narrow, and you should know where the edges are before running it.

**There is no per-caller authentication.** Neither the REST API (`:8000`) nor
the MCP server's Streamable HTTP transport (`:8765`) checks who is calling.
Anyone who can reach the port can read the ledger, approve tasks, claim
territory and send relays. This is a documented design decision
([README](README.md#license), [coordination-spec §10](docs/coordination-spec.md)):
the boundary is drawn at the network layer, not in the application.

So the rules are:

- Run it on loopback (the default), or inside a private network such as a
  Tailscale tailnet. `docker compose` binds Postgres to loopback and binds the
  backend to `BACKEND_BIND_ADDR` (loopback unless you set it).
- **Never** bind to `0.0.0.0` on a machine with a public interface, and never
  add the MCP port to a public tunnel without an access layer (for example
  Cloudflare Access) in front of it.
- Change the Postgres password from the documented development default
  (`axonrelay_dev`) anywhere other than a laptop.

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
