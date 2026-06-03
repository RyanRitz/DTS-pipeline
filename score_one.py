"""
DTS Pipeline — Manual one-shot scoring (FINAL mode)
=====================================================
Forces a FINAL-style scoring run for a single (track, race_date) pair,
bypassing the time-window checks that gate the live orchestrator.

Use case: validation. SAS comparison. Re-running a card after-the-fact
with the scratch list as it actually played out.

This script does EVERYTHING the live orchestrator's publish_final()
path does, in the same order, with the same data sources:

    1. Pull scratches from Equibase RSS (live, exactly like race day)
    2. Optionally fetch Equibase track status (first post, conditions)
    3. Filter the DRF (apply_scratches)
    4. Engineer features (race-level normalization)
    5. Run scoring (KEE models via the model registry)
    6. Write Excel:  output/YYYYMMDD_TRACK_FINAL.xlsx

Usage:
    python score_one.py CD 20260509
    python score_one.py KEE 20260408 --no-track-status
    python score_one.py CD 20260509 --label VALIDATION
        (writes 20260509_CD_VALIDATION.xlsx instead of FINAL.xlsx
         — useful if you want to keep the auto-FINAL output separately)

Notes:
- This always writes a FINAL-labeled Excel. It does NOT touch
  pipeline_state.json, so the live orchestrator will still consider
  the card "not yet finalized" and could publish its own version
  later when the FINAL window opens.
- Equibase RSS scratches are LIVE — they reflect the current state of
  the feed AT THE MOMENT YOU RUN THIS, not the state at any historical
  time. Re-running an old card may pull scratches that have since been
  reversed or amended. For reproducibility-critical validation, pin
  the scratch list to a file and edit this to read from it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Make sure top-level modules resolve regardless of cwd
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ── Logging setup (mirrors run_pipeline.py format so logs feel familiar) ──
log = logging.getLogger("score_one")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def _load_scratches_csv(path: Path) -> list[dict]:
    """
    Load scratches from a CSV file for historical validation.

    Required columns: race, program
    Optional columns: horse, reason

    Returns a list of dicts in the same shape pull_scratches() returns:
        [{"race": 1, "program": "5", "horse": "TEMPORARILYFOREVER",
          "reason": "PrivVet-Illness", "source": "file:<filename>"}, ...]

    Skips rows where race isn't a positive int or program is empty. Logs
    every skipped row so operators can fix typos in the CSV.
    """
    import csv

    out: list[dict] = []
    skipped = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            log.error(f"  scratches CSV {path.name} has no header row")
            return []
        # Normalize header names (lowercase, strip)
        reader.fieldnames = [
            (c.lower().strip() if c else c) for c in reader.fieldnames
        ]
        if "race" not in reader.fieldnames or "program" not in reader.fieldnames:
            log.error(
                f"  scratches CSV {path.name} missing required columns; "
                f"found {reader.fieldnames}, need at least 'race' and 'program'"
            )
            return []

        for row_num, row in enumerate(reader, start=2):  # start=2 to account for header
            race_raw = (row.get("race") or "").strip()
            prog_raw = (row.get("program") or "").strip()
            if not race_raw or not prog_raw:
                # Allow blank lines without warning
                if race_raw or prog_raw:
                    log.warning(
                        f"  scratches CSV row {row_num}: missing race or "
                        f"program, skipping ({row})"
                    )
                    skipped += 1
                continue
            try:
                race_int = int(race_raw)
                if race_int < 1:
                    raise ValueError
            except ValueError:
                log.warning(
                    f"  scratches CSV row {row_num}: race={race_raw!r} not a "
                    f"positive int, skipping"
                )
                skipped += 1
                continue

            out.append({
                "race":    race_int,
                "program": prog_raw,
                "horse":   (row.get("horse") or "").strip().upper() or None,
                "reason":  (row.get("reason") or "").strip() or None,
                "source":  f"file:{path.name}",
            })

    if skipped:
        log.warning(f"  scratches CSV: skipped {skipped} bad row(s)")
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Manually score one track/date — bypasses time-window gates."
    )
    parser.add_argument("track", help="Track code, e.g. CD, KEE, SAR")
    parser.add_argument("race_date", help="YYYYMMDD")
    parser.add_argument(
        "--label", default="FINAL",
        help="Label for output filename (default: FINAL). e.g. VALIDATION",
    )
    parser.add_argument(
        "--no-track-status", action="store_true",
        help="Skip the Equibase Selenium fetch (faster, no track conditions)",
    )
    parser.add_argument(
        "--no-scratches", action="store_true",
        help="Don't pull scratches — score the raw DRF (PREVIEW-equivalent)",
    )
    parser.add_argument(
        "--scratches-file", default=None, metavar="PATH",
        help="Load scratches from a CSV file instead of the Equibase RSS feed. "
             "Use for historical validation, since Equibase RSS only serves "
             "today's scratches. CSV must have a header row with at least "
             "'race' and 'program' columns; an optional 'horse' column will "
             "be logged for sanity. When this flag is used, no RSS fetch is "
             "attempted. Mutually exclusive with --no-scratches.",
    )
    parser.add_argument(
        "--drf", default=None, metavar="PATH",
        help="Explicit DRF file path to score. By default the script searches "
             "DRF_Downloads/ for the file matching the orchestrator's naming "
             "convention; this flag bypasses that and points directly at any "
             "DRF file you specify. Use this for validation against a known-"
             "good file when DRF_Downloads/ has a stale copy.",
    )
    args = parser.parse_args()

    track     = args.track.upper()
    race_date = args.race_date
    label     = args.label.upper()

    if not (race_date.isdigit() and len(race_date) == 8):
        log.error(f"race_date must be YYYYMMDD, got: {race_date!r}")
        sys.exit(2)

    drf_override = None
    if args.drf:
        drf_override = Path(args.drf).resolve()
        if not drf_override.exists():
            log.error(f"--drf file does not exist: {drf_override}")
            sys.exit(2)
        log.info(f"DRF override: {drf_override}")

    log.info("=" * 60)
    log.info(f"  Manual scoring run: {track} {race_date} (label={label})")
    log.info("=" * 60)

    # Validate mutually-exclusive flags
    if args.no_scratches and args.scratches_file:
        log.error("--no-scratches and --scratches-file are mutually exclusive")
        sys.exit(2)

    # ── 1. Build the scratch list ────────────────────────────────────────
    # Three sources, in priority order:
    #   1. --scratches-file: load from CSV (for historical validation)
    #   2. --no-scratches: skip entirely (PREVIEW-equivalent)
    #   3. default: pull live from Equibase RSS (today only)
    scratches = []
    if args.no_scratches:
        log.info("Skipping scratch fetch (--no-scratches)")
    elif args.scratches_file:
        scratches_file = Path(args.scratches_file).resolve()
        if not scratches_file.exists():
            log.error(f"--scratches-file does not exist: {scratches_file}")
            sys.exit(2)
        log.info(f"Loading scratches from file: {scratches_file}")
        scratches = _load_scratches_csv(scratches_file)
        if scratches:
            log.info(f"Scratches loaded from file: {len(scratches)}")
            for s in scratches:
                log.info(f"  Race {s['race']:>2} #{s['program']:<3} "
                         f"{s.get('horse','?'):<25} {s.get('reason','')}")
        else:
            log.warning(
                f"Scratches file {scratches_file.name} contained no valid "
                f"rows — proceeding as if --no-scratches was given."
            )
    else:
        from run_pipeline import pull_scratches
        scratches = pull_scratches(track, race_date)
        if scratches:
            log.info(f"Scratches fetched: {len(scratches)}")
            for s in scratches:
                log.info(f"  Race {s['race']:>2} #{s['program']:<3} "
                         f"{s.get('horse','?'):<25} {s.get('reason','')}")
        else:
            log.info("Scratches fetched: 0 (no scratches reported, "
                     "or feed has no entries for this date/track)")

    # ── 2. Track status (Equibase via Selenium) ──────────────────────────
    track_status = None
    if not args.no_track_status:
        from track_status import get_track_status
        year = race_date[:4]
        mmdd = race_date[4:]
        try:
            track_status = get_track_status(track, mmdd, year)
            if track_status.fetch_ok:
                log.info(
                    f"Track status: first_post={track_status.first_post}, "
                    f"dirt={track_status.dirt_condition or '?'}, "
                    f"turf={track_status.turf_condition or '?'}"
                )
            else:
                log.warning(
                    f"Track status fetch failed: {track_status.fetch_error or 'unknown'}"
                )
        except Exception as e:
            log.warning(f"Track status fetch error (non-fatal): {e}")
            track_status = None
    else:
        log.info("Skipping track status fetch (--no-track-status)")

    # ── 3-6. Run scoring chain via the same function the orchestrator uses ──
    from run_pipeline import run_scoring
    log.info("Starting scoring chain...")
    result = run_scoring(
        track=track,
        race_date=race_date,
        scratches=scratches if scratches else None,
        track_status=track_status,
        drf_path_override=drf_override,
    )

    if result is None:
        log.error("Scoring failed. See messages above.")
        sys.exit(1)

    out_path = result.out_path

    # ── 7. Optionally rename to use a non-FINAL label ───────────────────
    # run_scoring auto-names with PREVIEW or FINAL based on whether
    # `scratches` was None or not. If the user requested a custom label,
    # rename the produced file.
    auto_label = "FINAL" if scratches else "PREVIEW"
    if label != auto_label:
        out_dir  = out_path.parent
        new_path = out_dir / f"{race_date}_{track}_{label}.xlsx"
        if out_path != new_path:
            if new_path.exists():
                new_path.unlink()
            out_path.rename(new_path)
            out_path = new_path

    log.info("=" * 60)
    log.info(f"  DONE: {out_path}")
    log.info(f"  Full scored DataFrame: {len(result.scored_df)} rows × "
             f"{len(result.scored_df.columns)} cols available for downstream PDF")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
