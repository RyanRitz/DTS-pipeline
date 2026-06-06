"""
Dump the Python pipeline's scored predictions for the SAR validation cards to one
CSV, for SAS-vs-Python reconciliation.

Run from the FullAutomation folder:
    python dump_pyscores.py                      # default: SAR, the 7 validation cards
    python dump_pyscores.py KEE 20260408 20260409   # any track + dates (YYYYMMDD)

Writes: output/pyscore_<TRACK>_all.csv  and  output/pyfeat_<TRACK>_all.csv
"""
import sys
import pandas as pd
from run_pipeline import run_scoring

TRACK = sys.argv[1] if len(sys.argv) > 1 else "SAR"
DATES = sys.argv[2:] if len(sys.argv) > 2 else [
    "20250827", "20250828", "20250829",
    "20260603", "20260604", "20260605", "20260606"]

KEEP = ["Race", "ProgramNumberifavailable", "HorseName", "Surface", "RaceType",
        "Distanceinyards", "ProbToWin", "predicted", "DTSOdds", "rank",
        # model variables, to diff against the SAS feature file
        "baseprob2", "TrainerKEEOct17", "xBRISPd6", "pywps_sard", "histspd_dmrd",
        "trnwcm_sart", "xdrfsp1m", "XTrnPYROI", "JNT365StrtsKEE1017", "spdft_sard",
        "claimdropper_dmrd", "xNumDaysSinceLRcut", "ltstr_sart", "xwrkdateind",
        "xcMonths_old", "EarlySpeed", "iearningscyind", "wotimefrlg_sart",
        "IEPSLTFDKEE1017", "twofurspd1", "jnt365_sarm", "BrisRelatedKEEOct17D",
        "BestBris0422", "jckytrainmatch", "BBtrck_kaaw13", "xR308_K23",
        "xNumEntLast5cut", "TRNJCKCM_kaaw13", "jcky_d", "TrnCY_WPpct",
        "ieps_LTCYR26", "xNYBred"]

MODELVARS = ["baseprob2","TrainerKEEOct17","xBRISPd6","pywps_sard","histspd_dmrd",
    "trnwcm_sart","xdrfsp1m","XTrnPYROI","JNT365StrtsKEE1017","spdft_sard",
    "claimdropper_dmrd","xNumDaysSinceLRcut","ltstr_sart","xwrkdateind","xcMonths_old",
    "EarlySpeed","iearningscyind","wotimefrlg_sart","IEPSLTFDKEE1017","twofurspd1",
    "jnt365_sarm","BrisRelatedKEEOct17D","BestBris0422","jckytrainmatch","BBtrck_kaaw13",
    "xR308_K23","xNumEntLast5cut","TRNJCKCM_kaaw13","jcky_d","TrnCY_WPpct","ieps_LTCYR26","xNYBred"]
KEYS = ["Race","ProgramNumberifavailable","HorseName","Surface","RaceType","Distanceinyards"]

frames, featframes = [], []
for d in DATES:
    try:
        res = run_scoring(TRACK, d)
    except Exception as e:
        print(f"{d}: ERROR {e}")
        continue
    if res is None:
        print(f"{d}: run_scoring returned None")
        continue
    # predictions from scored_df
    sdf = res.scored_df.copy(); sdf["card"] = d
    frames.append(sdf[["card"] + [c for c in KEEP if c in sdf.columns]])
    # model-variable values from feature_df (the actual scoring inputs)
    fdf = res.feature_df.copy(); fdf["card"] = d
    fcols = ["card"] + [c for c in KEYS + MODELVARS if c in fdf.columns]
    featframes.append(fdf[fcols])
    print(f"{d}: {len(sdf)} scored, feature_df {len(fdf)} rows")

if frames:
    pd.concat(frames, ignore_index=True).to_csv(f"output/pyscore_{TRACK}_all.csv", index=False)
    pd.concat(featframes, ignore_index=True).to_csv(f"output/pyfeat_{TRACK}_all.csv", index=False)
    print(f"wrote output/pyscore_{TRACK}_all.csv and output/pyfeat_{TRACK}_all.csv")
else:
    print("no frames written")
