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
