"""Stdlib-only HTTP layer for Reddit's public .json endpoints (and PullPush).

No third-party dependencies — uses urllib. Centralizes User-Agent, politeness
(cross-process throttle), retry/backoff, and reddit-scoped cookie handling.
"""

import json as _json
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlparse

RETRYABLE = {429, 500, 502, 503, 504}

# A realistic desktop User-Agent — Reddit blocks empty/generic ones.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class RedditAuthError(RuntimeError):
    """Raised when Reddit rejects the request as unauthenticated (HTTP 403)."""


def is_reddit_url(url):
    """Return True only if *url* targets reddit.com or a subdomain."""
    host = (urlparse(url).hostname or "").lower()
    return host == "reddit.com" or host.endswith(".reddit.com")


class _Response:
    """Minimal response matching the seam RedditClient (and tests) expect."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return _json.loads(self._body.decode("utf-8"))

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class UrllibSession:
    """stdlib HTTP GET with the interface RedditClient/tests expect.

    Returns a _Response for every HTTP status (including 4xx/5xx) so the caller's
    retry / 403 logic can inspect status_code; only genuine transport failures
    (urllib.error.URLError) propagate.
    """

    def get(self, url, params=None, headers=None, timeout=None, cookies=None):
        if params:
            sep = "&" if urlparse(url).query else "?"
            url = url + sep + urlencode(params)
        req = urllib.request.Request(url, headers=headers or {})
        if cookies:
            req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _Response(resp.status, resp.read())
        except urllib.error.HTTPError as e:
            # Non-2xx arrives as an exception; surface as a response so the caller's
            # retry/403 logic can inspect status_code. Transport errors (URLError)
            # intentionally propagate un-retried (retries are keyed on status only).
            return _Response(e.code, e.read())


class RedditClient:
    def __init__(self, user_agent=DEFAULT_USER_AGENT, request_delay=1.0,
                 max_retries=4, session=None, sleep=time.sleep,
                 cookies=None, throttle=None):
        if not user_agent or not user_agent.strip():
            raise ValueError("user_agent is required — Reddit blocks generic ones.")
        self.user_agent = user_agent
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.session = session or UrllibSession()
        self._sleep = sleep
        self.cookies = cookies
        # Optional cross-process throttle (zero-arg callable). Paces reddit
        # requests globally; non-reddit hosts (PullPush) use request_delay.
        self.throttle = throttle

    def get_json(self, url, params=None):
        last = None
        reddit = is_reddit_url(url)
        req_cookies = self.cookies if reddit else None
        for attempt in range(self.max_retries):
            if reddit and self.throttle:
                self.throttle()
            elif self.request_delay:
                self._sleep(self.request_delay)
            resp = self.session.get(
                url, params=params,
                headers={"User-Agent": self.user_agent}, timeout=30,
                cookies=req_cookies,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in RETRYABLE:
                last = RuntimeError(f"HTTP {resp.status_code} for {url}")
                self._sleep(2 ** attempt)
                continue
            if resp.status_code == 403:
                raise RedditAuthError(
                    "Reddit returned 403 — cookies missing or expired. "
                    "Run: reddit-search refresh-cookies"
                )
            resp.raise_for_status()
            return resp.json()
        raise last or RuntimeError(f"Failed to GET {url}")
