#!/usr/bin/env python3
"""
sync_modeling_db.py — pull new DRFs + chart files from the home desktop's
archive (via Google Drive) into THIS laptop's modelling database.

Topology:
  DESKTOP  = the only machine that touches Brisnet. Downloads DRS (+CCF),
             scores, posts sheets, and runs archive_drfs.py / archive_charts.py
             so its <TRACK>/RAW_DATA/... is the canonical archive.
             Google Drive backs that folder up to:
                 G:\\Other computers\\HomeDesktop\\Documents\\BTSM
  LAPTOP   = modelling. This script mirrors the desktop's RAW_DATA down to the
             local BTSM so SAS reads fast local disk (not streamed Drive).

Only ever ADDS files - never deletes, never overwrites. Safe to run repeatedly;
re-runs are cheap because existing files are skipped. If a day is missed the
next run backfills it, so a laptop that sleeps or travels loses nothing.

Usage:
    python sync_modeling_db.py --dry-run          # preview
    python sync_modeling_db.py                    # sync everything new
    python sync_modeling_db.py --tracks DMR,CDX   # limit to some tracks
    python sync_modeling_db.py --year 2026        # limit to a year
"""
from __future__ import annotations
import argparse, shutil, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent          # ...\BTSM\FullAutomation
LOCAL_BTSM = HERE.parent                        # ...\BTSM
DESKTOP_BTSM = Path(r"G:\Other computers\HomeDesktop\Documents\BTSM")

KINDS = ("RACINGFORM", "RESULTS")


def main():
    ap = argparse.ArgumentParser(description="Mirror the desktop's RAW_DATA into the local modelling DB")
    ap.add_argument("--src", default=str(DESKTOP_BTSM), help="desktop BTSM (via Google Drive)")
    ap.add_argument("--dest", default=str(LOCAL_BTSM), help="this laptop's BTSM")
    ap.add_argument("--tracks", default="", help="comma list, e.g. DMR,CDX (default: all)")
    ap.add_argument("--year", default="", help="limit to one year, e.g. 2026")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    src, dest = Path(a.src), Path(a.dest)
    if not src.exists():
        print(f"[!] Desktop archive not reachable: {src}")
        print("    Is Google Drive running / is 'Other computers' synced?")
        sys.exit(1)

    want = {t.strip().upper() for t in a.tracks.split(",") if t.strip()}
    added = Counter(); skipped = 0; scanned = 0

    for trackdir in sorted(p for p in src.iterdir() if p.is_dir()):
        track = trackdir.name.upper()
        if want and track not in want:
            continue
        for kind in KINDS:
            kdir = trackdir / "RAW_DATA" / kind
            if not kdir.is_dir():
                continue
            for ydir in sorted(p for p in kdir.iterdir() if p.is_dir()):
                if a.year and ydir.name != a.year:
                    continue
                for f in sorted(ydir.iterdir()):
                    if not f.is_file():
                        continue
                    scanned += 1
                    target = dest / track / "RAW_DATA" / kind / ydir.name / f.name
                    if target.exists():
                        skipped += 1
                        continue
                    if not a.dry_run:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, target)
                    added[f"{track} {kind} {ydir.name}"] += 1

    verb = "would copy" if a.dry_run else "copied"
    print(f"Scanned {scanned} file(s) in {src}")
    print(f"{verb}: {sum(added.values())}   already local: {skipped}")
    if added:
        print("\nNew files by track / kind / year:")
        for k in sorted(added):
            print(f"  {k}: {added[k]}")
    else:
        print("\nLocal modelling DB is already up to date.")


if __name__ == "__main__":
    main()
