# nextdns-logs

Small Python CLI tools for querying and managing NextDNS data.

## Requirements

- Python 3.9+
- Dependencies:

```bash
pip install -r requirements.txt
```

## API key setup

All scripts use this API key resolution order:

1. `--api-key` argument
2. `NEXTDNS_API_KEY` environment variable
3. Config file written by `register.py`

Register your key:

```bash
./register.py
```

Or non-interactive:

```bash
./register.py --api-key YOUR_NEXTDNS_API_KEY
```

Show config path:

```bash
./register.py --show-path
```

Default config location:

- macOS: `~/Library/Application Support/nextdns-logs/config.json`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/nextdns-logs/config.json`
- Windows: `%APPDATA%\\nextdns-logs\\config.json`

Override config path with `NEXTDNS_LOGS_CONFIG`.

## Scripts

### `list_profiles.py`

List available profiles for your account.

```bash
./list_profiles.py
```

### `blocked_domains.py`

Show blocked domains ranked by query count.

- If `--profile` is omitted, runs for all profiles in API order.
- Supports collapse rules (`collapse_rules.json`) to aggregate noisy hostnames.
- Shows progress on stderr and updates the same line while paging.

Examples:

```bash
# One profile, last day (default)
./blocked_domains.py --profile abc123

# All profiles, custom range
./blocked_domains.py --from 7d --to now

# New domains in last hour (default window is 1h)
./blocked_domains.py --profile abc123 --from 7d --new

# New domains in last 6h
./blocked_domains.py --profile abc123 --from 7d --new 6h

# Disable collapsing
./blocked_domains.py --profile abc123 --no-collapse
```

Notes:

- Relative times can be passed as `1h`, `24h`, `7d` (leading `-` is added automatically).
- `--new WINDOW` compares:
  - baseline: `[--from, -WINDOW)`
  - recent: `[-WINDOW, now)`

### `unblock_domain.py`

Unblock a domain by ensuring it is active in allowlist and removed from denylist.

- If `--profile` is omitted, applies to all profiles.

```bash
# Single profile
./unblock_domain.py example.com --profile abc123

# All profiles
./unblock_domain.py example.com

# Preview only
./unblock_domain.py example.com --dry-run
```

### `oldest_blocked_domain.py`

Show the oldest blocked log entry for a profile.

```bash
./oldest_blocked_domain.py --profile abc123
```

Optional filters:

```bash
./oldest_blocked_domain.py --profile abc123 --from 2026-03-01 --to now --device DEVICE_ID --raw
```

### `stream_domains.py`

Stream live allowed/blocked domains for a profile until Ctrl+C.

- Colorized output by default on TTY
- Marks first-seen domains in current session with `NEW`
- Prints device info when available
- Auto reconnect on stream interruption

```bash
./stream_domains.py --profile abc123
```

Options:

```bash
./stream_domains.py --profile abc123 --id LAST_EVENT_ID
./stream_domains.py --profile abc123 --no-color
```

## Collapse rules

`blocked_domains.py` reads rules from `collapse_rules.json` by default.

Rules are pattern-only. Each regex must include at least one capture group.
Matched capture groups are replaced with `*`.

Example rule:

```json
{
  "pattern": "^([0-9a-f]+)\\.safeframe\\.googlesyndication\\.com$"
}
```

This collapses:

- `3d38d765678da6047fa288236983f04e.safeframe.googlesyndication.com`

into:

- `*.safeframe.googlesyndication.com`
