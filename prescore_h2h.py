"""Pre-race side-by-side: champion (production SAS coefficients) vs SAS-vars tree challenger on a DRF.
No results needed. Writes results/prescore_{TRK}_{date}.csv and prints per-race top of the board.
Usage: python prescore_h2h.py --drf ../KEE/RAW_DATA/RACINGFORM/2026/KEE1014.DRF --date 20261014 [--track KEE]"""
import sys, json, argparse, warnings; warnings.simplefilter("ignore")
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
CH = HERE.parent / "Challenger" / "data"

ap = argparse.ArgumentParser(); ap.add_argument("--drf", required=True); ap.add_argument("--date", required=True); ap.add_argument("--track", default="KEE")
a = ap.parse_args()
import config, model_setup, score, model_registry as mr
from ingest_drf import load_drf; from features import engineer_features
model_setup.setup_registry(config)
cdir = Path(config.COEFF_DIR)
if not cdir.exists(): cdir = HERE / "coefficients"
mm, yyyy = a.date[4:6], a.date[:4]
drf = load_drf(a.drf, a.track, mm + a.date[6:8], yyyy, validate=False)
feats = engineer_features(drf.copy())

# champion
sc = mr.get_scoring_models(a.track, config, a.date)
cp = score.run_scoring(feats.copy(), cdir, sc)
cp["pn"] = cp["ProgramNumberifavailable"].astype(str).str.strip(); cp["race"] = cp["Race"].astype(int)
cp = cp[["race", "pn", "HorseName", "Surface", "RaceType", "MornLineOddsifavailable", "MornOddsAdj", "ProbToWin", "DTSOdds"]].rename(columns={"ProbToWin": "p_cp", "DTSOdds": "odds_cp"})

# challenger (SAS vars -> LightGBM, 3 segments)
import lightgbm as lgb
models = {s: lgb.Booster(model_file=str(CH / f"sastree_{s}.txt")) for s in ["dirt", "turf", "maiden"]}
feat = models["dirt"].feature_name()   # feature list is embedded in the model file
assert all(m.feature_name() == feat for m in models.values())
x = feats.copy(); x["pn"] = x["ProgramNumberifavailable"].astype(str).str.strip(); x["race"] = pd.to_numeric(x["Race"], errors="coerce").astype(int)
for v in feat:
    x[v] = pd.to_numeric(x[v], errors="coerce") if v in x.columns else np.nan
S = x["Surface"].astype(str); RT = x["RaceType"].astype(str)
x["seg"] = np.where(RT.isin(["M", "S"]), "maiden", np.where(S.isin(["T", "t"]), "turf", "dirt"))
x["raw"] = np.nan
for s, m in models.items():
    k = x["seg"] == s
    if k.any(): x.loc[k, "raw"] = m.predict(x.loc[k, feat].values.astype(np.float32))
x["p_ch"] = x["raw"] / x.groupby("race")["raw"].transform("sum")
ch = x[["race", "pn", "p_ch", "seg"]]

df = cp.merge(ch, on=["race", "pn"], how="left")
df["odds_ch"] = 1 / df["p_ch"] - 1
for p in ["p_cp", "p_ch"]:
    d = df.sort_values(["race", p], ascending=[True, False])
    df["rank_" + p] = d.groupby("race").cumcount().add(1).reindex(df.index)
    df["pa_" + p] = (d.groupby("race")[p].cumsum() - d[p]).reindex(df.index)
    df["edge_" + p] = (pd.to_numeric(df["MornOddsAdj"], errors="coerce") + 1) * df[p]
    df["gold_" + p] = (df["edge_" + p] >= 1.5) & (df["pa_" + p] < 0.5)
    df["top50_" + p] = df["pa_" + p] < 0.5
out = HERE / "results" / f"prescore_{a.track}_{a.date}.csv"
df.sort_values(["race", "p_cp"], ascending=[True, False]).to_csv(out, index=False)
print(f"{a.track} {a.date}: {df['race'].nunique()} races, {len(df)} entries (pre-scratch) -> {out.name}")
print(f"golds: champion {int(df['gold_p_cp'].sum())}, tree {int(df['gold_p_ch'].sum())}, both {int((df['gold_p_cp']&df['gold_p_ch']).sum())}")
for r, g in df.groupby("race"):
    g = g.sort_values("p_cp", ascending=False)
    print(f"\nR{r} {g['Surface'].iloc[0]}/{g['RaceType'].iloc[0]} ({len(g)} entries)  [* = gold, + = top-50% mass]")
    print(f"  {'pn':>3s} {'horse':20s} {'ML':>5s} | {'champ':>6s} {'rk':>2s}    | {'tree':>6s} {'rk':>2s}")
    for _, h in g.iterrows():
        fc = "*" if h["gold_p_cp"] else ("+" if h["top50_p_cp"] else " ")
        ft = "*" if h["gold_p_ch"] else ("+" if h["top50_p_ch"] else " ")
        print(f"  {h['pn']:>3s} {str(h['HorseName'])[:20]:20s} {str(h['MornLineOddsifavailable']):>5s} | {h['p_cp']*100:5.1f}% {int(h['rank_p_cp']):2d} {fc}  | {h['p_ch']*100:5.1f}% {int(h['rank_p_ch']):2d} {ft}")
