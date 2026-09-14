"""
Layer 4 (SCORE) — DMR.Maiden config-F model.

config-F blend = mean(cell, surface-parent, racetype-parent, distance-parent)
of the four logistic predictions, then within-race normalize. No core.
Mirrors the locked maiden blend (AUC 0.762; beat core-only -13.7% and
parents-only -12.1% on ROI vs final odds). Twin of score_dmr_turf.py, with
one extra parent (surface) because maidens span both surfaces.

Coeffs = the 14 coef_maid_*.csv (one row of betas each; only the SELECTED vars
are non-missing). coef_maid_core.csv exists but is NOT blended.

Cell key per horse: surface(T/D) x racetype(S/M) x distance(sp/rt):
  e.g. (T,S,sprint)->TSsp   (D,M,route)->DMrt
Surface  parent: turf->'surfT',  dirt->'surfD'
Racetype parent: S   ->'rtS',    M   ->'rtM'      (S = maiden special wt, M = maiden claiming)
Distance parent: sprint->'distSp', route->'distRt'
"""
import numpy as np, pandas as pd
from pathlib import Path

_META = {"_link_", "_type_", "_status_", "_name_", "_lnlike_", "_esttype_"}

# model-key -> coef filename stem.  8 cells + 6 parents (core is intentionally excluded).
_MODELS = {
    "TSsp": "coef_maid_TSsp", "TSrt": "coef_maid_TSrt",
    "TMsp": "coef_maid_TMsp", "TMrt": "coef_maid_TMrt",
    "DSsp": "coef_maid_DSsp", "DSrt": "coef_maid_DSrt",
    "DMsp": "coef_maid_DMsp", "DMrt": "coef_maid_DMrt",
    "surfT": "coef_maid_surfT", "surfD": "coef_maid_surfD",
    "rtS": "coef_maid_rtS", "rtM": "coef_maid_rtM",
    "distSp": "coef_maid_distSp", "distRt": "coef_maid_distRt",
}


def _load_betas(coef_dir):
    betas = {}
    for key, stem in _MODELS.items():
        row = pd.read_csv(Path(coef_dir) / f"{stem}.csv").iloc[0]
        b = {"Intercept": float(row["Intercept"])}
        for c in row.index:
            if c.strip().lower() in _META or c == "Intercept":
                continue
            if pd.notna(row[c]):
                b[c] = float(row[c])
        betas[key] = b
    return betas


def selected_vars(coef_dir):
    """Union of all SELECTED (non-missing) predictor names across the 14 blended
    coef files. Every one of these MUST exist as a column at score time, or
    _logit_prob silently drops it (0 contribution) and the prob is wrong.
    Use this against the var-builder output to prove coverage."""
    betas = _load_betas(coef_dir)
    out = set()
    for b in betas.values():
        out |= {v for v in b if v != "Intercept"}
    return sorted(out)


def _logit_prob(df, b):
    z = pd.Series(b["Intercept"], index=df.index, dtype=float)
    for v, beta in b.items():
        if v == "Intercept":
            continue
        col = df[v] if v in df.columns else pd.Series(0.0, index=df.index)
        z = z + beta * col.fillna(0.0)      # missing var -> 0 contribution
    return 1.0 / (1.0 + np.exp(-z))


def _route(df):
    """Per-row config-F model routing (single source of truth; share with the
    attribution twin so comments blend exactly what score blends).
    Returns (cellkey, surfkey, rtkey, distkey) numpy arrays aligned to df rows.
      cell : surface x racetype x dist -> TSsp/.../DMrt
      surf : turf -> 'surfT',  dirt -> 'surfD'
      rt   : S -> 'rtS',       M -> 'rtM'
      dist : sprint -> 'distSp', route -> 'distRt'
    """
    def _ci(name, default):
        # case-insensitive fetch: live pipeline uses lowercase 'sprint'/'surface';
        # the SAS/coef world uses 'Sprint'/'Surface'.
        if name in df.columns:
            return df[name]
        lc = {c.lower(): c for c in df.columns}
        a = lc.get(name.lower())
        return df[a] if a is not None else pd.Series(default, index=df.index)

    surf_raw = _ci("Surface", "D").fillna("D").astype(str).str.upper().str[0]
    is_turf = surf_raw.eq("T")
    is_sprint = _ci("Sprint", 0).fillna(0).astype(int) == 1
    # maiden racetype: 'S' = maiden special weight, anything else (M/C…) = maiden claiming
    rt_raw = _ci("RaceType", "M").fillna("M").astype(str).str.upper().str[0]
    is_special = rt_raw.eq("S")

    s_let = np.where(is_turf, "T", "D")
    r_let = np.where(is_special, "S", "M")
    d_sfx = np.where(is_sprint, "sp", "rt")
    cellkey = pd.Series(s_let, index=df.index) + r_let + d_sfx

    surfkey = np.where(is_turf, "surfT", "surfD")
    rtkey = np.where(is_special, "rtS", "rtM")
    distkey = np.where(is_sprint, "distSp", "distRt")
    return cellkey.to_numpy(), surfkey, rtkey, distkey


def score_dmr_maiden(df, coef_dir, race_group=("Track", "Date", "Race"), normalize=True):
    """config-F win prob per row = mean(cell, surf-parent, rt-parent, dist-parent).

    normalize=True  -> within-race normalized (validation / standalone use).
    normalize=False -> RAW blend (production: the live pipeline's
                       _normalize_probabilities does the within-race normalize
                       once for all segments, so maiden returns raw like the
                       dirt/turf config-F scorers).
    Callers must pass only the maiden subset (score._score_maiden does)."""
    betas = _load_betas(coef_dir)
    cellkey, surfkey, rtkey, distkey = _route(df)
    P = {k: _logit_prob(df, b) for k, b in betas.items()}

    def pick(keys):
        return pd.Series([P[k].iloc[i] for i, k in enumerate(keys)], index=df.index)

    p_cell = pick(cellkey)
    p_surf = pick(surfkey)
    p_rt = pick(rtkey)
    p_dist = pick(distkey)
    cf = (p_cell + p_surf + p_rt + p_dist) / 4.0
    if not normalize:
        return cf
    s = cf.groupby([df[c] for c in race_group]).transform("sum")
    return cf / s


def score_dmr_maiden_frame(df, coef_dir):
    """Pipeline-facing entry: standardize ratios -> build DMR-maiden vars ->
    config-F score. Returns [Track,Date,Race,HorseName,predicted] for the
    DMR maiden rows. Global vars are built on a copy so nothing else moves.

    NOTE: build_dmr_maiden_vars must produce every name returned by
    selected_vars(coef_dir); run that coverage check during validation."""
    from standardize import standardize_ratios
    from dmr_maiden_vars import build_dmr_maiden_vars
    d = standardize_ratios(df)
    d = build_dmr_maiden_vars(d)
    d["predicted"] = score_dmr_maiden(d, coef_dir)
    keep = [c for c in ["Track", "Date", "Race", "HorseName", "predicted"] if c in d.columns]
    return d[keep]
