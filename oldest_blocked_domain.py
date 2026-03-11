#!/usr/bin/env python3
"""Find the oldest logged blocked domain for a NextDNS profile."""

from __future__ import annotations

import argparse
import sys
from typing import Dict, Optional

import requests

from nextdns_common import resolve_api_key

API_BASE = "https://api.nextdns.io"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show the oldest blocked log entry for a profile."
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="NextDNS profile ID (for example: abc123)",
    )
    parser.add_argument(
        "--api-key",
        help="NextDNS API key (overrides env/config)",
    )
    parser.add_argument(
        "--from",
        dest="from_time",
        help="Optional start time filter (inclusive)",
    )
    parser.add_argument(
        "--to",
        dest="to_time",
        help="Optional end time filter (exclusive)",
    )
    parser.add_argument(
        "--device",
        help="Optional device ID filter",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Use raw logs (no dedupe/filtering)",
    )
    return parser.parse_args()


def fetch_oldest_blocked(
    profile_id: str,
    api_key: str,
    from_time: Optional[str],
    to_time: Optional[str],
    device: Optional[str],
    raw: bool,
) -> Optional[Dict]:
    params: Dict[str, str] = {
        "status": "blocked",
        "sort": "asc",
        "limit": "10",
    }
    if from_time:
        params["from"] = from_time
    if to_time:
        params["to"] = to_time
    if device:
        params["device"] = device
    if raw:
        params["raw"] = "1"

    url = f"{API_BASE}/profiles/{profile_id}/logs"
    resp = requests.get(url, headers={"X-Api-Key": api_key}, params=params, timeout=30)

    if resp.status_code != 200:
        raise RuntimeError(
            f"HTTP {resp.status_code} from NextDNS API: {resp.text.strip()}"
        )

    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(f"API returned errors: {payload['errors']}")

    data = payload.get("data", [])
    if not data:
        return None
    return data[0]


def main() -> int:
    args = parse_args()

    try:
        api_key = resolve_api_key(args.api_key)
        entry = fetch_oldest_blocked(
            profile_id=args.profile,
            api_key=api_key,
            from_time=args.from_time,
            to_time=args.to_time,
            device=args.device,
            raw=args.raw,
        )

        if entry is None:
            print("No blocked log entries found for the selected scope.")
            return 0

        domain = entry.get("domain", "")
        timestamp = entry.get("timestamp", "")
        status = entry.get("status", "")
        client_ip = entry.get("clientIp", "")
        device_name = (
            entry.get("device", {}).get("name", "")
            if isinstance(entry.get("device"), dict)
            else ""
        )

        print("Oldest blocked log entry")
        print("=" * 32)
        print(f"timestamp: {timestamp}")
        print(f"domain:    {domain}")
        print(f"status:    {status}")
        if client_ip:
            print(f"clientIp:  {client_ip}")
        if device_name:
            print(f"device:    {device_name}")

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
