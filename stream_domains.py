#!/usr/bin/env python3
"""Stream allowed/blocked domains for a NextDNS profile until Ctrl+C."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from typing import Dict, Iterator, List, Optional

import requests

from nextdns_common import resolve_api_key

API_BASE = "https://api.nextdns.io"

ANSI_RESET = "\033[0m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_CYAN = "\033[36m"
ANSI_DIM = "\033[2m"


class RateLimitError(RuntimeError):
    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


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
    entry: Dict, seen_domains: set[str], use_color: bool
) -> Optional[tuple[tuple[str, str, str], str]]:
    status = entry.get("status", "")
    if status not in {"default", "allowed", "blocked"}:
        return None

    domain = entry.get("domain", "")
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

    is_new = domain not in seen_domains
    seen_domains.add(domain)

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
        domain,
    ]
    if device:
        parts.append(colorize(f"device={device}", ANSI_DIM, use_color))
    if is_new:
        parts.append(colorize("NEW", ANSI_YELLOW, use_color))

    line = " | ".join(part for part in parts if part)
    event_key = (status_label, domain, device)
    return event_key, line


def print_buffered_line(line: str, repeat_count: int) -> None:
    if repeat_count > 1:
        print(f"{line} x{repeat_count}")
    else:
        print(line)


def open_stream(
    session: requests.Session, profile_id: str, api_key: str, last_id: Optional[str]
) -> requests.Response:
    params: Dict[str, str] = {}
    if last_id:
        params["id"] = last_id
    # Use raw logs by default (no dedupe/filtering).
    params["raw"] = "1"

    url = f"{API_BASE}/profiles/{profile_id}/logs/stream"
    resp = session.get(
        url,
        headers={"X-Api-Key": api_key},
        params=params,
        timeout=90,
        stream=True,
    )
    if resp.status_code == 429:
        retry_after: Optional[float] = None
        retry_after_header = resp.headers.get("Retry-After", "").strip()
        if retry_after_header:
            try:
                retry_after = float(retry_after_header)
            except ValueError:
                retry_after = None
        raise RateLimitError(
            f"HTTP 429 from NextDNS API: {resp.text.strip()}",
            retry_after=retry_after,
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"HTTP {resp.status_code} from NextDNS API: {resp.text.strip()}"
        )
    return resp


def stream_loop(
    profile_id: str,
    api_key: str,
    last_id: Optional[str],
    use_color: bool,
) -> None:
    seen_domains: set[str] = set()
    pending_key: Optional[tuple[str, str, str]] = None
    pending_line: Optional[str] = None
    pending_count = 0
    reconnect_delay = 2.0
    max_reconnect_delay = 120.0
    stable_reset_seconds = 30.0

    with requests.Session() as session:
        while True:
            try:
                sys.stderr.write("\r[stream] connecting...\n")
                sys.stderr.flush()
                connected_at = time.monotonic()
                with open_stream(session, profile_id, api_key, last_id) as resp:
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

                        formatted = format_event(entry, seen_domains, use_color)
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
            except RateLimitError as exc:
                if pending_line is not None:
                    print_buffered_line(pending_line, pending_count)
                    pending_key = None
                    pending_line = None
                    pending_count = 0
                base = (
                    exc.retry_after if exc.retry_after is not None else reconnect_delay
                )
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
        api_key = resolve_api_key(args.api_key)
        use_color = (not args.no_color) and sys.stdout.isatty()
        stream_loop(
            profile_id=args.profile,
            api_key=api_key,
            last_id=args.last_id,
            use_color=use_color,
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
