"""
DTS Pipeline — model_vars.py
================================
Builds the 72 composite model input variables needed by the coefficient files.
These are translated directly from scoring_KEE_APR26.sas lines 1400–2412.

All variables are derived from the `x`-prefix (race-centered residuals) and
`I`-prefix (race-indexed ratios) columns produced by features.py / 5scoring.sas.

Called by features.engineer_features() as the final step before scoring.
"""

import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)


def build_model_vars(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build all 72 composite model variables needed by the scoring engine.
    Returns df with these columns added.
    """
    df = df.copy()

    # --- Prerequisite aliases needed by multiple sub-functions ---

    # numbulls3 — bullets in last 3 workouts (must be computed first)
    if "numbulls3" not in df.columns:
        bullet_cols = [f"WorkoutTime{i}Bullet" for i in range(1, 4)
                       if f"WorkoutTime{i}Bullet" in df.columns]
        df["numbulls3"] = (df[bullet_cols] > 0).sum(axis=1) if bullet_cols else 0

    # turffy_last5 alias (SAS uses lowercase, Python computed TurfyLast5)
    if "turffy_last5" not in df.columns and "TurfyLast5" in df.columns:
        df["turffy_last5"] = df["TurfyLast5"]

    # workoutpctrnk1 alias (SAS lowercase, Python PascalCase)
    if "workoutpctrnk1" not in df.columns and "WorkoutPctRnk1" in df.columns:
        df["workoutpctrnk1"] = df["WorkoutPctRnk1"]
    if "Iworkoutpctrnk1" not in df.columns and "IWorkoutPctRnk1" in df.columns:
        df["Iworkoutpctrnk1"] = df["IWorkoutPctRnk1"]

    # StretchBtnLngthsonly1 — ensure this is the correct signed column
    # (sign-corrected in race_normalize._convert_positions)
    # xStretchBtnLngthsonly1 should be race-centered residual

    df = _dirt_vars(df)
    df = _dirt_vars_dmr(df)
    df = _turf_sart_vars(df)
    df = _sarm_vars(df)
    df = _kaaw13_vars(df)
    df = _sard_vars(df)
    df = _keeod_vars(df)
    df = _kta13_vars(df)
    df = _sar_turf_v8_vars(df)
    df = _sar_maiden_vars(df)
    df = _ko25_vars(df)
    df = _apr26_vars(df)
    df = _kee_oct26_dirt_vars(df)
    df = _kee_oct26_turf_vars(df)
    df = _kee_oct26_maiden_vars(df)
    df = _shared_final_vars(df)

    logger.info(f"  Model vars built: {len(df.columns)} total columns")
    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _g(df, col, default=np.nan):
    """Safe column getter — returns series or default scalar series."""
    if col in df.columns:
        return df[col]
    return pd.Series(default, index=df.index)


def _clip(series, lo, hi, fill=None):
    """Clip series; optionally fill NaN before clipping."""
    if fill is not None:
        series = series.fillna(fill)
    return series.clip(lo, hi)


# ---------------------------------------------------------------------------
# Dirt model vars (scoring lines ~1380–1530)
# ---------------------------------------------------------------------------

def _dirt_vars(df: pd.DataFrame) -> pd.DataFrame:
    # BRISD12t_dmrd, BRIStt12t_dmrd, BRISAW12t_dmrd
    # SAS conditions: only compute each component if _indr > 3
    # (at least 4 horses in the race have that speed type non-missing)
    aw_indr  = _g(df, "BRISSpeedAW_indr",  0).fillna(0)
    tt_indr  = _g(df, "BRISSpeedTT_indr",  0).fillna(0)
    di_indr  = _g(df, "BRISSpeedD_indr",   0).fillna(0)

    xbsaw = _g(df, "xBRISSpeedAllWeather", np.nan)
    xbstt = _g(df, "xBestBRISSpeedTodaysTrack", np.nan)
    xbsdi = _g(df, "xBestBRISSpdDist", np.nan)

    df["BRISAW12t_dmrd"] = np.where((aw_indr > 3) & xbsaw.notna(),
                                     _clip(xbsaw, -10, 10), np.nan)
    df["BRIStt12t_dmrd"] = np.where((tt_indr > 3) & xbstt.notna(),
                                     _clip(xbstt, -10, 10), np.nan)
    df["BRISD12t_dmrd"]  = np.where((di_indr > 3) & xbsdi.notna(),
                                     _clip(xbsdi, -10, 10), np.nan)

    cols_dmrd = ["BRISD12t_dmrd", "BRIStt12t_dmrd", "BRISAW12t_dmrd"]
    all_miss  = df[cols_dmrd].isna().all(axis=1)
    df["BrisRelated_dmrd"] = np.where(
        all_miss, 0,
        df[cols_dmrd].mean(axis=1, skipna=True).fillna(0))

    # claimdropper_dmrd
    xhc = _g(df, "xHC_Dropsoffclaim", 0)
    df["claimdropper_dmrd"] = np.where(
        xhc > 0, 5,
        np.where(xhc < 0, -1, 0))

    # histspd_dmrd — historical speed composite (last 5-6 races)
    # SAS: mean(xBRISSpeedRating6, xBRISSpeedRating5, xDRFSpeedRating5, xDRFSpeedRating6, 0), clip ±5
    hist_cols = ["xBRISSpeedRating5", "xBRISSpeedRating6", "xDRFSpeedRating5", "xDRFSpeedRating6"]
    existing_hist = [c for c in hist_cols if c in df.columns]
    if existing_hist:
        hist_df   = df[existing_hist]
        hist_sum  = hist_df.sum(axis=1, skipna=True)
        hist_cnt  = hist_df.notna().sum(axis=1) + 1   # +1 for the 0 in mean()
        df["histspd_dmrd"] = _clip(hist_sum / hist_cnt, -5, 5, fill=0)
    else:
        df["histspd_dmrd"] = 0

    # woalone_dmrm — workout alone vs field size
    xwon = _g(df, "xWorkoutNumOthDayDist1", 0)
    rt   = _g(df, "RaceType", "")
    df["woalone_dmrm"] = _clip(xwon.fillna(0), -30, 30)
    df.loc[rt == "M", "woalone_dmrm"] = 0

    return df


# ---------------------------------------------------------------------------
# SAR Turf / Route model vars (lines ~1650–1730)
# ---------------------------------------------------------------------------

def _turf_sart_vars(df: pd.DataFrame) -> pd.DataFrame:
    # ltstr_sart — lifetime starts indexed
    ilts = _g(df, "IStartsLTRec", 1.0)
    df["ltstr_sart"] = _clip(ilts.fillna(1.0), 0.2, 2.5)

    # wotimefrlg_sart — workout time per furlong sum
    wot_cols = [f"xwotimeperfrlg{i}c" for i in range(1, 5)
                if f"xwotimeperfrlg{i}c" in df.columns]
    df["wotimefrlg_sart"] = (df[wot_cols].sum(axis=1, skipna=True)
                              if wot_cols else pd.Series(0, index=df.index))

    # drf1_sart — DRF speed rating last race, race-centered
    xdrf1 = _g(df, "xDRFSpeedRating1", 0)
    df["drf1_sart"] = _clip(xdrf1.fillna(0), -12, 12)

    # trnwcm_sart — trainer current meet wins standardized
    # SAS: if xTrainerWinsCurrentMeet_std = . then trnwcm_sart = 0
    xtrn   = _g(df, "xTrainerWinsCurrentMeet", 0)
    trnstd = _g(df, "xTrainerWinsCurrentMeet_std", np.nan)
    df["trnwcm_sart"] = np.where(
        trnstd.isna() | (trnstd == 0), 0,
        _clip(xtrn / trnstd, -2, 2))

    # trnwcm_sart_tempdirt — same but using dirt-specific std
    trnstdd = _g(df, "xTrainerWinsCurrentMeet_std_DIRT", np.nan)
    df["trnwcm_sart_tempdirt"] = np.where(
        trnstdd.isna() | (trnstdd == 0), 0,
        _clip(xtrn / trnstdd, -2, 2))

    # BrisRelated_sart — speed composite for SAR models
    # SAS: if BRISSpeedTT_indr>3 and BRISSpeedD_indr>3 (race must have 4+ horses w/ each speed)
    # Then zero-fill NaN and always divide by 2
    tt_indr_s = _g(df, "BRISSpeedTT_indr", 0).fillna(0)
    di_indr_s = _g(df, "BRISSpeedD_indr",  0).fillna(0)
    xbstt_s   = _g(df, "xBestBRISSpeedTodaysTrack", np.nan)
    xbsdi_s   = _g(df, "xBestBRISSpdDist",  np.nan)
    df["BRIStt12t_sart"] = np.where((tt_indr_s > 3) & xbstt_s.notna(), _clip(xbstt_s, -10, 10), np.nan)
    df["BRISD12t_sart"]  = np.where((di_indr_s > 3) & xbsdi_s.notna(), _clip(xbsdi_s, -10, 10), np.nan)
    fd1_s = df["BRISD12t_sart"].copy()
    fd2_s = df["BRIStt12t_sart"].copy()
    both_miss_srt = fd1_s.isna() & fd2_s.isna()
    df["BrisRelated_sart"] = np.where(both_miss_srt, 0, (fd1_s.fillna(0) + fd2_s.fillna(0)) / 2)

    return df


# ---------------------------------------------------------------------------
# SAR maiden model vars (lines ~1730–1800)
# ---------------------------------------------------------------------------

def _sarm_vars(df: pd.DataFrame) -> pd.DataFrame:
    # jckcm2_sarm — jockey current meet WPS pct standardized, squared
    # SAS: xJkyWCMstd_sarm = xJockeyWinsCurrentMeet / xjwins_std, clip [-1.5,2]
    # When xjwins_std = NaN → xJkyWCMstd_sarm = 0 → jckcm2_sarm = 2.5^2 = 6.25
    xjwins     = _g(df, "xJockeyWinsCurrentMeet", np.nan)
    xjwins_std = _g(df, "xjwins_std", np.nan)
    xJkyWCMstd = np.where(
        xjwins_std.isna() | (xjwins_std == 0) | xjwins.isna(),
        0, np.clip(xjwins / xjwins_std, -1.5, 2.0))
    df["xJkyWCMstd_sarm"] = xJkyWCMstd
    df["jckcm2_sarm"] = (2.5 + xJkyWCMstd) ** 2

    # jckcm2_sarm_DIRT — same using dirt-specific jockey std
    xjwins_std_d = _g(df, "xjwins_std_DIRT", np.nan)
    xJkyWCMstd_d = np.where(
        xjwins_std_d.isna() | (xjwins_std_d == 0) | xjwins.isna(),
        0, np.clip(xjwins / xjwins_std_d, -1.5, 2.0))
    df["xJkyWCMstd_sarm_DIRT"] = xJkyWCMstd_d
    df["jckcm2_sarm_DIRT"] = (2.5 + xJkyWCMstd_d) ** 2

    # trncm2_sart — trainer current meet wins standardized, squared.
    # Mirrors jckcm2_sarm structure for the trainer side. Display-only
    # variable for the TRN bar (not currently consumed by any model).
    #   trnwcm_sart = clip(xTrainerWinsCurrentMeet / xTrainerWinsCurrentMeet_std, -2, 2)
    #                 (already computed in features.py)
    #   trncm2_sart = (2.5 + trnwcm_sart) ** 2
    # Theoretical bounds: (2.5-2)^2 = 0.25 .. (2.5+2)^2 = 20.25.
    # NaN -> 0 -> (2.5+0)^2 = 6.25 (~30% bar, "no signal").
    if "trnwcm_sart" in df.columns:
        df["trncm2_sart"] = (2.5 + df["trnwcm_sart"].fillna(0)) ** 2
    if "trnwcm_sart_tempdirt" in df.columns:
        df["trncm2_sart_DIRT"] = (2.5 + df["trnwcm_sart_tempdirt"].fillna(0)) ** 2

    # BrisRelated_sarm — BRIS best speed composite for SAR maiden models
    # SAS: if BRISSpeedTT_indr>=3 and BRISSpeedD_indr>=3 (note: >= not >)
    # Formula: sum(BRISD12t, BRIStt12t, 0)/2  (not mean, always /2)
    tt_indr_m = _g(df, "BRISSpeedTT_indr", 0).fillna(0)
    di_indr_m = _g(df, "BRISSpeedD_indr",  0).fillna(0)
    xbstt_m   = _g(df, "xBestBRISSpeedTodaysTrack", np.nan)
    xbsdi_m   = _g(df, "xBestBRISSpdDist",  np.nan)
    fd_sarm  = np.where((di_indr_m >= 3) & xbsdi_m.notna(), np.clip(xbsdi_m, -10, 10), np.nan)
    ft_sarm  = np.where((tt_indr_m >= 3) & xbstt_m.notna(), np.clip(xbstt_m, -10, 10), np.nan)
    df["BRISD12t_sarm"]  = fd_sarm
    df["BRIStt12t_sarm"] = ft_sarm
    fd_s = pd.Series(fd_sarm, index=df.index)
    ft_s = pd.Series(ft_sarm, index=df.index)
    either_valid = fd_s.notna() | ft_s.notna()
    df["BrisRelated_sarm"] = np.where(either_valid,
                                       (fd_s.fillna(0) + ft_s.fillna(0)) / 2, 0)

    return df


# ---------------------------------------------------------------------------
# KEE AW Oct 2013 model vars (lines ~1860–1910)
# ---------------------------------------------------------------------------

def _kaaw13_vars(df: pd.DataFrame) -> pd.DataFrame:
    # BBtrck_kaaw13 — best BRIS speed at today's track, race-centered
    xbtt = _g(df, "xBestBRISSpeedTodaysTrack", 0)
    df["BBtrck_kaaw13"] = _clip(xbtt.fillna(0), -8, 8)

    # xwrkdate_kaaw13 — last workout date vs race avg, clipped
    xwd5 = _g(df, "xWorkoutDate5", 0)
    df["xwrkdate_kaaw13"] = _clip(xwd5.fillna(0), -40, 60)

    # TRNJCKCM_kaaw13 — trainer+jockey current meet WPS pct combined (×100)
    # SAS: xTrainerCurMtWPpct clipped ±0.13 ×100 + xJockeyCurMtWPpct clipped ±0.13 ×100
    xtrn_cm  = _g(df, "xTrainerCurMtWPpct", np.nan)
    xjky_cm2 = _g(df, "xJockeyCurMtWPpct", np.nan)
    trn_clipped = np.where(xtrn_cm.notna(), np.clip(xtrn_cm, -0.13, 0.13) * 100, 0)
    jky_clipped = np.where(xjky_cm2.notna(), np.clip(xjky_cm2, -0.13, 0.13) * 100, 0)
    df["xTrainerCurMtWPpctkaaw13"] = trn_clipped
    df["xJockeyCurMtWPpctkaaw13"]  = jky_clipped
    df["TRNJCKCM_kaaw13"] = trn_clipped + jky_clipped

    return df


# ---------------------------------------------------------------------------
# SARD (Saratoga Dirt) model vars (lines ~1600–1660, 1910–1960)
# ---------------------------------------------------------------------------

def _sard_vars(df: pd.DataFrame) -> pd.DataFrame:
    # EPS3_SARD15 — EPS composite (track + current year + lifetime)
    # SAS: each defaults to 1.0 when the I-prefix value is NaN, clipped [0.2, 2.5]
    def _sard15(col):
        val = _g(df, col, np.nan)
        return np.where(val.isna(), 1.0, np.clip(val, 0.2, 2.5))

    df["EPS3_SARD15"] = _sard15("IEPS_LTTrack") + _sard15("IEPS_LTCyr") + _sard15("IEPS_LT")

    # eps4dalt_sard — same formula, SARD vintage (uses same I-prefix sources)
    df["eps4dalt_sard"] = df["EPS3_SARD15"].copy()

    # spdft_sard — best BRIS speed on fast track, indexed
    ibsft = _g(df, "IBestBRISSpdFastTrack", 1.0)
    df["spdft_sard"] = _clip(ibsft.fillna(1.0), 0.92, 1.08)

    # pywps_sard — prior year WPS pct, race-centered
    xpyw = _g(df, "xPrevYearRecWPSpct", -0.4)
    df["pywps_sard"] = _clip(xpyw.fillna(-0.4), -0.4, 0.4)
    df["pywps2_sard"] = (2 + df["pywps_sard"]) ** 2

    # xdrfsp1m_sard — DRF speed last race vs field
    xdrf1 = _g(df, "xDRFSpeedRating1", 0)
    bppr_indr = _g(df, "brisPPR_indr", 0)
    df["xdrfsp1m_sard"] = np.where(
        xdrf1.isna() | (bppr_indr < 4), 0,
        _clip(xdrf1, -10, 10))

    return df


# ---------------------------------------------------------------------------
# KEE Oct 2017 dirt model vars (lines ~1965–1985)
# ---------------------------------------------------------------------------

def _keeod_vars(df: pd.DataFrame) -> pd.DataFrame:
    # Components of TrainerKEEOct17 — these were referenced below but never
    # built, so TrainerKEEOct17 was always 0.  SAS scoring (~lines 1244-1253):
    #   xtran_wpct_50ckeeod = clip(xtran_wpct_50, -13, 13)  (NaN if source NaN)
    #   xtran_wpct_55ckeeod = clip(xtran_wpct_55, -13, 13)  (NaN if source NaN)
    #   xks_w_winpctakeeod  = clip(xKS_w_winpct->0 if missing, -0.15, 0.15)
    #   kswpct_100keeod     = xks_w_winpctakeeod * 100
    # (race_norm builds the centered source as xKS_w_winpct — capital KS.)
    x50 = _g(df, "xtran_wpct_50", np.nan)
    df["xtran_wpct_50ckeeod"] = np.where(x50.notna(), x50.clip(-13, 13), np.nan)
    x55 = _g(df, "xtran_wpct_55", np.nan)
    df["xtran_wpct_55ckeeod"] = np.where(x55.notna(), x55.clip(-13, 13), np.nan)
    xksw_kee = _clip(_g(df, "xKS_w_winpct", np.nan).fillna(0), -0.15, 0.15)
    df["xks_w_winpctakeeod"] = xksw_kee
    df["kswpct_100keeod"]    = xksw_kee * 100

    # TrainerKEEOct17 — trainer route/sprint/AW composite
    cols = ["kswpct_100keeod", "xtran_wpct_50ckeeod", "xtran_wpct_55ckeeod"]
    existing = [c for c in cols if c in df.columns]
    if existing:
        df["TrainerKEEOct17"] = df[existing].mean(axis=1, skipna=True)
    else:
        df["TrainerKEEOct17"] = 0
    df["TrainerKEEOct17_2"] = (df["TrainerKEEOct17"] + 16) ** 2

    # IEPSLTFDKEE1017 — EPS on fast dirt, indexed
    iepslfd = _g(df, "IEPS_LTFastDirt", 1.0)
    df["IEPSLTFDKEE1017"] = _clip(iepslfd.fillna(1.0), 0.2, 2.0)

    # BrisRelatedKEEOct17D — best BRIS speed dirt/AW combo
    # SAS: fixerdirt1 = BRISD12t_sart (already _indr conditioned from _turf_sart_vars)
    #      fixerdirt2 = BRIStt12t_sart (already _indr conditioned)
    #      sum(fixerdirt1, fixerdirt2, 0) / 2; default -10 if both missing
    fd1_k = _g(df, "BRISD12t_sart", np.nan)
    fd2_k = _g(df, "BRIStt12t_sart", np.nan)
    both_miss_k = fd1_k.isna() & fd2_k.isna()
    df["BrisRelatedKEEOct17D"] = np.where(both_miss_k, -10,
                                           (fd1_k.fillna(0) + fd2_k.fillna(0)) / 2)

    return df


# ---------------------------------------------------------------------------
# KTA13 turf model vars (lines ~2060–2125)
# ---------------------------------------------------------------------------

def _kta13_vars(df: pd.DataFrame) -> pd.DataFrame:
    # IJKYe_kta13 — jockey earnings at distance/turf, indexed
    ijkye = _g(df, "IJKYatDisJkyonTurfEarnings", 1.0)
    df["IJKYe_kta13"] = np.where(
        ijkye > 3, 3,
        np.where(ijkye.isna(), 1,
        np.where(ijkye < 0.25, 0.25, ijkye)))

    # iworkoutpctrnk1_ckta13 — first workout percent rank, capped
    # SAS: Iworkoutpctrnk1 = workoutpctrnk1 / workoutpctrnk1_ave
    # Python computes IWorkoutPctRnk1 — same thing
    iwo1 = _g(df, "IWorkoutPctRnk1", np.nan)
    if iwo1.isna().all():
        iwo1 = _g(df, "Iworkoutpctrnk1", np.nan)
    df["iworkoutpctrnk1_ckta13"] = np.where(
        iwo1.isna(), 1.0,
        np.where(iwo1 > 2, 2,
        np.where(iwo1 < 0.15, 0.15, iwo1)))

    # LastWOatTT_kta13 — last workout at today's track ratio
    df["LastWOatTT_kta13"] = _g(df, "ILastWOatTT", 1.0).fillna(1.0)

    return df


# ---------------------------------------------------------------------------
# SAR Turf v8 model vars (BTSM_SAR_Turf_2026.sas)
# ---------------------------------------------------------------------------
# The 10 turf-only inputs to the v8 hierarchical ensemble that were not already
# produced by the dirt/KEE ports.  Each recode reads an upstream x-residual /
# I-index column emitted by race_normalize (all confirmed present in
# race_norm_vars.txt).  SAS line refs are from BTSM_SAR_Turf_2026.sas.
# (xNYBred is already built in features.py; not repeated here.)

def _sar_turf_v8_vars(df: pd.DataFrame) -> pd.DataFrame:
    # eps4d_sard (SAS 1355/1666) — 4-part indexed EPS composite.
    #   each part: NaN -> 1, then clip [0.2, 2.5]  (the _sard15 recode)
    def _sard15(col):
        v = _g(df, col, np.nan)
        return np.where(v.isna(), 1.0, np.clip(v, 0.2, 2.5))
    df["eps4d_sard"] = (_sard15("IEPS_LTDist") + _sard15("IEPS_LTTrack")
                        + _sard15("IEPS_LTCyr") + _sard15("IEPS_LT"))

    # TrnITMTurf (SAS 1987) — trainer turf ITM%, race-centered.
    #   NaN -> 15, clip [-15, 15]
    df["TrnITMTurf"] = _clip(_g(df, "xtran_itm_58", np.nan), -15, 15, fill=15)

    # XjockeycypyW_dmrd (SAS 1084-1092) — jockey prior+current-year wins,
    # each race-centered then clipped, rescaled and summed.
    xjkycywdmr = _clip(_g(df, "xJockeyCurYrWins", np.nan), -100, 150, fill=0)
    xjkypywdmr = _clip(_g(df, "xJockeyPrvYrWins", np.nan), -150, 200, fill=0)
    df["XjockeycypyW_dmrd"] = (xjkypywdmr + 0.05) / 75.7 + (xjkycywdmr + 0.02) / 49.6

    # xHBL4c (SAS 1901) — horses-beaten last 4, race-centered.  NaN -> 0, clip ±14
    df["xHBL4c"] = _clip(_g(df, "xHBL4", np.nan), -14, 14, fill=0)

    # ipurse16_kta13 (SAS 1925-1933) — mean of last-6 indexed purses, each
    # clipped [0.25, 2.5].  SAS treats missing as < .25 (missing -> 0.25), so
    # every element is filled to 0.25 and the nmiss=6 branch is dead code.
    purse_parts = [_clip(_g(df, f"IPurse{i}", np.nan), 0.25, 2.5, fill=0.25)
                   for i in range(1, 7)]
    df["ipurse16_kta13"] = pd.concat(purse_parts, axis=1).mean(axis=1)

    # xLTTurfWPpctSAR22 (SAS 1923) — lifetime turf WP%, race-centered.
    #   NaN -> -0.35, clip ±0.35
    df["xLTTurfWPpctSAR22"] = _clip(_g(df, "xLTturfRecWPpct", np.nan),
                                    -0.35, 0.35, fill=-0.35)

    # qsp_kta13 (SAS 1883) — Quirin speed points, indexed.  NaN -> 1, clip [0.5, 2.5]
    df["qsp_kta13"] = _clip(_g(df, "IQuirinstyleSpeedPoints", np.nan),
                            0.5, 2.5, fill=1.0)

    # xTrainerCurMtWPSpctc (SAS 1910) — trainer current-meet WPS%, race-centered.
    #   NaN -> 0, clip ±0.33
    df["xTrainerCurMtWPSpctc"] = _clip(_g(df, "xTrainerCurMtWPSpct", np.nan),
                                       -0.33, 0.33, fill=0)

    # xDRFSPR2SAR (SAS 1950) — DRF speed 2nd-last race, race-centered.
    #   NaN -> 6, clip ±18   (note the non-zero missing default)
    df["xDRFSPR2SAR"] = _clip(_g(df, "xDRFSpeedRating2", np.nan), -18, 18, fill=6)

    # EPS_SAR25 (SAS 1913) — current-year EPS, race-centered.  NaN -> 0, clip ±30000
    df["EPS_SAR25"] = _clip(_g(df, "xEPS_LTCyr", np.nan), -30000, 30000, fill=0)

    return df


# ---------------------------------------------------------------------------
# SAR maiden model vars (BTSM_SAR_MadienModel_2026.sas)
# ---------------------------------------------------------------------------
# The 10 maiden-only recodes not already produced by the dirt/turf ports.
# Each reads an upstream x-residual / I-index from race_normalize (all raw
# sources confirmed in race_norm_vars.txt; the tran_st_* residuals are built
# via the supplement pass in features.py). (xBRISRunstyle_S is the raw
# x-residual used directly — no recode needed.)

def _sar_maiden_vars(df: pd.DataFrame) -> pd.DataFrame:
    # xtran_st_*_s25 — trainer starts-by-category, race-centered, clip +/-400 (NaN->0)
    for n in (9, 18, 34, 44, 55, 50):
        df[f"xtran_st_{n}_s25"] = _clip(_g(df, f"xtran_st_{n}", np.nan), -400, 400, fill=0)

    # xr101109c25 — jockey-trainer 365-day combo, race-centered, clip +/-0.3 (NaN->0)
    df["xr101109c25"] = _clip(_g(df, "xr101109", np.nan), -0.3, 0.3, fill=0)

    # xRaceDate1_25 — days-since-last-race (last-race date), race-centered, clip +/-100 (NaN->0)
    df["xRaceDate1_25"] = _clip(_g(df, "xRaceDate1", np.nan), -100, 100, fill=0)

    # xSameTrackraces_keeod — starts at today's track, race-centered, clip +/-1 (NaN->0)
    df["xSameTrackraces_keeod"] = _clip(_g(df, "xSameTrackraces", np.nan), -1, 1, fill=0)

    # ipurseSAR25 — mean of last-3 indexed purses WITH a constant 1 in the pool.
    # SAS: mean(of ipurse1,ipurse2,ipurse3,1); all-3-missing -> 1 (dead branch since
    # the constant 1 keeps the mean defined).
    pool = [_g(df, f"IPurse{i}", np.nan) for i in (1, 2, 3)]
    pool.append(pd.Series(1.0, index=df.index))
    df["ipurseSAR25"] = pd.concat(pool, axis=1).mean(axis=1, skipna=True)

    return df


# ---------------------------------------------------------------------------
# KO25 maiden model vars (lines ~2135–2295)
# ---------------------------------------------------------------------------

def _ko25_vars(df: pd.DataFrame) -> pd.DataFrame:
    # IAucPri_keeA25 — auction price indexed, capped
    # SAS: if auction_indr > 1 (not just maiden races)
    auction_indr = _g(df, "auction_indr", 0)
    iauc = _g(df, "IAuctionPrice", 1.0)
    df["IAucPri_keeA25"] = 1.0
    mask = auction_indr > 1
    df.loc[mask, "IAucPri_keeA25"] = _clip(iauc[mask].fillna(1.0), 0.03, 3.0)

    # LRbris_25 — BRIS speed last race, race-centered
    xbrs1 = _g(df, "xBRISSpeedRating1", 0)
    df["LRbris_25"] = _clip(xbrs1.fillna(0), -8, 8)

    # IJKYe_Ko25 — jockey earnings at dist/turf indexed
    ijkye = _g(df, "IJKYatDisJkyonTurfEarnings", 1.0)
    df["IJKYe_Ko25"] = np.where(
        ijkye > 3, 3,
        np.where(ijkye.isna(), 1,
        np.where(ijkye < 0.15, 0.15, ijkye)))

    # BullLast3WO — any bullet in last 3 workouts (binary)
    nb3 = _g(df, "numbulls3", 0)
    df["BullLast3WO"] = (nb3 >= 1).astype(int)

    # xDRF2_Ko25 — DRF speed 2nd last race, race-centered
    xdrf2 = _g(df, "xDRFSpeedRating2", 0)
    df["xDRF2_Ko25"] = _clip(xdrf2.fillna(0), -18, 18)

    # trncurWPSKo25 — trainer current meet WPS pct, race-centered
    xtrn_wps = _g(df, "xTrainerCurMtWPSpct", -0.2)
    df["trncurWPSKo25"] = _clip(xtrn_wps.fillna(-0.2), -0.3, 0.3)

    # lrclass_kma13 — BRIS class level last race, race-centered
    xbrcls1 = _g(df, "xBRISSpeedParforClsLvl1", 0)
    bppr_indr = _g(df, "brisPPR_indr", 0)
    df["lrclass_kma13"] = np.where(
        xbrcls1.isna() | (bppr_indr <= 4), 0,
        _clip(xbrcls1, -6, 6))

    # jckcm_kma13 — jockey current meet WPS pct, clipped
    xjky_wps = _g(df, "xJockeyCurMtWPSpct", 0)
    df["jckcm_kma13"] = _clip(xjky_wps.fillna(0), -0.25, 0.25)

    # xR308_KEE25 — jockey-trainer combined wins ratio, clipped
    xr308 = _g(df, "xR308", 0)
    df["xR308_KEE25"] = np.where(
        xr308 > 10, 10,
        np.where(xr308 < -5, -5, xr308.fillna(0)))

    # xAP_KEE25 — auction price race-centered, clipped
    xauc = _g(df, "xAuctionPrice", -90000)
    df["xAP_KEE25"] = np.where(
        xauc.isna(), -90000,
        _clip(xauc, -200000, 200000))

    # jntWP365Ko25 — joint jockey-trainer 365-day win combo, race-centered
    # SAS: xr101109gt10 clipped [-0.15, 0.30], default 0
    xr101 = _g(df, "xr101109gt10", np.nan)
    df["jntWP365Ko25"] = np.where(
        xr101.isna(), 0,
        np.clip(xr101, -0.15, 0.30))

    # xEarnLTDist — earnings at today's distance, race-centered
    xelt = _g(df, "xEarningsLTRecTodayDist", 0)
    df["xEarnLTDist"] = np.where(
        xelt.isna(), 0,
        np.where(xelt > 15000, 15000,
        np.where(xelt < -7000, -7000, xelt)))

    # JKY_CM_WINSAPR25 — jockey has at least 1 current meet win
    ijwy = _g(df, "IJockeyWinsCurrentMeet", 0)
    df["JKY_CM_WINSAPR25"] = (ijwy >= 1).astype(int)

    # xHC_MdntoMdnClm_C — maiden to maiden claiming trainer flag
    xhc = _g(df, "xHC_MdntoMdnClm", 0)
    df["xHC_MdntoMdnClm_C"] = _clip(xhc.fillna(0), -0.6, 0.6)

    return df


# ---------------------------------------------------------------------------
# April 2026 final composite vars (lines ~1990–2412)
# ---------------------------------------------------------------------------

def _apr26_vars(df: pd.DataFrame) -> pd.DataFrame:
    # EarlySpeed — mean(xBRISTwofPaceFig1..5, 0) × 1.2, clipped ±10
    # SAS formula: mean(of xBRISTwofPaceFig1-xBRISTwofPaceFig5, 0) * 1.2
    # The ", 0" adds a zero to the list — missing values treated as present zeros
    # Equivalent: (sum of non-missing values) / (count of non-missing + 1) * 1.2
    pace_cols = [f"xBRISTwofPaceFig{i}" for i in range(1, 6)
                 if f"xBRISTwofPaceFig{i}" in df.columns]
    if pace_cols:
        pace_df = df[pace_cols]
        pace_sum = pace_df.sum(axis=1, skipna=True)                  # sum non-missing
        pace_cnt = pace_df.notna().sum(axis=1) + 1                    # count + 1 for the 0
        df["EarlySpeed"] = _clip((pace_sum / pace_cnt) * 1.2, -10, 10, fill=0)
    else:
        df["EarlySpeed"] = 0

    # BestBris0422 — best BRIS speed lifetime, race-centered
    xbbl = _g(df, "xBestBRISSpeedLife", 0)
    df["BestBris0422"] = _clip(xbbl.fillna(0), -10, 10)

    # xwrkdateind — last workout was 30+ days before race day
    xwrk = _g(df, "xwrkdate", 0)
    df["xwrkdateind"] = ((xwrk >= 30).astype(int)).fillna(0)

    # x0Numofentrants1..5 — alias for xNumofentrants1..5 (SAS naming)
    for i in range(1, 6):
        src = f"xNumofentrants{i}"
        dst = f"x0Numofentrants{i}"
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]

    # xNumEntLast5 — entries in last 5 races, race-centered (sum of x0Numofentrants1..5)
    x0_cols = [f"x0Numofentrants{i}" for i in range(1, 6)
               if f"x0Numofentrants{i}" in df.columns]
    if x0_cols:
        df["xNumEntLast5"] = df[x0_cols].sum(axis=1, skipna=True)
        df["xNumEntLast5cut"] = _clip(df["xNumEntLast5"], -12, 12)
    else:
        df["xNumEntLast5"]    = 0
        df["xNumEntLast5cut"] = 0

    # xNumDaysSinceLRcut — days since last race, race-centered, clipped ±100
    # SAS: missing (debut) = treated as neg-infinity → clips to -100
    xnds = _g(df, "xNumDaysSinceLastRace", np.nan)
    df["xNumDaysSinceLRcut"]  = np.where(xnds.isna(), -100, _clip(xnds, -100, 100))
    df["xNumDaysSinceLRcut2"] = np.where(xnds.isna(), -200, _clip(xnds, -200, 250))

    # XTrnPYROI — trainer prior year ROI, clipped
    xroi = _g(df, "xTrainerPrvYrROI", 0)
    df["XTrnPYROI"] = _clip(xroi.fillna(0), -2, 2)

    # xcMonths_old — horse age in months vs race average, clipped
    xmo = _g(df, "xMonths_old", 0)
    df["xcMonths_old"] = _clip(xmo.fillna(0), -15, 15)

    # xsexcolt0425 — sex indicator (colt vs others)
    xsc = _g(df, "xsex_colt", 0)
    df["xsexcolt0425"] = np.where(
        xsc == 0, 0,
        np.where(xsc < 0, -2, 3))

    # ieps_LTCYR26 — current year EPS indexed
    iepcy = _g(df, "IEPS_LTCyr", 0.7)
    df["ieps_LTCYR26"] = np.where(
        iepcy.isna(), 0.7,
        np.where(iepcy > 3, 3, iepcy))

    # JCK_PY_WPS — jockey prior year WPS pct, race-centered
    xjpy = _g(df, "xJockeyPrvYrWPSpct", 0)
    df["JCK_PY_WPS"] = _clip(xjpy.fillna(0), -0.20, 0.20)

    # iJCK_StrtCM — jockey current meet starts, indexed
    # Uses IJockeyStsCurrentMeet (ratio = horse/race_avg)
    ijsts = _g(df, "IJockeyStsCurrentMeet", 1.0)
    df["iJCK_StrtCM"] = np.where(
        ijsts.isna(), 1.0,
        np.where(ijsts > 2.5, 2.5, ijsts))

    # xdaysoff26 — average days off last 5 races, race-centered, clipped ±60
    # SAS: xdaysoff26 = xaveragedaysoff5 if non-missing, else 40 (debut default)
    xado5 = _g(df, "xaveragedaysoff5", np.nan)
    df["xdaysoff26"] = np.where(
        xado5.isna(), 40,       # SAS default for debut / insufficient history
        _clip(xado5, -60, 60))

    # KS_itmm_26 — key stat ITM pct, race-centered
    xksitm = _g(df, "xKS_w_ITMpct", 0)
    df["KS_itmm_26"] = _clip(xksitm.fillna(0), -0.25, 0.25)

    # xJCK_CMWPS26 — jockey current meet WPS pct, race-centered, clipped ±0.4
    # Python computes xJockeyCurMtWPSpct (correct) — just alias it
    xjcm = _g(df, "xJockeyCurMtWPSpct", np.nan)
    df["xJCK_CMWPS26"] = np.where(xjcm.isna(), 0, _clip(xjcm, -0.4, 0.4))

    # StretchBL_LR26 — stretch beaten lengths last race, race-centered, clipped ±8
    # Uses xStretchBtnLngthsonly1 (sign-corrected in race_normalize)
    xsbl = _g(df, "xStretchBtnLngthsonly1", np.nan)
    df["StretchBL_LR26"] = np.where(xsbl.isna(), 0, _clip(xsbl, -8, 8))

    # WinsatDist26 — wins at today's distance, indexed
    iwld = _g(df, "IWinsLTRecTodayDist", np.nan)
    df["WinsatDist26"] = np.where(
        iwld.isna(), 1.0,
        np.where(iwld >= 4, 4, iwld))

    # xturffy_last5 — turf tendency last 5, race-centered
    # After supplementary normalization, xTurfyLast5 = TurfyLast5 - TurfyLast5_ave
    xtf5 = _g(df, "xTurfyLast5", np.nan)
    if xtf5.isna().all():
        xtf5 = _g(df, "xturffy_last5", np.nan)
    df["xturffy_last5"] = xtf5.fillna(0)

    # Weight_LR — weight last race vs field, clipped
    xw1 = _g(df, "xWeight1", 0)
    df["Weight_LR"] = _clip(xw1.fillna(0), -8, 8)

    # xQSP_2025KEE — QSP race-centered, clipped ±5
    xqsp = _g(df, "xQSP_2025", -5)
    df["xQSP_2025KEE"] = np.where(
        xqsp.isna(), -5,
        _clip(xqsp, -5, 5))

    # FirstFractionL3 — mean first fraction last 3 races
    frac_cols = ["xFraction11", "xFraction21", "xFraction31"]
    existing = [c for c in frac_cols if c in df.columns]
    if existing:
        df["FirstFractionL3"] = df[existing].mean(axis=1, skipna=True).fillna(0)
        df["FirstFractionL3"] = _clip(df["FirstFractionL3"], -5, 5, fill=0)
    else:
        df["FirstFractionL3"] = 0

    # ShowedLateSP_LR — showed late speed in last race
    xblp = _g(df, "xBRISLatePaceFig1", 0)
    df["ShowedLateSP_LR"] = (xblp >= 10).astype(int)

    # xsexcolt0425 already done above
    # numbulls3 already done in features.py

    return df


# ---------------------------------------------------------------------------
# Shared final variables used across multiple models
# ---------------------------------------------------------------------------

def _shared_final_vars(df: pd.DataFrame) -> pd.DataFrame:
    # xks_w_winpcta — key stat win pct, clipped ±0.15 then × nothing
    xksw = _g(df, "xKS_w_winpct", 0)
    df["xks_w_winpcta"] = _clip(xksw.fillna(0), -0.15, 0.15)

    # xBRIS_DsPRn_dc — distance pedigree rating, race-centered
    xbdsp = _g(df, "xBRIS_DsPRn", 0)
    df["xBRIS_DsPRn_dc"] = _clip(xbdsp.fillna(0), -10, 10)

    # xBRISPd2 — PPR polynomial (already in features.py but named differently)
    if "xBRISPd" in df.columns:
        df["xBRISPd2"] = (df["xBRISPd"] + 14) ** 2
    elif "xBRISPrimePowerRating" in df.columns:
        xbp = _clip(df["xBRISPrimePowerRating"].fillna(0), -13, 13)
        df["xBRISPd2"] = (xbp + 14) ** 2
    else:
        df["xBRISPd2"] = 0

    # xBRISPd6 — PPR polynomial ^2.5
    if "xBRISPd" in df.columns:
        df["xBRISPd6"] = (df["xBRISPd"] + 14) ** 2.5
    elif "xBRISPrimePowerRating" in df.columns:
        xbp = _clip(df["xBRISPrimePowerRating"].fillna(0), -13, 13)
        df["xBRISPd6"] = (xbp + 14) ** 2.5
    else:
        df["xBRISPd6"] = 0

    # XBPPR_tc12_3 — PPR turf polynomial
    if "xBRISPrimePowerRating" in df.columns:
        xb12 = _clip(df["xBRISPrimePowerRating"].fillna(0), -20, 20)
        df["XBPPR_tc12_3"] = (xb12 + 21) ** 2
    else:
        df["XBPPR_tc12_3"] = 0

    # xBRISSpeedAWc_keeod — AW speed race-centered, clipped
    xbaw = _g(df, "xBRISSpeedAllWeather", 0)
    df["xBRISSpeedAWc_keeod"] = _clip(xbaw.fillna(0), -8, 8)

    # xBRISRunstyle_EP — E/P run style race-centered
    # already computed in features.py as xBRISRunstyle_EP
    if "xBRISRunstyle_EP" not in df.columns:
        df["xBRISRunstyle_EP"] = 0

    # xturffy_last5 — turf tendency last 5, race-centered
    if "xturffy_last5" not in df.columns:
        xtf5 = _g(df, "xTurfyLast5", 0)
        df["xturffy_last5"] = xtf5.fillna(0)

    # xPostPosition — post position race-centered
    if "xPostPosition" not in df.columns:
        xpp = _g(df, "xhorsenum", 0)
        df["xPostPosition"] = xpp.fillna(0)

    # baseprob2 — already built in features.py, ensure present
    if "baseprob2" not in df.columns:
        df["baseprob2"] = 1 / df.get("HorsesRan",
                          pd.Series(10, index=df.index)).replace(0, np.nan)

    # LastWOatTT — binary: last workout at today's track
    if "LastWOatTT" not in df.columns:
        df["LastWOatTT"] = 0

    # numbulls3 — bullets in last 3 workouts (ensure present)
    if "numbulls3" not in df.columns:
        bullet_cols = [f"WorkoutTime{i}Bullet" for i in range(1, 4)
                       if f"WorkoutTime{i}Bullet" in df.columns]
        if bullet_cols:
            df["numbulls3"] = (df[bullet_cols] > 0).sum(axis=1)
        else:
            df["numbulls3"] = 0

    # HC_1stongrass and HC_ShipperToUS — trainer category binary flags
    for hc_col in ["HC_1stongrass", "HC_ShipperToUS"]:
        if hc_col not in df.columns:
            df[hc_col] = 0

    # PPt12 — post position race-centered, clipped ±5
    if "PPt12" not in df.columns:
        xpp = _g(df, "xPostPosition", 0)
        df["PPt12"] = _clip(xpp.fillna(0), -5, 5)

    # jcky_d — jockey EPS at distance/turf, indexed (dirt model input)
    if "jcky_d" not in df.columns:
        ijkye = _g(df, "IJKYatDisJkyonTurfEPS", 1.0)
        df["jcky_d"] = np.where(
            ijkye.isna(), 1.0,
            np.where(ijkye < 0.4, 0.4,
            np.where(ijkye > 3.0, 3.0, ijkye)))

    # wotimefrlg_keeom — workout time per furlong (maiden 5-race variant)
    if "wotimefrlg_keeom" not in df.columns:
        wot_cols = [f"xwotimeperfrlg{i}c" for i in range(1, 6)
                    if f"xwotimeperfrlg{i}c" in df.columns]
        df["wotimefrlg_keeom"] = (df[wot_cols].sum(axis=1, skipna=True)
                                   if wot_cols else pd.Series(0, index=df.index))

    # xR309c — jockey-trainer 309-day win ratio, clipped ±2/5
    if "xR309c" not in df.columns:
        xr309 = _g(df, "xR309", np.nan)
        df["xR309c"] = np.where(xr309.notna(), _clip(xr309, -2, 5), np.nan)

    # -----------------------------------------------------------------
    # SAR dirt model inputs (also used by the current KEE APR26 build,
    # so this re-syncs the KEE port too).  Definitions verified
    # byte-for-byte identical in BTSM_SAR_DirtModel_2026.sas and
    # BTSM_KEE_DirtModel_APR26_VF.sas.  All source columns
    # (xDRFSpeedRating1, xr101109, IBRISTwofPaceFig1, xTrainerCurYrWPpct)
    # are produced by race_normalize from race_norm_vars.txt — pure add.
    # -----------------------------------------------------------------

    # xdrfsp1m — DRF speed last race, race-centered, clipped ±10;
    # zeroed when the race has <4 horses carrying a PPR (brisPPR_indr<4).
    # SAS: if xDRFSpeedRating1=. or brisPPR_indr<4 then 0; else clip(±10)
    if "xdrfsp1m" not in df.columns:
        xdrf1m = _g(df, "xDRFSpeedRating1", np.nan)
        bppr   = _g(df, "brisPPR_indr", 0).fillna(0)
        df["xdrfsp1m"] = np.where(
            xdrf1m.isna() | (bppr < 4), 0,
            _clip(xdrf1m, -10, 10))

    # jnt365_sarm — joint jockey-trainer 365-day combo (xr101109), race-centered.
    # NOTE: distinct from jntWP365Ko25 (that one uses xr101109gt10, caps -0.15/0.30).
    # SAS: if xr101109=. then -0.1; else clip(xr101109, -0.3, 0.3)
    if "jnt365_sarm" not in df.columns:
        xr101 = _g(df, "xr101109", np.nan)
        df["jnt365_sarm"] = np.where(
            xr101.isna(), -0.1,
            _clip(xr101, -0.3, 0.3))

    # twofurspd1 — early (2-furlong) pace-figure indicator, indexed.
    # SAS: if IBRISTwofPaceFig1>=1 then 1; else 0
    if "twofurspd1" not in df.columns:
        ibtf = _g(df, "IBRISTwofPaceFig1", np.nan)
        df["twofurspd1"] = np.where(ibtf >= 1, 1, 0)

    # TrnCY_WPpct — trainer current-year W+P pct (TrainerCurYrWPpct),
    # race-centered, missing -> 0.
    # SAS: if xTrainerCurYrWPpct=. then 0; else xTrainerCurYrWPpct
    if "TrnCY_WPpct" not in df.columns:
        xtcy = _g(df, "xTrainerCurYrWPpct", np.nan)
        df["TrnCY_WPpct"] = xtcy.fillna(0)

    # iearningscyind — current-year earnings indicator (active in SAR sprint model;
    # core/NC use the newer ieps_LTCYR26 instead).
    # SAS: if IEarningsCurYearRec>1.3 then 1; else 0  (missing -> 0)
    if "iearningscyind" not in df.columns:
        iecyr = _g(df, "IEarningsCurYearRec", np.nan)
        df["iearningscyind"] = np.where(iecyr > 1.3, 1, 0)

    return df


# ---------------------------------------------------------------------------
# DMR dirt config-F model vars (24 transforms; bit-exact port of
# BTSM_DMR_DirtModel_2026.sas). Names verified NOT to collide with any KEE/SAR
# coefficient var, so building them for every card is inert for other families.
# xBRISPd2a is already built in features.py (identical) -> not rebuilt here.
# ---------------------------------------------------------------------------

def _dirt_vars_dmr(df):
    g = lambda c, d=np.nan: _g(df, c, d)
    xbr1 = g("xBRISSpeedRating1")
    df["LRBris_dmr26"]  = _clip(xbr1, -9, 12, fill=0)
    df["lastbris_dmrd"] = _clip(xbr1, -8,  5, fill=0)
    df["xbrispy_kaaw13"] = _clip(g("xBestBRISSpeedMostRecentY"), -4, 4, fill=0)
    spdft = _clip(g("IBestBRISSpdFastTrack"), .92, 1.08, fill=1)
    spdlf = _clip(g("IBestBRISSpeedLife"),    .92, 1.08, fill=1)
    df["spdoth_sard26"] = pd.concat([spdft, spdlf], axis=1).mean(axis=1)
    df["BRISDist_dmr26alt"] = _clip(g("xBestBRISSpdDist"), -10, 10, fill=0)
    bstt = _clip(g("xBestBRISSpeedTodaysTrack"), -8, 8)
    bsds = _clip(g("xBestBRISSpdDist"), -8, 8)
    df["trkdist_dmrn"] = pd.concat([bstt, bsds], axis=1).mean(axis=1, skipna=True).fillna(0)
    df["IEPS_LTDist_dmrt"] = _clip(g("IEPS_LTDist"), .2, 2.5, fill=1)
    df["IEPS_LTCyr_dmrt"]  = _clip(g("IEPS_LTCyr"),  .2, 2.5, fill=1)
    lp1 = _clip(g("xBRISLatePaceFig1"), -10, 15)
    lp2 = _clip(g("xBRISLatePaceFig2"), -10, 15)
    df["latepace_avg2"] = pd.concat([lp1, lp2], axis=1).mean(axis=1, skipna=True).fillna(0)
    lc1 = _clip(g("xBRISSpeedParforClsLvl1"), -6, 6)
    lc2 = _clip(g("xBRISSpeedParforClsLvl2"), -6, 6)
    lc3 = _clip(g("xBRISSpeedParforClsLvl3"), -6, 6)
    df["lrclass_avg3_alt"] = pd.concat([lc1, lc2, lc3], axis=1).mean(axis=1, skipna=True).fillna(0)
    df["qsp_dmrd26"]  = _clip(g("xQuirinstyleSpeedPoints"), -5, 5, fill=0)
    df["finbtn_dmrn"] = _clip(g("xFinishBtnLngthsonly1"), -8, 8, fill=8)   # missing -> +8
    is_sprint = g("Distanceinyards").abs() <= 1540
    wd = pd.concat([g("xWorkoutDist1"), g("xWorkoutDist2")], axis=1).mean(axis=1)
    distwo = pd.Series(np.where(is_sprint, 0.0, wd), index=df.index)
    df["distwo_dmrm26"] = distwo.fillna(0).clip(-100, 100)
    df["icuryrwp_dc"]   = _clip(g("ICurYearRecWPpct"), .2, 2, fill=1)
    df["curyrwp_final"] = _clip(g("xCurYearRecWPpct"), -.7, .7, fill=0)
    df["iJCK_EPS2025"]  = _clip(g("IJKYatDisJkyonTurfEPS"), .2, 2, fill=1)
    jesp = _clip(g("IJKYatDisJkyonTurfEPS"), .2, 2.5, fill=1)
    jpyw = _clip(g("IJockeyPrvYrWpct"), .5, 1.4, fill=1)
    jcyw = _clip(g("IJockeyCurYrWpct"), .5, 1.4, fill=1)
    df["jcky_keeapraw13"] = 1.5 * jesp + jpyw + jcyw
    twcm = g("xTrainerWinsCurrentMeet"); tstd = g("xTrainerWinsCurrentMeet_std")
    tstd_ok = tstd.notna() & (tstd != 0)
    ratio = np.where(tstd_ok, twcm / tstd.where(tstd_ok, np.nan), 0.0)
    dmrm = pd.Series(ratio, index=df.index).clip(-2, 4)
    df["trnwcm_dmrm"] = np.where(g("RaceType") == "S", 0.0, dmrm)
    df["jnt365gt10"] = _clip(g("xr101109gt10"), -.3, .3, fill=0)
    iw1 = _clip(g("IWorkoutPctRnk1"), .15, 2, fill=.15)
    iw2 = _clip(g("IWorkoutPctRnk2"), .15, 2, fill=.15)
    iw3 = _clip(g("IWorkoutPctRnk3"), .15, 2, fill=.15)
    df["iworkout_dmrm"] = iw1 + iw2 + iw3
    df["xWorkoutDate3_keeod"] = _clip(g("xWorkoutDate3"), -75, 75, fill=0)
    df["xLastWOatTT"]         = g("xLastWOatTT")
    # CA-bred: features.py doesn't build it -> derive from StateCountryabrvw, race-center
    _cab = (g("StateCountryabrvw").astype(str).str.strip().str.upper() == "CA").astype(float)
    _cabavg = _cab.groupby([df["Track"], df["Date"], df["Race"]]).transform("mean")
    df["xCABred"] = (_cab - _cabavg).fillna(0)
    return df


# ---------------------------------------------------------------------------
# KEE October 2026 DIRT model vars  (locked 2026-09; from KEE_AllModelVars_master.sas)
# 5-cell ensemble core/c/n/s/r.  These 29 model vars + 7 upstream deps post-dated
# the general model_vars build; all are transforms of 5scoring-standardized inputs
# already produced by engineer_features (resolved case-insensitively — SAS is
# case-insensitive, the Python pipeline is not).
# ---------------------------------------------------------------------------

def _kee_oct26_dirt_vars(df: pd.DataFrame) -> pd.DataFrame:
    # case-insensitive column resolver (SAS is case-insensitive). Resolve LIVE
    # (rebuild the lookup each call) so vars built earlier in THIS function are
    # visible to later ones — a snapshot taken at entry misses them and silently
    # returns the fill value (single-pass production bug).
    def gi(name, default=np.nan):
        lc = {c.lower(): c for c in df.columns}
        col = lc.get(name.lower())
        return df[col] if col is not None else pd.Series(default, index=df.index)

    def race_mean(s):
        return s.groupby([df["Track"], df["Date"], df["Race"]]).transform("mean")

    # ── upstream deps not built elsewhere ────────────────────────────────
    # infortag26 (raw claim tag) + xinfortag26 (race-centered, proc-sql in SAS)
    infortag26 = (gi("ClaimingPriceofhorse", 0).fillna(0) > 0).astype(float)
    df["infortag26"] = infortag26
    df["xinfortag26"] = infortag26 - race_mean(infortag26)

    # _k1.._k4 — KeyStat ITM% components, gated by >=10 starts
    for i in (1, 2, 3, 4):
        starts = gi(f"KeyStatofstarts{i}")
        itm = gi(f"xKeyStatITMpct{i}")
        df[f"_k{i}"] = np.where((starts >= 10) & itm.notna(), itm, 0.0)

    # lp1_avg — late-pace fig L1, clipped (MISSING STAYS MISSING, no fill)
    lp1 = gi("xBRISLatePaceFig1")
    df["lp1_avg"] = np.where(lp1.isna(), np.nan,
                             np.where(lp1 >= 15, 15, np.where(lp1 < -10, -10, lp1)))

    # xtran_wpct_30 — trainer claiming win% (KeyStat cat 30), race-centered
    tw30 = gi("tran_wpct_30")
    df["xtran_wpct_30"] = tw30 - race_mean(tw30)

    # claimedinlast6 chain — pipeline leaves these 0; rebuild from Claimedcode1-6
    # (5.sas: claimedinraceN = 1 if Claimedcode{N}=='c'; claimedinlast6=sum(1..6);
    #  xclaimedinlast6 = claimedinlast6 - race_mean, else missing.)
    cl6 = sum(((gi(f"Claimedcode{i}").astype(str).str.lower() == "c").astype(float))
              for i in (1, 2, 3, 4, 5, 6))
    df["claimedinlast6"] = cl6
    cl6_ave = race_mean(cl6)
    df["claimedinlast6_ave"] = cl6_ave
    df["xclaimedinlast6"] = np.where(cl6_ave.notna() & cl6.notna(), cl6 - cl6_ave, np.nan)

    # xwotimeperfrlg{1..4}c — centered workout-time comps, clip +/-0.6, fill 0
    for i in (1, 2, 3, 4):
        w = gi(f"xwotimeperfrlg{i}")
        df[f"xwotimeperfrlg{i}c"] = _clip(w.fillna(0), -0.6, 0.6)

    # effpost — scratch-adjusted post = sequential counter within race (cap 13).
    # SAS: by track date race; first.race -> _pr=0; _pr+1; min(_pr,13).
    # Row order = DRF order (post order); replicate with within-race cumcount.
    _pr = df.groupby([df["Track"], df["Date"], df["Race"]]).cumcount() + 1
    df["effpost"] = _pr.clip(upper=13).astype(float)

    # helper: clip-with-fill in SAS "if missing then FILL; clip [lo,hi]" order
    def cf(src, lo, hi, fill):
        s = gi(src)
        return np.where(s.isna(), fill, np.where(s > hi, hi, np.where(s < lo, lo, s)))

    # ── 29 model vars ────────────────────────────────────────────────────
    # simple clip/fill of a standardized input
    df["IEPS_LTCyrKOct26"] = cf("IEPS_LTCyr", 0, 3.5, 0.77)
    df["IEPS_LTCyr_nc"]    = cf("IEPS_LTCyr", 0, 5, 1.0)
    df["iepscy_rt"]        = cf("IEPS_LTCyr", 0, 3, 1.3)
    df["iepscy_sp"]        = cf("IEPS_LTCyr", 0, 3, 0.5)
    df["imonthscap"]       = cf("IMonths_old", 0.6, 1.5, 1)
    df["jkyErnDT_rt"]      = cf("IJKYatDisJkyonTurfEarnings", 0, 4, 1)
    df["ltwpstr_dmrm"]     = cf("xLTrecTodaytrackWPSpct", -0.3, 0.3, 0)
    df["spdft_sard26"]     = cf("IBestBRISSpdFastTrack", 0.92, 1.08, 1)
    # spdlf_sard: clip/fill THEN sprint gate (route races -> forced to 1)
    _spdlf = cf("IBestBRISSpeedLife", 0.92, 1.08, 1)
    df["spdlf_sard"]       = np.where(gi("sprint") == 0, 1.0, _spdlf)
    df["strbtn_clm"]       = cf("xStretchBtnLngthsonly1", -5, 8, 0)
    df["tranclm30"]        = cf("xtran_wpct_30", -20, 40, 0.5)
    df["trncurmtKo25"]     = cf("xTrainerCurMtWPpct", -0.25, 0.25, -0.18)
    df["TopTrnStatWpct"]   = cf("xKeyStatWinpct1", -20, 20, 0)
    df["xBRISftKOct26"]    = cf("xBestBRISSpdFastTrack", -12, 12, -1.2)
    df["xBRISlfKOct26"]    = cf("xBestBRISSpeedLife", -12, 12, 0)
    df["xKSwins1KOct26"]   = cf("xKS_wins1", -30, 55, 0)
    df["xclaimed6KOct26"]  = cf("xclaimedinlast6", -1.75, 2.5, 0)

    # IJKYatDisJkyonTurfEPS_keeom — fill 1 if missing, else raw; then clip [0.25, 2]
    ijk = gi("IJKYatDisJkyonTurfEPS")
    df["IJKYatDisJkyonTurfEPS_keeom"] = np.clip(np.where(ijk.isna(), 1.0, ijk), 0.25, 2.0)

    # Months_oldKOct26 — clip +/-36, NO missing fill (missing stays missing)
    xmo = gi("xMonths_old")
    df["Months_oldKOct26"] = np.where(xmo >= 36, 36, np.where(xmo <= -36, -36, xmo))

    # xStartsLTReccut — clip +/-30, missing stays missing
    xslt = gi("xStartsLTRec")
    df["xStartsLTReccut"] = np.where(xslt.isna(), np.nan,
                                     np.where(xslt > 30, 30, np.where(xslt < -30, -30, xslt)))

    # DRF1_SARD15 — 0 if missing OR brisPPR_indr<4; else clip +/-10
    xdrf = gi("xDRFSpeedRating1"); ppr = gi("brisPPR_indr")
    df["DRF1_SARD15"] = np.where(xdrf.isna() | (ppr < 4), 0,
                                 np.where(xdrf > 10, 10, np.where(xdrf < -10, -10, xdrf)))

    # lp1_nc — 5 if lp1_avg missing else lp1_avg
    lp1a = df["lp1_avg"]
    df["lp1_nc"] = np.where(pd.isna(lp1a), 5, lp1a)

    # xBRIStrkKOct26 — gated by BRISSpeedTT_indr>=3 & track-speed present
    tt = gi("BRISSpeedTT_indr"); xtrk = gi("xBestBRISSpeedTodaysTrack")
    gate = (tt >= 3) & xtrk.notna()
    df["xBRIStrkKOct26"] = np.where(gate,
                                    np.where(xtrk > 10, 10, np.where(xtrk < -10, -10, xtrk)), 0.0)
    df["notrk_KOct26"] = np.where(gate, 0.0, 1.0)

    # sums
    df["xKeyStatITM14sum_KOct26"] = (df["_k1"] + df["_k2"] + df["_k3"] + df["_k4"]).clip(-60, 60)
    # SAS sum(of ...) = sum of non-missing; missing only if ALL missing
    df["xJTcmITMKOct26"] = pd.concat([gi("xR309"), gi("xR310"), gi("xR311")],
                                     axis=1).sum(axis=1, min_count=1)
    df["wotimefrlg_dmrd"] = (df["xwotimeperfrlg1c"] + df["xwotimeperfrlg2c"]
                             + df["xwotimeperfrlg3c"] + df["xwotimeperfrlg4c"])
    return df


# ---------------------------------------------------------------------------
# KEE October 2026 TURF model vars  (locked 2026-09; from KEE_AllModelVars_master.sas)
# core/s/r/graded ensemble.  23 model vars + lasix deps.  Runs AFTER the dirt
# function (reuses xwotimeperfrlg{1..4}c it builds).
# ---------------------------------------------------------------------------

def _kee_oct26_turf_vars(df: pd.DataFrame) -> pd.DataFrame:
    def gi(name, default=np.nan):
        lc = {c.lower(): c for c in df.columns}
        col = lc.get(name.lower())
        return df[col] if col is not None else pd.Series(default, index=df.index)

    def race_mean(s):
        return s.groupby([df["Track"], df["Date"], df["Race"]]).transform("mean")

    def cf(src, lo, hi, fill):
        s = gi(src)
        return np.where(s.isna(), fill, np.where(s > hi, hi, np.where(s < lo, lo, s)))

    # ── lasix deps + xlasixchg26 (race-centered, proc-sql in SAS) ─────────
    tmed = gi("TodaysMedN"); med1 = gi("Medication1")
    lasix_today = tmed.isin([1, 3, 4, 5])
    lasix_last = med1.isin([1, 3])
    lasixchg26 = np.where(tmed == 9, 0.0,
                          np.where(lasix_today & ~lasix_last, 1.0,
                                   np.where(~lasix_today & lasix_last, -1.0, 0.0)))
    lasixchg26 = pd.Series(lasixchg26, index=df.index)
    df["lasixchg26"] = lasixchg26
    df["xlasixchg26"] = lasixchg26 - race_mean(lasixchg26)

    # ── 23 model vars ────────────────────────────────────────────────────
    # symmetric fill-then-clip
    df["IEPS_LTTrack_dmrt"]     = cf("IEPS_LTTrack", 0.2, 2.5, 1)
    df["iStrBtnLR26"]           = cf("IStretchBtnLngthsonly1", -3, 5, 1)
    df["lrclass1_kot26"]        = cf("xBRISSpeedParforClsLvl1", -6, 5, 3)
    df["lrclass1_alt"]          = cf("xBRISSpeedParforClsLvl1", -5, 5, 0)   # turf final (+/-5, miss->0)
    df["xBRISSpeedAWc_kot26"]   = cf("xBRISSpeedAllWeather", -8, 8, 7)
    df["xBRISSpeedAWc_krt26"]   = cf("xBRISSpeedAllWeather", -12, 8, 7)
    df["xHBL4_kot26"]           = cf("xHBL4", -20, 20, 3)
    df["xKeyStatITMpct1_kot26"] = cf("xKeyStatITMpct1", -30, 30, 0)
    df["distspd_kst26"]         = cf("xBestBRISSpdDist", -4, 6, -0.5)
    df["lp1_kot26"]             = cf("xBRISLatePaceFig1", -10, 15, 14)
    df["IAucPriKOct26"]         = cf("IAuctionPrice", 0.03, 4, 1)

    # asymmetric / high-only / special-map
    ieps_ltt = gi("IEPS_LTTurf")
    df["IEPS_LTTurf_kgt26"] = np.where(ieps_ltt.isna(), 0.7, np.where(ieps_ltt > 4, 4, ieps_ltt))
    df["IEPS_LTTurf_krt26"] = np.where(ieps_ltt.isna(), 0.4, np.where(ieps_ltt > 3, 3, ieps_ltt))
    ieps_lt = gi("IEPS_LT")
    df["IEPS_LT_keeot"] = np.where(ieps_lt > 3.5, 3.5, np.where(ieps_lt.isna(), 1, ieps_lt))
    df["IEPS_LT_kst26"] = np.where(ieps_lt.isna(), 1, np.where(ieps_lt > 4, 4, ieps_lt))
    iltrec = gi("ILTrecWpct")
    df["ILTrecWpct_krt26"] = np.where(iltrec.isna(), 1, np.where(iltrec > 1.7, 2, iltrec))

    # xLTrecWPSpct_d12 — clip +/-0.25, NO missing fill (missing stays missing)
    xltw = gi("xLTrecWPSpct")
    df["xLTrecWPSpct_d12"] = np.where(xltw > 0.25, 0.25, np.where(xltw < -0.25, -0.25, xltw))

    # foreignbred26 — flag
    scab = gi("StateCountryabrvw").astype(str).str.upper().str.strip()
    df["foreignbred26"] = scab.isin(["GB", "IRE", "FR", "GER"]).astype(float)

    # means-of-fields with fill+clip
    efr = pd.concat([gi("xFrstCallBtnLngthsonly1"), gi("xFrstCallBtnLngthsonly2")], axis=1).mean(axis=1)
    df["efrbtn_krt26"] = np.where(efr.isna(), -2, np.where(efr < -4, -4, np.where(efr > 7, 7, efr)))
    lp3 = pd.concat([gi("xBRISLatePaceFig1"), gi("xBRISLatePaceFig2"), gi("xBRISLatePaceFig3")], axis=1).mean(axis=1)
    df["lp3_kot26"] = np.where(lp3.isna(), 6, np.where(lp3 > 20, 20, np.where(lp3 < -20, -20, lp3)))

    # tjhot26 — T/J hot combo composite (components fill 0)
    _w = gi("xR309c").fillna(0); _p = gi("xR310c").fillna(0); _st = gi("xR308c").fillna(0)
    df["tjhot26"] = (_w + _p) + 0.5 * _st

    # wotimefrlg_kot26 — sum of centered workout comps (built in dirt fn)
    df["wotimefrlg_kot26"] = (gi("xwotimeperfrlg1c").fillna(0) + gi("xwotimeperfrlg2c").fillna(0)
                              + gi("xwotimeperfrlg3c").fillna(0) + gi("xwotimeperfrlg4c").fillna(0))
    return df


# ---------------------------------------------------------------------------
# KEE October 2026 MAIDEN model vars  (locked 2026-09; from KEE_AllModelVars_master.sas)
# 7-cell family (Core/M/S/ST/SD/MSp/MRt).  23 model vars + 4 upstream deps.
# ---------------------------------------------------------------------------

def _kee_oct26_maiden_vars(df: pd.DataFrame) -> pd.DataFrame:
    def gi(name, default=np.nan):
        lc = {c.lower(): c for c in df.columns}
        col = lc.get(name.lower())
        return df[col] if col is not None else pd.Series(default, index=df.index)

    def race_mean(s):
        return s.groupby([df["Track"], df["Date"], df["Race"]]).transform("mean")

    def cf(src, lo, hi, fill):
        s = gi(src)
        return np.where(s.isna(), fill, np.where(s > hi, hi, np.where(s < lo, lo, s)))

    # ── deps ─────────────────────────────────────────────────────────────
    # temp4f = mean(xBRISFourfPaceFig1-3)  (for pace4f_kmd26)
    temp4f = pd.concat([gi("xBRISFourfPaceFig1"), gi("xBRISFourfPaceFig2"),
                        gi("xBRISFourfPaceFig3")], axis=1).mean(axis=1)
    df["temp4f"] = temp4f

    # xworkoutpctrnk_gt4_1 = race-centered (for wo_kmr26)
    wpg = gi("workoutpctrnk_gt4_1"); wpg_ave = race_mean(wpg)
    df["xworkoutpctrnk_gt4_1"] = np.where(wpg_ave.notna() & wpg.notna(), wpg - wpg_ave, np.nan)

    # Itran_wpct_55 = indexed tran_wpct_55 (raw/race-ave; ave in (.,0)->1) (for iTrnSpr_kmd26)
    tw55 = gi("tran_wpct_55"); tw55_ave = race_mean(tw55)
    df["Itran_wpct_55"] = np.where(tw55_ave.notna() & (tw55_ave != 0) & tw55.notna(),
                                   tw55 / tw55_ave.where(tw55_ave != 0, np.nan),
                                   np.where(tw55_ave.isna() | (tw55_ave == 0), 1.0, np.nan))

    # KS_w_ITM1 = KS_ITM1 * slot-1 weight (weight by # populated KeyStat slots),
    # then xKS_w_ITM1 = race-centered (for xKSitm1_kmd26).
    nslots = sum(gi(f"KeyStatofstarts{i}").notna().astype(int) for i in (1, 2, 3, 4, 5, 6))
    wmap = {6: 0.33, 5: 0.35, 4: 0.38, 3: 0.43, 2: 0.57}
    w1 = nslots.map(wmap)                    # NaN for nslots in {0,1} -> KS_w_ITM1 missing
    ks_w_itm1 = gi("KS_ITM1") * w1
    df["KS_w_ITM1"] = ks_w_itm1
    kwi_ave = race_mean(ks_w_itm1)
    df["xKS_w_ITM1"] = np.where(kwi_ave.notna() & ks_w_itm1.notna(), ks_w_itm1 - kwi_ave, np.nan)

    # ── 23 model vars ────────────────────────────────────────────────────
    df["IEPS_LTDist_kmd26"] = cf("IEPS_LTDist", 0.2, 2.5, 0.7)
    df["TrnStCM_msp26"]     = cf("xTrainerStsCurrentMeet", -20, 20, 0)
    df["TwoF_sarm"]         = cf("xBRISTwofPaceFig1", -4, 4, 0)
    df["bris2f12"]          = cf("xBRISTwofPaceFig1", -9, 9, 0)
    df["iJkyPrvWin_sd26"]   = cf("IJockeyPrvYrWins", 0.1, 2.5, 1)
    df["iTrnSpr_kmd26"]     = cf("Itran_wpct_55", 0.1, 2.5, 1)
    df["iwork1_kmd26"]      = cf("iworkoutpctrnk1", 0.1, 1.0, 0.7)
    df["lrclass1_kmr26"]    = cf("xBRISSpeedParforClsLvl1", -8, 2, 0)
    df["trnpyw_kaaw13"]     = cf("xTrainerPrvYrWpct", -0.14, 0.14, 0)
    df["wo_kmr26"]          = cf("xworkoutpctrnk_gt4_1", -0.30, 0.30, 0)
    df["xJkyWCM_kmd26"]     = cf("xJockeyWinsCurrentMeet", -6, 8, 0)
    df["xKSitm1_kmd26"]     = cf("xKS_w_ITM1", -50, 75, -50)
    df["xKSwins1KMd26"]     = cf("xKS_wins1", -30, 70, -20)
    df["xKSwpct1_kmd26"]    = cf("xKeyStatWinpct1", -15, 20, 0)
    df["xKSwpct1_msp26"]    = cf("xKeyStatWinpct1", -15, 20, -10)
    df["xTrnWCM_kmd26"]     = cf("xTrainerWinsCurrentMeet", -3, 6, 0)

    # high-only clips
    iap = gi("IAuctionPrice")
    df["IAucPrice_s26"] = np.where(iap.isna(), 0.8, np.where(iap > 5, 5, iap))
    icy = gi("ICurYearRecWPSpct")
    df["icuryrwps_st26"] = np.where(icy.isna(), 0.4, np.where(icy > 3.5, 3.5, icy))
    ilt = gi("ILTrecWPSpct")
    df["iltrwps_s26"] = np.where(ilt.isna(), 0.2, np.where(ilt > 2.5, 2.5, ilt))

    # xBRIS_DtPRc — clip [-7, 8], no missing fill
    dtpr = gi("xBRIS_DtPRn")
    df["xBRIS_DtPRc"] = np.where(dtpr > 8, 8, np.where(dtpr < -7, -7, dtpr))

    # firstlasix26 — flag
    df["firstlasix26"] = gi("TodaysMedN").isin([4, 5]).astype(float)

    # pace4f_kmd26 — -6.5 if temp4f missing else clip [-15, 15]
    df["pace4f_kmd26"] = np.where(temp4f.isna(), -6.5, np.clip(temp4f, -15, 15))

    # xjckyeps_s26 — /1000 scale, plateau 7.5 / floor -5, missing -> -5
    je = gi("xJKYatDisJkyonTurfEPS")
    _xj = np.where(je > 7500, 7.5, np.where(je < -5000, -5, je / 1000.0))
    df["xjckyeps_s26"] = np.where(pd.isna(_xj), -5, _xj)
    return df
