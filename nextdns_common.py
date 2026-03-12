#!/usr/bin/env python3
"""Shared config helpers for NextDNS scripts."""

from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_NAME = "nextdns-logs"
CONFIG_ENV_VAR = "NEXTDNS_LOGS_CONFIG"
API_KEY_ENV_VAR = "NEXTDNS_API_KEY"
DEFAULT_COLLAPSE_RULES_PATH = Path(__file__).with_name("collapse_rules.json")


def get_config_path() -> Path:
    override = os.getenv(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()

    system = platform.system().lower()
    if system == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif system == "windows":
        base = Path(os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))

    return base / APP_NAME / "config.json"


def load_config() -> Dict[str, Any]:
    path = get_config_path()
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid config JSON at {path}: {exc}") from exc


def save_config(config: Dict[str, Any]) -> Path:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    # Best-effort permission hardening for UNIX-like systems.
    if os.name != "nt":
        os.chmod(path, 0o600)

    return path


def resolve_api_key(cli_api_key: Optional[str] = None) -> str:
    if cli_api_key:
        return cli_api_key

    env_api_key = os.getenv(API_KEY_ENV_VAR)
    if env_api_key:
        return env_api_key

    config = load_config()
    config_api_key = config.get("api_key")
    if isinstance(config_api_key, str) and config_api_key.strip():
        return config_api_key.strip()

    path = get_config_path()
    raise RuntimeError(
        "No API key configured. Run register.py or set NEXTDNS_API_KEY. "
        f"Expected config path: {path}"
    )


def load_collapse_rules(path: str) -> List[re.Pattern[str]]:
    rules_path = Path(path).expanduser()
    if not rules_path.exists():
        return []

    try:
        data = json.loads(rules_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid collapse rules JSON at {rules_path}: {exc}"
        ) from exc

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise RuntimeError(
            f"Invalid collapse rules format in {rules_path}: 'rules' must be a list"
        )

    compiled: List[re.Pattern[str]] = []
    for idx, rule in enumerate(raw_rules, start=1):
        if not isinstance(rule, dict):
            raise RuntimeError(f"Invalid rule #{idx} in {rules_path}: expected object")
        pattern = rule.get("pattern")
        if not isinstance(pattern, str):
            raise RuntimeError(
                f"Invalid rule #{idx} in {rules_path}: 'pattern' must be a string"
            )
        if "replacement" in rule:
            raise RuntimeError(
                f"Invalid rule #{idx} in {rules_path}: 'replacement' is not supported; "
                "use capture groups and implicit '*' replacement"
            )
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise RuntimeError(
                f"Invalid regex in rule #{idx} ({rules_path}): {exc}"
            ) from exc
        if regex.groups < 1:
            raise RuntimeError(
                f"Invalid rule #{idx} in {rules_path}: pattern must include at least one capture group"
            )
        compiled.append(regex)

    return compiled


def collapse_domain(domain: str, rules: List[re.Pattern[str]]) -> str:
    for pattern in rules:
        match = pattern.search(domain)
        if not match:
            continue

        collapsed = domain
        for group_index in range(match.lastindex or 0, 0, -1):
            span = match.span(group_index)
            if span == (-1, -1):
                continue
            start, end = span
            collapsed = collapsed[:start] + "*" + collapsed[end:]
        return collapsed
    return domain
