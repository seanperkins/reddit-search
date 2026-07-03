---
name: reddit-search
description: Use when a task needs Reddit content (searching threads, reading a discussion) and direct reddit.com access is blocked — reddit.com is unreachable via WebFetch and anonymous .json returns 403. This plugin ships a cookie-authenticated CLI (`reddit-search`) that rides the user's logged-in session.
---

# Reddit search (agent-facing)

Do NOT use WebFetch on reddit.com (blocked) or fetch anonymous `.json` (403).
Use the bundled `reddit-search` CLI — it's on PATH while this plugin is enabled:

```bash
reddit-search query "<text>" [--sub S] [--sort relevance|hot|top|new|comments] [--time hour|day|week|month|year|all] [--limit N] [--json]
reddit-search read <post_id|url> [--limit N] [--max-more N] [--json]
```

- `query` searches live (global, or one subreddit with `--sub`).
- `read` prints a post's selftext + top comments; deep threads are expanded,
  bounded by `--max-more` (0 = initial page only). `--limit` caps comments shown.
- Read-only (nothing is stored). Concise text by default; `--json` for structured output.
- Requests are rate-limited across ALL processes, so running several agents at
  once stays safe automatically.

## Auth
The tool rides the user's logged-in Reddit session via cookies. If a command
prints "403 — cookies missing or expired":
- Tell the user to run `reddit-search refresh-cookies` — it auto-extracts if they
  have `agent-browser`, `browser_cookie3`, `playwright`, or `selenium` installed.
- Otherwise they place cookies at `~/.config/reddit-search/cookies.json` (a JSON
  map, a browser-export JSON list, or a Netscape cookies.txt).
Run `reddit-search doctor` to check cookies + available extractors.

Keep volume modest — this uses the user's authenticated session (account risk).
