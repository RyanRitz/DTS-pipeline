"""
DTS Pipeline — make_sample_pdf.py
=================================
Render a sheet PDF for a single (track, race_date) OFFLINE, for use as a
marketing / documentation sample. Does NOT upload anything.

Unlike score_one.py (which stops at the Excel), this runs the same scoring
chain and then calls run_pipeline.generate_pdf(), so the PDF uses the CURRENT
pdf.py header and styling.

Fully offline: no Brisnet download, no Equibase scratch feed, no Selenium
track-status fetch. Track conditions and first post are supplied by you.

Usage:
    python make_sample_pdf.py SAR 20260703 --dirt Fast --turf Firm --first-post 13:00

    # no conditions band (renders whatever the FINAL path shows without status)
    python make_sample_pdf.py SAR 20260703 --no-conditions

    # point at a DRF explicitly
    python make_sample_pdf.py SAR 20260703 --drf DRF_Downloads/20260703_SAR_DRS.DRF

Output:
    output/YYYYMMDD_TRACK_FINAL.pdf
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("make_sample_pdf")


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a sample sheet PDF offline (no upload).")
    ap.add_argument("track", help="Track code, e.g. SAR")
    ap.add_argument("race_date", help="YYYYMMDD")
    ap.add_argument("--drf", default=None, help="Explicit DRF path (default: auto-discover in DRF_Downloads/)")
    ap.add_argument("--dirt", default=None, help='Dirt condition, e.g. "Fast"')
    ap.add_argument("--turf", default=None, help='Turf condition, e.g. "Firm"')
    ap.add_argument("--first-post", dest="first_post", default=None, help='First post, 24h "HH:MM", e.g. 13:00')
    ap.add_argument("--no-conditions", action="store_true", help="Do not attach a TrackStatus at all")
    args = ap.parse_args()

    track = args.track.upper()
    race_date = args.race_date
    if not (race_date.isdigit() and len(race_date) == 8):
        log.error("race_date must be YYYYMMDD, got %r", race_date)
        return 2

    drf_override = None
    if args.drf:
        drf_override = Path(args.drf).resolve()
        if not drf_override.exists():
            log.error("--drf not found: %s", drf_override)
            return 2

    # Build an offline TrackStatus so the FINAL header can show conditions
    # without hitting Equibase/Selenium.
    track_status = None
    if not args.no_conditions:
        from track_status import TrackStatus

        d = datetime.strptime(race_date, "%Y%m%d").date()
        fp = None
        if args.first_post:
            hh, mm = args.first_post.split(":")
            fp = datetime(d.year, d.month, d.day, int(hh), int(mm))

        track_status = TrackStatus(
            track=track,
            race_date=d,
            first_post=fp,
            dirt_condition=args.dirt,
            turf_condition=args.turf,
            fetch_ok=True,
            source_url="(offline sample)",
        )
        log.info("Offline TrackStatus: first_post=%s dirt=%s turf=%s", fp, args.dirt, args.turf)

    from run_pipeline import run_scoring, generate_pdf, SKIP_CARD

    log.info("Scoring %s %s (offline, no scratch fetch)...", track, race_date)
    result = run_scoring(
        track=track,
        race_date=race_date,
        scratches=None,            # offline: no Equibase RSS
        track_status=track_status,
        drf_path_override=drf_override,
    )

    if result is SKIP_CARD:
        log.info("Card skipped (non-thoroughbred / non-flat). Nothing to render.")
        return 0
    if result is None:
        log.error("Scoring failed. See messages above.")
        return 1

    log.info("Scored %d rows. Rendering PDF...", len(result.scored_df))

    # is_final=True so the header shows the conditions band rather than "TBD".
    pdf_path = generate_pdf(result, is_final=True)
    if not pdf_path:
        log.error("PDF generation failed.")
        return 1

    log.info("=" * 60)
    log.info("  PDF: %s", pdf_path)
    log.info("  (nothing was uploaded)")
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
