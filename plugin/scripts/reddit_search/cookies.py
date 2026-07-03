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
import shutil
import subprocess
import tempfile

DEFAULT_PATH = "~/.config/reddit-search/cookies.json"
ENV_VAR = "REDDIT_COOKIES"


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
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _flag, _path, _secure, _expiry, name, value = parts
        if _is_reddit_domain(domain) and name:
            out[name] = value
    return out


def load_cookies(path=None):
    """Load cookies from the resolved path, or {} if none present."""
    resolved = resolve_path(path)
    if not os.path.exists(resolved):
        return {}
    with open(resolved) as f:
        return parse_cookies(f.read())


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

def _extract_agent_browser():
    """Export reddit cookies from a running Chrome via the agent-browser CLI."""
    if not shutil.which("agent-browser"):
        return None
    fd, state = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        subprocess.run(["agent-browser", "--auto-connect", "open",
                        "https://www.reddit.com"], check=True, capture_output=True)
        subprocess.run(["agent-browser", "state", "save", state],
                       check=True, capture_output=True)
        with open(state) as f:
            data = json.load(f)
        return {c["name"]: c["value"] for c in data.get("cookies", [])
                if _is_reddit_domain(c.get("domain", ""))}
    except Exception:
        return None
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
        return None
    try:
        jar = browser_cookie3.load(domain_name="reddit.com")
        cookies = {c.name: c.value for c in jar if _is_reddit_domain(c.domain)}
        return cookies or None
    except Exception:
        return None


def _extract_playwright():
    """Best-effort: read reddit cookies from the user's persistent Chrome profile
    via Playwright. Returns None unless Playwright is installed and it works."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    profile = _default_chrome_profile()
    if not profile:
        return None
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(profile, headless=True)
            try:
                cookies = {c["name"]: c["value"] for c in ctx.cookies()
                           if _is_reddit_domain(c.get("domain", ""))}
            finally:
                ctx.close()
        return cookies or None
    except Exception:
        return None


def _extract_selenium():
    """Best-effort: read reddit cookies via Selenium driving the user's Chrome
    profile. Returns None unless Selenium is installed and it works."""
    try:
        from selenium import webdriver
    except ImportError:
        return None
    profile = _default_chrome_profile()
    if not profile:
        return None
    try:
        opts = webdriver.ChromeOptions()
        opts.add_argument("--headless=new")
        opts.add_argument(f"--user-data-dir={profile}")
        driver = webdriver.Chrome(options=opts)
        try:
            driver.get("https://www.reddit.com")
            cookies = {c["name"]: c["value"] for c in driver.get_cookies()
                       if _is_reddit_domain(c.get("domain", ""))}
        finally:
            driver.quit()
        return cookies or None
    except Exception:
        return None


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


def refresh(path=None, providers=None):
    """Try each available extractor in order; save the first non-empty result.

    Returns (provider_name, count) on success, or (None, 0) if none worked.
    """
    for name, avail, extract in (providers or PROVIDERS):
        if not avail():
            continue
        cookies = extract()
        if cookies:
            save_cookies(cookies, path)
            return name, len(cookies)
    return None, 0
