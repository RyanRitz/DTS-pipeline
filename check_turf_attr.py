"""
Quick diagnostic: does THIS machine load the SAR turf attribution models?
Run from the FullAutomation folder:  python check_turf_attr.py
Expect: fix present = True, all 17 turf files FOUND, TURF SETS LOADED: 17
"""
import sys, inspect, logging
sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(message)s")
from pathlib import Path
import pandas as pd, pyreadstat
import config, model_setup
from model_registry import get_scoring_models
import attribution

# 1) Is the fix present in the attribution.py Python actually imports?
src = inspect.getsource(attribution._load_coefficient_sets)
has_fix = 'for sub_key in getattr(config, "TURF_MODELS", {}):' in src
print("attribution.py imported from :", attribution.__file__)
print("turf-loader fix present      :", has_fix)

# 2) Register SAR family and try to load the turf coefficients the pipeline way
model_setup.setup_registry(config)
sc = get_scoring_models("SAR", config)
cdir = Path(sc.COEFF_DIR)
print("COEFF_DIR resolved to        :", cdir)

def numeric_cols(fn):
    cdf, _ = pyreadstat.read_sas7bdat(str(cdir / fn))
    row = cdf.iloc[0]
    out = []
    for c in cdf.columns:
        try:
            float(row[c]); out.append(c)
        except (TypeError, ValueError):
            pass
    return out

avail = set()
for fn in sorted(set(sc.TURF_MODELS.values())):
    p = cdir / fn
    print(("  FOUND   " if p.exists() else "  MISSING ") + fn)
    if p.exists():
        avail.update(numeric_cols(fn))

sets = attribution._load_coefficient_sets(sc, cdir, avail)
print("\nTURF SETS LOADED:", len(sets[2]), "  (want 17)")
print("keys:", sorted(sets[2].keys()))
