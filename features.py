"""
DTS Pipeline — features.py
=============================
Translates all feature engineering from scoring_KEE_APR26.sas into
vectorized pandas operations. This is a faithful Python replication of
the SAS DATA steps from lines 1–2412.

Organized into 8 logical blocks matching the SAS code structure:

  Block 1 — Base flags & surface / sex dummies
  Block 2 — Workout bullet flags & percent rank arrays
  Block 3 — Days-off averages & mud/turf history
  Block 4 — Pedigree ratings & weight change
  Block 5 — Trainer category pivot (63 categories × 4 stats)
  Block 6 — Speed slope, horses beaten, indicator flags
  Block 7 — Race-level aggregates (PROC SQL → groupby merge)
  Block 8 — Standardization, transforms, derived model inputs
             (categorical cuts, clipping, polynomial terms)

Usage:
    from features import engineer_features
    df = engineer_features(df)
"""

import numpy as np
import pandas as pd
import logging
from model_vars import build_model_vars

logger = logging.getLogger(__name__)


# =============================================================================
# Main entry point
# =============================================================================

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply all feature engineering to the parsed DRF DataFrame.

    Parameters
    ----------
    df : output of ingest_drf.load_drf()

    Returns
    -------
    df with all engineered features added, ready for score.py
    """
    logger.info("Engineering features...")

    df = _block1_base_flags(df)
    df = _block2_workout_flags(df)
    df = _block3_days_off_mud_turf(df)
    df = _block4_pedigree_weight(df)

    # ------------------------------------------------------------------
    # First race normalization pass — raw DRF + prerequisite columns
    # (EPS, pedigree ratings, HC flags, age, WPS pcts, etc.)
    # ------------------------------------------------------------------
    from race_normalize import compute_race_normalizations
    df = compute_race_normalizations(df)

    df = _block5_trainer_categories(df)
    df = _block6_speed_slopes_indicators(df)

    # ------------------------------------------------------------------
    # Supplementary normalization — build x/I ONLY for the specific
    # Block 5-6 computed variables needed by the scoring models.
    # These weren't available during the first race_normalize pass.
    # ------------------------------------------------------------------
    _SUPPLEMENT_VARS = [
        "tran_itm_58",                      # trainer turf-ITM% (Block 5) -> xtran_itm_58 feeds TrnITMTurf
        "r101109gt10", "r101109",          # jockey-trainer combo (Block 6)
        "TurfyLast5",                       # turf tendency (Block 3, PascalCase)
        "turffy_last5",                     # lowercase alias
        "workoutpctrnk1",                   # workout pct rank (Block 2 lowercase)
        "WorkoutPctRnk1",                   # PascalCase variant
        "StretchBtnLngthsonly1",            # stretch position (Block prereq)
        # WorkoutDate vars (datetime → need special handling)
        "WorkoutDate1","WorkoutDate2","WorkoutDate3",
        "WorkoutDate4","WorkoutDate5","WorkoutDate6",
    ]
    RACE_GROUP_S = ["Track", "Date", "Race"]
    SAS_EPOCH = pd.Timestamp('1960-01-01')

    for _col in _SUPPLEMENT_VARS:
        _ave = f"{_col}_ave"
        _xcol = f"x{_col}"
        _icol = f"I{_col}"
        if _col not in df.columns:
            continue
        _is_dt = pd.api.types.is_datetime64_any_dtype(df[_col])
        if not _is_dt and not pd.api.types.is_numeric_dtype(df[_col]):
            continue
        if _ave not in df.columns:
            _agg = df.groupby(RACE_GROUP_S)[_col].mean().reset_index().rename(columns={_col: _ave})
            df = df.merge(_agg, on=RACE_GROUP_S, how="left")
        if _xcol not in df.columns or df[_xcol].isna().all():
            raw = df[_col]
            ave = df[_ave]
            if _is_dt:
                # fractional days (not .dt.days, which floors and drops the
                # fractional part of the race mean — that shift flips
                # threshold tests like xwrkdate>=30 by up to a full day)
                raw = (raw - SAS_EPOCH) / pd.Timedelta(days=1)
                ave = ((ave - SAS_EPOCH) / pd.Timedelta(days=1)
                       if pd.api.types.is_datetime64_any_dtype(ave) else ave)
            df[_icol] = np.where(ave.isna()|(ave==0), 1.0, np.where(raw.isna(), np.nan, raw/ave))
            df[_xcol] = np.where(ave.isna()|raw.isna(), np.nan, raw-ave)

    df = _block7_race_aggregates(df)
    df = _block6_speed_slopes_indicators(df)
    df = _block7_race_aggregates(df)
    df = _block8_transforms(df)
    df = build_model_vars(df)

    logger.info(f"  Features engineered: {len(df.columns)} total columns")
    return df


# =============================================================================
# Block 1 — Base flags, surface/sex dummies, sprint flag
# =============================================================================

def _block1_base_flags(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # QSP null if never raced
    df.loc[df["RaceDate1"].isna(), "QuirinstyleSpeedPoints"] = np.nan

    # Year / month / day from date
    df["year"]  = df["Date"].dt.year
    df["month"] = df["Date"].dt.month
    df["day"]   = df["Date"].dt.day

    # Distance sign correction
    df["Distanceinyards"] = df["Distanceinyards"].abs()

    # Sprint flag (< 1760 yards = 8 furlongs)
    df["sprint"] = (df["Distanceinyards"] < 1760).astype(int)

    # Surface dummies
    surf = df["Surface"].str.upper().fillna("")
    df["surfacedirt"] = surf.isin(["D"]).astype(int)
    df["surfaceturf"] = surf.isin(["T"]).astype(int)
    df["surfaceothr"] = surf.isin(["H", "S"]).astype(int)

    # Sex dummies
    sex = df["Sex"].fillna("")
    df["sex_geld"] = (sex == "G").astype(int)
    df["sex_fem"]  = (sex == "F").astype(int)
    df["sex_colt"] = (sex == "C").astype(int)
    df["sex_h"]    = (sex == "H").astype(int)
    df["sex_male"] = (sex == "M").astype(int)
    df["sex_r"]    = (sex == "R").astype(int)

    # BRIS run style dummies
    rs = df["BRISRunStyledesignation"].fillna("")
    df["BRISRunstyle_E"]  = (rs == "E").astype(int)
    df["BRISRunstyle_S"]  = (rs == "S").astype(int)
    df["BRISRunstyle_P"]  = (rs == "P").astype(int)
    df["BRISRunstyle_EP"] = (rs == "E/P").astype(int)
    df["BRISRunstyle_NA"] = (rs == "NA").astype(int)

    # Null fills for trainer/jockey stats at meet
    fill_zero_cols = [
        "TrainerWinsCurrentMeet", "TrainerPlacesCurrentMeet", "TrainerShowsCureentMeet",
        "JKYatDisJkyonTurfStarts", "JKYatDisJkyonTurfWins", "JKYatDisJkyonTurfPlaces",
        "JKYatDisJkyonTurfShows", "JKYatDisJkyonTurfEarnings",
        "KeyStatofstarts1", "KS_wins1", "KS_ITM1", "KS_w_wins1", "KS_w_strts1", "KS_w_ITM1",
        "KeyStatofstarts2", "KS_wins2", "KS_ITM2", "KS_w_wins2", "KS_w_strts2", "KS_w_ITM2",
        "KeyStatofstarts3", "KS_wins3", "KS_ITM3", "KS_w_wins3", "KS_w_strts3", "KS_w_ITM3",
        "KeyStatofstarts4", "KS_wins4", "KS_ITM4", "KS_w_wins4", "KS_w_strts4", "KS_w_ITM4",
        "KeyStatofstarts5", "KS_wins5", "KS_ITM5", "KS_w_wins5", "KS_w_strts5", "KS_w_ITM5",
        "KeyStatofstarts6", "KS_wins6", "KS_ITM6", "KS_w_wins6", "KS_w_strts6", "KS_w_ITM6",
        "LTStrtsAllWeather", "LTWinsAllWeather", "LTPlaceAllWeather",
        "LTShowAllWeather", "LTEarnAllWeather",
    ]
    for col in fill_zero_cols:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    return df


# =============================================================================
# Block 2 — Workout bullet flags & percent rank arrays
# =============================================================================

def _block2_workout_flags(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    for i in range(1, 13):
        wt_col = f"WorkoutTime{i}"
        bl_col = f"WorkoutTime{i}Bullet"
        wd_col = f"WorkoutDist{i}"

        if wt_col in df.columns:
            wt = df[wt_col]
            df[bl_col] = np.where(wt.isna(), np.nan,
                         np.where(wt < -1, 1, 0))

        # Workout distance: force positive
        if wd_col in df.columns:
            df[wd_col] = df[wd_col].abs()

    # Workout percent rank (workoutpctrnk = rank/numothers)
    for i in range(1, 13):
        f_col = f"WorkoutNumOthDayDist{i}"
        d_col = f"WorkoutRankvsOth{i}"
        e_col = f"WorkoutPctRnk{i}"
        x_col = f"WorkoutPctRnk_gt4_{i}"

        if f_col in df.columns and d_col in df.columns:
            f = df[f_col]
            d = df[d_col]
            df[e_col] = np.where((f > 1) & d.notna(), d / f, np.nan)
            df[x_col] = np.where((f > 4) & d.notna(), d / f, np.nan)

    return df


# =============================================================================
# Block 3 — Days-off averages & mud/turf history
# =============================================================================

def _block3_days_off_mud_turf(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Average days off rolling windows (open-ended using SAS mean → skipna)
    days_cols = ["NumDaysSinceLastRace"] + [f"DaysSincePrevRace{i}" for i in range(1, 6)]
    existing  = [c for c in days_cols if c in df.columns]

    for n in range(2, 7):
        cols_n = existing[:n]
        col_out_oa = f"AverageDaysOffOA{n}"
        col_out    = f"AverageDaysOff{n}"

        # OA version: always compute mean over available (skipna)
        df[col_out_oa] = df[cols_n].mean(axis=1, skipna=True)

        # Strict version: all must be non-null
        all_valid = df[cols_n].notna().all(axis=1)
        df[col_out] = np.where(all_valid, df[cols_n].mean(axis=1), np.nan)

    # Mud counter (track condition != starts with F and not blank)
    for i in range(1, 11):
        tc_col  = f"TrackCondition{i}"
        md_col  = f"MudCounter{i}"
        if tc_col in df.columns:
            tc = df[tc_col].fillna("")
            df[md_col] = np.where(
                (tc != "") & (~tc.str.startswith("F")), 1, 0
            )

    mud_cols = [f"MudCounter{i}" for i in range(1, 11) if f"MudCounter{i}" in df.columns]
    if mud_cols:
        df["NumTimesInMud"] = df[mud_cols].sum(axis=1)

    # Past race distance: force positive
    for i in range(1, 11):
        col = f"DistanceInYards{i}"
        if col in df.columns:
            df[col] = df[col].fillna(0).abs()

    # Turf tendency (turf=+1, dirt=-1)
    for i in range(1, 11):
        s_col = f"Surface{i}"
        t_col = f"Turfy{i}"
        if s_col in df.columns:
            s = df[s_col].fillna("").str.upper()
            df[t_col] = np.where(s.isin(["D"]), -1,
                        np.where(s.isin(["T"]),  1, np.nan))

    # Sum turf tendency
    turf10 = [f"Turfy{i}" for i in range(1, 11) if f"Turfy{i}" in df.columns]
    turf5  = [f"Turfy{i}" for i in range(1, 6)  if f"Turfy{i}" in df.columns]
    df["TurfyLast10"] = np.where(df.get("Turfy1", pd.Series(np.nan)).notna(),
                                  df[turf10].sum(axis=1, skipna=True), 0)
    df["TurfyLast5"]  = np.where(df.get("Turfy1", pd.Series(np.nan)).notna(),
                                  df[turf5].sum(axis=1, skipna=True), 0)

    return df


# =============================================================================
# Block 4 — Pedigree ratings & weight change
# =============================================================================

def _block4_pedigree_weight(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def parse_ped_rating(series: pd.Series) -> pd.Series:
        """
        Strip trailing *, ?, X characters from pedigree rating strings,
        return numeric. Replicates SAS substr logic.
        """
        import re
        def _parse(val):
            if pd.isna(val) or val == '*':
                return np.nan
            val = str(val).strip()
            # Remove trailing non-numeric flags
            val = re.sub(r'[\*\?X]+$', '', val).strip()
            try:
                return float(val)
            except (ValueError, TypeError):
                return np.nan
        return series.apply(_parse)

    for col_raw, col_out in [
        ("BRISDirtPedRating", "BRIS_DtPRn"),
        ("BRISMudPedRating",  "BRIS_MdPRn"),
        ("BRISTurfPedRating", "BRIS_TfPRn"),
        ("BRISDistPedRating", "BRIS_DsPRn"),
    ]:
        if col_raw in df.columns:
            df[col_out] = parse_ped_rating(df[col_raw])

    ped_cols = ["BRIS_DtPRn", "BRIS_MdPRn", "BRIS_TfPRn", "BRIS_DsPRn"]
    existing = [c for c in ped_cols if c in df.columns]
    if len(existing) == 4:
        all_valid = df[existing].notna().all(axis=1)
        df["BRISTltPedRating"] = np.where(all_valid, df[existing].sum(axis=1),  np.nan)
        df["BRISAvePedRating"] = np.where(all_valid, df[existing].mean(axis=1), np.nan)

    # Weight change vs average of last 3 races
    w_cols = [f"Weight{i}" for i in range(1, 4) if f"Weight{i}" in df.columns]
    if "Weight" in df.columns and w_cols:
        avg_w = df[w_cols].mean(axis=1, skipna=True)
        df["WeightChange"] = np.where(df["Weight1"].notna(), df["Weight"] / avg_w, np.nan)

    return df


# =============================================================================
# Block 5 — Trainer category pivot (63 categories)
# =============================================================================

TRAINER_CAT_MAP = {
    "1-5 days away": 1,   "1st after clm": 2,   "1st at route": 3,
    "1st on grass": 4,    "1st strt w/trn": 5,  "1st Time Clmg": 6,
    "1st time lasix": 7,  "1st Time MdnClm": 8, "1st time str": 9,
    "1stTimeBlinkers": 10,"2nd after clm": 11,  "2nd career race": 12,
    "2nd grass race": 13, "2nd off layoff": 14,  "2nd Rte race": 15,
    "2nd strt w/trn": 16, "2nd time Lasix": 17,  "2YO": 18,
    "31-90daysAway": 19,  "3rd off layoff": 20,  "46-90daysAway": 21,
    "90+ days away": 22,  "Alarming drop": 23,   "All Weather": 24,
    "Allowance": 25,      "AW to Turf": 26,      "Blinkers off": 27,
    "Blnkr back on": 28,  "Btn favorite": 29,    "Claiming": 30,
    "Clm repeater": 31,   "Debut >= 1m": 32,     "Debut Mdn Clm": 33,
    "Debut MdnSpWt": 34,  "Debut vs Wnrs": 35,   "Dirt to AW": 36,
    "Dirt to turf": 37,   "Down 2+ classes": 38, "Down one class": 39,
    "Drops off claim": 40,"Drops off win": 41,   "Graded stakes": 42,
    "Maiden Clming": 43,  "Maiden Sp Wt": 44,    "Mdn to MdnClm": 45,
    "Mdn win L/R": 46,    "MdnClm to Mdn": 47,   "No class chg": 48,
    "NonGraded Stk": 49,  "Routes": 50,          "Rte to Sprint": 51,
    "Shipper": 52,        "ShipperToU.S.": 53,   "Sprint to Rte": 54,
    "Sprints": 55,        "Sprnt-Rte-Sprnt": 56, "Sprnt-Sprnt-Rte": 57,
    "Turf starts": 58,    "Turf to AW": 59,      "Turf to dirt": 60,
    "Up 2+ classes": 61,  "Up one class": 62,    "Wnr last race": 63,
}


def _block5_trainer_categories(df: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot 6 KeyTrnrStatCategory columns into 63 numbered tran_st/wpct/itm/roi cols.
    Replicates the %trainerdatafix SAS macro.
    """
    df = df.copy()

    # Initialize all 63 trainer stat columns
    for n in range(1, 64):
        for stat in ["tran_st", "tran_wpct", "tran_itm", "tran_roi"]:
            col = f"{stat}_{n}"
            if col not in df.columns:
                df[col] = np.nan

    # Pivot from the 6 key stat slots
    for i in range(1, 7):
        cat_col  = f"KeyTrnrStatCategory{i}"
        st_col   = f"KeyStatofStarts{i}"
        wp_col   = f"KeyStatWinpct{i}"
        itm_col  = f"KeyStatITMpct{i}"
        roi_col  = f"KeyStatDol2ROI{i}"

        if cat_col not in df.columns:
            continue

        cat_series = df[cat_col].fillna("")
        for cat_name, cat_num in TRAINER_CAT_MAP.items():
            mask = cat_series == cat_name
            if mask.any():
                if st_col  in df.columns: df.loc[mask, f"tran_st_{cat_num}"]   = df.loc[mask, st_col]
                if wp_col  in df.columns: df.loc[mask, f"tran_wpct_{cat_num}"] = df.loc[mask, wp_col]
                if itm_col in df.columns: df.loc[mask, f"tran_itm_{cat_num}"]  = df.loc[mask, itm_col]
                if roi_col in df.columns: df.loc[mask, f"tran_roi_{cat_num}"]  = df.loc[mask, roi_col]

    return df


# =============================================================================
# Block 6 — Speed slopes, horses beaten, indicator flags
# =============================================================================

def _block6_speed_slopes_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # ---- BRIS speed rating slope (weighted linear regression over last N races)
    for rating_prefix, slope_prefix in [
        ("BRISSpeedRating", "bracespslope"),
        ("DRFSpeedRating",  "dracespslope"),
    ]:
        cols5 = [f"{rating_prefix}{i}" for i in range(1, 6)]
        cols4 = [f"{rating_prefix}{i}" for i in range(1, 5)]
        cols3 = [f"{rating_prefix}{i}" for i in range(1, 4)]

        existing5 = [c for c in cols5 if c in df.columns]
        existing4 = [c for c in cols4 if c in df.columns]
        existing3 = [c for c in cols3 if c in df.columns]

        if len(existing5) == 5:
            valid5 = df[existing5].notna().all(axis=1)
            avg5   = df[existing5].mean(axis=1)
            slope5 = ((-2*(df[existing5[4]] - avg5)) +
                      (-1*(df[existing5[3]] - avg5)) +
                      ( 0*(df[existing5[2]] - avg5)) +
                      ( 1*(df[existing5[1]] - avg5)) +
                      ( 2*(df[existing5[0]] - avg5))) / 10
            df[f"{slope_prefix}5"] = np.where(valid5, slope5, 0)

        if len(existing4) == 4:
            valid4 = df[existing4].notna().all(axis=1)
            avg4   = df[existing4].mean(axis=1)
            slope4 = ((-1.5*(df[existing4[3]] - avg4)) +
                      (-0.5*(df[existing4[2]] - avg4)) +
                      ( 0.5*(df[existing4[1]] - avg4)) +
                      ( 1.5*(df[existing4[0]] - avg4))) / 5
            df[f"{slope_prefix}4"] = np.where(valid4, slope4, 0)

        if len(existing3) == 3:
            valid3 = df[existing3].notna().all(axis=1)
            avg3   = df[existing3].mean(axis=1)
            slope3 = ((-1*(df[existing3[2]] - avg3)) +
                      ( 0*(df[existing3[1]] - avg3)) +
                      ( 1*(df[existing3[0]] - avg3))) / 2
            df[f"{slope_prefix}3"] = np.where(valid3, slope3, 0)

        # Last 2 diff
        c1, c2 = f"{rating_prefix}1", f"{rating_prefix}2"
        if c1 in df.columns and c2 in df.columns:
            df[f"{slope_prefix[0]}{'r'}alast2diff" if "dra" in slope_prefix else "brislast2diff"] = \
                np.where(df[c1].notna() & df[c2].notna(), df[c1] - df[c2], 0)

    # Cleaner explicit last2diff
    if "BRISSpeedRating1" in df.columns and "BRISSpeedRating2" in df.columns:
        df["brislast2diff"] = np.where(
            df["BRISSpeedRating1"].notna() & df["BRISSpeedRating2"].notna(),
            df["BRISSpeedRating1"] - df["BRISSpeedRating2"], 0)
    if "DRFSpeedRating1" in df.columns and "DRFSpeedRating2" in df.columns:
        df["drflast2diff"] = np.where(
            df["DRFSpeedRating1"].notna() & df["DRFSpeedRating2"].notna(),
            df["DRFSpeedRating1"] - df["DRFSpeedRating2"], 0)

    # Horses beaten (L3, L4)
    for n, label in [(4, "L4"), (3, "L3")]:
        # Resolve names case-insensitively: the DRF schema uses
        # 'Numofentrants#' / 'Finishposition#', not the 'NumOfEntrants#' /
        # 'FinishPosition#' spelling this loop assumed — so HorsesBeatenL4/L3
        # (and hence xHBL4 -> xHBL4c) were never built.
        # SAS: horsesbeatenL4 = sum(numofentrants1-4) - sum(finishposition1-4).
        _lc = {c.lower(): c for c in df.columns}
        ent_cols = [_lc.get(f"numofentrants{i}") for i in range(1, n+1)]
        fin_cols = [_lc.get(f"finishposition{i}") for i in range(1, n+1)]
        fp_last  = _lc.get(f"finishposition{n}")
        if fp_last and all(ent_cols) and all(fin_cols):
            valid = df[fp_last].notna()
            df[f"HorsesBeaten{label}"] = np.where(
                valid,
                df[ent_cols].sum(axis=1) - df[fin_cols].sum(axis=1),
                np.nan)

    # Indicator flags (1 if not missing, 0 if missing)
    for col, ind_col in [
        ("BRISPrimePowerRating",       "BrisPPR_ind"),
        ("AuctionPrice",               "auction_ind"),
        ("BRISSpeedAllWeather",        "BRISSpeedAW_ind"),
        ("BestBRISSpeedTodaysTrack",   "BRISSpeedTT_ind"),
        ("BestBRISSpdDist",            "BRISSpeedD_ind"),
        ("BestBRISSpdOffTrack",        "BestBRISSpdOff_ind"),
    ]:
        if col in df.columns:
            if col == "AuctionPrice":
                df[ind_col] = ((df[col].notna()) & (df[col] != 0)).astype(int)
            else:
                df[ind_col] = df[col].notna().astype(int)

    # Trainer win pct indicators
    for n in [9, 12, 18, 34, 43, 44]:
        col = f"tran_wpct_{n}"
        ind = f"tran_wpct_{n}ind"
        if col in df.columns:
            df[ind] = df[col].notna().astype(int)

    # r101109 = (r10 + r11) / r9  (jockey-trainer win combo)
    for col in ["R9", "R10", "R11"]:
        if col not in df.columns:
            df[col] = np.nan

    valid_r9 = df["R9"].notna() & (df["R9"] != 0)
    df["r101109"] = np.where(valid_r9, (df["R10"].fillna(0) + df["R11"].fillna(0)) / df["R9"], np.nan)
    df["r101109gt10"] = np.where(valid_r9 & (df["R9"] >= 10), df["r101109"], np.nan)
    df["r101109_ind"]     = df["r101109"].notna().astype(int)
    df["r101109gt10_ind"] = df["r101109gt10"].notna().astype(int)
    df["r101109"]     = df["r101109"].fillna(0)
    df["r101109gt10"] = df["r101109gt10"].fillna(0)

    return df


# =============================================================================
# Block 7 — Race-level aggregates (mirrors PROC SQL group-by in SAS)
# =============================================================================

def _block7_race_aggregates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    group_cols = ["Track", "Date", "Race"]

    agg_specs = {}

    # Speed slope averages
    for col in ["bracespslope5", "bracespslope4", "bracespslope3", "brislast2diff",
                "dracespslope5", "dracespslope4", "dracespslope3", "drflast2diff",
                "HorsesBeatenL4", "HorsesBeatenL3", "QuirinstyleSpeedPoints"]:
        if col in df.columns:
            short = {"bracespslope5": "btlt5", "bracespslope4": "btlt4",
                     "bracespslope3": "btlt3", "brislast2diff": "btlt2",
                     "dracespslope5": "dtlt5", "dracespslope4": "dtlt4",
                     "dracespslope3": "dtlt3", "drflast2diff": "dtlt2",
                     "HorsesBeatenL4": "HBL4", "HorsesBeatenL3": "HBL3",
                     "QuirinstyleSpeedPoints": "QSP_avg"}.get(col, col)
            agg_specs[col] = ("mean", short)

    # Indicator sums
    for col, alias in [
        ("BrisPPR_ind",         "brisPPR_indr"),
        ("auction_ind",         "auction_indr"),
        ("tran_wpct_9ind",      "tran_wpct_9indr"),
        ("tran_wpct_18ind",     "tran_wpct_18indr"),
        ("tran_wpct_34ind",     "tran_wpct_34indr"),
        ("tran_wpct_12ind",     "tran_wpct_12indr"),
        ("tran_wpct_43ind",     "tran_wpct_43indr"),
        ("tran_wpct_44ind",     "tran_wpct_44indr"),
        ("r101109gt10_ind",     "r101109gt10_indr"),
        ("r101109_ind",         "r101109_indr"),
        ("BRISSpeedAW_ind",     "BRISSpeedAW_indr"),
        ("BRISSpeedTT_ind",     "BRISSpeedTT_indr"),
        ("BRISSpeedD_ind",      "BRISSpeedD_indr"),
        ("BestBRISSpdOff_ind",  "BestBRISSpdOff_indr"),
    ]:
        if col in df.columns:
            agg_specs[col] = ("sum", alias)

    for col, alias in [
        ("r101109",      "r101109_avg"),
        ("r101109gt10",  "r101109gt10_avg"),
    ]:
        if col in df.columns:
            agg_specs[col] = ("mean", alias)

    # R308 sum (trainer-jockey combined starts)
    if "R308" in df.columns:
        agg_specs["R308"] = ("sum", "tlttrnstrts4race")

    # NYBred average
    if "NYBred" in df.columns:
        agg_specs["NYBred"] = ("mean", "NYBred_avg")

    # Build aggregation
    if agg_specs:
        agg_dict = {col: func for col, (func, _) in agg_specs.items()}
        rename_dict = {col: alias for col, (_, alias) in agg_specs.items()}
        temp = df.groupby(group_cols).agg(agg_dict).reset_index()
        temp = temp.rename(columns=rename_dict)
        df = df.merge(temp, on=group_cols, how="left", suffixes=("", "_race_agg"))

    # Race-centered deviations (horse - race average)
    for raw, avg, out in [
        ("HorsesBeatenL4",             "HBL4",    "xHBL4"),
        ("HorsesBeatenL3",             "HBL3",    "xHBL3"),
        ("bracespslope5",              "btlt5",   "xbspdslp5"),
        ("bracespslope4",              "btlt4",   "xbspdslp4"),
        ("bracespslope3",              "btlt3",   "xbspdslp3"),
        ("brislast2diff",              "btlt2",   "xbspdslp2"),
        ("dracespslope5",              "dtlt5",   "xdspdslp5"),
        ("dracespslope4",              "dtlt4",   "xdspdslp4"),
        ("dracespslope3",              "dtlt3",   "xdspdslp3"),
        ("drflast2diff",               "dtlt2",   "xdspdslp2"),
        ("QuirinstyleSpeedPoints",     "QSP_avg", "xQSP_2025"),
    ]:
        r_col = avg    # race average
        if raw in df.columns and r_col in df.columns:
            if out == "xQSP_2025":
                df[out] = np.where(
                    df[r_col].isna() | df[raw].isna(), np.nan,
                    df[raw] - df[r_col])
            else:
                df[out] = np.where(df[r_col].notna(), df[raw] - df[r_col], np.nan)

    # NYBred centered
    if "NYBred" in df.columns and "NYBred_avg" in df.columns:
        df["xNYBred"] = df["NYBred"] - df["NYBred_avg"]

    # Standardization (PROC MEANS std → merge back)
    df = _compute_stds(df)

    return df


def _compute_stds(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute track-level standard deviations for select variables,
    replicating PROC MEANS std by track/date in SAS.
    """
    std_groups = {
        "all": {
            "group": ["Track", "Date"],
            "vars": {
                "xJockeyWinsCurrentMeet": "xjwins_std",
                "MTRAINERCURMTWPCT":      "mtrncmwpct_std",
                "xR310":                   "xR310_std",
                "xR309":                   "xR309_std",
                "xTrainerWinsCurrentMeet": "xTrainerWinsCurrentMeet_std",
                "xEPS_LTCyr":             "xeps_ltcyr_std",
                "xEPS_LTPyr":             "xeps_ltpyr_std",
            }
        },
        "dirt": {
            "group": ["Track", "Date"],
            "filter": lambda d: d["Surface"].str.upper().isin(["D"]) & ~d["RaceType"].isin(["M", "S"]),
            "vars": {
                "xTrainerWinsCurrentMeet": "xTrainerWinsCurrentMeet_std_DIRT",
                "xJockeyWinsCurrentMeet":  "xjwins_std_DIRT",
            }
        }
    }

    for grp_name, spec in std_groups.items():
        subset = df
        if "filter" in spec:
            try:
                subset = df[spec["filter"](df)]
            except Exception:
                continue

        for raw_col, std_col in spec["vars"].items():
            if raw_col not in df.columns:
                continue
            std_vals = subset.groupby(spec["group"])[raw_col].std().reset_index()
            std_vals = std_vals.rename(columns={raw_col: std_col})
            df = df.merge(std_vals, on=spec["group"], how="left", suffixes=("", f"_{grp_name}_dup"))

    return df


# =============================================================================
# Block 8 — Transforms, clipping, polynomial terms, model-specific inputs
# =============================================================================

def _block8_transforms(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Compute field size per race (NumOfEntries / HorsesRan not in raw DRF — derived here)
    race_counts = df.groupby(["Date", "Race"]).size().reset_index(name="NumOfEntries")
    df = df.merge(race_counts, on=["Date", "Race"], how="left")
    df["HorsesRan"] = df["NumOfEntries"]   # same pre-scratch; scratches.py reduces it

    # Base probabilities
    df["baseprob1"] = 1 / df["NumOfEntries"].replace(0, np.nan)
    df["baseprob2"] = 1 / df["HorsesRan"].replace(0, np.nan)

    # Jockey totals
    for cy_py, cols, out in [
        ("py", ["iJockeyPrvYrWins", "iJockeyPrvYrPlac", "iJockeyPrvYrShow"], "iTotjockey"),
        ("cy", ["iJockeyCurYrWins", "iJockeyCurYrPlac", "iJockeyCurYrShow"], "iTotjockey_cy"),
    ]:
        existing = [c for c in cols if c in df.columns]
        if existing:
            df[out] = df[existing].sum(axis=1, skipna=True)

    # Workout composite
    wopct_cols = [f"iWorkoutPctRnk{i}" for i in range(1, 4) if f"iWorkoutPctRnk{i}" in df.columns]
    if wopct_cols:
        all_missing = df[wopct_cols].isna().all(axis=1)
        df["iWorkout"] = np.where(all_missing, np.nan, df[wopct_cols].mean(axis=1, skipna=True))

    for n, out in [(3, "iWorkoutBullets"), (2, "iWorkoutBullets2")]:
        bullet_cols = [f"iWorkoutTime{i}Bullet" for i in range(1, n+1) if f"iWorkoutTime{i}Bullet" in df.columns]
        if bullet_cols:
            all_missing = df[bullet_cols].isna().all(axis=1)
            df[out] = np.where(all_missing, np.nan, df[bullet_cols].sum(axis=1, skipna=True))

    # Purse composite (mean last 3)
    purse_cols = [f"iPurse{i}" for i in range(1, 4) if f"iPurse{i}" in df.columns]
    if purse_cols:
        all_missing = df[purse_cols].isna().all(axis=1)
        df["iPurse13"] = np.where(all_missing, np.nan, df[purse_cols].mean(axis=1, skipna=True))

    # BRIS Prime Power Rating transforms
    if "iBRISPrimePowerRating" in df.columns:
        ibppr = df["iBRISPrimePowerRating"].fillna(1)
        df["IBPPR_c"] = ibppr.clip(0.88, 1.10)
        df["iBPPR_2slp"] = np.where(
            ibppr < 0.983,
            (0.3898 * df["IBPPR_c"]) - 0.4676,
            (3.266  * df["IBPPR_c"]) - 3.2932)

    # ---- Categorical binning functions (lifted directly from SAS) ----

    df = _apply_categorical_cuts(df)
    df = _apply_clipping(df)
    df = _apply_polynomial_terms(df)
    df = _apply_model_specific_vars(df)

    return df


def _apply_categorical_cuts(df: pd.DataFrame) -> pd.DataFrame:
    """Categorical binning — mirrors the IF/ELSE chains in SAS lines 640–900."""
    df = df.copy()

    # xLTWpct_cat
    x = df.get("xLTrecTodaytrackWpct", pd.Series(np.nan, index=df.index))
    df["xLTWpct_cat"] = np.where(x.isna(), 0,
                        np.where(x < -0.21, -0.05,
                        np.where(x < 0,     -0.01,
                        np.where(x < 0.38,   0.02, 0.08))))

    df["xLTWpct_cat2"] = np.where(x.isna(), 2,
                         np.where(x < -0.21, 1,
                         np.where(x < 0,     2,
                         np.where(x < 0.38,  3, 4))))

    # xTrnCMWP_cat
    t = df.get("xTrainerCurMtWPpct", pd.Series(np.nan, index=df.index))
    df["xTrnCMWP_cat"] = np.where(t.isna(), -0.014,
                         np.where(t < -0.07, -0.033,
                         np.where(t < 0.15,   0.026, 0.067)))

    # LTAWWPSpctcat
    aw = df.get("ILTAWWPSpct", pd.Series(np.nan, index=df.index))
    df["LTAWWPSpctcat"] = np.where(aw.isna(), 0.01,
                          np.where(aw < 0.18,  -0.044,
                          np.where(aw < 1.65,  -0.005, 0.062)))

    df["awcat"] = np.where(aw <= 0.84, 1, np.where(aw > 1.4, 3, 2))

    # ijckypywpctcat
    jp = df.get("IJockeyPrvYrWpct", pd.Series(np.nan, index=df.index))
    df["ijckypywpctcat"] = np.where(
        jp.isna() | ((jp >= 0) & (jp < 0.894)), -0.0256,
        np.where((jp >= 0.894) & (jp < 1.0348), -0.008,
        np.where((jp >= 1.0348) & (jp < 1.3),    0.018, 0.047)))

    # QSPcat
    qsp = df.get("IQuirinstyleSpeedPoints", pd.Series(np.nan, index=df.index))
    df["QSPcat"] = np.where(qsp < 0.19512, -0.031,
                   np.where(qsp < 1.6,      0,     0.026))

    # brisAW_c
    baw = df.get("xBRISSpeedAllWeather", pd.Series(np.nan, index=df.index))
    df["brisAW_c"] = np.where(baw.isna(), 0, np.where(baw < 3, -0.015, 0.03))

    return df


def _apply_clipping(df: pd.DataFrame) -> pd.DataFrame:
    """Clip variables to model-safe ranges — mirrors the min/max guards in SAS."""
    df = df.copy()

    clip_specs = [
        ("xBRISPrimePowerRating", "xBRISPda",  -10,    10,   0),
        ("xBRISPrimePowerRating", "xBRISPd",   -13,    13,   0),
        ("xJKYatDisJkyonTurfEPS", "xjckyepsc", -5000, 5000, None),  # /1000 below
        ("xTrainerCurMtWPpct",   "trncurmt",  -0.20,  0.20,  0),
        ("xLTturfRecWPSpct",     "xLTturfRecWPSpctc", -0.35, 0.35, None),
        ("xLTrecTodaytrackWpct", "xLTrecTodaytrackWpctc", -0.35, 0.35, None),
        ("xLTAWWpct",            "xLTAWWpctc", -0.35, 0.35, None),
        ("xBestBRISSpeedTodaysTrack", "xBestBRISSatTr", -8, 8, None),
        ("xBRISSpeedAllWeather",      "xBRISSpeedAWc",  -8, 8, None),
    ]

    for src, dst, lo, hi, fill in clip_specs:
        if src in df.columns:
            val = df[src]
            if fill is not None:
                val = val.fillna(fill)
            df[dst] = val.clip(lo, hi)

    # Scale xjckyepsc to /1000
    if "xjckyepsc" in df.columns:
        df["xjckyepsc"] = df["xjckyepsc"] / 1000
        df.loc[df["xJKYatDisJkyonTurfEPS"].isna(), "xjckyepsc"] = -5

    # Trainer dist/AW clipped combos
    for col, out, lo, hi in [
        ("xtran_wpct_50", "xtran_wpct_50c", -13, 13),
        ("xtran_wpct_55", "xtran_wpct_55c", -13, 13),
        ("xtran_wpct_24", "xtran_wpct_24c", -13, 13),
    ]:
        if col in df.columns:
            df[out] = np.where(df[col].notna(), df[col].clip(lo, hi), np.nan)

    # trndistaw, trndistexp composites
    td_cols = [c for c in ["xtran_wpct_50c", "xtran_wpct_55c", "xtran_wpct_24c"] if c in df.columns]
    if len(td_cols) == 3:
        all_miss = df[td_cols].isna().all(axis=1)
        df["trndistaw"] = pd.Series(
            np.where(all_miss, np.nan, df[td_cols].mean(axis=1, skipna=True)),
            index=df.index).fillna(0)

    if "xtran_wpct_50c" in df.columns and "xtran_wpct_55c" in df.columns:
        two = df[["xtran_wpct_50c", "xtran_wpct_55c"]]
        df["trndistexp"] = np.where(two.isna().all(axis=1), np.nan, two.mean(axis=1, skipna=True))
        df["trndistexp"] = df["trndistexp"].fillna(0).clip(-6, 10)
        df["trndistexp_ind"] = (df["trndistexp"] == 10).astype(int)

    # KS win pct
    if "xKS_winpct" in df.columns:
        df["xKS_winpcta"] = df["xKS_winpct"].fillna(0).clip(-0.1, 0.1)
    if "xks_w_winpct" in df.columns:
        df["xks_w_winpcta"] = df["xks_w_winpct"].fillna(0).clip(-0.15, 0.15)
        df["kswpct_100"] = df["xks_w_winpcta"] * 100

    return df


def _apply_polynomial_terms(df: pd.DataFrame) -> pd.DataFrame:
    """Polynomial/log/exp transformations on clipped variables."""
    df = df.copy()

    if "xBRISPda" in df.columns:
        df["xBRISPd2a"] = (df["xBRISPda"] + 12) ** 2

    if "xBRISPd" in df.columns:
        df["xBRISPd2"]  = (df["xBRISPd"] + 14) ** 2
        df["xBRISPd3"]  = (df["xBRISPd"] + 14) ** 3
        df["xBRISPd4"]  = np.log((df["xBRISPd"] + 14).clip(lower=0.001))
        df["xBRISPd5"]  = np.exp(df["xBRISPd"] + 14)
        df["xBRISPd6"]  = (df["xBRISPd"] + 14) ** 2.5
        df["xBRISPd100"]= df["xBRISPd"] + 100

    if "kswpct_100" in df.columns and "xtran_wpct_24c" in df.columns:
        cols = [c for c in ["kswpct_100", "xtran_wpct_24c", "xtran_wpct_50c", "xtran_wpct_55c"] if c in df.columns]
        df["kstrnvardisaw"]  = df[cols].mean(axis=1, skipna=True)
        df["kstrnvardisaw2"] = (df["kstrnvardisaw"] + 16) ** 2
        df["kstrnvardisaw3"] = (df["kstrnvardisaw"] + 16) ** 3

    # Workout date clipped
    if "xWorkoutDate5" in df.columns:
        df["xwrkdate"] = df["xWorkoutDate5"].fillna(0).clip(-60, 60)

    # EPS current year clipped → polynomial
    if "xEPS_LTCyr" in df.columns:
        df["xepsct12"] = df["xEPS_LTCyr"].fillna(4000).clip(-10000, 10000)
        df["xepsct12a"] = ((df["xepsct12"] / 1000) + 11) ** 2

    # BRIS Prime Power Rating → maiden polynomial
    if "xBRISPrimePowerRating" in df.columns:
        xbpr = df["xBRISPrimePowerRating"]
        df["tempcutm"]  = xbpr.fillna(0).clip(-6, 12)
        df["xBRISPdmg4"] = (df["tempcutm"] + 7) ** 2
        df.loc[df.get("brisPPR_indr", pd.Series(0, index=df.index)) < 4, "xBRISPdmg4"] = 49
        df["xBRISPdmg4"] = df["xBRISPdmg4"] / 100

    # Jockey standardized current meet wins
    if "xJockeyWinsCurrentMeet" in df.columns and "xjwins_std" in df.columns:
        df["xJockeyWinsCM_std"] = np.where(
            df["xjwins_std"] != 0,
            (df["xJockeyWinsCurrentMeet"] / df["xjwins_std"]).clip(-1.5, 2),
            0)

    return df


def _apply_model_specific_vars(df: pd.DataFrame) -> pd.DataFrame:
    """
    Model-specific feature creation for KEE Turf, KEE Maiden, KEE Dirt.
    Mirrors the 'VARIABLE CREATION' sections in SAS lines 1000–2412.
    These are the direct inputs to PROC SCORE.
    """
    df = df.copy()

    surf = df.get("Surface", pd.Series("", index=df.index)).str.upper().fillna("")
    rt   = df.get("RaceType", pd.Series("", index=df.index)).fillna("")

    # ---- ITM performance composite (AW/Turf/TodaysTrack) ----
    if all(c in df.columns for c in ["xLTturfRecWPSpctc", "xLTrecTodaytrackWpctc", "xLTAWWpctc"]):
        x2lt = df["xLTrecTodaytrackWpctc"] * 2
        df["ITMperfaw"] = df[["xLTturfRecWPSpctc", "xLTAWWpctc"]].join(x2lt.rename("x2lt")).mean(axis=1, skipna=True)

    # ---- Bristrkaw (best BRIS speed at today's track or AW) ----
    for col, out in [("xBestBRISSpeedTodaysTrack", "xBestBRISSatTr"), ("xBRISSpeedAllWeather", "xBRISSpeedAWc")]:
        if col in df.columns:
            df[f"x2{out}"] = df.get(out, df[col]).apply(lambda x: x * 2 if pd.notna(x) else np.nan)

    # ---- xbris_maid (BRIS PPR categorical for maiden) ----
    if "xBRISPrimePowerRating" in df.columns:
        xb = df["xBRISPrimePowerRating"]
        df["xbris_maid"] = np.where(xb.isna(), 0,
                           np.where(xb < -1.27,  -0.10,
                           np.where(xb < 2.33,   -0.025,
                           np.where(xb < 8.19,    0.10,  0.29))))

    # ---- EPS categorical (lifetime) ----
    if "ieps_lt" in df.columns:
        eps = df["ieps_lt"]
        df["eps_ct"] = np.where(eps < 0.98, -0.02,
                       np.where(eps >= 1.76, 0.10, 0))

    # ---- Maiden trainer stat indicators ----
    if "RaceType" in df.columns:
        mask_m = rt == "M"
        mask_s = rt == "S"
        if "xtran_st_43" in df.columns:
            df["trainmaid"] = np.nan
            df.loc[mask_m & (df["xtran_st_43"] < -44.41),                              "trainmaid"] = -0.044
            df.loc[mask_m & (df["xtran_st_43"] >= -44.41) & (df["xtran_st_43"] < 57.18), "trainmaid"] = -0.010
            df.loc[mask_m & (df["xtran_st_43"].isna() | (df["xtran_st_43"] >= 57.18)), "trainmaid"] =  0.040

    # ---- JCKchngName fill (from scratches.py merge) ----
    if "JCKchngName" not in df.columns:
        df["JCKchngName"] = np.nan

    # ---- Maiden bullet caps ----
    for i in range(1, 4):
        src = f"iWorkoutTime{i}Bullet"
        dst = f"iWorkoutTime{i}Bulleta"
        if src in df.columns:
            df[dst] = df[src].clip(upper=4)

    bullet_a_cols = [f"iWorkoutTime{i}Bulleta" for i in range(1, 4) if f"iWorkoutTime{i}Bulleta" in df.columns]
    if bullet_a_cols:
        all_missing = df[bullet_a_cols].isna().all(axis=1)
        df["iWorkoutBulletsa"] = np.where(all_missing, np.nan, df[bullet_a_cols].sum(axis=1, skipna=True))
        df["maidbullets"] = np.where(
            df["iWorkoutBulletsa"].isna() | (df["iWorkoutBulletsa"] < 1), -0.025,
            np.where(df["iWorkoutBulletsa"] < 5, -0.005, 0.06))

    # ---- Workout time per furlong clips ----
    for i in range(1, 6):
        src = f"xwotimeperfrlg{i}"
        dst = f"xwotimeperfrlg{i}c"
        if src in df.columns:
            df[dst] = df[src].fillna(0).clip(-0.6, 0.6)

    wot_cols = [f"xwotimeperfrlg{i}c" for i in range(1, 6) if f"xwotimeperfrlg{i}c" in df.columns]
    if wot_cols:
        df["wotimefrlg_keeom"] = df[wot_cols].sum(axis=1, skipna=True)

    # ---- Post position categories ----
    if "xPostPosition" in df.columns:
        pp = df["xPostPosition"]
        df["PP_T"] = np.where(pp < -3, 0.024, np.where(pp >= 4, -0.044, 0))

    # ---- R309 / R310 / R308 clips ----
    for col, out, lo, hi in [
        ("xR309", "xR309c", -2, 5),
        ("xR310", "xR310c", -2, 5),
        ("xR308", "xR308c", -2, 5),
    ]:
        if col in df.columns:
            df[out] = np.where(df[col].notna(), df[col].clip(lo, hi), np.nan)

    # ---- Jockey-trainer maiden combo (jckytrainmaid12) ----
    if all(c in df.columns for c in ["tlttrnstrts4race", "xR309c", "xR310c", "xR308c"]):
        df["jckytrainmaid12"] = np.where(
            (df["tlttrnstrts4race"] >= 9) & df["xR309c"].notna() & df["xR310c"].notna(),
            df["xR309c"] + df["xR310c"],
            df["xR308c"])
        df["jckytrainmaid12"] = df["jckytrainmaid12"].fillna(0)

    # ---- xbris_keeapraw13 / KEE AW Oct 2012 model vars ----
    if "xBRISPrimePowerRating" in df.columns:
        xb = df["xBRISPrimePowerRating"]
        df["xBRISPd_keeod"]  = xb.fillna(0).clip(-13, 13)
        df["xBRISPd2_keeod"] = (df["xBRISPd_keeod"] + 14) ** 2

    # ---- KEE Turf 2012 model vars ----
    if "xBRISPrimePowerRating" in df.columns:
        xb = df["xBRISPrimePowerRating"]
        df["xbris_keeot"]  = xb.fillna(0).clip(-20, 20)
        df["xbris2_keeot"] = (df["xbris_keeot"] + 21) ** 2

    if "xEPS_LTCyr" in df.columns:
        df["xepsct_keeot"]  = df["xEPS_LTCyr"].fillna(4000).clip(-10000, 10000)
        df["xepsct_keeota"] = ((df["xepsct_keeot"] / 1000) + 11) ** 2

    # ---- KEE Apr 2012 Turf 12 model ----
    if "xBRISPrimePowerRating" in df.columns:
        xb12 = df["xBRISPrimePowerRating"].fillna(0)
        df["xbrist12"]     = xb12
        df["XBPPR_tc12"]   = xb12.clip(-20, 20)
        df["XBPPR_tc12_3"] = (df["XBPPR_tc12"] + 21) ** 2

    # ---- Auction price transforms ----
    if "xAuctionPrice" in df.columns:
        df["xauctpcm"] = np.where(
            (rt == "S") & (df.get("auction_indr", 0) >= 3),
            df["xAuctionPrice"].fillna(0).clip(-10000, 100000),
            0) 
        df["xauctpcm"] = df["xauctpcm"] / 10000

    # ---- Trainer top 2 stat composite ----
    for out, ks1, ks2, ks3, lo, hi in [
        ("trnrtop2stat_keeom", "KeyStatofStarts1", "KeyStatofStarts2", None, 0.75, 3.25),
        ("trnrtop2stat_dmrd",  "KeyStatofStarts1", "KeyStatofStarts2", None, 0.75, 3.25),
    ]:
        if all(c in df.columns for c in [ks1, ks2, "IKeyStatWinpct1", "IKeyStatWinpct2"]):
            cond_both = (df[ks1] >= 10) & (df[ks2] >= 10)
            cond_ks2  = (df[ks2] >= 10) & ~cond_both
            cond_ks1  = (df[ks1] >= 10) & ~cond_both & ~cond_ks2
            df[out] = np.where(cond_both,
                         df["IKeyStatWinpct1"] + df["IKeyStatWinpct2"],
                     np.where(cond_ks2, df["IKeyStatWinpct2"],
                     np.where(cond_ks1, df["IKeyStatWinpct1"], 1.0)))
            df[out] = df[out].clip(lo, hi)

    logger.info("  Block 8 transforms complete")
    return df
