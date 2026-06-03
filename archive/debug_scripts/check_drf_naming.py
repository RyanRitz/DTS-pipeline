"""
BTSM Pipeline — DRF naming sanity check
========================================
Scans every .DRF file in DRF_Downloads/ and reports any file where
the embedded track/date inside the file disagrees with the filename.

Usage:
    python check_drf_naming.py

What it checks (per file):
    1. File magic bytes — is this a ZIP or plain CSV?
    2. First row's track + date columns (cols 0 and 1 in BRIS schema)
    3. Filename's encoded track + date (parsed via filename pattern)

Outputs a table:
    OK     — filename matches contents
    MISMATCH  — filename says X, file contains Y (BUG)
    UNREADABLE — file couldn't be parsed (likely corruption)

If MISMATCHes are found, the orchestrator has been scoring the wrong
data. Fix the downloader/renaming step that produced the bad files.
"""

from __future__ import annotations
import io
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
DRF_DIR = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\DRF_Downloads")
# ─────────────────────────────────────────────────────────────────────────────


# Both naming conventions we've seen in this project:
#   YYYYMMDD_TRACK_DRS.DRF      e.g. 20260509_CD_DRS.DRF
#   TRACKMMDD.DRF               e.g. CDX0509.DRF
PATTERN_NEW = re.compile(r"^(?P<date>\d{8})_(?P<track>[A-Z]+)_DRS\.DRF$", re.I)
PATTERN_OLD = re.compile(r"^(?P<track>[A-Z]+)(?P<mmdd>\d{4})\.DRF$",      re.I)


def parse_filename(fname: str) -> tuple[str | None, str | None, str]:
    """Return (track, date_yyyymmdd, pattern_name)."""
    m = PATTERN_NEW.match(fname)
    if m:
        return m.group("track").upper(), m.group("date"), "NEW"
    m = PATTERN_OLD.match(fname)
    if m:
        # Old pattern lacks year — assume current year context
        # Caller can compare just track + MMDD if needed
        return m.group("track").upper(), "????" + m.group("mmdd"), "OLD"
    return None, None, "UNKNOWN"


def read_drf_header(path: Path) -> tuple[str | None, str | None]:
    """Return (track, date_yyyymmdd) parsed from the first row of the DRF."""
    with open(path, "rb") as f:
        magic = f.read(4)

    if magic[:2] == b"PK":
        with zipfile.ZipFile(path) as zf:
            inner = next((n for n in zf.namelist() if n.upper().endswith(".DRF")),
                         zf.namelist()[0])
            data = zf.read(inner)
        src = io.BytesIO(data)
    else:
        src = path

    try:
        # Just the first row — col 0 = track, col 1 = date
        df = pd.read_csv(src, header=None, encoding="latin-1",
                         dtype=str, on_bad_lines="warn", nrows=1)
        track = str(df.iloc[0, 0]).strip().upper()
        date  = str(df.iloc[0, 1]).strip()
        return track, date
    except Exception as e:
        return None, f"ERROR: {e}"


def main():
    if not DRF_DIR.exists():
        print(f"DRF_Downloads not found: {DRF_DIR}")
        sys.exit(1)

    files = sorted(DRF_DIR.glob("*.DRF"))
    if not files:
        print(f"No .DRF files in {DRF_DIR}")
        sys.exit(0)

    print(f"Scanning {len(files)} files in {DRF_DIR}\n")
    print(f"{'Filename':<35} {'Filename says':<14} {'File contains':<14} {'Status'}")
    print("-" * 90)

    n_ok        = 0
    n_mismatch  = 0
    n_unreadable = 0
    mismatches  = []

    for p in files:
        fn_track, fn_date, _pat = parse_filename(p.name)
        ct_track, ct_date = read_drf_header(p)

        # Format display
        fn_str = f"{fn_track or '?'}/{fn_date or '?'}"
        ct_str = f"{ct_track or '?'}/{ct_date or '?'}"

        # Status decision
        if ct_track is None or (ct_date and ct_date.startswith("ERROR")):
            status = "UNREADABLE"
            n_unreadable += 1
        elif fn_track is None:
            status = "UNKNOWN_PATTERN"
            n_unreadable += 1
        else:
            track_ok = (fn_track == ct_track) or (fn_track + "X" == ct_track) or \
                       (fn_track == ct_track + "X")
            # Date: NEW pattern stores full YYYYMMDD; OLD stores just MMDD
            if fn_date and fn_date.startswith("????"):
                # OLD pattern — only MMDD is reliable
                date_ok = ct_date and len(ct_date) >= 8 and \
                          fn_date[-4:] == ct_date[4:8]
            else:
                date_ok = (fn_date == ct_date)

            if track_ok and date_ok:
                status = "OK"
                n_ok += 1
            else:
                status = "MISMATCH"
                n_mismatch += 1
                mismatches.append((p.name, fn_str, ct_str))

        print(f"{p.name:<35} {fn_str:<14} {ct_str:<14} {status}")

    # ── Summary ──────────────────────────────────────────────────────────
    print()
    print(f"  OK:          {n_ok:>4}")
    print(f"  MISMATCH:    {n_mismatch:>4}")
    print(f"  UNREADABLE:  {n_unreadable:>4}")
    print()
    if mismatches:
        print("MISMATCHES (filename says one thing, file contains another):")
        for fname, says, contains in mismatches:
            print(f"  {fname}  |  filename: {says}  |  contents: {contains}")
        print()
        print("Implication: any pipeline run that picked these files by filename")
        print("scored the wrong card. Fix the downloader/renamer that produced them.")
        sys.exit(2)


if __name__ == "__main__":
    main()
