"""Which address the rate limiter buckets a request under."""

import pytest
from starlette.requests import Request

from app import ratelimit


def _request(client: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (client, 12345)})


def test_peer_address_by_default(monkeypatch):
    monkeypatch.delenv(ratelimit.TRUST_PROXY_ENV, raising=False)
    assert ratelimit.client_key(_request("10.0.0.5", forwarded="203.0.113.9")) == "10.0.0.5"


@pytest.mark.parametrize("value", ["1", "true", "YES", " yes "])
def test_first_forwarded_hop_when_the_proxy_is_trusted(monkeypatch, value):
    monkeypatch.setenv(ratelimit.TRUST_PROXY_ENV, value)
    assert ratelimit.client_key(_request("10.0.0.5", forwarded="203.0.113.9, 10.0.0.5")) == "203.0.113.9"


def test_falls_back_to_peer_when_trusted_but_no_header(monkeypatch):
    monkeypatch.setenv(ratelimit.TRUST_PROXY_ENV, "1")
    assert ratelimit.client_key(_request("10.0.0.5")) == "10.0.0.5"
    assert ratelimit.client_key(_request("10.0.0.5", forwarded="  ")) == "10.0.0.5"


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_other_values_do_not_trust(monkeypatch, value):
    monkeypatch.setenv(ratelimit.TRUST_PROXY_ENV, value)
    assert ratelimit.client_key(_request("10.0.0.5", forwarded="203.0.113.9")) == "10.0.0.5"


def test_the_api_uses_this_key():
    from app.main import limiter

    assert limiter._key_func is ratelimit.client_key
