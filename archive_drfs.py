#!/usr/bin/env python3
"""
archive_drfs.py — promote downloaded single-file DRFs into the permanent,
per-track historical database so we accumulate racing history for free and
never have to re-buy it from the archive.

Sources save cards as  YYYYMMDD_TRACK_DRS.DRF  into DRF_Downloads/ (past-date
ones get moved to DRF_Downloads/archive/). This tool files each one into the
layout the SAS builder (5.sas) reads:

    <BTSM>/<TRACK>/RAW_DATA/RACINGFORM/<YEAR>/<TRACK><MMDD>.DRF

EVERYTHING IS DECIDED BY THE FILE'S CONTENTS, NOT ITS NAME:
  - The daily downloader (brisnet_download.py) saves PLAIN CSV. The historical
    sweep (brisnet_ccf.py) saves the raw BRISnet payload, which is a ZIP holding
    one canonically-named member (e.g. GPX0326.DRF). The old version of this
    tool accepted zips and copy2'd them verbatim, so 141 of 143 GPX 2025 "cards"
    in the DB were zip blobs 5.sas cannot read. We now unzip.
  - The destination folder comes from the TRACK FIELD INSIDE the file, not the
    download filename. A PIM0517.DRF (Pimlico) was found filed under GPX/
    because the name was trusted.
  - The year/date come from the DATE FIELD INSIDE the file. DRS names carry only
    MMDD, and a truncated download once got misattributed to the wrong day.
  - An existing target that is a ZIP gets REPLACED by the unzipped payload; a
    real (plain) target is never overwritten.

Usage:
    python archive_drfs.py                # copy DRF_Downloads(+archive) -> DB
    python archive_drfs.py --dry-run      # preview only, writes nothing
    python archive_drfs.py --move         # move instead of copy
"""
from __future__ import annotations
import argparse, csv, io, re, shutil, zipfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent            # ...\BTSM\FullAutomation
BTSM = HERE.parent                                 # ...\BTSM
SRC_DIRS = [HERE / "DRF_Downloads", HERE / "DRF_Downloads" / "archive"]

# modern Equibase code -> canonical DB folder. The X is BRISnet's suffix for
# 2-letter track codes; 3-letter codes (DMR, SAR, KEE) are already canonical.
# Must stay in sync with archive_charts.py / brisnet_ccf.py / sync_modeling_db.py.
CANON = {"CD": "CDX", "GP": "GPX", "FG": "FGX", "SA": "SAX"}

PAT = re.compile(r"^(\d{4})(\d{2})(\d{2})_([A-Za-z]{2,4})_DRS$", re.I)


def canon(track: str) -> str:
    t = track.strip().upper()
    return CANON.get(t, t)


def payload(p: Path):
    """(inner_name|None, data_bytes|None). Unzips the BRISnet zip wrapper."""
    try:
        raw = p.open("rb").read()
    except OSError:
        return None, None
    if raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                members = [n for n in z.namelist() if n.upper().endswith(".DRF")]
                if len(members) != 1:
                    return None, None
                return Path(members[0]).name, z.read(members[0])
        except (zipfile.BadZipFile, OSError):
            return None, None
    if raw[:1] in (b"<", b"{") or len(raw) < 500:
        return None, None                      # HTML error blob / stub
    return None, raw


def internals(data: bytes):
    """(track, YYYYMMDD) read from the first CSV row. This is the source of truth."""
    try:
        line = data.split(b"\n", 1)[0].decode("latin-1", "replace")
        row = next(csv.reader([line]))
        trk, dt = row[0].strip().upper(), row[1].strip()
        if not re.fullmatch(r"\d{8}", dt):
            return None, None
        return trk, dt
    except Exception:
        return None, None


def main():
    ap = argparse.ArgumentParser(description="Archive downloaded DRFs into the per-track DB")
    ap.add_argument("--dry-run", action="store_true", help="preview only, write nothing")
    ap.add_argument("--move", action="store_true", help="move instead of copy")
    ap.add_argument("--dest", default=str(BTSM), help="BTSM root that holds <TRACK>/ folders")
    a = ap.parse_args()
    dest_base = Path(a.dest)

    added = Counter(); notes = []
    skipped = bad = ambiguous = seen = replaced = renamed = 0

    for src in [d for d in SRC_DIRS if d.exists()]:
        for p in sorted(src.glob("*.DRF")):
            seen += 1
            m = PAT.match(p.stem)
            if not m:
                ambiguous += 1
                continue
            fyyyy, fmm, fdd, ftrk = m.groups()
            inner, data = payload(p)
            if not data:
                bad += 1
                continue
            itrk, idate = internals(data)
            if not itrk:
                bad += 1
                continue

            folder = canon(itrk)
            yyyy, mmdd = idate[:4], idate[4:8]
            name = inner or f"{folder}{mmdd}.DRF"

            if canon(ftrk) != folder:
                notes.append(f"track: {p.name} -> inside says {itrk} -> filing under {folder}")
                renamed += 1
            if f"{fyyyy}{fmm}{fdd}" != idate:
                notes.append(f"date : {p.name} -> inside says {idate} -> filing as {yyyy}/{name}")

            target = dest_base / folder / "RAW_DATA" / "RACINGFORM" / yyyy / name
            if target.exists():
                try:
                    is_zip = target.open("rb").read(2) == b"PK"
                except OSError:
                    is_zip = False
                if not is_zip:
                    skipped += 1
                    continue
                notes.append(f"zip  : {target.name} was a zip blob -> replacing with real DRF")
                replaced += 1
            if not a.dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                if a.move:
                    p.unlink()
            added[f"{folder} {yyyy}"] += 1

    verb = "would archive" if a.dry_run else ("moved" if a.move else "copied")
    print(f"Scanned {seen} file(s).")
    print(f"{verb}: {sum(added.values())}  |  already in DB: {skipped}  |  "
          f"zip blobs replaced: {replaced}  |  refiled by internal track: {renamed}  |  "
          f"unparseable name: {ambiguous}  |  not-a-DRF: {bad}")
    if added:
        print("By track/year:")
        for k in sorted(added):
            print(f"  {k}: {added[k]}")
    if notes:
        print(f"\nCorrections ({len(notes)}):")
        for n in notes[:25]:
            print(f"  {n}")
        if len(notes) > 25:
            print(f"  ... and {len(notes)-25} more")


if __name__ == "__main__":
    main()
