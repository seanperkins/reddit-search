from reddit_search import cli
from reddit_search.client import RedditAuthError


class FakeClient:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        if self._error:
            raise self._error
        return self._result


def test_query_runs(capsys):
    client = FakeClient(result={"data": {"children": [
        {"kind": "t3", "data": {"id": "p1", "title": "T", "subreddit": "roguelikes",
         "score": 1, "num_comments": 0, "created_utc": 1.0,
         "permalink": "/r/roguelikes/comments/p1/", "selftext": ""}}]}})
    rc = cli.main(["query", "hello", "--limit", "3"], client=client)
    assert rc == 0
    assert "r/roguelikes" in capsys.readouterr().out


def test_query_403_is_friendly(capsys):
    client = FakeClient(error=RedditAuthError("cookies missing"))
    rc = cli.main(["query", "hello"], client=client)
    assert rc == 3
    assert "cookies" in capsys.readouterr().err.lower()


def test_read_bad_id(capsys):
    rc = cli.main(["read", "bad id!!"], client=FakeClient(result=None))
    assert rc == 2
    assert "bad id" in capsys.readouterr().err.lower()


def test_read_structural_error_is_clean(capsys):
    rc = cli.main(["read", "abc123"], client=FakeClient(result={"error": 404}))
    assert rc == 2
    assert "abc123" in capsys.readouterr().err


def test_doctor_reports_status(capsys, tmp_path):
    rc = cli.main(["doctor", "--cookies", str(tmp_path / "none.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cookie file:" in out and "cookies loaded:" in out


def test_refresh_no_provider_gives_manual_instructions(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.cookies_mod, "refresh", lambda path=None: (None, 0))
    monkeypatch.setattr(cli.cookies_mod, "available_providers", lambda: [])
    rc = cli.main(["refresh-cookies", "--cookies", str(tmp_path / "c.json")])
    assert rc == 1
    assert "manually" in capsys.readouterr().err.lower()
