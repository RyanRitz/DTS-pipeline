"""
Unit tests for upload_to_dts.py

Pattern follows the existing notify.py / brisnet_download.py tests:
no test framework dependency — just a flat list of test functions and
a small runner at the bottom. Run with:

    python test_upload_to_dts.py

We never make real HTTP calls. The `requests.post` function is
monkey-patched on the upload_to_dts module to return a fake response.
The `_send_failure_email` hook is captured to verify alert behavior.

Scenarios covered:
  1.  Happy path: 200 → returns True, no alert
  2.  Missing PDF file → False, CRITICAL alert
  3.  Missing env vars → False, CRITICAL alert
  4.  Dry-run mode → True, no HTTP call, no alert
  5.  HTTP 401 → False, CRITICAL alert
  6.  HTTP 500 → False, CRITICAL alert
  7.  HTTP 400 → False, WARNING alert
  8.  Network timeout → False, WARNING alert
  9.  Network error → False, WARNING alert
 10.  Filename construction (PREVIEW vs FINAL, case normalization)
 11.  PREVIEW vs FINAL label sent in form data
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

# Make sure we're testing the local module
sys.path.insert(0, str(Path(__file__).parent))

import upload_to_dts as uut   # "unit under test"


# ── Fakes & helpers ─────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code: int, body: dict | str | None = None):
        self.status_code = status_code
        if isinstance(body, dict):
            self._json = body
            self.text  = str(body)
        else:
            self._json = None
            self.text  = body or ""

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


# Captured alerts and the last request that would have been sent
_alerts: list[dict] = []
_last_request: dict = {}


def _fake_alert(subject: str, body: str):
    _alerts.append({"subject": subject, "body": body})


def _make_fake_post(response: FakeResponse | None = None,
                    raise_exc: Exception | None = None):
    """
    Build a fake `requests.post` that captures call args and either returns
    the supplied FakeResponse or raises the supplied exception.
    """
    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        _last_request.clear()
        _last_request.update({
            "url": url,
            "headers": dict(headers or {}),
            "data": dict(data or {}),
            # Drain the file tuple so the test can inspect filename / content
            "files": {
                k: (v[0], v[1].read() if hasattr(v[1], "read") else v[1], v[2])
                for k, v in (files or {}).items()
            },
            "timeout": timeout,
        })
        if raise_exc is not None:
            raise raise_exc
        return response

    return fake_post


def _setup(env_url: str | None = "https://example.test/api/sheets/upload",
           env_secret: str | None = "test-secret-abc123",
           response: FakeResponse | None = None,
           raise_exc: Exception | None = None,
           monkeypatch_env: dict | None = None):
    """Reset captured state and wire fakes into the module."""
    _alerts.clear()
    _last_request.clear()

    # Patch the notify hook directly on the module
    uut._send_failure_email = _fake_alert
    uut._NOTIFY_OK = True

    # Patch env vars by patching os.environ.get directly via setting them
    import os
    for k in ("DTS_UPLOAD_URL", "DTS_UPLOAD_SECRET"):
        os.environ.pop(k, None)
    if env_url is not None:
        os.environ["DTS_UPLOAD_URL"] = env_url
    if env_secret is not None:
        os.environ["DTS_UPLOAD_SECRET"] = env_secret
    if monkeypatch_env:
        for k, v in monkeypatch_env.items():
            os.environ[k] = v

    # Patch the HTTP call
    uut.requests.post = _make_fake_post(response=response, raise_exc=raise_exc)


def _make_pdf() -> Path:
    """Create a small fake PDF on disk and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        prefix="test_", suffix=".pdf", delete=False
    )
    tmp.write(b"%PDF-1.4 fake pdf bytes for test\n")
    tmp.close()
    return Path(tmp.name)


# ── Tests ───────────────────────────────────────────────────────────────────

def test_happy_path_returns_true_no_alert():
    pdf = _make_pdf()
    _setup(response=FakeResponse(
        200, {"ok": True, "url": "https://downthestretch.ai/sheets/20260515-LRL-PREVIEW.pdf"}
    ))
    ok = uut.upload_to_dts(pdf, track="LRL", race_date="2026-05-15", is_final=False)
    assert ok is True, "expected True on 200"
    assert len(_alerts) == 0, f"expected no alert, got {_alerts}"
    assert _last_request["headers"]["X-DTS-Upload-Secret"] == "test-secret-abc123"
    assert _last_request["data"]["track"] == "LRL"
    assert _last_request["data"]["race_date"] == "2026-05-15"
    assert _last_request["data"]["label"] == "PREVIEW"
    fname, content, mime = _last_request["files"]["file"]
    assert fname == "20260515-LRL-PREVIEW.pdf", f"unexpected filename: {fname}"
    assert mime == "application/pdf"
    assert content.startswith(b"%PDF"), "PDF content not sent"
    print("  PASS happy_path_returns_true_no_alert")


def test_missing_pdf_returns_false_alerts_critical():
    _setup()
    ok = uut.upload_to_dts(
        Path("/tmp/does_not_exist_anywhere.pdf"),
        track="LRL", race_date="2026-05-15"
    )
    assert ok is False
    assert len(_alerts) == 1
    assert "[CRITICAL]" in _alerts[0]["subject"]
    assert "PDF not found" in _alerts[0]["subject"]
    print("  PASS missing_pdf_returns_false_alerts_critical")


def test_missing_env_returns_false_alerts_critical():
    pdf = _make_pdf()
    _setup(env_url=None, env_secret=None)
    ok = uut.upload_to_dts(pdf, track="LRL", race_date="2026-05-15")
    assert ok is False
    assert len(_alerts) == 1
    assert "[CRITICAL]" in _alerts[0]["subject"]
    assert "DTS_UPLOAD_URL"    in _alerts[0]["body"]
    assert "DTS_UPLOAD_SECRET" in _alerts[0]["body"]
    print("  PASS missing_env_returns_false_alerts_critical")


def test_dry_run_returns_true_no_http_call_no_alert():
    pdf = _make_pdf()
    _setup(response=FakeResponse(500, "should not be hit"))
    ok = uut.upload_to_dts(pdf, track="lrl", race_date="2026-05-15",
                           is_final=True, dry_run=True)
    assert ok is True, "dry run should always succeed when inputs are valid"
    assert len(_alerts) == 0, "dry run should not alert"
    assert _last_request == {}, "dry run should not make an HTTP call"
    print("  PASS dry_run_returns_true_no_http_call_no_alert")


def test_http_401_returns_false_alerts_critical():
    pdf = _make_pdf()
    _setup(response=FakeResponse(401, {"ok": False, "error": "unauthorized"}))
    ok = uut.upload_to_dts(pdf, track="LRL", race_date="2026-05-15")
    assert ok is False
    assert len(_alerts) == 1
    assert "[CRITICAL]" in _alerts[0]["subject"]
    assert "401" in _alerts[0]["subject"]
    print("  PASS http_401_returns_false_alerts_critical")


def test_http_500_returns_false_alerts_critical():
    pdf = _make_pdf()
    _setup(response=FakeResponse(500, "server error"))
    ok = uut.upload_to_dts(pdf, track="LRL", race_date="2026-05-15")
    assert ok is False
    assert len(_alerts) == 1
    assert "[CRITICAL]" in _alerts[0]["subject"]
    print("  PASS http_500_returns_false_alerts_critical")


def test_http_400_returns_false_alerts_warning():
    pdf = _make_pdf()
    _setup(response=FakeResponse(400, {"ok": False, "error": "bad filename"}))
    ok = uut.upload_to_dts(pdf, track="LRL", race_date="2026-05-15")
    assert ok is False
    assert len(_alerts) == 1
    assert "[WARNING]" in _alerts[0]["subject"]
    print("  PASS http_400_returns_false_alerts_warning")


def test_timeout_returns_false_alerts_warning():
    import requests as _requests
    pdf = _make_pdf()
    _setup(raise_exc=_requests.exceptions.Timeout("read timed out"))
    ok = uut.upload_to_dts(pdf, track="LRL", race_date="2026-05-15")
    assert ok is False
    assert len(_alerts) == 1
    assert "[WARNING]" in _alerts[0]["subject"]
    assert "timeout" in _alerts[0]["subject"]
    print("  PASS timeout_returns_false_alerts_warning")


def test_network_error_returns_false_alerts_warning():
    import requests as _requests
    pdf = _make_pdf()
    _setup(raise_exc=_requests.exceptions.ConnectionError("dns failure"))
    ok = uut.upload_to_dts(pdf, track="LRL", race_date="2026-05-15")
    assert ok is False
    assert len(_alerts) == 1
    assert "[WARNING]" in _alerts[0]["subject"]
    print("  PASS network_error_returns_false_alerts_warning")


def test_filename_construction_preview_and_final_case_normalized():
    # PREVIEW with lowercase track
    name = uut._canonical_filename("2026-05-15", "lrl", is_final=False)
    assert name == "20260515-LRL-PREVIEW.pdf", name

    # FINAL with mixed case
    name = uut._canonical_filename("2026-05-15", "Cd", is_final=True)
    assert name == "20260515-CD-FINAL.pdf", name

    # Date without dashes already
    name = uut._canonical_filename("20260515", "GP", is_final=False)
    assert name == "20260515-GP-PREVIEW.pdf", name
    print("  PASS filename_construction_preview_and_final_case_normalized")


def test_final_label_sent_in_form_data():
    pdf = _make_pdf()
    _setup(response=FakeResponse(200, {"ok": True, "url": "x"}))
    uut.upload_to_dts(pdf, track="LRL", race_date="2026-05-15", is_final=True)
    assert _last_request["data"]["label"] == "FINAL"
    fname, _, _ = _last_request["files"]["file"]
    assert fname == "20260515-LRL-FINAL.pdf"
    print("  PASS final_label_sent_in_form_data")


# ── Runner ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = 0
    print(f"\nRunning {len(tests)} tests for upload_to_dts.py\n")
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
