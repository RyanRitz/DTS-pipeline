"""
audit_drf_labels.py — find DRFs whose FILENAME lies about their CONTENTS.

Why this exists (2026-07-19): a Colonial Downs 7/22 card was published to the
site as "Del Mar, July 19". Root cause: load_drf() takes track/date as
ARGUMENTS (from the filename) and stamps them on — it never compares them to
the DRF's own `Track` column and `Date` field. So a mis-named/mis-delivered
file is published under whatever identity the filename claims.

This script is READ-ONLY. It loads every DRF and reports any file where the
internal Track/Date disagree with the filename.

Run on the DESKTOP (that's where the live DRF_Downloads lives):
    .\.venv\Scripts\python.exe audit_drf_labels.py
"""
from __future__ import annotations
import sys, re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from ingest_drf import load_drf

DRF_DIR = _HERE / "DRF_Downloads"
OUT     = _HERE / "drf_label_audit.txt"


def _first_str(series) -> str:
    s = series.dropna().astype(str).str.strip()
    s = s[s != ""]
    return s.mode().iloc[0] if not s.empty else ""


def main() -> int:
    lines: list[str] = []
    def emit(s=""):
        lines.append(s)
        try:
            print(s)
        except Exception:
            pass

    drfs = sorted(DRF_DIR.glob("*.DRF")) + sorted(DRF_DIR.glob("*.drf"))
    if not drfs:
        emit(f"No DRFs in {DRF_DIR}")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return 1

    emit(f"Auditing {len(drfs)} DRF(s) in {DRF_DIR}")
    emit("Comparing FILENAME (claim) vs FILE CONTENTS (truth)")
    emit("")

    bad = ok = skipped = 0
    for path in drfs:
        m = re.match(r"(\d{4})(\d{4})_([A-Za-z]+)", path.stem)
        if not m:
            skipped += 1
            continue
        year, mmdd, claim_track = m.group(1), m.group(2), m.group(3).upper()
        claim_date = f"{year}-{mmdd[:2]}-{mmdd[2:]}"
        try:
            df = load_drf(path, claim_track, mmdd, year)
        except Exception as e:
            emit(f"  ! {path.name}: load failed ({e})")
            skipped += 1
            continue

        real_track = _first_str(df["Track"]) if "Track" in df.columns else "?"
        real_date = ""
        if "Date" in df.columns:
            d = df["Date"].dropna()
            if not d.empty:
                real_date = str(d.iloc[0].date())

        # Track codes: filename may use a 3L canon (GPX/SAX/CDX) vs file 2L.
        t_ok = real_track.upper() in (claim_track, claim_track[:2], claim_track.rstrip("X"))
        d_ok = (real_date == claim_date) or not real_date

        if t_ok and d_ok:
            ok += 1
            continue

        bad += 1
        emit(f"MISMATCH  {path.name}")
        emit(f"    filename claims : track={claim_track:5s} date={claim_date}")
        emit(f"    file contains   : track={real_track:5s} date={real_date}")
        if "HorseName" in df.columns:
            names = df["HorseName"].dropna().unique()[:4]
            emit(f"    sample horses   : {', '.join(names)}")
        emit("")

    emit("")
    emit(f"SUMMARY: {ok} OK, {bad} MISMATCHED, {skipped} skipped/unparsed")
    if bad:
        emit("Any MISMATCHED file was published under the WRONG track/date.")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n---\nWrote {len(lines)} lines to: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
