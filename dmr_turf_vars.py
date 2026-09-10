"""
Global model-variable layer — DMR-turf additions (Layer 3).
Every var built once, bit-exact to BTSM_DMR_TurfModel_2026.sas. Consumed only by
the DMR.Turf.NonMaiden config-F model, but defined globally per the architecture.

Inputs assumed present from Layer 2 (standardize): the x/I race-centered vars and
the derived meet-rate family (derive_meet_rates). temp4f = mean of the 3 centered
4f pace figs is built here.

SAS missing semantics preserved exactly, incl. gotchas:
  - latepacec: SAS '.< -6' is TRUE, so missing -> -6
  - bullet3/havebullet: missing handled FIRST
  - pp_raw26: missing stays missing (NaN)
"""
import numpy as np
import pandas as pd


def _g(df, c):
    """Case-insensitive column fetch (returns NaN Series if absent).
    The SAS build and the live Python DRF differ in case for several inputs
    (postposition->PostPosition, ipurse1->IPurse1, xworkouttime1Bullet->
    xWorkoutTime1Bullet), so match case-insensitively rather than exact."""
    if c in df.columns:
        return df[c]
    m = df.attrs.get("_lc_map")
    if m is None or m.get("__n__") != len(df.columns):
        m = {col.lower(): col for col in df.columns}
        m["__n__"] = len(df.columns)
        df.attrs["_lc_map"] = m
    actual = m.get(c.lower())
    return df[actual] if actual is not None else pd.Series(np.nan, index=df.index)


def build_dmr_turf_vars(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    W = np.where  # shorthand

    # --- prime power chain: xBRISPd (cap ±13, miss->0) -> xBRISPd3 = (xBRISPd+14)^3 ---
    xppr = _g(df, "xBRISPrimePowerRating")
    xBRISPd = W(xppr.isna(), 0.0, np.clip(xppr, -13, 13))
    df["xBRISPd3"] = (xBRISPd + 14.0) ** 3

    # --- speed / all-weather / recent-year ---
    aw = _g(df, "xBRISSpeedAllWeather")
    df["xBRISSpeedAW_dmrt"] = W(aw.isna(), -2.0, np.clip(aw, -8, 8))
    py = _g(df, "xBestBRISSpeedMostRecentY")
    df["xbrispy_dmrt"] = W(py.isna(), 0.0, np.clip(py, -10, 10))

    # --- trainer current-YEAR wps (raw, cap ±.2, miss->0) ---
    tyr = _g(df, "xTrainerCurYrWPSpct")
    df["trn102021"] = np.clip(W(tyr.isna(), 0.0, tyr), -0.2, 0.2)

    # --- best-life speed indexed (cap .92..1.08, miss->1) ---
    ibl = _g(df, "IBestBRISSpeedLife")
    df["spdlf_sard26"] = W(ibl.isna(), 1.0, np.clip(ibl, 0.92, 1.08))

    # --- turf form / trainer turf ITM / turf starts / trainer turf% ---
    tf = _g(df, "xLTturfRecWPpct")
    df["turfform_dmrt26"] = W(tf.isna(), -0.10, np.clip(tf, -0.35, 0.35))
    itm = _g(df, "xtran_itm_58")
    df["trnitmturf_dmrt26"] = W(itm.isna(), -7.0, np.clip(itm, -15, 15))
    tst = _g(df, "xtran_st_58")
    df["trnturfst_dmrt26"] = W(tst.isna(), -475.0, np.clip(tst, -700, 700))
    tw = _g(df, "xtran_wpct_58")
    df["xtran_wpct_58c"]  = W(tw.isna(), -5.2, np.clip(tw, -13, 13))
    df["xtran_wpct_58cc"] = W(tw.isna(), -3.0, np.clip(tw, -10, 10))

    # --- post (missing stays NaN) ---
    pp = _g(df, "postposition")
    df["pp_raw26"] = W(pp.isna(), np.nan, np.minimum(pp, 10))

    # --- stretch position / lifetime starts / track WPS+WP ---
    sb = _g(df, "xStretchBtnLngthsonly1")
    df["strbtn_dmrt26"] = W(sb.isna(), 1.0, np.clip(sb, -5, 7))
    ls = _g(df, "xStartsLTRec")
    df["ltstarts_dmrt26"] = W(ls.isna(), 0.0, np.clip(ls, -15, 20))
    tkw = _g(df, "xLTrecTodaytrackWPSpct")
    df["trkwps_dmrt26"] = W(tkw.isna(), 0.0, np.clip(tkw, -0.5, 0.5))
    tkp = _g(df, "xLTrecTodaytrackWPpct")
    df["trkrecwp_dmrt26"] = W(tkp.isna(), 0.0, np.clip(tkp, -0.35, 0.50))

    # --- trainer current-MEET wps (gate on starts=0 or missing pct -> -0.1) ---
    tsts = _g(df, "trainerstscurrentmeet")
    tcm = _g(df, "xTrainerCurMtWPSpct")
    df["trncmwps_c"] = W((tsts == 0) | tcm.isna(), -0.1, np.clip(tcm, -0.4, 0.4))

    # --- EPS current-year, signed-sqrt, cap ±200 (miss->0) ---
    ec = _g(df, "xEPS_LTCyr")
    e = W(ec.isna(), 0.0, np.sign(ec) * np.sqrt(np.abs(ec)))
    df["epslt_dmrt26"] = np.clip(e, -200, 200)

    # --- turf speed (route/nonclaim miss->-1.3 ; claim miss->-7), cap ±10 ---
    ts = _g(df, "xBestBRISSpdTurf")
    df["turfspd_dmrt26"] = W(ts.isna(), -1.3, np.clip(ts, -10, 10))
    df["turfspd_dmrc26"] = W(ts.isna(), -7.0, np.clip(ts, -10, 10))

    # --- late pace: SAS '.< -6' TRUE => missing -> -6 ---
    lp = _g(df, "latepace_avg2")
    df["latepacec_dmrt26"] = W(lp > 7, 7.0, W(lp.isna() | (lp < -6), -6.0, lp))

    # --- purse class legs (indexed, miss->1.0/1.3, cap .2..4) ---
    ip1 = _g(df, "ipurse1")
    df["ipurse1c"] = W(ip1.isna(), 1.0, np.clip(ip1, 0.2, 4))
    ip3 = _g(df, "ipurse3")
    df["ipurse3c"] = W(ip3.isna(), 1.3, np.clip(ip3, 0.2, 4))

    # --- dirt-earnings penalty (index, cap top 6; never missing) ---
    de = _g(df, "IEarningsFASTDirt")
    df["dirtearn_dmrt26"] = W(de > 6, 6.0, de)

    # --- bullet workout (missing handled FIRST) ---
    b1 = _g(df, "xworkouttime1Bullet"); b2 = _g(df, "xworkouttime2Bullet"); b3 = _g(df, "xworkouttime3Bullet")
    all3miss = b1.isna() & b2.isna() & b3.isna()
    df["havebullet_dmrt26"] = W(all3miss, 0, (b1 > 0).astype(int))
    df["bullet3_dmrt26"] = W(b1.isna(), 0.0, W(b1 > 0, 3.0, W(b1 < 0, -1.0, 0.0)))

    # --- turf record WPS (miss->0.02, cap -.35..+.50) ---
    trw = _g(df, "xLTturfRecWPSpct")
    df["turfrecwps_dmrt26"] = W(trw.isna(), 0.02, np.clip(trw, -0.35, 0.50))

    # --- best speed today's track (route/nonclaim miss->-1.5), cap ±10 ---
    bst = _g(df, "xBestBRISSpeedTodaysTrack")
    df["bestspdtrk_dmrt26"] = W(bst.isna(), -1.5, np.clip(bst, -10, 10))

    # --- horses-beaten L3 (miss->0, cap -8..+6) ---
    h3 = _g(df, "xHBL3")
    df["xHBL3c"] = W(h3.isna(), 0.0, np.clip(h3, -8, 6))

    # --- trainer current-year starts (miss->0, cap ±400) ---
    tys = _g(df, "xTrainerCurYrStrts")
    df["trncystrts26"] = W(tys.isna(), 0.0, np.clip(tys, -400, 400))

    # --- early-pace 4f: temp4f = mean of the 3 centered figs; cap ±15, miss->0 ---
    f1 = _g(df, "xBRISFourfPaceFig1"); f2 = _g(df, "xBRISFourfPaceFig2"); f3 = _g(df, "xBRISFourfPaceFig3")
    temp4f = pd.concat([f1, f2, f3], axis=1).mean(axis=1)   # SAS mean() skips missing; all-missing -> NaN
    df["pace4f_dmrt26"] = W(temp4f.isna(), 0.0, np.clip(temp4f, -15, 15))

    # --- AW wins indexed (miss->1, cap top 6, natural floor 0) ---
    aww = _g(df, "ILTWinsAllWeather")
    df["awwins_dmrt26"] = W(aww.isna(), 1.0, np.minimum(aww, 6))

    # --- distance record WP (miss->-0.12, cap -.35..+.5) ---
    dr = _g(df, "xLTrecTodaydistWPpct")
    df["distrecwp_dmrt26"] = W(dr.isna(), -0.12, np.clip(dr, -0.35, 0.5))

    # --- distance speed (claim miss->-5, cap -11..+9) ---
    ds = _g(df, "xBestBRISSpdDist")
    df["distspd_dmrc26"] = W(ds.isna(), -5.0, np.clip(ds, -11, 9))

    # --- finish position last race (miss->0, cap -2.5..+5) ---
    fp = _g(df, "xFinishPosition1")
    df["finpos1_dmrc26"] = W(fp.isna(), 0.0, np.clip(fp, -2.5, 5))

    # --- trainer current-meet starts indexed (miss->1, cap top 3) ---
    itms = _g(df, "ITrainerStsCurrentMeet")
    df["trnmtstrts26"] = W(itms.isna(), 1.0, np.minimum(itms, 3))

    # --- meet WIN-rate vars (from derive_meet_rates -> x-centered) ---
    tcw = _g(df, "xTrainerCurMtWpct")
    df["trncmwin_dmrc26"] = W(tcw.isna(), -0.12, np.clip(tcw, -0.15, 0.25))
    jws = _g(df, "xJockeyCurMtWPSpct")
    df["jckcmwps_dmrc26"] = W(jws.isna(), 0.0, np.clip(jws, -0.25, 0.30))
    jw = _g(df, "xJockeyCurMtWpct")
    df["jckcmwin_dmrn26"] = W(jw.isna(), 0.0, np.clip(jw, -0.15, 0.12))

    # --- inherited vars (defined in DMR-turf build, clip already-available bases) ---
    xppr2 = _g(df, "xBRISPrimePowerRating")
    xbrist12 = W(xppr2.isna(), 0.0, xppr2)                       # xbrist12 = xBRISPrimePowerRating, miss->0
    df["XBPPR_tc12"] = np.clip(xbrist12, -20, 20)

    sk = _g(df, "xStretchBtnLngthsonly1")
    df["xStretchBtnLngthsonly1_keeod"] = W(sk.isna(), 0.0, np.clip(sk, -5, 5))

    ije = _g(df, "IJKYatDisJkyonTurfEPS")
    df["IJKYatDisJkyonTurfEPS_keeom"] = W(ije.isna(), 1.0, np.clip(ije, 0.25, 2.0))

    jwd = _g(df, "xJKYatDisJkyonTurfWPSpct")
    df["jwps_sarm"] = W(jwd.isna(), 0.0, np.clip(jwd, -0.2, 0.2))

    xlr = _g(df, "xLTrecWPpct")                                  # squared transform, miss->1
    df["xLTrecWPpct_kta13"] = W(xlr.isna(), 1.0, W(xlr > 0.5, (1.5)**2, (1.0 + xlr)**2))

    xwd = _g(df, "xWorkoutDate3")
    df["xWorkoutDate3_kma13"] = W(xwd.isna(), 0.0, np.clip(xwd, -35, 35))

    # jcky_tc: SAS treats missing as < any value (missing is smallest) -> -.03
    jte = _g(df, "IJKYatDisJkyonTurfEarnings")
    df["jcky_tc"] = W(jte.isna() | (jte < 1.29), -0.03, 0.07)

    return df
