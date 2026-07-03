# reddit-search (Claude Code plugin + portable CLI)

Search and read Reddit from Claude Code (or any shell) via your **logged-in
browser session** — for when `reddit.com` is blocked to agents (WebFetch is
disallowed and anonymous `.json` returns 403).

- **Read-only** — searches and reads; never writes anywhere except a cookie file.
- **Stdlib-only** — the runtime tool imports nothing outside the Python standard
  library. No `pip install`, no venv. Requires only `python3`.
- **Cross-process rate limited** — a lockfile throttle floors the *combined*
  request rate across every process/agent, to protect your account.

> ⚠️ **Account risk.** This sends *your authenticated Reddit session* on
> automated requests against unofficial endpoints. That's against Reddit's User
> Agreement and the realistic downside is **suspension of your account**. Keep
> volume low; consider a throwaway account.

## Install as a Claude Code plugin

```
/plugin marketplace add <your-github>/reddit-search
/plugin install reddit-search@reddit-search
```

Local testing (no marketplace): `claude --plugin-dir ./plugin`

Once enabled, `reddit-search` is on PATH and a skill tells agents to use it
instead of a blocked `reddit.com` fetch.

## Usage

```bash
reddit-search query "procedural generation" --sub roguelikedev --limit 5
reddit-search read <post_id_or_url> --limit 20 --max-more 6
reddit-search doctor            # check cookies + available extractors
reddit-search refresh-cookies   # extract cookies from an available browser tool
```

## Auth setup

The tool needs your Reddit cookies (it rides your existing login):

1. **Automatic** — `reddit-search refresh-cookies` tries, in order, whatever you
   have: `agent-browser`, `browser_cookie3`, `playwright`, `selenium`.
2. **Manual** — put a file at `~/.config/reddit-search/cookies.json` (override with
   `--cookies` or `$REDDIT_COOKIES`). Accepted formats: a JSON map
   `{"reddit_session": "..."}`, a browser-export JSON list, or a Netscape
   `cookies.txt`.

Cookies expire; when you start seeing 403s, refresh them again.

## Layout

```
.claude-plugin/marketplace.json   # marketplace listing
plugin/
├── .claude-plugin/plugin.json    # plugin manifest
├── skills/reddit-search/SKILL.md # description-triggered nudge
├── bin/reddit-search             # PATH entrypoint (python3)
└── scripts/reddit_search/        # the stdlib-only tool (client, ratelimit, cookies, core, cli)
```

## Development

```bash
uv run pytest        # runtime is stdlib-only; pytest is dev-only
```
