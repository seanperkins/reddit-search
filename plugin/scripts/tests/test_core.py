import json

from reddit_search import core


class FakeClient:
    """Routes by URL: search/comments -> comment_json; morechildren -> a list
    (popped) or single dict; continue-thread (/_/) -> continue_thread; pullpush."""

    def __init__(self, listing=None, morechildren=None, continue_thread=None,
                 search_children=None):
        self._listing = listing
        self._search = search_children
        self._morechildren = (morechildren if morechildren is not None
                              else {"json": {"data": {"things": []}}})
        self._continue = continue_thread
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        if "pullpush" in url:
            raise RuntimeError("no pullpush")
        if "morechildren" in url:
            if isinstance(self._morechildren, list):
                return self._morechildren.pop(0)
            return self._morechildren
        if "/search.json" in url:
            return {"data": {"children": self._search or []}}
        if "/_/" in url:
            return self._continue
        return self._listing


def _t3(pid, **over):
    d = {"id": pid, "title": f"t{pid}", "subreddit": "roguelikes", "score": 5,
         "num_comments": 2, "created_utc": 1000.0,
         "permalink": f"/r/roguelikes/comments/{pid}/x/", "selftext": "body"}
    d.update(over)
    return {"kind": "t3", "data": d}


def _t1(cid, parent="t3_post1"):
    return {"kind": "t1", "data": {"id": cid, "parent_id": parent,
            "link_id": "t3_post1", "author": "a", "body": "hi", "score": 1,
            "created_utc": 1.0}}


def _more(mid, children, parent="t3_post1"):
    return {"kind": "more", "data": {"id": mid, "count": len(children),
            "children": list(children), "parent_id": parent}}


# --- search ---

def test_search_posts_global_and_scoped():
    c = FakeClient(search_children=[_t3("p1")])
    core.search_posts(c, "x", limit=5)
    assert c.calls[0][0] == "https://www.reddit.com/search.json"
    assert "restrict_sr" not in c.calls[0][1]
    core.search_posts(c, "x", sub="roguelikedev")
    assert c.calls[1][0] == "https://www.reddit.com/r/roguelikedev/search.json"
    assert c.calls[1][1]["restrict_sr"] == 1


def test_format_results_text_and_json():
    posts = core.search_posts(FakeClient(search_children=[_t3("p1")]), "x")
    text = core.format_results(posts, now=1000.0)
    assert "r/roguelikes" in text and "comments/p1" in text
    assert json.loads(core.format_results(posts, now=1000.0, as_json=True))[0]["id"] == "p1"


# --- read + expansion ---

def test_parse_post_id():
    assert core.parse_post_id("https://www.reddit.com/r/x/comments/abc/y/") == "abc"
    assert core.parse_post_id("abc123") == "abc123"


def test_fetch_post_expands_and_reports_truncated():
    listing = [
        {"data": {"children": [_t3("post1")]}},
        {"data": {"children": [_t1("c1"), _more("m", ["e"])]}},
    ]
    mc = {"json": {"data": {"things": [{"kind": "t1", "data": {
        "id": "e", "parent_id": "t3_post1", "link_id": "t3_post1",
        "author": "z", "body": "expanded", "score": 1, "created_utc": 1.0, "depth": 0}}]}}}
    c = FakeClient(listing=listing, morechildren=mc)
    post, rows, truncated = core.fetch_post(c, "post1", now=9.0)
    assert post["id"] == "post1"
    assert {r["id"] for r in rows} == {"c1", "e"}
    assert truncated is False
    # disable expansion
    c2 = FakeClient(listing=listing)
    _, rows2, _ = core.fetch_post(c2, "post1", now=9.0, max_more_fetches=0)
    assert {r["id"] for r in rows2} == {"c1"}


def test_format_post_limit_and_truncated():
    post = {"id": "p1", "title": "Hi", "permalink": "/r/x/comments/p1/"}
    comments = [{"id": f"c{i}", "author": "a", "body": "b", "score": 1, "depth": 0}
                for i in range(4)]
    text = core.format_post(post, comments, now=1.0, limit=2, truncated=True)
    assert "showing 2 of 4" in text and "truncated" in text.lower()
    blob = json.loads(core.format_post(post, comments, now=1.0, as_json=True,
                                       limit=2, truncated=True))
    assert len(blob["comments"]) == 2 and blob["omitted_comments"] == 2
    assert blob["truncated"] is True
