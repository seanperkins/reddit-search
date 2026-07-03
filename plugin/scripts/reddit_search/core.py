"""Search + read logic for the portable Reddit tool (stdlib-only, read-only).

Mirrors the reviewed search/dig code from the reddit-scraping project: live
search, single-post read with bounded-recursive "more comments" expansion, and
best-effort removed-comment recovery via PullPush.
"""

import json
import re

SORTS = ("relevance", "hot", "top", "new", "comments")
TIMES = ("hour", "day", "week", "month", "year", "all")

DEFAULT_READ_LIMIT = 50
DEFAULT_MAX_MORE_FETCHES = 20
MORECHILDREN_BATCH = 100          # Reddit caps children per morechildren call
MORECHILDREN_URL = "https://www.reddit.com/api/morechildren.json"
PULLPUSH_COMMENT = "https://api.pullpush.io/reddit/comment/search/"
MISSING_BODIES = (None, "[removed]", "[deleted]")
_SNIPPET_LEN = 280

_ID_RE = re.compile(r"/comments/([a-z0-9]+)", re.IGNORECASE)
_RAW_ID_RE = re.compile(r"^[a-z0-9]+$", re.IGNORECASE)


# --- small helpers ---

def _snippet(text):
    text = " ".join((text or "").split())
    return text[:_SNIPPET_LEN] + ("…" if len(text) > _SNIPPET_LEN else "")


def _age(created_utc, now):
    secs = max(0, int(now - (created_utc or now)))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit}"
    return f"{secs}s"


def _permalink_url(permalink):
    return f"https://www.reddit.com{permalink}" if permalink else ""


def _strip(fullname):
    return fullname.split("_", 1)[1] if fullname and "_" in fullname else None


# --- search ---

def project_post(d):
    return {
        "id": d.get("id"),
        "title": d.get("title"),
        "subreddit": d.get("subreddit"),
        "score": d.get("score"),
        "num_comments": d.get("num_comments"),
        "created_utc": d.get("created_utc"),
        "permalink": d.get("permalink"),
        "selftext": d.get("selftext") or "",
    }


def search_posts(client, text, sub=None, sort="relevance", time_filter="all", limit=25):
    if sub:
        url = f"https://www.reddit.com/r/{sub}/search.json"
        params = {"q": text, "sort": sort, "t": time_filter, "limit": limit, "restrict_sr": 1}
    else:
        url = "https://www.reddit.com/search.json"
        params = {"q": text, "sort": sort, "t": time_filter, "limit": limit}
    data = client.get_json(url, params=params)
    return [project_post(c["data"]) for c in data["data"]["children"]
            if c.get("kind") == "t3"]


def format_results(posts, now, as_json=False):
    if as_json:
        return json.dumps(posts, indent=2)
    if not posts:
        return "No results."
    lines = []
    for i, p in enumerate(posts, 1):
        lines.append(
            f"{i}. {p['title']}  ·  r/{p['subreddit']}  ·  "
            f"score {p['score']}  ·  {p['num_comments']} comments  ·  "
            f"{_age(p['created_utc'], now)} ago")
        lines.append(f"   {_permalink_url(p['permalink'])}")
        snip = _snippet(p["selftext"])
        if snip:
            lines.append(f"   {snip}")
        lines.append("")
    return "\n".join(lines).rstrip()


# --- comment tree + deep-thread expansion ---

def _row(d, depth, now):
    parent = d.get("parent_id") or ""
    parent_id = _strip(parent) if parent.startswith("t1_") else None
    author = d.get("author")
    if author in (None, "[deleted]"):
        author = None
    return {
        "id": d["id"], "post_id": _strip(d.get("link_id")), "parent_id": parent_id,
        "author": author, "body": d.get("body"), "score": d.get("score"),
        "created_utc": d.get("created_utc"), "depth": depth,
        "source": "reddit", "now": now,
    }


def flatten_comments(listing_json, now):
    rows = []
    comments_listing = listing_json[1]

    def walk(children, depth):
        for node in children:
            if node.get("kind") != "t1":
                continue
            d = node["data"]
            rows.append(_row(d, depth, now))
            replies = d.get("replies")
            if isinstance(replies, dict):
                walk(replies["data"]["children"], depth + 1)

    walk(comments_listing["data"]["children"], 0)
    return rows


def _collect_stubs(children):
    stubs = []

    def walk(nodes):
        for n in nodes:
            if n.get("kind") == "more":
                stubs.append(n["data"])
            elif n.get("kind") == "t1":
                replies = n["data"].get("replies")
                if isinstance(replies, dict):
                    walk(replies["data"]["children"])

    walk(children)
    return stubs


def _thing_to_row(d, now):
    return _row(d, d.get("depth", 0), now)


def _morechildren(client, post_id, child_ids, now):
    data = client.get_json(MORECHILDREN_URL, params={
        "api_type": "json", "link_id": f"t3_{post_id}", "children": ",".join(child_ids)})
    things = ((data.get("json") or {}).get("data") or {}).get("things") or []
    rows, stubs = [], []
    for t in things:
        if t.get("kind") == "t1":
            rows.append(_thing_to_row(t["data"], now))
        elif t.get("kind") == "more":
            stubs.append(t["data"])
    return rows, stubs


def _continue_thread(client, post_id, parent_cid, now):
    listing = client.get_json(
        f"https://www.reddit.com/comments/{post_id}/_/{parent_cid}.json")
    return flatten_comments(listing, now), _collect_stubs(listing[1]["data"]["children"])


def _continue_parent(stub):
    parent = stub.get("parent_id") or ""
    return _strip(parent) if parent.startswith("t1_") else None


def _would_fetch(stub, seen_ids, done_parents):
    if stub.get("id") == "_" or not (stub.get("children") or []):
        pcid = _continue_parent(stub)
        return bool(pcid) and pcid not in done_parents
    return any(c not in seen_ids for c in stub["children"])


def expand_more(client, post_id, stubs, seen_ids, now, max_fetches):
    rows = []
    queue = list(stubs)
    done_parents = set()
    fetches = 0
    truncated = False
    while queue:
        if fetches >= max_fetches:
            truncated = any(_would_fetch(s, seen_ids, done_parents) for s in queue)
            break
        stub = queue.pop(0)
        children = stub.get("children") or []
        try:
            if stub.get("id") == "_" or not children:
                pcid = _continue_parent(stub)
                if not pcid or pcid in done_parents:
                    continue
                done_parents.add(pcid)
                fetches += 1
                new_rows, new_stubs = _continue_thread(client, post_id, pcid, now)
            else:
                ids = [c for c in children if c not in seen_ids]
                if not ids:
                    continue
                fetches += 1
                batch, rest = ids[:MORECHILDREN_BATCH], ids[MORECHILDREN_BATCH:]
                new_rows, new_stubs = _morechildren(client, post_id, batch, now)
                if rest:
                    queue.append({"id": "batch", "children": rest,
                                  "parent_id": stub.get("parent_id")})
        except Exception:
            continue
        for r in new_rows:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                rows.append(r)
        queue.extend(new_stubs)
    return rows, truncated


def recover_removed(client, post_id, rows):
    needs = [r for r in rows if r.get("body") in MISSING_BODIES]
    if not needs:
        return 0
    try:
        data = client.get_json(PULLPUSH_COMMENT, params={"link_id": post_id, "size": 100})
        by_id = {c.get("id"): c.get("body") for c in data.get("data", [])}
    except Exception:
        return 0
    recovered = 0
    for r in needs:
        body = by_id.get(r["id"])
        if body and body not in MISSING_BODIES:
            r["body"] = body
            r["source"] = "pullpush"
            recovered += 1
    return recovered


# --- read ---

def parse_post_id(id_or_url):
    m = _ID_RE.search(id_or_url)
    if m:
        return m.group(1)
    if _RAW_ID_RE.match(id_or_url.strip()):
        return id_or_url.strip()
    raise ValueError(f"Could not parse a post id from: {id_or_url!r}")


def fetch_post(client, post_id, now, max_more_fetches=DEFAULT_MAX_MORE_FETCHES):
    """Return (post, comment_rows, truncated). Read-only; no persistence."""
    listing = client.get_json(f"https://www.reddit.com/comments/{post_id}.json")
    post_children = listing[0]["data"]["children"]
    post = post_children[0]["data"] if post_children else None
    rows = flatten_comments(listing, now)
    truncated = False
    if max_more_fetches:
        seen = {r["id"] for r in rows}
        stubs = _collect_stubs(listing[1]["data"]["children"])
        if stubs:
            extra, truncated = expand_more(client, post_id, stubs, seen, now, max_more_fetches)
            rows.extend(extra)
    recover_removed(client, post_id, rows)
    return post, rows, truncated


def read_post(client, id_or_url, now, max_more_fetches=DEFAULT_MAX_MORE_FETCHES):
    return fetch_post(client, parse_post_id(id_or_url), now,
                      max_more_fetches=max_more_fetches)


def format_post(post, comments, now, as_json=False, limit=None, truncated=False):
    post = post or {}
    total = len(comments)
    shown = comments if not limit or limit >= total else comments[:limit]
    omitted = total - len(shown)
    if as_json:
        return json.dumps({"post": post, "comments": shown,
                           "omitted_comments": omitted, "truncated": truncated}, indent=2)
    lines = [
        post.get("title", "(no title)"),
        f"r/{post.get('subreddit')}  ·  score {post.get('score')}  ·  "
        f"{_age(post.get('created_utc'), now)} ago",
        _permalink_url(post.get("permalink")),
        "",
    ]
    body = (post.get("selftext") or "").strip()
    if body:
        lines += [body, ""]
    header = (f"--- showing {len(shown)} of {total} comments ---" if omitted
              else f"--- {total} comments ---")
    lines.append(header)
    for c in shown:
        indent = "  " * c.get("depth", 0)
        author = c.get("author") or "[deleted]"
        lines.append(f"{indent}• {author} ({c.get('score')}): {c.get('body')}")
    if omitted:
        lines.append(f"... {omitted} more comments (use --limit 0 for all)")
    if truncated:
        lines.append("(deep threads truncated at the fetch cap — raise --max-more for more)")
    return "\n".join(lines)
