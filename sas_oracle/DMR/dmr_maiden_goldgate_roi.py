#!/usr/bin/env python3
"""
DMR MAIDEN config-F blend -- ROI evaluation.

Reconstructs the locked config-F blend (cell + 3 parents: surface, racetype,
distance) from the 15 per-model scored CSVs in DMR\\SAS_DATA, then reports ROI
under two gates:

  (A) VALIDATION gate (honest, vs FINAL odds):
      top 50% of field by model prob  AND  EDGE = res_odds/pred_odds_nv > 1.25
      (25% value vs final tote odds). Expect negative-to-breakeven; that's normal.

  (B) GOLD gate (production, vs MORNING LINE):
      cumulative win-prob MASS gate  (prob_above < 0.50, and a tighter < 0.25)
      AND  50% value vs the morning line (EDGE_ml = ml_odds_nv/pred_odds_nv > 1.50).
      This is the softer benchmark the live sheets use.

Blend math (matches the SAS placeholder `predicted = mean(of p_cell p_surf p_rt p_dist)`):
  per horse  blend_raw = mean(pred_cell, pred_surf, pred_rt, pred_dist)
  per race   norm      = blend_raw / sum(blend_raw)          (ProbToWin)

Win indicator: RES_Winpayout present (the $2 payout is only filled for the winner).
Flat $2 bet; ROI = (sum payout - 2*N) / (2*N).

Usage:
    python dmr_maiden_goldgate_roi.py
    python dmr_maiden_goldgate_roi.py --mass 0.50 0.25 --ml-edge 1.50 --final-edge 1.25
"""
import argparse
import os
import sys
import numpy as np
import pandas as pd

# ---- locations -------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "SAS_DATA"))

# Key that uniquely identifies a horse-in-a-race across the scored files.
KEY = ["Track", "Date", "Race", "HorseName"]

# The four config-F components per horse.
#  - cell tag  = surface(T/D) + racetype(S/M) + distance(sp/rt)
#  - parents   = surface family, racetype family, distance family
CELL_TAGS = ["TSsp", "TSrt", "TMsp", "TMrt", "DSsp", "DSrt", "DMsp", "DMrt"]
SURF_TAGS = ["surfT", "surfD"]
RT_TAGS = ["rtS", "rtM"]
DIST_TAGS = ["distSp", "distRt"]
ALL_TAGS = CELL_TAGS + SURF_TAGS + RT_TAGS + DIST_TAGS


def _scored_path(tag):
    return os.path.join(DATA_DIR, f"maid_{tag}_scored.csv")


def load_blend():
    """Merge every model's prediction onto the core rows and build the blend."""
    base = pd.read_csv(_scored_path("core"))
    # normalise surface / racetype casing for routing
    base["_surf"] = base["RES_surface"].astype(str).str.upper().str[0]
    base["_rt"] = base["RaceType"].astype(str).str.upper().str[0]
    base["_sp"] = pd.to_numeric(base["sprint"], errors="coerce").fillna(0).astype(int)

    # pull each pred_<tag> column in on the horse key
    for tag in ALL_TAGS:
        col = f"pred_{tag}"
        s = pd.read_csv(_scored_path(tag), usecols=KEY + [col])
        base = base.merge(s, on=KEY, how="left")

    # route to the right cell + 3 parents
    surf_letter = np.where(base["_surf"] == "T", "T", "D")
    rt_letter = np.where(base["_rt"] == "S", "S", "M")
    dist_sfx = np.where(base["_sp"] == 1, "sp", "rt")
    cell_tag = pd.Series(surf_letter, index=base.index) + rt_letter + dist_sfx

    surf_par = np.where(base["_surf"] == "T", "surfT", "surfD")
    rt_par = np.where(base["_rt"] == "S", "rtS", "rtM")
    dist_par = np.where(base["_sp"] == 1, "distSp", "distRt")

    def pick(tag_series):
        # gather the pred value from the column named by each row's tag
        vals = np.full(len(base), np.nan)
        for tag in np.unique(tag_series):
            m = tag_series == tag
            vals[m] = base.loc[m, f"pred_{tag}"].to_numpy()
        return vals

    p_cell = pick(cell_tag.to_numpy())
    p_surf = pick(surf_par)
    p_rt = pick(rt_par)
    p_dist = pick(dist_par)

    comp = np.vstack([p_cell, p_surf, p_rt, p_dist])
    n_missing = np.isnan(comp).any(axis=0)
    if n_missing.any():
        print(f"[warn] {int(n_missing.sum())} rows missing >=1 config-F component "
              f"-> dropped from blend", file=sys.stderr)

    base["blend_raw"] = np.nanmean(comp, axis=0)
    base.loc[n_missing, "blend_raw"] = np.nan
    base = base.dropna(subset=["blend_raw"]).copy()

    # within-race normalisation -> ProbToWin
    base["race_id"] = base["Track"] + "|" + base["Date"].astype(str) + "|" + base["Race"].astype(str)
    grp = base.groupby("race_id")["blend_raw"]
    base["ProbToWin"] = base["blend_raw"] / grp.transform("sum")
    return base


def add_gate_cols(df, mass_levels, ml_edge, final_edge):
    """Add rank, model fair odds, edges, mass gate, and value flags."""
    df = df.copy()
    df["win"] = pd.to_numeric(df["RES_Winpayout"], errors="coerce").fillna(0) > 0
    df["payout"] = pd.to_numeric(df["RES_Winpayout"], errors="coerce").fillna(0.0)
    df["res_odds"] = pd.to_numeric(df["RES_Odds"], errors="coerce")
    df["ml_prob"] = pd.to_numeric(df["mlprob_adj_novig"], errors="coerce")
    df["numofentries"] = pd.to_numeric(df["numofentries"], errors="coerce")

    # rank within race by ProbToWin (1 = strongest)
    df["rank"] = df.groupby("race_id")["ProbToWin"].rank(ascending=False, method="first")
    df["rank_frac"] = df["rank"] / df["numofentries"]

    # cumulative mass ABOVE each horse (prob held by everyone ranked higher)
    df = df.sort_values(["race_id", "ProbToWin"], ascending=[True, False])
    csum = df.groupby("race_id")["ProbToWin"].cumsum()
    df["prob_above"] = csum - df["ProbToWin"]

    # model fair odds and value edges
    df["pred_odds_nv"] = (1.0 / df["ProbToWin"]) - 1.0
    df["edge_final"] = df["res_odds"] / df["pred_odds_nv"]
    ml_odds_nv = (1.0 / df["ml_prob"]) - 1.0
    df["edge_ml"] = ml_odds_nv / df["pred_odds_nv"]

    df["top_half"] = df["rank_frac"] <= 0.50
    df["val_final"] = df["edge_final"] > final_edge
    df["val_ml"] = df["edge_ml"] > ml_edge
    for lv in mass_levels:
        df[f"mass_{lv}"] = df["prob_above"] < lv
    return df


def roi(df, mask):
    sub = df[mask]
    n = len(sub)
    if n == 0:
        return dict(n=0, wins=0, roi=np.nan, hit=np.nan)
    payout = sub["payout"].sum()
    return dict(n=n, wins=int(sub["win"].sum()),
                roi=(payout - 2 * n) / (2 * n), hit=sub["win"].mean())


def report(df, mass_levels, ml_edge, final_edge):
    def line(name, r):
        if r["n"] == 0:
            print(f"  {name:<34} n=0")
            return
        print(f"  {name:<34} n={r['n']:>5}  wins={r['wins']:>4}  "
              f"hit={r['hit']*100:5.1f}%  ROI={r['roi']*100:+6.1f}%")

    print("=" * 72)
    print("DMR MAIDEN config-F blend  --  ROI evaluation")
    print(f"rows scored: {len(df)}   races: {df['race_id'].nunique()}")
    print("=" * 72)

    print("\n(A) VALIDATION gate  [top-half of field  &  final-odds EDGE > "
          f"{final_edge:g}]  (honest, expect <=0)")
    line("all horses (flat)", roi(df, df.index == df.index))
    line("top-half only", roi(df, df["top_half"]))
    line("top-half + value(final)", roi(df, df["top_half"] & df["val_final"]))
    line("rank1 + value(final)", roi(df, (df["rank"] == 1) & df["val_final"]))

    print(f"\n(B) GOLD gate  [mass gate  &  morning-line EDGE > {ml_edge:g}]  "
          "(production benchmark, softer)")
    for lv in mass_levels:
        m = df[f"mass_{lv}"]
        line(f"mass<{lv:g} only", roi(df, m))
        line(f"mass<{lv:g} + value(ML)", roi(df, m & df["val_ml"]))

    # segment cut on the primary gold gate (tightest mass + ML value)
    tight = min(mass_levels)
    gate = df[f"mass_{tight}"] & df["val_ml"]
    print(f"\nGOLD segments  [mass<{tight:g} + ML EDGE>{ml_edge:g}]")
    for label, mask in [
        ("turf", df["_surf"] == "T"),
        ("dirt", df["_surf"] == "D"),
        ("special-wt (S)", df["_rt"] == "S"),
        ("maiden-claim (M)", df["_rt"] == "M"),
        ("sprint", df["_sp"] == 1),
        ("route", df["_sp"] == 0),
        ("2019+", df["year"] >= 2019),
        ("2023+", df["year"] >= 2023),
    ]:
        line(label, roi(df, gate & mask))
    print("=" * 72)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mass", type=float, nargs="+", default=[0.50, 0.25],
                    help="cumulative-mass gold thresholds (default 0.50 0.25)")
    ap.add_argument("--ml-edge", type=float, default=1.50,
                    help="value multiple vs morning line (1.50 = 50%% value)")
    ap.add_argument("--final-edge", type=float, default=1.25,
                    help="value multiple vs final odds for the validation gate")
    ap.add_argument("--dump", default=None,
                    help="optional path to write the blended+gated rows as CSV")
    args = ap.parse_args()

    df = load_blend()
    df = add_gate_cols(df, args.mass, args.ml_edge, args.final_edge)
    report(df, args.mass, args.ml_edge, args.final_edge)
    if args.dump:
        df.to_csv(args.dump, index=False)
        print(f"\nwrote {args.dump}")


if __name__ == "__main__":
    main()
