#!/usr/bin/env python3
"""Register NextDNS API key into the user config file."""

from __future__ import annotations

import argparse
import getpass
import sys

from nextdns_common import get_config_path, load_config, save_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Save your NextDNS API key to the local user config file."
    )
    parser.add_argument("--api-key", help="NextDNS API key to save")
    parser.add_argument(
        "--show-path",
        action="store_true",
        help="Print config path and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.show_path:
        print(get_config_path())
        return 0

    api_key = args.api_key
    if not api_key:
        api_key = getpass.getpass("NextDNS API key: ").strip()

    if not api_key:
        print("Error: API key cannot be empty", file=sys.stderr)
        return 1

    config = load_config()
    config["api_key"] = api_key
    path = save_config(config)

    print(f"Saved API key to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
