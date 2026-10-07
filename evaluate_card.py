#!/usr/bin/env python3
"""
evaluate_card.py — DTS model performance evaluation engine.
================================================================
Scores a card with the production models, grades it against actual results,
and APPENDS every scored horse (with gate flags + outcome) to a persistent
performance log so results accumulate over time. One card is noise; the log
is where the real signal (rolling ROI / strike / CLV by gate) lives.

This is the engine the Model Performance chat drives. It does NOT touch the
models — model development stays in the dev repo/chat.

USAGE
-----
    # 1) chart -> results CSV (+ exotics CSV)       [ccf_results.py]
    python ccf_results.py DRF_Downloads/kee10022026c.zip
    #    or, once archived:  python ccf_results.py ../KEE/RAW_DATA/RESULTS/2026 KEE 20261002
    # 2) score + grade + log
    python evaluate_card.py KEE 20261002 \
        --drf ../KEE/RAW_DATA/RACINGFORM/2026/KEE1002.DRF \
        --results results/KEE_20261002_results.csv \
        --log results/performance_log.csv

RESULTS CSV (one row per horse that RAN; scratched horses simply omitted):
    race, program, finish, final_odds, win_payout
      race        int   race number
      program     str   program number ('1','1A','11'...)
      finish      int   1=win, 2, 3, ...; 0 / blank = unplaced
      final_odds  float decimal odds-to-1 (8.0 = 8-1). Optional but needed
                        for closing-line value. For the winner it can be
                        derived from win_payout if omitted.
      win_payout  float $2 win payout (winner only). Optional; if omitted
                        and final_odds present, payout = 2*(final_odds+1).
Any (race, program) in the DRF but NOT in the results CSV is treated as a
scratch (removed before scoring so the field re-centers correctly).

Sources for the results CSV: hand-entered from the tote/chart, the Equibase
chart (keeneland.equibase.com/...), or a future BRISnet CCF chart parser
(task #22) — the schema is the contract between them and this engine.

GATES (scratch-adjusted no-vig morning line; honest payouts)
    gold          value>=1.50 vs ML & top-50% by model prob & field>=7
    rank1_value   model rank 1 & value>=1.25
    rank1         model rank 1
    top50_value   top-50% by model prob & value>=1.25
    bet_all       every runner (null / takeout baseline)
"""
from __future__ import annotations
import argparse, sys, re
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


# gate -> boolean mask builder over the scored+graded frame
GATES = {
    "gold":        lambda x: (x["value_ml"] >= 1.50) & (x["pct"] <= 0.5) & (x["field"] >= 7),
    "rank1_value": lambda x: (x["rank"] == 1) & (x["value_ml"] >= 1.25),
    "rank1":       lambda x: x["rank"] == 1,
    "top50_value": lambda x: (x["pct"] <= 0.5) & (x["value_ml"] >= 1.25),
    "bet_all":     lambda x: pd.Series(True, index=x.index),
}


def score_and_grade(track: str, race_date: str, drf_path: Path,
                    results_csv: Path, coeff_dir: Path | None) -> pd.DataFrame:
    """Return a per-horse frame with model prob, value, gate flags, and outcome."""
    import config
    from ingest_drf import load_drf
    from features import engineer_features
    import score
    import model_setup
    import model_registry as mr

    model_setup.setup_registry(config)
    cdir = Path(coeff_dir) if coeff_dir else Path(config.COEFF_DIR)
    if not cdir.exists() and (HERE / "coefficients").exists():
        # config.COEFF_DIR is a Windows path; fall back to the repo copy (sandbox / Linux runs)
        cdir = HERE / "coefficients"
    if not cdir.exists():
        sys.exit(f"coefficient dir not found: {cdir} (use --coeff-dir)")

    mm, yyyy = race_date[4:6], race_date[:4]
    drf = load_drf(str(drf_path), track, mm + race_date[6:8], yyyy, validate=False)
    drf["pn"] = drf["ProgramNumberifavailable"].astype(str)

    res = pd.read_csv(results_csv)
    res.columns = [c.strip().lower() for c in res.columns]
    res["pn"] = res["program"].astype(str)
    res["race"] = res["race"].astype(int)
    ran = set(zip(res["race"], res["pn"]))

    # scratch = DRF entrant not in the results (didn't run)
    keep = [(int(r), p) in ran for r, p in zip(drf["Race"], drf["pn"])]
    kept = drf[pd.Series(keep, index=drf.index)].reset_index(drop=True)
    n_scr = len(drf) - len(kept)

    sc = mr.get_scoring_models(track, config, race_date)
    out = score.run_scoring(engineer_features(kept.copy()), cdir, sc)
    out["pn"] = out["ProgramNumberifavailable"].astype(str)
    out["race"] = out["Race"].astype(int)

    # no-vig morning line (scratch-adjusted) + value + rank percentile + field
    ml = pd.to_numeric(out["MornLineOddsifavailable"], errors="coerce")
    out["mlimp"] = 1.0 / (ml + 1.0)
    out["ml_novig"] = out["mlimp"] / out.groupby("race")["mlimp"].transform("sum")
    out["value_ml"] = out["ProbToWin"] / out["ml_novig"]
    out["pct"] = out.groupby("race")["ProbToWin"].rank(ascending=False, pct=True)
    out["field"] = out.groupby("race")["pn"].transform("size")

    # merge actual results
    r = res[["race", "pn", "finish"] + [c for c in ("final_odds", "win_payout") if c in res.columns]]
    g = out.merge(r, on=["race", "pn"], how="left")
    g["finish"] = pd.to_numeric(g.get("finish"), errors="coerce")
    g["won"] = (g["finish"] == 1)
    fo = pd.to_numeric(g.get("final_odds"), errors="coerce") if "final_odds" in g else pd.Series(np.nan, index=g.index)
    wp = pd.to_numeric(g.get("win_payout"), errors="coerce") if "win_payout" in g else pd.Series(np.nan, index=g.index)
    # winner final odds from payout when final_odds missing
    fo = np.where(fo.isna() & g["won"] & wp.notna(), wp / 2.0 - 1.0, fo)
    g["final_odds"] = fo
    # $2 win payout: given, else derived from final_odds
    g["win_payout"] = np.where(wp.notna(), wp, np.where(pd.notna(fo), 2.0 * (pd.Series(fo) + 1.0), np.nan))
    # closing-line value where final odds known
    g["clv"] = g["ProbToWin"] / (1.0 / (g["final_odds"] + 1.0))
    g["date"] = race_date
    g["track"] = track
    g.attrs["n_scratched"] = n_scr
    return g


def grade_table(g: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, fn in GATES.items():
        b = g[fn(g)]
        n = len(b)
        wins = int(b["won"].sum())
        ret = float(np.where(b["won"], b["win_payout"].fillna(0), 0).sum())
        roi = 100 * (ret - 2 * n) / (2 * n) if n else np.nan
        rows.append((name, n, wins, round(100 * wins / n, 1) if n else np.nan,
                     round(roi, 1), round(ret, 2)))
    return pd.DataFrame(rows, columns=["gate", "plays", "wins", "strike_%", "roi_%", "returned_$"])


LOG_COLS = ["date", "track", "race", "pn", "HorseName", "Surface", "RaceType", "field",
            "ProbToWin", "ml_novig", "value_ml", "pct", "rank", "final_odds", "clv",
            "finish", "won", "win_payout", "gold", "rank1_value", "rank1", "top50_value"]


def append_log(g: pd.DataFrame, log_path: Path) -> int:
    row = g.copy()
    for name, fn in GATES.items():
        if name != "bet_all":
            row[name] = fn(g)
    out = row[[c for c in LOG_COLS if c in row.columns]].copy()
    # de-dupe: drop any prior rows for this date/track before appending (idempotent re-runs)
    if log_path.exists():
        prev = pd.read_csv(log_path)
        prev = prev[~((prev["date"].astype(str) == str(g["date"].iloc[0])) &
                      (prev["track"].astype(str) == str(g["track"].iloc[0])))]
        out = pd.concat([prev, out], ignore_index=True)
    out.to_csv(log_path, index=False)
    return len(out)


def rolling_summary(log_path: Path) -> pd.DataFrame:
    if not log_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(log_path)
    df["won"] = df["won"].astype(bool)
    rows = []
    masks = {"gold": df.get("gold", False), "rank1_value": df.get("rank1_value", False),
             "rank1": df.get("rank1", False), "top50_value": df.get("top50_value", False),
             "bet_all": pd.Series(True, index=df.index)}
    for name, m in masks.items():
        b = df[m.astype(bool)] if not isinstance(m, bool) else df
        n = len(b)
        if not n:
            continue
        wins = int(b["won"].sum())
        ret = float(np.where(b["won"], pd.to_numeric(b["win_payout"], errors="coerce").fillna(0), 0).sum())
        rows.append((name, n, wins, round(100 * wins / n, 1), round(100 * (ret - 2 * n) / (2 * n), 1)))
    cards = df.groupby(["date", "track"]).ngroups
    out = pd.DataFrame(rows, columns=["gate", "plays", "wins", "strike_%", "roi_%"])
    out.attrs["cards"] = cards
    out.attrs["races"] = df.groupby(["date", "track", "race"]).ngroups
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="DTS card performance evaluation + rolling log.")
    ap.add_argument("track")
    ap.add_argument("race_date", help="YYYYMMDD")
    ap.add_argument("--drf", required=True)
    ap.add_argument("--results", required=True, help="results CSV (see header for schema)")
    ap.add_argument("--log", default=str(HERE / "performance_log.csv"))
    ap.add_argument("--coeff-dir", default=None, help="override config.COEFF_DIR (sandbox/testing)")
    a = ap.parse_args()

    g = score_and_grade(a.track, a.race_date, Path(a.drf), Path(a.results),
                        Path(a.coeff_dir) if a.coeff_dir else None)
    gt = grade_table(g)

    print(f"\n=== {a.track} {a.race_date} — card review "
          f"({g['race'].nunique()} races, {len(g)} runners, {g.attrs['n_scratched']} scratched) ===\n")
    print(gt.to_string(index=False))

    # winner containment in top-50%
    w = g[g["won"]]
    print(f"\nWinners in model top-50% by prob: {int((w['pct'] <= 0.5).sum())}/{len(w)}")

    # CLV on winners (where final odds known)
    wc = w.dropna(subset=["clv"])
    if len(wc):
        print(f"Mean closing-line value on winners: {wc['clv'].mean():.2f}x  "
              f"(>1 = model had the winner as an overlay at post)")

    nrows = append_log(g, Path(a.log))
    print(f"\nLogged to {a.log} ({nrows} total horse-rows).")

    rs = rolling_summary(Path(a.log))
    if len(rs):
        print(f"\n=== ROLLING performance (all logged: {rs.attrs['cards']} cards, "
              f"{rs.attrs['races']} races) ===\n")
        print(rs.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
