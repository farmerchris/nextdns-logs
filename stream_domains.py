#!/usr/bin/env python3
"""Stream allowed/blocked domains for a NextDNS profile until Ctrl+C."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from typing import Dict, Iterator, List, Optional, Pattern

import requests

from nextdns_api import NextDNSAPIError, NextDNSClient
from nextdns_common import (
    DEFAULT_COLLAPSE_RULES_PATH,
    collapse_domain,
    load_collapse_rules,
)

ANSI_RESET = "\033[0m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_CYAN = "\033[36m"
ANSI_DIM = "\033[2m"


class SSEEvent:
    def __init__(self) -> None:
        self.event_id: Optional[str] = None
        self.data_lines: List[str] = []

    def add_line(self, line: str) -> None:
        if line.startswith("id:"):
            self.event_id = line[3:].strip()
        elif line.startswith("data:"):
            self.data_lines.append(line[5:].lstrip())

    def is_empty(self) -> bool:
        return self.event_id is None and not self.data_lines

    def payload(self) -> str:
        return "\n".join(self.data_lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream allowed and blocked domains for a NextDNS profile."
    )
    parser.add_argument("--profile", required=True, help="NextDNS profile ID")
    parser.add_argument("--api-key", help="NextDNS API key (overrides env/config)")
    parser.add_argument(
        "--id",
        dest="last_id",
        help="Optional stream event id to resume from",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors",
    )
    parser.add_argument(
        "--no-collapse",
        action="store_true",
        help="Disable domain collapsing",
    )
    parser.add_argument(
        "--collapse-rules",
        default=str(DEFAULT_COLLAPSE_RULES_PATH),
        help=f"Path to collapse rules JSON. Default: {DEFAULT_COLLAPSE_RULES_PATH}",
    )
    return parser.parse_args()


def colorize(text: str, code: str, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{code}{text}{ANSI_RESET}"


def sse_events(response: requests.Response) -> Iterator[SSEEvent]:
    event = SSEEvent()
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue

        line = raw_line.rstrip("\r")
        if line == "":
            if not event.is_empty():
                yield event
                event = SSEEvent()
            continue

        if line.startswith(":"):
            continue

        event.add_line(line)


def format_timestamp(ts: str) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.isoformat()
    except ValueError:
        return ts


def format_event(
    entry: Dict, seen_domains: set[str], use_color: bool, collapse_rules: List[Pattern[str]]
) -> Optional[tuple[tuple[str, str, str], str]]:
    status = entry.get("status", "")
    if status not in {"default", "allowed", "blocked"}:
        return None

    domain = entry.get("domain", "")
    display_domain = collapse_domain(domain, collapse_rules)
    timestamp = format_timestamp(entry.get("timestamp", ""))
    device = ""
    if isinstance(entry.get("device"), dict):
        device_obj = entry["device"]
        device_name = device_obj.get("name", "")
        device_id = device_obj.get("id", "")
        device_model = device_obj.get("model", "")
        if device_name:
            device = device_name
        elif device_id:
            device = device_id
        elif device_model:
            device = device_model

    is_new = display_domain not in seen_domains
    seen_domains.add(display_domain)

    if status == "blocked":
        status_label = "BLOCKED"
        status_color = ANSI_RED
    else:
        # Treat both "default" and explicit "allowed" as allowed traffic.
        status_label = "ALLOWED"
        status_color = ANSI_GREEN

    parts = [
        colorize(timestamp, ANSI_CYAN, use_color),
        colorize(status_label, status_color, use_color),
        display_domain,
    ]
    if device:
        parts.append(colorize(f"device={device}", ANSI_DIM, use_color))
    if is_new:
        parts.append(colorize("NEW", ANSI_YELLOW, use_color))

    line = " | ".join(part for part in parts if part)
    event_key = (status_label, display_domain, device)
    return event_key, line


def print_buffered_line(line: str, repeat_count: int) -> None:
    if repeat_count > 1:
        print(f"{line} x{repeat_count}")
    else:
        print(line)


def open_stream(
    client: NextDNSClient, profile_id: str, last_id: Optional[str]
) -> requests.Response:
    params: Dict[str, str] = {}
    if last_id:
        params["id"] = last_id
    # Use raw logs by default (no dedupe/filtering).
    params["raw"] = "1"

    return client.get_stream(
        f"/profiles/{profile_id}/logs/stream", params=params, timeout=90
    )


def parse_retry_after(headers: Optional[Dict[str, str]]) -> Optional[float]:
    if not headers:
        return None
    value = headers.get("Retry-After", "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def stream_loop(
    client: NextDNSClient,
    profile_id: str,
    last_id: Optional[str],
    use_color: bool,
    collapse_rules: List[Pattern[str]],
) -> None:
    seen_domains: set[str] = set()
    pending_key: Optional[tuple[str, str, str]] = None
    pending_line: Optional[str] = None
    pending_count = 0
    reconnect_delay = 2.0
    max_reconnect_delay = 120.0
    stable_reset_seconds = 30.0

    while True:
        try:
            sys.stderr.write("\r[stream] connecting...\n")
            sys.stderr.flush()
            connected_at = time.monotonic()
            with open_stream(client, profile_id, last_id) as resp:
                sys.stderr.write("\r[stream] connected. Press Ctrl+C to stop.\n")
                sys.stderr.flush()
                for event in sse_events(resp):
                    if event.event_id:
                        last_id = event.event_id

                    payload = event.payload()
                    if not payload:
                        continue

                    try:
                        entry = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    formatted = format_event(
                        entry, seen_domains, use_color, collapse_rules
                    )
                    if formatted is None:
                        continue
                    event_key, line = formatted

                    if pending_key is None:
                        pending_key = event_key
                        pending_line = line
                        pending_count = 1
                        continue

                    if event_key == pending_key:
                        pending_count += 1
                        continue

                    if pending_line is not None:
                        print_buffered_line(pending_line, pending_count)
                    pending_key = event_key
                    pending_line = line
                    pending_count = 1

            connection_lifetime = time.monotonic() - connected_at
            if pending_line is not None:
                print_buffered_line(pending_line, pending_count)
                pending_key = None
                pending_line = None
                pending_count = 0
            if connection_lifetime >= stable_reset_seconds:
                reconnect_delay = 2.0
            else:
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

            sys.stderr.write("\r[stream] disconnected, reconnecting...\n")
            sys.stderr.flush()
            time.sleep(reconnect_delay + random.uniform(0, 1.0))
        except KeyboardInterrupt:
            if pending_line is not None:
                print_buffered_line(pending_line, pending_count)
            raise
        except NextDNSAPIError as exc:
            if pending_line is not None:
                print_buffered_line(pending_line, pending_count)
                pending_key = None
                pending_line = None
                pending_count = 0

            if exc.status_code == 429:
                base = parse_retry_after(exc.headers) or reconnect_delay
                sleep_for = max(base, reconnect_delay, 5.0)
                sleep_for = min(sleep_for, max_reconnect_delay)
                sys.stderr.write(
                    f"\r[stream] rate limited; retrying in {sleep_for:.1f}s\n"
                )
                sys.stderr.flush()
                time.sleep(sleep_for + random.uniform(0, 1.0))
                reconnect_delay = min(
                    max(reconnect_delay * 2, sleep_for), max_reconnect_delay
                )
            else:
                sys.stderr.write(
                    f"\r[stream] API error: {exc}; retrying in {reconnect_delay:.1f}s\n"
                )
                sys.stderr.flush()
                time.sleep(reconnect_delay + random.uniform(0, 1.0))
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
        except Exception as exc:
            if pending_line is not None:
                print_buffered_line(pending_line, pending_count)
                pending_key = None
                pending_line = None
                pending_count = 0
            sys.stderr.write(
                f"\r[stream] error: {exc}; retrying in {reconnect_delay:.1f}s\n"
            )
            sys.stderr.flush()
            time.sleep(reconnect_delay + random.uniform(0, 1.0))
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)


def main() -> int:
    args = parse_args()

    try:
        use_color = (not args.no_color) and sys.stdout.isatty()
        collapse_rules: List[Pattern[str]] = []
        if not args.no_collapse:
            collapse_rules = load_collapse_rules(args.collapse_rules)
        with NextDNSClient.from_cli_api_key(args.api_key) as client:
            stream_loop(
                client=client,
                profile_id=args.profile,
                last_id=args.last_id,
                use_color=use_color,
                collapse_rules=collapse_rules,
            )
    except KeyboardInterrupt:
        sys.stderr.write("\nStopped.\n")
        sys.stderr.flush()
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
