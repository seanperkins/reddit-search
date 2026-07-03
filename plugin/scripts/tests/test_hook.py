import json
import os
import subprocess
import sys

# plugin/scripts/tests/ -> plugin/hooks/reddit_redirect.py
_PLUGIN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOK = os.path.join(_PLUGIN, "hooks", "reddit_redirect.py")


def _run(payload):
    return subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                          capture_output=True, text=True)


def test_blocks_reddit_webfetch():
    r = _run({"tool_name": "WebFetch",
              "tool_input": {"url": "https://www.reddit.com/r/x/comments/1/y/"}})
    assert r.returncode == 2
    assert "reddit-search" in r.stderr


def test_allows_non_reddit_webfetch():
    r = _run({"tool_name": "WebFetch", "tool_input": {"url": "https://example.com"}})
    assert r.returncode == 0 and r.stderr == ""


def test_ignores_other_tools():
    r = _run({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert r.returncode == 0


def test_fail_open_on_garbage_stdin():
    r = subprocess.run([sys.executable, HOOK], input="not json",
                       capture_output=True, text=True)
    assert r.returncode == 0        # never break WebFetch on a hook parse error
