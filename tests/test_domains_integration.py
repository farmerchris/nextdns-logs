#!/usr/bin/env python3
"""Integration-style tests for domains.py with mocked API responses."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

import domains


class FakeDomainsClient:
    def __init__(self, rows_by_status: dict[str, list[dict]]) -> None:
        self.rows_by_status = rows_by_status

    def __enter__(self) -> "FakeDomainsClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def list_profiles(self) -> list[dict]:
        return []

    def analytics_domains(
        self,
        *,
        profile_id: str,
        from_time: str,
        to_time: str,
        status: str,
        limit: int = 500,
        root: bool = False,
        progress=None,
    ) -> list[dict]:
        _ = (profile_id, from_time, to_time, limit, root, progress)
        return list(self.rows_by_status.get(status, []))


def run_domains_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    client: FakeDomainsClient,
) -> tuple[int, str, str]:
    monkeypatch.setattr(
        domains.NextDNSClient,
        "from_cli_api_key",
        classmethod(lambda cls, cli_api_key: client),
    )
    monkeypatch.setattr(sys, "argv", ["domains.py", *args])
    rc = domains.main()
    out, err = capsys.readouterr()
    return rc, out, err


def test_default_collapse_uses_rules_and_aggregates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rows = {
        "blocked": [
            {"domain": "foo123.example.com", "queries": 3},
            {"domain": "foo999.example.com", "queries": 2},
            {"domain": "bar.example.com", "queries": 6},
        ]
    }
    client = FakeDomainsClient(rows_by_status=rows)
    rules_path = tmp_path / "collapse_rules.json"
    rules_path.write_text(
        json.dumps({"rules": [{"pattern": r"foo([0-9]+)\.example\.com$"}]}),
        encoding="utf-8",
    )
    rc, out, _ = run_domains_main(
        monkeypatch,
        capsys,
        [
            "--profile",
            "p1",
            "--blocked",
            "--collapse-rules",
            str(rules_path),
        ],
        client,
    )

    assert rc == 0
    assert "foo*.example.com" in out
    assert "foo123.example.com" not in out
    assert "foo999.example.com" not in out


def test_no_collapse_still_sorts_by_queries_desc(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = {
        "blocked": [
            {"domain": "low.example.com", "queries": 1},
            {"domain": "high.example.com", "queries": 99},
            {"domain": "mid.example.com", "queries": 25},
        ]
    }
    client = FakeDomainsClient(rows_by_status=rows)
    rc, out, _ = run_domains_main(
        monkeypatch,
        capsys,
        ["--profile", "p1", "--blocked", "--no-collapse"],
        client,
    )

    assert rc == 0
    data_lines = [
        line for line in out.splitlines() if re.match(r"^\s*\d+\s+\d+\s+", line)
    ]
    queries = [int(line.split()[1]) for line in data_lines]
    assert queries == sorted(queries, reverse=True)
    assert "high.example.com" in data_lines[0]
