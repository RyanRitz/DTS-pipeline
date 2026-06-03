# verify_drf.py
import os, glob
import pandas as pd

drf_dir = r"C:\Users\ryanr\Documents\BTSM\FullAutomation\DRF_Downloads"

# Pick the most recent zip-style (small file with date prefix)
candidates = []
for p in glob.glob(os.path.join(drf_dir, "20260*_DRS.DRF")):
    candidates.append((os.path.getsize(p), p))

if not candidates:
    print("No 20260*_DRS.DRF files found.")
    raise SystemExit(1)

# Sort by size; smallest first (most likely zip)
candidates.sort()
path = candidates[0][1]
size_kb = candidates[0][0] // 1024
print(f"Inspecting: {os.path.basename(path)} ({size_kb} KB)")

# Try pandas read_csv with auto-detect compression
print("\nAttempt 1: pd.read_csv with default compression='infer'")
try:
    df = pd.read_csv(path, header=None, dtype=str, keep_default_na=False, nrows=10, on_bad_lines='warn')
    print(f"  SUCCESS: shape={df.shape}")
    print(f"  col 2 (race): {df[2].tolist()[:5]}")
    print(f"  col 1417:     {df[1417].tolist()[:5]}")
    print(f"  col 1418:     {df[1418].tolist()[:5]}")
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")

# Try forcing compression='zip'
print("\nAttempt 2: pd.read_csv with compression='zip'")
try:
    df = pd.read_csv(path, header=None, dtype=str, keep_default_na=False, nrows=10, compression='zip', on_bad_lines='warn')
    print(f"  SUCCESS: shape={df.shape}")
    print(f"  col 2 (race): {df[2].tolist()[:5]}")
    print(f"  col 1417:     {df[1417].tolist()[:5]}")
    print(f"  col 1418:     {df[1418].tolist()[:5]}")
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")

# Also try zipfile.ZipFile directly
print("\nAttempt 3: Python's zipfile module")
import zipfile
try:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        print(f"  Contents: {names}")
        with zf.open(names[0]) as f:
            head = f.read(200)
            print(f"  First 200 chars of inner file: {head[:200]!r}")
except Exception as e:
    print(f"  FAILED: {type(e).__name__}: {e}")