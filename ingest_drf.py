"""
DTS Pipeline — ingest_drf.py
==============================
Parses a Brisnet DRF (.DRF) Past Performance file into a clean pandas DataFrame.

Replicates the %ryan() SAS macro from 1.sas exactly:
  - 1435 columns, comma-delimited, no header row
  - Date columns parsed from YYYYMMDD to datetime
  - Numeric columns coerced to float (missing → NaN)
  - String columns stripped of whitespace
  - Horse name cleanup (removes country codes in parentheses)
  - Program number normalization to numeric (handles 1A, 2B, etc.)
  - Entry fix for coupled entries
  - uniquenumforscratching key built for merging with race-day changes

Usage:
    from ingest_drf import load_drf
    df = load_drf("raw_data/KEE0408.DRF", track="KEE", date="0408", year="2026")
"""

import pandas as pd
import numpy as np
import logging
from pathlib import Path

from drf_schema import DRF_COLUMN_NAMES, DRF_DATE_COLUMNS, DRF_FLOAT_COLUMNS, DRF_STR_COLUMNS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

class DRFLabelMismatch(ValueError):
    """
    The DRF's contents disagree with the track/date the caller claimed.

    Raised by load_drf(validate=True). Callers derive track/date from the
    FILENAME, and the downloader can mis-name a file (see the 2026-07-19
    incident: a Colonial Downs 7/22 card saved as 20260719_DMR_DRS.DRF and
    published as "Del Mar, July 19"). The file carries its own Track and Date,
    so we refuse rather than publish a card under the wrong identity.
    """


def _track_matches(claim: str, real: str) -> bool:
    """Filenames may use a 3L canon (GPX/SAX/CDX) where the file says 2L."""
    c, r = (claim or "").upper(), (real or "").upper()
    if not c or not r:
        return True
    return r in (c, c[:2], c.rstrip("X")) or c in (r, r[:2])


def _validate_identity(df: pd.DataFrame, track: str, date: str,
                       year: str, drf_path) -> None:
    real_track = ""
    if "Track" in df.columns:
        t = df["Track"].dropna().astype(str).str.strip()
        t = t[t != ""]
        if not t.empty:
            real_track = t.mode().iloc[0]
    real_date = ""
    if "Date" in df.columns:
        d = df["Date"].dropna()
        if not d.empty:
            real_date = d.iloc[0].strftime("%Y%m%d")

    claim_date = f"{year}{date}"
    problems = []
    if real_track and not _track_matches(track, real_track):
        problems.append(f"track: caller said {track.upper()}, file says {real_track.upper()}")
    if real_date and real_date != claim_date:
        problems.append(f"date: caller said {claim_date}, file says {real_date}")

    if problems:
        raise DRFLabelMismatch(
            f"{Path(drf_path).name}: contents do not match the claimed "
            f"identity ({'; '.join(problems)}). Refusing to load a mislabeled "
            f"card. Run repair_drf_names.py to rename DRFs to their contents."
        )


def load_drf(drf_path: str | Path, track: str, date: str, year: str,
             validate: bool = True) -> pd.DataFrame:
    """
    Load and parse a Brisnet DRF file into a clean DataFrame.

    Accepts either a plain CSV-style .DRF file or a ZIP archive containing
    one (BRISnet ships both formats — the daily download is typically a ZIP).
    ZIP detection is automatic via the file's magic bytes.

    Parameters
    ----------
    drf_path : path to the .DRF file  e.g. "raw_data/KEE0408.DRF"
    track    : 3-letter track code    e.g. "KEE"
    date     : MMDD string            e.g. "0408"
    year     : 4-digit year string    e.g. "2026"

    Returns
    -------
    pd.DataFrame — one row per horse entry, cleaned and ready for feature engineering
    """
    drf_path = Path(drf_path)
    if not drf_path.exists():
        raise FileNotFoundError(f"DRF file not found: {drf_path}")

    logger.info(f"Loading DRF: {drf_path}")

    # ------------------------------------------------------------------
    # 0. Detect ZIP wrapper and resolve to a readable text source.
    #    BRISnet ships .DRF files inside ZIP archives; the magic bytes
    #    are 'PK\x03\x04'. If we see those, extract the inner file to
    #    memory and pass that to read_csv via a BytesIO buffer.
    # ------------------------------------------------------------------
    import io
    import zipfile

    with open(drf_path, "rb") as f:
        magic = f.read(4)

    if magic[:2] == b"PK":
        # ZIP archive
        with zipfile.ZipFile(drf_path) as zf:
            names = zf.namelist()
            if not names:
                raise ValueError(f"DRF zip is empty: {drf_path}")
            # BRISnet zips contain exactly one .DRF; pick the first
            # (or first .DRF if there are stray files).
            inner = next((n for n in names if n.upper().endswith(".DRF")), names[0])
            logger.info(f"  Unzipping inner file: {inner}")
            csv_source = io.BytesIO(zf.read(inner))
    else:
        # Plain CSV-style file
        csv_source = drf_path

    # ------------------------------------------------------------------
    # 1. Read raw CSV — no header, all columns as string initially.
    #    Encoding: BRISnet files use Latin-1 (ISO-8859-1) for accented
    #    horse/jockey names. UTF-8 would fail on those rows.
    # ------------------------------------------------------------------
    df = pd.read_csv(
        csv_source,
        header=None,
        names=DRF_COLUMN_NAMES,
        dtype=str,
        keep_default_na=False,
        encoding="latin-1",
        on_bad_lines="warn",
    )

    logger.info(f"  Raw rows loaded: {len(df):,}  |  Columns: {len(df.columns)}")

    # ------------------------------------------------------------------
    # 2. Parse date columns  (YYYYMMDD → datetime)
    # ------------------------------------------------------------------
    for col in DRF_DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col].str.strip(), format="%Y%m%d", errors="coerce")

    # ------------------------------------------------------------------
    # 3. Coerce numeric columns to float  (blanks/errors → NaN)
    # ------------------------------------------------------------------
    for col in DRF_FLOAT_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].str.strip(), errors="coerce")

    # ------------------------------------------------------------------
    # 4. Strip whitespace from string columns
    # ------------------------------------------------------------------
    for col in DRF_STR_COLUMNS:
        if col in df.columns:
            df[col] = df[col].str.strip()

    # ------------------------------------------------------------------
    # 5. Add metadata columns (mirrors SAS ScoreIt_FINAL.sas)
    # ------------------------------------------------------------------
    # Refuse a file whose contents contradict the caller's claim. Must run
    # BEFORE we stamp track3L, which is what silently rewrote identity before.
    if validate:
        _validate_identity(df, track, date, year, drf_path)

    df["track3L"] = track.upper()
    df["HorseName"] = df["HorseName"].str.upper()

    # ------------------------------------------------------------------
    # 6. Strip country code suffixes from horse names
    #    e.g. "HORSE NAME (IRE)" → "HORSE NAME"
    #         "HORSE NAME (GB)"  → "HORSE NAME"
    # ------------------------------------------------------------------
    df["HorseName"] = _clean_horse_name(df["HorseName"])

    # ------------------------------------------------------------------
    # 7. Normalize program number to numeric (handles 1A, 2B, 1X, etc.)
    # ------------------------------------------------------------------
    df["horsenum"] = _normalize_program_number(df["ProgramNumberifavailable"])

    # ------------------------------------------------------------------
    # 8. Build scratch merge key: race#programnumber  e.g. "3#7"
    # ------------------------------------------------------------------
    df["uniquenumforscratching"] = (
        df["Race"].astype("Int64").astype(str) + "#" + df["ProgramNumberifavailable"]
    )
    df["unfs"] = df["uniquenumforscratching"].str[:5]

    # ------------------------------------------------------------------
    # 9. Entry fix for coupled entries (same horsenum on multiple rows)
    #    Mirrors SAS entry_fix logic
    # ------------------------------------------------------------------
    df = _apply_entry_fix(df)

    # ------------------------------------------------------------------
    # 10. Year record correction
    #     If YearCurYearRec is last year → shift to current year with 0 starts
    # ------------------------------------------------------------------
    df = _fix_year_records(df, int(year))

    # ------------------------------------------------------------------
    # 11. Defragment DataFrame (avoids PerformanceWarning from column additions)
    # ------------------------------------------------------------------
    df = df.copy()

    # ------------------------------------------------------------------
    # 12. Sort: date, race, horsenum, program number
    # ------------------------------------------------------------------
    df = df.sort_values(
        ["Date", "Race", "horsenum", "ProgramNumberifavailable"],
        na_position="last"
    ).reset_index(drop=True)

    logger.info(f"  Parsed rows: {len(df):,}  |  Races: {df['Race'].nunique()}")
    return df


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _clean_horse_name(series: pd.Series) -> pd.Series:
    """
    Remove country code suffixes from horse names.
    '(IRE)', '(GB)', '(FR)' etc. — 3-char code in parens at end of name.
    Also handles 4-char codes like '(AUS)'.
    Mirrors the SAS substr/length logic in ScoreIt_FINAL.sas.
    """
    import re
    def _clean(name: str) -> str:
        if not isinstance(name, str):
            return name
        # Remove trailing (XXX) or (XXXX) country codes
        name = re.sub(r'\s*\([A-Z]{2,4}\)\s*$', '', name.strip())
        return name.strip()
    return series.apply(_clean)


def _normalize_program_number(series: pd.Series) -> pd.Series:
    """
    Convert program number strings to numeric horse numbers.
    Handles coupled entry designations: 1A→1, 2B→2, 1X→1, 3C→3, 4D→4
    Mirrors SAS horsenum logic in ScoreIt_FINAL.sas.
    """
    coupled = {'1A', '2B', '3C', '4D', '1X', '2X', '3X', '4X', '1C', '1B'}

    def _convert(val):
        if pd.isna(val) or val == '':
            return np.nan
        if val in coupled:
            return float(val[0])
        try:
            return float(val)
        except (ValueError, TypeError):
            return np.nan

    return series.apply(_convert)


def _apply_entry_fix(df: pd.DataFrame) -> pd.DataFrame:
    """
    For coupled entries (multiple horses with same horsenum in same race),
    assign entry_fix labels A/B/C/D.
    Mirrors SAS entry_fix DATA step logic.
    """
    df = df.copy()
    df["entry_fix"] = ""

    entry_map = {1: "A", 2: "B", 3: "C", 4: "D"}

    # Find races with duplicate hornums
    dupes = df[df.duplicated(subset=["Date", "Race", "horsenum"], keep=False)]

    for idx, row in dupes.iterrows():
        hn = int(row["horsenum"]) if not pd.isna(row["horsenum"]) else None
        if hn and hn in entry_map:
            df.at[idx, "entry_fix"] = entry_map[hn]

    return df


def _fix_year_records(df: pd.DataFrame, current_year: int) -> pd.DataFrame:
    """
    Correct year-record fields when YearCurYearRec is stale.
    Mirrors the SAS year record correction logic in scoring_KEE_APR26.sas.

    If YearCurYearRec == current_year - 1:
        shift current → previous, zero out current
    If YearCurYearRec < current_year - 1:
        zero out both, set prev year = current_year - 1
    """
    df = df.copy()

    prev_year = current_year - 1

    mask_shift = df["YearCurYearRec"] == prev_year
    mask_stale = df["YearCurYearRec"] < prev_year

    # Shift: current → previous
    for col_cur, col_prv in [
        ("YearCurYearRec",    "YearPrevYearRec"),
        ("StartsCurYearRec",  "StartsPrevYearRec"),
        ("WinsCurYearRec",    "WinsPrevYearRec"),
        ("PlacesCurYearRec",  "PlacesPrevYearRec"),
        ("ShowsCurYearRec",   "ShowsPrevYearRec"),
        ("EarningsCurYearRec","EarningsPrevYearRec"),
    ]:
        if col_cur in df.columns and col_prv in df.columns:
            df.loc[mask_shift, col_prv] = df.loc[mask_shift, col_cur]
            df.loc[mask_shift, col_cur] = 0 if not col_cur.startswith("Year") else current_year

    # Stale: zero everything out
    zero_cols = [
        "StartsPrevYearRec", "WinsPrevYearRec", "PlacesPrevYearRec",
        "ShowsPrevYearRec",  "EarningsPrevYearRec",
        "StartsCurYearRec",  "WinsCurYearRec",  "PlacesCurYearRec",
        "ShowsCurYearRec",   "EarningsCurYearRec",
    ]
    for col in zero_cols:
        if col in df.columns:
            df.loc[mask_stale, col] = 0

    if "YearPrevYearRec" in df.columns:
        df.loc[mask_stale, "YearPrevYearRec"] = prev_year
    if "YearCurYearRec" in df.columns:
        df.loc[mask_stale, "YearCurYearRec"] = current_year

    return df


# ---------------------------------------------------------------------------
# Quick test when run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Default test against KEE0408.DRF
    drf = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\ryanr\Documents\BTSM\FullAutomation\raw_data\KEE0408.DRF"

    df = load_drf(drf, track="KEE", date="0408", year="2026")

    print(f"\n{'='*60}")
    print(f"  Track: {df['track3L'].iloc[0]}   Date: {df['Date'].iloc[0].date()}")
    print(f"  Total horses: {len(df)}")
    print(f"  Races: {sorted(df['Race'].dropna().unique().astype(int).tolist())}")
    print(f"{'='*60}\n")

    # Show one race summary
    race1 = df[df["Race"] == 1][["Race", "horsenum", "ProgramNumberifavailable", "HorseName",
                                   "TodaysJockey", "TodaysTrainer", "MornLineOddsifavailable",
                                   "uniquenumforscratching"]].head(12)
    print(race1.to_string(index=False))
