"""
Global model-variable layer — DMR-maiden additions (Layer 3).

The DMR maiden config-F models (8 cells + 6 parents) select from a pool that is
almost entirely already built elsewhere in the pipeline:
  * the DMR-turf family vars      -> build_dmr_turf_vars (reused here)
  * the SAR/KEE/dirt family vars   -> model_vars.build_model_vars (global, runs first)

This module adds ONLY the 9 selected vars that no existing builder produces.
Each is ported line-for-line from BTSM_DMR_MaidenModel_2026.sas (line refs noted).
SAS missing semantics preserved exactly.

RAW INPUTS this module depends on (verify these exist on the scoring df — a
missing raw input silently yields the fill value, the same trap one level down):
  iworkouttime{1,2,3}Bullet, xBRISAvePedRating, xStartsFASTDirt, xhorsenum,
  xaveragedaysoff5, iworkoutpctrnk{1,2,3}, xBRISLatePaceFig1, xtran_st_34,
  xAuctionPrice
"""
import numpy as np
import pandas as pd
from dmr_turf_vars import build_dmr_turf_vars, _g


def build_dmr_maiden_vars(df: pd.DataFrame) -> pd.DataFrame:
    # turf-family selected vars (SPDLF_SARD26, XBPPR_TC12, PACE4F_DMRT26,
    # TURFSPD_DMRT26, STRBTN_DMRT26, TRNITMTURF_DMRT26, DIRTEARN_DMRT26,
    # JWPS_SARM, XLTRECWPPCT_KTA13, IJKYATDISJKYONTURFEPS_KEEOM, XBRISPD3,
    # XSTRETCHBTNLNGTHSONLY1_KEEOD, XTRAN_WPCT_58C). Recomputed from the same
    # x/I bases -> identical values; no-op if already present.
    df = build_dmr_turf_vars(df)
    W = np.where

    # 1. wobulls_keeot  (SAS 1017-1018) — bullet count, miss->1, clip [0,9]
    b1 = _g(df, "iworkouttime1Bullet"); b2 = _g(df, "iworkouttime2Bullet"); b3 = _g(df, "iworkouttime3Bullet")
    all3 = b1.isna() & b2.isna() & b3.isna()
    iworkoutbullets = pd.concat([b1, b2, b3], axis=1).sum(axis=1, min_count=1)  # all-missing -> NaN
    iworkoutbullets = iworkoutbullets.where(~all3, np.nan)
    wob = W(iworkoutbullets.isna(), 1.0, iworkoutbullets)
    df["wobulls_keeot"] = np.clip(wob, 0, 9)

    # 2. xBRISAvePedRatKEE1017  (SAS 1725-1726) — miss->0, clip [-10,10]
    ped = _g(df, "xBRISAvePedRating")
    df["xBRISAvePedRatKEE1017"] = np.clip(W(ped.isna(), 0.0, ped), -10, 10)

    # 3. xstrtsFT_kta13  (SAS 1827) — miss->0, clip [-6,6]
    sfd = _g(df, "xStartsFASTDirt")
    df["xstrtsFT_kta13"] = np.clip(W(sfd.isna(), 0.0, sfd), -6, 6)

    # 4. xhnumsar  (SAS 1875) — 1 iff xhorsenum present and <=1, else 0
    hn = _g(df, "xhorsenum")
    df["xhnumsar"] = W(hn.isna(), 0, W(hn > 1, 0, 1)).astype(int)

    # 5. xdaysoff5  (SAS 1889-1891) — miss->-25, clip [-40,40]
    doff = _g(df, "xaveragedaysoff5")
    df["xdaysoff5"] = np.clip(W(doff.isna(), -25.0, doff), -40, 40)

    # 6. iworkout_kta13  (SAS 1899-1905) — sum of 3 workout-rank legs,
    #    each: miss->1.0 else clip [.15, 2]
    def _leg(name):
        v = _g(df, name)
        return W(v.isna(), 1.0, np.clip(v, 0.15, 2.0))
    df["iworkout_kta13"] = _leg("iworkoutpctrnk1") + _leg("iworkoutpctrnk2") + _leg("iworkoutpctrnk3")

    # 7. latepace_kma13  (SAS 1974) — 1 iff xBRISLatePaceFig1 > 0 (miss/<=0 -> 0)
    lp1 = _g(df, "xBRISLatePaceFig1")
    df["latepace_kma13"] = W(lp1.isna() | (lp1 <= 0), 0, 1).astype(int)

    # 8. SMW_trn_strs  (SAS 1975) — trainer cat-34 starts, miss->0, clip [-100,100]
    ts34 = _g(df, "xtran_st_34")
    df["SMW_trn_strs"] = np.clip(W(ts34.isna(), 0.0, ts34), -100, 100)

    # 9. auct_kma13  (SAS 2014-2015) — miss->0, clip [-10000, 100000]
    auc = _g(df, "xAuctionPrice")
    df["auct_kma13"] = W(auc.isna(), 0.0, np.clip(auc, -10000, 100000))

    return df
