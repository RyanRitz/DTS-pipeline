"""
Layer 4 (SCORE) — DMR.Turf.NonMaiden config-F model.

config-F blend = mean(cell, distance-parent, class-parent) of the three logistic
predictions, then within-race normalize. No core. Mirrors the validated blend
(AUC 0.749). Coeffs = the 8 coef_dmr_turf_2026*.csv (one row of betas each;
only the SELECTED vars are non-missing).

Cell key per horse: sprint(1/0) x claim(1/0):
  (1,1)->SC  (1,0)->SN  (0,1)->RC  (0,0)->RN
Distance parent: sprint->'sprint', route->'route'
Class parent:    claim ->'claim',  nonclaim->'stake'
"""
import numpy as np, pandas as pd
from pathlib import Path

_META = {"_link_","_type_","_status_","_name_","_lnlike_","_esttype_"}
_MODELS = {  # model-key -> coef filename stem
    "route":"coef_dmr_turf_2026route","sprint":"coef_dmr_turf_2026sprint",
    "claim":"coef_dmr_turf_2026claim","stake":"coef_dmr_turf_2026stake",
    "SC":"coef_dmr_turf_2026_sc","SN":"coef_dmr_turf_2026_sn",
    "RC":"coef_dmr_turf_2026_rc","RN":"coef_dmr_turf_2026_rn",
}

def _load_betas(coef_dir):
    betas = {}
    for key, stem in _MODELS.items():
        row = pd.read_csv(Path(coef_dir)/f"{stem}.csv").iloc[0]
        b = {"Intercept": float(row["Intercept"])}
        for c in row.index:
            if c.strip().lower() in _META or c == "Intercept":
                continue
            if pd.notna(row[c]):
                b[c] = float(row[c])
        betas[key] = b
    return betas

def _logit_prob(df, b):
    z = pd.Series(b["Intercept"], index=df.index, dtype=float)
    for v, beta in b.items():
        if v == "Intercept":
            continue
        col = df[v] if v in df.columns else pd.Series(0.0, index=df.index)
        z = z + beta * col.fillna(0.0)      # missing var -> 0 contribution
    return 1.0 / (1.0 + np.exp(-z))

def _route(df):
    """Per-row config-F model routing (single source of truth, shared with
    attribution so the twin blends exactly the models score blended).
    Returns (cellkey, distkey, clskey) numpy arrays aligned to df rows.
      cell : sprint x claim -> SC/SN/RC/RN
      dist : sprint -> 'sprint', route -> 'route'
      cls  : claim  -> 'claim',  nonclaim -> 'stake'
    """
    def _ci(name, default):
        # case-insensitive column fetch: the live pipeline uses lowercase
        # 'sprint'; the SAS/coef world uses 'Sprint'.
        if name in df.columns:
            return df[name]
        lc = {c.lower(): c for c in df.columns}
        a = lc.get(name.lower())
        return df[a] if a is not None else pd.Series(default, index=df.index)
    is_sprint = _ci("Sprint", 0).fillna(0).astype(int) == 1
    is_claim  = _ci("RaceType", "").fillna("").astype(str).str.upper().isin(["C","CO","R"])
    cellkey = np.where(is_sprint, np.where(is_claim,"SC","SN"), np.where(is_claim,"RC","RN"))
    distkey = np.where(is_sprint, "sprint", "route")
    clskey  = np.where(is_claim,  "claim",  "stake")
    return cellkey, distkey, clskey


def score_dmr_turf(df, coef_dir, race_group=("Track","Date","Race"), normalize=True):
    """config-F win prob per row = mean(cell, distance-parent, class-parent).

    normalize=True  -> within-race normalized (validation / standalone use).
    normalize=False -> RAW blend (production: the live pipeline's
                       _normalize_probabilities does the within-race normalize
                       once for all models, so turf returns raw like dirt config-F).
    NOTE: this computes a prob for EVERY row it's given, so callers must pass
    only the turf-non-maiden subset (the score.py branch does)."""
    betas = _load_betas(coef_dir)
    cellkey, distkey, clskey = _route(df)
    # score each of the 8 models across all rows once, then pick per-row
    P = {k: _logit_prob(df, b) for k, b in betas.items()}
    p_cell = pd.Series([P[k].iloc[i] for i, k in enumerate(cellkey)], index=df.index)
    p_dist = pd.Series([P[k].iloc[i] for i, k in enumerate(distkey)], index=df.index)
    p_cls  = pd.Series([P[k].iloc[i] for i, k in enumerate(clskey)],  index=df.index)
    cf = (p_cell + p_dist + p_cls) / 3.0
    if not normalize:
        return cf
    s = cf.groupby([df[c] for c in race_group]).transform("sum")
    return cf / s


def score_dmr_turf_frame(df, coef_dir):
    """Pipeline-facing entry: standardize ratios -> build DMR-turf vars ->
    config-F score. Returns [Track,Date,Race,HorseName,predicted] for the
    DMR-turf-nonmaiden rows. Global vars are built on a copy so nothing else moves."""
    from standardize import standardize_ratios
    from dmr_turf_vars import build_dmr_turf_vars
    d = standardize_ratios(df)
    # NOTE: x/I centering of the derived bases is done by the shared race_normalize
    # (features._SUPPLEMENT_VARS) BEFORE this is called in the live pipeline; on a
    # pre-centered df it is a no-op. build_dmr_turf_vars then reads x/I columns.
    d = build_dmr_turf_vars(d)
    d["predicted"] = score_dmr_turf(d, coef_dir)
    keep = [c for c in ["Track","Date","Race","HorseName","predicted"] if c in d.columns]
    return d[keep]
