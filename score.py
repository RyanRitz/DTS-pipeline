"""
DTS Pipeline — score.py
==========================
Replicates SAS PROC SCORE (type=parms) using the .sas7bdat coefficient files.

PROC SCORE with type=parms does:
  log_odds = Intercept + b1*x1 + b2*x2 + ... + bn*xn
  probability = 1 / (1 + exp(-log_odds))   [sigmoid / logistic]

Each coefficient file has ONE row (the parameter estimates) with columns:
  _NAME_  → output variable name (e.g. 'res_marker')
  Intercept, var1, var2, ...  → coefficients

For each horse, the score = dot product of (feature vector · coefficient vector).
Only coefficients where the feature is non-missing are included (SAS dynamic newvars logic).

Usage:
    from pipeline.score import run_scoring
    results = run_scoring(df, coeff_dir="coefficients/")
"""

import numpy as np
import pandas as pd
import pyreadstat
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Vig assumption for implied probability normalization
VIG = 1.2049


# =============================================================================
# Main entry point
# =============================================================================

def run_scoring(df: pd.DataFrame, coeff_dir: str | Path, config) -> pd.DataFrame:
    """
    Run the full scoring pipeline: Dirt → Turf → Maiden → ensemble → probabilities.

    Parameters
    ----------
    df       : output of features.engineer_features()
    coeff_dir: path to directory containing .sas7bdat coefficient files
    config   : the config module (for DIRT_MODELS, TURF_MODELS, MAIDEN_MODELS)

    Returns
    -------
    DataFrame with DTSOdds, ProbToWin, ValBetIf, rank, etc.
    """
    coeff_dir = Path(coeff_dir)
    logger.info("Running scoring engine...")

    # Score each segment
    dirt_df   = _score_dirt(df, coeff_dir, config)
    turf_df   = _score_turf(df, coeff_dir, config)
    maiden_df = _score_maiden(df, coeff_dir, config)

    # Combine all segments
    validated = _combine_segments(dirt_df, turf_df, maiden_df, df)

    # Dual-entry handling and final probability normalization
    validated = _normalize_probabilities(validated)

    # Build output columns
    validated = _build_output(validated)

    logger.info(f"  Scoring complete: {len(validated)} horses scored")
    return validated


# =============================================================================
# Segment scoring
# =============================================================================

def _dirt_ny_restricted(df: pd.DataFrame) -> pd.Series:
    """
    Race-level NY-bred-restricted flag.  True for every horse in a race whose
    RaceConditions1 text mentions "NEW YORK" — matching the SAS
    index(res_condition1,"NEW YORK") test used to fit the Saratoga NY model.
    RaceConditions1 is stamped on only one row per race, so the per-row hit is
    propagated to all rows in the race via groupby-any.  If the column is
    absent, the flag is all-False (NY routing is simply inactive).
    """
    rc = (df.get("RaceConditions1", pd.Series("", index=df.index))
            .fillna("").astype(str).str.upper())
    has_ny = rc.str.contains("NEW YORK", regex=False).astype(int)
    grp = [df.get(c, pd.Series("", index=df.index)) for c in ["Track", "Date", "Race"]]
    # propagate to all rows in the race (max of 0/1 == "any"), robust across pandas versions
    return has_ny.groupby(grp).transform("max").astype(bool)


def _score_dirt(df: pd.DataFrame, coeff_dir: Path, config) -> pd.DataFrame:
    """
    Score all dirt non-maiden races.

    The ensemble composition is FAMILY-CONFIGURABLE via config.DIRT_ENSEMBLE,
    a list of (model_key, filter_name) pairs.  When a family declares nothing
    (e.g. KEE), it defaults to the legacy [c, n, s, r] blend with no core —
    so existing families score exactly as before.

      filter_name -> subset
        all       : every dirt non-maiden horse        (the "core" model)
        claim     : claiming       (clm=True)
        nonclaim  : non-claiming   (clm=False)
        sprint    : sprint         (sprint=True)
        route     : route          (sprint=False)

    config.DIRT_NY_MODEL (optional): a model key whose coefficient file scores
    NY-bred-restricted races ALONE, bypassing the ensemble — replicating the
    SAS Saratoga build (predicted = predictedcoreNY for NY races).  Because the
    ensemble = mean of whichever sub-models scored a horse, restricting the
    ensemble components to non-NY races and scoring NY races only with the NY
    model makes NY horses collapse to the NY prediction automatically.
    """
    spec = getattr(config, "DIRT_ENSEMBLE", None) or [
        ("c", "claim"), ("n", "nonclaim"), ("s", "sprint"), ("r", "route")]
    ny_key = getattr(config, "DIRT_NY_MODEL", None)

    filters = {
        "all":      lambda d: _filter(d, surf="D", maiden=False),
        "claim":    lambda d: _filter(d, surf="D", maiden=False, clm=True),
        "nonclaim": lambda d: _filter(d, surf="D", maiden=False, clm=False),
        "sprint":   lambda d: _filter(d, surf="D", maiden=False, sprint=True),
        "route":    lambda d: _filter(d, surf="D", maiden=False, sprint=False),
    }

    ny_mask = (_dirt_ny_restricted(df) if ny_key
               else pd.Series(False, index=df.index))

    # dirt-specific variable swaps. Default (KEE) replaces trnwcm_sart / jckcm2_sarm
    # with their dirt-std variants. A family can override with its own dict (or {}
    # for none) via config.DIRT_VAR_OVERRIDES — SAR uses {} because its models were
    # built on the plain (general-std) trnwcm_sart.
    overrides = getattr(config, "DIRT_VAR_OVERRIDES", None)
    if overrides is None:
        overrides = {"trnwcm_sart": "trnwcm_sart_tempdirt",
                     "jckcm2_sarm": "jckcm2_sarm_DIRT"}

    def _dirt_overrides(subset):
        for target, source in overrides.items():
            if source in df.columns:
                subset[target] = subset.get(source, np.nan)
        return subset

    scored_parts = {}
    marker_map = []

    # Ensemble components (restricted to non-NY races when NY routing is on)
    for key, fname in spec:
        fn = filters.get(fname)
        if fn is None:
            logger.warning(f"  Unknown dirt filter '{fname}' for model '{key}' — skipped")
            continue
        coeff_name = config.DIRT_MODELS.get(key, "")
        coeff_file = coeff_dir / coeff_name
        if not coeff_name or not coeff_file.exists():
            logger.warning(f"  Dirt model '{key}' coefficient file not found: {coeff_file}")
            continue
        subset = fn(df).copy()
        if ny_key:
            subset = subset.loc[~ny_mask.reindex(subset.index).fillna(False)]
        subset = _dirt_overrides(subset)
        scored_parts[key] = _proc_score(subset, coeff_file, f"res_marker{key}")
        marker_map.append((key, f"res_marker{key}", f"predicted{key}"))

    # NY-bred-restricted races -> scored by the NY model ALONE
    if ny_key:
        coeff_name = config.DIRT_MODELS.get(ny_key, "")
        coeff_file = coeff_dir / coeff_name
        if coeff_name and coeff_file.exists():
            subset = _filter(df, surf="D", maiden=False).copy()
            subset = subset.loc[ny_mask.reindex(subset.index).fillna(False)]
            if len(subset):
                subset = _dirt_overrides(subset)
                scored_parts[ny_key] = _proc_score(subset, coeff_file, f"res_marker{ny_key}")
                marker_map.append((ny_key, f"res_marker{ny_key}", f"predicted{ny_key}"))
        else:
            logger.warning(f"  Dirt NY model '{ny_key}' coefficient file not found: {coeff_file}")

    return _merge_scored_parts(scored_parts, df, marker_map,
                                ensemble_col="predicted", model_id=1)


def _score_turf(df: pd.DataFrame, coeff_dir: Path, config) -> pd.DataFrame:
    """
    Score all turf non-maiden races.

    Two paths, selected by config.TURF_ENSEMBLE:
      * None (KEE, default)  -> legacy flat 4-model blend (s/r/hp/lp). Unchanged.
      * list of cells (SAR)  -> course x distance x class HIERARCHY, with the
                                probabilities of whichever cells a horse
                                qualifies for averaged (matches the SAS hcall
                                mean), plus coreNY/NYr NY-bred routing.
    """
    ensemble = getattr(config, "TURF_ENSEMBLE", None)
    if ensemble:
        return _score_turf_hierarchy(df, coeff_dir, config, ensemble)
    return _score_turf_legacy(df, coeff_dir, config)


def _score_turf_legacy(df: pd.DataFrame, coeff_dir: Path, config) -> pd.DataFrame:
    """Legacy KEE turf scorer — 4 models: s, r, hp, lp. Unchanged behaviour."""
    models = [
        ("s",  lambda d: _filter(d, surf="T", maiden=False, sprint=True),              "res_markers"),
        ("r",  lambda d: _filter(d, surf="T", maiden=False, sprint=False),             "res_markerr"),
        ("hp", lambda d: _filter(d, surf="T", maiden=False, race_type_not_in=["A","R"]), "res_markerehp"),
        ("lp", lambda d: _filter(d, surf="T", maiden=False, race_type_in=["A","R"]),    "res_markerelp"),
    ]

    scored_parts = {}
    for key, filter_fn, output_col in models:
        coeff_file = coeff_dir / config.TURF_MODELS.get(key, "")
        if not coeff_file.exists():
            logger.warning(f"  Turf model '{key}' not found: {coeff_file}")
            continue
        subset = filter_fn(df).copy()
        scored = _proc_score(subset, coeff_file, output_col)
        scored_parts[key] = scored

    return _merge_scored_parts(scored_parts, df, [
        ("s",  "res_markers",   "predicteds"),
        ("r",  "res_markerr",   "predictedr"),
        ("hp", "res_markerehp", "predictedhp"),
        ("lp", "res_markerelp", "predictedlp"),
    ], ensemble_col="predicted", model_id=2)


def _score_turf_hierarchy(df: pd.DataFrame, coeff_dir: Path, config,
                          ensemble: list) -> pd.DataFrame:
    """
    SAR turf hierarchy. Each cell = (model_key, course, dist, cls):
       course : 'i' inner ('t'), 'o' Mellon ('T'), or None (all turf)
       dist   : 'sp' sprint (<=1540y), 'rt' route (>1540y), or None
       cls    : 'cl' claiming (RaceType in C/CO), 'nc' non-claiming, or None
    A horse's prediction is the mean of the sigmoid of every cell whose mask it
    satisfies (NaN-skipping, via _merge_scored_parts) — bit-for-bit the SAS
    `mean(pval)` over hcall. The Mellon fix is encoded by the ensemble simply
    not listing any 'o' cells, so Mellon horses fall to the pooled cells.

    NY-bred-restricted turf races (RaceConditions1 ~ 'NEW YORK') bypass the
    hierarchy: they are scored by TURF_NY_MODEL (all NY) and, for routes,
    averaged with TURF_NY_ROUTE_MODEL — matching the SAS
    `predicted = mean(of predictedcoreNY, predictedNYr)`.

    Inner vs Mellon is read from the *case* of Surface ('t' vs 'T'); the column
    must reach scoring un-uppercased (it does — features.py uses a local copy).
    """
    tm = config.TURF_MODELS
    ny_model       = getattr(config, "TURF_NY_MODEL", None)
    ny_route_model = getattr(config, "TURF_NY_ROUTE_MODEL", None)
    ny_mask = (_dirt_ny_restricted(df) if ny_model
               else pd.Series(False, index=df.index))

    surf_raw = df.get("Surface", pd.Series("", index=df.index)).astype(str)
    surf_u   = surf_raw.str.upper().fillna("")
    rt       = df.get("RaceType", pd.Series("", index=df.index)).fillna("").astype(str).str.upper()
    dist     = df.get("Distanceinyards", pd.Series(np.nan, index=df.index)).abs()

    is_turf      = surf_u == "T"
    is_nonmaiden = ~rt.isin(["M", "S"])
    is_inner     = surf_raw == "t"          # case-sensitive: lowercase 't' = inner
    is_sprint    = dist <= 1540
    is_route     = dist > 1540
    is_claim     = rt.isin(["C", "CO"])

    def cell_mask(course, dst, cls):
        m = is_turf & is_nonmaiden & (~ny_mask)
        if   course == "i": m = m & is_inner
        elif course == "o": m = m & is_turf & (~is_inner)
        if   dst == "sp":   m = m & is_sprint
        elif dst == "rt":   m = m & is_route
        if   cls == "cl":   m = m & is_claim
        elif cls == "nc":   m = m & (~is_claim)
        return m

    scored_parts = {}
    marker_map = []

    # ── Non-NY hierarchy cells ────────────────────────────────────────────
    for cell in ensemble:
        key, course, dst, cls = cell
        coeff_name = tm.get(key, "")
        coeff_file = coeff_dir / coeff_name
        if not coeff_name or not coeff_file.exists():
            logger.warning(f"  Turf cell '{key}' coeff file missing: {coeff_file}")
            continue
        subset = df[cell_mask(course, dst, cls)].copy()
        if len(subset) == 0:
            continue
        scored_parts[key] = _proc_score(subset, coeff_file, f"res_marker_t_{key}")
        marker_map.append((key, f"res_marker_t_{key}", f"predicted_t_{key}"))

    # ── NY-bred turf: coreNY (all NY) + NYr (NY routes) ───────────────────
    for nk, route_only in [(ny_model, False), (ny_route_model, True)]:
        if not nk:
            continue
        coeff_name = tm.get(nk, "")
        coeff_file = coeff_dir / coeff_name
        if not coeff_name or not coeff_file.exists():
            logger.warning(f"  Turf NY model '{nk}' coeff file missing: {coeff_file}")
            continue
        m = is_turf & is_nonmaiden & ny_mask
        if route_only:
            m = m & is_route
        subset = df[m].copy()
        if len(subset) == 0:
            continue
        scored_parts[nk] = _proc_score(subset, coeff_file, f"res_marker_t_{nk}")
        marker_map.append((nk, f"res_marker_t_{nk}", f"predicted_t_{nk}"))

    return _merge_scored_parts(scored_parts, df, marker_map,
                               ensemble_col="predicted", model_id=2)


def _score_maiden(df: pd.DataFrame, coeff_dir: Path, config) -> pd.DataFrame:
    """Score all maiden / maiden special weight races.

    Two paths: config.MAIDEN_ENSEMBLE set (SAR 3-suite 32-cell blend) ->
    _score_maiden_sar; otherwise the legacy KEE 15-model blend below.
    """
    if getattr(config, "MAIDEN_ENSEMBLE", None):
        return _score_maiden_sar(df, coeff_dir, config)
    surf = df["Surface"].str.upper().fillna("")
    rt   = df["RaceType"].fillna("")
    dist = df["Distanceinyards"].abs()
    sp   = dist <= 1540   # 5scoring.sas sprint definition

    # Model filter definitions
    models = [
        (1,   lambda d: d[(sp) & (rt == "S") & (~surf.isin(["D"]))],      "res_marker1"),
        (2,   lambda d: d[(sp) & (rt == "S") &   (surf.isin(["D"]))],     "res_marker2"),
        (3,   lambda d: d[(~sp) & (rt == "S") & (~surf.isin(["D"]))],     "res_marker3"),
        (4,   lambda d: d[(~sp) & (rt == "S") &   (surf.isin(["D"]))],    "res_marker4"),
        (6,   lambda d: d[(sp) & (rt == "M") &   (surf.isin(["D"]))],     "res_marker6"),
        (8,   lambda d: d[(~sp) & (rt == "M") &   (surf.isin(["D"]))],    "res_marker8"),
        (9,   lambda d: d[(rt == "S") & (~surf.isin(["D"]))],              "res_marker9"),
        (10,  lambda d: d[(rt == "S") &   (surf.isin(["D"]))],             "res_marker10"),
        (12,  lambda d: d[(rt == "M") &   (surf.isin(["D"]))],             "res_marker12"),
        (13,  lambda d: d[(rt == "S") & (sp)],                             "res_marker13"),
        (14,  lambda d: d[(rt == "S") & (~sp)],                            "res_marker14"),
        (15,  lambda d: d[(rt == "M") & (sp)],                             "res_marker15"),
        (16,  lambda d: d[(rt == "M") & (~sp)],                            "res_marker16"),
        ("M", lambda d: d[(rt == "M")],                                    "res_markerM"),
        ("S", lambda d: d[(rt == "S")],                                    "res_markerS"),
    ]

    scored_parts = {}
    for key, filter_fn, output_col in models:
        coeff_filename = config.MAIDEN_MODELS.get(key, "")
        if not coeff_filename:
            continue
        coeff_file = coeff_dir / coeff_filename
        if not coeff_file.exists():
            logger.warning(f"  Maiden model {key} not found: {coeff_file}")
            continue
        subset = filter_fn(df).copy()
        if len(subset) == 0:
            continue
        scored = _proc_score(subset, coeff_file, output_col)
        scored_parts[key] = scored

    # Build marker → predicted mapping
    marker_map = [(k, f"res_marker{k}", f"predicted{k}") for k in scored_parts.keys()]

    result = _merge_scored_parts(scored_parts, df, marker_map,
                                  ensemble_col=None, model_id=3)

    # Compute ensemble scores matching SAS formula exactly
    def safe_mean(cols):
        existing = [c for c in cols if c in result.columns]
        if not existing:
            return pd.Series(np.nan, index=result.index)
        return result[existing].mean(axis=1, skipna=True)

    # Apply sigmoid to each predicted
    for key in scored_parts.keys():
        marker_col = f"res_marker{key}"
        pred_col   = f"predicted{key}"
        if marker_col in result.columns:
            result[pred_col] = sigmoid(result[marker_col])

    score1_cols = [f"predicted{k}" for k in [1,2,3,4,6,8]   if f"predicted{k}" in result.columns]
    score2_cols = [f"predicted{k}" for k in [9,10,12]        if f"predicted{k}" in result.columns]
    score3_cols = [f"predicted{k}" for k in [13,14,15,16]    if f"predicted{k}" in result.columns]
    score4_cols = [f"predicted{k}" for k in ["M","S"]        if f"predicted{k}" in result.columns]

    result["score1"] = safe_mean(score1_cols)
    result["score2"] = safe_mean(score2_cols)
    result["score3"] = safe_mean(score3_cols)
    result["score4"] = safe_mean(score4_cols)

    # ── Final ensemble ─────────────────────────────────────────────────────
    # SAS behavior: in arithmetic, any missing operand propagates missing.
    # So if score1, score2, or score3 is NaN, the entire `predicted` is NaN
    # and the race effectively drops out of the published card.
    #
    # The one explicit exception, replicated from this SAS one-liner:
    #
    #     /* FIX FOR MAIDEN CLAIMING TURF — NO RACES AT KEE */
    #     data validated_madien; set validated_madien;
    #       if racetype='M' and surface in ('T','t')
    #         then predicted=mean(of predicted13, predicted14,
    #                                predicted15, predicted16);
    #     run;
    #
    # is applied AFTER the main ensemble runs. For maiden turf races where
    # the standard weighted ensemble produced NaN (because score1/score2
    # were empty for lack of turf-maiden models), we override with the
    # unweighted mean of score3 (which IS populated for these races).
    result["predicted"] = (
        result["score1"] * 0.50 +
        result["score2"] * 0.25 +
        result["score3"] * 0.25
    )

    # Maiden-turf override: replicates the SAS post-scoring fix
    surf_upper = result.get("Surface", pd.Series(dtype=object)).astype(str).str.upper()
    is_maiden_turf = (result.get("RaceType", "") == "M") & (~surf_upper.isin(["D"]))
    result.loc[is_maiden_turf, "predicted"] = result.loc[is_maiden_turf, "score3"]

    return result


def _score_maiden_sar(df: pd.DataFrame, coeff_dir: Path, config) -> pd.DataFrame:
    """
    SAR maiden 3-suite blend (mirrors BTSM_SAR_MadienModel_2026 scoring):

        predicted = 0.50*score1 + 0.25*score2 + 0.25*score3

    where each suite score is the mean over the single cell a horse falls in:
        suite 1 (leaf)      : racetype x distance x surface   (x NY)
        suite 2 (rt x surf) : racetype x surface, pooled over distance
        suite 3 (rt x dist) : racetype x distance, pooled over surface

    32 cells (16 open + 16 NY-bred). Each maiden horse matches exactly one cell
    per suite; NY-bred horses route to the *NY cells. Turf = surface not in {D}.
    """
    surf = df.get("Surface", pd.Series("", index=df.index)).astype(str).str.upper().fillna("")
    rt   = df.get("RaceType", pd.Series("", index=df.index)).astype(str).str.upper().fillna("")
    is_dirt = surf.isin(["D"])
    if "sprint" in df.columns:
        sp = pd.to_numeric(df["sprint"], errors="coerce").fillna(0).astype(int) == 1
    else:
        sp = df.get("Distanceinyards", pd.Series(np.nan, index=df.index)).abs() <= 1540
    ny = pd.to_numeric(df.get("NYBredRace", pd.Series(0, index=df.index)),
                       errors="coerce").fillna(0).astype(int)

    def cell_mask(racetype, d, s, nyv):
        m = (rt == racetype) & (ny == nyv)
        if   d == "sp": m = m & sp
        elif d == "rt": m = m & (~sp)
        if   s == "T":  m = m & (~is_dirt)     # turf = not dirt
        elif s == "D":  m = m & is_dirt
        return m

    scored_parts = {}
    suite_cells = {1: [], 2: [], 3: []}        # suite -> [(key, marker_col, pred_col)]
    for fname, suite, racetype, d, s, nyv in config.MAIDEN_ENSEMBLE:
        coeff_file = coeff_dir / fname
        if not coeff_file.exists():
            logger.warning(f"  Maiden cell coeff missing: {coeff_file}")
            continue
        key = fname.replace(".sas7bdat", "")
        subset = df[cell_mask(racetype, d, s, nyv)].copy()
        if len(subset) == 0:
            continue
        mcol = f"res_marker_m_{key}"
        scored_parts[key] = _proc_score(subset, coeff_file, mcol)
        suite_cells[suite].append((key, mcol, f"pred_m_{key}"))

    marker_map = [(k, mcol, pcol) for s in (1, 2, 3) for (k, mcol, pcol) in suite_cells[s]]
    result = _merge_scored_parts(scored_parts, df, marker_map, ensemble_col=None, model_id=3)

    def suite_mean(suite):
        pcols = []
        for key, mcol, pcol in suite_cells[suite]:
            if mcol in result.columns:
                result[pcol] = sigmoid(result[mcol])
                pcols.append(pcol)
        pcols = [c for c in pcols if c in result.columns]
        return (result[pcols].mean(axis=1, skipna=True) if pcols
                else pd.Series(np.nan, index=result.index))

    result["score1"] = suite_mean(1)
    result["score2"] = suite_mean(2)
    result["score3"] = suite_mean(3)
    result["predicted"] = (result["score1"] * 0.50
                           + result["score2"] * 0.25
                           + result["score3"] * 0.25)
    return result


# =============================================================================
# Core PROC SCORE replication
# =============================================================================

def _proc_score(df: pd.DataFrame, coeff_file: Path, output_col: str) -> pd.DataFrame:
    """
    Replicate PROC SCORE type=parms.

    Reads the coefficient file, intersects with available non-missing features,
    computes log_odds = dot(features, coefficients), stores in output_col.

    Parameters
    ----------
    df         : subset DataFrame for this model
    coeff_file : path to .sas7bdat coefficient file
    output_col : name for the output log-odds column (e.g. 'res_markerc')

    Returns
    -------
    df copy with output_col added
    """
    if len(df) == 0:
        return df.copy()

    df = df.copy()

    # Load coefficient file — supports both .csv (preferred) and .sas7bdat
    try:
        suffix = coeff_file.suffix.lower()
        if suffix == ".csv":
            coef_df = pd.read_csv(str(coeff_file))
        else:
            coef_df, _ = pyreadstat.read_sas7bdat(str(coeff_file))
    except Exception as e:
        logger.error(f"Failed to read {coeff_file}: {e}")
        df[output_col] = np.nan
        return df

    # The coefficient row has Intercept + feature columns
    # _NAME_ column = output variable name (we rename later)
    coef_row = coef_df.iloc[0]

    # Build feature vector from available columns
    # SAS: only uses features where coeff is non-missing (dynamic newvars)
    feature_cols = []
    coefficients = []

    for col in coef_df.columns:
        if col.upper() in ["_NAME_", "_TYPE_", "_LABEL_"]:
            continue
        coef_val = coef_row[col]
        if pd.isna(coef_val):
            continue
        if col == "Intercept":
            feature_cols.append("__intercept__")
            coefficients.append(float(coef_val))
        elif col in df.columns:
            feature_cols.append(col)
            coefficients.append(float(coef_val))

    if not feature_cols:
        logger.warning(f"  No matching features for {coeff_file.name}")
        df[output_col] = np.nan
        return df

    # Build feature matrix
    coef_array = np.array(coefficients)

    # Compute dot product row-wise
    log_odds = pd.Series(np.nan, index=df.index)

    for idx in df.index:
        row_vals = []
        for col in feature_cols:
            if col == "__intercept__":
                row_vals.append(1.0)
            else:
                val = df.at[idx, col]
                row_vals.append(float(val) if pd.notna(val) else 0.0)
        log_odds[idx] = np.dot(np.array(row_vals), coef_array)

    df[output_col] = log_odds
    logger.debug(f"  Scored {len(df)} horses with {coeff_file.name} "
                 f"({len(feature_cols)} features)")
    return df


def sigmoid(series: pd.Series) -> pd.Series:
    """Logistic sigmoid: 1 / (1 + exp(-x))"""
    return 1 / (1 + np.exp(-series.clip(-500, 500)))


# =============================================================================
# Segment merging and ensemble
# =============================================================================

def _merge_scored_parts(
    scored_parts: dict,
    original_df: pd.DataFrame,
    marker_map: list[tuple],
    ensemble_col: Optional[str],
    model_id: int,
) -> pd.DataFrame:
    """
    Merge scored sub-DataFrames back together and compute ensemble average.
    Handles the case where different sub-models score different subsets of horses.
    """
    key_cols = ["Track", "Date", "Race", "HorseName"]

    # Start with base info from original_df.
    # NOTE: this is an explicit carry-through whitelist — any column NOT listed
    # here is dropped during scoring. The display-only race-level fields
    # (RaceConditions1/2, WagerType1-9, AgeSexRestrictions, RaceName) must be
    # carried so the PDF race header can show the eligibility description and
    # the multi-race wager line. Omitting them silently blanks those fields.
    base_cols = key_cols + ["horsenum", "ProgramNumberifavailable",
                             "RaceType", "Surface", "Distanceinyards",
                             "NumOfEntries", "HorsesRan", "baseprob1", "baseprob2",
                             "MornLineOddsifavailable", "TodaysJockey", "TodaysTrainer",
                             "TodaysRaceClassification", "Purse",
                             # ── display-only race-level fields (header) ──
                             "AgeSexRestrictions", "RaceName",
                             "RaceConditions1", "RaceConditions2",
                             "WagerType1", "WagerType2", "WagerType3",
                             "WagerType4", "WagerType5", "WagerType6",
                             "WagerType7", "WagerType8", "WagerType9"]
    base_cols = [c for c in base_cols if c in original_df.columns]
    result = original_df[base_cols].copy()
    result["model"] = model_id

    # Merge each scored part's marker column back
    for key, marker_col, pred_col in marker_map:
        if key not in scored_parts:
            continue
        part = scored_parts[key]
        if marker_col not in part.columns:
            continue
        # Only keep key cols + the marker column
        merge_part = part[key_cols + [marker_col]].drop_duplicates(subset=key_cols)
        result = result.merge(merge_part, on=key_cols, how="left")

    # Apply sigmoid and compute ensemble for non-maiden models
    if ensemble_col:
        pred_cols = []
        for _, marker_col, pred_col in marker_map:
            if marker_col in result.columns:
                result[pred_col] = sigmoid(result[marker_col])
                pred_cols.append(pred_col)
        # Ensemble = mean of whichever predicted values are non-NaN
        if pred_cols:
            result[ensemble_col] = result[pred_cols].mean(axis=1, skipna=True)
            # Only set ensemble if at least one model scored this horse
            all_nan = result[pred_cols].isna().all(axis=1)
            result.loc[all_nan, ensemble_col] = np.nan

    return result


def _combine_segments(
    dirt_df: pd.DataFrame,
    turf_df: pd.DataFrame,
    maiden_df: pd.DataFrame,
    original_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Stack dirt, turf, and maiden results into a single DataFrame.
    Mirrors: data validated; set validated_dirt validated_turf validated_madien;
    """
    parts = []
    for part_df, mid in [(dirt_df, 1), (turf_df, 2), (maiden_df, 3)]:
        if part_df is not None and len(part_df) > 0:
            part = part_df.copy()
            part["model"] = mid
            parts.append(part)

    if not parts:
        return original_df.copy()

    combined = pd.concat(parts, ignore_index=True, sort=False)

    # Normalize program number
    def _norm_prog(val):
        coupled = {"1A":1,"1B":1,"1C":1,"1X":1,"2A":2,"2B":2,"2X":2,
                   "3B":3,"3C":3,"3X":3,"4D":4,"4X":4}
        if val in coupled:
            return float(coupled[val])
        try:
            return float(val)
        except (ValueError, TypeError):
            return np.nan

    if "ProgramNumberifavailable" in combined.columns:
        combined["ProgramNumber"] = combined["ProgramNumberifavailable"].apply(_norm_prog)

    return combined


# =============================================================================
# Probability normalization and output building
# =============================================================================

def _normalize_probabilities(df: pd.DataFrame) -> pd.DataFrame:
    """
    Handle coupled entries, normalize win probabilities, compute DTSOdds.
    Mirrors the final data steps in SAS lines 2840–2960.

    Key fix: each horse appears 3 times (dirt/turf/maiden). 
    Keep only the row where predicted is non-NaN (the model that actually scored it).
    For horses scored by multiple models, take the first non-NaN.
    """
    df = df.copy()

    # -----------------------------------------------------------------------
    # Step 1: Collapse 3 rows per horse → 1 row (keep the scored model)
    # -----------------------------------------------------------------------
    # Sort so non-NaN predicted comes first for each horse
    df = df.sort_values(
        ["Track", "Date", "Race", "HorseName", "predicted"],
        na_position="last"
    )

    # Keep first non-NaN predicted per horse
    df = df.drop_duplicates(
        subset=["Track", "Date", "Race", "HorseName"],
        keep="first"
    ).reset_index(drop=True)

    # -----------------------------------------------------------------------
    # Step 2: Coupled entry — sum predicted for same program number per race
    # -----------------------------------------------------------------------
    group = ["Track", "Date", "Race", "ProgramNumber"]
    dual = df.groupby(group)["predicted"].sum().reset_index()
    dual = dual.rename(columns={"predicted": "predicted_alt"})
    df = df.merge(dual, on=group, how="left")

    # For coupled entries, keep only the entry with highest predicted
    df = df.sort_values(["Track", "Date", "Race", "ProgramNumber", "predicted"],
                        na_position="last", ascending=[True,True,True,True,False])
    df["_is_coupled"] = df.duplicated(
        subset=["Track", "Date", "Race", "ProgramNumber"], keep=False)
    df["_keep"] = ~df.duplicated(
        subset=["Track", "Date", "Race", "ProgramNumber"], keep="first")
    
    # For non-coupled entries keep as-is; for coupled use predicted_alt on first row
    df.loc[df["_is_coupled"] & df["_keep"], "predicted"] = \
        df.loc[df["_is_coupled"] & df["_keep"], "predicted_alt"]
    df = df[df["_keep"]].drop(columns=["_is_coupled", "_keep"]).reset_index(drop=True)

    # -----------------------------------------------------------------------
    # Step 3: Race totals
    # -----------------------------------------------------------------------
    race_group = ["Track", "Date", "Race"]
    race_stats = df.groupby(race_group).agg(
        predtotprob=("predicted", "sum"),
        favodds=("predicted", "min"),
    ).reset_index()
    df = df.merge(race_stats, on=race_group, how="left")

    # -----------------------------------------------------------------------
    # Step 4: Normalized probability and odds
    # -----------------------------------------------------------------------
    df["norm_predprob"] = df["predicted"] / df["predtotprob"]
    df["pred_odds"]     = (1 / df["norm_predprob"]) - 1

    # Odds with vig (replicates SAS formula exactly)
    vig = VIG
    p   = df["predicted"]
    pt  = df["predtotprob"]
    df["pred_odds_wvig"] = (
        (1 - (p + ((p / pt) * (vig - pt)))) /
           (p + ((p / pt) * (vig - pt)))
    )

    # Force strength category
    if "baseprob1" in df.columns:
        df["fs_odds"] = df["norm_predprob"] / df["baseprob1"]
        df["fs_catp"] = np.select(
            [df["fs_odds"] >  2.00,
             df["fs_odds"] >  1.30,
             df["fs_odds"] >  1.00,
             df["fs_odds"] >  0.75,
             df["fs_odds"] >  0.50],
            [1, 2, 3, 4, 5],
            default=6)

    return df


def _build_output(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute final output columns: DTSOdds, ProbToWin, ValBetIf, rank, etc.
    Mirrors: data roi2 in SAS lines 2960–3109.
    """
    df = df.copy()

    # DTSOdds — rounded to nearest reasonable increment
    df["DTSOdds"] = np.where(
        df["pred_odds_wvig"] < 1.9,
        (df["pred_odds_wvig"] / 0.2).round() * 0.2,
        np.where(
            df["pred_odds_wvig"] < 4.75,
            (df["pred_odds_wvig"] / 0.5).round() * 0.5,
            df["pred_odds_wvig"].round(0)
        )
    )

    df["MornOdds"]  = df.get("MornLineOddsifavailable", np.nan)
    df["ProbToWin"] = df["norm_predprob"]

    # -----------------------------------------------------------------------
    # Scratch-adjusted morning line  (MornOddsAdj) — BEHIND THE CURTAIN
    # -----------------------------------------------------------------------
    # After scratches are removed upstream (apply_scratches), the raw morning
    # line no longer forms a coherent book: the scratched horses' implied
    # probability is simply gone, so the surviving field's prices are stale
    # (too long). We rebuild a consistent line by:
    #   1. converting each survivor's raw ML to implied prob  p = 1/(ml+1)
    #   2. renormalizing the per-race field to the model's vig (VIG=1.2049),
    #      the SAME vig baked into DTSOdds — so DTSOdds and the adjusted ML
    #      sit on an apples-to-apples footing for value/best-bet flagging
    #   3. converting back to odds  ml_adj = (1/p_adj) - 1
    # This is used ONLY for value_tier / highlighting / best bets. The raw
    # MornOdds is still what gets DISPLAYED on the sheet.
    _ml = pd.to_numeric(df["MornOdds"], errors="coerce")
    _mlprob = 1.0 / (_ml + 1.0)                      # NaN where ML missing
    _race_key = ["Track", "Date", "Race"]
    _race_sum = df.assign(_p=_mlprob).groupby(_race_key)["_p"].transform("sum")
    # Scale factor maps the surviving field's prob mass onto VIG.
    _adj_prob = _mlprob * (VIG / _race_sum)
    # Clamp to (0, 0.99] so a tiny post-scratch field can't yield prob >= 1
    # (which would give zero/negative odds).
    _adj_prob = _adj_prob.clip(lower=1e-6, upper=0.99)
    df["MornOddsAdj"] = (1.0 / _adj_prob) - 1.0
    # Where the race had no usable ML at all, fall back to the raw value.
    df["MornOddsAdj"] = df["MornOddsAdj"].where(_race_sum > 0, df["MornOdds"])

    # po variants
    df["po"]   = df["pred_odds"]
    df["po_s"] = (df["pred_odds"] * 1.25).round(1)
    df["po_m"] = (df["pred_odds"] * 1.50).round(1)
    df["po_h"] = (df["pred_odds"] * 2.00).round(1)

    # Round po_s to same increments as DTSOdds
    df["po_s"] = np.where(
        df["po_s"] < 1.9,
        (df["po_s"] / 0.2).round() * 0.2,
        np.where(df["po_s"] < 4.75,
                 (df["po_s"] / 0.5).round() * 0.5,
                 df["po_s"].round(0)))

    # Race descriptor columns
    if "Purse" in df.columns:
        df["pursed"]  = (df["Purse"] / 1000).round(1)
    if "Distanceinyards" in df.columns:
        df["furlng"]  = (df["Distanceinyards"] / 220).round() / 2
    if "Race" in df.columns and "RaceType" in df.columns:
        df["RaceNum"] = df["Race"].astype(str) + df["RaceType"].fillna("") + \
                        df.get("pursed", pd.Series("", index=df.index)).astype(str) + \
                        df.get("Surface", pd.Series("", index=df.index)).fillna("") + \
                        df.get("furlng", pd.Series("", index=df.index)).astype(str)

    df["ROI_25_50_100"] = "[" + df["po_s"].astype(str) + " / " + \
                           df["po_m"].astype(str) + " / " + \
                           df["po_h"].astype(str) + "]"

    # Rank within race by ProbToWin descending
    df["rank"] = df.groupby(["Track", "Date", "Race"])["ProbToWin"] \
                   .rank(ascending=False, method="min")

    # ValBetIf — only show for top half of field
    if "NumOfEntries" in df.columns:
        df["ValBetIf"] = np.where(
            df["rank"] / df["NumOfEntries"] <= 0.5,
            df["po_s"], np.nan)
    else:
        df["ValBetIf"] = np.where(df["rank"] <= 4, df["po_s"], np.nan)

    df["ValueBetIf"] = df["ValBetIf"]

    # Race title
    if all(c in df.columns for c in ["Race", "TodaysRaceClassification", "pursed", "Surface", "furlng"]):
        df["racetitle"] = (df["Race"].astype(str) + " " +
                           df["TodaysRaceClassification"].fillna("") + " " +
                           df["pursed"].astype(str) + "K  " +
                           df["Surface"].fillna("") + "  " +
                           df["furlng"].astype(str) + "F")

    df["lookup"] = df.get("RaceType", pd.Series("", index=df.index)).fillna("") + \
                   df.get("Surface",  pd.Series("", index=df.index)).fillna("")

    df["Horse"] = df.get("HorseName", pd.Series("", index=df.index))
    df["Num"]   = df.get("ProgramNumber", df.get("horsenum", pd.Series(np.nan, index=df.index)))

    logger.info("  Output columns built successfully")
    return df


# =============================================================================
# Filter helpers
# =============================================================================

def _filter(
    df: pd.DataFrame,
    surf: Optional[str] = None,
    maiden: Optional[bool] = None,
    clm: Optional[bool] = None,
    sprint: Optional[bool] = None,
    race_type_in: Optional[list] = None,
    race_type_not_in: Optional[list] = None,
) -> pd.DataFrame:
    """Apply segment filter matching SAS data step subsetting."""
    mask = pd.Series(True, index=df.index)

    if surf is not None:
        mask &= df["Surface"].str.upper().fillna("") == surf.upper()

    if maiden is not None:
        # Maiden = RaceType starts with M or S
        rt = df["RaceType"].fillna("")
        if maiden:
            mask &= rt.isin(["M", "S"])
        else:
            mask &= ~rt.isin(["M", "S"])

    if clm is not None:
        rt = df["RaceType"].fillna("")
        if clm:
            mask &= rt == "C"
        else:
            mask &= rt != "C"

    if sprint is not None:
        # 5scoring.sas uses <= 1540 yards for sprint
        dist = df["Distanceinyards"].abs()
        if sprint:
            mask &= dist <= 1540
        else:
            mask &= dist > 1540

    if race_type_in is not None:
        mask &= df["RaceType"].fillna("").isin(race_type_in)

    if race_type_not_in is not None:
        mask &= ~df["RaceType"].fillna("").isin(race_type_not_in)

    return df[mask]


# =============================================================================
# Export helper — build final Excel output matching SAS myxls dataset
# =============================================================================

OUTPUT_COLUMNS = [
    "Track", "Date", "TodaysRaceClassification", "Purse",
    "TodaysTrainer", "TodaysJockey", "BRISRunStyledesignation",
    "xBRISPrimePowerRating",
    "Race", "Num", "Horse", "rank", "DTSOdds", "MornOdds",
    "ValueBetIf", "ProbToWin", "racetitle", "RaceType", "Surface",
    "furlng", "pursed", "NumOfEntries", "HorsesRan", "lookup",
    "JCKchngName", "CurTrackCond", "FrstPostTime",
]

def build_excel_output(df: pd.DataFrame) -> pd.DataFrame:
    """Select and order columns matching the SAS myxls export."""
    cols = [c for c in OUTPUT_COLUMNS if c in df.columns]
    return df[cols].sort_values(["Race", "Num", "ProbToWin"]).reset_index(drop=True)
