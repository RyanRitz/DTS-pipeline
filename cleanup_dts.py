"""
cleanup_dts.py — DTS Pipeline → DTS Retention
================================================
Daily cleanup job. POSTs to `/api/sheets/cleanup` on DTS to enforce the
3-day rolling archive.

Per Publishing Architecture v1.0 §3.2 / §9.3.

Contract
--------
    run_cleanup() -> bool

    Returns True on 2xx response, False on any failure.
    Logs the response summary (blobs deleted, rows deleted, blob failures).
    Fires a Gmail alert via notify.py on failure.

Severity (§8.1)
---------------
    CRITICAL : 401/403 (bad secret) or 5xx (endpoint broken)
    WARNING  : 4xx, timeout, network — pipeline keeps running, next day
               retries automatically.

Failure isn't critical to operations — yesterday's expired sheets just
stay around an extra day until the next run sweeps them. Idempotent.

Environment variables
---------------------
    DTS_CLEANUP_URL    e.g. https://downthestretch.ai/api/sheets/cleanup
    DTS_UPLOAD_SECRET  shared secret (same one upload_to_dts uses)

CLI
---
    python cleanup_dts.py [--dry-run] [-v]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import requests

# ── Load .env once at import time ───────────────────────────────────────────
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

log = logging.getLogger(__name__)


# ── Constants ───────────────────────────────────────────────────────────────

# Cleanup can touch a lot of rows in one go (theoretically dozens of stale
# sheets across many tracks). Give it more time than the upload route.
REQUEST_TIMEOUT = (10, 60)


# ── notify.py is optional ──────────────────────────────────────────────────
try:
    from notify import send_failure_email as _send_failure_email
    _NOTIFY_OK = True
except Exception as _imp_err:
    _send_failure_email = None
    _NOTIFY_OK = False
    log.debug("notify.py not importable: %s", _imp_err)


def _alert(severity: str, subject: str, body: str) -> None:
    """
    Send a Gmail alert with severity prefix.
    Silent no-op if notify.py isn't importable.
    """
    if not _NOTIFY_OK or _send_failure_email is None:
        log.warning("[cleanup_dts] would have sent %s alert: %s", severity, subject)
        return

    prefixed = f"[{severity}] DTS cleanup: {subject}"
    try:
        _send_failure_email(prefixed, body)
    except Exception as e:
        log.warning("[cleanup_dts] notify failed: %s", e)


def _classify_severity(status_code: int | None) -> str:
    """Map an HTTP outcome to WARNING or CRITICAL per §8.1."""
    if status_code in (401, 403):
        return "CRITICAL"
    if status_code is not None and 500 <= status_code < 600:
        return "CRITICAL"
    return "WARNING"


# ── Main entry point ───────────────────────────────────────────────────────

def run_cleanup(*, dry_run: bool = False) -> bool:
    """
    POST to the DTS cleanup endpoint.

    Returns True on success (2xx), False on any failure.
    """
    url    = os.environ.get("DTS_CLEANUP_URL")
    secret = os.environ.get("DTS_UPLOAD_SECRET")

    if not url or not secret:
        missing = [n for n, v in (("DTS_CLEANUP_URL", url),
                                   ("DTS_UPLOAD_SECRET", secret)) if not v]
        log.error("[cleanup_dts] missing env vars: %s", ", ".join(missing))
        _alert(
            "CRITICAL",
            "missing env vars",
            f"cleanup_dts could not run because these env vars are unset: "
            f"{', '.join(missing)}\n\n"
            f"Set them in .env (BASE_DIR/.env) and re-run.",
        )
        return False

    log.info("[cleanup_dts] POST %s", url)

    if dry_run:
        log.info("[cleanup_dts] DRY RUN — would POST:")
        log.info("                URL    : %s", url)
        log.info("                Header : X-DTS-Upload-Secret: <%d chars>",
                 len(secret))
        log.info("                Body   : (empty)")
        return True

    headers = {"X-DTS-Upload-Secret": secret}

    try:
        resp = requests.post(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.Timeout as e:
        log.error("[cleanup_dts] timeout: %s", e)
        _alert(
            "WARNING",
            "timeout calling cleanup endpoint",
            f"Request to {url} timed out after {REQUEST_TIMEOUT[1]}s.\n\n"
            f"Stale sheets stay around an extra day. The next scheduled run "
            f"will retry automatically.",
        )
        return False
    except requests.exceptions.RequestException as e:
        log.error("[cleanup_dts] network error: %s", e)
        _alert(
            "WARNING",
            "network error calling cleanup endpoint",
            f"Request to {url} failed: {e}\n\n"
            f"Stale sheets stay around an extra day. The next scheduled run "
            f"will retry automatically.",
        )
        return False

    # ── Parse and report ──────────────────────────────────────────────
    if 200 <= resp.status_code < 300:
        try:
            body = resp.json()
        except Exception:
            log.warning("[cleanup_dts] response was not JSON; body=%r",
                        resp.text[:200])
            return True

        cutoff = body.get("cutoff_date", "?")
        bd     = body.get("blobs_deleted", 0)
        rd     = body.get("rows_deleted",  0)
        bf     = body.get("blob_failures", 0)
        names  = body.get("filenames", [])

        log.info(
            "[cleanup_dts] OK   cutoff=%s  blobs_deleted=%d  rows_deleted=%d  "
            "blob_failures=%d",
            cutoff, bd, rd, bf,
        )
        if names:
            log.info("[cleanup_dts] swept: %s", ", ".join(names))

        # Partial blob failures: surface as a WARNING but don't fail the run
        # (the rows stay in the DB and the next day will retry).
        if bf > 0:
            _alert(
                "WARNING",
                f"{bf} blob delete failure(s) during cleanup",
                f"Cleanup completed but {bf} blob(s) could not be deleted "
                f"from Vercel Blob. Their DB rows remain and the next "
                f"scheduled run will retry.\n\n"
                f"Cutoff: {cutoff}\n"
                f"Blobs deleted: {bd}\n"
                f"Rows deleted:  {rd}\n"
                f"Filenames in this batch: {names}",
            )

        return True

    # Non-2xx: log + alert
    severity = _classify_severity(resp.status_code)
    body_snippet = (resp.text or "")[:500]
    log.error("[cleanup_dts] HTTP %d: %s", resp.status_code, body_snippet)
    _alert(
        severity,
        f"HTTP {resp.status_code} from cleanup endpoint",
        f"Endpoint: {url}\n"
        f"Status:   {resp.status_code}\n\n"
        f"Response body:\n{body_snippet}",
    )
    return False


# ── CLI ─────────────────────────────────────────────────────────────────────

def _main() -> int:
    p = argparse.ArgumentParser(
        description="Trigger the DTS 3-day retention cleanup.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Construct the request but don't send")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Enable DEBUG-level logging")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    ok = run_cleanup(dry_run=args.dry_run)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main())
