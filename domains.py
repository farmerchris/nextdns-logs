#!/usr/bin/env python3
"""List domains for a NextDNS profile over a time window."""

from __future__ import annotations

import argparse
import re
import sys
from typing import Dict, List, Optional

from nextdns_api import NextDNSClient
from nextdns_common import (
    DEFAULT_COLLAPSE_RULES_PATH,
    collapse_domain,
    load_collapse_rules,
)

RELATIVE_TIME_RE = re.compile(r"^\d+(?:[smhdwMy])$")
NEGATIVE_RELATIVE_TIME_RE = re.compile(r"^-\d+(?:[smhdwMy])$")
ANSI_RESET = "\033[0m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show domains for a NextDNS profile.")
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
        "--blocked",
        action="store_true",
        help="Filter to blocked domains only",
    )
    parser.add_argument(
        "--allowed",
        action="store_true",
        help="Filter to allowed domains only",
    )
    parser.add_argument(
        "--new",
        nargs="?",
        const="1h",
        default=None,
        metavar="WINDOW",
        help=("Show domains new in the recent WINDOW (default WINDOW: 1h)."),
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


def fetch_domains(
    client: NextDNSClient,
    profile_id: str,
    from_time: str,
    to_time: str,
    limit: int,
    root: bool,
    api_status: str,
    output_status: str,
    progress_label: Optional[str] = None,
) -> List[Dict]:
    def progress_update(message: str, *, done: bool = False) -> None:
        if not progress_label:
            return
        sys.stderr.write(f"\r[progress] {progress_label}: {message}")
        if done:
            sys.stderr.write("\n")
        sys.stderr.flush()

    rows = client.analytics_domains(
        profile_id=profile_id,
        from_time=from_time,
        to_time=to_time,
        status=api_status,
        limit=limit,
        root=root,
        progress=progress_update,
    )
    for row in rows:
        row["status"] = output_status
    return rows


def status_text(value: str, use_color: bool) -> str:
    if not use_color:
        return value
    if value == "blocked":
        return f"{ANSI_RED}{value}{ANSI_RESET}"
    if value == "allowed":
        return f"{ANSI_GREEN}{value}{ANSI_RESET}"
    return value


def print_table(
    rows: List[Dict], from_time: str, to_time: str, label: str, use_color: bool
) -> None:
    print(f"{label} from {from_time} to {to_time}")
    print("=" * 72)

    if not rows:
        print("No domains found for the selected window.")
        return

    max_domain = max(len(item.get("domain", "")) for item in rows)
    domain_col = max(10, min(max_domain, 60))

    print(f"{'#':>4}  {'Queries':>10}  {'Status':<8}  {'Domain':<{domain_col}}")
    print("-" * 72)

    for idx, item in enumerate(rows, start=1):
        domain = item.get("domain", "")
        queries = item.get("queries", 0)
        status = item.get("status", "")
        print(
            f"{idx:>4}  {queries:>10}  {status_text(status, use_color):<8}  {domain:<{domain_col}}"
        )


def collapse_rows(rows: List[Dict], rules: List[re.Pattern[str]]) -> List[Dict]:
    if not rules:
        sorted_rows = list(rows)
        sorted_rows.sort(key=lambda item: item.get("queries", 0), reverse=True)
        return sorted_rows

    totals: Dict[tuple[str, str], int] = {}
    for row in rows:
        domain = row.get("domain", "")
        status = row.get("status", "")
        queries = int(row.get("queries", 0))
        collapsed = collapse_domain(domain, rules)
        key = (status, collapsed)
        totals[key] = totals.get(key, 0) + queries

    collapsed_rows = [
        {"status": status, "domain": domain, "queries": queries}
        for (status, domain), queries in totals.items()
    ]
    collapsed_rows.sort(key=lambda item: item.get("queries", 0), reverse=True)
    return collapsed_rows


def find_new_domains(
    client: NextDNSClient,
    profile_id: str,
    limit: int,
    root: bool,
    from_time: str,
    new_window: str,
    status_queries: List[Dict[str, str]],
    collapse_rules: List[re.Pattern[str]],
    progress_prefix: str = "",
) -> List[Dict]:
    recent_from = f"-{new_window.lstrip('-')}"
    recent_rows: List[Dict] = []
    baseline_rows: List[Dict] = []
    for query in status_queries:
        api_status = query["api_status"]
        output_status = query["output_status"]
        recent_rows.extend(
            fetch_domains(
                client=client,
                profile_id=profile_id,
                from_time=recent_from,
                to_time="now",
                limit=limit,
                root=root,
                api_status=api_status,
                output_status=output_status,
                progress_label=f"{progress_prefix}recent window [{api_status}]".strip(),
            )
        )
        baseline_rows.extend(
            fetch_domains(
                client=client,
                profile_id=profile_id,
                from_time=from_time,
                to_time=recent_from,
                limit=limit,
                root=root,
                api_status=api_status,
                output_status=output_status,
                progress_label=f"{progress_prefix}baseline window [{api_status}]".strip(),
            )
        )

    baseline_domains = {
        (row.get("status", ""), collapse_domain(row.get("domain", ""), collapse_rules))
        for row in baseline_rows
    }
    recent_collapsed = collapse_rows(recent_rows, collapse_rules)
    new_rows = [
        row
        for row in recent_collapsed
        if (row.get("status", ""), row.get("domain", "")) not in baseline_domains
    ]
    new_rows.sort(key=lambda item: item.get("queries", 0), reverse=True)
    return new_rows


def main() -> int:
    args = parse_args()

    try:
        from_time = normalize_time_value(args.from_time)
        to_time = normalize_time_value(args.to_time)
        new_window = normalize_window_value(args.new) if args.new is not None else None
        use_color = sys.stdout.isatty()
        status_queries: List[Dict[str, str]]
        if args.blocked and not args.allowed:
            status_queries = [{"api_status": "blocked", "output_status": "blocked"}]
        elif args.allowed and not args.blocked:
            status_queries = [
                {"api_status": "default", "output_status": "allowed"},
                {"api_status": "allowed", "output_status": "allowed"},
            ]
        else:
            status_queries = [
                {"api_status": "blocked", "output_status": "blocked"},
                {"api_status": "default", "output_status": "allowed"},
                {"api_status": "allowed", "output_status": "allowed"},
            ]

        if args.blocked and not args.allowed:
            status_label = "Blocked domains"
        elif args.allowed and not args.blocked:
            status_label = "Allowed domains"
        else:
            status_label = "Domains"

        collapse_rules: List[re.Pattern[str]] = []
        if not args.no_collapse:
            collapse_rules = load_collapse_rules(args.collapse_rules)

        with NextDNSClient.from_cli_api_key(args.api_key) as client:
            if args.profile:
                targets = [{"id": args.profile, "name": ""}]
            else:
                targets = client.list_profiles()
                targets = [
                    {"id": p.get("id", ""), "name": p.get("name", "")} for p in targets
                ]
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
                        client=client,
                        profile_id=profile_id,
                        limit=args.limit,
                        root=args.root,
                        from_time=from_time,
                        new_window=new_window,
                        status_queries=status_queries,
                        collapse_rules=collapse_rules,
                        progress_prefix=f"({profile_id}) ",
                    )
                    window_label = new_window.lstrip("-")
                    print_table(
                        rows,
                        new_window,
                        "now",
                        f"New {status_label.lower()} in last {window_label} (baseline from {from_time})",
                        use_color,
                    )
                else:
                    rows: List[Dict] = []
                    for query in status_queries:
                        api_status = query["api_status"]
                        output_status = query["output_status"]
                        rows.extend(
                            fetch_domains(
                                client=client,
                                profile_id=profile_id,
                                from_time=from_time,
                                to_time=to_time,
                                limit=args.limit,
                                root=args.root,
                                api_status=api_status,
                                output_status=output_status,
                                progress_label=f"main window ({profile_id}) [{api_status}]",
                            )
                        )
                    rows = collapse_rows(rows, collapse_rules)
                    print_table(rows, from_time, to_time, status_label, use_color)

                if idx < len(targets):
                    print()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
