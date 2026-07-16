#!/usr/bin/env python3
r"""
extract_brisnet_order.py — unpack a Brisnet bulk order into the RAW_DATA database.

Bobby's archives arrive as a zip-of-zips:

    <stamp>/drs/<year>/dmr0817k.zip        -> contains DMR0817.DRF          (one card)
    <stamp>/ccf/<year>/dmr08192017c.zip    -> contains DMR08192017.1 .. .6  (six chart files)

which map exactly onto the existing layout:

    <BTSM>/<TRACK>/RAW_DATA/RACINGFORM/<year>/<TRACK><MMDD>.DRF
    <BTSM>/<TRACK>/RAW_DATA/RESULTS/<year>/<TRACK><MMDDYYYY>.<n>

Notes
  - DRS filenames carry only MMDD, so the YEAR comes from the folder.
  - Every DRF is verified against the date INSIDE the file before filing. We
    already got burned once by a mislabelled card, and a silently wrong date
    poisons the model fit.
  - Adds only; never overwrites. Re-runnable.

Usage:
    python extract_brisnet_order.py --zip "DMR\RAW_DATA\Archive-07-15-2026-142539.zip" --dest .
    python extract_brisnet_order.py --zip <path> --dest <BTSM root> --dry-run
"""
from __future__ import annotations
import argparse, csv, io, re, sys, zipfile
from collections import Counter
from pathlib import Path

DRS_NAME = re.compile(r"^([a-z]{2,4})(\d{2})(\d{2})k$", re.I)          # dmr0817k
CCF_NAME = re.compile(r"^([a-z]{2,4})(\d{2})(\d{2})(\d{4})c$", re.I)   # dmr08192017c
CANON = {"CD": "CDX", "GP": "GPX", "FG": "FGX"}


def canon(t): return CANON.get(t.upper(), t.upper())


def drf_date(blob: bytes):
    """(track, YYYYMMDD) from inside the DRF: col0=Track, col1=Date."""
    try:
        row = next(csv.reader(io.StringIO(blob.replace(b"\x00", b"").decode("latin-1"))))
        return row[0].strip().upper(), row[1].strip().replace("-", "").replace("/", "")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--dest", default=".", help="BTSM root")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    dest = Path(a.dest)
    outer = zipfile.ZipFile(a.zip)

    added = Counter(); skipped = bad = 0
    cards = charts = 0

    for name in outer.namelist():
        if name.endswith("/"):
            continue
        parts = Path(name).parts
        stem = Path(name).stem
        kind = "drs" if "/drs/" in name.replace("\\", "/") else ("ccf" if "/ccf/" in name.replace("\\", "/") else None)
        if not kind:
            continue
        year_from_dir = next((p for p in parts if re.fullmatch(r"20\d{2}", p)), None)
        try:
            inner_bytes = outer.read(name)
            inner = zipfile.ZipFile(io.BytesIO(inner_bytes))
        except Exception:
            bad += 1; continue

        if kind == "drs":
            m = DRS_NAME.match(stem)
            if not m or not year_from_dir:
                bad += 1; continue
            trk, mm, dd = m.groups()
            for member in inner.namelist():
                blob = inner.read(member)
                ident = drf_date(blob)
                if not ident:
                    bad += 1; continue
                itrk, idate = ident
                if idate[:4] != year_from_dir or idate[4:] != f"{mm}{dd}":
                    print(f"  !! {name}: folder says {year_from_dir}{mm}{dd} but file says {idate} - filing under TRUE date")
                folder = canon(itrk or trk)
                out = dest / folder / "RAW_DATA" / "RACINGFORM" / idate[:4] / f"{folder if folder==itrk else itrk}{idate[4:]}.DRF"
                out = out.with_name(f"{itrk}{idate[4:]}.DRF")
                if out.exists():
                    skipped += 1; continue
                if not a.dry_run:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(blob)
                added[f"{folder} RACINGFORM {idate[:4]}"] += 1
                cards += 1
        else:
            m = CCF_NAME.match(stem)
            if not m:
                bad += 1; continue
            trk, mm, dd, yyyy = m.groups()
            folder = canon(trk)
            for member in inner.namelist():
                mem = Path(member).name
                out = dest / folder / "RAW_DATA" / "RESULTS" / yyyy / mem
                if out.exists():
                    skipped += 1; continue
                if not a.dry_run:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(inner.read(member))
                added[f"{folder} RESULTS {yyyy}"] += 1
                charts += 1

    verb = "would write" if a.dry_run else "wrote"
    print(f"\n{verb}: {sum(added.values())} file(s)   ({cards} cards, {charts} chart files)")
    print(f"already present: {skipped}   unreadable/skipped: {bad}")
    if added:
        print("\nBy track / kind / year:")
        for k in sorted(added):
            n = added[k]
            extra = f"  ({n//6} cards)" if "RESULTS" in k else ""
            print(f"  {k}: {n}{extra}")


if __name__ == "__main__":
    main()
