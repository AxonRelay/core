"""The identity the rate limiter counts by.

Behind Cloudflare Tunnel every request reaches the backend from the tunnel
connector's address, so keying on the TCP peer would count all callers as one
client and throttle everyone together. The real client sits in
``X-Forwarded-For`` - but only when a proxy we control put it there. A caller
hitting the port directly can write that header too, and trusting it there
would let anyone pick their own bucket.

So the header is honoured only when ``AXONRELAY_TRUST_PROXY`` is set, which the
operator does exactly when the port is *not* reachable except through the
proxy. The two settings go together; one without the other is wrong in one of
the two directions.
"""

import os

from fastapi import Request
from slowapi.util import get_remote_address

TRUST_PROXY_ENV = "AXONRELAY_TRUST_PROXY"


def trust_proxy() -> bool:
    return os.environ.get(TRUST_PROXY_ENV, "").strip().lower() in {"1", "true", "yes"}


def client_key(request: Request) -> str:
    """Peer address, or the first ``X-Forwarded-For`` hop when a proxy is trusted."""
    if trust_proxy():
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return get_remote_address(request)
