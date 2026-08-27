#!/usr/bin/env python3
"""
archive_charts.py — file downloaded CCF (Comprehensive Chart File) zips into the
permanent per-track results database.

Charts are the OUTCOME half of the modelling data (DRS = predictors, CCF = who
actually won). Without them the accumulated DRS cards can't fit a model. This is
the twin of archive_drfs.py.

A CCF download is a ZIP per card containing exactly SIX files:

    SAR07112026.1   race conditions / header
    SAR07112026.2   entries + jockeys
    SAR07112026.3   win/place/show payouts
    SAR07112026.4   exotic payouts
    SAR07112026.5   breeding / ownership
    SAR07112026.6   trip comments

That is byte-for-byte the same layout already on disk (e.g. SAR07242015.1-.6),
so NO conversion is needed - just unzip into:

    <BTSM>/<TRACK>/RAW_DATA/RESULTS/<YEAR>/<TRACK><MMDDYYYY>.<n>

Track + date are read from the INNER filenames (canonical), never the zip name.
Idempotent: cards already filed are skipped.

Usage:
    python archive_charts.py                 # CCF_Downloads -> RESULTS db
    python archive_charts.py --dry-run
    python archive_charts.py --src <dir> --dest <BTSM root>
"""
from __future__ import annotations
import argparse, re, shutil, zipfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent      # ...\BTSM\FullAutomation
BTSM = HERE.parent                          # ...\BTSM

# modern Equibase code -> canonical DB folder (matches archive_drfs.py)
CANON = {"CD": "CDX", "GP": "GPX", "FG": "FGX", "SA": "SAX"}

# inner chart filename: TRACK + MMDDYYYY + . + filetype(1-6)
INNER = re.compile(r"^([A-Za-z]{2,4})(\d{2})(\d{2})(\d{4})\.(\d+)$")


def canon(t: str) -> str:
    t = t.upper()
    return CANON.get(t, t)


def parse_inner(name: str):
    m = INNER.match(Path(name).name)
    if not m:
        return None
    trk, mm, dd, yyyy, part = m.groups()
    return canon(trk), yyyy, f"{mm}{dd}", int(part)


def main():
    ap = argparse.ArgumentParser(description="Archive CCF chart zips into the RESULTS db")
    ap.add_argument("--src", default=str(HERE / "CCF_Downloads"))
    ap.add_argument("--dest", default=str(BTSM))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-zip", action="store_true", help="don't delete the zip after filing")
    a = ap.parse_args()
    src, dest = Path(a.src), Path(a.dest)
    if not src.exists():
        print(f"source dir not found: {src}"); return

    added = Counter(); skipped = bad = cards = 0
    # Dedup by real path: on Windows glob is CASE-INSENSITIVE, so "*.zip" and
    # "*.ZIP" return the SAME files. Concatenating them listed every zip twice;
    # the first pass filed + unlink()'d it, the second hit "No such file" and
    # counted a phantom "bad zip" (the 89-filed / 89-bad symmetry). One set.
    zips = {p.resolve(): p for p in list(src.glob("*.zip")) + list(src.glob("*.ZIP"))}
    for z in sorted(zips.values()):
        try:
            zf = zipfile.ZipFile(z)
        except Exception as e:
            print(f"  !! not a zip: {z.name} ({e})"); bad += 1; continue
        members = [m for m in zf.namelist() if parse_inner(m)]
        if not members:
            print(f"  !! no chart files inside: {z.name}"); bad += 1; continue
        cards += 1
        filed_any = False
        for m in members:
            trk, yyyy, mmdd, part = parse_inner(m)
            outdir = dest / trk / "RAW_DATA" / "RESULTS" / yyyy
            target = outdir / Path(m).name
            if target.exists():
                skipped += 1; continue
            if not a.dry_run:
                outdir.mkdir(parents=True, exist_ok=True)
                with zf.open(m) as fsrc, open(target, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)
            added[f"{trk} {yyyy}"] += 1
            filed_any = True
        n = len(members)
        if n != 6:
            print(f"  ~  {z.name}: {n} chart files (expected 6) - check for a partial card")
        zf.close()
        if filed_any and not a.dry_run and not a.keep_zip:
            try: z.unlink()
            except Exception: pass
    verb = "would file" if a.dry_run else "filed"
    print(f"Scanned {cards} card zip(s) in {src}")
    print(f"{verb}: {sum(added.values())} chart file(s)  |  already in db: {skipped}  |  bad/skipped zips: {bad}")
    if added:
        print("By track/year:")
        for k in sorted(added):
            print(f"  {k}: {added[k]}  ({added[k]/6:.0f} cards)")


if __name__ == "__main__":
    main()
