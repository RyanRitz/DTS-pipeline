"""
Unit tests for the failure-tracking and email-summary logic added to
brisnet_download_v3.py.

We don't actually send emails — we monkey-patch _send_failure_email
with a recorder and check the subject/body would have been correct.

Five scenarios:
  1. Empty failure list → no email sent (clean run)
  2. One DOWNLOAD_TIMEOUT → "1 file(s) failed" email
  3. ACCOUNT_LOCKED present → "URGENT: Brisnet account locked" subject
  4. CRASH present → "Brisnet downloader crashed" subject
  5. Notify module unavailable → no exception, just a warning log
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import brisnet_download_v3 as bd


# Recorder for what would have been emailed
_sent = []


def _fake_send(subject: str, body: str):
    _sent.append({"subject": subject, "body": body})


def setup_recorder():
    """Install the fake notifier and clear state."""
    bd._send_failure_email = _fake_send
    bd._NOTIFY_OK = True
    bd._FAILURES.clear()
    _sent.clear()


def test_clean_run_sends_no_email():
    setup_recorder()
    bd._send_run_summary_email(downloaded=37, skipped=0)
    assert len(_sent) == 0, f"expected no email on clean run, got: {_sent}"
    print(f"  PASS clean run sends no email")


def test_single_download_timeout():
    setup_recorder()
    bd._record_failure(
        "DOWNLOAD_TIMEOUT",
        "Evangeline Downs (EVD) 20260515: no file appeared after timeout. "
        "URL: https://www.brisnet.com/product/download/2026-05-15/DRS/USA/TB/EVD/D/0/",
    )
    bd._send_run_summary_email(downloaded=36, skipped=0)
    assert len(_sent) == 1, f"expected 1 email, got {len(_sent)}"
    e = _sent[0]
    assert e["subject"] == "Brisnet downloader: 1 file(s) failed", (
        f"unexpected subject: {e['subject']!r}"
    )
    assert "DOWNLOAD_TIMEOUT" in e["body"]
    assert "EVD" in e["body"]
    assert "Downloaded=36" in e["body"]
    print(f"  PASS download timeout: {e['subject']}")


def test_account_locked_elevates_subject():
    setup_recorder()
    bd._record_failure(
        "ACCOUNT_LOCKED",
        "Brisnet account is locked due to too many failed login attempts.",
    )
    bd._record_failure(
        "DOWNLOAD_TIMEOUT",
        "Something else failed too — should still get the urgent subject.",
    )
    bd._send_run_summary_email(downloaded=0, skipped=0)
    assert len(_sent) == 1
    e = _sent[0]
    assert e["subject"] == "URGENT: Brisnet account locked", (
        f"expected URGENT subject when ACCOUNT_LOCKED present, got: {e['subject']!r}"
    )
    assert "ACCOUNT_LOCKED" in e["body"]
    assert "DOWNLOAD_TIMEOUT" in e["body"]   # both still reported
    print(f"  PASS account lockout escalates to: {e['subject']}")


def test_crash_subject():
    setup_recorder()
    bd._record_failure(
        "CRASH",
        "Unhandled exception: SessionNotCreatedException: Chrome instance "
        "exited.\n\nTraceback...",
    )
    bd._send_run_summary_email(downloaded=0, skipped=0)
    assert len(_sent) == 1
    e = _sent[0]
    assert e["subject"] == "Brisnet downloader crashed"
    assert "SessionNotCreatedException" in e["body"]
    print(f"  PASS crash: {e['subject']}")


def test_login_failed_subject():
    setup_recorder()
    bd._record_failure("LOGIN_FAILED", "bad creds")
    bd._send_run_summary_email(downloaded=0, skipped=0)
    assert len(_sent) == 1
    assert _sent[0]["subject"] == "Brisnet login failed"
    print(f"  PASS login failed: {_sent[0]['subject']}")


def test_notify_unavailable_logs_warning_no_crash():
    setup_recorder()
    bd._NOTIFY_OK = False
    bd._send_failure_email = None
    bd._record_failure("DOWNLOAD_TIMEOUT", "x")
    # Should not raise even though notify is offline
    bd._send_run_summary_email(downloaded=0, skipped=0)
    assert len(_sent) == 0, "shouldn't have sent anything when notify is offline"
    print(f"  PASS notify unavailable: graceful no-op")


def test_multiple_failures_grouped_by_kind():
    setup_recorder()
    bd._record_failure("DOWNLOAD_TIMEOUT", "EVD 20260515: URL ...")
    bd._record_failure("DOWNLOAD_TIMEOUT", "HOU 20260516: URL ...")
    bd._record_failure("RENAME_FAILED",    "PRX 20260518: file locked")
    bd._send_run_summary_email(downloaded=33, skipped=0)
    assert len(_sent) == 1
    body = _sent[0]["body"]
    # Both timeouts grouped under one heading
    assert body.count("DOWNLOAD_TIMEOUT") == 1, "kinds should be grouped"
    assert body.count("RENAME_FAILED") == 1
    # All three details listed
    assert "EVD 20260515" in body
    assert "HOU 20260516" in body
    assert "PRX 20260518" in body
    assert "(2)" in body  # group count for DOWNLOAD_TIMEOUT
    assert "(1)" in body  # group count for RENAME_FAILED
    print(f"  PASS multiple failures grouped: {_sent[0]['subject']}")


if __name__ == "__main__":
    print("Failure-tracking / email-summary tests…\n")
    test_clean_run_sends_no_email()
    test_single_download_timeout()
    test_account_locked_elevates_subject()
    test_crash_subject()
    test_login_failed_subject()
    test_notify_unavailable_logs_warning_no_crash()
    test_multiple_failures_grouped_by_kind()
    print("\nAll tests passed ✓")
