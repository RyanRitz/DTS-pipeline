"""
upload_to_dts.py — DTS Pipeline → DTS Publishing
==================================================
Publishes a generated PDF to Down The Stretch AI by POSTing it to the
`/api/sheets/upload` endpoint with a shared-secret header.

Replaces the `upload_to_btsm()` stub in run_pipeline.py.

Per Publishing Architecture v1.0 §3.2 / §3.3 / §6.2 / §8.

Contract
--------
    upload_to_dts(pdf_path, track, race_date, is_final=False) -> bool

    Returns True on HTTP 2xx response, False on any failure
    (network error, non-2xx HTTP, missing env, missing file).

    On failure, logs the error and fires a Gmail alert via notify.py.
    Severity is INFO/WARNING/CRITICAL per §8.1:
      - CRITICAL : 401/403 (bad secret) or 5xx (endpoint broken) →
                   high-priority subject line, expects human intervention
      - WARNING  : every other failure (4xx, network, timeout) →
                   normal alert, pipeline keeps retrying on next tick

Idempotency (§8.2)
------------------
The upload endpoint upserts on filename. Re-uploading after a partial
failure is harmless. This module makes no attempt to detect "already
uploaded" — that's the endpoint's job.

Environment variables (read from .env in BASE_DIR or process env)
-----------------------------------------------------------------
    DTS_UPLOAD_URL     e.g. https://downthestretch.ai/api/sheets/upload
    DTS_UPLOAD_SECRET  shared secret matching the Vercel env var

CLI
---
    python upload_to_dts.py path/to/sheet.pdf --track LRL --race-date 2026-05-15 [--final] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import requests

# ── Load .env once at import time ───────────────────────────────────────────
# Same convention as notify.py / brisnet_download.py.
try:
    from dotenv import load_dotenv
    # Look for .env next to this file (the FullAutomation folder).
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    # python-dotenv is optional; if absent, env vars must come from the shell.
    pass

log = logging.getLogger(__name__)


# ── Constants ───────────────────────────────────────────────────────────────

# Network timeout: connect 10s, read 30s. PDFs are small; if the server
# hasn't responded in 30s something's wrong and we should fail fast so the
# next tick can retry.
REQUEST_TIMEOUT = (10, 30)

# The endpoint validates label as exactly "PREVIEW" or "FINAL".
LABEL_PREVIEW = "PREVIEW"
LABEL_FINAL   = "FINAL"


# ── notify.py is optional at import time ────────────────────────────────────
# We don't want a broken notifier to take down the upload module.
try:
    from notify import send_failure_email as _send_failure_email
    _NOTIFY_OK = True
except Exception as _imp_err:
    _send_failure_email = None
    _NOTIFY_OK = False
    log.debug("notify.py not importable: %s", _imp_err)


def _alert(severity: str, subject: str, body: str) -> None:
    """
    Send a Gmail alert with severity prefix in the subject.

    Severity levels (§8.1):
      WARNING   — normal failure, pipeline will retry
      CRITICAL  — auth or 5xx; needs human attention

    Silent no-op if notify.py isn't importable — we don't want to break the
    pipeline because the alerter is broken. The failure is still logged.
    """
    if not _NOTIFY_OK or _send_failure_email is None:
        log.warning("[upload_to_dts] would have sent %s alert: %s", severity, subject)
        return

    prefixed = f"[{severity}] DTS upload: {subject}"
    try:
        _send_failure_email(prefixed, body)
    except Exception as e:
        # Never let a broken notifier escape this module.
        log.warning("[upload_to_dts] notify failed: %s", e)


# ── Filename construction ──────────────────────────────────────────────────

def _canonical_filename(race_date: str, track: str, is_final: bool) -> str:
    """
    Build the canonical filename the endpoint expects.

    Format:   YYYYMMDD-TRACK-LABEL.pdf
    Example:  20260515-LRL-FINAL.pdf

    The endpoint independently validates this format and rejects mismatches,
    so we construct it here to keep the pipeline-side caller simple
    (callers pass race_date in YYYY-MM-DD form).
    """
    ymd = race_date.replace("-", "")
    label = LABEL_FINAL if is_final else LABEL_PREVIEW
    return f"{ymd}-{track.upper()}-{label}.pdf"


def _classify_severity(status_code: Optional[int], exc: Optional[BaseException]) -> str:
    """
    Map an outcome to WARNING or CRITICAL per §8.1.

    - 401/403          → CRITICAL (secret mismatch, server-side config bad)
    - 5xx              → CRITICAL (endpoint broken)
    - everything else  → WARNING  (transient, 4xx, network, etc.)
    """
    if status_code in (401, 403):
        return "CRITICAL"
    if status_code is not None and 500 <= status_code < 600:
        return "CRITICAL"
    return "WARNING"


# ── Main entry point ───────────────────────────────────────────────────────

def upload_to_dts(
    pdf_path: Path | str,
    track: str,
    race_date: str,
    is_final: bool = False,
    *,
    dry_run: bool = False,
) -> bool:
    """
    POST a PDF to the DTS upload endpoint.

    Parameters
    ----------
    pdf_path  : path to the generated PDF
    track     : 2-3 letter track code, e.g. "LRL"
    race_date : "YYYY-MM-DD"
    is_final  : True for FINAL sheet, False for PREVIEW
    dry_run   : if True, construct the request but log it instead of sending.

    Returns
    -------
    True on success (2xx), False on any failure.
    """
    pdf_path = Path(pdf_path)
    label = LABEL_FINAL if is_final else LABEL_PREVIEW

    # Normalize race_date to YYYY-MM-DD. The DTS endpoint requires dashes,
    # but the upstream pipeline (discover_drf_files → drf["race_date"])
    # uses YYYYMMDD. Accept both so this module works with any caller.
    if isinstance(race_date, str) and len(race_date) == 8 and race_date.isdigit():
        race_date = f"{race_date[:4]}-{race_date[4:6]}-{race_date[6:8]}"

    fname = _canonical_filename(race_date, track, is_final)

    # ── 1. Sanity checks ────────────────────────────────────────────────
    if not pdf_path.exists():
        log.error("[upload_to_dts] PDF not found: %s", pdf_path)
        _alert(
            "CRITICAL",
            f"PDF not found for {track} {race_date} {label}",
            f"Expected file: {pdf_path}\n\nThis suggests the PDF generator "
            f"failed silently or pdf_path was mis-constructed.",
        )
        return False

    url    = os.environ.get("DTS_UPLOAD_URL")
    secret = os.environ.get("DTS_UPLOAD_SECRET")

    if not url or not secret:
        missing = [n for n, v in (("DTS_UPLOAD_URL", url),
                                   ("DTS_UPLOAD_SECRET", secret)) if not v]
        log.error("[upload_to_dts] missing env vars: %s", ", ".join(missing))
        _alert(
            "CRITICAL",
            "missing env vars",
            f"upload_to_dts could not run because these env vars are unset: "
            f"{', '.join(missing)}\n\n"
            f"Set them in .env (BASE_DIR/.env) and re-run.",
        )
        return False

    size = pdf_path.stat().st_size
    log.info("[upload_to_dts] %s  (%s bytes)  →  %s",
             fname, f"{size:,}", url)

    # ── 2. Dry run ──────────────────────────────────────────────────────
    if dry_run:
        log.info("[upload_to_dts] DRY RUN — would POST:")
        log.info("                URL         : %s", url)
        log.info("                Header      : X-DTS-Upload-Secret: <%d chars>",
                 len(secret))
        log.info("                Form fields : track=%s race_date=%s label=%s",
                 track.upper(), race_date, label)
        log.info("                File        : %s (%s bytes, filename=%s)",
                 pdf_path, f"{size:,}", fname)
        return True

    # ── 3. Build and send the request ──────────────────────────────────
    headers = {"X-DTS-Upload-Secret": secret}
    data = {
        "track":     track.upper(),
        "race_date": race_date,
        "label":     label,
    }

    try:
        with open(pdf_path, "rb") as fh:
            files = {"file": (fname, fh, "application/pdf")}
            resp = requests.post(
                url,
                headers=headers,
                data=data,
                files=files,
                timeout=REQUEST_TIMEOUT,
            )
    except requests.exceptions.Timeout as e:
        log.error("[upload_to_dts] timeout uploading %s: %s", fname, e)
        _alert(
            "WARNING",
            f"timeout uploading {fname}",
            f"Request to {url} timed out after {REQUEST_TIMEOUT[1]}s.\n"
            f"Track: {track}  Date: {race_date}  Label: {label}\n\n"
            f"Pipeline will retry on the next tick.",
        )
        return False
    except requests.exceptions.RequestException as e:
        log.error("[upload_to_dts] network error uploading %s: %s", fname, e)
        _alert(
            "WARNING",
            f"network error uploading {fname}",
            f"Request to {url} failed: {e}\n"
            f"Track: {track}  Date: {race_date}  Label: {label}\n\n"
            f"Pipeline will retry on the next tick.",
        )
        return False

    # ── 4. Check the response ──────────────────────────────────────────
    if 200 <= resp.status_code < 300:
        # Parse the JSON body just to log the public URL — non-fatal if it fails
        try:
            body = resp.json()
            public_url = body.get("url", "(no url in response)")
        except Exception:
            public_url = "(could not parse response body)"
        log.info("[upload_to_dts] OK   %s  →  %s", fname, public_url)
        return True

    # Non-2xx: log + alert with severity classification
    severity = _classify_severity(resp.status_code, None)
    body_snippet = (resp.text or "")[:500]
    log.error("[upload_to_dts] HTTP %d uploading %s: %s",
              resp.status_code, fname, body_snippet)
    _alert(
        severity,
        f"HTTP {resp.status_code} uploading {fname}",
        f"Endpoint: {url}\n"
        f"Status:   {resp.status_code}\n"
        f"Track: {track}  Date: {race_date}  Label: {label}\n\n"
        f"Response body:\n{body_snippet}",
    )
    return False


# ── CLI ─────────────────────────────────────────────────────────────────────

def _main() -> int:
    p = argparse.ArgumentParser(
        description="Upload a DTS PDF to the DTS publishing endpoint.",
    )
    p.add_argument("pdf", type=Path, help="Path to the PDF file")
    p.add_argument("--track",     required=True, help="Track code, e.g. LRL")
    p.add_argument("--race-date", required=True, help="Race date YYYY-MM-DD")
    p.add_argument("--final",     action="store_true",
                   help="Upload as FINAL (default: PREVIEW)")
    p.add_argument("--dry-run",   action="store_true",
                   help="Construct the request but don't send")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Enable DEBUG-level logging")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    ok = upload_to_dts(
        args.pdf,
        track=args.track,
        race_date=args.race_date,
        is_final=args.final,
        dry_run=args.dry_run,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_main())
