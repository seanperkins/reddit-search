import json

from reddit_search import cookies as ck


def test_parse_json_map():
    assert ck.parse_cookies('{"reddit_session": "abc", "token": "t"}') == {
        "reddit_session": "abc", "token": "t"}


def test_parse_json_list_filters_reddit():
    text = json.dumps([
        {"name": "reddit_session", "value": "abc", "domain": ".reddit.com"},
        {"name": "other", "value": "x", "domain": "example.com"},
    ])
    assert ck.parse_cookies(text) == {"reddit_session": "abc"}


def test_parse_netscape_filters_reddit():
    text = (
        "# Netscape HTTP Cookie File\n"
        ".reddit.com\tTRUE\t/\tTRUE\t0\treddit_session\tabc\n"
        "example.com\tTRUE\t/\tTRUE\t0\tnope\tx\n"
    )
    assert ck.parse_cookies(text) == {"reddit_session": "abc"}


def test_parse_empty():
    assert ck.parse_cookies("") == {}


def test_save_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "c.json")
    ck.save_cookies({"reddit_session": "abc"}, path)
    import os
    assert oct(os.stat(path).st_mode)[-3:] == "600"
    assert ck.load_cookies(path) == {"reddit_session": "abc"}


def test_load_missing_returns_empty(tmp_path):
    assert ck.load_cookies(str(tmp_path / "nope.json")) == {}


def test_resolve_path_prefers_arg_then_env(monkeypatch):
    monkeypatch.setenv("REDDIT_COOKIES", "/from/env")
    assert ck.resolve_path("/explicit") == "/explicit"
    assert ck.resolve_path(None) == "/from/env"
    monkeypatch.delenv("REDDIT_COOKIES")
    assert ck.resolve_path(None).endswith("reddit-search/cookies.json")


def test_refresh_uses_first_available_provider(tmp_path):
    path = str(tmp_path / "c.json")
    providers = [
        ("unavailable", lambda: False, lambda: {"should": "not run"}),
        ("good", lambda: True, lambda: {"reddit_session": "abc"}),
        ("later", lambda: True, lambda: {"never": "reached"}),
    ]
    name, n = ck.refresh(path=path, providers=providers)
    assert name == "good" and n == 1
    assert ck.load_cookies(path) == {"reddit_session": "abc"}


def test_refresh_skips_empty_results(tmp_path):
    providers = [
        ("empty", lambda: True, lambda: None),
        ("also_empty", lambda: True, lambda: {}),
    ]
    assert ck.refresh(path=str(tmp_path / "c.json"), providers=providers) == (None, 0)


def test_available_providers_filters(monkeypatch):
    providers = [("a", lambda: True, None), ("b", lambda: False, None)]
    assert ck.available_providers(providers) == ["a"]


def test_load_cookies_raises_cookie_error_on_garbage(tmp_path):
    import pytest
    path = tmp_path / "c.json"
    path.write_text("{not valid json")
    with pytest.raises(ck.CookieError):
        ck.load_cookies(str(path))


def test_parse_netscape_keeps_httponly_lines():
    text = "#HttpOnly_.reddit.com\tTRUE\t/\tTRUE\t0\treddit_session\tabc\n"
    assert ck.parse_cookies(text) == {"reddit_session": "abc"}


# --- refresh_report: a failed refresh must say why ---

def test_refresh_report_collects_reasons_and_keeps_going(tmp_path):
    def diagnosed():
        raise ck.ExtractError("no Chrome here")

    def crashes():
        raise KeyError("value")

    providers = [
        ("diagnosed", lambda: True, diagnosed),
        ("crashes", lambda: True, crashes),
        ("empty", lambda: True, lambda: {}),
        ("good", lambda: True, lambda: {"reddit_session": "abc"}),
    ]
    name, n, failures = ck.refresh_report(path=str(tmp_path / "c.json"), providers=providers)
    assert (name, n) == ("good", 1)
    assert failures == [
        ("diagnosed", "no Chrome here"),
        ("crashes", "KeyError: 'value'"),
        ("empty", ck.NO_COOKIES),
    ]


def test_refresh_report_total_failure_lists_every_tried_provider(tmp_path):
    def boom():
        raise OSError("disk on fire")

    providers = [
        ("skipped", lambda: False, lambda: {"never": "run"}),
        ("a", lambda: True, boom),
    ]
    name, n, failures = ck.refresh_report(path=str(tmp_path / "c.json"), providers=providers)
    assert (name, n) == (None, 0)
    assert failures == [("a", "OSError: disk on fire")]      # the unavailable one is not listed


def test_refresh_keeps_its_old_return_shape(tmp_path):
    providers = [("a", lambda: True, lambda: (_ for _ in ()).throw(RuntimeError("x")))]
    assert ck.refresh(path=str(tmp_path / "c.json"), providers=providers) == (None, 0)


def test_run_tool_surfaces_the_tools_own_message(monkeypatch):
    import subprocess

    def fake_run(argv, **kw):
        raise subprocess.CalledProcessError(
            1, argv, output=b"", stderr=b"\x1b[31m\xe2\x9c\x97 Auto-launch failed: No running Chrome instance found.\x1b[0m\n")

    monkeypatch.setattr(ck.subprocess, "run", fake_run)
    try:
        ck._run_tool(["agent-browser", "--auto-connect", "open", "https://www.reddit.com"])
    except ck.ExtractError as e:
        msg = str(e)
    assert "`agent-browser --auto-connect open` failed" in msg
    assert "No running Chrome instance found." in msg
    assert "\x1b" not in msg                    # terminal escapes are stripped
    assert "https://www.reddit.com" not in msg  # the label never carries the URL


def test_run_tool_reports_a_timeout(monkeypatch):
    import subprocess

    def fake_run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(ck.subprocess, "run", fake_run)
    try:
        ck._run_tool(["agent-browser", "state", "save", "/tmp/x.json"], timeout=7)
    except ck.ExtractError as e:
        assert str(e) == "`agent-browser state save` timed out after 7s"
    else:
        raise AssertionError("expected ExtractError")


def test_run_tool_falls_back_to_the_exit_status(monkeypatch):
    import subprocess

    def fake_run(argv, **kw):
        raise subprocess.CalledProcessError(3, argv, output=b"", stderr=b"")

    monkeypatch.setattr(ck.subprocess, "run", fake_run)
    try:
        ck._run_tool(["agent-browser", "state", "save", "/tmp/x.json"])
    except ck.ExtractError as e:
        assert "exit status 3" in str(e)


def test_agent_browser_extractor_failure_is_diagnosable(monkeypatch):
    import subprocess

    monkeypatch.setattr(ck.shutil, "which", lambda name: "/usr/bin/agent-browser")

    def fake_run(argv, **kw):
        raise subprocess.CalledProcessError(
            1, argv, output=b"", stderr=b"No running Chrome instance found.\n")

    monkeypatch.setattr(ck.subprocess, "run", fake_run)
    name, n, failures = ck.refresh_report(
        path="/dev/null", providers=[("agent-browser", lambda: True, ck._extract_agent_browser)])
    assert (name, n) == (None, 0)
    assert "No running Chrome instance found." in failures[0][1]


def test_agent_browser_extractor_with_no_reddit_cookies_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(ck.shutil, "which", lambda name: "/usr/bin/agent-browser")

    def fake_run(argv, **kw):
        if argv[1:3] == ["state", "save"]:
            with open(argv[3], "w") as f:
                json.dump({"cookies": [{"name": "x", "value": "y", "domain": "example.com"}]}, f)

    monkeypatch.setattr(ck.subprocess, "run", fake_run)
    name, n, failures = ck.refresh_report(
        path=str(tmp_path / "c.json"),
        providers=[("agent-browser", lambda: True, ck._extract_agent_browser)])
    assert (name, n) == (None, 0)
    assert failures == [("agent-browser", ck.NO_COOKIES)]


def test_reasons_are_one_line_and_bounded():
    assert ck._clip("a\n  b\tc") == "a b c"
    long = ck._clip("x" * 1000)
    assert len(long) == ck.REASON_LIMIT and long.endswith("…")


def test_hint_only_for_the_recognised_failure():
    assert "remote debugging" in ck.hint_for("agent-browser", "x No running Chrome instance y")
    assert ck.hint_for("agent-browser", "something else") is None
    assert ck.hint_for("selenium", "No running Chrome") is None


# --- visitor cookies are not a login ---

def test_visitor_cookies_fall_through_to_the_next_extractor(tmp_path):
    visitor = {"loid": "x", "csrf_token": "y", "token_v2": "z"}
    providers = [
        ("logged_out", lambda: True, lambda: dict(visitor)),
        ("signed_in", lambda: True, lambda: {"reddit_session": "abc", "loid": "x"}),
    ]
    path = str(tmp_path / "c.json")
    name, n, failures = ck.refresh_report(path=path, providers=providers)
    assert (name, n) == ("signed_in", 2)
    assert failures == [("logged_out", ck.NO_LOGIN)]
    assert "reddit_session" in ck.load_cookies(path)


def test_visitor_cookies_alone_are_a_failure_and_are_not_saved(tmp_path):
    path = tmp_path / "c.json"
    providers = [("logged_out", lambda: True, lambda: {"loid": "x", "csrf_token": "y"})]
    name, n, failures = ck.refresh_report(path=str(path), providers=providers)
    assert (name, n) == (None, 0)
    assert failures == [("logged_out", ck.NO_LOGIN)]
    assert not path.exists()                      # never overwrite a file with a non-login


def test_no_login_tells_you_to_sign_in_whichever_browser_it_was():
    for provider in ("agent-browser", "browser_cookie3", "selenium"):
        assert "sign in to reddit.com" in ck.hint_for(provider, ck.NO_LOGIN)
