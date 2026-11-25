"""
Layer 4 (SCORE) -- GP.Turf.NonMaiden model (frozen 2026-11-24).

Each horse gets two logistic predictions, averaged 50/50:
    distance model : sprint | route
    crossed model  : SC | SN | RC | RN   (sprint/route x claim/nonclaim)
then normalized within race. Same shape as the DMR turf config-F port
(score_dmr_turf.py) with ONE difference: DMR averages cell + distance + class;
GP averages distance + crossed only. No core, no class parent.

Routing:
  sprint : the `sprint` flag (1 = sprint). All GP turf sprints are 5f.
  claim  : RaceType in (C, CO, R, T); everything else (A, AO, N, G1-G3) is
           non-claiming. Graded stakes go through route-nonclaim.

Coefficients: the six coef_gp_turf_2026*.csv in coeff_dir (header + one row of
betas; fitted on data through 2026-10-03). Do not refit from the SAS tables.
"""
import numpy as np
import pandas as pd
from pathlib import Path

from score_dmr_turf import _logit_prob
from gp_turf_vars import GP_TURF_VAR_ALIASES

_META = {"_link_", "_type_", "_status_", "_name_", "_lnlike_", "_esttype_"}
GP_TURF_MODELS = {   # model key -> coef filename stem
    "sprint": "coef_gp_turf_2026sprint", "route": "coef_gp_turf_2026route",
    "SC": "coef_gp_turf_2026_sc", "SN": "coef_gp_turf_2026_sn",
    "RC": "coef_gp_turf_2026_rc", "RN": "coef_gp_turf_2026_rn",
}
GP_CLAIM_TYPES = ("C", "CO", "R", "T")
GP_MAIDEN_TYPES = ("M", "S", "MO")     # all excluded from the GP turf build


def load_gp_turf_betas(coef_dir):
    """{model_key: {"Intercept": b0, column: beta, ...}} for the six GP turf models.
    Raises if a file is missing: a silent KEE fallback here would be wrong."""
    betas = {}
    for key, stem in GP_TURF_MODELS.items():
        row = pd.read_csv(Path(coef_dir) / f"{stem}.csv").iloc[0]
        b = {"Intercept": float(row["Intercept"])}
        for c in row.index:
            if c.strip().lower() in _META or c == "Intercept":
                continue
            if pd.notna(row[c]):
                # keyed by the FRAME column the model reads (two shared names
                # are GP-scoped rebuilds; see gp_turf_vars.GP_TURF_VAR_ALIASES)
                b[GP_TURF_VAR_ALIASES.get(c, c)] = float(row[c])
        betas[key] = b
    return betas


def _ci(df, name, default):
    if name in df.columns:
        return df[name]
    lc = {c.lower(): c for c in df.columns}
    a = lc.get(name.lower())
    return df[a] if a is not None else pd.Series(default, index=df.index)


def gp_turf_domain(df):
    """Boolean mask of the rows the GP turf model was built for: turf,
    non-maiden. MO (maiden optional claiming) is a maiden type in the SAS build,
    so MO turf races are NOT in the domain; they keep the fallback family's
    turf scoring exactly as before this port."""
    surf = _ci(df, "Surface", "").fillna("").astype(str).str.strip().str.upper()
    rt = _ci(df, "RaceType", "").fillna("").astype(str).str.strip().str.upper()
    return (surf == "T") & ~rt.isin(GP_MAIDEN_TYPES)


def _route(df):
    """Per-row routing; the single source of truth shared with attribution.
    Returns (distkey, cellkey) numpy arrays aligned to df rows."""
    is_sprint = pd.to_numeric(_ci(df, "sprint", 0), errors="coerce").fillna(0).astype(int) == 1
    is_claim = (_ci(df, "RaceType", "").fillna("").astype(str).str.strip().str.upper()
                .isin(GP_CLAIM_TYPES))
    distkey = np.where(is_sprint, "sprint", "route")
    cellkey = np.where(is_sprint, np.where(is_claim, "SC", "SN"),
                       np.where(is_claim, "RC", "RN"))
    return distkey, cellkey


def score_gp_turf_components(df, coef_dir):
    """DataFrame of p_dist, p_cell, distkey, cellkey and the raw blend (for parity
    checks against the SAS cell predictions)."""
    betas = load_gp_turf_betas(coef_dir)
    distkey, cellkey = _route(df)
    P = {k: _logit_prob(df, b).to_numpy(dtype=float) for k, b in betas.items()}
    rows = np.arange(len(df))
    p_dist = np.empty(len(df)); p_cell = np.empty(len(df))
    for k in ("sprint", "route"):
        m = distkey == k; p_dist[m] = P[k][m]
    for k in ("SC", "SN", "RC", "RN"):
        m = cellkey == k; p_cell[m] = P[k][m]
    return pd.DataFrame({"distkey": distkey, "cellkey": cellkey, "p_dist": p_dist,
                         "p_cell": p_cell, "blend_raw": (p_dist + p_cell) / 2.0},
                        index=df.index)


def score_gp_turf(df, coef_dir, race_group=("Track", "Date", "Race"), normalize=True):
    """Win prob per row = mean(distance model, crossed model).

    normalize=True  -> within-race normalized (validation / standalone use).
    normalize=False -> RAW blend (production: score._normalize_probabilities does
                       the within-race normalize once for all segments).
    Scores EVERY row it is given: pass only the turf non-maiden subset."""
    raw = score_gp_turf_components(df, coef_dir)["blend_raw"]
    if not normalize:
        return raw
    s = raw.groupby([df[c] for c in race_group]).transform("sum")
    return raw / s
