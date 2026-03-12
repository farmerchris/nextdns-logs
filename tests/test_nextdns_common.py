#!/usr/bin/env python3
"""Tests for shared helpers in nextdns_common.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import nextdns_common


def test_get_config_path_uses_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "custom-config.json"
    monkeypatch.setenv(nextdns_common.CONFIG_ENV_VAR, str(cfg))
    assert nextdns_common.get_config_path() == cfg


def test_load_and_save_config_roundtrip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "config.json"
    monkeypatch.setenv(nextdns_common.CONFIG_ENV_VAR, str(cfg))
    assert nextdns_common.load_config() == {}

    out = nextdns_common.save_config({"api_key": "abc"})
    assert out == cfg
    loaded = nextdns_common.load_config()
    assert loaded["api_key"] == "abc"


def test_resolve_api_key_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        nextdns_common, "load_config", lambda: {"api_key": "from-config"}
    )

    assert nextdns_common.resolve_api_key("from-cli") == "from-cli"
    monkeypatch.setenv(nextdns_common.API_KEY_ENV_VAR, "from-env")
    assert nextdns_common.resolve_api_key(None) == "from-env"
    monkeypatch.delenv(nextdns_common.API_KEY_ENV_VAR, raising=False)
    assert nextdns_common.resolve_api_key(None) == "from-config"


def test_resolve_api_key_missing_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "missing-config.json"
    monkeypatch.setenv(nextdns_common.CONFIG_ENV_VAR, str(cfg))
    monkeypatch.delenv(nextdns_common.API_KEY_ENV_VAR, raising=False)
    monkeypatch.setattr(nextdns_common, "load_config", lambda: {})

    with pytest.raises(RuntimeError, match="No API key configured"):
        nextdns_common.resolve_api_key(None)


def test_load_collapse_rules_and_collapse_domain(tmp_path: Path) -> None:
    rules_path = tmp_path / "collapse_rules.json"
    rules_path.write_text(
        json.dumps(
            {"rules": [{"pattern": r"^foo([0-9]+)\.bar([0-9]+)\.example\.com$"}]}
        ),
        encoding="utf-8",
    )
    rules = nextdns_common.load_collapse_rules(str(rules_path))
    out = nextdns_common.collapse_domain("foo123.bar456.example.com", rules)
    assert out == "foo*.bar*.example.com"


def test_load_collapse_rules_invalid_replacement(tmp_path: Path) -> None:
    rules_path = tmp_path / "collapse_rules.json"
    rules_path.write_text(
        json.dumps({"rules": [{"pattern": r"foo([0-9]+)", "replacement": "*"}]}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="replacement"):
        nextdns_common.load_collapse_rules(str(rules_path))
