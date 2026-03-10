#!/usr/bin/env python3
"""List blocked domains for a NextDNS profile over a time window.

Defaults to the last 24 hours and prints results sorted by blocked query count.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

import requests
from nextdns_common import resolve_api_key

API_BASE = "https://api.nextdns.io"
RELATIVE_TIME_RE = re.compile(r"^\d+(?:[smhdwMy])$")
NEGATIVE_RELATIVE_TIME_RE = re.compile(r"^-\d+(?:[smhdwMy])$")
DEFAULT_COLLAPSE_RULES_PATH = Path(__file__).with_name("collapse_rules.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show blocked domains for a NextDNS profile."
    )
    parser.add_argument(
        "--profile",
        help="NextDNS profile ID (for example: abc123). If omitted, run for all profiles.",
    )
    parser.add_argument(
        "--api-key",
        help="NextDNS API key (overrides env/config)",
    )
    parser.add_argument(
        "--from",
        dest="from_time",
        default="-1d",
        help="Start time (inclusive). Default: -1d",
    )
    parser.add_argument(
        "--to",
        dest="to_time",
        default="now",
        help="End time (exclusive). Default: now",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Page size (1-500). Default: 500",
    )
    parser.add_argument(
        "--root",
        action="store_true",
        help="Ask API to aggregate by root domain when supported",
    )
    parser.add_argument(
        "--new",
        nargs="?",
        const="1h",
        default=None,
        metavar="WINDOW",
        help=(
            "Show domains newly blocked in the recent WINDOW "
            "(default WINDOW: 1h)."
        ),
    )
    parser.add_argument(
        "--collapse-rules",
        default=str(DEFAULT_COLLAPSE_RULES_PATH),
        help=f"Path to collapse rules JSON. Default: {DEFAULT_COLLAPSE_RULES_PATH}",
    )
    parser.add_argument(
        "--no-collapse",
        action="store_true",
        help="Disable domain collapsing",
    )
    return parser.parse_args()


def normalize_time_value(value: str) -> str:
    value = value.strip()
    if value.startswith("-") or value == "now":
        return value
    if RELATIVE_TIME_RE.fullmatch(value):
        return f"-{value}"
    return value


def normalize_window_value(value: str) -> str:
    normalized = normalize_time_value(value)
    if not NEGATIVE_RELATIVE_TIME_RE.fullmatch(normalized):
        raise ValueError("--new must be a relative duration like 1h, 30m, 2d, or -1h")
    return normalized


def list_profiles(api_key: str) -> List[Dict]:
    resp = requests.get(f"{API_BASE}/profiles", headers={"X-Api-Key": api_key}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from NextDNS API: {resp.text.strip()}")

    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(f"API returned errors: {payload['errors']}")
    return payload.get("data", [])


def fetch_blocked_domains(
    profile_id: str,
    api_key: str,
    from_time: str,
    to_time: str,
    limit: int,
    root: bool,
    progress_label: Optional[str] = None,
) -> List[Dict]:
    if not (1 <= limit <= 500):
        raise ValueError("--limit must be between 1 and 500")

    headers = {"X-Api-Key": api_key}
    cursor: Optional[str] = None
    rows: List[Dict] = []
    page = 0

    def progress_update(message: str, *, done: bool = False) -> None:
        if not progress_label:
            return
        sys.stderr.write(f"\r[progress] {progress_label}: {message}")
        if done:
            sys.stderr.write("\n")
        sys.stderr.flush()

    progress_update(f"querying {from_time} -> {to_time}")

    while True:
        page += 1
        params = {
            "status": "blocked",
            "from": from_time,
            "to": to_time,
            "limit": limit,
        }
        if root:
            params["root"] = "1"
        if cursor:
            params["cursor"] = cursor

        url = f"{API_BASE}/profiles/{profile_id}/analytics/domains"
        resp = requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code != 200:
            raise RuntimeError(
                f"HTTP {resp.status_code} from NextDNS API: {resp.text.strip()}"
            )

        payload = resp.json()
        if payload.get("errors"):
            raise RuntimeError(f"API returned errors: {payload['errors']}")

        data = payload.get("data", [])
        rows.extend(data)
        progress_update(f"page {page}, got {len(data)} rows, total {len(rows)}")

        cursor = (
            payload.get("meta", {})
            .get("pagination", {})
            .get("cursor")
        )
        if not cursor:
            break

    rows.sort(key=lambda item: item.get("queries", 0), reverse=True)
    progress_update(f"done ({len(rows)} rows)", done=True)
    return rows


def print_table(rows: List[Dict], from_time: str, to_time: str) -> None:
    print(f"Blocked domains from {from_time} to {to_time}")
    print("=" * 72)

    if not rows:
        print("No blocked domains found for the selected window.")
        return

    max_domain = max(len(item.get("domain", "")) for item in rows)
    domain_col = max(10, min(max_domain, 60))

    print(f"{'#':>4}  {'Blocked':>10}  {'Domain':<{domain_col}}")
    print("-" * 72)

    for idx, item in enumerate(rows, start=1):
        domain = item.get("domain", "")
        queries = item.get("queries", 0)
        print(f"{idx:>4}  {queries:>10}  {domain:<{domain_col}}")


def load_collapse_rules(path: str) -> List[re.Pattern[str]]:
    rules_path = Path(path).expanduser()
    if not rules_path.exists():
        return []

    try:
        data = json.loads(rules_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid collapse rules JSON at {rules_path}: {exc}") from exc

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise RuntimeError(f"Invalid collapse rules format in {rules_path}: 'rules' must be a list")

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
            raise RuntimeError(f"Invalid regex in rule #{idx} ({rules_path}): {exc}") from exc
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


def collapse_rows(rows: List[Dict], rules: List[re.Pattern[str]]) -> List[Dict]:
    if not rules:
        return rows

    totals: Dict[str, int] = {}
    for row in rows:
        domain = row.get("domain", "")
        queries = int(row.get("queries", 0))
        collapsed = collapse_domain(domain, rules)
        totals[collapsed] = totals.get(collapsed, 0) + queries

    collapsed_rows = [{"domain": domain, "queries": queries} for domain, queries in totals.items()]
    collapsed_rows.sort(key=lambda item: item.get("queries", 0), reverse=True)
    return collapsed_rows


def find_new_domains(
    profile_id: str,
    api_key: str,
    limit: int,
    root: bool,
    from_time: str,
    new_window: str,
    collapse_rules: List[re.Pattern[str]],
    progress_prefix: str = "",
) -> List[Dict]:
    recent_from = f"-{new_window.lstrip('-')}"
    recent_rows = fetch_blocked_domains(
        profile_id=profile_id,
        api_key=api_key,
        from_time=recent_from,
        to_time="now",
        limit=limit,
        root=root,
        progress_label=f"{progress_prefix}recent window".strip(),
    )
    baseline_rows = fetch_blocked_domains(
        profile_id=profile_id,
        api_key=api_key,
        from_time=from_time,
        to_time=recent_from,
        limit=limit,
        root=root,
        progress_label=f"{progress_prefix}baseline window".strip(),
    )

    baseline_domains = {
        collapse_domain(row.get("domain", ""), collapse_rules) for row in baseline_rows
    }
    recent_collapsed = collapse_rows(recent_rows, collapse_rules)
    new_rows = [row for row in recent_collapsed if row.get("domain", "") not in baseline_domains]
    new_rows.sort(key=lambda item: item.get("queries", 0), reverse=True)
    return new_rows


def main() -> int:
    args = parse_args()

    try:
        from_time = normalize_time_value(args.from_time)
        to_time = normalize_time_value(args.to_time)
        new_window = normalize_window_value(args.new) if args.new is not None else None
        api_key = resolve_api_key(args.api_key)
        collapse_rules: List[re.Pattern[str]] = []
        if not args.no_collapse:
            collapse_rules = load_collapse_rules(args.collapse_rules)

        if args.profile:
            targets = [{"id": args.profile, "name": ""}]
        else:
            targets = list_profiles(api_key)
            targets = [{"id": p.get("id", ""), "name": p.get("name", "")} for p in targets]
            targets = [t for t in targets if t["id"]]

        if not targets:
            print("No profiles found.")
            return 0

        for idx, target in enumerate(targets, start=1):
            profile_id = target["id"]
            profile_name = target.get("name", "")
            if len(targets) > 1:
                header = f"Profile {idx}/{len(targets)}: {profile_id}"
                if profile_name:
                    header += f" ({profile_name})"
                print(header)

            if new_window is not None:
                rows = find_new_domains(
                    profile_id=profile_id,
                    api_key=api_key,
                    limit=args.limit,
                    root=args.root,
                    from_time=from_time,
                    new_window=new_window,
                    collapse_rules=collapse_rules,
                    progress_prefix=f"({profile_id}) ",
                )
                print_table(rows, new_window, "now")
            else:
                rows = fetch_blocked_domains(
                    profile_id=profile_id,
                    api_key=api_key,
                    from_time=from_time,
                    to_time=to_time,
                    limit=args.limit,
                    root=args.root,
                    progress_label=f"main window ({profile_id})",
                )
                rows = collapse_rows(rows, collapse_rules)
                print_table(rows, from_time, to_time)

            if idx < len(targets):
                print()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
