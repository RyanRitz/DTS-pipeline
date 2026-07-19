"""
repair_drf_names.py — rename DRFs to match their CONTENTS.

Companion to audit_drf_labels.py. The downloader (brisnet_download.py) claims
"the newest new file" after each request, so a slow download can land during the
NEXT request's wait window and be renamed to the wrong target — see the
2026-07-19 incident (a Colonial Downs 7/22 card saved as 20260719_DMR_DRS.DRF).

Since every DRF carries its own Track + Date, the safe repair is to rename each
file to what it actually contains.

DRY-RUN BY DEFAULT. Nothing is renamed unless you pass --apply.

    .\.venv\Scripts\python.exe repair_drf_names.py           # show the plan
    .\.venv\Scripts\python.exe repair_drf_names.py --apply   # do it
"""
from __future__ import annotations
import sys, re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from ingest_drf import load_drf

DRF_DIR = _HERE / "DRF_Downloads"
PRODUCT = "DRS"
APPLY   = "--apply" in sys.argv


def _first_str(series) -> str:
    s = series.dropna().astype(str).str.strip()
    s = s[s != ""]
    return s.mode().iloc[0] if not s.empty else ""


def main() -> int:
    drfs = sorted({p.resolve() for p in DRF_DIR.glob("*.[Dd][Rr][Ff]")})
    print(f"Scanning {len(drfs)} DRF(s) in {DRF_DIR}")
    print("MODE:", "APPLY (files WILL be renamed)" if APPLY else "DRY-RUN (no changes)")
    print()

    planned: list[tuple[Path, Path]] = []
    for path in drfs:
        m = re.match(r"(\d{4})(\d{4})_([A-Za-z]+)", path.stem)
        if not m:
            continue
        year, mmdd, claim = m.group(1), m.group(2), m.group(3).upper()
        try:
            df = load_drf(path, claim, mmdd, year)
        except Exception as e:
            print(f"  ! {path.name}: load failed ({e})")
            continue
        real_track = _first_str(df["Track"]).upper() if "Track" in df.columns else ""
        d = df["Date"].dropna() if "Date" in df.columns else []
        if not real_track or len(d) == 0:
            print(f"  ? {path.name}: cannot read Track/Date — leaving alone")
            continue
        rd = d.iloc[0].date()
        correct = f"{rd.strftime('%Y%m%d')}_{real_track}_{PRODUCT}.DRF"
        if correct.lower() == path.name.lower():
            continue
        planned.append((path, path.with_name(correct)))

    if not planned:
        print("Every DRF already matches its contents. Nothing to do.")
        return 0

    print(f"{len(planned)} file(s) need renaming:\n")
    for src, dst in planned:
        flag = "  (TARGET EXISTS — will use .bad suffix)" if dst.exists() and dst not in [s for s, _ in planned] else ""
        print(f"  {src.name}\n    -> {dst.name}{flag}")
    print()

    if not APPLY:
        print("DRY-RUN: nothing changed. Re-run with --apply to perform the renames.")
        return 0

    # Two-phase so swaps (A->B, B->A) don't collide.
    tmps: list[tuple[Path, Path]] = []
    for src, dst in planned:
        tmp = src.with_suffix(src.suffix + ".tmprename")
        src.rename(tmp)
        tmps.append((tmp, dst))
    for tmp, dst in tmps:
        if dst.exists():
            dst.rename(dst.with_suffix(dst.suffix + ".bad"))
            print(f"  ! {dst.name} already existed -> {dst.name}.bad")
        tmp.rename(dst)
        print(f"  renamed -> {dst.name}")
    print("\nDone. Re-run audit_drf_labels.py to confirm 0 mismatches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
