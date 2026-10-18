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


def test_query_malformed_cookie_file_is_friendly(capsys, tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{garbage")
    rc = cli.main(["query", "hello", "--cookies", str(path)])   # client built for real
    assert rc == 4
    err = capsys.readouterr().err
    assert "not valid" in err and "Traceback" not in err


def test_doctor_malformed_cookie_file(capsys, tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{garbage")
    rc = cli.main(["doctor", "--cookies", str(path)])
    assert rc == 1
    assert "INVALID" in capsys.readouterr().out


def test_refresh_no_provider_gives_manual_instructions(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.cookies_mod, "refresh_report", lambda path=None: (None, 0, []))
    rc = cli.main(["refresh-cookies", "--cookies", str(tmp_path / "c.json")])
    assert rc == 1
    err = capsys.readouterr().err.lower()
    assert "manually" in err and "no agent-browser" in err


def test_refresh_failure_says_why_per_provider(capsys, tmp_path, monkeypatch):
    reason = ("`agent-browser --auto-connect open` failed: ✗ Auto-launch failed: "
              "No running Chrome instance found.")
    monkeypatch.setattr(cli.cookies_mod, "refresh_report",
                        lambda path=None: (None, 0, [("agent-browser", reason),
                                                     ("selenium", "ImportError: boom")]))
    rc = cli.main(["refresh-cookies", "--cookies", str(tmp_path / "c.json")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "agent-browser: `agent-browser --auto-connect open` failed" in err
    assert "No running Chrome instance found" in err
    assert "selenium: ImportError: boom" in err
    assert "remote debugging" in err          # the hint for the recognised failure
    assert "manually" in err.lower()


def test_refresh_success_mentions_skipped_extractors(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.cookies_mod, "refresh_report",
                        lambda path=None: ("browser_cookie3", 3,
                                           [("agent-browser", "tool missing")]))
    rc = cli.main(["refresh-cookies", "--cookies", str(tmp_path / "c.json")])
    cap = capsys.readouterr()
    assert rc == 0
    assert "Saved 3 reddit cookie(s) via browser_cookie3" in cap.out
    assert "skipped agent-browser: tool missing" in cap.err

