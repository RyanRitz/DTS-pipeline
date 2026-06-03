#!/usr/bin/env python3
"""
Quick single-card test runner.
=================================
Regenerates ONE card straight from a DRF already sitting in DRF_Downloads,
running the full current pipeline (ingest -> breed/jump/surface filters ->
scratches -> features -> score -> Excel + PDF). Use it to eyeball what the
latest code changes actually do on a real card, without waiting for the
scheduled run.

USAGE  (run from the FullAutomation folder, same env the pipeline uses):

    python test_card.py TDN 20260602
    python test_card.py IND 20260602
    python test_card.py MNR 20260602

By default it produces a PREVIEW (no scratches applied), which is the fastest
way to see formatting/value changes. The output lands in your output/ folder as
    <YYYYMMDD>_<TRACK>_PREVIEW.xlsx   and   <YYYYMMDD>_<TRACK>_PREVIEW.pdf

Note: a PREVIEW scores the FULL field (no scratches removed), so the green/gold
shading is computed over every entrant. To reproduce a FINAL exactly you'd need
the same scratch list the scheduled run used — but for checking the RC summary,
turns, multi-race wagers, state-bred wording, and the value math, the preview is
all you need.
"""
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        print("Usage: python test_card.py <TRACK> <YYYYMMDD>")
        return 1

    track = sys.argv[1].upper()
    date = sys.argv[2]

    # Imported here (not at top) so the usage message prints even if a heavy
    # dependency is missing.
    import pdf as _pdfmod
    import run_pipeline as _rpmod
    from run_pipeline import run_scoring, generate_pdf, SKIP_CARD

    # ── Loaded-code sanity check ─────────────────────────────────────────
    # Confirms Python is running the CURRENT edited source, not a stale
    # __pycache__/*.pyc. If any of these say MISSING, delete the __pycache__
    # folder next to these files and re-run.
    print("[test_card] Loaded modules:")
    print(f"    pdf.py         -> {_pdfmod.__file__}")
    print(f"    run_pipeline.py -> {_rpmod.__file__}")
    checks = {
        "pdf: multi-race wager price-strip": hasattr(_pdfmod, "_WAGER_PRICE_RE"),
        "run_pipeline: RC state abbrev":     hasattr(_rpmod, "_rc_states"),
        "run_pipeline: turns wiring":        "get_turns" in open(_rpmod.__file__,
                                              encoding="utf-8", errors="replace").read(),
    }
    for name, ok in checks.items():
        print(f"    [{'OK ' if ok else 'MISSING'}] {name}")
    if not all(checks.values()):
        print("\n[test_card] STALE CODE DETECTED — delete __pycache__ and re-run:")
        print("    (Windows)  rmdir /s /q __pycache__")
        print("    (or)       del /s /q *.pyc")
        return 1
    print()

    print(f"\n[test_card] Scoring {track} {date}  (PREVIEW — no scratches)\n")
    result = run_scoring(track, date)            # scratches=None => preview
    if result is SKIP_CARD:
        print("[test_card] Card skipped: non-thoroughbred / non-flat, nothing to score.")
        return 0
    if result is None:
        print(
            "[test_card] No result. Common causes:\n"
            f"  - DRF not found: expected DRF_Downloads/{date}_{track}_DRS.DRF\n"
            "  - Track not in config.DTS_TRACK_WHITELIST\n"
            "  - All races filtered out (non-TB / jumps / unsupported surface)\n"
        )
        return 1

    print(f"[test_card] Excel: {result.out_path}")
    pdf_path = generate_pdf(result, is_final=False)
    if pdf_path:
        print(f"[test_card] PDF:   {pdf_path}")
        print("\n[test_card] Done — open the PDF above to review the changes.")
        return 0
    print("[test_card] PDF generation returned no path — check pipeline.log.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
