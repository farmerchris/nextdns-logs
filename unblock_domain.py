#!/usr/bin/env python3
"""Unblock a domain in NextDNS.

If --profile is omitted, applies to all available profiles.
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from nextdns_common import resolve_api_key

API_BASE = "https://api.nextdns.io"


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


def request_json(
    method: str,
    path: str,
    api_key: str,
    params: Optional[Dict] = None,
    json_body: Optional[Dict] = None,
) -> Dict:
    url = f"{API_BASE}{path}"
    resp = requests.request(
        method,
        url,
        headers={"X-Api-Key": api_key},
        params=params,
        json=json_body,
        timeout=30,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"{method} {path} failed (HTTP {resp.status_code}): {resp.text.strip()}")

    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(f"{method} {path} returned errors: {payload['errors']}")
    return payload


def list_profiles(api_key: str) -> List[Dict]:
    payload = request_json("GET", "/profiles", api_key)
    return payload.get("data", [])


def list_array_entries(api_key: str, profile_id: str, endpoint: str) -> List[Dict]:
    cursor: Optional[str] = None
    entries: List[Dict] = []

    while True:
        params: Dict = {}
        if cursor:
            params["cursor"] = cursor

        payload = request_json(
            "GET",
            f"/profiles/{profile_id}/{endpoint}",
            api_key,
            params=params,
        )

        entries.extend(payload.get("data", []))
        cursor = payload.get("meta", {}).get("pagination", {}).get("cursor")
        if not cursor:
            break

    return entries


def unblock_on_profile(api_key: str, profile_id: str, domain: str, dry_run: bool) -> Tuple[bool, List[str]]:
    actions: List[str] = []
    changed = False

    allowlist = list_array_entries(api_key, profile_id, "allowlist")
    existing_allow = next((entry for entry in allowlist if entry.get("id") == domain), None)

    if existing_allow is None:
        actions.append("add allowlist entry")
        changed = True
        if not dry_run:
            request_json(
                "POST",
                f"/profiles/{profile_id}/allowlist",
                api_key,
                json_body={"id": domain, "active": True},
            )
    elif not existing_allow.get("active", False):
        actions.append("activate allowlist entry")
        changed = True
        if not dry_run:
            request_json(
                "PATCH",
                f"/profiles/{profile_id}/allowlist/{quote(domain, safe='')}",
                api_key,
                json_body={"active": True},
            )
    else:
        actions.append("allowlist already active")

    denylist = list_array_entries(api_key, profile_id, "denylist")
    existing_deny = next((entry for entry in denylist if entry.get("id") == domain), None)

    if existing_deny is not None:
        actions.append("remove denylist entry")
        changed = True
        if not dry_run:
            request_json(
                "DELETE",
                f"/profiles/{profile_id}/denylist/{quote(domain, safe='')}",
                api_key,
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
        api_key = resolve_api_key(args.api_key)

        if args.profile:
            targets = [{"id": args.profile, "name": args.profile}]
        else:
            profiles = list_profiles(api_key)
            targets = [{"id": p.get("id", ""), "name": p.get("name", "")} for p in profiles]
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
                api_key=api_key,
                profile_id=target["id"],
                domain=domain,
                dry_run=args.dry_run,
            )
            if changed:
                changed_count += 1

            profile_label = f"{target['id']} ({target['name']})" if target["name"] else target["id"]
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
