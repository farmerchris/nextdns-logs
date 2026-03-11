#!/usr/bin/env python3
"""List available NextDNS profiles for the authenticated account."""

from __future__ import annotations

import argparse
import sys
from typing import Dict, List

from nextdns_api import NextDNSClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List available NextDNS profiles.")
    parser.add_argument(
        "--api-key",
        help="NextDNS API key (overrides env/config)",
    )
    return parser.parse_args()


def print_profiles(profiles: List[Dict]) -> None:
    if not profiles:
        print("No profiles found.")
        return

    max_name = max(len(p.get("name", "")) for p in profiles)
    name_col = max(10, min(max_name, 50))

    print(f"{'#':>4}  {'Profile ID':<12}  {'Name':<{name_col}}")
    print("-" * 72)

    for idx, profile in enumerate(profiles, start=1):
        profile_id = profile.get("id", "")
        name = profile.get("name", "")
        print(f"{idx:>4}  {profile_id:<12}  {name:<{name_col}}")


def main() -> int:
    args = parse_args()

    try:
        with NextDNSClient.from_cli_api_key(args.api_key) as client:
            profiles = client.list_profiles()
        print_profiles(profiles)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
