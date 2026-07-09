"""
Pinpoint why attribution produces no reasons for a given card.

Run from FullAutomation/ with the venv python:
    .venv\\Scripts\\python.exe debug_attr.py SAR 20260710

Prints:
  1. Which coefficient sets load, and how many coefficients SURVIVE the
     `available` (merged.columns) filter. A set with 0 survivors is the bug:
     it still counts as "loaded" but contributes nothing.
  2. The `model` column dtype / unique values vs the int keys attribution uses.
  3. Per-race: model_id, sub-model count, and how many horses got a reason.
  4. Coefficient names that are NOT engineered columns, and those that ARE
     but have no entry in the feature->label map (so can never become a ✓/✗).
"""
import sys
from pathlib import Path

sys.path.insert(0, ".")
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

import pandas as pd
import config, model_setup
from model_registry import get_scoring_models
import attribution
from run_pipeline import run_scoring


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    track, race_date = sys.argv[1].upper(), sys.argv[2]

    model_setup.setup_registry(config)
    sc = get_scoring_models(track, config)
    cdir = Path(sc.COEFF_DIR)

    print(f"\n=== family for {track}: {getattr(sc,'family_name','?')}  coeff_dir={cdir}")

    res = run_scoring(track=track, race_date=race_date, scratches=None, track_status=None)
    if res is None or res is getattr(__import__("run_pipeline"), "SKIP_CARD", object()):
        print("scoring failed / skipped")
        return 1

    scored, feat = res.scored_df, res.feature_df

    key_cols = ["Track", "Date", "Race", "HorseName"]
    extra = [c for c in feat.columns if c not in scored.columns or c in key_cols]
    merged = scored.merge(feat[extra], on=key_cols, how="left", suffixes=("", "_feat"))
    available = set(merged.columns)

    print(f"\n=== merged columns: {len(available)}")

    # 2. model column
    print("\n=== `model` column")
    print("   dtype :", merged["model"].dtype)
    print("   unique:", sorted(merged['model'].dropna().unique().tolist()))
    print("   attribution keys are ints 1(dirt) 2(turf) 3(maiden)")

    # 1 + 4. coefficient survival
    label_map_keys = set()
    import re
    src = open("attribution.py", encoding="utf-8").read()
    label_map_keys = set(re.findall(r'^\s*"([A-Za-z0-9_]+)":\s*"[a-z]+",', src, re.M))

    import pyreadstat
    for mid, name, models in ((1, "DIRT", sc.DIRT_MODELS),
                              (2, "TURF", sc.TURF_MODELS),
                              (3, "MAIDEN", sc.MAIDEN_MODELS)):
        print(f"\n=== {name} sub-models ({len(models)})")
        for sub_key, fname in models.items():
            # maiden keys are ints (1..16) — force str before the width spec
            sk = str(sub_key)
            p = cdir / fname
            if not p.exists():
                print(f"   {sk:10s} MISSING FILE {fname}")
                continue
            try:
                cdf, _ = pyreadstat.read_sas7bdat(str(p))
            except Exception as e:
                print(f"   {sk:10s} UNREADABLE  {fname}: {e}")
                continue
            cols = [c for c in cdf.columns if c not in getattr(attribution, "EXCLUDE", set())]
            survive = [c for c in cols if c in available]
            labeled = [c for c in survive if c in label_map_keys]
            flag = "  <-- 0 SURVIVORS" if not survive else ("  <-- 0 LABELED" if not labeled else "")
            print(f"   {sk:10s} coefs={len(cols):3d}  in_data={len(survive):3d}  labeled={len(labeled):3d}{flag}")
            # Does score.py actually emit the column that marks this cell as fired?
            pc = f"predicted_t_{sk}" if name == "TURF" and f"predicted_t_{sk}" in available else f"predicted{sk}"
            if pc not in available:
                print(f"              FIRING COL MISSING: {pc}  <-- this sub-model can never fire")
            if cols and not survive:
                print(f"              e.g. not in data: {cols[:6]}")
            elif survive and not labeled:
                print(f"              e.g. unlabeled  : {survive[:6]}")

    # 3. per-race outcome
    print("\n=== per-race attribution")
    out = attribution.add_attributions(scored, coeff_dir=cdir, config=sc, feature_df=feat)
    for race, g in out.groupby("Race"):
        mid = merged.loc[merged["Race"] == race, "model"].iloc[0]
        n_like = int((g["why_like_1"].astype(str).str.strip() != "").sum())
        n_fade = int((g["why_fade_1"].astype(str).str.strip() != "").sum())
        print(f"   R{int(race):<2} model={mid!r:>6}  horses={len(g):2d}  with_like={n_like:2d}  with_fade={n_fade:2d}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
