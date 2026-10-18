import pytest

from reddit_search import cookies as ck


@pytest.fixture(autouse=True)
def never_drive_a_real_browser(monkeypatch):
    """No test may launch the real agent-browser against the developer's Chrome.

    pytest.fail is not an Exception subclass, so refresh_report's catch-all cannot
    swallow it: a test that forgot to stub the extractor fails instead of passing
    quietly. A test that needs subprocess.run replaces this patch with its own.
    """
    real_run = ck.subprocess.run

    def guard(argv, *args, **kwargs):
        if argv and argv[0] == "agent-browser":
            pytest.fail("test tried to run the real agent-browser: " + " ".join(argv[:3]))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(ck.subprocess, "run", guard)
