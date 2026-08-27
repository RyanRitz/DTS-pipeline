#!/usr/bin/env python3
"""
Clean up the 4 misfiled zip-blob DRFs in the 2026 modeling DB (found 2026-08-05).

Background: 2026 RACINGFORM .DRF files are raw BRIS zip blobs; the ZIP member name is
the authoritative (track,date). archive_drfs.py files by content but has no --src to
reach files already sitting (mislabeled) inside the DB, so this does the targeted fix.

  EQK/EQK0508.DRF  -> member CDX0509  (dup of existing, charted)   => DELETE
  CCP/CCP0508.DRF  -> member EQK0508  (the real EQK 5/8)           => MOVE  -> EQK/EQK0508.DRF
  FP /FP0509.DRF   -> member EVD0509  (dup of existing, charted)   => DELETE
  FP /FP0512.DRF   -> member FPK0512  (real; FPK has chart, no DRF) => MOVE -> FPK/FPK0512.DRF

Dry-run by default. Pass --apply to execute. Every action re-verifies the zip member
live, and every DELETE is guarded by confirming the true card already exists AND is
charted elsewhere, so no unique data can be lost.
"""
import os, sys, zipfile, shutil, re
from pathlib import Path

BTSM = Path(__file__).resolve().parent.parent          # FullAutomation -> BTSM root
APPLY = "--apply" in sys.argv

def rf(track, stem):     return BTSM/track/"RAW_DATA"/"RACINGFORM"/"2026"/f"{stem}.DRF"
def member(p):
    try:    return zipfile.ZipFile(p).namelist()[0].rsplit(".",1)[0]   # 'CDX0509'
    except Exception: return None
def charted(track, mmdd):
    d = BTSM/track/"RAW_DATA"/"RESULTS"/"2026"
    return d.is_dir() and any(x.startswith(f"{track}{mmdd}2026.") for x in os.listdir(d))

OPS = [
 dict(src=("EQK","EQK0508"), expect="CDX0509", act="delete"),
 dict(src=("CCP","CCP0508"), expect="EQK0508", act="move", dst=("EQK","EQK0508")),
 dict(src=("FP","FP0509"),   expect="EVD0509", act="delete"),
 dict(src=("FP","FP0512"),   expect="FPK0512", act="move", dst=("FPK","FPK0512")),
]

print(f"BTSM = {BTSM}")
print(f"MODE = {'APPLY' if APPLY else 'DRY-RUN (pass --apply to execute)'}\n")
done=0; skipped=0
for op in OPS:
    strk, sstem = op["src"]; sp = rf(strk, sstem)
    m = member(sp)
    tag = f"{strk}/{sstem}.DRF -> member {m}"
    if not sp.exists():
        print(f"[skip] {tag}: source missing (already cleaned?)"); skipped+=1; continue
    if m != op["expect"]:
        print(f"[SKIP] {tag}: member != expected {op['expect']} -- NOT touching"); skipped+=1; continue
    tt = re.match(r'^([A-Za-z]+)(\d{4})$', m)             # true track + MMDD
    ttrk, tmmdd = tt.group(1), tt.group(2)
    if op["act"]=="delete":
        # guard: true card must already exist AND be charted elsewhere
        if not rf(ttrk, m).exists() or not charted(ttrk, tmmdd):
            print(f"[SKIP] {tag}: safety guard (true {ttrk} DRF/chart not confirmed) -- NOT deleting"); skipped+=1; continue
        print(f"[del ] {tag}: redundant dup of {ttrk}/{m}.DRF (charted) -> DELETE")
        if APPLY: sp.unlink()
        done+=1
    else:
        dtrk, dstem = op["dst"]; dp = rf(dtrk, dstem)
        dp.parent.mkdir(parents=True, exist_ok=True)
        if dp.exists():
            print(f"[SKIP] {tag}: target {dtrk}/{dstem}.DRF already exists -- NOT overwriting"); skipped+=1; continue
        print(f"[move] {tag}: real {ttrk} card -> {dtrk}/{dstem}.DRF")
        if APPLY: shutil.move(str(sp), str(dp))
        done+=1

# tidy: remove FP folder if now empty of DRFs
fpdir = BTSM/"FP"/"RAW_DATA"/"RACINGFORM"/"2026"
if fpdir.is_dir() and not any(fpdir.iterdir()):
    print(f"\n[note] FP RACINGFORM/2026 is now empty (both files were misfiles).")
    if APPLY:
        try: fpdir.rmdir()
        except OSError: pass

print(f"\n{'APPLIED' if APPLY else 'WOULD DO'}: {done} action(s), {skipped} skipped.")
if not APPLY:
    print("Re-run with --apply to execute. Verify afterward in PowerShell (the bash mount can cache stale deletes).")
