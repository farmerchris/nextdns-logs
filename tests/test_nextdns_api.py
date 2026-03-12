#!/usr/bin/env python3
"""Tests for NextDNS API client behavior with a fake HTTP session."""

from __future__ import annotations

from typing import Any, Optional

import pytest

import nextdns_api


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        reason: str = "OK",
        text: str = "",
        payload: Any = None,
        json_error: Optional[Exception] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self.reason = reason
        self.text = text
        self._payload = payload
        self._json_error = json_error
        self.headers = headers or {}

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def test_from_cli_api_key_uses_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nextdns_api, "resolve_api_key", lambda cli: "resolved-key")
    client = nextdns_api.NextDNSClient.from_cli_api_key("ignored")
    assert client.api_key == "resolved-key"


def test_request_json_success_and_headers() -> None:
    session = FakeSession([FakeResponse(payload={"data": [{"id": "p1"}]})])
    client = nextdns_api.NextDNSClient("k", session=session)

    payload = client.request_json("GET", "/profiles")
    assert payload["data"][0]["id"] == "p1"
    assert session.calls[0]["headers"]["X-Api-Key"] == "k"
    assert session.calls[0]["url"].endswith("/profiles")


def test_request_non_200_raises_with_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(
                status_code=400,
                reason="Bad Request",
                text="bad",
                payload={"errors": [{"code": "invalid"}]},
                headers={"Retry-After": "5"},
            )
        ]
    )
    client = nextdns_api.NextDNSClient("k", session=session)

    with pytest.raises(nextdns_api.NextDNSAPIError) as exc:
        client.request_json("GET", "/profiles")
    assert exc.value.status_code == 400
    assert exc.value.errors == [{"code": "invalid"}]
    assert exc.value.headers == {"Retry-After": "5"}


def test_request_json_invalid_json_raises() -> None:
    session = FakeSession(
        [
            FakeResponse(
                status_code=200, text="not-json", json_error=ValueError("bad json")
            )
        ]
    )
    client = nextdns_api.NextDNSClient("k", session=session)

    with pytest.raises(nextdns_api.NextDNSAPIError, match="Invalid JSON response"):
        client.request_json("GET", "/profiles")


def test_request_json_payload_errors_raises() -> None:
    session = FakeSession(
        [FakeResponse(status_code=200, text="{}", payload={"errors": [{"m": "nope"}]})]
    )
    client = nextdns_api.NextDNSClient("k", session=session)
    with pytest.raises(nextdns_api.NextDNSAPIError, match="returned errors"):
        client.request_json("GET", "/profiles")


def test_get_paginated_follows_cursor() -> None:
    session = FakeSession(
        [
            FakeResponse(
                payload={"data": [{"id": 1}], "meta": {"pagination": {"cursor": "c2"}}}
            ),
            FakeResponse(payload={"data": [{"id": 2}], "meta": {"pagination": {}}}),
        ]
    )
    client = nextdns_api.NextDNSClient("k", session=session)
    rows = client.get_paginated("/profiles/p1/allowlist", params={"limit": 1})

    assert [r["id"] for r in rows] == [1, 2]
    assert session.calls[1]["params"]["cursor"] == "c2"


def test_analytics_domains_sorts_and_reports_progress() -> None:
    session = FakeSession(
        [
            FakeResponse(
                payload={
                    "data": [{"domain": "a", "queries": 1}],
                    "meta": {"pagination": {"cursor": "next"}},
                }
            ),
            FakeResponse(
                payload={
                    "data": [{"domain": "b", "queries": 9}],
                    "meta": {"pagination": {}},
                }
            ),
        ]
    )
    client = nextdns_api.NextDNSClient("k", session=session)
    progress: list[tuple[str, bool]] = []

    rows = client.analytics_domains(
        profile_id="p1",
        from_time="-1d",
        to_time="now",
        status="blocked",
        root=True,
        progress=lambda message, done=False: progress.append((message, done)),
    )

    assert [r["domain"] for r in rows] == ["b", "a"]
    assert session.calls[0]["params"]["root"] == "1"
    assert progress[0][0].startswith("querying")
    assert progress[-1][1] is True


def test_analytics_domains_rejects_invalid_limit() -> None:
    client = nextdns_api.NextDNSClient("k", session=FakeSession([]))
    with pytest.raises(ValueError, match="--limit must be between 1 and 500"):
        client.analytics_domains(
            profile_id="p1",
            from_time="-1d",
            to_time="now",
            status="blocked",
            limit=0,
        )
