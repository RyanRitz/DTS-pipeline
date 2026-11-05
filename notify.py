"""
notify.py
=========
Send failure-alert emails via Gmail SMTP.

Setup (one-time):
  1. Enable 2-Step Verification on your Google Account
  2. Go to https://myaccount.google.com/apppasswords
  3. Create an App Password named "DTS Pipeline" (16-char password)
  4. Add to .env:
       NOTIFY_FROM=youraddress@gmail.com
       NOTIFY_TO=youraddress@gmail.com           (or any other inbox)
       NOTIFY_APP_PASSWORD=xxxx xxxx xxxx xxxx   (the 16-char App Password)

Usage from CLI:
  python notify.py "Subject line" "Body text"

Usage from Python:
  from notify import send_failure_email
  send_failure_email("DTS pipeline failed", "Stage: scoring\nTrack: CD\n...")
"""

import os
import sys
import smtplib
import ssl
from pathlib import Path
from email.message import EmailMessage
from datetime import datetime

# Load .env (same convention as the downloader)
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

GMAIL_HOST = "smtp.gmail.com"
GMAIL_PORT = 465  # SSL


def send_failure_email(subject: str, body: str) -> bool:
    """Send an email via Gmail SMTP. Returns True on success."""
    sender   = os.environ.get("NOTIFY_FROM", "")
    password = os.environ.get("NOTIFY_APP_PASSWORD", "")
    recipient = os.environ.get("NOTIFY_TO", sender)

    if not sender or not password:
        print("[notify] NOTIFY_FROM and NOTIFY_APP_PASSWORD must be set in .env",
              file=sys.stderr)
        return False

    msg = EmailMessage()
    msg["Subject"] = f"[DTS] {subject}"
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.set_content(
        f"DTS pipeline alert — {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        f"{'-' * 50}\n"
        f"{body}\n"
        f"{'-' * 50}\n"
        f"Host: {os.environ.get('COMPUTERNAME', 'unknown')}\n"
    )

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(GMAIL_HOST, GMAIL_PORT, context=ctx, timeout=30) as smtp:
            smtp.login(sender, password.replace(" ", ""))  # strip spaces from app pw
            smtp.send_message(msg)
        return True
    except Exception as e:
        print(f"[notify] send failed: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    # Optional flags (order-independent): --logfile PATH, --tail N.
    # When --logfile is given, the last N lines of that file are appended to
    # the email body so a real failure arrives with its own log tail.
    _args = sys.argv[1:]
    _logfile = None
    _tail_n = 30
    _pos = []
    _i = 0
    while _i < len(_args):
        if _args[_i] == "--logfile" and _i + 1 < len(_args):
            _logfile = _args[_i + 1]; _i += 2; continue
        if _args[_i] == "--tail" and _i + 1 < len(_args):
            try:
                _tail_n = int(_args[_i + 1])
            except ValueError:
                pass
            _i += 2; continue
        _pos.append(_args[_i]); _i += 1
    if len(_pos) < 2:
        print("Usage: notify.py <subject> <body> [--logfile PATH] [--tail N]")
        sys.exit(2)
    _subject, _body = _pos[0], _pos[1]
    if _logfile:
        try:
            with open(_logfile, "r", encoding="utf-8", errors="replace") as _fh:
                _lines = _fh.readlines()
            _tail = "".join(_lines[-_tail_n:]).rstrip()
            _body = f"{_body}\n\nLast {_tail_n} log lines:\n{'-' * 40}\n{_tail}"
        except Exception as _e:
            _body = f"{_body}\n\n(could not read log file {_logfile}: {_e})"
    ok = send_failure_email(_subject, _body)
    sys.exit(0 if ok else 1)
