"""Cross-process rate limiting for Reddit access (stdlib-only).

Multiple tools/agents can hit Reddit concurrently on the same authenticated
account; each process pausing individually does not bound the *combined* rate,
which is what risks a ban. This serializes requests across processes via an
exclusive file lock guarding a last-request timestamp. The lock is an
`fcntl.flock`, auto-released by the OS on process exit — so a crash never leaves
a stale lock.

The state path must be on a LOCAL filesystem: flock over NFS/overlay is
unreliable, and serialization would silently not hold. On platforms without
`fcntl` (Windows) the lock degrades to a no-op (timestamp spacing only).
"""

import os
import time as _time

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

DEFAULT_STATE = "~/.cache/reddit-search/ratelimit"


def _read_last(state_path, now):
    """Last-request timestamp, or 0.0 on first run. A present-but-unreadable or
    garbage file is treated as now() — conservative, forcing a full wait."""
    try:
        with open(state_path) as f:
            data = f.read().strip()
    except FileNotFoundError:
        return 0.0
    except OSError:
        return now()
    if not data:
        return now()
    try:
        return float(data)
    except ValueError:
        return now()


def _write_last(state_path, t):
    tmp = state_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(repr(float(t)))
    os.replace(tmp, state_path)


def wait_for_slot(min_interval, state_path, lock_path,
                  now=_time.time, sleep=_time.sleep):
    """Block until the next request is allowed, then record the time. Holds an
    exclusive lock across the wait so concurrent processes queue up >= interval."""
    for path in (lock_path, state_path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    with open(lock_path, "a") as lockf:
        if fcntl is not None:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            wait = _read_last(state_path, now) + min_interval - now()
            if wait > 0:
                sleep(wait)
            _write_last(state_path, now())
        finally:
            if fcntl is not None:
                fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def make_throttle(min_interval, state_path=None, lock_path=None,
                  now=_time.time, sleep=_time.sleep):
    """Return a zero-arg callable enforcing global request spacing. If the state
    dir can't be used, degrades to a per-process delay (warn once) rather than
    failing every request."""
    state_path = os.path.expanduser(state_path or DEFAULT_STATE)
    lock_path = lock_path or (state_path + ".lock")
    warned = [False]

    def throttle():
        try:
            wait_for_slot(min_interval, state_path, lock_path, now=now, sleep=sleep)
        except OSError as e:
            if not warned[0]:
                warned[0] = True
                import sys
                print(f"rate-limit state unavailable ({e}); using a per-process "
                      f"{min_interval}s delay.", file=sys.stderr)
            sleep(min_interval)

    return throttle
