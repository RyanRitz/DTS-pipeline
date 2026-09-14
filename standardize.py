"""
Layer 2 (STANDARDIZE / clean-up) — DTS pipeline.

Component-ratio derivations that were previously referenced by model_vars but
never built (so they resolved to 0). Bit-exact to 5.sas ~340-420:
each rate = sum(numerator components)/starts ONLY when starts not in (0, missing),
else the rate is MISSING (NaN). Trainer-shows keeps the SAS/schema typo
'TrainerShowsCureentMeet'.

After this runs, add the derived base names to features._SUPPLEMENT_VARS so the
existing race-centering builds their x/I versions.
"""
import numpy as np
import pandas as pd

# (output_name, starts_col, [numerator component cols])  -> out = sum(nums)/starts
_RATIO_SPECS = [
    # jockey / trainer current-meet
    ("JockeyCurMtWPSpct",  "JockeyStsCurrentMeet",  ["JockeyWinsCurrentMeet","JockeyPlacesCurrentMeet","JockeyShowsCurrentMeet"]),
    ("JockeyCurMtWPpct",   "JockeyStsCurrentMeet",  ["JockeyWinsCurrentMeet","JockeyPlacesCurrentMeet"]),
    ("JockeyCurMtWpct",    "JockeyStsCurrentMeet",  ["JockeyWinsCurrentMeet"]),
    ("TrainerCurMtWPSpct", "TrainerStsCurrentMeet", ["TrainerWinsCurrentMeet","TrainerPlacesCurrentMeet","TrainerShowsCureentMeet"]),
    ("TrainerCurMtWPpct",  "TrainerStsCurrentMeet", ["TrainerWinsCurrentMeet","TrainerPlacesCurrentMeet"]),
    ("TrainerCurMtWpct",   "TrainerStsCurrentMeet", ["TrainerWinsCurrentMeet"]),
    # lifetime record
    ("LTrecWPSpct", "StartsLTRec", ["WinsLTRec","PlacesLTRec","ShowsLTRec"]),
    ("LTrecWPpct",  "StartsLTRec", ["WinsLTRec","PlacesLTRec"]),
    # jockey at distance on turf
    ("JKYatDisJkyonTurfWPSpct", "JKYatDisJkyonTurfStarts", ["JKYatDisJkyonTurfWins","JKYatDisJkyonTurfPlaces","JKYatDisJkyonTurfShows"]),
    ("JKYatDisJkyonTurfWPpct",  "JKYatDisJkyonTurfStarts", ["JKYatDisJkyonTurfWins","JKYatDisJkyonTurfPlaces"]),
    ("JKYatDisJkyonTurfEPS",    "JKYatDisJkyonTurfStarts", ["JKYatDisJkyonTurfEarnings"]),
    # earnings-per-start
    ("EPS_LTCyr", "StartsCurYearRec", ["EarningsCurYearRec"]),
    ("EPS_LT",    "StartsLTRec",      ["EarningsLTRec"]),
]

# base names to feed features._SUPPLEMENT_VARS for x/I race-centering
STANDARDIZE_CENTER = [
    "JockeyCurMtWpct","JockeyCurMtWPpct","JockeyCurMtWPSpct",
    "TrainerCurMtWpct","TrainerCurMtWPpct","TrainerCurMtWPSpct",
    "LTrecWPpct","LTrecWPSpct","JKYatDisJkyonTurfWPSpct","JKYatDisJkyonTurfEPS","EPS_LTCyr","EPS_LT",
]

# Race-centered (x = raw - race_avg) bases that the live feature layer does NOT
# already build but the DMR-turf config-F needs. Discovered on the real DMR
# 09/04-09/05 cards: features.py x-centers tran_itm_58 but not st_58/wpct_58,
# and never x-centers WorkoutTime1Bullet. Bit-exact to 5scoring.sas
# (x = raw - avg(raw) over race; avg skips missing; x is NaN where raw is NaN).
_CENTER_SPECS = [
    ("xtran_st_58",         "tran_st_58"),
    ("xtran_wpct_58",       "tran_wpct_58"),
    ("xworkouttime1Bullet", "WorkoutTime1Bullet"),
]
# Race-INDEXED (I = raw / race_avg) bases the DMR-maiden config-F needs (wobulls_keeot).
# race_normalize never builds these: race_norm_vars.txt lists 'workouttime1Bullet'
# but features Block 2 names it 'WorkoutTime1Bullet' (exact-case match misses).
# Bit-exact to 5scoring.sas 8090-8097: I = raw/avg when avg not in (.,0) and raw
# present; 1 when avg in (.,0); missing when raw missing. Lowercase-i output name
# so the older exact-name 'iWorkoutTime{i}Bullet' readers in features.py stay inert.
_INDEX_SPECS = [
    ("iworkouttime1Bullet", "WorkoutTime1Bullet"),
    ("iworkouttime2Bullet", "WorkoutTime2Bullet"),
    ("iworkouttime3Bullet", "WorkoutTime3Bullet"),
]
RACE_GROUP_S = ["Track", "Date", "Race"]

def _col(df, c):
    return df[c] if c in df.columns else pd.Series(np.nan, index=df.index)

def _race_center(df, out, raw, groups):
    """x = raw - race_mean(raw); NaN where raw is NaN (matches SAS avg/subtract)."""
    if raw not in df.columns or not all(g in df.columns for g in groups):
        df[out] = np.nan
        return
    r = pd.to_numeric(df[raw], errors="coerce")
    grpmean = r.groupby([df[g] for g in groups]).transform("mean")
    df[out] = r - grpmean

def _race_index(df, out, raw, groups):
    """I = raw / race_mean(raw); 1 where the mean is 0/missing; NaN where raw is
    NaN (matches SAS avg/divide in 5scoring.sas)."""
    if raw not in df.columns or not all(g in df.columns for g in groups):
        df[out] = np.nan
        return
    r = pd.to_numeric(df[raw], errors="coerce")
    ave = r.groupby([df[g] for g in groups]).transform("mean")
    df[out] = np.where(ave.isna() | (ave == 0), 1.0,
                       np.where(r.isna(), np.nan, r / ave.where(ave != 0)))

def standardize_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """Derive all component-ratio bases + the config-F-only race-centered vars.
    Bit-exact to 5.sas: rate only when starts not in (0, missing), else NaN."""
    df = df.copy()
    for out, sts_c, num_cols in _RATIO_SPECS:
        STS = _col(df, sts_c)
        num = sum((_col(df, c).fillna(0) for c in num_cols), pd.Series(0.0, index=df.index))
        valid = STS.notna() & (STS != 0)
        df[out] = np.where(valid, num / STS, np.nan)
    for out, raw in _CENTER_SPECS:
        _race_center(df, out, raw, RACE_GROUP_S)
    for out, raw in _INDEX_SPECS:
        _race_index(df, out, raw, RACE_GROUP_S)
    return df

# back-compat alias (increment-1 wiring)
def derive_meet_rates(df: pd.DataFrame) -> pd.DataFrame:
    return standardize_ratios(df)
