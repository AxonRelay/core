"""Issue, list and revoke API credentials (issue #27).

    python -m app.credentials issue --actor self --scopes ledger:read,ledger:write
    python -m app.credentials list
    python -m app.credentials revoke 3

`issue` prints the token **once**. It is not stored and cannot be recovered:
the row keeps only its SHA-256 (app/authz.py). Losing it means issuing another
and revoking this one, which is the intended failure mode - a system that can
show you an old token can also leak it.

The Actor is named, not invented: `--actor` must match an existing Actor, so a
credential cannot conjure an identity the ledger has never seen.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from app import authz, models
from app.database import SessionLocal


def _resolve_actor(db, name: str) -> models.Actor:
    actor = db.query(models.Actor).filter(models.Actor.name == name).order_by(models.Actor.id.asc()).first()
    if actor is None:
        known = ", ".join(a.name for a in db.query(models.Actor).order_by(models.Actor.id.asc()).limit(20))
        raise SystemExit(f"no Actor named {name!r}. Known actors: {known or '(none)'}")
    return actor


def _issue(args: argparse.Namespace) -> int:
    scopes = authz.parse_scopes(args.scopes)
    if not scopes:
        raise SystemExit(f"--scopes must name at least one of: {', '.join(s.value for s in authz.Scope)}")
    db = SessionLocal()
    try:
        actor = _resolve_actor(db, args.actor)
        token = authz.issue_token()
        credential = models.Credential(
            actor_id=actor.id,
            label=args.label or f"{actor.name}-{datetime.utcnow():%Y%m%d}",
            token_hash=authz.token_digest(token),
            scopes=authz.format_scopes(scopes),
        )
        db.add(credential)
        db.commit()
        db.refresh(credential)
        print(f"credential #{credential.id} for actor {actor.name!r} (id={actor.id})")
        print(f"scopes: {credential.scopes}")
        print("\nToken (shown once, store it now):\n")
        print(f"  {token}\n")
        print("Use it as:  Authorization: Bearer <token>")
        print(f"Enforcement is on only while {authz.REQUIRE_AUTH_ENV}=1.")
    finally:
        db.close()
    return 0


def _list(args: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        rows = db.query(models.Credential).order_by(models.Credential.id.asc()).all()
        if not rows:
            print("no credentials issued")
            return 0
        print(f"{'id':>3}  {'actor':<20} {'scopes':<60} {'state':<9} last used")
        for row in rows:
            actor = db.query(models.Actor).filter(models.Actor.id == row.actor_id).first()
            state = "revoked" if row.revoked_at else "active"
            used = row.last_used_at.isoformat() if row.last_used_at else "never"
            print(f"{row.id:>3}  {(actor.name if actor else '?'):<20} {row.scopes:<60} {state:<9} {used}")
    finally:
        db.close()
    return 0


def _revoke(args: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        row = db.query(models.Credential).filter(models.Credential.id == args.credential_id).first()
        if row is None:
            raise SystemExit(f"no credential #{args.credential_id}")
        if row.revoked_at is None:
            row.revoked_at = datetime.utcnow()
            db.commit()
        print(f"credential #{row.id} revoked")
    finally:
        db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.credentials", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue", help="issue a credential for an existing Actor")
    issue.add_argument("--actor", required=True, help="Actor name (e.g. 'self', or an agent's name)")
    issue.add_argument("--scopes", required=True, help=f"comma-separated: {', '.join(s.value for s in authz.Scope)}")
    issue.add_argument("--label", help="a name for this credential (default: actor + date)")
    issue.set_defaults(func=_issue)

    listing = sub.add_parser("list", help="list credentials (never their tokens)")
    listing.set_defaults(func=_list)

    revoke = sub.add_parser("revoke", help="revoke a credential by id")
    revoke.add_argument("credential_id", type=int)
    revoke.set_defaults(func=_revoke)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
