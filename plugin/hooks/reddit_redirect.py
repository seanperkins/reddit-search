#!/usr/bin/env python3
"""PreToolUse hook: block WebFetch against reddit.com and redirect to the CLI.

Reddit is unreachable via WebFetch (blocked) and anonymous access returns 403,
so a WebFetch on reddit.com wastes a turn. This intercepts it and tells the agent
to use the `reddit-search` CLI instead. Exit 2 blocks the tool call and feeds the
stderr message back to the model.

Fail-open by design: any parse problem or non-reddit URL exits 0 (allow), so a
hook bug never breaks WebFetch generally.
"""

import json
import sys
from urllib.parse import urlparse


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("tool_name") != "WebFetch":
        return 0
    url = (data.get("tool_input") or {}).get("url", "") or ""
    host = (urlparse(url).hostname or "").lower()
    if host == "reddit.com" or host.endswith(".reddit.com"):
        sys.stderr.write(
            "reddit.com is blocked to WebFetch and anonymous access returns 403. "
            "Use the reddit-search CLI (on PATH via this plugin) instead:\n"
            '  reddit-search query "<text>" [--sub S] [--json]\n'
            "  reddit-search read <post_id|url> [--json]\n"
            "If it reports missing cookies, run: reddit-search refresh-cookies\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
