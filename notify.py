"""
notify.py
=========
Send failure-alert emails via Gmail SMTP.

Setup (one-time):
  1. Enable 2-Step Verification on your Google Account
  2. Go to https://myaccount.google.com/apppasswords
  3. Create an App Password named "DTS Pipeline" (16-char password)
  4. Add to .env (matches env.example schema):
       GMAIL_USER=youraddress@gmail.com
       GMAIL_NOTIFY_TO=youraddress@gmail.com     (or any other inbox)
       GMAIL_APP_PASS=xxxx xxxx xxxx xxxx        (the 16-char App Password)

  Legacy variable names (NOTIFY_FROM / NOTIFY_TO / NOTIFY_APP_PASSWORD) are
  still accepted as a fallback, but GMAIL_* takes precedence.

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
    """Send an email via Gmail SMTP. Returns True on success.

    Env vars (in priority order):
      GMAIL_USER       or NOTIFY_FROM           — sender address
      GMAIL_APP_PASS   or NOTIFY_APP_PASSWORD   — 16-char Google App Password
      GMAIL_NOTIFY_TO  or NOTIFY_TO             — recipient (defaults to sender)
    """
    sender = (
        os.environ.get("GMAIL_USER")
        or os.environ.get("NOTIFY_FROM")
        or ""
    )
    password = (
        os.environ.get("GMAIL_APP_PASS")
        or os.environ.get("NOTIFY_APP_PASSWORD")
        or ""
    )
    recipient = (
        os.environ.get("GMAIL_NOTIFY_TO")
        or os.environ.get("NOTIFY_TO")
        or sender
    )

    if not sender or not password:
        print("[notify] GMAIL_USER and GMAIL_APP_PASS must be set in .env",
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
    if len(sys.argv) < 3:
        print("Usage: notify.py <subject> <body>")
        sys.exit(2)
    ok = send_failure_email(sys.argv[1], sys.argv[2])
    sys.exit(0 if ok else 1)
