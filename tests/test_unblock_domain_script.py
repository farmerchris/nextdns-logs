#!/usr/bin/env python3
"""Integration-style tests for unblock_domain.py."""

from __future__ import annotations

import sys

import pytest

import unblock_domain


class FakeUnblockClient:
    def __init__(
        self,
        *,
        allowlist: list[dict] | None = None,
        denylist: list[dict] | None = None,
        profiles: list[dict] | None = None,
    ) -> None:
        self.allowlist = allowlist or []
        self.denylist = denylist or []
        self.profiles = profiles or []
        self.calls: list[tuple[str, str, dict | None]] = []

    def __enter__(self) -> "FakeUnblockClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def list_profiles(self) -> list[dict]:
        return self.profiles

    def get_paginated(self, path: str, *, params=None):
        _ = params
        if path.endswith("/allowlist"):
            return list(self.allowlist)
        if path.endswith("/denylist"):
            return list(self.denylist)
        return []

    def request_json(self, method: str, path: str, *, json_body=None, params=None):
        _ = params
        self.calls.append((method, path, json_body))
        return {}


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    client: FakeUnblockClient,
) -> tuple[int, str, str]:
    monkeypatch.setattr(
        unblock_domain.NextDNSClient,
        "from_cli_api_key",
        classmethod(lambda cls, cli_api_key: client),
    )
    monkeypatch.setattr(sys, "argv", ["unblock_domain.py", *args])
    rc = unblock_domain.main()
    out, err = capsys.readouterr()
    return rc, out, err


def test_unblock_on_profile_adds_allow_and_removes_deny() -> None:
    client = FakeUnblockClient(allowlist=[], denylist=[{"id": "example.com"}])
    changed, actions = unblock_domain.unblock_on_profile(client, "p1", "example.com", False)
    assert changed is True
    assert "add allowlist entry" in actions
    assert "remove denylist entry" in actions
    assert ("POST", "/profiles/p1/allowlist", {"id": "example.com", "active": True}) in client.calls
    assert (
        "DELETE",
        "/profiles/p1/denylist/example.com",
        None,
    ) in client.calls


def test_unblock_on_profile_activates_allow_when_inactive() -> None:
    client = FakeUnblockClient(allowlist=[{"id": "example.com", "active": False}], denylist=[])
    changed, actions = unblock_domain.unblock_on_profile(client, "p1", "example.com", False)
    assert changed is True
    assert "activate allowlist entry" in actions
    assert ("PATCH", "/profiles/p1/allowlist/example.com", {"active": True}) in client.calls


def test_unblock_on_profile_no_changes() -> None:
    client = FakeUnblockClient(allowlist=[{"id": "example.com", "active": True}], denylist=[])
    changed, actions = unblock_domain.unblock_on_profile(client, "p1", "example.com", False)
    assert changed is False
    assert "allowlist already active" in actions
    assert "not present in denylist" in actions
    assert client.calls == []


def test_main_all_profiles_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeUnblockClient(
        allowlist=[],
        denylist=[{"id": "example.com"}],
        profiles=[{"id": "p1", "name": "Home"}],
    )
    rc, out, err = run_main(monkeypatch, capsys, ["example.com", "--dry-run"], client)
    assert rc == 0
    assert "[dry-run]" in out
    assert "Profiles changed: 1/1" in out
    assert err == ""
    assert client.calls == []


def test_main_no_target_profiles(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, err = run_main(monkeypatch, capsys, ["example.com"], FakeUnblockClient())
    assert rc == 0
    assert "No target profiles found." in out
    assert err == ""


def test_main_rejects_empty_domain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, _, err = run_main(monkeypatch, capsys, ["   "], FakeUnblockClient())
    assert rc == 1
    assert "domain cannot be empty" in err
