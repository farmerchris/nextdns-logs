#!/usr/bin/env python3
"""Integration-style tests for register.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import register


def run_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], args: list[str]
) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", ["register.py", *args])
    rc = register.main()
    out, err = capsys.readouterr()
    return rc, out, err


def test_show_path(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(register, "get_config_path", lambda: Path("/tmp/fake-config.json"))
    rc, out, err = run_main(monkeypatch, capsys, ["--show-path"])
    assert rc == 0
    assert "/tmp/fake-config.json" in out
    assert err == ""


def test_empty_prompted_api_key_is_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(register.getpass, "getpass", lambda prompt: "   ")
    rc, _, err = run_main(monkeypatch, capsys, [])
    assert rc == 1
    assert "API key cannot be empty" in err


def test_save_api_key_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    saved: dict = {}

    def fake_save(config: dict) -> Path:
        saved.update(config)
        return Path("/tmp/config.json")

    monkeypatch.setattr(register, "load_config", lambda: {"other": "keep"})
    monkeypatch.setattr(register, "save_config", fake_save)

    rc, out, err = run_main(monkeypatch, capsys, ["--api-key", "secret"])
    assert rc == 0
    assert err == ""
    assert "Saved API key to /tmp/config.json" in out
    assert saved == {"other": "keep", "api_key": "secret"}
