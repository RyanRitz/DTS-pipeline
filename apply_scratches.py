"""
DTS Automation Pipeline — apply_scratches.py
===============================================
Filters scratched horses out of an ingested DRF DataFrame.

Designed to slot into run_pipeline.py between ingest_drf.load_drf() and
features/scoring. The merge key matches the existing 'unfs' column that
ingest_drf.py already builds: "race#programnumber" (e.g. "1#5", "9#12").

Public API:
    apply_scratches(df, scratches) -> (df_filtered, summary)
    fetch_and_apply(df, track, race_date, year, manual_extra=None)
        -> (df_filtered, summary)

Typical use in run_pipeline.py:
    from ingest_drf import load_drf
    from apply_scratches import fetch_and_apply
    import config

    df = load_drf(config.DRF_FILE, config.TRACK, config.RACE_DATE, config.YEAR)
    df, summary = fetch_and_apply(
        df, config.TRACK, config.RACE_DATE, config.YEAR,
        manual_extra=config.MANUAL_SCRATCHES,
    )
    # df is now ready for features.engineer_features()
    # summary is a list of dicts you can log or attach to the run state
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd

from scratches import (
    ScratchEntry,
    get_scratches,
    merge_with_manual,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class ScratchApplyResult:
    """Summary of a scratch-filtering operation, suitable for logging."""
    rows_before: int
    rows_after: int
    rows_dropped: int
    scratches_requested: int    # how many ScratchEntry objects we received
    scratches_matched: int      # how many actually matched a DRF row
    unmatched: list[ScratchEntry]   # scratches that didn't match any DRF row
    dropped_keys: list[tuple[int, str]]   # (race, program) actually removed

    def as_dict(self) -> dict:
        """Serializable summary, e.g. for pipeline_state.json."""
        return {
            "rows_before": self.rows_before,
            "rows_after": self.rows_after,
            "rows_dropped": self.rows_dropped,
            "scratches_requested": self.scratches_requested,
            "scratches_matched": self.scratches_matched,
            "unmatched": [
                {"race": s.race, "program": s.program_number,
                 "horse": s.horse_name, "source": s.source}
                for s in self.unmatched
            ],
            "dropped": [
                {"race": r, "program": p} for r, p in self.dropped_keys
            ],
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def apply_scratches(
    df: pd.DataFrame,
    scratches: Iterable[ScratchEntry],
) -> tuple[pd.DataFrame, ScratchApplyResult]:
    """
    Remove scratched rows from an ingested DRF DataFrame.

    Parameters
    ----------
    df : DataFrame
        Output of ingest_drf.load_drf(). Must have columns 'Race' and
        'ProgramNumberifavailable' (or 'unfs', if already built).
    scratches : iterable of ScratchEntry
        The scratch list, e.g. from scratches.get_scratches().

    Returns
    -------
    df_filtered : DataFrame
        Same as `df` but with scratched rows removed.
    summary : ScratchApplyResult
        Counts and per-scratch match status for logging.
    """
    rows_before = len(df)
    scratches = list(scratches)

    if rows_before == 0:
        logger.warning("apply_scratches called on empty DataFrame; nothing to do.")
        return df, ScratchApplyResult(0, 0, 0, len(scratches), 0, list(scratches), [])

    # Build the scratch key set in the same form as 'unfs': "race#programnumber"
    # ingest_drf.py builds unfs as: Race(int).str + "#" + ProgramNumberifavailable.str
    # We have to match that format exactly, including the program-number string
    # as it appears in the DRF (which can be "5", "1A", etc.).
    scratch_keys = {
        f"{int(s.race)}#{str(s.program_number).strip()}"
        for s in scratches
    }

    # Make sure the merge column is present.  ingest_drf.py builds 'unfs' but
    # if we're called against a DataFrame that hasn't been through ingest yet,
    # rebuild it on the fly from Race + ProgramNumberifavailable.
    if "unfs" in df.columns:
        keys = df["unfs"].astype(str)
    elif {"Race", "ProgramNumberifavailable"}.issubset(df.columns):
        keys = (
            df["Race"].astype("Int64").astype(str)
            + "#"
            + df["ProgramNumberifavailable"].astype(str).str.strip()
        )
    else:
        raise ValueError(
            "DataFrame is missing the 'unfs' column AND the ('Race', "
            "'ProgramNumberifavailable') pair needed to rebuild it."
        )

    # Identify which DRF rows to drop and which scratches actually match
    drop_mask = keys.isin(scratch_keys)
    matched_keys = set(keys[drop_mask].unique())

    unmatched: list[ScratchEntry] = []
    for s in scratches:
        key = f"{int(s.race)}#{str(s.program_number).strip()}"
        if key not in matched_keys:
            unmatched.append(s)

    dropped_keys = sorted(
        {
            (int(k.split("#")[0]), k.split("#", 1)[1])
            for k in matched_keys
        },
        key=lambda x: (x[0], _prog_sort(x[1])),
    )

    df_filtered = df.loc[~drop_mask].copy().reset_index(drop=True)
    rows_after = len(df_filtered)
    rows_dropped = rows_before - rows_after

    summary = ScratchApplyResult(
        rows_before=rows_before,
        rows_after=rows_after,
        rows_dropped=rows_dropped,
        scratches_requested=len(scratches),
        scratches_matched=len(matched_keys),
        unmatched=unmatched,
        dropped_keys=dropped_keys,
    )

    # Log clearly — this is the kind of thing you'll want in pipeline.log.
    logger.info(
        "Scratches applied: %d requested, %d matched, %d rows dropped "
        "(%d -> %d). Field by race after scratches:",
        summary.scratches_requested,
        summary.scratches_matched,
        summary.rows_dropped,
        summary.rows_before,
        summary.rows_after,
    )
    if unmatched:
        for s in unmatched:
            logger.warning(
                "  Unmatched scratch: race %s #%s %s (source=%s) — "
                "not present in DRF",
                s.race, s.program_number, s.horse_name or "?", s.source,
            )

    # Per-race after-counts (helpful for visual sanity check)
    if "Race" in df_filtered.columns and not df_filtered.empty:
        per_race = df_filtered.groupby("Race").size().to_dict()
        for race in sorted(per_race):
            logger.info("    Race %s: %d horses", int(race), per_race[race])

    return df_filtered, summary


def fetch_and_apply(
    df: pd.DataFrame,
    track: str,
    race_date: str,
    year: Optional[str] = None,
    manual_extra: Optional[Iterable[tuple[int, str]]] = None,
    fail_on_fetch_error: bool = False,
) -> tuple[pd.DataFrame, ScratchApplyResult]:
    """
    One-shot helper: fetch scratches from Equibase RSS, optionally merge with
    manual extras from config.MANUAL_SCRATCHES, and filter the DRF.

    Parameters
    ----------
    df : DataFrame
        Output of ingest_drf.load_drf().
    track, race_date, year :
        Same as scratches.get_scratches().
    manual_extra : iterable of (race, program) tuples, optional
        Typically config.MANUAL_SCRATCHES. Merged with the RSS list before
        filtering.
    fail_on_fetch_error : bool
        If True, an Equibase fetch failure raises. If False (default), the
        pipeline proceeds with an empty RSS list (manual scratches still
        applied), and a warning is logged. This is the safer default for
        production scheduled runs.

    Returns
    -------
    Same shape as apply_scratches().
    """
    rss_scratches: list[ScratchEntry] = []
    try:
        rss_scratches = get_scratches(track, race_date, year)
    except Exception as e:
        msg = f"Equibase RSS fetch failed for {track} on {race_date}: {e}"
        if fail_on_fetch_error:
            raise
        logger.warning(msg + " — proceeding with manual scratches only.")

    if manual_extra is not None:
        all_scratches = merge_with_manual(rss_scratches, manual_extra)
    else:
        all_scratches = rss_scratches

    return apply_scratches(df, all_scratches)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
import re

_PROG_KEY_RE = re.compile(r"^(\d+)([A-Z]?)$")


def _prog_sort(prog: str) -> tuple:
    """Sort program numbers numerically, with letter suffix as tiebreaker."""
    m = _PROG_KEY_RE.match(prog.upper())
    if m:
        return (int(m.group(1)), m.group(2))
    return (999, prog)


def _minimal_drf_read(path: str) -> pd.DataFrame:
    """
    Lightweight DRF reader for the CLI smoke test only — bypasses the full
    ingest_drf preprocessing.  Reads just the columns the scratch filter
    needs: Race, ProgramNumberifavailable, HorseName.
    Column indices verified against a 1435-column BRISnet DRF file.
    """
    df = pd.read_csv(
        path, header=None, dtype=str, keep_default_na=False,
        encoding="latin-1",
    )
    # Column positions verified against CDX0508.DRF (May 2026 BRIS PP format)
    out = pd.DataFrame({
        "Race": pd.to_numeric(df.iloc[:, 2].str.strip(), errors="coerce").astype("Int64"),
        "ProgramNumberifavailable": df.iloc[:, 42].astype(str).str.strip(),
        "HorseName": df.iloc[:, 44].astype(str).str.strip().str.upper(),
    })
    out["unfs"] = out["Race"].astype(str) + "#" + out["ProgramNumberifavailable"]
    return out


# ---------------------------------------------------------------------------
# CLI: end-to-end test against a real DRF
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    p = argparse.ArgumentParser(
        description="Filter scratches out of a DRF (end-to-end smoke test)"
    )
    p.add_argument("track", help="Track code (BTSM or Equibase, e.g. KEE, CDX, CD)")
    p.add_argument("race_date", help="MMDD (e.g. 0508)")
    p.add_argument("--year", default=None, help="4-digit year (default: current)")
    p.add_argument("--drf", required=True, help="Path to DRF file")
    args = p.parse_args()

    # Use ingest_drf if available; otherwise fall back to a minimal CSV read
    # so this script is testable without the full pipeline package.
    try:
        from ingest_drf import load_drf
        # ingest_drf expects a 4-arg call: (path, track, date, year)
        df = load_drf(args.drf, args.track, args.race_date, args.year or "2026")
    except Exception as e:
        logger.warning("Falling back to minimal DRF read (%s)", e)
        df = _minimal_drf_read(args.drf)

    df_after, summary = fetch_and_apply(
        df, args.track, args.race_date, args.year,
    )

    print()
    print(f"Before: {summary.rows_before} horses")
    print(f"After:  {summary.rows_after} horses ({summary.rows_dropped} dropped)")
    print()
    if summary.unmatched:
        print(f"Unmatched scratches ({len(summary.unmatched)}):")
        for s in summary.unmatched:
            print(f"  Race {s.race} #{s.program_number} {s.horse_name}")
        sys.exit(1)
    print("All scratches matched DRF entries.")
