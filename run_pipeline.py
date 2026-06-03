"""
run_pipeline.py
===============
DTS daily pipeline orchestrator. Runs every 30 minutes via Task Scheduler.

State machine per (race_date, track):
  PREVIEW SHEET  — published as soon as DRF arrives. No scratches.
                   Re-published if DRF mtime changes (BRISnet updated the file).
  FINAL SHEET    — published 30-60 min before first post on race day.
                   Includes scratches, jockey changes, surface/condition changes.

Each tick of this script:
  1. Load pipeline_state.json
  2. Scan DRF_Downloads/ for all DRF files
  3. For each DRF file:
       a. Always check: should preview be published or republished?
       b. If today == race_date: should final be published?
  4. Save pipeline_state.json
  5. Exit (errors emailed by run_pipeline.bat wrapper)

Idempotent: safe to run repeatedly. Catch up automatically if a tick is missed.
"""

import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, date
from pathlib import Path

# Make sure THIS file's directory is the first place Python looks for modules.
# Without this, importing run_pipeline.py from a different cwd (e.g. via Task
# Scheduler with a working dir not set) would fail to find drf_schema, score,
# features, etc. — they're all top-level files alongside this one.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ── DTS modules ─────────────────────────────────────────────────────────────
# scratches.py + apply_scratches.py + track_status.py live alongside this file.
from scratches import get_scratches, merge_with_manual, ScratchEntry
from track_status import get_track_status, TrackStatus

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent.resolve()
DRF_DIR         = Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\DRF_Downloads")
STATE_FILE      = BASE_DIR / "pipeline_state.json"
LOG_FILE        = BASE_DIR / "pipeline.log"
KEEP_DAYS       = 7  # Prune state older than this

# Publish anchors for FINAL sheet — minutes BEFORE first post at which we
# attempt to publish. Equibase typically refreshes scratches and conditions
# ~1 hour before first post, so:
#   T-60: first poll once Equibase data is likely available
#   T-30: catch any late changes
#   T-0:  final snapshot at first post
#
# Each anchor allows a +/- tolerance window in minutes. With a 30-min Task
# Scheduler tick, +/-15 min covers every first-post time without gaps and
# without anchor overlap.
#
# Window-gating uses the DRF heuristic for first-post (free), not Equibase
# (Selenium fetch). Equibase is only consulted once we're inside a window.
FINAL_ANCHORS_MIN_BEFORE = (60, 30, 0)    # T-60, T-30, T-0
FINAL_ANCHOR_TOLERANCE_MIN = 15           # +/-15 minutes around each anchor

# In-card anchors: re-check each race ~20 minutes before its post time. With
# 30-min Task Scheduler ticks and +/-15 min tolerance, every race gets one
# poll opportunity. State key: "R<race_number>" (e.g. "R5").
IN_CARD_ANCHOR_MIN_BEFORE_RACE = 20

# Once `now > last_race_post + CARD_COMPLETE_GRACE_MIN`, stop polling that
# track for the day — racing is over, no more useful changes will arrive.
CARD_COMPLETE_GRACE_MIN = 5

# Per-tick cap on the number of tracks we'll do a Selenium fetch for. Equibase
# can hang for 2-5 minutes when Imperva is fussy, so we cap fetches per tick
# to keep the pipeline from running over its 30-min slot. Tracks deferred to
# the next tick still hit their anchor window (+/-15 min covers the wait).
MAX_FETCHES_PER_TICK = 4

# ── Logging ──────────────────────────────────────────────────────────────────
# Rotate at 10 MB, keep last 3 archives (pipeline.log.1 .. pipeline.log.3).
# Rotation only kicks in when the file *next* exceeds 10 MB, so the current
# 18-MB pipeline.log won't shrink retroactively — delete or archive it
# manually if you want a clean start.
LOG_MAX_BYTES   = 10 * 1024 * 1024     # 10 MB
LOG_BACKUP_COUNT = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[RotatingFileHandler(LOG_FILE,
                                  maxBytes=LOG_MAX_BYTES,
                                  backupCount=LOG_BACKUP_COUNT,
                                  encoding="utf-8"),
              logging.StreamHandler()],
)
log = logging.getLogger(__name__)


# ── State management ─────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load pipeline state, creating empty structure if missing."""
    if not STATE_FILE.exists():
        return {"last_run": None, "published": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"State file corrupt, starting fresh: {e}")
        return {"last_run": None, "published": {}}


def save_state(state: dict) -> None:
    """Save state, pruning entries older than KEEP_DAYS."""
    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    state["published"] = {
        d: tracks for d, tracks in state.get("published", {}).items()
        if d >= cutoff
    }
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── DRF file discovery ──────────────────────────────────────────────────────

def parse_drf_filename(path: Path) -> tuple[str, str] | None:
    """
    DRF filename convention: YYYYMMDD_TRACK_DRS.DRF
    Returns (race_date, track) or None.
    """
    name = path.stem  # strip .DRF
    parts = name.split("_")
    if len(parts) < 2:
        return None
    date_str = parts[0]
    track    = parts[1]
    if len(date_str) != 8 or not date_str.isdigit():
        return None
    return date_str, track


def discover_drf_files() -> list[dict]:
    """
    Return list of {path, race_date, track, mtime} for every DRF in DRF_DIR
    whose race date is today or in the future (a 1-day grace window catches
    finals running just after midnight ET).

    If config.DTS_TRACK_WHITELIST is set (a set of track codes), DRFs from
    tracks not in the whitelist are excluded. This saves several minutes per
    pipeline tick by skipping Equibase / Selenium fetches for tracks DTS
    doesn't intend to publish. Set DTS_TRACK_WHITELIST = None in config.py
    to disable filtering and process every DRF on disk (legacy behavior).

    Past-date DRFs are skipped entirely. This protects against scenarios
    where pipeline_state.json gets cleared (manual reset, migration, etc.)
    while past-date DRFs still sit on disk — without this filter the
    pipeline would re-score, re-render, and re-upload PDFs for races that
    already ran, polluting the public site with stale "previews".
    """
    if not DRF_DIR.exists():
        log.warning(f"DRF directory does not exist: {DRF_DIR}")
        return []

    # Resolve whitelist from config (treat missing attribute as "no filter")
    try:
        import config
        whitelist = getattr(config, "DTS_TRACK_WHITELIST", None)
    except Exception:
        whitelist = None

    # Cutoff: race dates strictly before yesterday are considered past.
    # Using yesterday (not today) gives the pipeline a one-day grace window
    # so finals being processed just after a card ends — including past
    # midnight ET — still get through.
    from datetime import date, timedelta
    cutoff_str = (date.today() - timedelta(days=1)).strftime("%Y%m%d")

    out = []
    skipped = 0
    skipped_tracks: set[str] = set()
    past_count = 0
    for p in DRF_DIR.glob("*.DRF"):
        parsed = parse_drf_filename(p)
        if not parsed:
            continue
        race_date, track = parsed
        if race_date < cutoff_str:
            past_count += 1
            continue
        if whitelist is not None and track.upper() not in whitelist:
            skipped += 1
            skipped_tracks.add(track.upper())
            continue
        out.append({
            "path":      p,
            "race_date": race_date,
            "track":     track,
            "mtime":     p.stat().st_mtime,
        })

    if past_count:
        log.info(
            f"[*] {past_count} DRF file(s) skipped — race date older than "
            f"{cutoff_str} (move to DRF_Downloads/archive/ for cleanliness)"
        )
    if skipped:
        log.info(
            f"[*] {skipped} DRF file(s) skipped — not in DTS_TRACK_WHITELIST "
            f"(tracks: {sorted(skipped_tracks)})"
        )
    return out


# ── First-post extraction from DRF ───────────────────────────────────────────
#
# Reads BRISnet field #1418 ("Post Time, Pacific military time", documented
# in BRISnet's DRF schema). In 0-based column indexing this is column 1417.
#
# Files in DRF_Downloads/ may arrive in two formats:
#   1. Plain CSV (e.g. CDX0509.DRF after manual extraction)
#   2. ZIP-wrapped (e.g. 20260512_PEN_DRS.DRF — BRISnet's default delivery)
#
# We detect format by the ZIP magic bytes "PK\x03\x04" at file start, then
# either read directly or unzip to memory. Encoding is Latin-1 to match
# ingest_drf.load_drf() — BRISnet uses Latin-1 for accented horse/jockey
# names; UTF-8 would fail on those rows.
#
# Conversion: Pacific Time -> Eastern Time = +3 hours. Uses timedelta so
# day rollover (e.g. PT 22:00 -> ET 01:00 next day) handles cleanly.

POST_TIME_COL_PACIFIC = 1417   # 0-based; BRISnet field #1418
RACE_NUMBER_COL       = 2      # 0-based; BRISnet field #3


def _open_drf_text(drf_path: Path):
    """
    Return an open text iterator over a DRF file's CSV contents, whether
    the file on disk is plain CSV or ZIP-wrapped. Caller is responsible
    for closing.

    Matches the encoding ("latin-1") and ZIP-detection approach used by
    ingest_drf.load_drf() so both code paths see identical bytes.
    """
    import io
    import zipfile

    with open(drf_path, "rb") as f:
        magic = f.read(4)

    if magic[:2] == b"PK":
        # ZIP-wrapped — extract the inner .DRF to memory and wrap in a
        # text reader.
        zf = zipfile.ZipFile(drf_path)
        try:
            names = zf.namelist()
            if not names:
                zf.close()
                raise ValueError(f"DRF zip is empty: {drf_path}")
            inner = next((n for n in names if n.upper().endswith(".DRF")), names[0])
            data = zf.read(inner)
        finally:
            zf.close()
        return io.TextIOWrapper(io.BytesIO(data), encoding="latin-1",
                                errors="replace", newline="")
    else:
        return open(drf_path, "r", encoding="latin-1", errors="replace")


def _pt_hhmm_to_et(race_date_str: str, hhmm: str) -> datetime | None:
    """
    Convert a 4-digit Pacific HHMM string ("1330") into a naive Eastern
    Time datetime on the given race date (YYYYMMDD).
    Returns None on malformed input.
    """
    if not (hhmm.isdigit() and len(hhmm) == 4):
        return None
    hh, mm = int(hhmm[:2]), int(hhmm[2:])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    try:
        race_date = datetime.strptime(race_date_str, "%Y%m%d").date()
    except (ValueError, TypeError):
        return None
    pt_dt = datetime.combine(race_date, datetime.min.time()).replace(
        hour=hh, minute=mm,
    )
    return pt_dt + timedelta(hours=3)  # PT -> ET


def get_first_post(drf_path: Path) -> datetime | None:
    """
    Extract first-post time from a DRF file.

    Reads BRISnet field #1418 (Post Time, Pacific military time HHMM) from
    the first horse's row, converts Pacific -> Eastern, returns naive
    datetime. Handles both plain-CSV and ZIP-wrapped DRFs.

    Returns the race-date + HH:MM (ET) as a naive datetime, or None on
    parse failure.
    """
    try:
        parsed = parse_drf_filename(drf_path)
        if parsed is None:
            log.warning(f"  {drf_path.name}: filename does not match "
                        f"YYYYMMDD_TRACK_*.DRF pattern; skipping")
            return None
        race_date_str = parsed[0]
        with _open_drf_text(drf_path) as f:
            first_line = f.readline()
        cols = first_line.split(",")
        if len(cols) <= POST_TIME_COL_PACIFIC:
            log.warning(
                f"  {drf_path.name}: only {len(cols)} columns "
                f"(need at least {POST_TIME_COL_PACIFIC + 1}); skipping"
            )
            return None
        hhmm = cols[POST_TIME_COL_PACIFIC].strip().strip('"')
        et_dt = _pt_hhmm_to_et(race_date_str, hhmm)
        if et_dt is None:
            log.warning(
                f"  {drf_path.name}: post-time column 1417 has invalid value "
                f"{hhmm!r}"
            )
        return et_dt
    except Exception as e:
        log.warning(f"Could not parse first-post from {drf_path.name}: {e}")
        return None


def get_all_race_posts(drf_path: Path) -> dict[int, datetime] | None:
    """
    Extract per-race post times from a DRF file.

    Returns a dict {race_number: post_time_ET} for every race on the card,
    or None if parsing fails / no races found. Handles both plain-CSV and
    ZIP-wrapped DRFs.

    One DRF row per horse; we group by the Race column (col 2 per BRISnet
    schema) and take the first valid post-time per race from the documented
    Pacific HHMM column (#1418 = col 1417).
    """
    try:
        parsed = parse_drf_filename(drf_path)
        if parsed is None:
            log.warning(f"  {drf_path.name}: filename does not match "
                        f"YYYYMMDD_TRACK_*.DRF pattern; skipping")
            return None
        race_date_str = parsed[0]

        posts: dict[int, datetime] = {}
        with _open_drf_text(drf_path) as f:
            for line in f:
                cols = line.split(",")
                if len(cols) <= POST_TIME_COL_PACIFIC:
                    continue

                race_raw = cols[RACE_NUMBER_COL].strip().strip('"')
                try:
                    race_num = int(float(race_raw))
                    if race_num < 1:
                        continue
                except (ValueError, TypeError):
                    continue

                if race_num in posts:
                    # Already have a post time for this race; skip the rest
                    # of its horses.
                    continue

                hhmm = cols[POST_TIME_COL_PACIFIC].strip().strip('"')
                et_dt = _pt_hhmm_to_et(race_date_str, hhmm)
                if et_dt is not None:
                    posts[race_num] = et_dt

        if not posts:
            log.warning(
                f"  {drf_path.name}: no valid race posts found "
                f"(checked column {POST_TIME_COL_PACIFIC})"
            )
            return None
        return posts

    except Exception as e:
        log.warning(f"Could not parse per-race posts from {drf_path.name}: {e}")
        return None


def _get_final_first_post(drf: dict) -> tuple[datetime | None, TrackStatus | None]:
    """
    Get first-post time for the RACE-DAY (FINAL) path.

    Tries Equibase via track_status.get_track_status() first, then falls
    back to the DRF heuristic. Caches the result on the `drf` dict so a
    single tick that calls this twice (once to gate the window, once to
    record state) only triggers ONE Selenium fetch.

    Returns
    -------
    (first_post, track_status)
        first_post   : datetime or None
        track_status : TrackStatus or None  (None if Equibase fetch failed
                       AND we fell back to DRF — no conditions in that case)
    """
    # Per-tick memoization
    if "_final_first_post_cache" in drf:
        return drf["_final_first_post_cache"]

    track     = drf["track"]
    race_date = drf["race_date"]   # "YYYYMMDD"

    # YYYYMMDD -> (YYYY, MMDD)
    if len(race_date) == 8 and race_date.isdigit():
        year = race_date[:4]
        mmdd = race_date[4:]
    else:
        year, mmdd = None, None

    fp:  datetime | None    = None
    ts:  TrackStatus | None = None

    # --- Equibase (primary) ---
    if mmdd:
        try:
            ts = get_track_status(track, mmdd, year)
            if ts.fetch_ok and ts.first_post:
                fp = ts.first_post
                # %I gives leading zero (e.g. "02:45 PM"); strip it for
                # display. Avoid %-I (Linux-only) and %#I (Windows-only)
                # for cross-platform safety.
                fp_display = fp.strftime("%I:%M %p ET").lstrip("0")
                log.info(
                    f"  first-post for {track} {race_date}: "
                    f"{fp_display} (source=equibase, "
                    f"dirt={ts.dirt_condition or '?'}, "
                    f"turf={ts.turf_condition or '?'})"
                )
            else:
                err = ts.fetch_error or "unknown"
                log.info(
                    f"  Equibase first-post unavailable for {track} {race_date} "
                    f"({err}) — falling back to DRF heuristic"
                )
        except Exception as e:
            log.warning(
                f"  track_status fetch failed for {track} {race_date}: {e} "
                f"— falling back to DRF heuristic"
            )

    # --- DRF heuristic (fallback) ---
    if fp is None:
        fp = get_first_post(drf["path"])
        if fp is not None:
            log.info(
                f"  first-post for {track} {race_date}: "
                f"{fp.strftime('%H:%M')} (source=drf-heuristic)"
            )

    drf["_final_first_post_cache"] = (fp, ts)
    return fp, ts


# ── Stage stubs (replace with real implementations as built) ─────────────────

def pull_scratches(track: str, race_date: str) -> list[dict]:
    """
    Fetch today's scratches for (track, race_date) from the Equibase RSS feed.

    Returns a list of dicts the rest of the pipeline can consume:
        [{"race": 1, "program": "5", "horse": "TEMPORARILYFOREVER",
          "reason": "PrivVet-Illness", "source": "equibase_rss"}, ...]

    Honors the cumulative bulletin format: "Scratched - Re-entered" horses
    are excluded; "Also-Eligible" horses remain on the list (per DTS rule:
    every DRF entry is scored unless explicitly out, and an AE that hasn't
    been promoted is not running).

    If config.MANUAL_SCRATCHES is set AND the active config track/date
    matches this (track, race_date), those are merged in as well — useful
    for operator overrides on the track currently being scored manually.

    Network failures are LOGGED, not raised. Returning an empty list on
    Equibase outage lets the FINAL pipeline still run with whatever scratch
    info was available (manual extras, or none).

    `race_date` arrives here in YYYYMMDD form from the DRF filename.
    scratches.get_scratches() expects MMDD + year, so we slice it.
    """
    # YYYYMMDD -> (YYYY, MMDD)
    if len(race_date) != 8 or not race_date.isdigit():
        log.warning(f"  pull_scratches: bad race_date {race_date!r}; returning []")
        return []
    year   = race_date[:4]
    mmdd   = race_date[4:]

    rss_scratches: list[ScratchEntry] = []
    try:
        rss_scratches = get_scratches(track, mmdd, year)
    except Exception as e:
        log.warning(
            f"  pull_scratches: Equibase fetch failed for {track} {race_date} "
            f"({e}) — proceeding with manual scratches only."
        )

    # Merge with config.MANUAL_SCRATCHES if (and only if) the operator's
    # active config matches this (track, date). This avoids leaking manual
    # entries from one track's scoring run into other tracks' final sheets.
    manual_entries: list[tuple[int, str]] = []
    try:
        import config
        active_yyyymmdd = f"{config.YEAR}{config.RACE_DATE}"
        if (track.upper() == config.TRACK.upper() and
                race_date == active_yyyymmdd and
                config.MANUAL_SCRATCHES):
            manual_entries = list(config.MANUAL_SCRATCHES)
            log.info(
                f"  pull_scratches: applying {len(manual_entries)} manual "
                f"scratch(es) from config for {track} {race_date}"
            )
    except Exception as e:
        log.debug(f"  pull_scratches: config not consulted ({e})")

    if manual_entries:
        all_scratches = merge_with_manual(rss_scratches, manual_entries)
    else:
        all_scratches = list(rss_scratches)

    out = [
        {
            "race":    s.race,
            "program": s.program_number,
            "horse":   s.horse_name,
            "reason":  s.reason,
            "source":  s.source,
        }
        for s in all_scratches
    ]
    log.info(
        f"  pull_scratches({track}, {race_date}) -> {len(out)} scratch(es) "
        f"(rss={len(rss_scratches)}, manual={len(manual_entries)})"
    )
    return out


# ── Scoring result container ─────────────────────────────────────────────────
# run_scoring() returns this so callers get both the xlsx path AND the full
# scored DataFrame. The xlsx is what we publish today; the DataFrame is what
# generate_pdf() will consume once we build the PDF layout.
#
# Backward compatibility: callers that historically expected a Path can still
# treat the .path attribute or .out_path as their xlsx file. New code (PDF
# generator) pulls .scored_df / .feature_df for full access to all 1500+
# DRF columns (wager types, comments, equipment, etc.) plus the engineered
# features and scoring outputs.
from typing import NamedTuple
import pandas as _pd  # imported as alias to avoid shadowing the top-of-file `pd`


class ScoringResult(NamedTuple):
    out_path:    Path                # The xlsx that was written
    scored_df:   _pd.DataFrame       # Full scored DataFrame (~1550 cols)
    feature_df:  _pd.DataFrame       # Pre-scoring DataFrame (post-scratches)
    track:       str
    race_date:   str
    is_final:    bool                # True if scored with scratches applied
    track_status: "TrackStatus | None" = None   # First post + track conditions (None for PREVIEW)


def run_scoring(
    track: str,
    race_date: str,
    scratches: list[dict] | None = None,
    track_status: TrackStatus | None = None,
    drf_path_override: Path | None = None,
) -> ScoringResult | None:
    """
    Real scoring implementation. Pipeline order:

        ingest_drf.load_drf()
            -> apply_scratches  (CRITICAL: must precede feature engineering)
            -> features.engineer_features
            -> score.run_scoring
            -> output.generate_excel

    Why scratches BEFORE features? Most engineered fields are race-level
    aggregates (means, stds, ranks, normalized variables). Including
    scratched horses corrupts every active horse's comparison baseline:
    field size is wrong, baseprob is depressed, ranks are off, and
    standard-deviation columns get the wrong spread. Scratches must be
    removed first.

    Returns a ScoringResult containing the xlsx path AND the full scored
    DataFrame (for downstream PDF generation), or None on failure.

    Parameters
    ----------
    track : str
        BRISnet/DTS track code from the DRF filename (e.g. "CD", "KEE").
    race_date : str
        YYYYMMDD date string from the DRF filename.
    scratches : list of dicts, optional
        Output of pull_scratches(). For PREVIEW runs, pass None.
    track_status : TrackStatus, optional
        For FINAL runs, the Equibase snapshot. Passes dirt/turf condition
        through to the Excel summary sheet. None for PREVIEW.

    Notes
    -----
    Currently only runs scoring when `track` matches `config.TRACK`. The
    coefficient files in `config.DIRT_MODELS` / `TURF_MODELS` / `MAIDEN_MODELS`
    are meet-specific (e.g. "keedirt042026c.sas7bdat"). The model registry
    maps each track to the appropriate model family — currently every track
    falls back to "KEE" (the default), but as you build SAR/CD/etc. models,
    register them in model_registry.py and they take precedence for those
    tracks automatically.
    """
    import config
    from ingest_drf import load_drf
    from apply_scratches import apply_scratches
    from features import engineer_features
    from score import run_scoring as score_run_scoring
    from output import generate_excel
    from attribution import add_attributions
    from scratches import ScratchEntry
    from model_registry import get_scoring_models
    from model_setup import setup_registry

    log.info(f"  run_scoring({track}, {race_date}, scratches={len(scratches or [])})")

    # ── 1. Resolve which model family to use for this track ──────────────
    # setup_registry() bootstraps from config and applies any per-track
    # overrides defined in model_setup.py. Idempotent.
    setup_registry(config)
    scoring_config = get_scoring_models(track, underlying_config=config)
    log.info(
        f"  Using model family {scoring_config.family_name!r} for track {track!r} "
        f"({len(scoring_config.DIRT_MODELS)} dirt, "
        f"{len(scoring_config.TURF_MODELS)} turf, "
        f"{len(scoring_config.MAIDEN_MODELS)} maiden models)"
    )

    # ── 2. Locate the DRF file ───────────────────────────────────────────
    # Standard path: search DRF_DIR for a file matching the orchestrator's
    # naming conventions. Override path: when run by score_one.py for
    # validation, an explicit DRF path can be passed in to score a specific
    # file (e.g. a manually-downloaded final-of-day DRF) regardless of what
    # files exist in DRF_DIR.
    if drf_path_override is not None:
        drf_path = Path(drf_path_override)
        if not drf_path.exists():
            log.error(f"  run_scoring: drf_path_override does not exist: {drf_path}")
            return None
        log.info(f"  Using DRF path override: {drf_path}")
    else:
        drf_filename_candidates = [
            DRF_DIR / f"{race_date}_{track}_DRS.DRF",
            DRF_DIR / f"{track}{race_date[4:]}.DRF",   # e.g. CDX0508.DRF
            config.DRF_FILE,                            # last resort
        ]
        drf_path = None
        for cand in drf_filename_candidates:
            if cand and Path(cand).exists():
                drf_path = Path(cand)
                break
        if drf_path is None:
            log.error(
                f"  run_scoring: no DRF file found for {track} {race_date}. "
                f"Tried: {[str(c) for c in drf_filename_candidates if c]}"
            )
            return None

    # ── 3. Load DRF ──────────────────────────────────────────────────────
    year = race_date[:4]
    mmdd = race_date[4:]
    log.info(f"  Loading DRF: {drf_path.name}")
    df = load_drf(drf_path, track=track, date=mmdd, year=year)
    initial_n = len(df)
    log.info(f"  Loaded {initial_n} horses across {df['Race'].nunique()} races")

    # ── 4. Apply scratches BEFORE features ───────────────────────────────
    if scratches:
        # Convert dicts back into ScratchEntry objects for apply_scratches.
        scratch_entries = [
            ScratchEntry(
                race=int(s["race"]),
                program_number=str(s["program"]),
                horse_name=s.get("horse", ""),
                reason=s.get("reason", ""),
                source=s.get("source", "unknown"),
            )
            for s in scratches
        ]
        df, scr_summary = apply_scratches(df, scratch_entries)
        log.info(
            f"  Scratches applied: {scr_summary.rows_dropped} dropped "
            f"({initial_n} -> {scr_summary.rows_after})"
        )
        # ── DEBUG: write diagnostic info to a file we can upload ─────────
        try:
            import json as _json
            _debug_path = config.OUTPUT_DIR / f"{race_date}_{track}_DEBUG.txt"
            _debug_lines = [
                f"=== DTS Pipeline Debug ({track} {race_date}) ===",
                "",
                "[Step 1] After apply_scratches:",
                f"  df rows: {len(df)}",
                f"  per-race counts: {df.groupby('Race').size().to_dict()}",
                f"  scratches dropped: {scr_summary.rows_dropped}",
                f"  scratches unmatched: {len(scr_summary.unmatched)}",
                "",
            ]
            with open(_debug_path, "w") as _f:
                _f.write("\n".join(_debug_lines))
            log.info(f"  DEBUG written to {_debug_path}")
        except Exception as _e:
            log.warning(f"  DEBUG write failed: {_e}")
            _debug_path = None
        if scr_summary.unmatched:
            for s in scr_summary.unmatched:
                log.warning(
                    f"    Unmatched scratch (not in DRF): "
                    f"race {s.race} #{s.program_number} {s.horse_name}"
                )

    # ── 5. Feature engineering (race-level stats now correct) ────────────
    log.info(f"  Engineering features on {len(df)} horses...")
    feature_df = engineer_features(df)
    # ── DEBUG: append post-features state to debug file ──────────────────
    try:
        if scratches and _debug_path:
            _bp = (
                feature_df.groupby('Race')['baseprob2'].first().to_dict()
                if 'baseprob2' in feature_df.columns else "MISSING"
            )
            _ne = (
                feature_df.groupby('Race')['NumOfEntries'].first().to_dict()
                if 'NumOfEntries' in feature_df.columns else "MISSING"
            )
            _hr = (
                feature_df.groupby('Race')['HorsesRan'].first().to_dict()
                if 'HorsesRan' in feature_df.columns else "MISSING"
            )
            with open(_debug_path, "a") as _f:
                _f.write("[Step 2] After engineer_features:\n")
                _f.write(f"  feature_df rows: {len(feature_df)}\n")
                _f.write(f"  per-race counts: {feature_df.groupby('Race').size().to_dict()}\n")
                _f.write(f"  baseprob2 per race: {_bp}\n")
                _f.write(f"  NumOfEntries per race: {_ne}\n")
                _f.write(f"  HorsesRan per race: {_hr}\n")
                _f.write("\n")
                _f.write("[Step 3] Spot check Race 10 OUTFIELDER raw features:\n")
                if 'HorseName' in feature_df.columns:
                    _row = feature_df[
                        (feature_df['Race']==10) & (feature_df['HorseName'].str.upper().str.strip()=='OUTFIELDER')
                    ]
                    if len(_row):
                        for _c in ['baseprob2','XBPPR_tc12_3','eps4dalt_sard','xR309c',
                                   'xPostPosition','xturffy_last5','xJCK_CMWPS26',
                                   'iJCK_StrtCM','IJKYe_kta13','IAucPri_keeA25']:
                            if _c in _row.columns:
                                _f.write(f"  {_c}: {_row.iloc[0][_c]}\n")
                            else:
                                _f.write(f"  {_c}: <not in feature_df>\n")
                    else:
                        _f.write("  OUTFIELDER not found in feature_df Race 10\n")
                _f.write("\n")
    except Exception as _e:
        log.warning(f"  DEBUG step-2 write failed: {_e}")

    # ── 6. Scoring ───────────────────────────────────────────────────────
    log.info(f"  Running scoring engine...")
    scored_df = score_run_scoring(
        feature_df, scoring_config.COEFF_DIR, scoring_config
    )

    # ── DEBUG: log scored_df Race 10 to file ─────────────────────────────
    try:
        if scratches and _debug_path:
            with open(_debug_path, "a") as _f:
                _f.write("[Step 4] After score_run_scoring (Race 10):\n")
                _f.write(f"  scored_df rows total: {len(scored_df)}\n")
                _r10 = scored_df[scored_df['Race']==10].copy()
                _f.write(f"  Race 10 rows: {len(_r10)}\n")
                _show_cols = [c for c in [
                    'HorseName','predicted','norm_predprob','ProbToWin',
                    'DTSOdds','baseprob2','HorsesRan','NumOfEntries',
                    'predicted_s','predicted_hp','predicted_r','predicted_lp',
                ] if c in _r10.columns]
                _f.write(f"  columns shown: {_show_cols}\n")
                _f.write(_r10[_show_cols].to_string(index=False))
                _f.write("\n\n")
                # Also dump WHICH features score actually saw for OUTFIELDER
                _f.write("[Step 5] feature_df Race 10 OUTFIELDER full feature row at scoring time:\n")
                _ofr = feature_df[
                    (feature_df['Race']==10) & 
                    (feature_df['HorseName'].str.upper().str.strip()=='OUTFIELDER')
                ]
                if len(_ofr):
                    _row = _ofr.iloc[0]
                    # Dump all turf model features
                    for _c in ['baseprob2','XBPPR_tc12_3','eps4dalt_sard','xR309c',
                               'xPostPosition','xturffy_last5','xJCK_CMWPS26',
                               'iJCK_StrtCM','IJKYe_kta13','IAucPri_keeA25',
                               'JCK_PY_WPS','HC_1stongrass','ltstr_sart',
                               'LastWOatTT','BrisRelated_dmrd','xNumEntLast5cut',
                               'xdaysoff26','xNumDaysSinceLRcut2',
                               'KS_itmm_26','WinsatDist26','StretchBL_LR26',
                               'BrisRelated_sarm','xBRISSpeedAWc_keeod',
                               'Weight_LR','lrclass_kma13','xdrfsp1m_sard',
                               'xNumEntLast5','xNumDaysSinceLRcut',
                               'xBRISRunstyle_EP','xsexcolt0425','PPt12',
                               'xwrkdate_kaaw13','iworkoutpctrnk1_ckta13',
                               'ShowedLateSP_LR','HC_ShipperToUS','brisAW_c',
                               'EarlySpeed','BRISPrimePowerRating','xBRISPrimePowerRating',
                               'BRISPrimePowerRating_ave']:
                        if _c in _row.index:
                            _f.write(f"  {_c}: {_row[_c]}\n")
                        else:
                            _f.write(f"  {_c}: <not in feature_df>\n")
                _f.write("\n")
    except Exception as _e:
        log.warning(f"  DEBUG step-4 write failed: {_e}")

    # ── 6b. Compute attributions (why each horse looks live / fades) ─────
    # Adds why_like_1..3 and why_fade_1..3 columns to scored_df. The Excel
    # template in output.py already reads those columns for the green/red
    # attribution bands; the PDF generator will read the same columns.
    #
    # Failure is non-fatal: if coefficient files are missing or unreadable,
    # add_attributions logs a warning and returns scored_df with empty
    # why_* columns. The Excel still writes; bands just stay blank.
    log.info(f"  Computing attributions...")
    try:
        scored_df = add_attributions(
            scored_df,
            coeff_dir=scoring_config.COEFF_DIR,
            config=scoring_config,
            feature_df=feature_df,
        )
        # Sanity: how many horses got at least one like and at least one fade?
        if "why_like_1" in scored_df.columns:
            n_with_like = int((scored_df["why_like_1"].astype(str) != "").sum())
            n_with_fade = int((scored_df["why_fade_1"].astype(str) != "").sum())
            log.info(
                f"    Attributions: {n_with_like}/{len(scored_df)} horses with "
                f"a like, {n_with_fade}/{len(scored_df)} with a fade"
            )
    except Exception as _e:
        log.warning(f"  Attribution computation failed (non-fatal): {_e}")
        # Make sure the columns exist even on failure, so output.py's
        # horse.get('why_like_1', '') reads cleanly.
        for _i in range(1, 4):
            if f"why_like_{_i}" not in scored_df.columns:
                scored_df[f"why_like_{_i}"] = ""
            if f"why_fade_{_i}" not in scored_df.columns:
                scored_df[f"why_fade_{_i}"] = ""

    # ── DEBUG: log attribution snapshot for Race 10 ──────────────────────
    try:
        if scratches and _debug_path:
            with open(_debug_path, "a") as _f:
                _f.write("[Step 6] After add_attributions (Race 10):\n")
                _r10 = scored_df[scored_df["Race"] == 10].copy()
                _f.write(f"  Race 10 rows: {len(_r10)}\n")
                if len(_r10):
                    _attr_cols = [c for c in [
                        "HorseName",
                        "why_like_1", "why_like_2", "why_like_3",
                        "why_fade_1", "why_fade_2", "why_fade_3",
                    ] if c in _r10.columns]
                    _f.write(_r10[_attr_cols].to_string(index=False))
                _f.write("\n\n")
    except Exception as _e:
        log.warning(f"  DEBUG step-6 write failed: {_e}")

    # ── 7. Write Excel ───────────────────────────────────────────────────
    # Output filename: YYYYMMDD_TRACK_(PREVIEW|FINAL).xlsx in the configured
    # output dir. Don't overwrite the user's manual config.OUTPUT_XLSX.
    label = "FINAL" if scratches is not None else "PREVIEW"
    out_path = config.OUTPUT_DIR / f"{race_date}_{track}_{label}.xlsx"

    dirt_cond = (track_status.dirt_condition if track_status else "") or ""
    turf_cond = (track_status.turf_condition if track_status else "") or ""

    full_track_name = (
        getattr(config, "TRACK_FULL_NAMES", {}).get(track.upper(), track)
    )

    log.info(f"  Generating Excel: {out_path}")
    generate_excel(
        scored_df=scored_df,
        feature_df=feature_df,
        output_path=out_path,
        config=scoring_config,
        track=full_track_name,
        race_date=race_date,
        dirt_condition=dirt_cond,
        turf_condition=turf_cond,
    )

    log.info(f"  run_scoring complete: {out_path.name}")
    return ScoringResult(
        out_path=out_path,
        scored_df=scored_df,
        feature_df=feature_df,
        track=track,
        race_date=race_date,
        is_final=(scratches is not None),
        track_status=track_status,
    )


# ── RaceConditions1/2 → single-line summary ────────────────────────────────
# BRISnet's DRF splits the eligibility/conditions text across two fixed-width
# fields (RaceConditions1 + RaceConditions2). The split routinely lands in
# the middle of a word (e.g. RC1 ends "...OR C" and RC2 begins "LAIMING..."),
# so we concatenate WITHOUT a space. We then collapse common phrases into
# short abbreviations so the result fits on one line in the race header.
import re as _re

_RC_ABBREVS = [
    # Class / claiming / starter
    (r"\bFOR MAIDENS,?\s*", "Mdn, "),
    (r"\bMAIDEN SPECIAL WEIGHT\b", "Mdn SpWt"),
    (r"\bMAIDEN CLAIMING\b", "Mdn Clm"),
    (r"\bSTARTER OPTIONAL CLAIMING\b", "Str Opt Clm"),
    (r"\bOPTIONAL CLAIMING\b", "Opt Clm"),
    (r"\bSTARTER ALLOWANCE\b", "Str Alw"),
    (r"\bSTARTER HANDICAP\b", "Str H'cap"),
    (r"\bSTARTERS?\b", "Str"),
    (r"\bCLAIMING PRICE\b", "Clm"),
    (r"\bCLAIMING\b", "Clm"),
    (r"\bALLOWANCE\b", "Alw"),
    (r"\bSTAKES\b", "Stk"),
    (r"\bHANDICAP\b", "H'cap"),
    (r"\bSWEEPSTAKES\b", "Stk"),

    # Eligibility phrases
    (r"\bNON-WINNERS OF\b", "NW"),
    (r"\bNON WINNERS OF\b", "NW"),
    (r"\bNON-?WINNERS\b", "NW"),
    (r"\bWHICH HAVE NEVER WON\b", "Never won"),
    (r"\bWHO HAVE NEVER WON\b", "Never won"),
    (r"\bWHICH HAVE NOT WON\b", "Not won"),
    (r"\bA RACE OTHER THAN\b", "other than"),
    (r"\bRACES? OTHER THAN\b", "other than"),
    (r"\bMAIDEN, CLAIMING OR STARTER\b", "Mdn/Clm/Str"),
    (r"\bMAIDEN OR CLAIMING\b", "Mdn/Clm"),
    (r"\bSINCE\b", "since"),
    (r"\bLIFETIME\b", "L"),
    (r"\bAT A MILE OR OVER\b", "1m+"),
    (r"\bAT ONE MILE OR OVER\b", "1m+"),

    # Age / sex (after major class phrases so we don't break them)
    (r"\bTHREE YEARS OLD AND UPWARD\b", "3YO+"),
    (r"\bFOUR YEARS OLD AND UPWARD\b", "4YO+"),
    (r"\bTHREE YEARS OLD AND OLDER\b", "3YO+"),
    (r"\bFOUR YEARS OLD AND OLDER\b", "4YO+"),
    (r"\bTHREE YEARS OLD\b", "3YO"),
    (r"\bFOUR YEARS OLD\b", "4YO"),
    (r"\bFIVE YEARS OLD\b", "5YO"),
    (r"\bTWO YEARS OLD\b", "2YO"),
    (r"\bFILLIES AND MARES\b", "F&M"),
    (r"\bFILLIES & MARES\b", "F&M"),
    (r"\bFILLIES\b", "F"),
    (r"\bMARES\b", "M"),

    # Misc
    (r"\bACCREDITED\b", "Acc"),
    (r"\bREGISTERED\b", "Reg"),
    (r"\bAND UPWARD\b", "+"),
    (r"\bAND OLDER\b", "+"),
    (r"\bAT TIME OF ENTRY\b", ""),
    (r"\bWEIGHT\b", "Wt"),
    (r"\bPOUNDS\b", "lbs"),
    (r"\bA RACE\b", "race"),
    (r"\bSUCH A RACE\b", "such race"),
    (r"\bCLOSED\b", ""),
]


def _summarize_rc(rc1, rc2, max_len: int = 85) -> str:
    """
    Concatenate RaceConditions1 + RaceConditions2 and shorten to a single
    line suitable for the race-header middle slot.

    BRISnet splits the field mid-word (e.g. "...OR C" + "LAIMING..."), so
    we concat with no separator. Semicolons substitute for commas in the
    raw feed; restore them. Apply abbreviation table, collapse whitespace,
    truncate at word boundary at max_len.
    """
    a = "" if rc1 is None else str(rc1)
    b = "" if rc2 is None else str(rc2)
    s = (a + b).replace(";", ",").strip()
    if not s:
        return ""
    s_up = s.upper()
    for pat, repl in _RC_ABBREVS:
        s_up = _re.sub(pat, repl, s_up)
    # Title-case for display but preserve our short tokens
    out = s_up
    # Collapse repeated punctuation / whitespace
    out = _re.sub(r"\s+", " ", out)
    out = _re.sub(r"\s*,\s*", ", ", out)
    out = _re.sub(r",\s*,+", ", ", out)
    out = _re.sub(r"\s*\.\s*", ". ", out)
    out = out.strip(" ,.")
    if not out:
        return ""
    # Truncate at word boundary
    if len(out) > max_len:
        cut = out[:max_len]
        sp = cut.rfind(" ")
        if sp > max_len * 0.6:
            cut = cut[:sp]
        out = cut.rstrip(" ,.") + "…"
    return out


def generate_pdf(scoring_result: ScoringResult,
                 is_final: bool = False) -> Path | None:
    """
    Render the daily handicapping PDF from a ScoringResult.

    Uses pdf.py (WeasyPrint-backed) to produce the PDF in
    config.OUTPUT_DIR with the same naming pattern as the Excel:
        YYYYMMDD_TRACK_(PREVIEW|FINAL).pdf

    For PREVIEW runs (is_final=False) the track conditions show "TBD"
    regardless of what's in scoring_result.track_status. For FINAL runs
    the conditions come from track_status (Equibase fetch).

    Returns the path to the generated PDF, or None on failure.
    """
    import config

    label = "FINAL" if is_final else "PREVIEW"
    track     = scoring_result.track
    race_date = scoring_result.race_date

    src = scoring_result.scored_df
    feature_df = scoring_result.feature_df
    if src.empty:
        log.error(f"  generate_pdf: scored_df is empty for {track} {race_date}")
        return None

    # ── Compose the Comments column + numeric ValueTier ─────────────────
    # The legacy VLOOKUP-based SmartComment is gone. Comments are now a
    # two-sentence summary built from attribution signals + DTS-vs-ML.
    # ValueTier (0-4) drives downstream best-bet logic instead of grepping
    # for "$$$" in prose.
    work = src.copy()
    try:
        from output import compose_comment as _compose, value_tier as _tier
        # Comments need access to btsm_odds / ml_odds. The composer accepts
        # either the canonical (btsm_odds/ml_odds) or upstream (DTSOdds/
        # MornOdds) names via row.get(), so this works on scored_df rows.
        work["Comments"]  = work.apply(_compose, axis=1)
        work["ValueTier"] = work.apply(
            lambda r: _tier(r.get("DTSOdds"), r.get("MornOdds")), axis=1
        )
    except Exception as e:
        log.warning(f"  generate_pdf: could not compose Comments: {e}")
        work["Comments"]  = ""
        work["ValueTier"] = 0

    # ── Merge feature columns we need for display from feature_df ───────
    # These live on feature_df (computed by features.py / race_normalize.py /
    # model_vars.py) rather than scored_df. Pull all of them in one pass.
    # output.py does the same merge for Excel.
    if feature_df is not None:
        key = [c for c in ["Track", "Date", "Race", "HorseName"]
               if c in work.columns and c in feature_df.columns]
        wanted = ["EarlySpeed",          # RUNS column
                  "xBRISPd2",            # SPD bar  ((xBRISPd+14)^2,  fixed 1-729)
                  "jckcm2_sarm",         # JKY bar  ((2.5+xJkyWCMstd)^2, fixed 1-20.25)
                  "trncm2_sart"]         # TRN bar  ((2.5+trnwcm_sart)^2, fixed 0.25-20.25)
        missing = [c for c in wanted if c in feature_df.columns and c not in work.columns]
        if key and missing:
            try:
                feat_sub = feature_df[key + missing].copy()
                work = work.merge(feat_sub, on=key, how="left",
                                  suffixes=("", "_f"))
            except Exception as e:
                log.warning(f"  generate_pdf: feature_df merge failed: {e}")

    # Format EarlySpeed → "Early" / "Late" / "—" using output._runs helper
    if "EarlySpeed" in work.columns:
        try:
            from output import _runs as _runs_label
            work["runs_label"] = work["EarlySpeed"].apply(_runs_label)
        except Exception:
            work["runs_label"] = "-"
    else:
        work["runs_label"] = "-"

    # ── Compute SPD / JKY / TRN bar widths (0-100) ──────────────────────
    # All three bars use FIXED absolute scales derived from the theoretical
    # bounds of the engineered transforms. Fixed scales let readers compare
    # horses ACROSS races, not just within the current field.
    #
    # SPD  -> xBRISPd2     ((xBRISPd + 14)^2 where xBRISPd is residual PPR
    #                       clipped to [-13, 13], NaN->0).
    #                       Bounds: (-13+14)^2 = 1 .. (+13+14)^2 = 729.
    # JKY  -> jckcm2_sarm  ((2.5 + xJkyWCMstd_sarm)^2 where xJkyWCMstd_sarm
    #                       is clip(xJockeyWinsCurrentMeet/xjwins_std, -1.5, 2.0)).
    #                       Bounds: (2.5-1.5)^2 = 1 .. (2.5+2.0)^2 = 20.25.
    # TRN  -> trncm2_sart  ((2.5 + trnwcm_sart)^2 where trnwcm_sart is
    #                       clip(xTrainerWinsCurrentMeet/xTrainerWinsCurrentMeet_std, -2, 2)).
    #                       Bounds: (2.5-2)^2 = 0.25 .. (2.5+2)^2 = 20.25.
    #
    # NaN handling is baked in upstream: missing data lands on the transform's
    # midpoint (~6.25 for JKY/TRN, ~196 for SPD), which maps to roughly a
    # 27-30% bar. That reads as "no signal" rather than "worst in field."
    XBRISPD2_MIN, XBRISPD2_MAX   = 1.0,  729.0    # (-13+14)^2 .. (+13+14)^2
    JCKCM2_MIN,   JCKCM2_MAX     = 1.0,   20.25   # (2.5-1.5)^2 .. (2.5+2.0)^2
    TRNCM2_MIN,   TRNCM2_MAX     = 0.25,  20.25   # (2.5-2.0)^2 .. (2.5+2.0)^2

    def _fixed_scale(series: _pd.Series, lo: float, hi: float) -> _pd.Series:
        vals = _pd.to_numeric(series, errors="coerce")
        pct = ((vals - lo) / (hi - lo) * 100).clip(lower=0, upper=100).round()
        return pct.fillna(0)

    if "Race" in work.columns:
        if "xBRISPd2" in work.columns:
            work["speed_bar"]   = _fixed_scale(work["xBRISPd2"],    XBRISPD2_MIN, XBRISPD2_MAX)
        if "jckcm2_sarm" in work.columns:
            work["jockey_bar"]  = _fixed_scale(work["jckcm2_sarm"], JCKCM2_MIN,   JCKCM2_MAX)
        if "trncm2_sart" in work.columns:
            work["trainer_bar"] = _fixed_scale(work["trncm2_sart"], TRNCM2_MIN,   TRNCM2_MAX)

    # ── Per-race RaceConditions1/2 summary ──────────────────────────────
    # Compute the abbreviated conditions string ONCE per race (not per
    # horse), then map back onto every row of that race. Doing it per
    # horse caused some rows to land empty when RC1/RC2 differed slightly
    # within the same race.
    if "Race" in work.columns:
        rc1_col = "RaceConditions1" if "RaceConditions1" in work.columns else None
        rc2_col = "RaceConditions2" if "RaceConditions2" in work.columns else None
        _rc_by_race: dict = {}
        if rc1_col or rc2_col:
            for race_num, grp in work.groupby("Race", sort=False):
                rc1 = grp[rc1_col].iloc[0] if rc1_col else None
                rc2 = grp[rc2_col].iloc[0] if rc2_col else None
                try:
                    _rc_by_race[int(race_num)] = _summarize_rc(rc1, rc2)
                except Exception:
                    _rc_by_race[int(race_num)] = ""
        work["race_conditions_summary"] = (
            work["Race"].astype("Int64").map(_rc_by_race).fillna("")
        )

    # ── Build the slim DataFrame that pdf.py consumes ──────────────────
    # Map BRISnet columns → pdf.py columns. Note the canonical names:
    #   "Num"      not "ProgramNumberifavailable"   (set by score.py line 576)
    #   "MornOdds" not "MornLineOddsifavailable"    (set by score.py line 518)
    # We accept either, preferring canonical, for robustness.
    def _col(name, fallback=None, default=None):
        """Get column by name. If absent, try fallback name. If still absent, return default-filled series."""
        if name in work.columns:
            return work[name]
        if fallback is not None and fallback in work.columns:
            return work[fallback]
        return _pd.Series([default] * len(work), index=work.index)

    def _clean_program(v) -> str:
        """
        Normalize a program number for display.
        score.py stores Num as a float Series (because pandas mixes ints with
        NaN), so naive str(8.0) -> "8.0". We want "8". Handles "1A" suffixed
        couplings cleanly too (they stay strings).
        """
        if v is None:
            return ""
        try:
            f = float(v)
            if f != f:   # NaN
                return ""
            if f == int(f):
                return str(int(f))
            return str(f)
        except (TypeError, ValueError):
            return str(v).strip()

    pdf_df = _pd.DataFrame({
        # Race identity
        "race":            _col("Race"),
        "program":         _col("Num", "ProgramNumberifavailable", "").apply(_clean_program),
        "horse":           _col("HorseName", default=""),

        # Race header fields
        "dist_yd":         _col("Distanceinyards"),
        "surface":         _col("Surface", default=""),
        "racetype":        _col("RaceType", default=""),
        "age_sex":         _col("AgeSexRestrictions", default=""),
        "classif":         _col("TodaysRaceClassification", default=""),
        "purse":           _col("Purse"),
        "turns":           _col("turns"),
        "race_name":       _col("RaceName", default=""),
        "race_conditions_summary": _col("race_conditions_summary", default=""),

        # Connections
        "jockey":          _col("TodaysJockey", default=""),
        "trainer":         _col("TodaysTrainer", default=""),

        # Odds — production uses canonical short names
        "ml_odds":         _col("MornOdds", "MornLineOddsifavailable"),
        "dts_odds":       _col("DTSOdds"),

        # Scoring outputs
        "prob_to_win":     _col("ProbToWin"),
        "rank":            _col("rank"),
        "smart_comment":   _col("Comments", default=""),    # 2-sentence prose
        "value_tier":      _col("ValueTier", default=0),    # 0-4, drives best-bet styling

        # Visual bars — computed above from xBRISPd2 / jckcm2_sarm / trncm2_sart
        # on fixed absolute scales (cross-race comparable).
        "speed_bar":       _col("speed_bar", default=0),
        "jockey_bar":      _col("jockey_bar", default=0),
        "trainer_bar":     _col("trainer_bar", default=0),
        "runs_label":      _col("runs_label", default="-"),
    })

    # Wagers — BRIS packs 9 WagerType columns. pdf.py splits on "/" and
    # filters to multi-race bets.
    wager_cols = [f"WagerType{i}" for i in range(1, 10) if f"WagerType{i}" in work.columns]
    if wager_cols:
        pdf_df["wagers"] = work[wager_cols].apply(
            lambda r: [v for v in r if isinstance(v, str) and v.strip()],
            axis=1,
        )
    else:
        pdf_df["wagers"] = [[] for _ in range(len(work))]

    # Attribution reasons + 20%-filter scores
    for i in (1, 2, 3):
        for side in ("like", "fade"):
            col = f"why_{side}_{i}"
            pdf_df[col] = _col(col, default="")
            sc = f"why_{side}_{i}_score"
            pdf_df[sc] = _col(sc)

    # ── First post and conditions ───────────────────────────────────────
    ts = scoring_result.track_status
    first_post_dt = None
    if ts is not None and getattr(ts, "first_post", None) is not None:
        first_post_dt = ts.first_post
    else:
        # PREVIEW path: track_status is None, but we can still get a
        # first-post estimate by reading the DRF. Try both naming
        # conventions used by the pipeline.
        try:
            drf_candidates = [
                DRF_DIR / f"{race_date}_{track}_DRS.DRF",       # 20260515_LRL_DRS.DRF
                DRF_DIR / f"{track.upper()}{race_date[4:]}.DRF",  # LRL0515.DRF
            ]
            drf_candidates += list(DRF_DIR.glob(f"{track.upper()}{race_date[4:]}*.DRF"))
            for cand in drf_candidates:
                if cand.exists():
                    first_post_dt = get_first_post(cand)
                    if first_post_dt:
                        log.info(f"  PDF first-post fallback: {cand.name} -> {first_post_dt}")
                        break
        except Exception as e:
            log.debug(f"  generate_pdf: no DRF first-post fallback: {e}")

    if first_post_dt is not None:
        # Portable 12-hour formatting (Windows + Linux)
        hour_12 = first_post_dt.hour % 12 or 12
        first_post_str = f"{hour_12}:{first_post_dt.strftime('%M %p')}"
    else:
        first_post_str = None

    conditions = {}
    if ts is not None:
        if getattr(ts, "dirt_condition", ""):
            conditions["dirt"] = ts.dirt_condition
        if getattr(ts, "turf_condition", ""):
            conditions["turf"] = ts.turf_condition

    # ── Logo path: optional ─────────────────────────────────────────────
    # Look for the DTS banner in canonical locations. If not found here,
    # pdf.py will fall back to its own DTS_banner.png lookup next to the
    # pdf.py module — so leaving logo_path as None is safe.
    logo_path = None
    for c in [
        BASE_DIR / "DTS_banner.png",
        BASE_DIR / "assets" / "DTS_banner.png",
    ]:
        if c.exists():
            logo_path = c
            break

    full_track_name = (
        getattr(config, "TRACK_FULL_NAMES", {}).get(track.upper(), track)
    )

    out_path = config.OUTPUT_DIR / f"{race_date}_{track}_{label}.pdf"

    # ── Call pdf.py ─────────────────────────────────────────────────────
    try:
        from pdf import generate_pdf as _build_pdf
    except Exception as e:
        log.error(f"  generate_pdf: cannot import pdf module: {e}")
        return None

    try:
        result_path = _build_pdf(
            pdf_df,
            out_path=out_path,
            track=track,
            track_full_name=full_track_name,
            race_date=race_date,
            label=label,
            first_post=first_post_str,
            conditions=conditions,
            logo_path=logo_path,
            is_preview=(not is_final),
        )
        log.info(f"  PDF generated: {result_path}")
        return result_path
    except Exception as e:
        log.exception(f"  generate_pdf failed for {track} {race_date}: {e}")
        return None


from upload_to_dts import upload_to_dts


# ── Per-track decision logic ─────────────────────────────────────────────────

def should_publish_preview(drf: dict, state: dict) -> tuple[bool, str]:
    """
    Decide if a preview sheet should be published for this DRF.
    Returns (should_publish, reason).
    """
    race_date = drf["race_date"]
    track     = drf["track"]
    mtime     = drf["mtime"]

    pub = state.get("published", {}).get(race_date, {}).get(track, {})
    prev = pub.get("preview")

    if not prev:
        return True, "no preview yet"

    # Re-publish if DRF file has been updated since last preview
    if prev.get("drf_mtime", 0) < mtime - 1:  # 1-sec tolerance
        return True, "DRF file updated"

    return False, "preview already current"


def _find_active_anchor(
    minutes_until_first: float,
    race_posts: dict[int, datetime] | None,
    now: datetime,
) -> tuple[str, float] | None:
    """
    Given the number of minutes until first post and the per-race post-time
    map, return (anchor_key, distance_to_anchor) for the active anchor at
    `now`, or None if not in any anchor window.

    Anchor keys:
      - "T60", "T30", "T0"  → pre-card anchors at T-60/30/0 before Race 1
      - "R<n>" (e.g. "R5")  → in-card anchor at T-20 before Race n's post

    Anchors must be within FINAL_ANCHOR_TOLERANCE_MIN minutes of `now`. If
    multiple anchors qualify simultaneously the nearest wins.
    """
    best:      tuple[str, float] | None = None
    best_dist: float = FINAL_ANCHOR_TOLERANCE_MIN + 1

    # Pre-card anchors (T-60, T-30, T-0 relative to Race 1's post)
    for anchor_min in FINAL_ANCHORS_MIN_BEFORE:
        dist = abs(minutes_until_first - anchor_min)
        if dist <= FINAL_ANCHOR_TOLERANCE_MIN and dist < best_dist:
            best = (f"T{anchor_min}", dist)
            best_dist = dist

    # In-card anchors: T-20 before each race AFTER race 1. (Race 1 is covered
    # by the pre-card anchors.)
    if race_posts:
        for race_num, post in race_posts.items():
            if race_num < 2:
                continue
            target = post - timedelta(minutes=IN_CARD_ANCHOR_MIN_BEFORE_RACE)
            dist = abs((target - now).total_seconds() / 60.0)
            if dist <= FINAL_ANCHOR_TOLERANCE_MIN and dist < best_dist:
                best = (f"R{race_num}", dist)
                best_dist = dist

    return best


def _is_card_complete(race_posts: dict[int, datetime] | None, now: datetime) -> bool:
    """
    Returns True if `now` is past the last race's post time + grace period
    (i.e. racing is over for the day — no more useful changes will arrive).
    Returns False if race_posts is None or the card hasn't ended yet.
    """
    if not race_posts:
        return False
    last_post = max(race_posts.values())
    return now > last_post + timedelta(minutes=CARD_COMPLETE_GRACE_MIN)


def should_publish_final(drf: dict, state: dict, now: datetime) -> tuple[bool, str]:
    """
    Decide if the FINAL sheet should be published right now.
    Returns (should_publish, reason).

    Uses an anchor-based schedule:
      - Pre-card: T-60, T-30, T-0 (relative to Race 1's post)
      - In-card:  T-20 (relative to each subsequent race's post)
    Each anchor +/- FINAL_ANCHOR_TOLERANCE_MIN. Each anchor publishes at most
    once per (track, race_date).

    Window gating uses the **DRF heuristic** for post times (free), NOT
    Equibase. The expensive Selenium fetch only happens once we're inside
    an anchor window and actually committing to publish.

    Once the card is complete (now > last_post + grace), polling stops for
    that track until tomorrow.
    """
    race_date = drf["race_date"]
    track     = drf["track"]

    # Only on race day
    today_str = now.date().strftime("%Y%m%d")
    if race_date != today_str:
        return False, "not race day"

    # Get the full per-race post-time map (cheap, no Selenium)
    race_posts = get_all_race_posts(drf["path"])
    if not race_posts:
        return False, "could not parse race posts from DRF"

    # Stop polling once the card is over
    if _is_card_complete(race_posts, now):
        return False, "card complete (past last race + grace period)"

    first_post = race_posts.get(1) or min(race_posts.values())
    minutes_until_first = (first_post - now).total_seconds() / 60.0

    active = _find_active_anchor(minutes_until_first, race_posts, now)
    if active is None:
        return False, (
            f"not in any anchor window "
            f"({minutes_until_first:+.0f} min to first post; "
            f"{len(race_posts)} races on card)"
        )
    anchor_key, dist = active

    # Has this anchor already fired for this (track, race_date)?
    pub = state.get("published", {}).get(race_date, {}).get(track, {})
    anchors_done = pub.get("final_anchors_done", [])
    if anchor_key in anchors_done:
        return False, (
            f"{anchor_key} anchor already published "
            f"(done: {anchors_done})"
        )

    return True, (
        f"in {anchor_key} window (dist={dist:.0f} min); "
        f"first post {minutes_until_first:+.0f} min away"
    )


# ── Publish actions ──────────────────────────────────────────────────────────

def publish_preview(drf: dict, state: dict) -> bool:
    """Run the preview pipeline. Update state on success."""
    race_date = drf["race_date"]
    track     = drf["track"]

    log.info(f"[PREVIEW] {track} {race_date} -- starting")
    try:
        # No scratches for preview
        result = run_scoring(track, race_date, scratches=None)
        if result is None:
            log.error(f"[PREVIEW] {track} {race_date} -- scoring failed")
            return False

        pdf_path = generate_pdf(result, is_final=False)
        if not pdf_path:
            log.error(f"[PREVIEW] {track} {race_date} -- PDF generation failed")
            return False

        if not upload_to_dts(pdf_path, track, race_date, is_final=False):
            log.error(f"[PREVIEW] {track} {race_date} -- upload failed")
            return False

        # Update state
        state.setdefault("published", {}).setdefault(race_date, {}).setdefault(track, {})
        state["published"][race_date][track]["preview"] = {
            "published_at": datetime.now().isoformat(timespec="seconds"),
            "drf_file":     drf["path"].name,
            "drf_mtime":    drf["mtime"],
            "score_xlsx":   str(result.out_path),
            "pdf":          str(pdf_path),
        }
        log.info(f"[PREVIEW] {track} {race_date} -- OK")
        return True

    except Exception as e:
        log.exception(f"[PREVIEW] {track} {race_date} -- exception: {e}")
        return False


def _scratch_signature(scratches: list[dict]) -> str:
    """
    Build a stable, order-independent signature for a scratch list, suitable
    for change detection across ticks.

    We hash only (race, program_number) pairs because horse names and reasons
    can vary slightly across Equibase RSS updates while the actual scratch
    set hasn't changed.
    """
    import hashlib
    keys = sorted(
        f"{int(s['race'])}#{str(s['program']).strip()}"
        for s in scratches
        if "race" in s and "program" in s
    )
    return hashlib.sha1("|".join(keys).encode("utf-8")).hexdigest()


def _active_anchor_for_drf(drf: dict, now: datetime) -> str | None:
    """
    Return the anchor key (e.g. "T60", "T30", "T0", "R5") for the current
    tick, or None if not in any window. Uses the DRF heuristic.
    """
    race_posts = get_all_race_posts(drf["path"])
    if not race_posts:
        return None
    first_post = race_posts.get(1) or min(race_posts.values())
    minutes_until = (first_post - now).total_seconds() / 60.0
    active = _find_active_anchor(minutes_until, race_posts, now)
    return active[0] if active else None


def publish_final(drf: dict, state: dict) -> bool:
    """
    Run the final pipeline (with scratches). Update state on success.

    Logic:
      1. Fetch scratches from Equibase.
      2. Compute a signature for this scratch list.
      3. If the signature matches the last successful FINAL publish for this
         (track, race_date), skip the re-score/PDF/upload — nothing changed.
         Still record that this anchor fired so we don't keep re-checking.
      4. Otherwise: re-score, regenerate PDF, re-upload, and record the new
         signature + anchor.
    """
    race_date = drf["race_date"]
    track     = drf["track"]

    log.info(f"[FINAL]   {track} {race_date} -- starting")
    try:
        scratches = pull_scratches(track, race_date)
        new_sig = _scratch_signature(scratches)

        # Determine which anchor this tick is firing for (used in state update)
        anchor = _active_anchor_for_drf(drf, datetime.now())

        # Compare to last-published signature
        pub = state.get("published", {}).get(race_date, {}).get(track, {})
        prev_final = pub.get("final") or {}
        prev_sig = prev_final.get("scratches_sig")

        anchors_done = list(pub.get("final_anchors_done", []))

        if prev_sig is not None and prev_sig == new_sig:
            # No change since last FINAL — skip the expensive work, just
            # record that this anchor fired.
            log.info(
                f"[FINAL]   {track} {race_date} -- no scratch change "
                f"(sig {new_sig[:8]}, {len(scratches)} scratches); "
                f"skipping re-publish, marking {anchor} done"
            )
            if anchor is not None and anchor not in anchors_done:
                anchors_done.append(anchor)
            state.setdefault("published", {}).setdefault(race_date, {}).setdefault(track, {})
            state["published"][race_date][track]["final_anchors_done"] = anchors_done
            # Touch a "last_checked" timestamp so operators can see we did look
            state["published"][race_date][track].setdefault("final", {})
            state["published"][race_date][track]["final"]["last_checked_at"] = \
                datetime.now().isoformat(timespec="seconds")
            return True

        # Scratch list changed (or first publish) — full pipeline
        first_post, ts = _get_final_first_post(drf)

        result = run_scoring(
            track, race_date, scratches=scratches, track_status=ts,
        )
        if result is None:
            log.error(f"[FINAL]   {track} {race_date} -- scoring failed")
            return False

        pdf_path = generate_pdf(result, is_final=True)
        if not pdf_path:
            log.error(f"[FINAL]   {track} {race_date} -- PDF generation failed")
            return False

        if not upload_to_dts(pdf_path, track, race_date, is_final=True):
            log.error(f"[FINAL]   {track} {race_date} -- upload failed")
            return False

        track_status_dict = ts.as_dict() if ts is not None else None

        # Record this anchor as done
        if anchor is not None and anchor not in anchors_done:
            anchors_done.append(anchor)

        state.setdefault("published", {}).setdefault(race_date, {}).setdefault(track, {})
        state["published"][race_date][track]["final"] = {
            "published_at":   datetime.now().isoformat(timespec="seconds"),
            "last_checked_at": datetime.now().isoformat(timespec="seconds"),
            "first_post":     first_post.isoformat(timespec="seconds") if first_post else None,
            "first_post_source": (
                "equibase" if (ts is not None and ts.fetch_ok and ts.first_post)
                else ("drf-heuristic" if first_post else None)
            ),
            "track_status":   track_status_dict,
            "scratch_count":  len(scratches),
            "scratches":      scratches,
            "scratches_sig":  new_sig,
            "score_xlsx":     str(result.out_path),
            "pdf":            str(pdf_path),
        }
        state["published"][race_date][track]["final_anchors_done"] = anchors_done

        if prev_sig is None:
            log.info(
                f"[FINAL]   {track} {race_date} -- OK (first publish, "
                f"{len(scratches)} scratches, {anchor})"
            )
        else:
            log.info(
                f"[FINAL]   {track} {race_date} -- OK (scratches changed: "
                f"sig {prev_sig[:8]} -> {new_sig[:8]}, {len(scratches)} scratches, {anchor})"
            )
        return True

    except Exception as e:
        log.exception(f"[FINAL]   {track} {race_date} -- exception: {e}")
        return False


# ── Main ─────────────────────────────────────────────────────────────────────

def _archive_past_drfs() -> int:
    """
    Move any DRF in DRF_DIR whose race date is older than yesterday into
    DRF_DIR/archive/. Called once per tick before discovery.

    Why this exists: discover_drf_files() now filters past-date DRFs out of
    its return value, but the underlying files still sit in DRF_DIR. Over a
    season that's hundreds of files, and they bloat backups and slow the
    glob in discover. Moving them out keeps the live directory lean.

    Idempotent and cheap. Failures move on quietly — a stuck rename should
    never block a pipeline tick.
    """
    if not DRF_DIR.exists():
        return 0

    from datetime import date, timedelta
    cutoff_str = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
    archive_dir = DRF_DIR / "archive"

    moved = 0
    for p in DRF_DIR.glob("*.DRF"):
        parsed = parse_drf_filename(p)
        if not parsed:
            continue
        race_date, _track = parsed
        if race_date >= cutoff_str:
            continue
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            target = archive_dir / p.name
            # If a same-named file already exists in archive, leave both alone
            # rather than overwriting — preserves an audit trail.
            if target.exists():
                continue
            p.rename(target)
            moved += 1
        except Exception as e:
            log.warning(f"[archive] could not move {p.name}: {e}")
    return moved


def main() -> int:
    log.info("=" * 60)
    log.info(f"  Pipeline poller tick @ {datetime.now():%Y-%m-%d %H:%M}")
    log.info("=" * 60)

    archived = _archive_past_drfs()
    if archived:
        log.info(f"[*] Archived {archived} past-date DRF(s) to {DRF_DIR / 'archive'}")

    state = load_state()
    drfs = discover_drf_files()
    log.info(f"[*] {len(drfs)} DRF files found in {DRF_DIR}")

    if not drfs:
        save_state(state)
        log.info("[*] Nothing to do.")
        return 0

    now = datetime.now()
    failures = []
    actions  = 0
    finals_published_this_tick = 0
    deferred_tracks: list[str] = []

    # Sort DRFs by race_date, then by minutes-to-first-post (ascending) so
    # that tracks closer to their next anchor get priority when the fetch
    # cap is binding. Tracks with no parseable first post sort last.
    def _priority(d: dict) -> tuple[str, float]:
        try:
            fp = get_first_post(d["path"])
            if fp:
                return (d["race_date"], (fp - now).total_seconds())
        except Exception:
            pass
        return (d["race_date"], 9_999_999)

    for drf in sorted(drfs, key=_priority):
        race_date = drf["race_date"]
        track     = drf["track"]

        # PREVIEW decision (no Selenium fetch, doesn't count against cap)
        publish_p, reason_p = should_publish_preview(drf, state)
        if publish_p:
            actions += 1
            if not publish_preview(drf, state):
                failures.append(f"PREVIEW {track} {race_date}")
                save_state(state)  # save partial state even on failure
        else:
            log.info(f"[skip preview] {track} {race_date}: {reason_p}")

        # FINAL decision (only relevant on race day)
        publish_f, reason_f = should_publish_final(drf, state, now)
        if not publish_f:
            log.debug(f"[skip final]   {track} {race_date}: {reason_f}")
            continue

        # In an anchor window — but enforce the per-tick fetch cap so we
        # don't blow through our 30-min Task Scheduler slot if Equibase
        # is slow.
        if finals_published_this_tick >= MAX_FETCHES_PER_TICK:
            deferred_tracks.append(track)
            log.info(
                f"[defer final] {track} {race_date}: per-tick fetch cap "
                f"({MAX_FETCHES_PER_TICK}) reached, deferring to next tick"
            )
            continue

        actions += 1
        finals_published_this_tick += 1
        if not publish_final(drf, state):
            failures.append(f"FINAL {track} {race_date}")
            save_state(state)

    save_state(state)

    log.info("=" * 60)
    if deferred_tracks:
        log.info(
            f"  Actions taken: {actions}  |  Failures: {len(failures)}  |  "
            f"Deferred: {deferred_tracks}"
        )
    else:
        log.info(f"  Actions taken: {actions}  |  Failures: {len(failures)}")
    if failures:
        log.error("  Failed: " + ", ".join(failures))
    log.info("=" * 60)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())