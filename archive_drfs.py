#!/usr/bin/env python3
"""
archive_drfs.py — promote downloaded single-file DRFs into the permanent,
per-track historical database so we accumulate racing history for free and
never have to re-buy it from the archive.

The daily downloader saves cards as  YYYYMMDD_TRACK_DRS.DRF  into DRF_Downloads/
(past-date ones get moved to DRF_Downloads/archive/). This tool files each one
into the database layout the SAS builder (5.sas) reads:

    <BTSM>/<TRACK>/RAW_DATA/RACINGFORM/<YEAR>/<TRACK><MMDD>.DRF

- Idempotent: skips any card already in the DB.
- Copies by default (never disturbs the live pipeline's working files); --move
  to reclaim space from the aged-out archive folder.
- Validates each file looks like a real DRF (zip/CSV, not an HTML error blob).
- Canonicalizes modern Equibase codes to the existing legacy folders (CD->CDX,
  GP->GPX, FG->FGX); everything else keeps its own code.

Run once to backfill, then schedule daily/weekly so every track the downloader
pulls is retained permanently.

Usage:
    python archive_drfs.py                # copy DRF_Downloads(+archive) -> DB
    python archive_drfs.py --dry-run      # preview only
    python archive_drfs.py --move         # move instead of copy
"""
from __future__ import annotations
import argparse, re, shutil
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent            # ...\BTSM\FullAutomation
BTSM = HERE.parent                                 # ...\BTSM
SRC_DIRS = [HERE / "DRF_Downloads", HERE / "DRF_Downloads" / "archive"]

# modern Equibase code -> canonical DB folder (existing legacy X-folders)
CANON = {"CD": "CDX", "GP": "GPX", "FG": "FGX"}

# downloader output stem: YYYYMMDD_TRACK_DRS
PAT = re.compile(r"^(\d{4})(\d{2})(\d{2})_([A-Za-z]{2,4})_DRS$", re.I)


def canon(track: str) -> str:
    t = track.upper()
    return CANON.get(t, t)


def looks_like_drf(p: Path) -> bool:
    try:
        head = p.open("rb").read(4)
    except Exception:
        return False
    return head[:1] not in (b"<", b"{") and p.stat().st_size > 500


def parse(p: Path):
    m = PAT.match(p.stem)
    if not m:
        return None
    yyyy, mm, dd, trk = m.groups()
    return canon(trk), yyyy, mm + dd


def main():
    ap = argparse.ArgumentParser(description="Archive downloaded DRFs into the per-track DB")
    ap.add_argument("--dry-run", action="store_true", help="preview only, write nothing")
    ap.add_argument("--move", action="store_true", help="move instead of copy")
    ap.add_argument("--dest", default=str(BTSM), help="BTSM root that holds <TRACK>/ folders")
    a = ap.parse_args()
    dest_base = Path(a.dest)

    added = Counter()
    skipped = bad = ambiguous = seen = 0
    src_present = [d for d in SRC_DIRS if d.exists()]

    for src in src_present:
        for p in sorted(src.glob("*.DRF")):
            seen += 1
            info = parse(p)
            if not info:
                ambiguous += 1
                continue
            trk, yyyy, mmdd = info
            if not looks_like_drf(p):
                bad += 1
                continue
            out_dir = dest_base / trk / "RAW_DATA" / "RACINGFORM" / yyyy
            target = out_dir / f"{trk}{mmdd}.DRF"
            if target.exists():
                skipped += 1
                continue
            if not a.dry_run:
                out_dir.mkdir(parents=True, exist_ok=True)
                if a.move:
                    shutil.move(str(p), str(target))
                else:
                    shutil.copy2(str(p), str(target))
            added[f"{trk} {yyyy}"] += 1

    verb = "would archive" if a.dry_run else ("moved" if a.move else "copied")
    print(f"Scanned {seen} file(s) across {len(src_present)} source dir(s).")
    print(f"{verb}: {sum(added.values())}  |  already in DB: {skipped}  |  "
          f"unparseable name: {ambiguous}  |  not-a-DRF: {bad}")
    if added:
        print("By track/year:")
        for k in sorted(added):
            print(f"  {k}: {added[k]}")


if __name__ == "__main__":
    main()
