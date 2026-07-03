import io

import pytest

from reddit_search import client as C
from reddit_search.client import RedditAuthError, RedditClient


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None, cookies=None):
        self.calls.append({"url": url, "params": params, "headers": headers,
                           "cookies": cookies})
        return self._responses.pop(0)


def test_requires_user_agent():
    with pytest.raises(ValueError):
        RedditClient(user_agent="")


def test_get_json_success_sets_user_agent():
    sess = FakeSession([FakeResp(200, {"ok": True})])
    client = RedditClient(session=sess, request_delay=0, sleep=lambda *_: None)
    assert client.get_json("http://x") == {"ok": True}
    assert sess.calls[0]["headers"]["User-Agent"] == C.DEFAULT_USER_AGENT


def test_get_json_retries_then_succeeds():
    sess = FakeSession([FakeResp(429), FakeResp(200, [1])])
    client = RedditClient(session=sess, request_delay=0, sleep=lambda *_: None)
    assert client.get_json("http://x") == [1]
    assert len(sess.calls) == 2


def test_403_raises_auth_error():
    sess = FakeSession([FakeResp(403)])
    client = RedditClient(session=sess, request_delay=0, sleep=lambda *_: None)
    with pytest.raises(RedditAuthError):
        client.get_json("https://www.reddit.com/search.json")


def test_cookies_only_for_reddit():
    sess = FakeSession([FakeResp(200, {"data": []})])
    client = RedditClient(session=sess, request_delay=0, sleep=lambda *_: None,
                          cookies={"reddit_session": "abc"})
    client.get_json("https://api.pullpush.io/reddit/comment/search/")
    assert not sess.calls[0]["cookies"]     # no leak to third-party host


def test_throttle_used_for_reddit_not_pullpush():
    calls, sleeps = [], []
    sess = FakeSession([FakeResp(200, {"ok": 1}), FakeResp(200, {"ok": 2})])
    client = RedditClient(session=sess, request_delay=5, sleep=lambda s: sleeps.append(s),
                          throttle=lambda: calls.append("t"))
    client.get_json("https://www.reddit.com/x.json")
    assert calls == ["t"] and sleeps == []
    client.get_json("https://api.pullpush.io/x")
    assert calls == ["t"] and sleeps == [5]


class _FakeHTTPResponse:
    status = 200

    def __init__(self, body=b'{"ok": true}'):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_urllib_session_builds_request(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeHTTPResponse()

    monkeypatch.setattr(C.urllib.request, "urlopen", fake_urlopen)
    resp = C.UrllibSession().get(
        "https://www.reddit.com/search.json", params={"q": "a b", "limit": 3},
        headers={"User-Agent": "ua/1.0"}, timeout=30, cookies={"s": "1"})
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert "q=a+b" in captured["url"] and "limit=3" in captured["url"]
    assert captured["headers"]["cookie"] == "s=1"


def test_urllib_session_maps_http_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise C.urllib.error.HTTPError(req.full_url, 403, "no", {}, io.BytesIO(b""))

    monkeypatch.setattr(C.urllib.request, "urlopen", fake_urlopen)
    assert C.UrllibSession().get("https://www.reddit.com/x").status_code == 403
