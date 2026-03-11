#!/usr/bin/env python3
"""Unblock a domain in NextDNS.

If --profile is omitted, applies to all available profiles.
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Tuple
from urllib.parse import quote

from nextdns_api import NextDNSClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unblock a domain for one profile or all profiles."
    )
    parser.add_argument("domain", help="Domain to unblock (for example: example.com)")
    parser.add_argument(
        "--profile",
        help="NextDNS profile ID. If omitted, applies to all profiles.",
    )
    parser.add_argument(
        "--api-key",
        help="NextDNS API key (overrides env/config)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show actions without modifying anything",
    )
    return parser.parse_args()


def unblock_on_profile(
    client: NextDNSClient, profile_id: str, domain: str, dry_run: bool
) -> Tuple[bool, List[str]]:
    actions: List[str] = []
    changed = False

    allowlist = client.get_paginated(f"/profiles/{profile_id}/allowlist")
    existing_allow = next(
        (entry for entry in allowlist if entry.get("id") == domain), None
    )

    if existing_allow is None:
        actions.append("add allowlist entry")
        changed = True
        if not dry_run:
            client.request_json(
                "POST",
                f"/profiles/{profile_id}/allowlist",
                json_body={"id": domain, "active": True},
            )
    elif not existing_allow.get("active", False):
        actions.append("activate allowlist entry")
        changed = True
        if not dry_run:
            client.request_json(
                "PATCH",
                f"/profiles/{profile_id}/allowlist/{quote(domain, safe='')}",
                json_body={"active": True},
            )
    else:
        actions.append("allowlist already active")

    denylist = client.get_paginated(f"/profiles/{profile_id}/denylist")
    existing_deny = next(
        (entry for entry in denylist if entry.get("id") == domain), None
    )

    if existing_deny is not None:
        actions.append("remove denylist entry")
        changed = True
        if not dry_run:
            client.request_json(
                "DELETE",
                f"/profiles/{profile_id}/denylist/{quote(domain, safe='')}",
            )
    else:
        actions.append("not present in denylist")

    return changed, actions


def main() -> int:
    args = parse_args()
    domain = args.domain.strip().lower()

    if not domain:
        print("Error: domain cannot be empty", file=sys.stderr)
        return 1

    try:
        with NextDNSClient.from_cli_api_key(args.api_key) as client:
            if args.profile:
                targets = [{"id": args.profile, "name": args.profile}]
            else:
                profiles = client.list_profiles()
                targets = [
                    {"id": p.get("id", ""), "name": p.get("name", "")} for p in profiles
                ]
                targets = [t for t in targets if t["id"]]

            if not targets:
                print("No target profiles found.")
                return 0

            print(
                f"Unblocking domain '{domain}' for {len(targets)} profile(s)"
                + (" [dry-run]" if args.dry_run else "")
            )
            print("=" * 72)

            changed_count = 0
            for idx, target in enumerate(targets, start=1):
                changed, actions = unblock_on_profile(
                    client=client,
                    profile_id=target["id"],
                    domain=domain,
                    dry_run=args.dry_run,
                )
                if changed:
                    changed_count += 1

                profile_label = (
                    f"{target['id']} ({target['name']})"
                    if target["name"]
                    else target["id"]
                )
                print(f"{idx:>3}. {profile_label}")
                print("     - " + "; ".join(actions))

            print("=" * 72)
            print(f"Profiles changed: {changed_count}/{len(targets)}")

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
