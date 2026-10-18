"""Cookie handling for the portable Reddit tool (stdlib core).

Auth model: the tool rides the user's *existing* logged-in Reddit session via
cookies. Cookies are resolved in this order (see `load_cookies`):
  1. an explicit path (CLI --cookies)
  2. the REDDIT_COOKIES env var (a path)
  3. the default user-level file ~/.config/reddit-search/cookies.json

`refresh()` populates that default file by trying, in order, whatever browser
tooling the user already has (agent-browser, then browser_cookie3, then
best-effort playwright/selenium). If none is available/works, the user provides
cookies manually (a JSON map, a browser-export JSON list, or a Netscape
cookies.txt). The stdlib core has NO third-party deps; extractors are optional
and only imported/invoked when present.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

DEFAULT_PATH = "~/.config/reddit-search/cookies.json"
ENV_VAR = "REDDIT_COOKIES"
TOOL_TIMEOUT = 60           # seconds; a stuck browser tool must fail, not hang
REASON_LIMIT = 300          # characters kept from a failure reason


class CookieError(RuntimeError):
    """Raised when a cookie file exists but can't be parsed."""


class ExtractError(RuntimeError):
    """An extractor ran but could not produce cookies. The message says why."""


def _is_reddit_domain(domain):
    d = (domain or "").lstrip(".").lower()
    return d == "reddit.com" or d.endswith(".reddit.com")


def resolve_path(path=None):
    """The cookie file path: explicit arg > env var > default."""
    return os.path.expanduser(path or os.environ.get(ENV_VAR) or DEFAULT_PATH)


def parse_cookies(text):
    """Parse cookies from JSON (map or browser-export list) or Netscape text.

    Returns a {name: value} dict of reddit cookies.
    """
    text = text.strip()
    if not text:
        return {}
    if text[0] in "{[":                       # JSON
        data = json.loads(text)
        if isinstance(data, dict):
            return dict(data)
        out = {}
        for c in data:                        # list of cookie objects
            if _is_reddit_domain(c.get("domain", "reddit.com")) and c.get("name"):
                out[c["name"]] = c.get("value", "")
        return out
    return _parse_netscape(text)


def _parse_netscape(text):
    """Parse a Netscape cookies.txt body -> reddit {name: value}."""
    out = {}
    for line in text.splitlines():
        stripped = line.lstrip()
        # Some exporters prefix HttpOnly cookie lines with "#HttpOnly_" — keep those.
        if stripped.startswith("#HttpOnly_"):
            line = line.replace("#HttpOnly_", "", 1)
        elif not line.strip() or stripped.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _flag, _path, _secure, _expiry, name, value = parts
        if _is_reddit_domain(domain) and name:
            out[name] = value
    return out


def load_cookies(path=None):
    """Load cookies from the resolved path, or {} if none present.

    Raises CookieError (not a raw JSONDecodeError) if the file exists but is
    unparseable, so callers can show a clear message instead of a traceback.
    """
    resolved = resolve_path(path)
    if not os.path.exists(resolved):
        return {}
    try:
        with open(resolved) as f:
            return parse_cookies(f.read())
    except (ValueError, OSError) as e:      # JSONDecodeError is a ValueError
        raise CookieError(
            f"cookie file at {resolved} is not valid (expected a JSON map, a "
            f"browser-export JSON list, or a Netscape cookies.txt): {e}")


def save_cookies(cookies, path=None):
    """Write cookies as a JSON map, mode 0600, atomically."""
    resolved = resolve_path(path)
    os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(resolved) or ".", suffix=".tmp")
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(dict(cookies), f)
        fd = None
        os.replace(tmp, resolved)
    except Exception:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return resolved


# --- extractors: optional, only used if the underlying tool is present ---
#
# An extractor returns {name: value}, or None/{} when it ran but found no reddit
# cookies. When it cannot run it RAISES (ExtractError for a diagnosed failure, or
# whatever the underlying library raised) and `refresh_report` records the reason.
# Swallowing the exception here is what once made a failed refresh say nothing
# about why.

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _clip(text, limit=REASON_LIMIT):
    """One line, no terminal escapes, bounded length. Reasons are shown to the user."""
    one = " ".join(_ANSI.sub("", str(text)).split())
    return one if len(one) <= limit else one[:limit - 1] + "…"


def _last_line(output):
    """The last non-empty line of a tool's output (bytes or str), or ''."""
    if isinstance(output, bytes):
        output = output.decode("utf-8", "replace")
    lines = [l for l in _ANSI.sub("", output or "").splitlines() if l.strip()]
    return lines[-1].strip() if lines else ""


def _run_tool(argv, timeout=TOOL_TIMEOUT):
    """Run a browser tool; on failure raise ExtractError carrying the tool's own message."""
    label = " ".join(argv[:3])      # the command and its verb, never a path or URL
    try:
        subprocess.run(argv, check=True, capture_output=True, timeout=timeout)
    except subprocess.CalledProcessError as e:
        detail = _last_line(e.stderr) or _last_line(e.stdout) or f"exit status {e.returncode}"
        raise ExtractError(f"`{label}` failed: {_clip(detail)}") from None
    except subprocess.TimeoutExpired:
        raise ExtractError(f"`{label}` timed out after {timeout}s") from None


def _extract_agent_browser():
    """Export reddit cookies from a running Chrome via the agent-browser CLI."""
    if not shutil.which("agent-browser"):
        raise ExtractError("agent-browser is not on PATH")
    fd, state = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        _run_tool(["agent-browser", "--auto-connect", "open", "https://www.reddit.com"])
        _run_tool(["agent-browser", "state", "save", state])
        with open(state) as f:
            data = json.load(f)
        return {c["name"]: c["value"] for c in data.get("cookies", [])
                if _is_reddit_domain(c.get("domain", ""))}
    finally:
        try:
            os.unlink(state)
        except OSError:
            pass


def _extract_browser_cookie3():
    """Read reddit cookies directly from the user's browser cookie store."""
    try:
        import browser_cookie3
    except ImportError:
        raise ExtractError("browser_cookie3 is not installed") from None
    jar = browser_cookie3.load(domain_name="reddit.com")
    return {c.name: c.value for c in jar if _is_reddit_domain(c.domain)}


def _extract_playwright():
    """Best-effort: read reddit cookies from the user's persistent Chrome profile
    via Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ExtractError("playwright is not installed") from None
    profile = _default_chrome_profile()
    if not profile:
        raise ExtractError("no Chrome profile directory found")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(profile, headless=True)
        try:
            return {c["name"]: c["value"] for c in ctx.cookies()
                    if _is_reddit_domain(c.get("domain", ""))}
        finally:
            ctx.close()


def _extract_selenium():
    """Best-effort: read reddit cookies via Selenium driving the user's Chrome
    profile."""
    try:
        from selenium import webdriver
    except ImportError:
        raise ExtractError("selenium is not installed") from None
    profile = _default_chrome_profile()
    if not profile:
        raise ExtractError("no Chrome profile directory found")
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument(f"--user-data-dir={profile}")
    driver = webdriver.Chrome(options=opts)
    try:
        driver.get("https://www.reddit.com")
        return {c["name"]: c["value"] for c in driver.get_cookies()
                if _is_reddit_domain(c.get("domain", ""))}
    finally:
        driver.quit()


def _default_chrome_profile():
    candidates = [
        "~/Library/Application Support/Google/Chrome",          # macOS
        "~/.config/google-chrome",                              # Linux
        "~/AppData/Local/Google/Chrome/User Data",             # Windows
    ]
    for c in candidates:
        p = os.path.expanduser(c)
        if os.path.isdir(p):
            return p
    return None


# Ordered registry. Each provider: (name, is_available, extract).
PROVIDERS = [
    ("agent-browser", lambda: bool(shutil.which("agent-browser")), _extract_agent_browser),
    ("browser_cookie3", lambda: _importable("browser_cookie3"), _extract_browser_cookie3),
    ("playwright", lambda: _importable("playwright"), _extract_playwright),
    ("selenium", lambda: _importable("selenium"), _extract_selenium),
]


def _importable(mod):
    import importlib.util
    return importlib.util.find_spec(mod) is not None


def available_providers(providers=None):
    return [name for name, avail, _ in (providers or PROVIDERS) if avail()]


NO_COOKIES = "ran, but found no reddit cookies (is that browser logged in to reddit.com?)"

# A browser that has merely visited reddit.com holds visitor cookies (loid, csrf_token,
# token_v2, ...) and still gets 403. The signed-in session is the one called reddit_session,
# so an extractor that returns cookies without it has not found a login.
LOGIN_COOKIE = "reddit_session"
NO_LOGIN = ("found reddit cookies, but no login session among them "
            f"(no {LOGIN_COOKIE}; is that browser signed in to reddit.com?)")


def refresh_report(path=None, providers=None):
    """Try each available extractor in order; save the first non-empty result.

    Returns (provider_name, count, failures). `failures` is a list of
    (provider_name, reason) for every extractor that was tried and did not yield
    cookies, so a caller can say why instead of just that it failed. On total
    failure provider_name is None and count is 0.
    """
    failures = []
    for name, avail, extract in (providers or PROVIDERS):
        if not avail():
            continue
        try:
            cookies = extract()
        except ExtractError as e:
            failures.append((name, _clip(e)))
            continue
        except Exception as e:      # an extractor library failing must not abort the others
            failures.append((name, _clip(f"{type(e).__name__}: {e}")))
            continue
        if cookies and LOGIN_COOKIE not in cookies:
            failures.append((name, NO_LOGIN))       # visitor cookies only: try the next one
            continue
        if cookies:
            save_cookies(cookies, path)
            return name, len(cookies), failures
        failures.append((name, NO_COOKIES))
    return None, 0, failures


def refresh(path=None, providers=None):
    """Like `refresh_report`, without the failure reasons: (provider_name, count)."""
    name, n, _failures = refresh_report(path, providers)
    return name, n


def hint_for(provider, reason):
    """A next step for a failure we recognise, else None."""
    if provider == "agent-browser" and "No running Chrome" in reason:
        return ("this route needs your Chrome to have remote debugging on, which recent "
                "Chrome ignores on its default profile. Export your cookies by hand "
                "instead (see below), or start Chrome with remote debugging on its own "
                "--user-data-dir.")
    if reason == NO_LOGIN:
        return ("sign in to reddit.com in that browser, then re-run "
                "`reddit-search refresh-cookies`.")
    return None
