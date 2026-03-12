#!/usr/bin/env python3
"""Integration-style tests for stream_domains.py with mocked stream responses."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import stream_domains


class FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __enter__(self) -> "FakeStreamResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def iter_lines(self, decode_unicode: bool = True):
        _ = decode_unicode
        for line in self._lines:
            yield line
        raise KeyboardInterrupt


class FakeStreamClient:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __enter__(self) -> "FakeStreamClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get_stream(self, path: str, *, params=None, timeout: int = 90) -> FakeStreamResponse:
        _ = (path, params, timeout)
        return FakeStreamResponse(self._lines)


def make_event(event_id: str, entry: dict) -> list[str]:
    return [f"id: {event_id}", f"data: {json.dumps(entry)}", ""]


def run_stream_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    client: FakeStreamClient,
) -> tuple[int, str, str]:
    monkeypatch.setattr(
        stream_domains.NextDNSClient,
        "from_cli_api_key",
        classmethod(lambda cls, cli_api_key: client),
    )
    monkeypatch.setattr(sys, "argv", ["stream_domains.py", *args])
    rc = stream_domains.main()
    out, err = capsys.readouterr()
    return rc, out, err


def test_stream_default_collapse_dedupes_collapsed_domains(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rules_path = tmp_path / "collapse_rules.json"
    rules_path.write_text(
        json.dumps({"rules": [{"pattern": r"foo([0-9]+)\.example\.com$"}]}),
        encoding="utf-8",
    )
    lines = (
        make_event(
            "1",
            {
                "status": "blocked",
                "domain": "foo123.example.com",
                "timestamp": "2026-03-12T00:00:00Z",
            },
        )
        + make_event(
            "2",
            {
                "status": "blocked",
                "domain": "foo999.example.com",
                "timestamp": "2026-03-12T00:00:01Z",
            },
        )
    )
    client = FakeStreamClient(lines)
    rc, out, _ = run_stream_main(
        monkeypatch,
        capsys,
        ["--profile", "p1", "--collapse-rules", str(rules_path), "--no-color"],
        client,
    )

    assert rc == 0
    assert "foo*.example.com" in out
    assert "x2" in out


def test_stream_no_collapse_keeps_distinct_domains(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rules_path = tmp_path / "collapse_rules.json"
    rules_path.write_text(
        json.dumps({"rules": [{"pattern": r"foo([0-9]+)\.example\.com$"}]}),
        encoding="utf-8",
    )
    lines = (
        make_event(
            "1",
            {
                "status": "blocked",
                "domain": "foo123.example.com",
                "timestamp": "2026-03-12T00:00:00Z",
            },
        )
        + make_event(
            "2",
            {
                "status": "blocked",
                "domain": "foo999.example.com",
                "timestamp": "2026-03-12T00:00:01Z",
            },
        )
    )
    client = FakeStreamClient(lines)
    rc, out, _ = run_stream_main(
        monkeypatch,
        capsys,
        [
            "--profile",
            "p1",
            "--collapse-rules",
            str(rules_path),
            "--no-collapse",
            "--no-color",
        ],
        client,
    )

    assert rc == 0
    assert "foo123.example.com" in out
    assert "foo999.example.com" in out
    assert "x2" not in out
