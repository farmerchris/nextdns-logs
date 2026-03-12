#!/usr/bin/env python3
"""Integration-style tests for oldest_blocked_domain.py."""

from __future__ import annotations

import sys

import pytest

import oldest_blocked_domain


class FakeOldestClient:
    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self.payload = payload if payload is not None else {"data": []}
        self.error = error
        self.calls: list[tuple[str, str, dict]] = []

    def __enter__(self) -> "FakeOldestClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def request_json(self, method: str, path: str, *, params=None):
        if self.error:
            raise self.error
        self.calls.append((method, path, dict(params or {})))
        return self.payload


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    client: FakeOldestClient,
) -> tuple[int, str, str]:
    monkeypatch.setattr(
        oldest_blocked_domain.NextDNSClient,
        "from_cli_api_key",
        classmethod(lambda cls, cli_api_key: client),
    )
    monkeypatch.setattr(sys, "argv", ["oldest_blocked_domain.py", *args])
    rc = oldest_blocked_domain.main()
    out, err = capsys.readouterr()
    return rc, out, err


def test_fetch_oldest_blocked_builds_params() -> None:
    client = FakeOldestClient(payload={"data": [{"domain": "x"}]})
    entry = oldest_blocked_domain.fetch_oldest_blocked(
        client=client,
        profile_id="p1",
        from_time="2026-03-01",
        to_time="now",
        device="dev1",
        raw=True,
    )
    assert entry == {"domain": "x"}
    _, _, params = client.calls[0]
    assert params["status"] == "blocked"
    assert params["sort"] == "asc"
    assert params["raw"] == "1"
    assert params["device"] == "dev1"


def test_main_no_entries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, err = run_main(
        monkeypatch,
        capsys,
        ["--profile", "p1"],
        FakeOldestClient(payload={"data": []}),
    )
    assert rc == 0
    assert "No blocked log entries found" in out
    assert err == ""


def test_main_prints_entry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {
        "data": [
            {
                "timestamp": "2026-03-01T00:00:00Z",
                "domain": "bad.example.com",
                "status": "blocked",
                "clientIp": "1.2.3.4",
                "device": {"name": "Laptop"},
            }
        ]
    }
    rc, out, err = run_main(monkeypatch, capsys, ["--profile", "p1"], FakeOldestClient(payload))
    assert rc == 0
    assert "bad.example.com" in out
    assert "Laptop" in out
    assert err == ""


def test_main_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, _, err = run_main(
        monkeypatch,
        capsys,
        ["--profile", "p1"],
        FakeOldestClient(error=RuntimeError("kaput")),
    )
    assert rc == 1
    assert "Error: kaput" in err
