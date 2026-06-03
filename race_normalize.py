"""
DTS Pipeline — race_normalize.py
====================================
Translates the PROC SQL + DATA step race normalization from 5scoring.sas.

For every variable in the 1480-variable list, computes:
  - {col}_ave  = race-level mean (group by Track, Date, Race)
  - I{col}     = col / col_ave  (index ratio, default 1 if missing/zero avg)
  - x{col}     = col - col_ave  (residual, NaN if either missing)

Also computes prerequisite derived variables that must exist before
the race averages can be computed (EPS ratios, WPS pcts, age, HC flags, etc.)

This is the translation of %ryan5() from 5scoring.sas lines 1–3720.
"""

import numpy as np
import pandas as pd
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Group key for race-level aggregation
RACE_GROUP = ["Track", "Date", "Race"]


def _get_str_col(df: pd.DataFrame, col: str) -> pd.Series:
    """
    Return df[col] as a guaranteed string-dtype Series suitable for the
    ``.str`` accessor. Handles three failure modes that pandas 2.x has
    made stricter:

      1. Column missing entirely  -> all-empty-string series
      2. Column present but float dtype (NaN-only PP slots) -> coerced
      3. Column with mixed/object dtype where some elements aren't str

    Always returns a Series indexed like df. Use this anywhere we need
    to call ``.str.strip()`` or similar on a possibly-numeric column.
    """
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype="string")
    s = df[col]
    # Cast to nullable string dtype, replacing NaN/NaT with "".
    # `.astype("string")` handles object, float, int, datetime, etc.
    return s.astype("string").fillna("")


# =============================================================================
# Main entry point
# =============================================================================

def compute_race_normalizations(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build all race-level averages, I-prefix ratio indices, and x-prefix residuals.

    Parameters
    ----------
    df : DataFrame after ingest_drf.load_drf() — raw columns only

    Returns
    -------
    df with all _ave, I*, and x* columns added
    """
    logger.info("Computing race normalizations (5scoring.sas %ryan5)...")

    # Step 1 — prerequisite computed variables (5scoring.sas lines 1–680)
    df = _compute_prerequisites(df)

    # Step 2 — race-level means for all normalizable columns
    df = _compute_race_averages(df)

    # Step 3 — I-prefix (ratio) and x-prefix (residual) variables
    df = _compute_ix_variables(df)

    # Step 4 — median-based variables (mLastWOatTT etc.)
    df = _compute_median_variables(df)

    logger.info(f"  Race normalization complete. Columns: {len(df.columns)}")
    return df


# =============================================================================
# Step 1 — Prerequisite computed variables (before race averaging)
# =============================================================================

def _compute_prerequisites(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute derived variables needed before race averaging.
    Translates 5scoring.sas DATA step temp2 (lines 1–680).
    """
    df = df.copy()

    # --- Apprentice weight allowance fill ---
    df["Apprenticewgtallowifany"] = df.get("Apprenticewgtallowifany",
                                            pd.Series(0, index=df.index)).fillna(0)
    for i in range(1, 11):
        col = f"ApprenticeWtallow{i}"
        if col in df.columns:
            df[col] = df[col].fillna(0)

    # --- averagedaysoff3..6: require ALL fields non-missing (SAS AND condition) ---
    # SAS: if NDSLR NE . AND dayssince1 AND ... AND dayssince{N-1}
    ndslr = df.get("NumDaysSinceLastRace", pd.Series(np.nan, index=df.index))
    for n in range(3, 7):
        prevrace_cols = [f"Dayssinceprevrace{i}" for i in range(1, n)
                         if f"Dayssinceprevrace{i}" in df.columns]
        if len(prevrace_cols) < n - 1:
            df[f"averagedaysoff{n}"] = np.nan
            continue
        all_series = [ndslr] + [df[c] for c in prevrace_cols]
        combined   = pd.concat(all_series, axis=1)
        all_valid  = combined.notna().all(axis=1)
        df[f"averagedaysoff{n}"] = np.where(all_valid, combined.mean(axis=1), np.nan)

    # --- Bred flags ---
    sc = df.get("StateCountryabrvw", pd.Series("", index=df.index)).fillna("")
    df["NYBred"] = sc.str[:2].eq("NY").astype(int)
    df["KYBred"] = sc.str[:2].eq("KY").astype(int)
    df["FLBred"] = sc.str[:2].eq("FL").astype(int)

    # --- Sold at track flag ---
    wa = df.get("WheresoldatAuction", pd.Series("", index=df.index)).fillna("")
    tk = df.get("Track", pd.Series("", index=df.index)).fillna("")
    df["SoldatTrack"] = (wa.str[:3] == tk).astype(int)

    # --- Last workout at today's track ---
    wt1 = df.get("WorkoutTrack1", pd.Series("", index=df.index)).fillna("")
    df["LastWOatTT"] = (wt1.str[:3] == tk).astype(int)

    # --- Same track races (count of past races at today's track) ---
    same = pd.Series(0, index=df.index)
    for i in range(1, 11):
        tc = df.get(f"TrackCode{i}", pd.Series("", index=df.index)).fillna("")
        same += (tc == tk).astype(int)
    df["SameTrackraces"] = same

    # --- Trainer change indicators ---
    trainer0 = df.get("TodaysTrainer", pd.Series("", index=df.index)).fillna("")
    df["trainerchangelast6"] = 0
    df["trainerchangelast3"] = 0
    for i in range(1, 7):
        col = f"Trainer{i}"
        if col in df.columns:
            tr = df[col].fillna("")
            changed = (tr != "") & (tr != trainer0)
            df["trainerchangelast6"] = np.where(changed, 1, df["trainerchangelast6"])
            if i <= 3:
                df["trainerchangelast3"] = np.where(changed, 1, df["trainerchangelast3"])

    # --- Claimed in race indicators ---
    for i in range(1, 11):
        cc = df.get(f"ClaimedCode{i}", pd.Series("", index=df.index)).fillna("")
        df[f"claimedinrace{i}"] = (cc == "c").astype(int)
    df["claimedinlast3"] = df[[f"claimedinrace{i}" for i in range(1, 4)]].sum(axis=1)
    df["claimedinlast6"] = df[[f"claimedinrace{i}" for i in range(1, 7)]].sum(axis=1)

    # --- Taken off turf ---
    for i in range(1, 11):
        dc = df.get(f"Code{i}", pd.Series("", index=df.index)).fillna("")
        df[f"takenoffturf{i}"] = (dc == "X").astype(int)
    df["tlttakenoffturfl10"] = df[[f"takenoffturf{i}" for i in range(1, 11)]].sum(axis=1)

    # --- Sold at auction flag ---
    auc = df.get("AuctionPrice", pd.Series(np.nan, index=df.index))
    df["soldatauction"] = auc.notna().astype(int)

    # --- Age in months ---
    yob = pd.to_numeric(df.get("YearofBirth", pd.Series(np.nan, index=df.index)), errors="coerce")
    fm  = pd.to_numeric(df.get("HorsesFoalingMonth", pd.Series(np.nan, index=df.index)), errors="coerce")
    birthyear = np.where(yob > 90, 1900 + yob, 2000 + yob)
    df["birthyear"] = birthyear
    df["Birthday"]  = pd.to_datetime({
        "year":  pd.Series(birthyear, index=df.index).fillna(2000).astype(int),
        "month": fm.fillna(1).astype(int),
        "day":   1,
    }, errors="coerce")
    df["Months_old"] = np.floor((df["Date"] - df["Birthday"]).dt.days / 30 + 0.5).astype(float)

    # --- Zero-to-missing corrections for speed ratings ---
    zero_to_nan = [
        "BRISSpeedAllWeather", "BestBRISSpdDist", "BestBRISSpdFastTrack",
        "BestBRISSpdOffTrack", "BestBRISSpdTurf", "BestBRISSpeed2ndMostRece",
        "BestBRISSpeedLife", "BestBRISSpeedMostRecentY", "BestBRISSpeedTodaysTrack",
        "BRISPrimePowerRating", "SireStudFeeCur",
    ]
    # DRFSpeedRating 0 = truly missing (no figure assigned); zero→NaN
    # BRISSpeedRating 0 = valid "very slow" figure; keep as 0
    for i in range(1, 11):
        zero_to_nan.append(f"DRFSpeedRating{i}")
    for col in zero_to_nan:
        if col in df.columns:
            df.loc[df[col] == 0, col] = np.nan

    if "TrainerCurYrStrts" in df.columns:
        df["TrainerCurYrStrts"] = df["TrainerCurYrStrts"].fillna(0)
    if "TrainerPrvYrStrts" in df.columns:
        df["TrainerPrvYrStrts"] = df["TrainerPrvYrStrts"].fillna(0)
    if "TrainerStsCurrentMeet" in df.columns:
        df["TrainerStsCurrentMeet"] = df["TrainerStsCurrentMeet"].fillna(0)

    # --- KS weighted win/ITM pcts ---
    df = _compute_ks_weighted(df)

    # --- All-weather prev surface flags ---
    n_aw = pd.Series(0, index=df.index)
    for i in range(1, 11):
        pa = df.get(f"PrevAllWeatherSurfFlag{i}", pd.Series("", index=df.index)).fillna("")
        n_aw += (pa == "A").astype(int)
    df["PrevAllWeatherPast10"] = n_aw

    # --- Lifetime record rates ---
    df = _compute_lt_rates(df)

    # --- Jockey rates ---
    df = _compute_jockey_rates(df)

    # --- Trainer rates ---
    df = _compute_trainer_rates(df)

    # --- HC binary flags (trainer category indicators) ---
    df = _compute_hc_flags(df)

    # --- Morningline probability ---
    ml = df.get("MornLineOddsifavailable", pd.Series(np.nan, index=df.index))
    df["mlprob"] = 1 / (ml + 1)
    df["mlprob"] = df["mlprob"].fillna(0)

    # --- Stretch margin sign fix (for leaders) ---
    # SAS: if horse was 1st at stretch, StretchBtnLngthsonly = -StretchBtnLngthsLdrmargin
    for i in range(1, 11):
        slm_col = f"StretchBtnLngthsLdrmargin{i}"
        sbo_col = f"StretchBtnLngthsonly{i}"
        # Use nStretchPosition if available, otherwise fall back to StretchPosition
        sp_num = pd.to_numeric(
            df.get(f"nStretchPosition{i}",
                   df.get(f"StretchPosition{i}", pd.Series("", index=df.index))),
            errors="coerce")
        is_lead = sp_num == 1

        if sbo_col not in df.columns:
            continue

        # If horse led at stretch, sign-fix: stretch beaten lengths = -leader margin
        if slm_col in df.columns:
            slm = pd.to_numeric(df[slm_col], errors="coerce")
            df[sbo_col] = np.where(is_lead & slm.notna(), -slm, df[sbo_col])

    # --- finish position numeric conversion ---
    df = _convert_positions(df)

    # --- Workout time per furlong ---
    for i in range(1, 13):
        wt  = df.get(f"WorkoutTime{i}", pd.Series(np.nan, index=df.index))
        wd  = df.get(f"WorkoutDist{i}", pd.Series(np.nan, index=df.index))
        wt  = wt.where(wt >= 0, wt.abs())
        wd  = wd.where(wd >= 0, wd.abs())
        col = f"wotimeperfrlg{i}"
        df[col] = np.where(
            wt.notna() & wd.notna() & (wt != 0) & (wd != 0),
            wt / (wd / 220), np.nan)

    # --- Sex dummies ---
    sex = df.get("Sex", pd.Series("", index=df.index)).fillna("")
    df["sex_geld"] = (sex == "G").astype(int)
    df["sex_fem"]  = (sex == "F").astype(int)
    df["sex_colt"] = (sex == "C").astype(int)
    df["sex_h"]    = (sex == "H").astype(int)
    df["sex_male"] = (sex == "M").astype(int)
    df["sex_r"]    = (sex == "R").astype(int)

    # --- BRIS run style dummies ---
    rs = df.get("BRISRunStyledesignation", pd.Series("", index=df.index)).fillna("")
    df["BRISRunstyle_E"]  = (rs == "E").astype(int)
    df["BRISRunstyle_S"]  = (rs == "S").astype(int)
    df["BRISRunstyle_P"]  = (rs == "P").astype(int)
    df["BRISRunstyle_EP"] = (rs == "E/P").astype(int)
    df["BRISRunstyle_NA"] = (rs == "NA").astype(int)

    # --- Sprint flag (5scoring uses <=1540 yards) ---
    dist = df.get("Distanceinyards", pd.Series(np.nan, index=df.index)).abs()
    df["sprint"] = (dist <= 1540).astype(int)

    # --- Stretch margin sign fix for winner ---
    for i in range(1, 11):
        fp  = _get_str_col(df, f"FinishPosition{i}")
        wm  = df.get(f"WinnersMargin{i}", pd.Series(np.nan, index=df.index))
        sb  = df.get(f"FinishBtnLngthsonly{i}", pd.Series(np.nan, index=df.index))
        mp  = _get_str_col(df, f"MoneyPosition{i}")
        is_win  = fp.str.strip() == "1"
        is_dnf  = mp.isin(["99","89","28"])
        df[f"FinishBtnLngthsonly{i}"] = np.where(is_win, -wm,
                                         np.where(is_dnf, 30,
                                         np.where(sb > 30, 30, sb)))

    return df


def _compute_ks_weighted(df: pd.DataFrame) -> pd.DataFrame:
    """Compute KS_wins, KS_ITM, and weighted win/ITM pcts.
    
    From 5scoring.sas lines 222-252:
      KS_wins{i} = round(KeyStatofstarts{i} * (KeyStatWinpct{i}/100), 1)
      KS_ITM{i}  = round(KeyStatofstarts{i} * (KeyStatITMpct{i}/100), 1)
    Then weighted with declining weights based on how many slots have data.
    """
    # Step 1: Derive KS_wins and KS_ITM from starts × pct
    for i in range(1, 7):
        st_col  = f"KeyStatofstarts{i}"
        wp_col  = f"KeyStatWinpct{i}"
        itm_col = f"KeyStatITMpct{i}"
        if st_col in df.columns and wp_col in df.columns:
            st = pd.to_numeric(df[st_col], errors="coerce")
            wp = pd.to_numeric(df[wp_col], errors="coerce")
            df[f"KS_wins{i}"] = np.where(
                st.notna() & wp.notna(),
                np.round(st * (wp / 100), 1), np.nan)
        if st_col in df.columns and itm_col in df.columns:
            st   = pd.to_numeric(df[st_col], errors="coerce")
            itm  = pd.to_numeric(df[itm_col], errors="coerce")
            df[f"KS_ITM{i}"] = np.where(
                st.notna() & itm.notna(),
                np.round(st * (itm / 100), 1), np.nan)

    # Fill nulls with 0
    for i in range(1, 7):
        for col in [f"KS_wins{i}", f"KS_ITM{i}"]:
            if col in df.columns:
                df[col] = df[col].fillna(0)

    # Step 2: Weighted pcts — weights depend on how many slots have data
    # 6 slots: [.33,.25,.18,.12,.07,.05]
    # 5 slots: [.35,.26,.19,.13,.07]
    # 4 slots: [.38,.28,.20,.14]
    # 3 slots: [.43,.33,.24]
    # 2 slots: [.57,.43]
    # 1 slot:  [1.0]
    weights_map = {
        6: [0.33, 0.25, 0.18, 0.12, 0.07, 0.05],
        5: [0.35, 0.26, 0.19, 0.13, 0.07],
        4: [0.38, 0.28, 0.20, 0.14],
        3: [0.43, 0.33, 0.24],
        2: [0.57, 0.43],
        1: [1.0],
    }

    df["KS_w_winpct"] = np.nan
    df["KS_w_ITMpct"] = np.nan
    df["KS_winpct"]   = np.nan
    df["KS_ITMpct"]   = np.nan

    for n_slots in range(6, 0, -1):
        weights = weights_map[n_slots]
        st_cols = [f"KeyStatofstarts{i}" for i in range(1, n_slots+1)]

        # Mask: exactly n_slots have data (slot n+1 is missing or n_slots=6)
        has_n = all(c in df.columns for c in st_cols)
        if not has_n:
            continue

        st_series = [pd.to_numeric(df[c], errors="coerce") for c in st_cols]
        if n_slots < 6:
            next_col = f"KeyStatofstarts{n_slots+1}"
            next_missing = (~pd.to_numeric(df.get(next_col, pd.Series(np.nan, index=df.index)),
                                           errors="coerce").notna())
        else:
            next_missing = pd.Series(True, index=df.index)

        first_present = st_series[0].notna()
        mask = first_present & next_missing

        if not mask.any():
            continue

        w_wins = pd.Series(0.0, index=df.index)
        w_strts = pd.Series(0.0, index=df.index)
        w_itm   = pd.Series(0.0, index=df.index)
        raw_wins = pd.Series(0.0, index=df.index)
        raw_strts = pd.Series(0.0, index=df.index)
        raw_itm   = pd.Series(0.0, index=df.index)

        for j, w in enumerate(weights, 1):
            st  = pd.to_numeric(df.get(f"KeyStatofstarts{j}", pd.Series(np.nan, index=df.index)), errors="coerce").fillna(0)
            kw  = df.get(f"KS_wins{j}", pd.Series(0, index=df.index)).fillna(0)
            ki  = df.get(f"KS_ITM{j}", pd.Series(0, index=df.index)).fillna(0)
            w_wins   += kw * w
            w_strts  += st * w
            w_itm    += ki * w
            raw_wins  += kw
            raw_strts += st
            raw_itm   += ki

        # Write for matching horses
        valid_w = mask & (w_strts > 0)
        valid_r = mask & (raw_strts > 0)
        df.loc[valid_w, "KS_w_winpct"] = (w_wins / w_strts)[valid_w]
        df.loc[valid_w, "KS_w_ITMpct"] = (w_itm  / w_strts)[valid_w]
        df.loc[valid_r, "KS_winpct"]   = (raw_wins / raw_strts)[valid_r]
        df.loc[valid_r, "KS_ITMpct"]   = (raw_itm  / raw_strts)[valid_r]

    return df


def _compute_lt_rates(df: pd.DataFrame) -> pd.DataFrame:
    """Compute EPS and WPS rates for lifetime, dist, track, turf, wet, cy, py, fast."""
    rate_defs = [
        ("LTDist",    "StartsLTRecTodayDist",   "EarningsLTRecTodayDist",
         ["WinsLTRecTodayDist","PlacesLTRecTodayDist","ShowsLTRecTodayDist"],
         "EPS_LTDist", "LTrecTodaydistWPSpct", "LTrecTodaydistWPpct", "LTrecTodaydistWpct"),
        ("LTTrack",   "StartsLTRecTodayTrack",  "EarningsLTRecTodayTrack",
         ["WinsLTRecTodayTrack","PlacesLTRecTodayTrack","ShowsLTRecTodayTrack"],
         "EPS_LTTrack","LTrecTodaytrackWPSpct","LTrecTodaytrackWPpct","LTrecTodaytrackWpct"),
        ("LTTurf",    "StartsLTTurfRec",        "EarningsLTTurfRec",
         ["WinsLTTurfRec","PlacesLTTurfRec","ShowsLTTurfRec"],
         "EPS_LTTurf","LTturfRecWPSpct","LTturfRecWPpct","LTturfRecWpct"),
        ("LTWet",     "StartsLTWetRec",         "EarningsLTWetRec",
         ["WinsLTWetRec","PlacesLTWetRec","ShowsLTWetRec"],
         "EPS_LTWet","LTwetrecWPSpct","LTwetrecWPpct","LTwetrecWpct"),
        ("CurYr",     "StartsCurYearRec",       "EarningsCurYearRec",
         ["WinsCurYearRec","PlacesCurYearRec","ShowsCurYearRec"],
         "EPS_LTCyr","CurYearRecWPSpct","CurYearRecWPpct","CurYearRecWpct"),
        ("PrevYr",    "StartsPrevYearRec",      "EarningsPrevYearRec",
         ["WinsPrevYearRec","PlacesPrevYearRec","ShowsPrevYearRec"],
         "EPS_LTPYr","PrevYearRecWPSpct","PrevYearRecWPpct","PrevYearRecWpct"),
        ("LT",        "StartsLTRec",            "EarningsLTRec",
         ["WinsLTRec","PlacesLTRec","ShowsLTRec"],
         "EPS_LT","LTrecWPSpct","LTrecWPpct","LTrecWpct"),
        ("FastDirt",  "StartsFASTDirt",         "EarningsFASTDirt",
         ["WinsFASTDirt","PlacesFASTDirt","ShowsFASTDirt"],
         "EPS_LTFastDirt","fastdirtWPSpct","fastdirtWPpct","fastdirtWpct"),
    ]

    for (tag, strts_col, earn_col, wps_cols, eps_out, wps_out, wp_out, w_out) in rate_defs:
        strts = df.get(strts_col, pd.Series(0, index=df.index)).fillna(0)
        earn  = df.get(earn_col,  pd.Series(np.nan, index=df.index))
        valid = strts != 0

        df[eps_out] = np.where(valid, earn / strts, np.nan)

        wps_exist = [c for c in wps_cols if c in df.columns]
        if wps_exist:
            wps_sum = df[wps_exist].sum(axis=1, skipna=True)
            wp_sum  = df[wps_exist[:2]].sum(axis=1, skipna=True) if len(wps_exist) >= 2 else wps_sum
            w_val   = df[wps_exist[0]] if wps_exist else pd.Series(np.nan, index=df.index)
            df[wps_out] = np.where(valid, wps_sum / strts, np.nan)
            df[wp_out]  = np.where(valid, wp_sum  / strts, np.nan)
            df[w_out]   = np.where(valid, w_val   / strts, np.nan)

    return df


def _compute_jockey_rates(df: pd.DataFrame) -> pd.DataFrame:
    """Compute jockey WPS pcts."""
    for tag, strts_col, wps_cols in [
        ("JKYatDisJkyonTurf", "JKYatDisJkyonTurfStarts",
         ["JKYatDisJkyonTurfWins","JKYatDisJkyonTurfPlaces","JKYatDisJkyonTurfShows"]),
        ("JockeyCurYr", "JockeyCurYrStrts",
         ["JockeyCurYrWins","JockeyCurYrPlac","JockeyCurYrShow"]),
        ("JockeyPrvYr", "JockeyPrvYrStrts",
         ["JockeyPrvYrWins","JockeyPrvYrPlac","JockeyPrvYrShow"]),
        ("JockeyCurMt", "JockeyStsCurrentMeet",
         ["JockeyWinsCurrentMeet","JockeyPlacesCurrentMeet","JockeyShowsCurrentMeet"]),
    ]:
        strts = df.get(strts_col, pd.Series(0, index=df.index)).fillna(0)
        valid = strts != 0
        exist = [c for c in wps_cols if c in df.columns]
        if exist:
            wps = df[exist].sum(axis=1, skipna=True)
            wp  = df[exist[:2]].sum(axis=1, skipna=True) if len(exist) >= 2 else wps
            w   = df[exist[0]]
            df[f"{tag}WPSpct"] = np.where(valid, wps / strts, np.nan)
            df[f"{tag}WPpct"]  = np.where(valid, wp  / strts, np.nan)
            df[f"{tag}Wpct"]   = np.where(valid, w   / strts, np.nan)
        if tag == "JKYatDisJkyonTurf":
            earn = df.get("JKYatDisJkyonTurfEarnings", pd.Series(np.nan, index=df.index))
            df["JKYatDisJkyonTurfEPS"] = np.where(valid, earn / strts, np.nan)

    return df


def _compute_trainer_rates(df: pd.DataFrame) -> pd.DataFrame:
    """Compute trainer WPS pcts and meet stats."""
    for tag, strts_col, wps_cols in [
        ("TrainerCurYr", "TrainerCurYrStrts",
         ["TrainerCurYrWins","TrainerCurYrPlac","TrainerCurYrShow"]),
        ("TrainerPrvYr", "TrainerPrvYrStrts",
         ["TrainerPrvYrWins","TrainerPrvYrPlac","TrainerPrvYrShow"]),
        ("TrainerCurMt", "TrainerStsCurrentMeet",
         ["TrainerWinsCurrentMeet","TrainerPlacesCurrentMeet","TrainerShowsCureentMeet"]),
    ]:
        strts = df.get(strts_col, pd.Series(0, index=df.index)).fillna(0)
        valid = strts != 0
        exist = [c for c in wps_cols if c in df.columns]
        if exist:
            wps = df[exist].sum(axis=1, skipna=True)
            wp  = df[exist[:2]].sum(axis=1, skipna=True) if len(exist) >= 2 else wps
            w   = df[exist[0]]
            df[f"{tag}WPSpct"] = np.where(valid, wps / strts, np.nan)
            df[f"{tag}WPpct"]  = np.where(valid, wp  / strts, np.nan)
            df[f"{tag}Wpct"]   = np.where(valid, w   / strts, np.nan)

    # All-weather
    aw_strts = df.get("LTStrtsAllWeather", pd.Series(0, index=df.index)).fillna(0)
    aw_valid = aw_strts != 0
    aw_wps   = df.get("LTWinsAllWeather", pd.Series(0, index=df.index)).fillna(0) + \
               df.get("LTPlaceAllWeather", pd.Series(0, index=df.index)).fillna(0) + \
               df.get("LTShowAllWeather",  pd.Series(0, index=df.index)).fillna(0)
    df["LTAWWPSpct"] = np.where(aw_valid, aw_wps / aw_strts, np.nan)
    df["LTAWWpct"]   = np.where(aw_valid,
                                df.get("LTWinsAllWeather", 0) / aw_strts, np.nan)
    df["LTAWEPS"]    = np.where(aw_valid,
                                df.get("LTEarnAllWeather", pd.Series(np.nan, index=df.index)) / aw_strts, np.nan)

    return df


def _compute_hc_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Build HC_ binary flags from trainer category slots (capped at 1)."""
    hc_map = {
        "HC_15daysaway": "1-5 days away",   "HC_1stafterclm": "1st after clm",
        "HC_1statroute": "1st at route",     "HC_1stongrass": "1st on grass",
        "HC_1ststrtwtrn": "1st strt w/trn",  "HC_1stTimeClmg": "1st Time Clmg",
        "HC_1sttimelasix": "1st time lasix", "HC_1stTimeMdnClm": "1st Time MdnClm",
        "HC_1sttimestr": "1st time str",     "HC_1stTimeBlinkers": "1stTimeBlinkers",
        "HC_2ndafterclm": "2nd after clm",   "HC_2ndcareerrace": "2nd career race",
        "HC_2ndgrassrace": "2nd grass race",  "HC_2ndofflayoff": "2nd off layoff",
        "HC_2ndRterace": "2nd Rte race",      "HC_2ndstrtwtrn": "2nd strt w/trn",
        "HC_2ndtimeLasix": "2nd time Lasix",  "HC_2YO": "2YO",
        "HC_3190daysAway": "31-90daysAway",   "HC_3rdofflayoff": "3rd off layoff",
        "HC_4690daysAway": "46-90daysAway",   "HC_90daysaway": "90+ days away",
        "HC_Alarmingdrop": "Alarming drop",   "HC_AllWeather": "All Weather",
        "HC_Allowance": "Allowance",          "HC_AWtoTurf": "AW to Turf",
        "HC_Blinkersoff": "Blinkers off",     "HC_Blnkrbackon": "Blnkr back on",
        "HC_Btnfavorite": "Btn favorite",     "HC_Claiming": "Claiming",
        "HC_Clmrepeater": "Clm repeater",     "HC_Debut1m": "Debut >= 1m",
        "HC_DebutMdnClm": "Debut Mdn Clm",   "HC_DebutMdnSpWt": "Debut MdnSpWt",
        "HC_DebutvsWnrs": "Debut vs Wnrs",    "HC_DirttoAW": "Dirt to AW",
        "HC_Dirttoturf": "Dirt to turf",      "HC_Down2classes": "Down 2+ classes",
        "HC_Downoneclass": "Down one class",  "HC_Dropsoffclaim": "Drops off claim",
        "HC_Dropsoffwin": "Drops off win",    "HC_Gradedstakes": "Graded stakes",
        "HC_MaidenClming": "Maiden Clming",   "HC_MaidenSpWt": "Maiden Sp Wt",
        "HC_MdntoMdnClm": "Mdn to MdnClm",   "HC_MdnwinLR": "Mdn win L/R",
        "HC_MdnClmtoMdn": "MdnClm to Mdn",   "HC_Noclasschg": "No class chg",
        "HC_NonGradedStk": "NonGraded Stk",   "HC_Routes": "Routes",
        "HC_RtetoSprint": "Rte to Sprint",    "HC_Shipper": "Shipper",
        "HC_ShipperToUS": "ShipperToU.S.",    "HC_SprinttoRte": "Sprint to Rte",
        "HC_Sprints": "Sprints",              "HC_SprntRteSprnt": "Sprnt-Rte-Sprnt",
        "HC_SprntSprntRte": "Sprnt-Sprnt-Rte","HC_Turfstarts": "Turf starts",
        "HC_TurftoAW": "Turf to AW",          "HC_Turftodirt": "Turf to dirt",
        "HC_Up2classes": "Up 2+ classes",     "HC_Uponeclass": "Up one class",
        "HC_Wnrlastrace": "Wnr last race",
    }

    # Initialize all HC flags to 0
    for hc_col in hc_map:
        df[hc_col] = 0

    # Accumulate from 6 trainer stat category slots, then cap at 1
    for i in range(1, 7):
        cat_col = f"KeyTrnrStatCategory{i}"
        if cat_col not in df.columns:
            continue
        cat = df[cat_col].fillna("")
        for hc_col, cat_name in hc_map.items():
            df[hc_col] = np.where(cat == cat_name, df[hc_col] + 1, df[hc_col])

    for hc_col in hc_map:
        df[hc_col] = df[hc_col].clip(upper=1)

    return df


def _convert_positions(df: pd.DataFrame) -> pd.DataFrame:
    """Convert string finish position fields to numeric."""
    def to_numeric_pos(series, dnf_val, entrants_series):
        def _conv(row):
            s, e = row
            if isinstance(s, str) and s.strip()[:1].isdigit():
                return float(s.strip()[:2])
            if str(s) in ("99","89","28"):
                return float(e) if pd.notna(e) else np.nan
            return np.nan
        return pd.Series(
            [_conv(r) for r in zip(series, entrants_series)],
            index=series.index)

    for i in range(1, 11):
        ent = df.get(f"Numofentrants{i}", pd.Series(np.nan, index=df.index))
        mp  = _get_str_col(df, f"MoneyPosition{i}")
        for pos_col in [f"FinishPosition{i}", f"FrstCallPosition{i}",
                        f"GateCallPosition{i}", f"SecCallPosition{i}",
                        f"StartCallPosition{i}", f"StretchPosition{i}"]:
            if pos_col in df.columns:
                df[pos_col] = to_numeric_pos(df[pos_col].fillna(""), mp, ent)

    return df


# =============================================================================
# Step 2 — Race-level means
# =============================================================================

def _compute_race_averages(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute race-level averages for all columns that appear in the
    5scoring.sas PROC SQL avg() list.
    Groups by Track, Date, Race.
    """
    # Load the variable list. Look in three places (in priority order):
    #   1. Same directory as this file (the production layout)
    #   2. A pipeline/ subdirectory if you organize files that way
    #   3. /home/claude (sandbox dev path — harmless if missing)
    here = Path(__file__).parent
    candidates = [
        here / "race_norm_vars.txt",
        here.parent / "pipeline" / "race_norm_vars.txt",
        Path("/home/claude/race_norm_vars.txt"),
    ]
    var_file = next((c for c in candidates if c.exists()), None)

    if var_file is not None:
        with open(var_file) as f:
            all_vars = [l.strip() for l in f if l.strip()]
        logger.info(f"Loaded {len(all_vars)} race-norm variables from {var_file.name}")
    else:
        logger.warning(
            "race_norm_vars.txt NOT FOUND — race normalization will be empty. "
            "This means the SAS coefficient models will be applied to raw values "
            "instead of the normalized I{col} / x{col} variables they were trained on. "
            "Drop race_norm_vars.txt next to race_normalize.py to fix."
        )
        all_vars = []

    # Only average columns that actually exist in df AND are numeric
    cols_to_avg = [c for c in all_vars
                   if c in df.columns
                   and pd.api.types.is_numeric_dtype(df[c])]

    if not cols_to_avg:
        logger.warning("No columns to average — race normalization will be empty")
        return df

    logger.info(f"  Computing race averages for {len(cols_to_avg)} columns...")

    agg = df.groupby(RACE_GROUP)[cols_to_avg].mean()
    agg.columns = [f"{c}_ave" for c in agg.columns]
    agg = agg.reset_index()

    df = df.merge(agg, on=RACE_GROUP, how="left")
    return df


# =============================================================================
# Step 3 — I-prefix (ratio) and x-prefix (residual) variables
# =============================================================================

def _compute_ix_variables(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each variable with a computed _ave column, build:
      I{col} = col / col_ave  (ratio, default 1 if avg missing/zero)
      x{col} = col - col_ave  (residual, NaN if either missing)
    """
    var_file = Path(__file__).parent / "race_norm_vars.txt"
    if not var_file.exists():
        var_file = Path("/home/claude/race_norm_vars.txt")
    if var_file.exists():
        with open(var_file) as f:
            all_vars = [l.strip() for l in f if l.strip()]
    else:
        all_vars = []

    built = 0
    for col in all_vars:
        ave_col = f"{col}_ave"
        i_col   = f"I{col}"
        x_col   = f"x{col}"

        if col not in df.columns or ave_col not in df.columns:
            continue

        raw = df[col]
        ave = df[ave_col]

        # Convert datetime to numeric (days since SAS epoch 1960-01-01)
        # SAS date serial = days since Jan 1 1960
        if pd.api.types.is_datetime64_any_dtype(raw):
            sas_epoch = pd.Timestamp('1960-01-01')
            raw = (raw - sas_epoch).dt.days.astype(float)
            ave = (ave - sas_epoch).dt.days.astype(float) if pd.api.types.is_datetime64_any_dtype(ave) else ave

        # I-prefix: ratio (default 1 when avg is 0 or missing)
        df[i_col] = np.where(
            ave.isna() | (ave == 0), 1.0,
            np.where(raw.isna(), np.nan, raw / ave))

        # x-prefix: residual (NaN when either is missing)
        df[x_col] = np.where(
            ave.isna() | raw.isna(), np.nan,
            raw - ave)

        built += 1

    logger.info(f"  Built {built} I/x variable pairs")
    return df


# =============================================================================
# Step 4 — Median-based variables
# =============================================================================

def _compute_median_variables(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute m{col} = col / col_median variables from the %media macro in 5scoring.sas.
    Only build for the ones actually needed by the scoring models.
    """
    needed_median = [
        "LastWOatTT", "LTrecWpct", "TrainerCurMtWPSpct",
        "BRISPrimePowerRating", "TrainerCurMtWpct",
    ]

    for col in needed_median:
        if col not in df.columns:
            continue
        med = df.groupby(RACE_GROUP)[col].transform("median")
        m_col = f"m{col}"
        df[m_col] = np.where(
            med.isna() | (med == 0), 1.0,
            np.where(df[col].isna(), np.nan, df[col] / med))

    return df
