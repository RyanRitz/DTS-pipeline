"""
BTSM Pipeline — pdf.py
========================
Generates the daily handicapping PDF from a scored DataFrame.

Layout: one race per US Letter page (portrait). Each page has:
  - Header band: track + date, BTSM logo (center), 1st post + conditions
  - TOP PICKS strip: per-race minimum contenders to reach cumulative ≥ 0.50,
    capped at 4 horses per race
  - Race header: race # + race type + surface/distance/purse/turns/par speed
  - Wager-types row from BRISnet WagerType1..9
  - Per-horse rows with:
      Line 1: # | name | BTSM odds | Prob2Win | Runs | Speed/Jockey/Trainer
              mini-bars | Smart Comment
      Line 2: Jockey/Trainer | ML
      LIKE/FADE panel: two columns, 3 reasons each
    Row highlight: green tint when BTSMOdds < MornLineOdds (value)
    Bold call-out when smart comment contains "$$$"
  - Footer: Daily $10 | URL | Full Meet $75

Public API:
    generate_pdf(scored_df, out_path, *, track, race_date, label="FINAL",
                 conditions=None, first_post=None, logo_path=None) -> Path

Wired into run_pipeline.generate_pdf() — replaces the stub at ~line 815.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_pdf(
    scored_df: pd.DataFrame,
    out_path: Path | str,
    *,
    track: str,
    race_date: str,                       # YYYYMMDD
    label: str = "FINAL",
    conditions: Optional[dict] = None,    # {"dirt": "Fast", "turf": "Firm"}
    first_post: Optional[str] = None,     # "1:00 PM"
    scratches_note: Optional[str] = None, # "No Scratches Updated" by default
    logo_path: Optional[Path | str] = None,
    track_full_name: Optional[str] = None,
    is_preview: bool = False,             # True before first scratches arrive
) -> Path:
    """
    Render the daily PDF.

    Returns the path to the generated file.

    Parameters
    ----------
    is_preview : bool
        When True (the morning before first scratches arrive), surface
        track conditions as 'TBD' regardless of what's in `conditions`.
        Once the first scratches roll in, the caller flips this to False
        and the real conditions are shown.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if scored_df.empty:
        raise ValueError("scored_df is empty — nothing to render")

    html = _build_html(
        scored_df,
        track=track,
        race_date=race_date,
        label=label,
        conditions=conditions or {},
        first_post=first_post,
        scratches_note=scratches_note or "No Scratches Updated",
        logo_path=logo_path,
        track_full_name=track_full_name or track,
        is_preview=is_preview,
    )

    # Local import so the module is loadable even when WeasyPrint isn't
    # installed (e.g. for unit tests that only build HTML).
    from weasyprint import HTML

    HTML(string=html, base_url=str(Path(__file__).parent)).write_pdf(str(out_path))
    logger.info(f"PDF written: {out_path}  ({out_path.stat().st_size:,} bytes)")
    return out_path


def _build_html(
    df: pd.DataFrame,
    *,
    track: str,
    race_date: str,
    label: str,
    conditions: dict,
    first_post: Optional[str],
    scratches_note: str,
    logo_path: Optional[Path | str],
    track_full_name: str,
    is_preview: bool,
) -> str:
    """Construct the HTML string. Separated for unit testing."""
    pretty_date = _format_date(race_date)
    logo_uri = _encode_logo(logo_path)

    races = sorted(df["race"].unique())
    top_picks_strip = _build_top_picks_strip(df, races)
    top3_selections = _select_top3_best_bets(df)

    # In PREVIEW mode, force conditions to TBD until the first scratch
    # update arrives. Once is_preview=False the real conditions show up.
    display_conditions = (
        {"dirt": "TBD", "turf": "TBD"} if is_preview else dict(conditions)
    )

    # Build the per-race pages, paginating big fields at PAGE_HORSE_CAP horses
    pages = []
    page_specs = _paginate_races(df, races)
    for i, spec in enumerate(page_specs):
        is_last = (i == len(page_specs) - 1)
        pages.append(_build_race_page(
            spec["race_df"],
            race_no=spec["race_no"],
            cont=spec["cont"],
            track_full_name=track_full_name,
            pretty_date=pretty_date,
            first_post=first_post,
            conditions=display_conditions,
            scratches_note=scratches_note,
            logo_uri=logo_uri,
            top_picks_strip=top_picks_strip,
            top3_selections=top3_selections,
            is_last_page=is_last,
            is_preview=is_preview,
        ))

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>BTSM {track} {pretty_date}</title>
<style>{_CSS}</style>
</head><body>
{"".join(pages)}
</body></html>"""


# ---------------------------------------------------------------------------
# Pagination — cap horses per page, spill rest to continuation pages
# ---------------------------------------------------------------------------

PAGE_HORSE_CAP = 10   # max horses per page


def _paginate_races(df: pd.DataFrame, races: list) -> list[dict]:
    """
    For each race, emit one or more page specs.  Each page has at most
    PAGE_HORSE_CAP horses.  Continuation pages get cont=N (the page number
    within the race, 2-based), used to label them "(continued)".
    """
    out = []
    for race in races:
        race_df = df[df["race"] == race].sort_values("rank").reset_index(drop=True)
        if len(race_df) <= PAGE_HORSE_CAP:
            out.append({"race_no": int(race), "race_df": race_df, "cont": 0})
        else:
            chunks = [race_df.iloc[i:i + PAGE_HORSE_CAP].reset_index(drop=True)
                      for i in range(0, len(race_df), PAGE_HORSE_CAP)]
            for j, chunk in enumerate(chunks):
                out.append({
                    "race_no": int(race),
                    "race_df": chunk,
                    "cont":    j,   # 0 = first page, 1+ = continuation
                })
    return out


# ---------------------------------------------------------------------------
# Top 3 BTSM Best Value Bets of the Day
# ---------------------------------------------------------------------------

def _select_top3_best_bets(df: pd.DataFrame) -> list[dict]:
    """
    Pick up to 3 horses that are BTSM Best Value Bets ($$$) — meaning their
    smart_comment contains '$$$', their rank is ≤ 3 in their race, AND
    BTSM odds beat the morning line.

    If more than 3 qualify, take the top 3 by ProbToWin descending.
    Returns dicts with race / program / horse / btsm_odds / ml_odds.
    """
    candidates = []
    for _, row in df.iterrows():
        smart = (row.get("smart_comment") or "")
        rank  = row.get("rank")
        btsm  = row.get("btsm_odds")
        ml    = row.get("ml_odds")
        if "$$$" not in smart:
            continue
        if not _is_num(rank) or int(rank) > 3:
            continue
        if not (_is_num(btsm) and _is_num(ml) and float(btsm) < float(ml)):
            continue
        candidates.append({
            "race":    int(row["race"]),
            "program": str(row["program"]),
            "horse":   row["horse"],
            "btsm":    float(btsm),
            "ml":      float(ml),
            "prob":    float(row.get("prob_to_win") or 0),
        })

    candidates.sort(key=lambda c: -c["prob"])
    return candidates[:3]


# ---------------------------------------------------------------------------
# Top-picks strip — cumulative-prob selection per race
# ---------------------------------------------------------------------------

def _select_top_picks_for_race(
    race_df: pd.DataFrame,
    threshold: float = 0.50,
    max_horses: int = 4,
) -> list[str]:
    """
    Sort by rank, accumulate prob_to_win, stop at first horse whose inclusion
    pushes the cumulative to ≥ threshold. Cap at max_horses.

    Returns a list of program numbers (strings) in rank order.
    """
    sorted_df = race_df.sort_values("rank").reset_index(drop=True)
    picks = []
    cum = 0.0
    for _, row in sorted_df.iterrows():
        picks.append(str(row["program"]))
        cum += float(row["prob_to_win"] or 0)
        if cum >= threshold:
            break
        if len(picks) >= max_horses:
            break
    return picks[:max_horses]


def _build_top_picks_strip(df: pd.DataFrame, races: list) -> str:
    """Render the TOP PICKS row that appears in every page header."""
    parts = []
    for race in races:
        race_df = df[df["race"] == race]
        picks = _select_top_picks_for_race(race_df)
        nums = "/".join(picks)
        parts.append(
            f'<span class="tp-race"><span class="tp-label">R{int(race)}:</span> '
            f'<span class="tp-nums">{nums}</span></span>'
        )
    return '<div class="top-picks-strip"><span class="tp-title">TOP PICKS</span>' \
           + " ".join(parts) + "</div>"


# ---------------------------------------------------------------------------
# Per-race page
# ---------------------------------------------------------------------------

def _build_race_page(
    race_df: pd.DataFrame,
    *,
    race_no: int,
    cont: int,                   # 0 = first page of race; 1+ = continuation
    track_full_name: str,
    pretty_date: str,
    first_post: Optional[str],
    conditions: dict,
    scratches_note: str,
    logo_uri: Optional[str],
    top_picks_strip: str,
    top3_selections: list[dict],
    is_last_page: bool,
    is_preview: bool,
) -> str:
    # Pull race-level fields from the first row (same for all horses in race)
    first = race_df.iloc[0]
    race_meta = _format_race_title(first)
    wager_types_str = _format_multi_race_wagers(first.get("wagers", []))

    # Per-horse rows
    horse_rows = "\n".join(
        _build_horse_row(row) for _, row in race_df.iterrows()
    )

    page_break_cls = "" if is_last_page else " page-break"

    # ── Conditions string (shows 'TBD' in preview mode) ─────────────────
    cond_parts = []
    if conditions.get("dirt"):
        cond_parts.append(f"Dirt: <b>{_html_escape(conditions['dirt'])}</b>")
    if conditions.get("turf"):
        cond_parts.append(f"Turf: <b>{_html_escape(conditions['turf'])}</b>")
    if not cond_parts and is_preview:
        cond_parts = ["Dirt: <b>TBD</b>", "Turf: <b>TBD</b>"]
    cond_str = " &nbsp; ".join(cond_parts)

    fp = f"1st Race: <b>{_html_escape(first_post)}</b>" if first_post else ""

    logo_html = (
        f'<img class="btsm-logo" src="{logo_uri}" alt="Be The Smart Money"/>'
        if logo_uri else ""
    )

    # ── Top 3 Best Bets card (right side of header) ─────────────────────
    # Best bets are filtered to only those in races that haven't been run
    # yet relative to THIS page. The bettor doesn't need to see a bet for
    # Race 2 once they're flipping to the Race 7 page.
    remaining_bets = [b for b in top3_selections if b["race"] >= race_no]
    if remaining_bets:
        bet_lines = "".join(
            f'<div class="bet-line"><span class="bet-race">R{b["race"]}</span> '
            f'<span class="bet-prog">#{_html_escape(b["program"])}</span> '
            f'<span class="bet-name">{_html_escape(b["horse"])}</span> '
            f'<span class="bet-odds">{_fmt_odds(b["btsm"])} / ML {_fmt_odds(b["ml"])}</span>'
            f'</div>'
            for b in remaining_bets
        )
        top3_html = (
            '<div class="top3-card">'
            '<div class="top3-title">TOP BTSM BEST BETS $$$</div>'
            f'{bet_lines}'
            '</div>'
        )
    elif top3_selections:
        # Best bets existed but all have passed
        top3_html = (
            '<div class="top3-card top3-empty">'
            '<div class="top3-title">TOP BTSM BEST BETS</div>'
            '<div class="bet-line empty">All $$$ best bets have passed</div>'
            '</div>'
        )
    else:
        # No best bets identified anywhere on the card
        top3_html = (
            '<div class="top3-card top3-empty">'
            '<div class="top3-title">TOP BTSM BEST BETS</div>'
            '<div class="bet-line empty">No $$$ best bets identified on this card</div>'
            '</div>'
        )

    # ── Continuation marker for big-field overflow pages ────────────────
    cont_marker = (
        f' <span class="cont-marker">(continued · pg {cont + 1})</span>'
        if cont > 0 else ""
    )

    return f"""
<section class="page{page_break_cls}">

  <header class="page-header">
    <div class="ph-left">
      <div class="ph-title">{_html_escape(track_full_name)}</div>
      <div class="ph-subtitle">{pretty_date}</div>
      <div class="ph-meta">{fp}</div>
      <div class="ph-meta">{cond_str}</div>
    </div>
    <div class="ph-mid">{logo_html}</div>
    <div class="ph-right">
      <div class="ph-url">www.BeTheSmartMoney.com</div>
      {top3_html}
    </div>
  </header>

  {top_picks_strip}

  <div class="race-header">
    <div class="rh-num">Race {race_no}{cont_marker}</div>
    <div class="rh-title">{race_meta['title']}</div>
    <div class="rh-middle">{race_meta['middle']}</div>
    <div class="rh-detail">{race_meta['detail']}</div>
  </div>

  <div class="race-meta">
    <span class="wagers">{_html_escape(wager_types_str)}</span>
  </div>

  <div class="horses">
    <div class="horse-col-header">
      <span class="hnum"></span>
      <span class="hname">Horse <span class="ml-inline">(ML)</span></span>
      <span class="hbtsm">BTSM</span>
      <span class="hprob">Prob</span>
      <span class="hruns">Runs</span>
      <span class="hbars">
        <span class="bar-hdr">Spd</span>
        <span class="bar-hdr">Jky</span>
        <span class="bar-hdr">Trn</span>
      </span>
      <span class="hcomment">Smart Comment</span>
    </div>
    {horse_rows}
  </div>

</section>
"""


# ---------------------------------------------------------------------------
# Per-horse row
# ---------------------------------------------------------------------------

def _build_horse_row(row: pd.Series) -> str:
    btsm = row.get("btsm_odds")
    ml   = row.get("ml_odds")
    smart = row.get("smart_comment", "") or ""

    # Value highlight when BTSM odds beat the morning line
    val_class = ""
    if _is_num(btsm) and _is_num(ml) and float(btsm) < float(ml):
        val_class = " value-row"
    if "$$$" in smart:
        val_class += " best-bet"

    speed_bar = int(float(row.get("speed_bar") or 0))
    jock_bar  = int(float(row.get("jockey_bar") or 0))
    train_bar = int(float(row.get("trainer_bar") or 0))

    runs = row.get("runs_label", "-") or "-"

    # Like/fade reasons — only show those that materially differ from
    # the race average. The `*_score` columns carry |delta / race_avg|
    # values from attribution.py (or simulated values in the mock).
    # We keep any with score > 0.20 (20% different from race average).
    # That means a horse may show 0, 1, 2, or 3 reasons per side —
    # variable, not always 3.
    REASON_THRESHOLD = 0.20

    def _select_reasons(prefix: str) -> list[str]:
        kept = []
        for i in (1, 2, 3):
            text = row.get(f"{prefix}_{i}", "") or ""
            if not text:
                continue
            score = row.get(f"{prefix}_{i}_score")
            # If no score is provided (legacy/production rows without the
            # field), keep the reason — be backwards compatible.
            if score is None or not _is_num(score):
                kept.append(text)
                continue
            if abs(float(score)) > REASON_THRESHOLD:
                kept.append(text)
        return kept

    likes = _select_reasons("why_like")
    fades = _select_reasons("why_fade")

    like_html = "".join(f'<li>✓ {_html_escape(l)}</li>' for l in likes) or '<li class="empty">—</li>'
    fade_html = "".join(f'<li>✗ {_html_escape(f)}</li>' for f in fades) or '<li class="empty">—</li>'

    horse_name = _html_escape(row.get("horse", "?"))
    program    = _html_escape(str(row.get("program", "")))
    jockey     = _html_escape(row.get("jockey", "") or "")
    trainer    = _html_escape(row.get("trainer", "") or "")

    btsm_txt = _fmt_odds(btsm)
    ml_txt   = _fmt_odds(ml)
    prob_val = row.get("prob_to_win")
    prob_txt = f"{float(prob_val) * 100:.0f}%" if _is_num(prob_val) else "—"

    return f"""
<div class="horse-block{val_class}">
  <div class="horse-line1">
    <span class="hnum">#{program}</span>
    <span class="hname">{horse_name} <span class="ml-inline">(ML {ml_txt})</span></span>
    <span class="hbtsm"><b>{btsm_txt}</b></span>
    <span class="hprob"><b>{prob_txt}</b></span>
    <span class="hruns">{_html_escape(str(runs))}</span>
    <span class="hbars">
      <span class="bar bar-speed"><span style="width:{speed_bar}%"></span></span>
      <span class="bar bar-jock"><span style="width:{jock_bar}%"></span></span>
      <span class="bar bar-trn"><span style="width:{train_bar}%"></span></span>
    </span>
    <span class="hcomment">{_html_escape(smart)}</span>
  </div>
  <div class="horse-line2">
    <span class="jt">{jockey} / {trainer}</span>
  </div>
  <div class="why-grid">
    <ul class="why-like">{like_html}</ul>
    <ul class="why-fade">{fade_html}</ul>
  </div>
</div>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_date(yyyymmdd: str) -> str:
    """
    Format YYYYMMDD as 'May 14, 2026'.

    We don't use %-d / %#d because the platform-specific codes are
    fragile (Windows uses %#d, Linux uses %-d, and a bad code raises
    a ValueError at runtime in the *strftime* call, not at parse).
    Instead we drop the leading zero by hand.
    """
    try:
        d = datetime.strptime(yyyymmdd, "%Y%m%d")
    except ValueError:
        return yyyymmdd
    return f"{d.strftime('%B')} {d.day}, {d.year}"


RACETYPE_MAP = {
    # Single letters used by Brisnet's DRF format. Anything not mapped
    # falls back to displaying the raw code (better than blank).
    "S":  "Maiden SpWt",
    "M":  "Maiden Claim",
    "C":  "Claiming",
    "N":  "Allow / NW",
    "A":  "Allowance",
    "AO": "Alw Opt. Claim",
    "G":  "Stakes",
    "R":  "Strtr Allw",
    "T":  "Trial",
}


def _is_num(x) -> bool:
    """True if x is a Python int/float OR a NumPy scalar number (np.int64 etc)."""
    if x is None:
        return False
    try:
        # NaN propagates as False, anything coercible to float is numeric
        f = float(x)
        return f == f  # rules out NaN
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Decimal → fractional odds conversion
# ---------------------------------------------------------------------------
# Standard horse-racing rounding table. We map continuous decimal odds to
# the nearest commonly-displayed fraction. Values ≥ 1 use a coarse grid
# (5/2, 7/2, 9/2, then whole numbers); values < 1 use heavy-favorite
# fractions (3/5, 2/5, 1/5) down to a floor of 1/9.
#
# Rules from spec:
#   - 3.5 → 7/2 (decimal-style with halves becomes fractional)
#   - 5   → 5    (denominator of 1 is dropped, no "/1" shown)
#   - 0.6 → 3/5  (under-1 uses fractional form too)
#   - anything below 1/9 (≈0.11) → "1/9" as the floor (heavy favorite cap)
#   - 1.0 → "1"  (even money rendered as just "1")
#
# Boundaries are inclusive-low: a horse rated 0.61 still shows as 3/5,
# not 2/5, until it crosses below the next breakpoint.

# Standard fractional-odds breakpoints — descending by decimal value
# so the first match-by-rounding wins. Each entry is (decimal, display).
_ODDS_TABLE: list[tuple[float, str]] = [
    # Below 1 — heavy favorites
    (0.05, "1/20"),
    (0.10, "1/10"),
    (0.11, "1/9"),    # floor — anything <= 0.11 also rounds here
    (0.13, "1/8"),
    (0.14, "1/7"),
    (0.17, "1/6"),
    (0.20, "1/5"),
    (0.25, "1/4"),
    (0.30, "3/10"),
    (0.40, "2/5"),
    (0.50, "1/2"),
    (0.60, "3/5"),
    (0.70, "7/10"),
    (0.80, "4/5"),
    (0.90, "9/10"),
    (1.00, "1"),       # even money, no "/1"
    (1.20, "6/5"),
    (1.40, "7/5"),
    (1.50, "3/2"),
    (1.60, "8/5"),
    (1.80, "9/5"),
    (2.00, "2"),
    (2.50, "5/2"),
    (3.00, "3"),
    (3.50, "7/2"),
    (4.00, "4"),
    (4.50, "9/2"),
    (5.00, "5"),
    (6.00, "6"),
    (7.00, "7"),
    (8.00, "8"),
    (9.00, "9"),
    (10.00, "10"),
    (12.00, "12"),
    (15.00, "15"),
    (20.00, "20"),
    (25.00, "25"),
    (30.00, "30"),
    (40.00, "40"),
    (50.00, "50"),
    (60.00, "60"),
    (75.00, "75"),
    (99.00, "99"),
]


def _fmt_odds(decimal_odds) -> str:
    """
    Convert decimal odds (e.g. 3.5) to traditional racing format (e.g. "7/2").

    - Whole-number odds drop the /1:           5.0  -> "5"
    - Even money is rendered as just "1":       1.0  -> "1"
    - Under-1 uses standard heavy-favorite fractions
    - Floor at 1/9 — anything below shows "1/9"
    - Non-numeric / NaN returns "—"
    """
    if not _is_num(decimal_odds):
        return "—"
    d = float(decimal_odds)
    if d <= 0:
        return "—"
    # Floor: anything at or below 0.11 is "1/9"
    if d <= 0.11:
        return "1/9"
    # Find the closest entry in the table by absolute difference
    best_display = _ODDS_TABLE[-1][1]
    best_diff = abs(d - _ODDS_TABLE[-1][0])
    for breakpoint_d, display in _ODDS_TABLE:
        diff = abs(d - breakpoint_d)
        if diff < best_diff:
            best_diff = diff
            best_display = display
    return best_display


def _format_race_title(row: pd.Series) -> dict:
    """
    Build the race-header parts.

    Layout (left to right):
      title   — race-type display ('Maiden SpWt', 'Claiming', 'Alw Opt. Claim')
      middle  — eligibility + class suffix + turns, joined by '·'.  Always
                non-empty if any of those pieces are available.
      detail  — surface · distance · purse (right-aligned in the header)
    """
    racetype_raw = (row.get("racetype") or "").strip().upper()
    racetype = RACETYPE_MAP.get(racetype_raw, racetype_raw)

    surface_code = (row.get("surface", "") or "").strip().upper()
    surface = {"D": "Dirt", "T": "Turf", "A": "All-Weather"}.get(
        surface_code, surface_code or "?")

    # ── Distance ───────────────────────────────────────────────────────
    dist_yd = row.get("dist_yd")
    if _is_num(dist_yd) and float(dist_yd) > 0:
        furlongs = float(dist_yd) / 220
        if abs(furlongs - round(furlongs)) < 0.05:
            dist_str = f"{int(round(furlongs))}f"
        else:
            dist_str = f"{furlongs:.1f}f"
    else:
        dist_str = "?"

    # ── Purse ──────────────────────────────────────────────────────────
    purse = row.get("purse")
    purse_str = f"${int(float(purse)/1000)}K" if _is_num(purse) and float(purse) > 0 else ""

    # ── Middle slot ────────────────────────────────────────────────────
    middle_parts = []

    # 1) Age + sex restrictions decoded from 3-letter code
    age_sex = (row.get("age_sex") or "").strip().upper()
    elig = _decode_age_sex(age_sex)
    if elig:
        middle_parts.append(elig)

    # 2) Class suffix from TodaysRaceClassification ('n2x' -> 'NW 2X')
    classif = (row.get("classif") or "").strip()
    suffix = _extract_nwx(classif)
    if suffix:
        middle_parts.append(suffix)

    # 3) Turns
    turns = row.get("turns")
    if _is_num(turns):
        n = int(turns)
        middle_parts.append(f"{n} Turn{'s' if n != 1 else ''}")

    # 4) Named race / feature (rare on day-to-day cards, common in stakes)
    race_name = (row.get("race_name") or "").strip()
    if race_name:
        middle_parts.insert(0, race_name)  # prepend so it leads

    middle = " &nbsp;·&nbsp; ".join(middle_parts)

    detail_parts = [surface, dist_str]
    if purse_str:
        detail_parts.append(purse_str)
    detail = " &nbsp;·&nbsp; ".join(detail_parts)

    return {
        "title":  _html_escape(racetype),
        "middle": middle,
        "detail": detail,
    }


# ────── Helpers for the race-header middle slot ─────────────────────────

# Age character (first letter of AgeSexRestrictions)
_AGE_MAP = {
    "A": "2YO",
    "B": "3YO+",
    "C": "4YO+",
    "D": "3YO",
    "E": "4YO",
    "F": "5YO+",
    "G": "3 & 4YO",
}
# Sex character (third letter)
_SEX_MAP = {
    "N": "Open",
    "F": "Fillies",
    "M": "F&M",          # Fillies & Mares
    "C": "Colts/Geldings",
}


def _decode_age_sex(code: str) -> str:
    """
    Decode 3-letter AgeSexRestrictions code (e.g. 'AOF', 'CUM', 'BUN')
    into a readable eligibility phrase.

      Position 1 (age):  A/B/C/D/E/F/G  → '2YO', '3YO+', '4YO+', ...
      Position 2 (mod):  O = Open, U = Upward, S = State-bred only
      Position 3 (sex):  N = Open, F = Fillies, M = F&M, C = Colts/Geldings

    Returns '' if code is unusable.
    """
    if not code or len(code) < 3:
        return ""
    age = _AGE_MAP.get(code[0])
    sex = _SEX_MAP.get(code[2])
    if not age and not sex:
        return ""
    if age and sex:
        return f"{sex} {age}" if sex != "Open" else f"Open {age}"
    return age or sex


# Match 'n2x', 'n1x', 'nw2x', 'NW 2 X' etc. and normalize to 'NW 2X'.
# Note we do NOT use \b before the 'n' because the suffix often follows
# a digit ('OClm 80000n2x') and digit/letter is not a word boundary.
import re as _re
_NWX_RE = _re.compile(r"n[wW]?\s*(\d+)\s*x\b", _re.IGNORECASE)


def _extract_nwx(classification: str) -> str:
    """
    Pull the 'non-winners of N times other than' suffix from classification
    text and render it as 'NW 2X', 'NW 1X', etc.  Returns '' if absent.
    """
    if not classification:
        return ""
    m = _NWX_RE.search(classification)
    if m:
        return f"NW {m.group(1)}X"
    return ""


def _format_multi_race_wagers(wagers) -> str:
    """
    Filter to multi-race wagers only: Daily Double, Pick 3, Pick 4, Pick 5,
    Pick 6.  Discard WPS, exactas, trifectas, superfectas, Super Hi-5,
    Odd vs Even.

    BRISnet packs wagers into slash-delimited compound strings like:
        'DAILY DOUBLE / EXACTA / TRIFECTA / SUPERFECTA / PICK 3 (RACES 1-2-3)'
    so we split on '/' first, then classify each fragment.

    Returns a clean comma-separated string, or '—' if nothing matches.
    """
    if not wagers:
        return "—"

    # Flatten + split compound strings on '/'
    pieces = []
    for raw in wagers:
        if not raw:
            continue
        for p in str(raw).split("/"):
            p = p.strip()
            if p:
                pieces.append(p)

    keep = []
    seen = set()
    for w in pieces:
        wu = w.upper()
        # Exact match on multi-race tokens.  Order matters: check the
        # longer match first ("PICK 3" before "PICK").
        if wu.startswith("DAILY DOUBLE") or wu == "DD":
            label = self_label("Daily Double", w)
        elif wu.startswith("PICK 3") or wu == "P3" or wu == "PK3":
            label = self_label("Pick 3", w)
        elif wu.startswith("PICK 4") or wu == "P4" or wu == "PK4":
            label = self_label("Pick 4", w)
        elif wu.startswith("PICK 5") or wu == "P5" or wu == "PK5":
            label = self_label("Pick 5", w)
        elif wu.startswith("PICK 6") or wu == "P6" or wu == "PK6":
            label = self_label("Pick 6", w)
        else:
            continue
        if label not in seen:
            seen.add(label)
            keep.append(label)

    if not keep:
        return "—"
    return "Multi-race wagers: " + " · ".join(keep)


def self_label(base: str, raw: str) -> str:
    """
    Preserve race-range parens when present.
        'PICK 3 (RACES 1-2-3)' -> 'Pick 3 (Races 1-2-3)'
    """
    if "(" in raw:
        rng = raw[raw.find("("):].title()
        return f"{base} {rng}"
    return base


def _encode_logo(logo_path: Optional[Path | str]) -> Optional[str]:
    """
    Read a logo file and return a data URI, or None if no logo.

    Browsers / WeasyPrint don't reliably render BMP, TIFF, etc. — only
    PNG, JPEG, GIF, WebP, SVG. If the operator drops in a less-supported
    format we transcode to PNG via PIL on the fly. PNG/JPEG/GIF/SVG pass
    through untouched.
    """
    if not logo_path:
        return None
    p = Path(logo_path)
    if not p.exists():
        logger.warning(f"Logo not found at {p}")
        return None

    ext = p.suffix.lower().lstrip(".")
    web_safe = {"png", "jpg", "jpeg", "gif", "webp", "svg"}

    if ext in web_safe:
        # Pass through unchanged
        data = p.read_bytes()
        if ext == "jpg":
            ext = "jpeg"
        return f"data:image/{ext};base64,{base64.b64encode(data).decode()}"

    # Convert via PIL → PNG. Pillow ships with pandas/openpyxl on most
    # Windows Python installs, but handle ImportError gracefully.
    try:
        from PIL import Image
        from io import BytesIO
        img = Image.open(p)
        # Drop alpha channel issues on some BMPs
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        buf = BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        return f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"
    except ImportError:
        logger.warning(
            f"Logo at {p} is .{ext} (not web-safe). "
            f"Install Pillow (`pip install Pillow`) to auto-convert, "
            f"or re-save the logo as PNG."
        )
        return None
    except Exception as e:
        logger.warning(f"Could not convert logo {p}: {e}")
        return None


def _html_escape(s: Any) -> str:
    if s is None:
        return ""
    s = str(s)
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = r"""
@page {
  size: Letter portrait;
  margin: 0.3in 0.3in 0.35in 0.3in;
}

body {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 9pt;
  color: #222;
  margin: 0; padding: 0;
}

.page { position: relative; }
.page-break { page-break-after: always; }

/* ── Page header ─────────────────────────────────────────────────────── */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 2px solid #4a7a2c;
  padding-bottom: 5pt;
  margin-bottom: 5pt;
  gap: 10pt;
}
.ph-left   { flex: 0 0 30%; }
.ph-mid    { flex: 1 1 auto; text-align: center; }
.ph-right  { flex: 0 0 32%; text-align: right; }

.ph-title    { font-size: 14pt; font-weight: bold; color: #2c4a16; line-height: 1.05; }
.ph-subtitle { font-size: 10pt; color: #555; }
.ph-meta     { font-size: 8pt; color: #555; margin-top: 1pt; line-height: 1.25; }

/* Logo — sized to fill the centered middle slot */
.btsm-logo {
  max-height: 64pt; max-width: 100%;
  display: inline-block;
}

/* URL on top-right */
.ph-url {
  font-size: 9.5pt; font-weight: bold; color: #2c4a16;
  margin-bottom: 3pt;
}

/* ── Top 3 BTSM Best Bets card (right side of header) ────────────────── */
.top3-card {
  background: #fcf8e3;
  border: 1pt solid #2c4a16;
  border-radius: 3pt;
  padding: 3pt 5pt 4pt 5pt;
  text-align: left;
  font-size: 7pt;
  line-height: 1.25;
}
.top3-empty { background: #f3f3f3; border-color: #aaa; }
.top3-title {
  font-weight: bold; color: #2c4a16; font-size: 7.5pt;
  letter-spacing: 0.3pt; margin-bottom: 2pt; text-align: center;
}
.bet-line { display: flex; gap: 3pt; align-items: baseline; }
.bet-line.empty { color: #888; font-style: italic; justify-content: center; }
.bet-race { font-weight: bold; color: #6b5d1c; min-width: 16pt; flex: 0 0 auto; }
.bet-prog { font-weight: bold; color: #2c4a16; min-width: 18pt; flex: 0 0 auto; }
.bet-name { flex: 1 1 auto; font-weight: bold; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bet-odds { font-size: 6.5pt; color: #555; flex: 0 0 auto; }

/* ── Top picks strip ────────────────────────────────────────────────── */
.top-picks-strip {
  background: #f4f1e6;
  border: 1pt solid #c4b76a;
  padding: 3pt 7pt;
  border-radius: 3pt;
  font-size: 8.5pt;
  margin-bottom: 4pt;
  line-height: 1.3;
}
.tp-title { font-weight: bold; color: #6b5d1c; margin-right: 7pt; letter-spacing: 0.5pt; }
.tp-race  { margin-right: 9pt; white-space: nowrap; }
.tp-label { color: #6b5d1c; }
.tp-nums  { font-weight: bold; color: #222; }

/* ── Race header ────────────────────────────────────────────────────── */
.race-header {
  background: #2c4a16;
  color: white;
  padding: 4pt 9pt;
  border-radius: 3pt 3pt 0 0;
  display: flex;
  align-items: baseline;
  gap: 10pt;
}
.rh-num     { font-size: 11pt; font-weight: bold; }
.rh-title   { font-size: 10.5pt; font-style: italic; }
.rh-middle  { font-size: 9pt; color: #d8e6c4; flex: 1; text-align: center; }
.rh-detail  { font-size: 10pt; font-weight: bold; margin-left: auto; }
.cont-marker {
  font-size: 8.5pt; font-style: italic; font-weight: normal;
  color: #d8e6c4; margin-left: 6pt;
}

.race-meta {
  background: #f0eee6;
  padding: 2.5pt 9pt;
  font-size: 8pt;
  color: #555;
  border-bottom: 1pt solid #ccc;
  margin-bottom: 3pt;
}
.wagers { color: #555; }

/* ── Horse row ──────────────────────────────────────────────────────── */
.horses { display: flex; flex-direction: column; gap: 0pt; }

/* Column header row (appears once above each race's horses) */
.horse-col-header {
  display: flex;
  align-items: center;
  gap: 5pt;
  font-size: 7pt;
  font-weight: bold;
  color: #6b5d1c;
  text-transform: uppercase;
  letter-spacing: 0.4pt;
  padding: 2pt 6pt 2pt 6pt;
  border-bottom: 1pt solid #c4b76a;
  background: #faf7ea;
}
.horse-col-header .ml-inline {
  font-weight: normal; color: #888; font-size: 6.5pt;
  text-transform: none; letter-spacing: 0;
}
.horse-col-header .hbars {
  display: inline-flex; gap: 1pt; align-items: center;
  width: 95pt; min-width: 95pt; flex: 0 0 95pt;
  justify-content: space-around;
}
.horse-col-header .bar-hdr {
  display: inline-block;
  width: 30pt;
  text-align: center;
  font-size: 6.5pt;
}

.horse-block {
  padding: 2.5pt 6pt 3pt 6pt;
  border-bottom: 1pt solid #e3e3e3;
  break-inside: avoid;
}
.horse-block.value-row { background: #eef7e3; }
.horse-block.best-bet  { border-left: 3pt solid #2c4a16; background: #e6f1d3; }

.horse-line1 {
  display: flex;
  align-items: center;
  gap: 5pt;
  font-size: 9pt;
  flex-wrap: nowrap;
}
.horse-line1 .hnum   { font-weight: bold; min-width: 20pt; color: #2c4a16; flex: 0 0 auto; }
.horse-line1 .hname  {
  font-weight: bold; min-width: 140pt; flex: 1 1 140pt;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.horse-line1 .hname .ml-inline {
  font-weight: normal; color: #888; font-size: 7.5pt; margin-left: 3pt;
}
.horse-line1 .hbtsm,
.horse-line1 .hprob,
.horse-line1 .hruns  {
  font-size: 9pt; flex: 0 0 auto;
  text-align: center;
}
.horse-line1 .hbtsm  { min-width: 32pt; }
.horse-line1 .hprob  { min-width: 32pt; }
.horse-line1 .hruns  { min-width: 32pt; font-size: 8pt; }
.horse-line1 .hcomment {
  font-style: italic; color: #2c4a16;
  text-align: right; font-size: 8.5pt;
  line-height: 1.15;
  flex: 0 0 auto;
  width: 118pt; max-width: 118pt;
  padding-right: 3pt;
}
.best-bet .hcomment  { font-weight: bold; }
.horse-col-header .hnum,
.horse-col-header .hname,
.horse-col-header .hbtsm,
.horse-col-header .hprob,
.horse-col-header .hruns,
.horse-col-header .hcomment {
  flex: 0 0 auto;
}
.horse-col-header .hnum    { min-width: 20pt; }
.horse-col-header .hname   {
  min-width: 140pt; flex: 1 1 140pt;
  color: #6b5d1c; font-weight: bold;
}
.horse-col-header .hbtsm   { min-width: 32pt; text-align: center; }
.horse-col-header .hprob   { min-width: 32pt; text-align: center; }
.horse-col-header .hruns   { min-width: 32pt; text-align: center; }
.horse-col-header .hcomment {
  width: 118pt; max-width: 118pt; text-align: right;
  padding-right: 3pt;
}

.horse-line2 {
  display: flex;
  font-size: 7.5pt;
  color: #555;
  padding-left: 22pt;
  gap: 8pt;
  margin-top: 0pt;
  line-height: 1.2;
}

/* ── Mini bars ──────────────────────────────────────────────────────── */
.horse-line1 .hbars {
  display: inline-flex; align-items: center; gap: 1pt;
  width: 95pt; min-width: 95pt; flex: 0 0 95pt;
  white-space: nowrap; overflow: hidden;
  justify-content: space-around;
}
.bar {
  display: inline-block; width: 28pt; height: 6pt;
  background: #e0e0e0; border-radius: 1pt; overflow: hidden;
  flex: 0 0 28pt;
}
.bar > span { display: block; height: 100%; max-width: 100%; }
.bar-speed > span { background: #7a8fc1; }
.bar-jock  > span { background: #e3a86b; }
.bar-trn   > span { background: #d77a4a; }

/* ── Like / Fade panel ──────────────────────────────────────────────── */
.why-grid {
  display: flex;
  gap: 14pt;
  margin: 1pt 0 0 22pt;
  font-size: 7.8pt;
}
.why-like, .why-fade {
  list-style: none; padding: 0; margin: 0;
  flex: 1;
}
.why-like li { color: #1c5d1c; line-height: 1.2; }
.why-fade li { color: #993316; line-height: 1.2; }
.why-like li.empty,
.why-fade li.empty { color: #aaa; }
"""
