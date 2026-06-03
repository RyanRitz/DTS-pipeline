# check_drf.py
import os, glob

drf_dir = r"C:\Users\ryanr\Documents\BTSM\FullAutomation\DRF_Downloads"

# Find any DRF on disk (we just need ONE for the inspection)
drfs = sorted(glob.glob(os.path.join(drf_dir, "*.DRF")))
if not drfs:
    print("No DRF files found.")
    raise SystemExit(1)

path = drfs[0]
print(f"Inspecting: {os.path.basename(path)}\n")

with open(path, encoding="utf-8", errors="replace") as f:
    for i, line in enumerate(f):
        if i >= 8:
            break
        cols = line.split(",")
        race  = cols[2].strip().strip('"')      if len(cols) > 2    else "?"
        c1373 = cols[1373].strip().strip('"')   if len(cols) > 1373 else "MISSING"
        c1417 = cols[1417].strip().strip('"')   if len(cols) > 1417 else "MISSING"
        c1418 = cols[1418].strip().strip('"')   if len(cols) > 1418 else "MISSING"
        # Also show the last 3 cols in case the doc indexing is off
        last3 = [c.strip().strip('"') for c in cols[-3:]]
        print(f"Row {i}: Race={race}, col1373={c1373!r}, col1417={c1417!r}, col1418={c1418!r}, last3={last3}")

print(f"\nTotal columns in last row: {len(cols)}")