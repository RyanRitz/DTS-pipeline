"""
DTS Automation Pipeline — Track Status Module (Selenium)
============================================================
Fetches today's first-post time and current track conditions from the
Equibase per-track HTML page using a real Chrome browser.

Why Selenium and not urllib?
  Equibase's HTML pages sit behind Imperva (Incapsula) bot protection,
  which serves a "Pardon Our Interruption" JS challenge to plain HTTP
  clients. A real browser passes the challenge; urllib never does.
  The RSS feed is open (urllib works there), but RSS only emits CHANGE
  events — it doesn't tell us today's first post or the current baseline
  track condition. Those live on the HTML page.

Why a separate module from scratches.py?
  - RSS = stream of change events (what changed since last bulletin)
  - HTML = current-state snapshot (what's true right now)
  Different sources, different fetch costs (RSS <1s, HTML 5-15s),
  different failure modes. Keeping them separate lets the pipeline make
  per-source resilience decisions independently.

Equibase per-track HTML URL pattern:
    https://www.equibase.com/static/latechanges/html/latechanges{EQB}-{COUNTRY}.html

Public API:
    get_track_status(track, race_date, year=None) -> TrackStatus
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Track code mapping  (BTSM / BRISnet code -> Equibase code, country)
# ---------------------------------------------------------------------------
# Mirrors scratches.py — keep in sync if new tracks are added.
#
# Covers the top 30 North American thoroughbred tracks by 2025 daily handle.
# BTSM codes that differ from Equibase codes (legacy BRISnet "X" suffix forms
# like CDX, GPX, OPX, TPX, FGX) are included as aliases mapping to the same
# Equibase code. Several tracks where BTSM and Equibase use the same code
# (most of them) are listed in a single-entry form.
_BTSM_TO_EQB: dict[str, tuple[str, str]] = {
    # ── Already in original map ───────────────────────────────────────────
    "KEE": ("KEE", "USA"),   # Keeneland
    "CDX": ("CD",  "USA"),   # Churchill Downs (legacy BRISnet alias)
    "CD":  ("CD",  "USA"),
    "SAR": ("SAR", "USA"),   # Saratoga
    "DMR": ("DMR", "USA"),   # Del Mar
    "GPX": ("GP",  "USA"),   # Gulfstream Park (legacy BRISnet alias)
    "GP":  ("GP",  "USA"),
    "AQU": ("AQU", "USA"),   # Aqueduct
    "BEL": ("BEL", "USA"),   # Belmont Park
    "OPX": ("OP",  "USA"),   # Oaklawn Park (legacy BRISnet alias)
    "OP":  ("OP",  "USA"),
    "TPX": ("TP",  "USA"),   # Turfway Park (legacy BRISnet alias)
    "TP":  ("TP",  "USA"),
    "FGX": ("FG",  "USA"),   # Fair Grounds (legacy BRISnet alias)
    "FG":  ("FG",  "USA"),
    "TAM": ("TAM", "USA"),   # Tampa Bay Downs
    "WO":  ("WO",  "CAN"),   # Woodbine

    # ── Added 2026-05-11: top-30 tracks not previously mapped ─────────────
    "SA":  ("SA",  "USA"),   # Santa Anita Park (#4)
    "KD":  ("KD",  "USA"),   # Kentucky Downs (#10)
    "MTH": ("MTH", "USA"),   # Monmouth Park (#11)
    "PRX": ("PRX", "USA"),   # Parx Racing (#12)
    "PIM": ("PIM", "USA"),   # Pimlico (#14)
    "RP":  ("RP",  "USA"),   # Remington Park (#17)
    "IND": ("IND", "USA"),   # Horseshoe Indianapolis (#18)
    "LRL": ("LRL", "USA"),   # Laurel Park (#19)
    "LS":  ("LS",  "USA"),   # Lone Star Park (#20)
    "DEL": ("DEL", "USA"),   # Delaware Park (#22)
    "PID": ("PID", "USA"),   # Presque Isle Downs (#24)
    "HOU": ("HOU", "USA"),   # Sam Houston Race Park (#25)
    "MVR": ("MVR", "USA"),   # Mahoning Valley (#26)
    "MNR": ("MNR", "USA"),   # Mountaineer (#27)
    "ZIA": ("ZIA", "USA"),   # Zia Park (#28)
    "ELP": ("ELP", "USA"),   # Ellis Park (#29)
    "TDN": ("TDN", "USA"),   # Thistledown (#30)
    "SUN": ("SUN", "USA"),   # Sunland Park (#31)
    "EVD": ("EVD", "USA"),   # Evangeline Downs (#32)
    "CNL": ("CNL", "USA"),   # Colonial Downs (#33)
    "PRM": ("PRM", "USA"),   # Prairie Meadows (#34)
}

_HTML_URL_TEMPLATE = (
    "https://www.equibase.com/static/latechanges/html/"
    "latechanges{code}-{country}.html"
)

# Reuse the persistent Chrome profile that already exists from the BRISnet
# downloader. Imperva trust cookies live in here and will persist across
# pipeline ticks, avoiding repeated full JS challenges.
_DEFAULT_PROFILE_DIR = (
    Path(r"C:\Users\ryanr\Documents\BTSM\FullAutomation\chrome_selenium_profile")
)

# Default fetch timing
_DEFAULT_PAGE_LOAD_TIMEOUT = 30   # max wall-clock seconds for the page itself
_IMPERVA_SETTLE_SECONDS    = 8    # extra wait for the JS challenge to clear
_DEFAULT_TIMEOUT_TOTAL     = 30   # total budget in seconds (safeguard)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------
@dataclass
class TrackStatus:
    """Snapshot of a track's race-day status from the Equibase HTML page."""
    track: str                              # input code, normalized upper
    race_date: date                         # the date being asked about
    first_post: Optional[datetime] = None   # naive ET; race_date + HH:MM
    dirt_condition: Optional[str] = None    # "Fast", "Good", "Sloppy", etc.
    turf_condition: Optional[str] = None    # "Firm", "Good", "Yielding", etc.
    last_updated_raw: Optional[str] = None  # original text, e.g. "May 8, 1:14 PM ET"
    fetched_at: Optional[datetime] = None
    source_url: str = ""
    fetch_ok: bool = False                  # True if Imperva was beaten
    fetch_error: Optional[str] = None       # human-readable reason if not ok

    def as_dict(self) -> dict:
        """JSON-serializable summary for pipeline_state.json."""
        return {
            "track":            self.track,
            "race_date":        self.race_date.isoformat() if self.race_date else None,
            "first_post":       self.first_post.isoformat(timespec="minutes")
                                if self.first_post else None,
            "dirt_condition":   self.dirt_condition,
            "turf_condition":   self.turf_condition,
            "last_updated_raw": self.last_updated_raw,
            "fetched_at":       self.fetched_at.isoformat(timespec="seconds")
                                if self.fetched_at else None,
            "source_url":       self.source_url,
            "fetch_ok":         self.fetch_ok,
            "fetch_error":      self.fetch_error,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_track_status(
    track: str,
    race_date: str,
    year: Optional[str] = None,
    *,
    profile_dir: Optional[Path] = None,
    headless: bool = True,
) -> TrackStatus:
    """
    Fetch and parse the Equibase per-track HTML page for first-post time
    and current track conditions.

    Parameters
    ----------
    track : str
        BTSM/BRISnet track code (e.g. "KEE", "CDX", "GPX") or Equibase
        code directly (e.g. "CD", "GP").
    race_date : str
        Race date in MMDD format (e.g. "0508" for May 8).
    year : str, optional
        4-digit year. If None, uses current year.
    profile_dir : Path, optional
        Chrome user-data directory to reuse. Defaults to the existing
        BRISnet `chrome_selenium_profile/`. Reusing the profile keeps
        Imperva's trust cookie across runs so most ticks don't re-solve
        the JS challenge.
    headless : bool
        Run Chrome headless (default True). Set False to watch the page
        load when debugging.

    Returns
    -------
    TrackStatus
        Always returns an object — fields are None and `fetch_ok=False`
        if the page couldn't be retrieved or the markers couldn't be
        found. Never raises on network/parse problems.
    """
    track_upper = track.upper().strip()
    target_date = _parse_race_date(race_date, year)
    url = _build_html_url(track_upper)

    status = TrackStatus(
        track=track_upper,
        race_date=target_date,
        fetched_at=datetime.now(),
        source_url=url,
    )

    html = _fetch_with_selenium(
        url,
        profile_dir=profile_dir or _DEFAULT_PROFILE_DIR,
        headless=headless,
    )
    if not html:
        status.fetch_error = status.fetch_error or "no html returned"
        return status

    if "Pardon Our Interruption" in html:
        status.fetch_error = "Imperva block page received"
        logger.warning(
            "Track status %s: Imperva blocked the request (no trust cookie yet)",
            track_upper,
        )
        return status

    status.fetch_ok = True
    _parse_into(html, status)

    logger.info(
        "Track status %s on %s: first_post=%s dirt=%s turf=%s",
        track_upper,
        target_date.isoformat(),
        status.first_post.strftime("%H:%M") if status.first_post else "?",
        status.dirt_condition or "?",
        status.turf_condition or "?",
    )
    return status


# ---------------------------------------------------------------------------
# Selenium fetch
# ---------------------------------------------------------------------------
def _fetch_with_selenium(
    url: str,
    *,
    profile_dir: Path,
    headless: bool,
) -> Optional[str]:
    """
    Launch Chrome via Selenium, navigate to `url`, wait for the Imperva
    challenge to settle, return the page HTML.  Returns None on failure.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.common.exceptions import (
            TimeoutException, WebDriverException,
        )
    except ImportError as e:
        logger.error(
            "Selenium not installed (%s). Run: pip install selenium", e
        )
        return None

    # Use a fresh temp profile for each Chrome launch.
    # WHY: Selenium back-to-back launches against the SAME --user-data-dir
    # collide on prefs file locks (`session not created: failed to write
    # prefs file`). The persistent BRISnet profile is meant for ONE long-
    # running session, not 25 quick fetches per pipeline tick.
    # We trade a slightly slower first hit (Imperva re-solves the JS
    # challenge each time, ~5s) for reliability.
    import tempfile
    import shutil as _shutil
    tmp_profile = Path(tempfile.mkdtemp(prefix="btsm_eqb_"))

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument(f"--user-data-dir={tmp_profile}")
    # A normal viewport size — Imperva flags weird ones.
    opts.add_argument("--window-size=1280,900")
    # Hide the "I'm a bot" navigator.webdriver flag.
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    # Quieter stderr.
    opts.add_argument("--log-level=3")
    opts.add_argument("--silent")

    driver = None
    try:
        logger.info("Launching Chrome (headless=%s) for %s", headless, url)
        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(_DEFAULT_PAGE_LOAD_TIMEOUT)

        # Mask navigator.webdriver  (Imperva and similar check this property)
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source":
                    "Object.defineProperty(navigator, 'webdriver', "
                    "{get: () => undefined});"},
            )
        except Exception:
            pass  # not all driver versions support this — non-fatal

        try:
            driver.get(url)
        except TimeoutException:
            logger.warning("Page load exceeded %ds, continuing with what loaded",
                           _DEFAULT_PAGE_LOAD_TIMEOUT)

        # Wait for Imperva to finish its JS challenge and reload to the real page.
        # The challenge typically takes 3-8 seconds and ends with a full
        # navigation. We poll for a marker that only appears on the real page.
        deadline = time.time() + _DEFAULT_TIMEOUT_TOTAL
        last_html = ""
        not_found_check_done = False
        while time.time() < deadline:
            try:
                last_html = driver.page_source
            except WebDriverException:
                last_html = ""

            # Fast-fail: detect tracks that Equibase doesn't publish (404,
            # missing page, etc.). Many small tracks aren't covered. Wait a
            # couple seconds first to give the real page a chance to load,
            # then bail rather than waiting the full 30s.
            elapsed = time.time() - (deadline - _DEFAULT_TIMEOUT_TOTAL)
            if not not_found_check_done and elapsed > 3.0 and last_html:
                low = last_html.lower()
                # Equibase 404s and missing-track pages are short and
                # contain none of our markers. If we're past the Imperva
                # window AND the page is small AND has no markers, treat
                # it as "track not covered" and bail.
                page_size = len(last_html)
                has_marker = (
                    "Scheduled First Post" in last_html
                    or "Current Track Conditions" in last_html
                    or "Today's Race Day Changes" in last_html
                )
                has_imperva = "Pardon Our Interruption" in last_html
                # 404 indicators
                is_not_found = (
                    "404" in last_html[:500] and "not found" in low
                    or "page not found" in low
                    or "page cannot be displayed" in low
                    or page_size < 500   # truly empty response
                )
                if is_not_found and not has_imperva and not has_marker:
                    logger.info(
                        "Equibase has no page for this track (likely not covered)"
                    )
                    return None
                not_found_check_done = True

            # Markers that tell us we have the REAL page:
            if last_html and (
                "Scheduled First Post" in last_html
                or "Current Track Conditions" in last_html
                or "Today's Race Day Changes" in last_html
            ):
                # Got it. Optional small settle for any late-loading content.
                time.sleep(1.0)
                last_html = driver.page_source
                return last_html
            # If we explicitly see the block page AND the challenge has
            # had time to complete, no point waiting longer.
            if "Pardon Our Interruption" in last_html and \
               (time.time() - (deadline - _DEFAULT_TIMEOUT_TOTAL)) > _IMPERVA_SETTLE_SECONDS:
                logger.warning("Imperva block persisted after challenge window")
                return last_html
            time.sleep(0.5)

        logger.warning("Timed out waiting for real page content")
        return last_html or None

    except WebDriverException as e:
        logger.warning("Chrome/Selenium error: %s", e)
        return None
    except Exception as e:
        logger.warning("Unexpected fetch error: %s", e)
        return None
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        # Best-effort cleanup of the temp profile dir
        try:
            _shutil.rmtree(tmp_profile, ignore_errors=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# HTML parsing  (same as the urllib version — operates on stripped text)
# ---------------------------------------------------------------------------
_FIRST_POST_RE = re.compile(
    r"Scheduled\s+First\s+Post\s*:?\s*"
    r"(\d{1,2}):(\d{2})\s*"
    r"(AM|PM)"
    r"(?:\s*ET)?",
    re.IGNORECASE,
)
_DIRT_RE = re.compile(
    r"\bDirt\s*:?\s*"
    r"(Fast|Wet[\s-]?Fast|Good|Muddy|Sloppy|Slow|Heavy|Frozen|Sealed)",
    re.IGNORECASE,
)
_TURF_RE = re.compile(
    r"\bTurf\s*:?\s*"
    r"(Firm|Good|Yielding|Soft|Heavy|Hard)",
    re.IGNORECASE,
)
_AW_RE = re.compile(
    r"\b(?:All[\s-]?Weather|Inner[\s-]?Track|Synthetic|Tapeta|Polytrack)\s*:?\s*"
    r"(Fast|Standard|Wet[\s-]?Fast|Sloppy|Slow)",
    re.IGNORECASE,
)
_LAST_UPDATED_RE = re.compile(
    r"Last\s+Updated\s*:?\s*"
    r"([A-Za-z]+\s+\d{1,2}(?:,\s*\d{4})?,?\s*\d{1,2}:\d{2}\s*(?:AM|PM)\s*ET)",
    re.IGNORECASE,
)


def _parse_into(html: str, status: TrackStatus) -> None:
    """Extract first post + conditions from `html` into `status`."""
    text = _strip_html(html)

    # First post
    m = _FIRST_POST_RE.search(text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        meridiem = m.group(3).upper()
        if meridiem == "PM" and hour != 12:
            hour += 12
        elif meridiem == "AM" and hour == 12:
            hour = 0
        try:
            status.first_post = datetime(
                status.race_date.year,
                status.race_date.month,
                status.race_date.day,
                hour,
                minute,
            )
        except ValueError as e:
            logger.warning("First post parse: bad time %s:%s %s (%s)",
                           hour, minute, meridiem, e)

    m = _DIRT_RE.search(text)
    if m:
        status.dirt_condition = _normalize_condition(m.group(1))

    m = _TURF_RE.search(text)
    if m:
        status.turf_condition = _normalize_condition(m.group(1))

    if status.dirt_condition is None:
        m = _AW_RE.search(text)
        if m:
            status.dirt_condition = _normalize_condition(m.group(1))

    m = _LAST_UPDATED_RE.search(text)
    if m:
        status.last_updated_raw = m.group(1).strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    """Remove HTML tags, decode entities, collapse whitespace."""
    text = _HTML_TAG_RE.sub(" ", html)
    text = unescape(text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _normalize_condition(raw: str) -> str:
    """Title-case a condition string, normalizing spacing/hyphens."""
    s = raw.strip()
    s = re.sub(r"\s+", " ", s)
    s = s.replace(" ", "-").replace("--", "-")
    return s.title()


def _build_html_url(track: str) -> str:
    """Map a BTSM/Equibase track code to the per-track HTML URL."""
    track = track.upper().strip()
    if track in _BTSM_TO_EQB:
        eqb_code, country = _BTSM_TO_EQB[track]
    else:
        logger.warning(
            "Track %r not in BTSM->Equibase map; assuming Equibase code %r in USA",
            track, track
        )
        eqb_code, country = track, "USA"
    return _HTML_URL_TEMPLATE.format(code=eqb_code, country=country)


def _parse_race_date(race_date: str, year: Optional[str]) -> date:
    """Parse MMDD + optional 4-digit year into a date object."""
    rd = race_date.strip()
    if len(rd) != 4 or not rd.isdigit():
        raise ValueError(f"race_date must be MMDD (4 digits), got {race_date!r}")
    mm = int(rd[:2])
    dd = int(rd[2:])
    yyyy = int(year) if year else datetime.now().year
    return date(yyyy, mm, dd)


# ---------------------------------------------------------------------------
# CLI for ad-hoc testing
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    p = argparse.ArgumentParser(
        description="Fetch first post + track conditions from Equibase via Selenium"
    )
    p.add_argument("track", help="Track code (BTSM or Equibase, e.g. KEE, CD)")
    p.add_argument("race_date", help="MMDD (e.g. 0508)")
    p.add_argument("--year", default=None, help="4-digit year (default: current)")
    p.add_argument("--show-browser", action="store_true",
                   help="Run Chrome in non-headless mode (debugging)")
    p.add_argument("--profile", default=None,
                   help="Override Chrome profile dir (default: existing BRISnet profile)")
    p.add_argument("--save-html", default=None,
                   help="If set, save raw fetched HTML to this path for debugging")
    args = p.parse_args()

    status = get_track_status(
        args.track, args.race_date, args.year,
        profile_dir=Path(args.profile) if args.profile else None,
        headless=not args.show_browser,
    )

    if args.save_html:
        # Re-run with HTML capture if asked. (Cheap second call OK for debugging.)
        # We don't store HTML in TrackStatus to avoid bloating pipeline_state.json.
        html = _fetch_with_selenium(
            status.source_url,
            profile_dir=Path(args.profile) if args.profile else _DEFAULT_PROFILE_DIR,
            headless=not args.show_browser,
        )
        if html:
            Path(args.save_html).write_text(html, encoding="utf-8")
            print(f"  Saved raw HTML to {args.save_html} ({len(html)} chars)")

    print()
    print(f"  Track:           {status.track}")
    print(f"  Race date:       {status.race_date.isoformat() if status.race_date else '?'}")
    print(f"  Fetch ok:        {status.fetch_ok}")
    if status.fetch_error:
        print(f"  Fetch error:     {status.fetch_error}")
    fp = (status.first_post.strftime("%I:%M %p ET").lstrip("0")
          if status.first_post else "(not found)")
    print(f"  First post:      {fp}")
    print(f"  Dirt:            {status.dirt_condition or '(not found)'}")
    print(f"  Turf:            {status.turf_condition or '(not found)'}")
    print(f"  Last updated:    {status.last_updated_raw or '(not found)'}")
    print(f"  Source:          {status.source_url}")
