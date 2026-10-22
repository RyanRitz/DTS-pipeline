"""Score the round-2 SAS-vars tree on a card post-hoc, dropping horses that did not run (per results CSV), so field-relative
features re-center exactly as the champion's evaluate_card does. Writes Challenger/predictions_sastree/KEE_<d>_sastree.csv.
Usage: python tree_posthoc.py 20261014 [20261015 ...]"""
import sys; from pathlib import Path
import numpy as np, pandas as pd, lightgbm as lgb
HERE = Path(__file__).resolve().parent; CH = HERE.parent / "Challenger" / "data"
import config, model_setup
from ingest_drf import load_drf; from features import engineer_features
model_setup.setup_registry(config)
models = {s: lgb.Booster(model_file=str(CH / f"sastree_{s}.txt")) for s in ["dirt", "turf", "maiden"]}
feat = models["dirt"].feature_name()
for d in sys.argv[1:]:
    yyyy, mm = d[:4], d[4:6]
    res = pd.read_csv(HERE / "results" / f"KEE_{d}_results.csv"); res["pn"] = res["program"].astype(str).str.strip()
    drf = load_drf(str(HERE.parent / "KEE" / "RAW_DATA" / "RACINGFORM" / yyyy / f"KEE{mm}{d[6:]}.DRF"), "KEE", d[4:], yyyy, validate=False)
    drf["pn"] = drf["ProgramNumberifavailable"].astype(str).str.strip(); drf["race"] = pd.to_numeric(drf["Race"], errors="coerce").astype(int)
    ran = set(zip(res["race"].astype(int), res["pn"]))
    drf = drf[[(r, p) in ran for r, p in zip(drf["race"], drf["pn"])]].reset_index(drop=True)
    x = engineer_features(drf.copy()); x["pn"] = x["ProgramNumberifavailable"].astype(str).str.strip(); x["race"] = pd.to_numeric(x["Race"], errors="coerce").astype(int)
    for v in feat: x[v] = pd.to_numeric(x[v], errors="coerce") if v in x.columns else np.nan
    S_ = x["Surface"].astype(str); RT = x["RaceType"].astype(str)
    x["seg"] = np.where(RT.isin(["M", "S"]), "maiden", np.where(S_.isin(["T", "t"]), "turf", "dirt")); x["raw"] = np.nan
    for s, m in models.items():
        k = x["seg"] == s
        if k.any(): x.loc[k, "raw"] = m.predict(x.loc[k, feat].values.astype(np.float32))
    x["ProbToWin"] = x["raw"] / x.groupby("race")["raw"].transform("sum")
    out = x[["race", "pn", "ProbToWin"]].rename(columns={"pn": "program"}); out["DTSOdds"] = 1 / out["ProbToWin"] - 1
    op = HERE.parent / "Challenger" / "predictions_sastree" / f"KEE_{d}_sastree.csv"; out.to_csv(op, index=False)
    print(d, len(out), "runners ->", op.name)
