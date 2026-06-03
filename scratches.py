"""
DTS Automation Pipeline — Scratches Module
=============================================
Fetches today's scratches from the Equibase RSS feed for a given track,
parses with feedparser, and returns a list of scratched program numbers
and horse names.

The Equibase per-track RSS URL pattern is:
    http://www.equibase.com/static/latechanges/rss/{EQB_CODE}-{COUNTRY}.rss

REAL FEED FORMAT (verified against live CD-USA.rss on 2026-05-08):
  - Each RSS <item> is a *bulletin* timestamped to when it was published.
  - Each bulletin's description is HTML with multiple change lines separated
    by <br />.  Typical lines (after HTML strip):

        Race 01: # 5 Temporarilyforever Scratched - PrivVet-Illness
        Race 04: #11 Amazon Time Scratched - Re-entered
        Race 04: # 8 Victor Valley Jockey - Andres Calleja changed to Alex Achard
        Race 09: Current Turf Track Condition - changed to Firm
        Race 09: #12 Brave Force Scratch Reason - Reason Unavailable changed to Also-Eligible
        Race 04: Temp Rail Distance set at 36 ft.
        Race 01: Superfecta Wagering Cancelled

  - Bulletins are CUMULATIVE: a later bulletin can update or reverse a
    change in an earlier bulletin.  Notably:
        * "Scratched - Re-entered"  -> horse is BACK IN THE RACE
        * "Scratch Reason ... changed to Also-Eligible" -> horse is BACK IN
    We track the latest state of each (race, program) and emit only those
    still actively scratched at parse time.

  - Program numbers may be space-padded for single digits (`# 5`) or
    unspaced for two digits (`#11`). Coupled entries: 1A, 2B, 3X.

Public API:
    get_scratches(track, race_date, year=None) -> list[ScratchEntry]
    fetch_all_changes(track) -> list[ChangeEntry]   (all change types)
    merge_with_manual(scratches, manual_list) -> list[ScratchEntry]
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape
from typing import Iterable, Optional

import feedparser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Track code mapping  (BTSM / BRISnet code  ->  Equibase code, country)
# ---------------------------------------------------------------------------
# Keep in sync with track_status.py — both modules need the same mapping.
#
# Covers the top 30 North American thoroughbred tracks by 2025 daily handle.
# BTSM codes that differ from Equibase codes (legacy BRISnet "X" suffix forms
# like CDX, GPX, OPX, TPX, FGX) are included as aliases mapping to the same
# Equibase code. Most tracks DTS and Equibase use the same code.
_BTSM_TO_EQB: dict[str, tuple[str, str]] = {
    # ── Original map (kept for backward compatibility) ───────────────────
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

    # ── Added 2026-05-12: top-30 tracks not previously mapped ────────────
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

_RSS_URL_TEMPLATE = "http://www.equibase.com/static/latechanges/rss/{code}-{country}.rss"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class ChangeEntry:
    """A single race-day change parsed from one line of an Equibase bulletin."""
    race: Optional[int]              # race number (1..N) or None if unparsed
    program_number: Optional[str]    # program number as string (e.g. "5", "1A")
    horse_name: Optional[str]        # horse name (Title Case in feed), or None
    change_type: str                 # "scratch", "reinstate", "jockey", "surface",
                                     # "track_cond", "wager", "rail", "equipment",
                                     # "weight", "trainer", "other"
    reason: str = ""                 # e.g. "PrivVet-Illness", "Trainer", or new value
    raw_text: str = ""               # original line (HTML stripped)
    bulletin_published: Optional[datetime] = None


@dataclass
class ScratchEntry:
    """A scratched horse — race number + program number + horse name."""
    race: int
    program_number: str
    horse_name: str = ""
    reason: str = ""
    source: str = "equibase_rss"     # "equibase_rss" or "manual"
    raw: str = ""

    def as_tuple(self) -> tuple[int, str]:
        """Compatible with config.MANUAL_SCRATCHES tuples."""
        return (self.race, self.program_number)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_scratches(
    track: str,
    race_date: str,
    year: Optional[str] = None,
    timeout: int = 15,
) -> list[ScratchEntry]:
    """
    Fetch today's scratches from Equibase RSS for the given track.

    Honors the cumulative nature of Equibase bulletins: a horse that is
    "Scratched - Re-entered" or has its scratch reason changed to
    "Also-Eligible" is treated as REINSTATED and excluded from the result.

    Parameters
    ----------
    track : str
        BTSM/BRISnet track code (e.g. "KEE", "CDX", "GPX") or Equibase
        code directly (e.g. "CD", "GP").
    race_date : str
        Race date in MMDD format (e.g. "0508" for May 8).
    year : str, optional
        4-digit year (e.g. "2026"). If None, uses current year.

    Returns
    -------
    list[ScratchEntry]
        One entry per still-scratched horse on the target date.
    """
    target = _parse_race_date(race_date, year)
    changes = fetch_all_changes(track, timeout=timeout)

    # Process bulletins in chronological order so later updates override earlier ones.
    changes_sorted = sorted(
        changes,
        key=lambda c: c.bulletin_published or datetime.min,
    )

    state: dict[tuple[int, str], ScratchEntry] = {}
    for c in changes_sorted:
        if c.race is None or c.program_number is None:
            continue
        key = (c.race, c.program_number)

        if c.change_type == "scratch":
            existing = state.get(key)
            state[key] = ScratchEntry(
                race=c.race,
                program_number=c.program_number,
                horse_name=c.horse_name or (existing.horse_name if existing else ""),
                reason=c.reason,
                source="equibase_rss",
                raw=c.raw_text,
            )
        elif c.change_type == "reinstate":
            # Horse is back in the race — drop from scratch list.
            state.pop(key, None)
        # other change types don't affect scratch state

    # Sanity check that the feed is for `target` (Equibase's per-track RSS
    # only ever publishes today's changes, but this catches stale feeds
    # if the script is run before the day's first bulletin).
    if changes_sorted:
        latest_pub = max(
            (c.bulletin_published for c in changes_sorted
             if c.bulletin_published is not None),
            default=None,
        )
        if latest_pub is not None and latest_pub.date() != target:
            logger.warning(
                "Equibase RSS latest bulletin is %s but target race date is %s — "
                "feed may be stale or for a different day. Returning anyway.",
                latest_pub.date().isoformat(), target.isoformat()
            )

    scratches = sorted(
        state.values(),
        key=lambda s: (s.race, _prog_sort_key(s.program_number)),
    )
    logger.info(
        "Equibase RSS: %d active scratches for %s on %s",
        len(scratches), track.upper(), target.isoformat()
    )
    return scratches


def fetch_all_changes(track: str, timeout: int = 15) -> list[ChangeEntry]:
    """
    Fetch and parse the Equibase RSS feed for a track. Returns ALL change
    lines (scratches, reinstates, jockey changes, surface changes, track
    condition updates, wager cancellations, rail distance updates, etc.).

    Useful for downstream modules that need jockey changes or track
    condition updates beyond just scratches.
    """
    url = _build_rss_url(track)
    logger.info("Fetching Equibase RSS: %s", url)

    parsed = feedparser.parse(
        url,
        request_headers={"User-Agent": "Mozilla/5.0 (BTSM Automation)"},
    )

    if parsed.bozo and not parsed.entries:
        reason = getattr(parsed, "bozo_exception", "unknown error")
        logger.warning("RSS parse problem for %s: %s", track, reason)
        return []

    out: list[ChangeEntry] = []
    for item in parsed.entries:
        title = (item.get("title") or "").strip()
        desc = (item.get("description") or item.get("summary") or "")
        published = _parse_pubdate(item)

        # Each bulletin's description contains multiple lines separated
        # by <br />. Split, strip HTML, and parse each.
        for line in _split_bulletin_lines(desc):
            change = _parse_line(line, published, bulletin_title=title)
            if change is not None:
                out.append(change)

    return out


def merge_with_manual(
    rss_scratches: Iterable[ScratchEntry],
    manual: Iterable[tuple[int, str]],
) -> list[ScratchEntry]:
    """
    Combine RSS-derived scratches with manually-listed scratches from
    config.MANUAL_SCRATCHES. RSS entries already present win on horse
    name and reason; manual-only entries are added with empty horse name.
    """
    by_key: dict[tuple[int, str], ScratchEntry] = {}
    for s in rss_scratches:
        by_key[(s.race, str(s.program_number))] = s

    for race, prog in manual:
        race = int(race)
        prog = str(prog)
        key = (race, prog)
        if key not in by_key:
            by_key[key] = ScratchEntry(
                race=race,
                program_number=prog,
                horse_name="",
                source="manual",
                raw=f"manual: race {race} program {prog}",
            )

    return sorted(
        by_key.values(),
        key=lambda s: (s.race, _prog_sort_key(s.program_number)),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _build_rss_url(track: str) -> str:
    """Map a BTSM/Equibase track code to the full RSS URL."""
    track = track.upper().strip()
    if track in _BTSM_TO_EQB:
        eqb_code, country = _BTSM_TO_EQB[track]
    else:
        logger.warning(
            "Track %r not in BTSM->Equibase map; assuming Equibase code %r in USA",
            track, track
        )
        eqb_code, country = track, "USA"
    return _RSS_URL_TEMPLATE.format(code=eqb_code, country=country)


def _parse_race_date(race_date: str, year: Optional[str]) -> date:
    """Parse MMDD + optional 4-digit year into a date object."""
    rd = race_date.strip()
    if len(rd) != 4 or not rd.isdigit():
        raise ValueError(f"race_date must be MMDD (4 digits), got {race_date!r}")
    mm = int(rd[:2])
    dd = int(rd[2:])
    yyyy = int(year) if year else datetime.now().year
    return date(yyyy, mm, dd)


def _parse_pubdate(entry) -> Optional[datetime]:
    """Convert feedparser's parsed time tuple to a datetime."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6])
    except (TypeError, ValueError):
        return None


def _prog_sort_key(prog: str) -> tuple:
    """Sort program numbers numerically with letter suffix as tiebreaker."""
    m = re.match(r"^(\d+)([A-Z]?)$", prog.upper())
    if m:
        return (int(m.group(1)), m.group(2))
    return (999, prog)


# ---- HTML splitting --------------------------------------------------------
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _split_bulletin_lines(html: str) -> list[str]:
    """Split an HTML bulletin into clean text lines (one per change)."""
    if not html:
        return []
    parts = _BR_RE.split(html)
    out: list[str] = []
    for p in parts:
        text = _HTML_TAG_RE.sub("", p)   # strip <b>, <i>, etc.
        text = unescape(text)            # decode &nbsp;, &amp;, etc.
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append(text)
    return out


# ---- Line parsing -----------------------------------------------------------
# "Race NN:" prefix
_RACE_LINE_RE = re.compile(r"^Race\s+(\d{1,2})\s*:\s*(.*)$", re.IGNORECASE)

# "# 5 HorseName" or "#11 HorseName" — capture program (digits + optional letter)
# and horse name (everything up to the next " Scratched", " Jockey", " Scratch Reason", end)
# Accepts straight or curly apostrophes in horse names.
_HORSE_LINE_RE = re.compile(
    r"""^\#\s*(\d{1,2}[A-Z]?)\s+        # program number
        ([A-Za-z][A-Za-z0-9 .'\u2019\-]*?)   # horse name (lazy)
        (?:\s+(Scratched|Scratch\ Reason|Jockey|Equipment|Weight|Trainer)\b
         |\s*$)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _parse_line(line: str, published: Optional[datetime],
                bulletin_title: str = "") -> Optional[ChangeEntry]:
    """
    Parse one cleaned-text line from a bulletin into a ChangeEntry.
    Returns None if the line is empty or doesn't start with 'Race NN:'.
    """
    m = _RACE_LINE_RE.match(line)
    if not m:
        return None
    race = int(m.group(1))
    body = m.group(2).strip()

    # Does the body start with a horse-specific change ("# N HorseName ...")?
    horse_match = _HORSE_LINE_RE.match(body)
    if horse_match:
        prog = horse_match.group(1).upper()
        horse = horse_match.group(2).strip()
        tail_start = horse_match.end(2)
        tail = body[tail_start:].strip()
        change_type, reason = _classify_horse_change(tail)
        return ChangeEntry(
            race=race,
            program_number=prog,
            horse_name=horse,
            change_type=change_type,
            reason=reason,
            raw_text=line,
            bulletin_published=published,
        )

    # Race-level change (no horse): track condition, wager, rail, etc.
    change_type, reason = _classify_race_level(body)
    return ChangeEntry(
        race=race,
        program_number=None,
        horse_name=None,
        change_type=change_type,
        reason=reason,
        raw_text=line,
        bulletin_published=published,
    )


def _classify_horse_change(tail: str) -> tuple[str, str]:
    """
    Given the text AFTER 'Race N: # X HorseName ', classify the change.
    Returns (change_type, reason).
    """
    t = tail.strip()
    low = t.lower()

    # --- Reinstatements (must check BEFORE generic "scratched") ---
    # "Scratched - Re-entered" — horse re-entered the race, no longer scratched.
    # NOTE: "Scratch Reason - X changed to Also-Eligible" is NOT a reinstatement.
    # AE horses are not running unless promoted, so they remain scratched for
    # scoring purposes. The reason field updates to capture the AE status.
    if low.startswith("scratched") and "re-entered" in low:
        return ("reinstate", "Re-entered")

    # --- Scratch reason update (still scratched, reason changed) ---
    # Examples:
    #   "Scratch Reason - Reason Unavailable changed to RegVet-Unsound"
    #   "Scratch Reason - Reason Unavailable changed to Also-Eligible"
    # Treat as a scratch with the NEW reason so cumulative state updates.
    #
    # SPECIAL CASE: "Scratch Reason - X changed to Re-entered" means the horse
    # WAS scratched, now is back in the race. This is a reinstatement, not a
    # scratch reason update. (Equibase's RSS sometimes posts the initial
    # scratch as "Scratched - Reason Unavailable" and then later revises the
    # reason to "Re-entered" via this delta path, instead of the more direct
    # "Scratched - Re-entered" form.)
    if low.startswith("scratch reason") and "changed to" in low:
        m = re.search(r"changed to\s+(.+?)\s*$", t, re.IGNORECASE)
        new_reason = m.group(1).strip() if m else _extract_reason_after_dash(t)
        if "re-entered" in new_reason.lower() or "reentered" in new_reason.lower():
            return ("reinstate", "Re-entered")
        return ("scratch", new_reason)

    # --- Plain scratch ---
    if low.startswith("scratched"):
        return ("scratch", _extract_reason_after_dash(t))

    # --- Jockey change ---
    if low.startswith("jockey"):
        return ("jockey", t)

    # --- Equipment / weight / trainer changes ---
    if low.startswith("equipment"):
        return ("equipment", t)
    if low.startswith("weight"):
        return ("weight", t)
    if low.startswith("trainer"):
        return ("trainer", t)

    return ("other", t)


def _classify_race_level(body: str) -> tuple[str, str]:
    """Classify a race-level (non-horse) change line."""
    low = body.lower()

    # Track condition: "Current Dirt Track Condition - changed to Fast"
    if "track condition" in low and "changed to" in low:
        m = re.search(r"changed to\s+([A-Za-z]+)", body, re.IGNORECASE)
        new_cond = m.group(1) if m else ""
        return ("track_cond", new_cond)

    # Surface change: "moved from Turf to Dirt" or "Off the Turf"
    if "off the turf" in low or "moved from" in low or "moved to" in low:
        return ("surface", body)

    # Wager cancellations: "Superfecta Wagering Cancelled"
    if "wagering cancelled" in low or "wager cancelled" in low:
        return ("wager", body)

    # Rail distance: "Temp Rail Distance set at 36 ft"
    if "rail distance" in low:
        return ("rail", body)

    return ("other", body)


def _extract_reason_after_dash(text: str) -> str:
    """Pull the reason after ' - ' in 'Scratched - <reason>'."""
    m = re.search(r"-\s*(.+)$", text)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Module CLI for ad-hoc testing / manual runs
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    p = argparse.ArgumentParser(description="Fetch Equibase scratches via RSS")
    p.add_argument("track", help="Track code (BTSM or Equibase, e.g. KEE, CDX, CD)")
    p.add_argument("race_date", help="MMDD (e.g. 0508)")
    p.add_argument("--year", default=None, help="4-digit year (default: current)")
    p.add_argument("--all", action="store_true",
                   help="Show all parsed change lines, not just scratches")
    args = p.parse_args()

    if args.all:
        for c in fetch_all_changes(args.track):
            prog = c.program_number or "—"
            horse = (c.horse_name or "")[:25]
            reason = c.reason[:30] if c.reason else ""
            race_str = str(c.race) if c.race else "—"
            print(f"[{c.change_type:11}] race={race_str:>2} prog={prog:<3} "
                  f"horse={horse:<25} reason={reason}")
    else:
        scr = get_scratches(args.track, args.race_date, args.year)
        if not scr:
            print(f"No active scratches found for {args.track} on {args.race_date}.")
        else:
            print(f"\n{len(scr)} active scratch(es):\n")
            for s in scr:
                print(f"  Race {s.race:>2}  #{s.program_number:<3}  "
                      f"{s.horse_name:<25}  ({s.reason})")
