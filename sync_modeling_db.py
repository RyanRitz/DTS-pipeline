#!/usr/bin/env python3
r"""
sync_modeling_db.py — pull DRFs + chart files from every machine's Google Drive
backup into THIS laptop's modelling database.

Topology:
  DESKTOP  = the only machine that touches Brisnet. Downloads DRS (+CCF),
             scores, posts sheets, and runs archive_drfs.py / archive_charts.py
             so its <TRACK>\RAW_DATA\... is the canonical live archive.
  OLD BOXES= SlimLaptop / BigWorkLaptop backups often hold history no other
             machine has. They are merged in too.
  LAPTOP   = modelling. Files land on local disk so SAS reads fast (not a
             streamed Drive path).

Sources are auto-discovered under "G:\Other computers" because layouts differ
per machine (HomeDesktop\Documents\BTSM vs SlimLaptop\BTSM).

Only ever ADDS files - never deletes, never overwrites, and a --dry-run writes
NOTHING. Safe to re-run; a missed day backfills on the next pass.

Usage:
    python sync_modeling_db.py --list-sources
    python sync_modeling_db.py --dry-run --tracks DMR,CDX,GPX,SAR,KEE,SAX
    python sync_modeling_db.py --tracks DMR
    python sync_modeling_db.py                 # everything (slow over Drive)
"""
from __future__ import annotations
import argparse, os, shutil, sys, time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL_BTSM = HERE.parent
OTHER_COMPUTERS = Path(r"G:\Other computers")
KINDS = ("RACINGFORM", "RESULTS")

# Source folder name -> canonical DB folder (must match archive_drfs.py /
# archive_charts.py / brisnet_ccf.py). A machine running a pre-fix archive_drfs
# files Santa Anita under SA\; the modelling DB and 5.sas expect SAX\. Without
# this, syncing recreates the SA/SAX split. The X is Brisnet's suffix for
# 2-letter track codes - 3-letter codes (DMR, SAR, KEE) are already canonical.
CANON = {"CD": "CDX", "GP": "GPX", "FG": "FGX", "SA": "SAX"}


def canon(t: str) -> str:
    return CANON.get(t.upper(), t.upper())


def _subdirs(path: Path):
    """Immediate subdirectories, via scandir (no per-entry stat)."""
    try:
        with os.scandir(path) as it:
            return sorted((Path(e.path) for e in it if e.is_dir()), key=lambda p: p.name)
    except OSError:
        return []


def _files(path: Path):
    """
    Immediate files, via scandir. entry.is_file() reads the type from the
    directory entry itself - Path.iterdir()+is_file() issues an os.stat PER FILE,
    which over Google Drive streaming means thousands of round-trips and looks
    like a hang.
    """
    try:
        with os.scandir(path) as it:
            return [Path(e.path) for e in it if e.is_file()]
    except OSError:
        return []


def machine_of(src: Path) -> str:
    try:
        return src.relative_to(OTHER_COMPUTERS).parts[0]
    except ValueError:
        return src.name


def discover_sources():
    found = []
    if not OTHER_COMPUTERS.exists():
        return found
    for machine in _subdirs(OTHER_COMPUTERS):
        for cand in (machine / "BTSM", machine / "Documents" / "BTSM"):
            try:
                if cand.is_dir():
                    found.append(cand)
            except OSError:
                pass
    return found


def main():
    ap = argparse.ArgumentParser(description="Mirror every machine's RAW_DATA into the local modelling DB")
    ap.add_argument("--src", default="", help="comma list of source BTSM roots (default: auto-discover)")
    ap.add_argument("--dest", default=str(LOCAL_BTSM))
    ap.add_argument("--tracks", default="", help="comma list, e.g. DMR,CDX (default: all)")
    ap.add_argument("--year", default="")
    ap.add_argument("--dry-run", action="store_true", help="report only - writes nothing at all")
    ap.add_argument("--list-sources", action="store_true")
    a = ap.parse_args()

    dest = Path(a.dest)
    sources = ([Path(x.strip()) for x in a.src.split(",") if x.strip()]
               if a.src else discover_sources())
    sources = [s for s in sources if s.is_dir()]
    if not sources:
        print(f"[!] No source BTSM roots found under {OTHER_COMPUTERS}")
        print("    Is Google Drive running / is 'Other computers' synced?")
        sys.exit(1)

    print("Sources:")
    for s in sources:
        print(f"   [{machine_of(s)}] {s}")
    print(flush=True)
    if a.list_sources:
        return

    want = {canon(t) for t in a.tracks.split(",") if t.strip()}   # --tracks SA or SAX both work
    added = Counter(); per_machine = Counter()
    planned: set[Path] = set()          # in-memory only - dry-run writes NOTHING
    skipped = scanned = copied = 0
    t0 = time.time()

    for src in sources:
        mach = machine_of(src)
        trackdirs = _subdirs(src)
        for trackdir in trackdirs:
            track_raw = trackdir.name.upper()
            track = canon(track_raw)
            if want and track not in want:
                continue
            for kind in KINDS:
                kdir = trackdir / "RAW_DATA" / kind
                if not kdir.is_dir():
                    continue
                ydirs = _subdirs(kdir)
                for ydir in ydirs:
                    if a.year and ydir.name != a.year:
                        continue
                    entries = _files(ydir)
                    print(f"  scanning {mach:15} {track:5} {kind:10} {ydir.name} ({len(entries)})", flush=True)
                    for f in entries:
                        scanned += 1
                        # Canon-map the FILENAME prefix too, not just the folder:
                        # SA0508.DRF -> SAX0508.DRF. Otherwise the same card ends
                        # up twice in SAX\ once a fixed archive_drfs.py writes the
                        # canonical name, and 5.sas would read it twice.
                        name = f.name
                        # ...but only if it is not ALREADY canonical: a GP\ folder
                        # can legitimately hold GPX0326.DRF, and "GPX0326"
                        # startswith("GP") -> a naive remap yields GPXX0326.DRF.
                        if (track != track_raw
                                and name.upper().startswith(track_raw)
                                and not name.upper().startswith(track)):
                            name = track + name[len(track_raw):]
                        target = dest / track / "RAW_DATA" / kind / ydir.name / name
                        if target in planned or target.exists():
                            skipped += 1
                            continue
                        planned.add(target)
                        if not a.dry_run:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(f, target)
                            copied += 1
                        added[f"{track} {kind} {ydir.name}"] += 1
                        per_machine[mach] += 1

    dt = time.time() - t0
    verb = "would copy" if a.dry_run else "copied"
    print(f"\nScanned {scanned} file(s) across {len(sources)} source(s) in {dt:.0f}s")
    print(f"{verb}: {sum(added.values())}   already local: {skipped}")
    if per_machine:
        print("\nContribution by machine:")
        for k in sorted(per_machine):
            print(f"  {k}: {per_machine[k]}")
    if added:
        print("\nNew files by track / kind / year:")
        for k in sorted(added):
            print(f"  {k}: {added[k]}")
    else:
        print("\nLocal modelling DB is already up to date.")


if __name__ == "__main__":
    main()
