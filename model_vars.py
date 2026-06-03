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
    df = _turf_sart_vars(df)
    df = _sarm_vars(df)
    df = _kaaw13_vars(df)
    df = _sard_vars(df)
    df = _keeod_vars(df)
    df = _kta13_vars(df)
    df = _ko25_vars(df)
    df = _apr26_vars(df)
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

    return df
