"""
audit_missing_features.py
=========================
Identifies features that coefficient files expect but that are MISSING
from the engineered DataFrame at scoring time.

Catches the silent-drop bug at score.py line 279 where features missing
from df are silently omitted from the dot product — producing the
asymmetric long-shot divergence we see in Races 5 and 10.

Writes full report to: audit_report.txt
Also writes machine-readable: missing_features.csv

USAGE:
    python audit_missing_features.py --drf "C:\\path\\to\\CDX0509.DRF"
"""

import argparse
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import pyreadstat


# =============================================================================
# IMPORTS — adjust to match your project layout
# =============================================================================
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
        print(f"ERROR: Couldn't import pipeline modules. Edit the IMPORTS block.")
        print(f"  Specifically: {e}")
        sys.exit(1)


# Columns that are SAS metadata, not features. Anything matching is skipped.
METADATA_COLS_UPPER = {
    "_NAME_", "_TYPE_", "_LABEL_", "_LINK_", "_STATUS_",
    "_DEPVAR_", "_LNLIKE_", "_MODEL_", "_RHS_", "_DV_",
}


def select_parms_row(coef_df: pd.DataFrame) -> tuple[pd.Series, str]:
    """Pick the row with parameter estimates (handles multi-row OUTEST)."""
    if "_TYPE_" in coef_df.columns:
        parms_rows = coef_df[coef_df["_TYPE_"].astype(str).str.upper() == "PARMS"]
        if len(parms_rows) >= 1:
            return parms_rows.iloc[0], "type=PARMS"

    if "Intercept" in coef_df.columns:
        for i in range(len(coef_df)):
            v = coef_df.iloc[i]["Intercept"]
            if pd.notna(v):
                try:
                    float(v)
                    return coef_df.iloc[i], f"row {i} (has numeric Intercept)"
                except (ValueError, TypeError):
                    pass

    return coef_df.iloc[0], "row 0 (default)"


def safe_float(v):
    """Convert to float, returning None if not numeric."""
    if pd.isna(v):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--track", default="CD")
    p.add_argument("--date", default="20260509", help="YYYYMMDD")
    p.add_argument("--drf", default=None, help="explicit path to .DRF file")
    p.add_argument("--out", default="audit_report.txt", help="output report file")
    args = p.parse_args()

    track = args.track
    yyyymmdd = args.date
    yyyy, mmdd = yyyymmdd[:4], yyyymmdd[4:]

    # Resolve DRF path
    if args.drf:
        drf_path = Path(args.drf)
    else:
        for candidate in [
            Path(f"raw_data/{track}{mmdd}.DRF"),
            Path(f"data/{track}{mmdd}.DRF"),
            Path(f"C:/Users/ryanr/Documents/BTSM/{track}X/RAW_DATA/RACINGFORM/{yyyy}/{track}X{mmdd}.DRF"),
        ]:
            if candidate.exists():
                drf_path = candidate
                break
        else:
            print("Couldn't find DRF file automatically. Pass --drf explicitly.")
            sys.exit(1)

    print(f"Loading DRF: {drf_path}")
    df = load_drf(drf_path, track=track, date=mmdd, year=yyyy)

    print(f"Engineering features...")
    df = engineer_features(df)

    print(f"DataFrame at scoring time: {len(df)} rows x {len(df.columns)} cols")
    df_cols = set(df.columns)

    grand_missing = defaultdict(list)
    grand_summary = []
    file_metadata = []

    coeff_files = sorted(COEFF_DIR.glob("*.sas7bdat"))
    print(f"Found {len(coeff_files)} coefficient files in {COEFF_DIR}")
    if not coeff_files:
        print(f"No .sas7bdat files. Edit COEFF_DIR.")
        sys.exit(1)

    skipped_files = []

    for cf in coeff_files:
        try:
            coef_df, _ = pyreadstat.read_sas7bdat(str(cf))
        except Exception as e:
            skipped_files.append((cf.name, str(e)))
            continue

        n_rows = len(coef_df)
        type_vals = ""
        if "_TYPE_" in coef_df.columns:
            type_vals = ",".join(str(t) for t in coef_df["_TYPE_"].head(5).tolist())

        coef_row, row_reason = select_parms_row(coef_df)

        expected, missing, present, intercept = [], [], [], None
        skipped_metadata, skipped_nonnumeric = [], []

        for col in coef_df.columns:
            if col.upper() in METADATA_COLS_UPPER:
                skipped_metadata.append(col)
                continue
            v = coef_row[col]
            f = safe_float(v)
            if f is None:
                if pd.notna(v):
                    skipped_nonnumeric.append((col, v))
                continue
            if col == "Intercept":
                intercept = f
                continue
            expected.append((col, f))
            if col in df_cols:
                present.append((col, f))
            else:
                missing.append((col, f))

        file_metadata.append({
            "model": cf.name,
            "n_rows": n_rows,
            "type_vals": type_vals,
            "row_used": row_reason,
            "metadata_cols": len(skipped_metadata),
            "nonnumeric_cols": len(skipped_nonnumeric),
            "nonnumeric_examples": skipped_nonnumeric[:3],
        })

        grand_summary.append({
            "model": cf.name,
            "expected": len(expected),
            "present": len(present),
            "missing": len(missing),
            "intercept": intercept,
        })
        for name, coef in missing:
            grand_missing[name].append((cf.name, coef))

    # ---------------------------------------------------------------------
    # Build the report file
    # ---------------------------------------------------------------------
    lines = []

    def w(s=""):
        lines.append(s)

    w("=" * 100)
    w("BTSM PYTHON-vs-SAS FEATURE AUDIT")
    w("=" * 100)
    w(f"DRF:          {drf_path}")
    w(f"Track / date: {track} / {yyyymmdd}")
    w(f"DataFrame:    {len(df)} rows x {len(df.columns)} cols at scoring time")
    w(f"Coeff dir:    {COEFF_DIR}")
    w(f"Coeff files:  {len(coeff_files)} found, {len(grand_summary)} read OK, {len(skipped_files)} skipped")
    w()

    if skipped_files:
        w("-" * 100)
        w("SKIPPED FILES (couldn't read)")
        w("-" * 100)
        for name, err in skipped_files:
            w(f"  {name}: {err}")
        w()

    # File structure
    w("=" * 100)
    w("COEFFICIENT FILE STRUCTURE")
    w("=" * 100)
    w(f"{'File':<55} {'Rows':>5} {'Row used':<25} {'_TYPE_ values'}")
    w("-" * 100)
    for fm in file_metadata:
        notes = ""
        if fm["nonnumeric_cols"] > 0:
            ex = fm["nonnumeric_examples"]
            ex_str = ", ".join(f"{c}={v!r}" for c, v in ex[:2])
            notes = f"  [non-numeric cols: {fm['nonnumeric_cols']} ({ex_str})]"
        w(f"{fm['model']:<55} {fm['n_rows']:>5} {fm['row_used']:<25} {fm['type_vals']}{notes}")
    w()

    # Per-model summary
    w("=" * 100)
    w("PER-MODEL SUMMARY")
    w("=" * 100)
    w(f"{'Model file':<55} {'Expected':>10} {'Present':>10} {'Missing':>10} {'Intercept':>12}")
    w("-" * 100)
    for r in grand_summary:
        flag = "  <- GAP" if r["missing"] else ""
        intc = f"{r['intercept']:.4f}" if r['intercept'] is not None else "—"
        w(f"{r['model']:<55} {r['expected']:>10} {r['present']:>10} {r['missing']:>10} {intc:>12}{flag}")
    total_exp  = sum(r["expected"] for r in grand_summary)
    total_pres = sum(r["present"]  for r in grand_summary)
    total_miss = sum(r["missing"]  for r in grand_summary)
    w("-" * 100)
    w(f"{'TOTAL':<55} {total_exp:>10} {total_pres:>10} {total_miss:>10}")
    w()

    # Sorted missing features
    w("=" * 100)
    w("MISSING FEATURES — ranked by # of models using it, then max |coefficient|")
    w("=" * 100)
    w(f"{'Feature':<50} {'#Models':>8} {'Max|coef|':>12}  {'First seen in'}")
    w("-" * 100)

    sortable = [
        (feat, len(hits), max(abs(c) for _, c in hits), hits[0][0])
        for feat, hits in grand_missing.items()
    ]
    sortable.sort(key=lambda x: (-x[1], -x[2]))

    for feat, n_models, max_abs, first in sortable:
        w(f"{feat:<50} {n_models:>8} {max_abs:>12.6f}  {first}")

    w()
    w(f"Unique missing features: {len(grand_missing)}")
    w(f"Total missing (model x feature occurrences): {total_miss}")
    w()

    # Per-horse audit
    w("=" * 100)
    w("HORSE-SPECIFIC IMPACT")
    w("=" * 100)

    name_col = "HorseName" if "HorseName" in df.columns else "horsename"
    if name_col not in df.columns:
        w("  HorseName column not found. Skipping per-horse audit.")
    else:
        suspects = ["OUTFIELDER", "WALTER THE MASON", "IN AMERICA", "BLUEGRASS PIKE",
                    "REB FIVE", "SANDAL'S SONG", "REBEL WITH A CAUSE", "KETCHUM",
                    "SUPERCHARGER", "COMPREHENSIVE"]
        name_upper = df[name_col].astype(str).str.upper().str.strip()

        for hname in suspects:
            match = df[name_upper == hname]
            if match.empty:
                w(f"\n  {hname}: not found in df")
                continue
            idx = match.index[0]
            prog = match.iloc[0].get('ProgramNumber','?')
            w(f"\n  {hname} (idx {idx}, ProgramNumber={prog}):")
            for cf in coeff_files:
                try:
                    coef_df, _ = pyreadstat.read_sas7bdat(str(cf))
                except Exception:
                    continue
                coef_row, _ = select_parms_row(coef_df)
                n_expected = 0
                n_missing_in_df = 0
                n_present_but_nan = 0
                for col in coef_df.columns:
                    if col.upper() in METADATA_COLS_UPPER or col == "Intercept":
                        continue
                    f = safe_float(coef_row[col])
                    if f is None:
                        continue
                    n_expected += 1
                    if col not in df.columns:
                        n_missing_in_df += 1
                    elif pd.isna(df.at[idx, col]):
                        n_present_but_nan += 1
                if n_missing_in_df > 0 or n_present_but_nan > 0:
                    w(f"    {cf.name:<55} expected={n_expected:>3}  missing-from-df={n_missing_in_df:>3}  present-but-NaN={n_present_but_nan:>3}")

    # Write the report
    out_path = Path(args.out)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    # Save full CSV
    rows = []
    for feat, hits in grand_missing.items():
        for model, coef in hits:
            rows.append({"feature": feat, "model": model, "coefficient": coef})
    out_csv = Path("missing_features.csv")
    pd.DataFrame(rows).to_csv(out_csv, index=False)

    # Console summary only
    print()
    print(f"Report written to: {out_path.resolve()}")
    print(f"CSV written to:    {out_csv.resolve()}")
    print()
    print(f"  Coefficient files: {len(grand_summary)} read, {len(skipped_files)} skipped")
    print(f"  Total feature slots expected: {total_exp}")
    print(f"  Total present in df:          {total_pres}")
    print(f"  Total missing (silently dropped by score.py): {total_miss}")
    print(f"  Unique missing feature names: {len(grand_missing)}")
    if total_miss > 0:
        print()
        print(f"  Top 5 missing features by # models that use them:")
        for feat, n_models, max_abs, first in sortable[:5]:
            print(f"    {feat:<40} (in {n_models} models, max |coef| = {max_abs:.4f})")


if __name__ == "__main__":
    main()
