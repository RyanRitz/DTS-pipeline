"""
scan_stakes.py — find where the stakes NAME lives in the DRF.

The sheet header shows "G3" for graded stakes but no race name ("Whitney",
"Diana", etc.). pdf.py already has a `race_name` slot that prepends the name
when present, but it reads a `RaceName` column that DOESN'T EXIST in
drf_schema.py — so it's always blank. Before wiring it to the right column we
have to find which real DRF field carries the name.

This scans every DRF in DRF_DIR and, for each race that looks like a stakes,
dumps the candidate header fields so we can see where the name actually is.

Run on the DESKTOP (that's where DRF_Downloads lives):
    .\.venv\Scripts\python.exe scan_stakes.py
    .\.venv\Scripts\python.exe scan_stakes.py --all      # every race, not just stakes
"""
from __future__ import annotations
import sys, re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from ingest_drf import load_drf

# Matches the hardcoded location every brisnet_download / check_drf uses.
DRF_DIR = _HERE / "DRF_Downloads"

SHOW_ALL = "--all" in sys.argv

# Race-header fields most likely to hold a stakes name / grade. RaceConditions
# is the free-text prose; the name is probably in there or in the classification.
CAND = ["RaceType", "TodaysRaceClassification", "RaceConditions",
        "RaceConditions1", "RaceConditions2"]

# A race is "interesting" (probably a stakes) if any of these show up.
STAKES_HINT = re.compile(
    r"\b(G1|G2|G3|Grade\s*[123I]{1,3}|Stakes|Handicap|\bStk\b|Listed|Invitational|"
    r"Derby|Oaks|Cup|Classic|Futurity|Distaff|Sprint|Mile|Trophy)\b",
    re.IGNORECASE,
)

def _first(row, col):
    v = row.get(col)
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none") else s

def main() -> int:
    drfs = sorted(DRF_DIR.glob("*.DRF")) + sorted(DRF_DIR.glob("*.drf"))
    if not drfs:
        print(f"No DRFs in {DRF_DIR}")
        return 1
    print(f"Scanning {len(drfs)} DRF(s) in {DRF_DIR}\n")

    hits = 0
    for path in drfs:
        # filename convention: YYYYMMDD_TRACK_DRS.DRF
        m = re.match(r"(\d{4})(\d{4})_([A-Za-z]+)", path.stem)
        if not m:
            continue
        year, mmdd, track = m.group(1), m.group(2), m.group(3)
        try:
            df = load_drf(path, track, mmdd, year)
        except Exception as e:
            print(f"  ! {path.name}: load failed ({e})")
            continue

        # one row per race (header fields are identical across a race's horses)
        races = df.drop_duplicates(subset=["Race"]).sort_values("Race")
        for _, row in races.iterrows():
            blob = " ".join(_first(row, c) for c in CAND)
            interesting = SHOW_ALL or bool(STAKES_HINT.search(blob))
            if not interesting:
                continue
            hits += 1
            rc = int(row["Race"]) if str(row.get("Race")).replace(".0", "").isdigit() else row.get("Race")
            print(f"== {track} {year}-{mmdd}  Race {rc}")
            for c in CAND:
                v = _first(row, c)
                if v:
                    print(f"     {c:26s} : {v[:160]}")
            print()

    if hits == 0:
        print("No stakes-looking races found. Re-run with --all to dump every race.")
    else:
        print(f"{hits} race(s) shown. Look for the NAME (e.g. 'Whitney') and note "
              f"which field it sits in — that's the column to wire into race_name.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
