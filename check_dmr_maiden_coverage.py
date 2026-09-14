#!/usr/bin/env python3
"""
Coverage check for the DMR maiden Python port.

Prints the exact set of predictor variables the 14 blended maiden coef files
REQUIRE, and (once build_dmr_maiden_vars exists) which of them are still
missing from the built frame. A missing selected var scores as 0 in
_logit_prob -> silently wrong probabilities, so this must be empty before the
port is trusted.

Usage:
    # just list what the coef files need:
    python check_dmr_maiden_coverage.py --coef ..\\DMR\\SAS_DATA

    # check against a built maiden frame (a DRF run through build_dmr_maiden_vars):
    python check_dmr_maiden_coverage.py --coef ..\\DMR\\SAS_DATA --frame some_built_frame.csv
"""
import argparse
import sys
import pandas as pd
from score_dmr_maiden import selected_vars


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coef", required=True, help="dir holding coef_maid_*.csv")
    ap.add_argument("--frame", default=None,
                    help="optional CSV of a built maiden frame to test coverage against")
    args = ap.parse_args()

    need = selected_vars(args.coef)
    print(f"{len(need)} selected predictors across the 14 blended maiden models:")
    for v in need:
        print(f"  {v}")

    if args.frame:
        cols = set(pd.read_csv(args.frame, nrows=1).columns)
        missing = [v for v in need if v not in cols]
        print(f"\nframe columns: {len(cols)}")
        if missing:
            print(f"MISSING {len(missing)} required vars (would score as 0):")
            for v in missing:
                print(f"  !! {v}")
            sys.exit(1)
        print("OK — every selected var is present in the frame.")


if __name__ == "__main__":
    main()
