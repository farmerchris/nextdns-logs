#!/usr/bin/env python3
"""Integration-style tests for profiles.py."""

from __future__ import annotations

import sys

import pytest

import profiles


class FakeProfilesClient:
    def __init__(self, rows: list[dict], error: Exception | None = None) -> None:
        self.rows = rows
        self.error = error

    def __enter__(self) -> "FakeProfilesClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def list_profiles(self) -> list[dict]:
        if self.error:
            raise self.error
        return self.rows


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    client: FakeProfilesClient,
) -> tuple[int, str, str]:
    monkeypatch.setattr(
        profiles.NextDNSClient,
        "from_cli_api_key",
        classmethod(lambda cls, cli_api_key: client),
    )
    monkeypatch.setattr(sys, "argv", ["profiles.py", *args])
    rc = profiles.main()
    out, err = capsys.readouterr()
    return rc, out, err


def test_main_prints_profiles_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, err = run_main(
        monkeypatch,
        capsys,
        [],
        FakeProfilesClient([{"id": "p1", "name": "Home"}]),
    )
    assert rc == 0
    assert "Profile ID" in out
    assert "p1" in out
    assert err == ""


def test_main_handles_no_profiles(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, _ = run_main(monkeypatch, capsys, [], FakeProfilesClient([]))
    assert rc == 0
    assert "No profiles found." in out


def test_main_error_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, _, err = run_main(
        monkeypatch, capsys, [], FakeProfilesClient([], error=RuntimeError("boom"))
    )
    assert rc == 1
    assert "Error: boom" in err
