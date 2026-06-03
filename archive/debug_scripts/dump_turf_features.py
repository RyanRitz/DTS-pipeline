"""
dump_turf_features.py
=====================
For every horse in turf races (Races 5, 10, 11 for CD 5/9), dumps the
EXACT VALUES of every feature used by the active turf coefficient models.

OUTPUTS:
  turf_feature_dump.csv  — full feature dump (one row per horse)
  turf_feature_dump.log  — human-readable report (per-model summaries +
                           per-horse breakdown showing which features
                           contributed most to each horse's log_odds)

USAGE:
    python dump_turf_features.py --drf "C:\\Users\\ryanr\\Documents\\BTSM\\CDX\\RAW_DATA\\RACINGFORM\\2026\\CDX0509.DRF"
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyreadstat


try:
    from pipeline.ingest_drf import load_drf
    from pipeline.features import engineer_features
    from pipeline import config as pipeline_config
    COEFF_DIR = Path(pipeline_config.COEFF_DIR)
except ImportError:
    try:
        from ingest_drf import load_drf
        from features import engineer_features
        import config as pipeline_config
        COEFF_DIR = Path(pipeline_config.COEFF_DIR)
    except ImportError as e:
        print(f"ERROR: Edit IMPORTS block. {e}")
        sys.exit(1)


METADATA_COLS_UPPER = {
    "_NAME_", "_TYPE_", "_LABEL_", "_LINK_", "_STATUS_",
    "_DEPVAR_", "_LNLIKE_", "_MODEL_", "_RHS_", "_DV_", "_ESTTYPE_",
}

ACTIVE_TURF_MODELS = [
    "keeturf042026s.sas7bdat",
    "keeturf042026r.sas7bdat",
    "keeturf042026hp.sas7bdat",
    "keeturf042026lp.sas7bdat",
]


def safe_float(v):
    if pd.isna(v):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def parse_coef_file(path):
    cdf, _ = pyreadstat.read_sas7bdat(str(path))
    row = cdf.iloc[0]
    if "_TYPE_" in cdf.columns:
        parms = cdf[cdf["_TYPE_"].astype(str).str.upper() == "PARMS"]
        if len(parms):
            row = parms.iloc[0]
    intercept, feats = None, {}
    for col in cdf.columns:
        if col.upper() in METADATA_COLS_UPPER:
            continue
        v = safe_float(row[col])
        if v is None:
            continue
        if col == "Intercept":
            intercept = v
        else:
            feats[col] = v
    return intercept, feats


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--track", default="CD")
    p.add_argument("--date", default="20260509")
    p.add_argument("--drf", default=None)
    p.add_argument("--csv", default="turf_feature_dump.csv")
    p.add_argument("--log", default="turf_feature_dump.log")
    args = p.parse_args()

    track = args.track
    yyyymmdd = args.date
    yyyy, mmdd = yyyymmdd[:4], yyyymmdd[4:]

    if args.drf:
        drf_path = Path(args.drf)
    else:
        drf_path = Path(f"raw_data/{track}{mmdd}.DRF")
        if not drf_path.exists():
            print("Pass --drf explicitly.")
            sys.exit(1)

    log_lines = []

    def log(s=""):
        log_lines.append(s)

    log("=" * 100)
    log("BTSM TURF-MODEL FEATURE DUMP")
    log("=" * 100)
    log(f"DRF:          {drf_path}")
    log(f"Track / date: {track} / {yyyymmdd}")
    log(f"Coeff dir:    {COEFF_DIR}")
    log()

    print(f"Loading DRF: {drf_path}")
    df = load_drf(drf_path, track=track, date=mmdd, year=yyyy)

    print("Engineering features...")
    df = engineer_features(df)
    log(f"DataFrame at scoring time: {len(df)} rows x {len(df.columns)} cols")

    # Load coefficient files
    coeff_files = {}
    log("\n" + "=" * 100)
    log("ACTIVE TURF MODELS — features and coefficients")
    log("=" * 100)
    for fname in ACTIVE_TURF_MODELS:
        path = COEFF_DIR / fname
        if not path.exists():
            log(f"WARNING: {path} not found, skipping")
            continue
        coeff_files[fname] = parse_coef_file(path)
        intercept, feats = coeff_files[fname]
        log(f"\n{fname}: intercept={intercept:+.6f}, {len(feats)} features")
        for fn, c in sorted(feats.items(), key=lambda x: -abs(x[1])):
            present = "OK " if fn in df.columns else "MISSING_FROM_DF"
            log(f"    [{present}]  {fn:<40} coef={c:>+12.6f}")

    # Union of all features
    all_feats = set()
    for _, feats in coeff_files.values():
        all_feats.update(feats.keys())
    all_feats = sorted(all_feats)
    log(f"\nUnion of features across {len(coeff_files)} turf models: {len(all_feats)}")

    # Identify turf races
    if "Surface" not in df.columns:
        log("Surface column missing")
        Path(args.log).write_text("\n".join(log_lines), encoding="utf-8")
        sys.exit(1)
    race_surface = df.groupby("Race")["Surface"].first().to_dict()
    turf_races = [r for r, s in race_surface.items() if str(s).upper() == "T"]
    log(f"Turf races on this card: {turf_races}")

    name_col = "HorseName" if "HorseName" in df.columns else "horsename"
    prog_col = "ProgramNumber" if "ProgramNumber" in df.columns else "horsenum"

    # Build dump rows
    rows = []
    for idx, hrow in df.iterrows():
        race = hrow.get("Race")
        if race not in turf_races:
            continue
        out = {
            "Race": race,
            "Program": hrow.get(prog_col),
            "Horse": hrow.get(name_col),
            "Surface": hrow.get("Surface"),
            "RaceType": hrow.get("RaceType"),
            "Distance_yards": hrow.get("Distanceinyards"),
        }
        for f in all_feats:
            if f in df.columns:
                v = hrow[f]
                out[f] = pd.to_numeric(v, errors="coerce") if pd.notna(v) else np.nan
            else:
                out[f] = "<MISSING_FROM_DF>"

        for model_name, (intercept, feats) in coeff_files.items():
            short = model_name.replace("keeturf042026", "").replace(".sas7bdat", "")
            log_odds = intercept
            for fname, coef in feats.items():
                if fname in df.columns:
                    fv = hrow[fname]
                    if pd.notna(fv):
                        try:
                            log_odds += float(fv) * coef
                        except (ValueError, TypeError):
                            pass
            out[f"log_odds_{short}"] = log_odds
            try:
                out[f"predicted_{short}"] = 1.0 / (1.0 + np.exp(-min(max(log_odds, -500), 500)))
            except OverflowError:
                out[f"predicted_{short}"] = 0.0 if log_odds < 0 else 1.0
        rows.append(out)

    out_df = pd.DataFrame(rows).sort_values(["Race", "Program"]).reset_index(drop=True)

    pred_cols = [c for c in out_df.columns if c.startswith("predicted_")]
    out_df["predicted_ensemble"] = out_df[pred_cols].mean(axis=1, skipna=True)

    Path(args.csv).write_text(out_df.to_csv(index=False, float_format="%.6f"), encoding="utf-8")

    # ---------------------------------------------------------------
    # Per-horse breakdown in the log
    # ---------------------------------------------------------------
    log("\n" + "=" * 100)
    log("PER-HORSE LOG_ODDS DECOMPOSITION (top contributors per model per horse)")
    log("=" * 100)

    for race in sorted(turf_races):
        sub = out_df[out_df["Race"] == race]
        log(f"\n--- Race {race} ({len(sub)} horses) ---")
        for _, hrow_out in sub.iterrows():
            log(f"\n  #{int(hrow_out['Program']) if pd.notna(hrow_out['Program']) else '?':>2}  "
                f"{hrow_out['Horse']:<25}  "
                f"ensemble={hrow_out['predicted_ensemble']:>.6f}")
            # For each model, show contributions
            for model_name, (intercept, feats) in coeff_files.items():
                short = model_name.replace("keeturf042026", "").replace(".sas7bdat", "")
                # Recompute contributions
                contribs = []
                contribs.append(("(Intercept)", intercept, intercept))
                for fname, coef in feats.items():
                    if fname not in df.columns:
                        contribs.append((fname, np.nan, 0.0))
                        continue
                    fv = pd.to_numeric(
                        df.loc[df[name_col].str.upper().str.strip() == hrow_out['Horse'].upper().strip(), fname].iloc[0]
                        if not df.loc[df[name_col].str.upper().str.strip() == hrow_out['Horse'].upper().strip()].empty
                        else np.nan,
                        errors="coerce"
                    )
                    if pd.isna(fv):
                        contribs.append((fname, np.nan, 0.0))
                    else:
                        contribs.append((fname, float(fv), float(fv) * coef))
                # Sort by abs(contribution) desc
                contribs_sorted = sorted(contribs, key=lambda x: -abs(x[2]))
                lo = hrow_out[f"log_odds_{short}"]
                pp = hrow_out[f"predicted_{short}"]
                log(f"      model={short:<3}  log_odds={lo:>+9.4f}  predicted={pp:>.6f}")
                for name, val, contrib in contribs_sorted[:5]:
                    if pd.isna(val):
                        log(f"        {name:<35} value=NaN              contrib= 0.0000")
                    else:
                        log(f"        {name:<35} value={val:>+12.6f}  contrib={contrib:>+9.4f}")

    # Quick console summary
    print(f"\nWritten:")
    print(f"  CSV: {Path(args.csv).resolve()}")
    print(f"  Log: {Path(args.log).resolve()}")
    print(f"  {len(out_df)} horses across {len(turf_races)} turf races")

    # Also append the predicted summary table to the log
    log("\n" + "=" * 100)
    log("ENSEMBLE PREDICTED SUMMARY — all turf horses")
    log("=" * 100)
    pred_short = [c.replace("predicted_", "") for c in pred_cols]
    header = f"{'Race':>4} {'#':>3}  {'Horse':<25}  " + \
             " ".join(f"{'pred_'+s:>11}" for s in pred_short) + \
             f"  {'ensemble':>10}"
    log(header)
    log("-" * len(header))
    for _, r in out_df.iterrows():
        prog = int(r['Program']) if pd.notna(r['Program']) else 0
        preds = " ".join(f"{r[c]:>11.6f}" for c in pred_cols)
        log(f"{int(r['Race']):>4} {prog:>3}  {r['Horse']:<25}  {preds}  {r['predicted_ensemble']:>10.6f}")

    Path(args.log).write_text("\n".join(log_lines), encoding="utf-8")
    print(f"\nDone. Upload turf_feature_dump.log")


if __name__ == "__main__":
    main()
