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

_DRY_TOUCHED = []
from pathlib import Path

HERE = Path(__file__).resolve().parent          # ...\BTSM\FullAutomation
LOCAL_BTSM = HERE.parent                        # ...\BTSM
OTHER_COMPUTERS = Path(r"G:\Other computers")   # Google Drive backup of every machine

KINDS = ("RACINGFORM", "RESULTS")


def _machine_of(src: Path) -> str:
    """Machine name = the folder directly under 'Other computers'."""
    try:
        return src.relative_to(OTHER_COMPUTERS).parts[0]
    except ValueError:
        return src.name


def discover_sources():
    """
    Find a BTSM root under every machine backed up to Google Drive.

    Layouts differ per machine (HomeDesktop\Documents\BTSM vs SlimLaptop\BTSM),
    and there may be several (HomeDesktop, SlimLaptop, BigWorkLaptop...), so we
    look rather than hard-code. Each is a potential source of history the others
    never had - a track missing on one machine is often complete on another.
    """
    found = []
    if not OTHER_COMPUTERS.exists():
        return found
    for machine in sorted(p for p in OTHER_COMPUTERS.iterdir() if p.is_dir()):
        for cand in (machine / "BTSM", machine / "Documents" / "BTSM"):
            try:
                if cand.is_dir():
                    found.append(cand)
            except OSError:
                pass
    return found


def main():
    ap = argparse.ArgumentParser(description="Mirror the desktop's RAW_DATA into the local modelling DB")
    ap.add_argument("--src", default="", help="comma list of source BTSM roots (default: auto-discover every machine on Drive)")
    ap.add_argument("--dest", default=str(LOCAL_BTSM), help="this laptop's BTSM")
    ap.add_argument("--tracks", default="", help="comma list, e.g. DMR,CDX (default: all)")
    ap.add_argument("--year", default="", help="limit to one year, e.g. 2026")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    dest = Path(a.dest)
    if a.src:
        sources = [Path(x.strip()) for x in a.src.split(",") if x.strip()]
    else:
        sources = discover_sources()
    sources = [s for s in sources if s.is_dir()]
    if not sources:
        print("[!] No source BTSM roots found under", OTHER_COMPUTERS)
        print("    Is Google Drive running / is 'Other computers' synced?")
        sys.exit(1)
    print("Sources:")
    for s_ in sources:
        print(f"   {s_}")
    print()

    want = {t.strip().upper() for t in a.tracks.split(",") if t.strip()}
    added = Counter(); per_source = Counter(); skipped = 0; scanned = 0

    for src in sources:
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
                try:
                    entries = sorted(ydir.iterdir())
                except OSError:
                    continue
                for f in entries:
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
                    else:
                        # so a later source doesn't double-count the same file
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.touch()
                        _DRY_TOUCHED.append(target)
                    added[f"{track} {kind} {ydir.name}"] += 1
                    per_source[_machine_of(src)] += 1

    for t in _DRY_TOUCHED:            # undo dry-run placeholders
        try: t.unlink()
        except OSError: pass
    verb = "would copy" if a.dry_run else "copied"
    print(f"Scanned {scanned} file(s) across {len(sources)} source(s)")
    print(f"{verb}: {sum(added.values())}   already local: {skipped}")
    if per_source:
        print("\nContribution by machine:")
        for k in sorted(per_source):
            print(f"  {k}: {per_source[k]}")
    if added:
        print("\nNew files by track / kind / year:")
        for k in sorted(added):
            print(f"  {k}: {added[k]}")
    else:
        print("\nLocal modelling DB is already up to date.")


if __name__ == "__main__":
    main()
