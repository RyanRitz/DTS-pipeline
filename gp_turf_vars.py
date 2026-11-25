"""
Global model-variable layer -- GP-turf additions (Layer 3).

The 20 model variables the GP.Turf.NonMaiden model needs that no other port
builds, bit-exact to GPX/PROGRAMS/BTSM_GP_TurfModel_2026.sas (frozen 2026-11-24,
coefficients fitted on data through 2026-10-03). The other 29 of the model's 49
variables already come from model_vars.py (24) and dmr_turf_vars.py (5).

Consumed only by score_gp_turf / attribution_gp_turf, but built globally per the
architecture: every name here is new, so building them on every card is inert
for every other family.

Three more variables are GP-SCOPED REBUILDS of names that already exist in the
shared layer with a different definition than the one the GP cells were fitted
on (found in the 2026-11-25 parity run; see GP_TURF_VAR_ALIASES):
  ipurse16_gpt26 -> used where the coefficients say ipurse16_kta13
  finpos1_gpt26  -> used where the coefficients say finpos1_dmrc26
  effpost_gpt26  -> used where the coefficients say effpost
The shared columns are left untouched so no other family moves.

SAS line refs are to BTSM_GP_TurfModel_2026.sas. Convention throughout:
missing handled FIRST, then clip (matches the SAS if/else-if ladders).

The six past-performance variables (SAS ~2560-2645) read raw BRIS past lines,
subtract the RACE mean of the non-missing raw values, clip, then fill. The race
mean is over the horses present in the frame for that race, so this must run
AFTER scratches are removed (same rule as every other race-centered variable).
"""
import numpy as np
import pandas as pd

RACE_GROUP = ["Track", "Date", "Race"]
_SPRINT_YDS = 1540     # GP turf sprints are 5f; shortest route is 7.5f


def _g(df, c):
    """Case-insensitive column fetch (NaN Series if absent). SAS is
    case-insensitive; the live Python frame is not."""
    if c in df.columns:
        return df[c]
    lc = {col.lower(): col for col in df.columns}
    a = lc.get(c.lower())
    return df[a] if a is not None else pd.Series(np.nan, index=df.index)


def _num(df, c):
    return pd.to_numeric(_g(df, c), errors="coerce")


def _fill_clip(s, fill, lo, hi):
    """SAS ladder: if x=. then fill; else if x>hi then hi; else if x<lo then lo; else x."""
    return pd.Series(np.where(s.isna(), fill, np.clip(s, lo, hi)), index=s.index)


def _race_mean(df, raw):
    """Race mean of the non-missing raw values (proc sql mean() skips missing;
    an all-missing race gives a missing mean)."""
    keys = [df[k] for k in RACE_GROUP if k in df.columns]
    if not keys:
        return pd.Series(raw.mean(), index=df.index)
    return raw.groupby(keys).transform("mean")


def _pp_block(df, base, n):
    """n past-line columns base1..basen as a float ndarray (rows x n)."""
    return np.column_stack([_num(df, f"{base}{i}").to_numpy(dtype=float)
                            for i in range(1, n + 1)])


_DNF_MONEY = {"99", "89", "28"}


def _pos_with_dnf_rule(df, base, i):
    """Past-line position i with the 5.sas L160-185 cleanup: a numeric position
    is kept; a non-numeric one becomes Numofentrants{i} when MoneyPosition{i} is
    99/89/28 (did not finish / disqualified); otherwise missing."""
    pos = _num(df, f"{base}{i}")
    money = _g(df, f"MoneyPosition{i}")
    mnum = pd.to_numeric(money, errors="coerce")
    is_dnf = (money.astype("string").str.strip().isin(_DNF_MONEY).fillna(False)
              | mnum.isin([99, 89, 28]))
    ne = _num(df, f"Numofentrants{i}")
    return pos.where(pos.notna(), ne.where(is_dnf, np.nan))


def build_gp_turf_vars(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    new = {}

    # ---- simple recodes -------------------------------------------------
    # L693: jockey at-distance turf earnings/start, centered, in $000
    new["xJKYatDisJkyEPSc"] = _fill_clip(_num(df, "xJKYatDisJkyonTurfEPS"), 0.0, -2500, 2500) / 1000.0
    # L2135: trainer current-year WPS%, centered
    new["xTrnCYwp_gpt26"] = _fill_clip(_num(df, "xTrainerCurYrWPSpct"), 0.0, -0.30, 0.30)
    # L2143: prior-year earnings per start, indexed
    new["IEPSpy_gpt26"] = _fill_clip(_num(df, "IEPS_LTPYr"), 1.0, 0.37, 2.5)
    # L2155: jockey at-distance turf starts, indexed
    new["IJkyDTstr_gpt26"] = _fill_clip(_num(df, "IJKYatDisJkyonTurfStarts"), 1.0, 0.35, 2.2)
    # L2183: prior-year win+place %, centered
    new["xPY_WPpct_gpt26"] = _fill_clip(_num(df, "xPrevYearRecWPpct"), 0.0, -0.25, 0.25)
    # L2275: trainer current-year win+place %, centered
    new["xTrnCYwpct_kst26"] = _fill_clip(_num(df, "xTrainerCurYrWPpct"), -0.05, -0.05, 0.20)
    # L2283: trainer current-year win %, centered
    new["xTrnCYwinp_kst26"] = _fill_clip(_num(df, "xTrainerCurYrWpct"), -0.10, -0.12, 0.12)
    # L2428: lifetime fast-dirt earnings/start, centered. Clip is on the RAW
    # dollars (>7000 -> 7, <-2000 -> -2), otherwise raw/1000.
    fd = _num(df, "xEPS_LTFastDirt")
    new["xEPSfd_gptclm26"] = pd.Series(
        np.where(fd.isna(), 4.0, np.where(fd > 7000, 7.0, np.where(fd < -2000, -2.0, fd / 1000.0))),
        index=df.index)
    # L2484: trainer/jockey combo 365d wins+places, centered. SAS sum(of ..)
    # skips missing; both missing -> missing -> 0.
    r10, r11 = _num(df, "xR10"), _num(df, "xR11")
    jt = r10.fillna(0) + r11.fillna(0)
    jt = jt.where(~(r10.isna() & r11.isna()), np.nan)
    new["jtWP365_gptnc26"] = _fill_clip(jt, 0.0, -50, 50)
    # L2488: jockey current-year starts, indexed
    new["IJkyCYst_gptnc26"] = _fill_clip(_num(df, "IJockeyCurYrStrts"), 1.0, 0.35, 1.75)
    # L2703: horse's current-year starts, centered
    new["xStCY_gptsn26"] = _fill_clip(_num(df, "xStartsCurYearRec"), 0.0, -2, 2)
    # L2789: lifetime all-weather win %, centered
    new["xAWwin_gptrn26"] = _fill_clip(_num(df, "xLTAWWpct"), 0.15, -0.40, 0.35)
    # L2793: earnings/start at today's track, indexed
    new["iEPS_TT_gptrc26"] = _fill_clip(_num(df, "IEPS_LTTrack"), 1.0, 0.0, 3.0)
    # L2797: lifetime turf win %, centered
    new["xLTturfW_gptrn26"] = _fill_clip(_num(df, "xLTturfRecWpct"), -0.10, -0.10, 0.40)

    # ---- GP-scoped rebuilds of two shared names -------------------------
    # ipurse16: the GP build data carries the KEE_AllModelVars_master.sas L324
    # definition = plain mean of the last-6 indexed purses, missing skipped, NO
    # clip. model_vars.ipurse16_kta13 is the older scoring-file version (each leg
    # clipped .25-2.5, missing -> .25). Verified 5e-11 vs the SAS RN export.
    # All six missing never occurs in training; neutral index 1.0 if it does.
    ip = pd.concat([_num(df, f"IPurse{i}") for i in range(1, 7)], axis=1).mean(axis=1, skipna=True)
    new["ipurse16_gpt26"] = ip.fillna(1.0)

    # finpos1: 5.sas L160-185 turns a non-numeric past position (K = lost rider,
    # etc.) into that race's field size when MoneyPosition is 99/89/28 (DNF/DQ).
    # The Python feature layer leaves it missing, which also shifts the race
    # mean for every other horse in the race. Rebuilt here with the 5.sas rule,
    # then centered and recoded exactly like finpos1_dmrc26 (KEE master L218).
    fp1 = _pos_with_dnf_rule(df, "FinishPosition", 1)
    xfp1 = fp1 - _race_mean(df, fp1)
    new["finpos1_gpt26"] = _fill_clip(xfp1, 0.0, -2.5, 5.0)

    # effpost: the SAS build sorts by track/date/race/POSTPOSITION before the
    # within-race counter (BTSM_GP_TurfModel_2026.sas L1942 + KEE master L156-160).
    # model_vars.effpost counts in DRF ROW order, which differs whenever a
    # coupled entry (1 / 1A) is listed together but drawn into different gates.
    # Rebuilt here as the rank of PostPosition within the race (missing first,
    # ties in row order = SAS stable sort), capped at 13.
    pp = _num(df, "PostPosition")
    order = pd.DataFrame({"pp": pp.fillna(-np.inf), "row": np.arange(len(df))}, index=df.index)
    keys = [df[k] for k in RACE_GROUP if k in df.columns]
    if keys:
        order = order.sort_values(["pp", "row"], kind="mergesort")
        cnt = order.groupby([k.reindex(order.index) for k in keys], sort=False).cumcount() + 1
        new["effpost_gpt26"] = cnt.reindex(df.index).clip(upper=13).astype(float)
    else:
        new["effpost_gpt26"] = pd.Series(np.nan, index=df.index)

    # ---- six past-performance variables (SAS data _cx_raw .. data model) ----
    n = len(df)
    surf = np.column_stack([
        _g(df, f"Surface{i}").astype("string").str.strip().str.upper().fillna("").to_numpy(dtype=object)
        for i in range(1, 11)])
    is_turf = (surf == "T")
    dist = _pp_block(df, "Distanceinyards", 10)
    spd = _pp_block(df, "BRISSpeedRating", 10)
    lp = _pp_block(df, "BRISLatePaceFig", 10)
    fc = np.column_stack([_pos_with_dnf_rule(df, "FrstCallPosition", i).to_numpy(dtype=float)
                          for i in range(1, 4)])   # 5.sas DNF rule applies to call positions too
    ne = _pp_block(df, "Numofentrants", 3)

    # today sprint: abs() because BRIS stores "about" distances as negatives.
    # SAS: (abs(.) < 1540) is TRUE (missing sorts low) -> missing = sprint.
    today = _num(df, "Distanceinyards").to_numpy(dtype=float)
    today_spr = np.isnan(today) | (np.abs(today) < _SPRINT_YDS)

    # best BRIS speed on turf, any distance, last 10
    with np.errstate(all="ignore"):
        r_turfbest = np.where(is_turf & ~np.isnan(spd), spd, -np.inf).max(axis=1)
    r_turfbest = np.where(np.isinf(r_turfbest), np.nan, r_turfbest)

    # "like" = turf AND same distance type as today; mean of the 2 most recent
    like = is_turf & ~np.isnan(dist) & ((np.abs(dist) < _SPRINT_YDS) == today_spr[:, None])

    def _first2_mean(vals):
        ok = like & ~np.isnan(vals)
        take = ok & (np.cumsum(ok, axis=1) <= 2)
        cnt = take.sum(axis=1)
        tot = np.where(take, vals, 0.0).sum(axis=1)
        return np.where(cnt > 0, tot / np.where(cnt > 0, cnt, 1), np.nan)

    r_spdlike2 = _first2_mean(spd)
    r_lplike = _first2_mean(lp)

    # best speed, last 3 (any surface)
    s3 = spd[:, :3]
    with np.errstate(all="ignore"):
        r_spdbest3 = np.where(np.isnan(s3), -np.inf, s3).max(axis=1)
    r_spdbest3 = np.where(np.isinf(r_spdbest3), np.nan, r_spdbest3)

    # first-call position as share of field, last 3
    okf = ~np.isnan(fc) & (ne > 0)
    cntf = okf.sum(axis=1)
    with np.errstate(all="ignore"):
        totf = np.where(okf, fc / np.where(okf, ne, 1.0), 0.0).sum(axis=1)
    r_fcpct3 = np.where(cntf > 0, totf / np.where(cntf > 0, cntf, 1), np.nan)

    # claiming-price change vs last race (negative = dropping)
    cp0 = _num(df, "ClaimingPriceofhorse").to_numpy(dtype=float)
    cp1 = _num(df, "ClaimingPriceofhorse1").to_numpy(dtype=float)
    okc = (cp0 > 0) & (cp1 > 0)
    with np.errstate(all="ignore"):
        r_clmdrop = np.where(okc, np.log(np.where(okc, cp0, 1.0) / np.where(okc, cp1, 1.0)), np.nan)

    def _center(raw, lo, hi, fill, fill_if_mean_missing=False):
        r = pd.Series(raw, index=df.index, dtype=float)
        m = _race_mean(df, r)
        miss = r.isna() | (m.isna() if fill_if_mean_missing else False)
        return pd.Series(np.where(miss, fill, np.clip(r - m, lo, hi)), index=df.index)

    new["xSpdTurfBest_rc26"] = _center(r_turfbest, -15.9, 12.0, -8.9)
    new["xFcPct3_rc26"] = _center(r_fcpct3, -0.43, 0.43, 0.0)
    new["xClmDrop_rc26"] = _center(r_clmdrop, -0.78, 0.93, -0.19, fill_if_mean_missing=True)
    new["xSpdBest3_rn26"] = _center(r_spdbest3, -13.4, 11.4, 1.0)
    new["xLpLike_rn26"] = _center(r_lplike, -24.6, 17.7, -8.2)
    new["xSpdLike2_sc26"] = _center(r_spdlike2, -15.4, 11.0, 2.8)

    return pd.concat([df.drop(columns=[c for c in new if c in df.columns]),
                      pd.DataFrame(new, index=df.index)], axis=1)


# coefficient name -> the frame column the GP model must read instead.
GP_TURF_VAR_ALIASES = {
    "ipurse16_kta13": "ipurse16_gpt26",
    "finpos1_dmrc26": "finpos1_gpt26",
    "effpost":        "effpost_gpt26",
}

GP_TURF_NEW_VARS = [
    "ipurse16_gpt26", "finpos1_gpt26", "effpost_gpt26",
    "xJKYatDisJkyEPSc", "xTrnCYwp_gpt26", "IEPSpy_gpt26", "IJkyDTstr_gpt26",
    "xPY_WPpct_gpt26", "xTrnCYwpct_kst26", "xTrnCYwinp_kst26", "xEPSfd_gptclm26",
    "jtWP365_gptnc26", "IJkyCYst_gptnc26", "xStCY_gptsn26", "xAWwin_gptrn26",
    "iEPS_TT_gptrc26", "xLTturfW_gptrn26",
    "xSpdTurfBest_rc26", "xFcPct3_rc26", "xClmDrop_rc26",
    "xSpdBest3_rn26", "xLpLike_rn26", "xSpdLike2_sc26",
]
