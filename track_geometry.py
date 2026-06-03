"""
DTS Automation Pipeline — track_geometry.py
=============================================
Maps (track, surface, distance_yards) -> number of turns in the race.

Distance is in yards (matching the BRISnet DRF `Distanceinyards` field).
Surface uses BRISnet's per-row codes:
    "D" = dirt (or all-weather/synthetic)
    "T" = main turf course (uppercase)
    "t" = inner turf (lowercase)

The function `get_turns(track, surface, distance_yards) -> int | None`
returns 1 or 2 for known configurations, or None for unknown cases.
Callers should treat None as "display a dash" in the PDF.

Coverage philosophy
-------------------
* Most US tracks are 1-mile dirt ovals. We use a default rule
  (sprints<=7f = 1 turn, routes >= 1mi = 2 turns) for tracks that
  follow this pattern.
* Per-track exceptions override the default. Known exceptions:
    CD   — 1mi via 7f chute = 1 turn (the famous "one-turn mile")
    LRL  — 1mi via chute = 1 turn (historic 7f chute)
    DEL  — 1mi via chute = 1 turn
    BEL  — 1.5-mile "Big Sandy": 1mi, 1 1/16, 1 1/8, 1 1/4 all
           run one-turn; 1 3/8+ go two turns
    FG   — 1-mile dirt oval but uses chute for some configurations;
           7.5f a one-turn, 1mi+ standard two-turn
* Turf course geometry varies wildly. We're conservative — when
  uncertain we leave the entry out (returns None) rather than guess.
  Easy to extend as we encounter unknown configurations on real cards.
* "All-weather" surface (Tapeta, Polytrack) follows dirt geometry
  since it's the main oval at synthetic-surface tracks (e.g. PID).

Adding a track or distance
--------------------------
Two ways:
  1. Add the track code to STANDARD_1MILE_DIRT_OVALS to apply the
     default rule.
  2. Add explicit entries to TRACK_TURNS for any exception or for
     turf configurations.

The dict key is (track_code_upper, surface, int_yards).
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


# Distances we care about, in yards. Maps furlong-fraction labels to yards.
# Each name is informational; the integer value is the lookup key.
DISTANCE_YARDS: dict[str, int] = {
    "4f":    880,
    "4.5f":  990,
    "5f":   1100,
    "5.5f": 1210,
    "6f":   1320,
    "6.5f": 1430,
    "7f":   1540,
    "7.5f": 1650,
    "1mi":  1760,
    "1_1/16": 1870,
    "1_1/8":  1980,
    "1_3/16": 2090,
    "1_1/4":  2200,
    "1_3/8":  2420,
    "1_1/2":  2640,
}

# Helper for readable construction below
_y = DISTANCE_YARDS


# Tracks whose dirt geometry follows the standard 1-mile-oval rule:
#   sprints <= 7f = 1 turn,  1 mile and longer = 2 turns
# This covers the vast majority of US dirt tracks. Tracks with quirks
# (chutes, big ovals) are NOT in this set; they live in TRACK_TURNS
# below with explicit entries.
STANDARD_1MILE_DIRT_OVALS: set[str] = {
    "SAR",   # 1 1/8 main but uses standard route layout (1-turn mile)
    "AQU",   # 1-mile main
    "SA",    # 1-mile main
    "GP",    # 1 1/8 main; sprints 1-turn, routes 2-turn
    "OP",    # 1-mile
    "DMR",   # 1-mile main
    "KEE",   # 1 1/16 main
    "KD",    # 1 5/16 turf primarily but has a dirt oval too
    "MTH",   # 1-mile
    "PRX",   # 1-mile
    "FG",    # 1-mile (chute for some distances — exceptions below)
    "PIM",   # 1-mile
    "TAM",   # 1-mile
    "WO",    # 1 1/8 main dirt
    "RP",    # 1-mile
    "IND",   # 1-mile
    "LS",    # 1-mile
    "PID",   # 1-mile synthetic
    "HOU",   # 1-mile
    "MVR",   # 1-mile
    "MNR",   # 1-mile
    "ZIA",   # 1-mile
    "ELP",   # 1-mile
    "TDN",   # 1-mile
    "SUN",   # 1-mile
    "EVD",   # 1-mile
    "CNL",   # 1 1/8 main with turf
    "PRM",   # 1-mile
    "TP",    # 1-mile synthetic
}


# Explicit (track, surface, distance_yards) -> turns overrides.
# Use for: every turf entry, every chute/quirk on dirt, BEL Big Sandy, etc.
# Anything in this dict trumps the default rule.
TRACK_TURNS: dict[tuple[str, str, int], int] = {

    # ── Churchill Downs ──────────────────────────────────────────────
    # Main dirt: 1-mile oval with a 7f backstretch chute.
    # The chute allows a one-turn dirt mile (Dirt Mile G1 setup).
    ("CD", "D", _y["4.5f"]): 1,
    ("CD", "D", _y["5f"]):   1,
    ("CD", "D", _y["5.5f"]): 1,
    ("CD", "D", _y["6f"]):   1,
    ("CD", "D", _y["6.5f"]): 1,
    ("CD", "D", _y["7f"]):   1,
    ("CD", "D", _y["1mi"]):  1,   # NB: one-turn via chute
    ("CD", "D", _y["1_1/16"]): 2,
    ("CD", "D", _y["1_1/8"]):  2,
    ("CD", "D", _y["1_3/16"]): 2,
    ("CD", "D", _y["1_1/4"]):  2,  # Kentucky Derby distance
    # CD turf (Matt Winn) — 7/8 mile (7f) inside main; very tight inner
    ("CD", "T", _y["5f"]):   1,
    ("CD", "T", _y["5.5f"]): 1,
    ("CD", "T", _y["6f"]):   1,
    ("CD", "T", _y["6.5f"]): 1,
    ("CD", "T", _y["1mi"]):  2,
    ("CD", "T", _y["1_1/16"]): 2,
    ("CD", "T", _y["1_1/8"]):  2,
    ("CD", "T", _y["1_3/16"]): 2,

    # ── Belmont Park (Big Sandy: 1.5-mile dirt oval) ──────────────────
    # The defining feature of BEL is one-turn races at distances that
    # would be two-turn anywhere else.
    ("BEL", "D", _y["5f"]):   1,
    ("BEL", "D", _y["5.5f"]): 1,
    ("BEL", "D", _y["6f"]):   1,
    ("BEL", "D", _y["6.5f"]): 1,
    ("BEL", "D", _y["7f"]):   1,
    ("BEL", "D", _y["1mi"]):  1,    # one-turn mile (famous)
    ("BEL", "D", _y["1_1/16"]): 1,
    ("BEL", "D", _y["1_1/8"]):  1,  # one-turn 9f
    ("BEL", "D", _y["1_1/4"]):  1,  # Belmont Stakes — historic one-turn
    ("BEL", "D", _y["1_3/8"]):  2,
    ("BEL", "D", _y["1_1/2"]):  2,
    # BEL turf — 1.5-mile widener, 1.25-mile inner. Both still very large.
    ("BEL", "T", _y["5f"]):   1,
    ("BEL", "T", _y["6f"]):   1,
    ("BEL", "T", _y["7f"]):   1,
    ("BEL", "T", _y["1mi"]):  1,    # widener mile = 1 turn on the BEL turf
    ("BEL", "T", _y["1_1/16"]): 1,
    ("BEL", "T", _y["1_1/8"]):  1,
    ("BEL", "T", _y["1_1/4"]):  1,
    ("BEL", "T", _y["1_3/8"]):  2,
    ("BEL", "T", _y["1_1/2"]):  2,
    # Inner turf (lowercase 't')
    ("BEL", "t", _y["5f"]):   1,
    ("BEL", "t", _y["6f"]):   1,
    ("BEL", "t", _y["7f"]):   1,
    ("BEL", "t", _y["1mi"]):  1,
    ("BEL", "t", _y["1_1/16"]): 2,
    ("BEL", "t", _y["1_1/8"]):  2,

    # ── Aqueduct: 1-mile main + inner dirt + turf ──────────────────────
    # Standard 1-mile dirt: sprints 1-turn, routes 2-turn (handled by default).
    # Turf course (7f inside main):
    ("AQU", "T", _y["6f"]):   1,
    ("AQU", "T", _y["6.5f"]): 1,
    ("AQU", "T", _y["7f"]):   1,
    ("AQU", "T", _y["1mi"]):  2,
    ("AQU", "T", _y["1_1/16"]): 2,
    ("AQU", "T", _y["1_1/8"]):  2,

    # ── Saratoga ──────────────────────────────────────────────────────
    # Main dirt is 1 1/8, but the route layout uses two turns for 1 1/16+.
    # 1mi is one-turn via backstretch chute.
    # (Default 1-mile-oval rule applied for sprints + 1 1/16+ = 2 turns.)
    ("SAR", "D", _y["1mi"]):  1,   # one-turn mile via Wilson Chute
    # Saratoga main turf (inner is 7f):
    ("SAR", "T", _y["5.5f"]): 1,
    ("SAR", "T", _y["6f"]):   1,
    ("SAR", "T", _y["1mi"]):  1,   # one-turn on outer (Mellon) turf
    ("SAR", "T", _y["1_1/16"]): 2,
    ("SAR", "T", _y["1_1/8"]):  2,
    ("SAR", "T", _y["1_3/8"]):  2,
    # Inner turf (lowercase t)
    ("SAR", "t", _y["5.5f"]): 1,
    ("SAR", "t", _y["1mi"]):  2,
    ("SAR", "t", _y["1_1/16"]): 2,
    ("SAR", "t", _y["1_1/8"]):  2,

    # ── Keeneland ─────────────────────────────────────────────────────
    # 1 1/16 main, 7.5f turf inside.
    # Default 1-mile-oval rule covers dirt. Turf:
    ("KEE", "T", _y["5.5f"]): 1,
    ("KEE", "T", _y["6f"]):   1,
    ("KEE", "T", _y["6.5f"]): 1,
    ("KEE", "T", _y["1mi"]):  2,
    ("KEE", "T", _y["1_1/16"]): 2,
    ("KEE", "T", _y["1_1/8"]):  2,
    ("KEE", "T", _y["1_3/16"]): 2,
    ("KEE", "T", _y["1_3/8"]):  2,

    # ── Santa Anita ───────────────────────────────────────────────────
    # 1-mile dirt; turf has the downhill chute (6.5f from hill).
    # Dirt: default rule.
    ("SA",  "T", _y["5f"]):   1,
    ("SA",  "T", _y["5.5f"]): 1,
    ("SA",  "T", _y["6f"]):   1,
    ("SA",  "T", _y["6.5f"]): 1,   # downhill course = 1 turn
    ("SA",  "T", _y["1mi"]):  2,
    ("SA",  "T", _y["1_1/16"]): 2,
    ("SA",  "T", _y["1_1/8"]):  2,
    ("SA",  "T", _y["1_1/4"]):  2,

    # ── Del Mar ───────────────────────────────────────────────────────
    # 1-mile main, 7.5f turf inside. Default rule for dirt.
    ("DMR", "T", _y["5f"]):   1,
    ("DMR", "T", _y["5.5f"]): 1,
    ("DMR", "T", _y["6f"]):   1,
    ("DMR", "T", _y["6.5f"]): 1,
    ("DMR", "T", _y["1mi"]):  2,
    ("DMR", "T", _y["1_1/16"]): 2,
    ("DMR", "T", _y["1_1/8"]):  2,
    ("DMR", "T", _y["1_3/8"]):  2,

    # ── Gulfstream Park ──────────────────────────────────────────────
    # 1 1/8 main dirt, 7f turf.
    # Default rule covers dirt sprints; routes use two turns:
    ("GP",  "T", _y["5f"]):   1,
    ("GP",  "T", _y["5.5f"]): 1,
    ("GP",  "T", _y["7.5f"]): 1,   # GP turf 7.5f one-turn (rare config)
    ("GP",  "T", _y["1mi"]):  2,
    ("GP",  "T", _y["1_1/16"]): 2,
    ("GP",  "T", _y["1_1/8"]):  2,
    ("GP",  "T", _y["1_3/16"]): 2,
    ("GP",  "T", _y["1_1/4"]):  2,

    # ── Tampa Bay Downs ──────────────────────────────────────────────
    # 1-mile main + 7f turf inside. Default for dirt; turf:
    ("TAM", "T", _y["5f"]):   1,
    ("TAM", "T", _y["1mi"]):  2,
    ("TAM", "T", _y["1_1/16"]): 2,
    ("TAM", "T", _y["1_1/8"]):  2,

    # ── Woodbine ──────────────────────────────────────────────────────
    # 1 1/8 main dirt, 1.5-mile E.P. Taylor turf course (the biggest in
    # North America), 7/8 mile inner turf.
    # Default for dirt. Turf has big-course quirks:
    ("WO",  "T", _y["6f"]):   1,
    ("WO",  "T", _y["7f"]):   1,
    ("WO",  "T", _y["1mi"]):  1,
    ("WO",  "T", _y["1_1/16"]): 1,
    ("WO",  "T", _y["1_1/8"]):  2,
    ("WO",  "T", _y["1_1/4"]):  2,
    ("WO",  "T", _y["1_3/8"]):  2,
    ("WO",  "T", _y["1_1/2"]):  2,

    # ── Kentucky Downs ────────────────────────────────────────────────
    # Turf-only meet on a unique kidney-shaped, undulating European-style
    # course. Effectively all races are run with one "turn" though the
    # geometry isn't a classic oval. Treating turf 7.5f+ as one turn here.
    ("KD",  "T", _y["5f"]):   1,
    ("KD",  "T", _y["5.5f"]): 1,
    ("KD",  "T", _y["6.5f"]): 1,
    ("KD",  "T", _y["7.5f"]): 1,
    ("KD",  "T", _y["1mi"]):  1,
    ("KD",  "T", _y["1_1/16"]): 1,
    # KD signature distances (not in DISTANCE_YARDS — explicit yards)
    ("KD",  "T", 2530): 1,    # 11.5f, KD signature
    ("KD",  "T", 1685): 1,    # 7 1/2f variant
    ("KD",  "T", 1750): 1,    # 1m 70 yd

    # ── Pimlico ───────────────────────────────────────────────────────
    # Standard 1-mile dirt; turf course also relatively standard.
    ("PIM", "T", _y["5f"]):   1,
    ("PIM", "T", _y["5.5f"]): 1,
    ("PIM", "T", _y["1mi"]):  2,
    ("PIM", "T", _y["1_1/16"]): 2,
    ("PIM", "T", _y["1_1/8"]):  2,
    ("PIM", "T", _y["1_3/16"]): 2,
    ("PIM", "T", _y["1_1/4"]):  2,

    # ── Laurel Park ───────────────────────────────────────────────────
    # 1 1/8 main dirt with the classic LRL turf course inside.
    # Note: LRL is NOT in STANDARD_1MILE_DIRT_OVALS — bigger oval needs
    # explicit entries.
    ("LRL", "D", _y["5f"]):   1,
    ("LRL", "D", _y["5.5f"]): 1,
    ("LRL", "D", _y["6f"]):   1,
    ("LRL", "D", _y["6.5f"]): 1,
    ("LRL", "D", _y["7f"]):   1,
    ("LRL", "D", _y["1mi"]):  1,   # one-turn mile via chute
    ("LRL", "D", _y["1_1/16"]): 2,
    ("LRL", "D", _y["1_1/8"]):  2,
    ("LRL", "T", _y["5f"]):   1,
    ("LRL", "T", _y["5.5f"]): 1,
    ("LRL", "T", _y["1mi"]):  2,
    ("LRL", "T", _y["1_1/16"]): 2,
    ("LRL", "T", _y["1_1/8"]):  2,

    # ── Oaklawn ───────────────────────────────────────────────────────
    # 1-mile main; no turf. Default rule covers all dirt.
    # (No explicit entries needed.)

    # ── Monmouth Park ─────────────────────────────────────────────────
    # 1-mile main, 7f turf inside.
    ("MTH", "T", _y["5f"]):   1,
    ("MTH", "T", _y["1mi"]):  2,
    ("MTH", "T", _y["1_1/16"]): 2,
    ("MTH", "T", _y["1_1/8"]):  2,
    ("MTH", "T", _y["1_3/16"]): 2,

    # ── Parx ──────────────────────────────────────────────────────────
    # 1-mile main, no turf. Default covers everything.

    # ── Fair Grounds ──────────────────────────────────────────────────
    # 1-mile main, 7f turf inside. Default for dirt; turf:
    ("FG",  "T", _y["5f"]):   1,
    ("FG",  "T", _y["5.5f"]): 1,
    ("FG",  "T", _y["1mi"]):  2,
    ("FG",  "T", _y["1_1/16"]): 2,
    ("FG",  "T", _y["1_1/8"]):  2,
    ("FG",  "T", _y["1_3/16"]): 2,
    ("FG",  "T", _y["1_1/4"]):  2,

    # ── Remington Park ────────────────────────────────────────────────
    # 1-mile main, turf only added recently (limited).
    ("RP",  "T", _y["1mi"]):  2,
    ("RP",  "T", _y["1_1/16"]): 2,

    # ── Indiana Grand (IND) ───────────────────────────────────────────
    # 1-mile main, 7f turf.
    ("IND", "T", _y["5f"]):   1,
    ("IND", "T", _y["5.5f"]): 1,
    ("IND", "T", _y["1mi"]):  2,
    ("IND", "T", _y["1_1/16"]): 2,

    # ── Lone Star Park ────────────────────────────────────────────────
    # 1-mile main + turf.
    ("LS",  "T", _y["5f"]):   1,
    ("LS",  "T", _y["1mi"]):  2,
    ("LS",  "T", _y["1_1/16"]): 2,

    # ── Delaware Park ─────────────────────────────────────────────────
    # 1-mile main with chute; turf inside.
    # DEL is NOT in default set — uses chute for mile.
    ("DEL", "D", _y["4.5f"]): 1,
    ("DEL", "D", _y["5f"]):   1,
    ("DEL", "D", _y["5.5f"]): 1,
    ("DEL", "D", _y["6f"]):   1,
    ("DEL", "D", _y["6.5f"]): 1,
    ("DEL", "D", _y["7f"]):   1,
    ("DEL", "D", _y["1mi"]):  1,   # one-turn mile via chute
    ("DEL", "D", _y["1_1/16"]): 2,
    ("DEL", "D", _y["1_1/8"]):  2,
    ("DEL", "T", _y["5f"]):   1,
    ("DEL", "T", _y["1mi"]):  2,
    ("DEL", "T", _y["1_1/16"]): 2,
    ("DEL", "T", _y["1_3/8"]):  2,

    # ── Presque Isle Downs ────────────────────────────────────────────
    # 1-mile synthetic Tapeta. No turf.

    # ── Sam Houston ───────────────────────────────────────────────────
    # 1-mile main + 7f turf.
    ("HOU", "T", _y["5f"]):   1,
    ("HOU", "T", _y["1mi"]):  2,
    ("HOU", "T", _y["1_1/16"]): 2,

    # ── Mahoning Valley, Mountaineer, Thistledown, Sunland, Evangeline,
    #    Zia, Ellis Park, Sunray — all 1-mile dirt ovals, default rule
    #    covers them. Turf typically absent or limited.
    # Ellis Park has a turf course (added recently):
    ("ELP", "T", _y["5f"]):   1,
    ("ELP", "T", _y["1mi"]):  2,
    ("ELP", "T", _y["1_1/16"]): 2,

    # ── Colonial Downs ────────────────────────────────────────────────
    # 1 1/4 dirt and a HUGE 1 1/8 turf course — the second-largest in NA.
    # CNL not in default set; explicit entries:
    ("CNL", "D", _y["5f"]):   1,
    ("CNL", "D", _y["5.5f"]): 1,
    ("CNL", "D", _y["6f"]):   1,
    ("CNL", "D", _y["6.5f"]): 1,
    ("CNL", "D", _y["7f"]):   1,
    ("CNL", "D", _y["1mi"]):  1,   # 1-turn mile on big oval
    ("CNL", "D", _y["1_1/16"]): 2,
    ("CNL", "D", _y["1_1/8"]):  2,
    ("CNL", "D", _y["1_1/4"]):  2,
    ("CNL", "T", _y["5f"]):   1,
    ("CNL", "T", _y["5.5f"]): 1,
    ("CNL", "T", _y["6f"]):   1,
    ("CNL", "T", _y["1mi"]):  1,   # 1-turn turf mile on the big oval
    ("CNL", "T", _y["1_1/16"]): 1,
    ("CNL", "T", _y["1_1/8"]):  2,
    ("CNL", "T", _y["1_3/16"]): 2,
    ("CNL", "T", _y["1_3/8"]):  2,

    # ── Prairie Meadows, Turfway Park ────────────────────────────────
    # PRM is 1-mile dirt; default rule. TP is 1-mile synthetic; default.
    # Neither has a turf course.
}


def get_turns(track: str, surface: str, distance_yards: int | float | None) -> int | None:
    """
    Look up the number of turns for a race configuration.

    Parameters
    ----------
    track : str
        BTSM/BRISnet track code (e.g. "CD", "SAR", "KEE"). Case-insensitive.
    surface : str
        Surface code: "D" dirt/synthetic, "T" main turf, "t" inner turf.
        Case-sensitive — inner-turf "t" is distinct from main turf "T".
    distance_yards : int | float | None
        Race distance from DRF `Distanceinyards`. Float OK; will be
        rounded to nearest int for lookup.

    Returns
    -------
    int | None
        1 or 2 if the configuration is known; None otherwise.
        Callers should display "—" for None.
    """
    if track is None or surface is None or distance_yards is None:
        return None
    try:
        d = int(round(float(distance_yards)))
    except (TypeError, ValueError):
        return None

    trk = track.upper()
    # Surface is case-sensitive for distinguishing "T" vs "t"; don't uppercase.
    key = (trk, surface, d)

    # Explicit entry wins
    if key in TRACK_TURNS:
        return TRACK_TURNS[key]

    # Default rule for standard 1-mile dirt ovals (dirt and synthetic).
    # We treat any non-turf surface as dirt for this rule. Turf is never
    # covered by the default — turf geometry must be explicit.
    if surface not in ("T", "t") and trk in STANDARD_1MILE_DIRT_OVALS:
        if d <= _y["7f"]:
            return 1
        if d >= _y["1mi"]:
            return 2

    # Unknown — return None so the PDF can show a dash.
    return None


# ─── CLI smoke test ──────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Look up turns for a (track, surface, distance) tuple")
    p.add_argument("track")
    p.add_argument("surface", help='"D", "T", or "t"')
    p.add_argument("distance_yards", type=int)
    args = p.parse_args()

    turns = get_turns(args.track, args.surface, args.distance_yards)
    if turns is None:
        print(f"{args.track} {args.surface} {args.distance_yards}yd: UNKNOWN")
    else:
        print(f"{args.track} {args.surface} {args.distance_yards}yd: {turns} turn{'s' if turns != 1 else ''}")
