"""CLI entrypoint: query / read / doctor / refresh-cookies.

No config file needed — sensible defaults, with optional env overrides:
  REDDIT_COOKIES       path to a cookie file (else ~/.config/reddit-search/cookies.json)
  REDDIT_USER_AGENT    override the default desktop User-Agent
  REDDIT_REQUEST_DELAY seconds between requests (floored at MIN_REQUEST_DELAY)
"""

import argparse
import os
import sys
import time

from . import cookies as cookies_mod
from . import core
from . import ratelimit
from .client import DEFAULT_USER_AGENT, RedditAuthError, RedditClient

MIN_REQUEST_DELAY = 1.0     # never hammer the user's authenticated session


def _build_client(args):
    delay = max(float(os.environ.get("REDDIT_REQUEST_DELAY", 1.0)), MIN_REQUEST_DELAY)
    ua = os.environ.get("REDDIT_USER_AGENT", DEFAULT_USER_AGENT)
    return RedditClient(user_agent=ua, request_delay=delay,
                        cookies=cookies_mod.load_cookies(args.cookies),
                        throttle=ratelimit.make_throttle(delay))


def _build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cookies", default=None,
                        help="cookie file path (default: $REDDIT_COOKIES or "
                             "~/.config/reddit-search/cookies.json)")

    ap = argparse.ArgumentParser(
        prog="reddit-search",
        description="Search and read Reddit via your logged-in session (read-only).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("query", parents=[common], help="search Reddit")
    q.add_argument("text")
    q.add_argument("--sub", help="restrict to a subreddit")
    q.add_argument("--sort", choices=core.SORTS, default="relevance")
    q.add_argument("--time", choices=core.TIMES, default="all")
    q.add_argument("--limit", type=int, default=25)
    q.add_argument("--json", action="store_true")

    r = sub.add_parser("read", parents=[common], help="read a post + top comments")
    r.add_argument("id_or_url")
    r.add_argument("--limit", type=int, default=core.DEFAULT_READ_LIMIT,
                   help="max comments to print (0 = all; default %(default)s)")
    r.add_argument("--max-more", type=int, default=core.DEFAULT_MAX_MORE_FETCHES,
                   dest="max_more", help="deep-thread expansion budget (0 = off)")
    r.add_argument("--json", action="store_true")

    sub.add_parser("doctor", parents=[common],
                   help="check cookies, extractors, and setup")
    sub.add_parser("refresh-cookies", parents=[common],
                   help="extract cookies from an available browser tool")
    return ap


def _doctor(args):
    path = cookies_mod.resolve_path(args.cookies)
    providers = cookies_mod.available_providers()
    print(f"python:          {sys.version.split()[0]}")
    print(f"cookie file:     {path}")
    try:
        cookies = cookies_mod.load_cookies(args.cookies)
    except cookies_mod.CookieError as e:
        print(f"cookies loaded:  INVALID — {e}")
        print(f"auto-extractors: {', '.join(providers) if providers else 'none installed'}")
        return 1
    print(f"cookies loaded:  {len(cookies)} reddit cookie(s)"
          + ("" if cookies else "  <-- none; run: reddit-search refresh-cookies"))
    print(f"auto-extractors: {', '.join(providers) if providers else 'none installed'}")
    if not cookies and not providers:
        print("no extractor available — provide cookies manually: a JSON map, a "
              "browser-export JSON list, or a Netscape cookies.txt at the path above.")
    return 0


def _refresh(args):
    name, n, failures = cookies_mod.refresh_report(path=args.cookies)
    if name:
        print(f"Saved {n} reddit cookie(s) via {name} -> {cookies_mod.resolve_path(args.cookies)}")
        for pname, reason in failures:      # earlier extractors that were tried first
            print(f"  (skipped {pname}: {reason})", file=sys.stderr)
        return 0
    if failures:
        print("Could not auto-extract cookies:", file=sys.stderr)
        for pname, reason in failures:
            print(f"  {pname}: {reason}", file=sys.stderr)
            hint = cookies_mod.hint_for(pname, reason)
            if hint:
                print(f"    -> {hint}", file=sys.stderr)
    else:
        print("Could not auto-extract cookies "
              "(no agent-browser / browser_cookie3 / playwright / selenium found).",
              file=sys.stderr)
    print("Provide cookies manually at "
          f"{cookies_mod.resolve_path(args.cookies)} — a JSON map "
          '{"reddit_session": "..."}, a browser-export JSON list, or a Netscape '
          "cookies.txt.", file=sys.stderr)
    return 1


def main(argv=None, client=None):
    args = _build_parser().parse_args(argv)
    if args.cmd == "doctor":
        return _doctor(args)
    if args.cmd == "refresh-cookies":
        return _refresh(args)

    now = time.time()
    if client is None:
        try:
            client = _build_client(args)
        except cookies_mod.CookieError as e:
            print(f"{e}\nRun: reddit-search refresh-cookies (or fix the file).",
                  file=sys.stderr)
            return 4
    try:
        if args.cmd == "query":
            posts = core.search_posts(client, args.text, sub=args.sub, sort=args.sort,
                                      time_filter=args.time, limit=args.limit)
            print(core.format_results(posts, now, as_json=args.json))
        elif args.cmd == "read":
            try:
                post, comments, truncated = core.read_post(
                    client, args.id_or_url, now, max_more_fetches=args.max_more)
            except (ValueError, KeyError, IndexError, TypeError):
                print(f"Could not read post: {args.id_or_url}", file=sys.stderr)
                return 2
            print(core.format_post(post, comments, now, as_json=args.json,
                                   limit=args.limit, truncated=truncated))
    except RedditAuthError as e:
        print(str(e), file=sys.stderr)
        return 3
    return 0
