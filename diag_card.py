#!/usr/bin/env python3
"""
Diagnostic single-card runner — writes a full debug log to a file.
====================================================================
Same as test_card.py but captures EVERYTHING to a log file you can upload:
  - which pdf.py / run_pipeline.py files are loaded, and whether they contain
    the latest code (detects a stale __pycache__),
  - the entire pipeline's verbose logging,
  - and — the important part — the exact race-header data (description, turns,
    multi-race wagers) being handed to the PDF renderer for each race.

USAGE (from the FullAutomation folder, same env the pipeline uses):

    python diag_card.py TDN 20260602

Then upload the log file it prints at the end:
    output\\diag_TDN_20260602.log
"""
import sys
import logging
from pathlib import Path
from datetime import datetime


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: python diag_card.py <TRACK> <YYYYMMDD>")
        return 1
    track = sys.argv[1].upper()
    date = sys.argv[2]

    log_path = Path(__file__).parent / "output" / f"diag_{track}_{date}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Capture the whole pipeline's logging (it's verbose) into the file.
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(fh)
    log = logging.getLogger("diag")

    log.info("=" * 70)
    log.info("DTS diagnostic run: %s %s  @ %s", track, date, datetime.now())
    log.info("=" * 70)

    # ── Which code is actually loaded? (reliable stale-.pyc detection) ──
    import pdf as _pdf
    import run_pipeline as _rp
    log.info("pdf.py loaded from      : %s", _pdf.__file__)
    log.info("pdf.py bytecode cache   : %s", getattr(_pdf, "__cached__", "?"))
    log.info("run_pipeline loaded from: %s", _rp.__file__)
    log.info("run_pipeline cache      : %s", getattr(_rp, "__cached__", "?"))
    checks = {
        "pdf._WAGER_PRICE_RE (wager fix)":      hasattr(_pdf, "_WAGER_PRICE_RE"),
        "run_pipeline._rc_states (state abbr)": hasattr(_rp, "_rc_states"),
        "run_pipeline._STATE_ABBR":             hasattr(_rp, "_STATE_ABBR"),
    }
    for name, ok in checks.items():
        log.info("LOADED-CODE CHECK [%s] %s", "OK " if ok else "STALE/MISSING", name)
    if not all(checks.values()):
        log.error("STALE BYTECODE: loaded module is missing the latest code. "
                  "Delete __pycache__ and re-run.")

    # ── Wrap pdf.generate_pdf to log the exact DataFrame it receives ────
    # This is the key diagnostic: it shows, per race, what run_pipeline
    # actually put in the columns the renderer reads.
    _orig_pdf = _pdf.generate_pdf

    def _wrapped_generate_pdf(df, *args, **kwargs):
        try:
            log.info("--- pdf.generate_pdf received a DataFrame ---")
            log.info("pdf_df columns (%d): %s", len(df.columns), list(df.columns))
            if "race" in df.columns:
                for r in sorted({int(x) for x in df["race"].dropna().tolist()}):
                    sub = df[df["race"] == r]

                    def g(col):
                        return (sub[col].iloc[0]
                                if col in sub.columns and len(sub) else "<COLUMN MISSING>")

                    log.info("  Race %s: rc_summary=%r | turns=%r | wagers=%r",
                             r, g("race_conditions_summary"), g("turns"), g("wagers"))
        except Exception:
            log.exception("diag wrapper failed while inspecting pdf_df")
        return _orig_pdf(df, *args, **kwargs)

    _pdf.generate_pdf = _wrapped_generate_pdf

    # ── Run the pipeline ────────────────────────────────────────────────
    try:
        from run_pipeline import run_scoring, generate_pdf, SKIP_CARD
        log.info("Calling run_scoring(%s, %s) [PREVIEW, no scratches]...", track, date)
        result = run_scoring(track, date)
        if result is SKIP_CARD:
            log.info("run_scoring skipped this card (non-thoroughbred / non-flat, nothing to score).")
            return 0
        if result is None:
            log.error("run_scoring returned None (no DRF / not whitelisted / all filtered).")
            return 1
        log.info("run_scoring OK. Excel: %s", result.out_path)
        pdf_path = generate_pdf(result, is_final=False)
        log.info("generate_pdf returned: %s", pdf_path)
    except Exception:
        log.exception("PIPELINE FAILED with an exception")
        return 1
    finally:
        log.info("=" * 70)
        log.info("Diagnostic log complete.")
        for h in list(root.handlers):
            h.flush()
        print(f"\n[diag_card] Diagnostic log written to:\n    {log_path}\n"
              f"[diag_card] Please upload that .log file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
