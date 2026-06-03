"""
Unit tests for cleanup_dts.py

Same pattern as test_upload_to_dts.py: no test framework, monkey-patches
requests.post and the alert hook. Run with:

    python test_cleanup_dts.py

Scenarios:
  1.  Happy path, nothing to delete (the common day-to-day case)
  2.  Happy path, some deletions
  3.  Happy path with partial blob failures → returns True but warns
  4.  Missing env vars → False, CRITICAL alert
  5.  Dry-run mode → True, no HTTP call
  6.  HTTP 401 → False, CRITICAL alert
  7.  HTTP 500 → False, CRITICAL alert
  8.  HTTP 400 → False, WARNING alert
  9.  Timeout → False, WARNING alert
 10.  Network error → False, WARNING alert
 11.  Non-JSON 200 body → True, no alert (defensive parsing)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import cleanup_dts as uut


# ── Fakes ──────────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code: int, body: dict | str | None = None):
        self.status_code = status_code
        if isinstance(body, dict):
            self._json = body
            self.text  = str(body)
        elif body is None:
            self._json = None
            self.text  = ""
        else:
            self._json = None
            self.text  = body

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


_alerts: list[dict] = []
_last_request: dict = {}


def _fake_alert(subject: str, body: str):
    _alerts.append({"subject": subject, "body": body})


def _make_fake_post(response: FakeResponse | None = None,
                    raise_exc: Exception | None = None):
    def fake_post(url, headers=None, timeout=None):
        _last_request.clear()
        _last_request.update({
            "url": url,
            "headers": dict(headers or {}),
            "timeout": timeout,
        })
        if raise_exc is not None:
            raise raise_exc
        return response
    return fake_post


def _setup(env_url: str | None = "https://example.test/api/sheets/cleanup",
           env_secret: str | None = "test-secret-abc123",
           response: FakeResponse | None = None,
           raise_exc: Exception | None = None):
    _alerts.clear()
    _last_request.clear()

    uut._send_failure_email = _fake_alert
    uut._NOTIFY_OK = True

    import os
    for k in ("DTS_CLEANUP_URL", "DTS_UPLOAD_SECRET"):
        os.environ.pop(k, None)
    if env_url is not None:
        os.environ["DTS_CLEANUP_URL"] = env_url
    if env_secret is not None:
        os.environ["DTS_UPLOAD_SECRET"] = env_secret

    uut.requests.post = _make_fake_post(response=response, raise_exc=raise_exc)


# ── Tests ──────────────────────────────────────────────────────────────────

def test_happy_path_nothing_to_delete():
    _setup(response=FakeResponse(200, {
        "ok": True, "cutoff_date": "2026-05-18",
        "blobs_deleted": 0, "rows_deleted": 0, "blob_failures": 0,
        "filenames": [],
    }))
    ok = uut.run_cleanup()
    assert ok is True
    assert len(_alerts) == 0, f"expected no alert, got {_alerts}"
    assert _last_request["headers"]["X-DTS-Upload-Secret"] == "test-secret-abc123"
    print("  PASS happy_path_nothing_to_delete")


def test_happy_path_with_deletions():
    _setup(response=FakeResponse(200, {
        "ok": True, "cutoff_date": "2026-05-18",
        "blobs_deleted": 4, "rows_deleted": 4, "blob_failures": 0,
        "filenames": ["20260517-LRL-FINAL.pdf", "20260517-CD-FINAL.pdf",
                      "20260517-GP-FINAL.pdf", "20260517-SA-FINAL.pdf"],
    }))
    ok = uut.run_cleanup()
    assert ok is True
    assert len(_alerts) == 0
    print("  PASS happy_path_with_deletions")


def test_partial_blob_failures_warns_but_succeeds():
    _setup(response=FakeResponse(200, {
        "ok": True, "cutoff_date": "2026-05-18",
        "blobs_deleted": 3, "rows_deleted": 3, "blob_failures": 1,
        "filenames": ["20260517-LRL-FINAL.pdf"],
    }))
    ok = uut.run_cleanup()
    assert ok is True, "partial blob failure should not fail the run"
    assert len(_alerts) == 1
    assert "[WARNING]" in _alerts[0]["subject"]
    assert "blob delete failure" in _alerts[0]["subject"]
    print("  PASS partial_blob_failures_warns_but_succeeds")


def test_missing_env_returns_false_alerts_critical():
    _setup(env_url=None, env_secret=None)
    ok = uut.run_cleanup()
    assert ok is False
    assert len(_alerts) == 1
    assert "[CRITICAL]" in _alerts[0]["subject"]
    assert "DTS_CLEANUP_URL"   in _alerts[0]["body"]
    assert "DTS_UPLOAD_SECRET" in _alerts[0]["body"]
    print("  PASS missing_env_returns_false_alerts_critical")


def test_dry_run_returns_true_no_http_call():
    _setup(response=FakeResponse(500, "should not be hit"))
    ok = uut.run_cleanup(dry_run=True)
    assert ok is True
    assert len(_alerts) == 0
    assert _last_request == {}, "dry run should not make an HTTP call"
    print("  PASS dry_run_returns_true_no_http_call")


def test_http_401_returns_false_alerts_critical():
    _setup(response=FakeResponse(401, {"ok": False, "error": "unauthorized"}))
    ok = uut.run_cleanup()
    assert ok is False
    assert len(_alerts) == 1
    assert "[CRITICAL]" in _alerts[0]["subject"]
    assert "401" in _alerts[0]["subject"]
    print("  PASS http_401_returns_false_alerts_critical")


def test_http_500_returns_false_alerts_critical():
    _setup(response=FakeResponse(500, "server error"))
    ok = uut.run_cleanup()
    assert ok is False
    assert len(_alerts) == 1
    assert "[CRITICAL]" in _alerts[0]["subject"]
    print("  PASS http_500_returns_false_alerts_critical")


def test_http_400_returns_false_alerts_warning():
    _setup(response=FakeResponse(400, {"ok": False, "error": "bad request"}))
    ok = uut.run_cleanup()
    assert ok is False
    assert len(_alerts) == 1
    assert "[WARNING]" in _alerts[0]["subject"]
    print("  PASS http_400_returns_false_alerts_warning")


def test_timeout_returns_false_alerts_warning():
    import requests as _r
    _setup(raise_exc=_r.exceptions.Timeout("read timed out"))
    ok = uut.run_cleanup()
    assert ok is False
    assert len(_alerts) == 1
    assert "[WARNING]" in _alerts[0]["subject"]
    assert "timeout" in _alerts[0]["subject"]
    print("  PASS timeout_returns_false_alerts_warning")


def test_network_error_returns_false_alerts_warning():
    import requests as _r
    _setup(raise_exc=_r.exceptions.ConnectionError("dns failure"))
    ok = uut.run_cleanup()
    assert ok is False
    assert len(_alerts) == 1
    assert "[WARNING]" in _alerts[0]["subject"]
    print("  PASS network_error_returns_false_alerts_warning")


def test_non_json_200_body_returns_true_no_alert():
    # Defensive: a 200 with a non-JSON body shouldn't crash or alert
    _setup(response=FakeResponse(200, "plain text body for some reason"))
    ok = uut.run_cleanup()
    assert ok is True
    assert len(_alerts) == 0
    print("  PASS non_json_200_body_returns_true_no_alert")


# ── Runner ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = 0
    print(f"\nRunning {len(tests)} tests for cleanup_dts.py\n")
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failures += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failures += 1

    print()
    if failures:
        print(f"{failures} test(s) failed out of {len(tests)}")
        sys.exit(1)
    print(f"All {len(tests)} tests passed")
    sys.exit(0)
