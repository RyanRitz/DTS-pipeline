"""
DTS Pipeline — pdf.py
========================
Generates the daily handicapping PDF from a scored DataFrame.

Branded for Down The Stretch (downthestretch.ai). Successor to the legacy BTSM
sheet — same data, new look:
  - Deep Forest primary, Racing Gold accents, Constantia/Calibri typography
  - Type-only DTS wordmark in the page header
  - Heritage attribution in the footer:
        "From the creators of Be The Smart Money · Est. 2009"

Layout: one race per US Letter page (portrait). Each page has:
  - Header band: track + date (left), DTS wordmark (center),
    domain + Top DTS Best Bets card (right)
  - TOP PICKS strip: per-race minimum contenders to reach cumulative ≥ 0.50,
    capped at 4 horses per race
  - Race header: race # + race type + surface/distance/purse/turns/par speed
  - Wager-types row from BRISnet WagerType1..9
  - Per-horse rows with:
      Line 1: # | name | DTS odds | Prob2Win | Runs | Speed/Jockey/Trainer
              mini-bars | Smart Comment
      Line 2: Jockey/Trainer | ML
      LIKE/FADE panel: two columns, up to 3 reasons each
    Row highlight: sage tint on green_flag (longshot looker: bottom-half
                   win-prob rank AND de-vigged edge vs adj-ML >= 1.75)
    Gold left-bar + bold call-out on best_bet (gold gate: >=1.5 edge + top-half)
  - Footer: heritage attribution + downthestretch.ai + "Intelligence at Full Stride"

Public API:
    generate_pdf(scored_df, out_path, *, track, race_date, label="FINAL",
                 conditions=None, first_post=None, logo_path=None) -> Path

Wired into run_pipeline.generate_pdf() — replaces the stub at ~line 815.

If `logo_path` is provided, a logo image renders in the centered header slot.
If not (the default in Phase 4), a CSS-typeset wordmark renders instead.
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
# Legal footer
# ---------------------------------------------------------------------------
# Rendered at the bottom of EVERY page of EVERY sheet. Sheets get downloaded,
# forwarded and screenshotted away from the site, so the disclaimer and the
# licence notice have to travel with the artifact itself -- a footer on
# downthestretch.ai does nothing once a PDF is detached from the site.
#
# TODO once the LLC is registered: set LEGAL_ENTITY = "Down The Stretch, LLC"
# ---------------------------------------------------------------------------
LEGAL_ENTITY = "Down The Stretch AI"

LEGAL_DISCLAIMER = (
    "Informational model output only \u2014 not wagering advice. No guarantee of "
    "accuracy or results. Verify entries, scratches, odds and post times with the "
    "official source before wagering. 18+ \u00b7 "
    '<span class="nb">1&#8209;800&#8209;GAMBLER</span>.'
)

_LEGAL_RIGHTS_TMPL = (
    "\u00a9 {year} {entity}. Licensed to the purchasing subscriber for personal "
    "use only; redistribution or resale prohibited. "
    "Terms: downthestretch.ai/terms"
)


def _legal_rights_line() -> str:
    """Copyright + licence line for the page footer, stamped with the current year."""
    return _LEGAL_RIGHTS_TMPL.format(year=datetime.now().year, entity=LEGAL_ENTITY)


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
        # Empty string = render nothing. The caller decides what (if anything)
        # to say; a PREVIEW has no change feed to report on.
        scratches_note=scratches_note or "",
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
<html><head><meta charset="utf-8"><title>DTS {track} {pretty_date}</title>
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
# Top 3 DTS Best Value Bets of the Day
# ---------------------------------------------------------------------------

def _select_top3_best_bets(df: pd.DataFrame) -> list[dict]:
    """
    Pick up to 3 horses that qualify as DTS Best (Gold) Bets — the `best_bet`
    gate computed upstream: the adjusted line exceeds DTS fair odds by >=40%
    AND the horse's win-probability rank is in the top half of its field.

    Overlay tiers (output.value_tier(), keyed on ML/DTS-1):
        4 = >=60% overlay   3 = >=40% (gold)   2 = >=25% (green)
        1 = mild/fair       0 = overbet

    Gold is intentionally rare — a race may contribute none. If more than 3
    qualify across the card, take the top 3 by ProbToWin descending.
    Returns dicts with race / program / horse / dts_odds / ml_odds (raw, for
    the (ML X) display).
    """
    candidates = []
    for _, row in df.iterrows():
        btsm  = row.get("dts_odds")
        ml    = row.get("ml_odds")          # raw ML — for DISPLAY only
        ml_cmp = row.get("ml_odds_adj")     # adjusted ML — behind the curtain
        if not _is_num(ml_cmp):
            ml_cmp = ml
        # TOP DTS BETS are exactly the GOLD best bets: >=40% overlay AND the
        # horse's win prob is in the top half of its field. That gate is
        # computed upstream as `best_bet`. Fall back to value_tier>=3 if the
        # flag isn't present (older rows).
        bb = row.get("best_bet")
        if bb is None:
            try:
                bb = int(row.get("value_tier") or 0) >= 3
            except (TypeError, ValueError):
                bb = False
        if not bool(bb):
            continue
        candidates.append({
            "race":    int(row["race"]),
            "program": str(row["program"]),
            "horse":   row["horse"],
            "btsm":    float(btsm) if _is_num(btsm) else 0.0,
            "ml":      float(ml) if _is_num(ml) else float(ml_cmp),  # raw ML shown as (ML X)
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
    cond_str = " &nbsp;·&nbsp; ".join(cond_parts)

    # Masthead post line. Race 1 keeps the authoritative "1st Post" (from the
    # Equibase track-status fetch); every later race shows ITS OWN estimated
    # post time, parsed per-race from the DRF (run_pipeline._post_times_by_race).
    race_post = str(first.get("post_time") or "").strip()
    if race_no == 1 and first_post:
        fp = f"1st Post: <b>{_html_escape(first_post)}</b>"
    elif race_post:
        fp = f"Post: <b>{_html_escape(race_post)}</b>"
    elif first_post:
        fp = f"1st Post: <b>{_html_escape(first_post)}</b>"
    else:
        fp = ""

    # ── Change-feed freshness line ───────────────────────────────────────
    # Sits under 1st Post / conditions. Equibase's own "Last Updated" stamp is
    # in ET, the same clock as first_post, so the two lines agree. Blank on a
    # PREVIEW (no change feed has been read yet) — we render nothing rather
    # than an empty element so the strip doesn't gain dead vertical space.
    sn = (f'<span class="ms-updated">{_html_escape(scratches_note)}</span>'
          if scratches_note else "")

    # ── Banner. A caller-supplied logo_path (logo_uri) still wins and renders
    # as an image. Otherwise we render the typographic DTS masthead: a crisp,
    # print-resolution header — Deep Forest field, Racing Gold Constantia
    # wordmark, and an upward-opening horseshoe + DTS monogram flanking each
    # side. This replaced the raster DTS_banner.png so the header stays sharp
    # at print DPI and matches the downthestretch.ai brand system.
    if logo_uri:
        banner_html = (
            f'<img class="dts-banner" src="{logo_uri}" alt="Down The Stretch AI"/>'
        )
    else:
        _shoe = (
            '<svg class="dts-shoe" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">'
            '<path d="M26.5 8.7 A13 13 0 1 1 13.5 8.7" fill="none" stroke="#C9A84C" '
            'stroke-width="4.5" stroke-linecap="round"/>'
            '<circle cx="31.8" cy="25.5" r="1.2" fill="#0D2B1E"/>'
            '<circle cx="25.5" cy="31.8" r="1.2" fill="#0D2B1E"/>'
            '<circle cx="20" cy="33" r="1.2" fill="#0D2B1E"/>'
            '<circle cx="14.5" cy="31.8" r="1.2" fill="#0D2B1E"/>'
            '<circle cx="8.2" cy="25.5" r="1.2" fill="#0D2B1E"/>'
            '<text x="20" y="24" text-anchor="middle" '
            'font-family="Constantia, Georgia, serif" font-size="8" fill="#C9A84C">DTS</text>'
            '</svg>'
        )
        banner_html = (
            '<div class="dts-masthead">'
            f'{_shoe}'
            '<div class="dts-mh-center">'
            '<div class="dts-mh-word">DOWN THE STRETCH AI</div>'
            '<div class="dts-mh-tagrow">'
            '<span class="dts-mh-rule"></span>'
            '<span class="dts-mh-tag">INTELLIGENCE AT FULL STRIDE</span>'
            '<span class="dts-mh-rule"></span>'
            '</div>'
            '<div class="dts-mh-heritage">The evolution of Be The Smart Money '
            '&#183; Est. 2009</div>'
            '</div>'
            f'{_shoe}'
            '</div>'
        )

    # ── Top 3 Best Bets card (right side of meta strip) ─────────────────
    # Best bets are filtered to only those in races that haven't been run
    # yet relative to THIS page. The bettor doesn't need to see a bet for
    # Race 2 once they're flipping to the Race 7 page.
    remaining_bets = [b for b in top3_selections if b["race"] >= race_no]
    if remaining_bets:
        bet_lines = "".join(
            f'<div class="bet-line"><span class="bet-race">R{b["race"]}</span> '
            f'<span class="bet-prog">#{_html_escape(b["program"])}</span> '
            f'<span class="bet-name">{_html_escape(b["horse"])}'
            f' <span class="bet-ml">(ML {_fmt_odds(b["ml"])})</span></span> '
            f'<span class="bet-odds">{_fmt_bet_odds(b["btsm"])}</span>'
            f'</div>'
            for b in remaining_bets
        )
        top3_html = (
            '<div class="top3-card">'
            '<div class="top3-title">TOP DTS BETS</div>'
            f'{bet_lines}'
            '</div>'
        )
    elif top3_selections:
        # Best bets existed but all have passed
        top3_html = (
            '<div class="top3-card top3-empty">'
            '<div class="top3-title">TOP DTS BETS</div>'
            '<div class="bet-line empty">All best bets have passed</div>'
            '</div>'
        )
    else:
        # No best bets identified anywhere on the card
        top3_html = (
            '<div class="top3-card top3-empty">'
            '<div class="top3-title">TOP DTS BETS</div>'
            '<div class="bet-line empty">No best bets identified on this card</div>'
            '</div>'
        )

    # ── Continuation marker for big-field overflow pages ────────────────
    cont_marker = (
        f' <span class="cont-marker">(continued · pg {cont + 1})</span>'
        if cont > 0 else ""
    )

    return f"""
<section class="page{page_break_cls}">

  <div class="banner-band">
    {banner_html}
  </div>

  <header class="meta-strip">
    <div class="ms-left">
      <span class="ms-track">{_html_escape(track_full_name)}</span>
      <span class="ms-sep">·</span>
      <span class="ms-date">{pretty_date}</span>
    </div>
    <div class="ms-mid">
      <div class="ms-mid-inner">
        <span class="ms-fp">{fp}</span>
        <span class="ms-cond">{cond_str}</span>
        {sn}
      </div>
    </div>
    <div class="ms-right">
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
      <span class="hdts">ODDS</span>
      <span class="hprob"><i>P</i>(Win)</span>
      <span class="hruns">Runs</span>
      <span class="hbars">
        <span class="bar-hdr">Spd</span>
        <span class="bar-hdr">Jky</span>
        <span class="bar-hdr">Trn</span>
      </span>
      <span class="hcomment">Comments</span>
    </div>
    {horse_rows}
  </div>

  <footer class="page-footer">
    <div class="pf-brand">
      <span class="pf-heritage">From the creators of Be The Smart Money (est. 2009) comes</span>
      <span class="pf-url">downthestretch.ai</span>
    </div>
    <div class="pf-legal">{LEGAL_DISCLAIMER}</div>
    <div class="pf-legal pf-rights">{_legal_rights_line()}</div>
  </footer>

</section>
"""


# ---------------------------------------------------------------------------
# Per-horse row
# ---------------------------------------------------------------------------

def _build_horse_row(row: pd.Series) -> str:
    btsm = row.get("dts_odds")
    ml   = row.get("ml_odds")
    smart = row.get("smart_comment", "") or ""

    # GREEN tint = the "longshot looker" gate (green_flag): win-prob rank in the
    # BOTTOM half of the field AND de-vigged edge vs the scratch-adjusted ML
    # >= 1.75. Computed upstream (run_pipeline.green_flag) so green stays rare
    # and visually cedes focus to the gold best bets. Falls back to the legacy
    # value_tier>=2 tint only if the flag is absent from the frame.
    val_class = ""
    gf = row.get("green_flag")
    if gf is None:
        try:
            gf = int(row.get("value_tier") or 0) >= 2
        except (TypeError, ValueError):
            gf = False
    if bool(gf):
        val_class = " value-row"
    # GOLD best-bet = the BestBet gate (>=40% overlay AND win-prob rank in the
    # top half of the field), computed upstream. Intentionally rare — a race
    # may have none. Falls back to value_tier>=3 only if the flag is absent.
    bb = row.get("best_bet")
    if bb is None:
        try:
            bb = int(row.get("value_tier") or 0) >= 3
        except (TypeError, ValueError):
            bb = False
    if bool(bb):
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
            raw = row.get(f"{prefix}_{i}", "")
            # Filter out NaN, None, empty string, and the literal "nan"
            if raw is None:
                continue
            try:
                # pd.isna handles NaN/NaT/None; raises TypeError on lists
                if pd.isna(raw):
                    continue
            except (TypeError, ValueError):
                pass
            text = str(raw).strip()
            if not text or text.lower() == "nan":
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
    jockey     = _html_escape(_format_name(row.get("jockey", "")))
    trainer    = _html_escape(_format_name(row.get("trainer", "")))

    dts_txt = _fmt_odds(btsm)
    ml_txt   = _fmt_odds(ml)
    prob_val = row.get("prob_to_win")
    prob_txt = f"{float(prob_val) * 100:.0f}%" if _is_num(prob_val) else "—"

    return f"""
<div class="horse-block{val_class}">
  <div class="horse-line1">
    <span class="hnum">#{program}</span>
    <span class="hname">{horse_name} <span class="ml-inline">(ML {ml_txt})</span></span>
    <span class="hdts"><b>{dts_txt}</b></span>
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
    "N":  "Stakes",       # nongraded stakes (Brisnet code N); named race leads the middle slot
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


def _fmt_bet_odds(decimal_odds) -> str:
    """
    Display odds for the TOP DTS BETS card.

    - Even money              -> "Even Money"
    - Whole-number multiples  -> "N:1"          (e.g. 3   -> "3:1")
    - Fractional odds         -> "X:Y"          (e.g. 7/2 -> "7:2")
    - Non-numeric / "—"       -> passthrough
    """
    s = _fmt_odds(decimal_odds)
    if not s or s == "—":
        return s
    s = s.strip()
    if s == "1":
        return "Even Money"
    if "/" in s:
        return s.replace("/", ":")
    return f"{s}:1"


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
    # Graded stakes encode RaceType as G1/G2/G3 — collapse to the 'G' key so
    # the title reads 'Stakes'; the grade token rides in the middle slot after
    # the race name.
    rt_key = ("G" if (len(racetype_raw) >= 2 and racetype_raw[0] == "G"
                      and racetype_raw[1:].isdigit()) else racetype_raw)
    racetype = RACETYPE_MAP.get(rt_key, rt_key)

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
    # Turns suffix applies to BOTH branches below (RC summary and fallback) —
    # previously it lived only in the fallback, so turns vanished whenever the
    # RC summary was present. Compute it once here.
    turns_str = ""
    turns = row.get("turns")
    if _is_num(turns):
        n = int(turns)
        turns_str = f"{n} Turn{'s' if n != 1 else ''}"

    # Prefer the pre-summarized RaceConditions1 text if the pipeline supplied
    # one (concise, abbreviated eligibility). Otherwise fall back to the
    # composed age/class string. Either way, append turns.
    rc_summary = (row.get("race_conditions_summary") or "").strip()
    if rc_summary:
        middle_parts = [_html_escape(rc_summary)]
    else:
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

    # Named stakes lead the middle slot (BOTH branches — previously this only
    # fired in the fallback, but real cards always carry a race_conditions_
    # summary, so the name never showed). For a graded stakes the grade token
    # ('G3') rides right after the name; nongraded stakes show the name alone.
    # race_name / race_grade are computed per race in run_pipeline
    # (_stakes_name / _stakes_grade) from RaceConditions1.
    race_name = (row.get("race_name") or "").strip()
    if race_name:
        lead = [_html_escape(race_name)]
        race_grade = (row.get("race_grade") or "").strip()
        if race_grade:
            lead.append(_html_escape(race_grade))
        middle_parts[0:0] = lead  # prepend so name (+grade) leads

    # Turns last — shown for every race we can resolve the geometry for.
    if turns_str:
        middle_parts.append(turns_str)

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
# Leading price prefix on a wager token: "$1 ", "$.50 ", "50 CENT ", "10 CENT ".
# BRISnet prefixes each wager with its minimum bet, which would otherwise break
# the startswith() classification (e.g. "$.50 PICK 3" never matches "PICK 3").
_WAGER_PRICE_RE = _re.compile(r"^(\$\s*[\d.]+|\d+\s*CENT)\s+", _re.IGNORECASE)


def _extract_nwx(classification: str) -> str:
    """
    Pull the 'non-winners of N times other than' suffix from classification
    text and render it as 'Non Winners 2X', 'Non Winners 1X', etc.
    Returns '' if absent.
    """
    if not classification:
        return ""
    m = _NWX_RE.search(classification)
    if m:
        return f"Non Winners {m.group(1)}X"
    return ""


# Multi-race wager parsing. BRISnet's wager strings vary by TRACK, not just by
# a clean '/'-delimited list of clean tokens, which is why the old startswith()
# parser silently dropped most pools (the 2026-07-25 "no wagers on the sheet"
# bug). Two real formats seen:
#   Saratoga:   'EXACTA ($1); TRIFECTA (.50); DOUBLE ($1) 1 & 2; PICK 3 ($1) (1-3)'
#               'EARLY PICK 5 (.50) (1-5)'   'MANDATORY PAY PICK 5 (.50) (3-7)'
#   Gulfstream: '$1 DAILY DOUBLE / $1 EXACTA / ...'
#               '$1.00 BET 3 (RACES 1-2-3) / $.50 PICK 5 (RACES 1-2-3-4-5)'
# So: split on ';', '/' and newlines; match the pick/double token ANYWHERE in a
# fragment; treat 'BET N' as 'Pick N' (Gulfstream branding); tolerate EARLY /
# LATE / MANDATORY PAY prefixes and inline prices; and derive the race range
# from whichever parenthesised group holds >=2 plausible race numbers.
_PICK_RE       = _re.compile(r"\b(?:PICK|BET|PK)\s*([3-6])\b", _re.IGNORECASE)
_DOUBLE_RE     = _re.compile(r"\bDOUBLE\b", _re.IGNORECASE)
_WAGER_QUAL_RE = _re.compile(
    r"\b(EARLY|LATE|MANDATORY(?:\s+PAY)?)\b", _re.IGNORECASE)
# Named specialty pools we deliberately omit — jackpot/multi-track/turf-only
# gimmicks that clutter the row and don't map to a clean consecutive sequence.
_WAGER_SKIP_RE = _re.compile(
    r"\b(RAINBOW|COAST\s+TO\s+COAST|TROPICAL|GRAND\s+SLAM|SURVIVOR|JACKPOT|"
    r"SUPER\s+HIGH|GOLDEN\s+HOUR)\b", _re.IGNORECASE)
_PAREN_RE      = _re.compile(r"\(([^)]*)\)")
_AMP_RANGE_RE  = _re.compile(r"(\d+)\s*&\s*(\d+)")


def _split_wager_pools(s: str) -> list[str]:
    """
    Split a WagerType string into individual pools on ';', '/' and newlines —
    but ONLY at paren depth 0. Saratoga separates pools with ';', while
    Gulfstream separates with '/' AND uses ';' *inside* parens to list
    non-consecutive races ('(RACES 4; 6; 9)'), so a naive split would shatter
    that range.
    """
    out, buf, depth = [], [], 0
    for ch in s:
        if ch == "(":
            depth += 1; buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1); buf.append(ch)
        elif ch in ";/\n" and depth == 0:
            out.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return [p.strip() for p in out if p.strip()]


def _race_range(frag: str) -> str:
    """
    ' (Races A-B)' spanning the first→last race number in a wager fragment, or
    '' if none. Race numbers live inside parens as '(1-3)', '(RACES 1-2-3-4-5)',
    or bare as 'N & M'. The minimum-bet price also sits in parens ('($1)',
    '(.50)'), so a group only counts as a range when it holds >=2 numbers in
    the plausible 1-14 race band.
    """
    for grp in _PAREN_RE.findall(frag):
        nums = sorted({int(n) for n in _re.findall(r"\d+", grp) if 1 <= int(n) <= 14})
        if len(nums) >= 2:
            return f" (Races {nums[0]}-{nums[-1]})"
    m = _AMP_RANGE_RE.search(frag)
    if m:
        return f" (Races {int(m.group(1))}-{int(m.group(2))})"
    return ""


def _format_multi_race_wagers(wagers) -> str:
    """
    Filter a race's WagerType strings to the standard multi-race pools — Daily
    Double, Pick 3/4/5/6 (incl. Gulfstream 'Bet N') — and render them deduped as
    'Multi-race wagers: Daily Double (Races 1-2) · Pick 3 (Races 1-3) ·
    Early Pick 5 (Races 1-5)'. Returns '—' when the race offers none.
    Discards WPS, exactas, trifectas, superfectas, and named specialty pools.
    """
    if not wagers:
        return "—"

    frags = []
    for raw in wagers:
        if raw:
            frags.extend(_split_wager_pools(str(raw)))

    # Dedup by (qualifier, base) but prefer the entry that carries a race range,
    # so a range-less specialty duplicate never shadows the real sequence.
    order, best = [], {}
    for frag in frags:
        up = frag.upper()
        if _WAGER_SKIP_RE.search(up):
            continue
        pm = _PICK_RE.search(up)
        if pm:
            base = f"Pick {pm.group(1)}"
        elif _DOUBLE_RE.search(up) and "PICK" not in up and "BET" not in up:
            base = "Daily Double"
        else:
            continue

        # Optional Early / Late / Mandatory qualifier — a card can carry both
        # an Early and a Late Pick 5, so keep them distinct.
        qual = ""
        qm = _WAGER_QUAL_RE.search(up)
        if qm:
            qual = qm.group(1).title().replace("Mandatory Pay", "Mandatory") + " "

        label = f"{qual}{base}{_race_range(frag)}"
        key = (qual.strip(), base)
        if key not in best:
            best[key] = label
            order.append(key)
        elif "(Races" in label and "(Races" not in best[key]:
            best[key] = label

    if not order:
        return "—"
    return "Multi-race wagers: " + " · ".join(best[k] for k in order)


def self_label(base: str, raw: str) -> str:
    """
    Preserve race-range parens when present, dropping any trailing note
    (e.g. carryover) after the closing paren.
        'PICK 3 (RACES 1-2-3)'                  -> 'Pick 3 (Races 1-2-3)'
        'PICK 6 (RACES 3-8) - 70% CARRYOVER'    -> 'Pick 6 (Races 3-8)'
    """
    if "(" in raw and ")" in raw:
        rng = raw[raw.find("("):raw.find(")") + 1].title()
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


# Particles that should remain lowercase inside a name, e.g.
# "Mary van Buren" not "Mary Van Buren". Applied only when the
# particle is NOT the first word.
_NAME_PARTICLES = {"de", "del", "la", "van", "von", "der", "den",
                   "di", "da", "du", "le", "el", "al", "bin", "ibn"}

# Prefixes that stay attached to the next word with internal caps,
# e.g. "McGaughey", "O'Brien", "MacDonald", "DiMarco".
_NAME_PREFIXES = ("Mc", "Mac", "O'", "D'", "Di", "Da", "Du", "De", "Le", "La")


def _smart_title(word: str) -> str:
    """Title-case a single name token, preserving Mc/O'/etc."""
    if not word:
        return word
    w = word.strip()
    if not w:
        return w
    lower = w.lower()
    # Particles (only if not the leading word — caller decides)
    if lower in _NAME_PARTICLES:
        return lower
    # Mc prefix: capitalize what follows
    # (Mac is too ambiguous — Machado, Macedo, Macario are common
    #  Latin American names. Mac names like MacDonald are rarer in
    #  thoroughbred racing, and plain "Macdonald" is acceptable.)
    if lower.startswith("mc") and len(lower) > 2:
        return "Mc" + lower[2:].capitalize()
    # O' D' apostrophe prefix
    if "'" in w and len(lower) > 2 and lower[1] == "'":
        return w[0].upper() + "'" + w[2:].capitalize()
    return w.capitalize()


def _format_name(raw: Any) -> str:
    """
    Convert BRISnet 'LASTNAME FIRSTNAME [MIDDLE] [SUFFIX]' to
    'Firstname [M] Lastname [Suffix]'.

    Rules:
      - Empty / None / NaN → empty string
      - Single token       → just title-case it
      - Two tokens         → flip:  "COLEBROOK BEN"     → "Ben Colebrook"
      - Three+ tokens      → first token(s) is last name (with optional
                             leading particles), rest is first+middle:
                               "WARD WESLEY A"            → "Wesley A Ward"
                               "ST GERMAIN J P"           → "J P St Germain"
                               "DE LA TORRE ALEX"         → "Alex de la Torre"
                               "VAN DYKE M"               → "M van Dyke"
                               "ORTIZ IRAD JR"            → "Irad Ortiz Jr"
                               "PIMENTEL J III"           → "J Pimentel III"

    A multi-word surname is detected when the first token is a known
    particle/prefix ('ST', 'DE', 'VAN', etc.). The surname extends through
    any further particles plus the next real word.

    Suffixes (JR, SR, II, III, IV, V) are detected in the token stream
    and pinned to the END of the rendered name.
    """
    if raw is None:
        return ""
    try:
        if pd.isna(raw):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(raw).strip()
    if not s:
        return ""

    # Race-day jockey-change marker: run_pipeline appends a trailing " *" to a
    # swapped-in rider whose name arrives from the Equibase feed ALREADY in
    # display order ("First Last"), NOT BRISnet "LASTNAME FIRSTNAME" order. Do
    # NOT flip it (that rotates the first word to the end and scrambles the
    # name) — just tidy per-token casing and render the "*" in FRONT (a leading
    # marker reads better than a trailing one that hides next to the trainer).
    if s.endswith("*"):
        core = s[:-1].strip()
        if core:
            return "* " + " ".join(_smart_title(t) for t in core.split())
        return s

    parts = s.split()
    if len(parts) == 1:
        return _smart_title(parts[0])

    # Known leading tokens that signal a multi-word surname
    _SURNAME_STARTERS = {"st", "saint", "de", "del", "la", "van", "von",
                         "der", "den", "di", "da", "du", "le", "el",
                         "mc", "mac", "bin", "ibn"}
    # Generational suffixes — render at the end of the name
    _NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

    # Strip a trailing suffix off the raw token list so it doesn't get
    # mistaken for a first/middle name. Capitalize it properly.
    suffix = ""
    if parts[-1].lower() in _NAME_SUFFIXES:
        suf = parts[-1].lower()
        suffix = suf.upper() if suf in {"ii", "iii", "iv", "v"} else suf.capitalize()
        parts = parts[:-1]
        if len(parts) == 1:
            # Edge case: name was just "LASTNAME JR" → "Lastname Jr"
            return f"{_smart_title(parts[0])} {suffix}"

    last_name_tokens = []
    i = 0
    # Eat leading surname-starter tokens (particles/prefixes)
    while i < len(parts) - 1 and parts[i].lower() in _SURNAME_STARTERS:
        last_name_tokens.append(parts[i])
        i += 1
    # Take exactly one more token as the surname-proper (only one,
    # to avoid grabbing first names by accident).
    if i < len(parts):
        last_name_tokens.append(parts[i])
        i += 1

    # If we somehow consumed everything, fall back to plain title case
    if i >= len(parts):
        # Probably a name like "DE LA" with nothing after — unusual.
        out = " ".join(_smart_title(t) for t in last_name_tokens)
        return f"{out} {suffix}".strip() if suffix else out

    rest = parts[i:]

    # Build last name: first token gets _smart_title, particles stay
    # lowercase, the surname-proper (final token) gets _smart_title.
    if len(last_name_tokens) == 1:
        last_name = _smart_title(last_name_tokens[0])
    else:
        first_tok = _smart_title(last_name_tokens[0])
        middle_toks = [
            t.lower() if t.lower() in _NAME_PARTICLES else _smart_title(t)
            for t in last_name_tokens[1:-1]
        ]
        surname_proper = _smart_title(last_name_tokens[-1])
        last_name = " ".join([first_tok] + middle_toks + [surname_proper])

    # First name + middle initials
    first = " ".join(_smart_title(t) for t in rest)
    out = f"{first} {last_name}".strip()
    return f"{out} {suffix}" if suffix else out


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = r"""
/* ─────────────────────────────────────────────────────────────────────────
   Down The Stretch  —  PDF stylesheet
   Palette: Deep Forest #0D2B1E · Racing Gold #C9A84C · Warm Ivory #F5F0E8
            Sage Mist #8BAF8E · Track Earth #3D2B1E
   Typography: Constantia (headers) · Calibri (body) · Consolas (numerics)
   ───────────────────────────────────────────────────────────────────────── */

@page {
  size: Letter portrait;
  margin: 0.3in 0.3in 0.35in 0.3in;
}

body {
  font-family: Calibri, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  font-size: 9pt;
  color: #1A1A1A;
  margin: 0; padding: 0;
}

.page { position: relative; padding-bottom: 34pt; }
.page-break { page-break-after: always; }

/* ── Banner band (full-width DTS marketing banner) ───────────────────── */
.banner-band {
  margin: 0 0 4pt 0;
  text-align: center;
  line-height: 0;          /* prevents stray inline gap below img */
}
.dts-banner {
  display: block;
  width: 100%;
  height: auto;
  /* The banner art is forest-green field with gold artwork.
     A subtle gold hairline below it would compete with the banner's
     own bottom gold line, so we leave it bare. */
}

/* ── DTS typographic masthead (Deep Forest field · Racing Gold) ───────── */
.dts-masthead {
  background: #0D2B1E;
  border-top: 1pt solid #C9A84C;
  border-bottom: 1pt solid #C9A84C;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 20pt;
  padding: 7pt 16pt 6pt 16pt;
  line-height: 1;
}
.dts-shoe { width: 46pt; height: 46pt; flex: 0 0 auto; }
.dts-mh-center { text-align: center; }
.dts-mh-word {
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-size: 21pt;
  letter-spacing: 4pt;
  color: #C9A84C;
  line-height: 1;
}
.dts-mh-tagrow {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8pt;
  margin-top: 5pt;
}
.dts-mh-rule { display: inline-block; height: 0.75pt; width: 46pt; background: #C9A84C; }
.dts-mh-tag {
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-size: 8pt;
  letter-spacing: 2.5pt;
  color: #8BAF8E;
}
.dts-mh-heritage {
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-style: italic;
  font-size: 7.5pt;
  color: #8BAF8E;
  margin-top: 4pt;
}

/* ── Meta strip (below banner: track, conditions, best bets) ─────────── */
.meta-strip {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10pt;
  padding: 3pt 2pt 5pt 2pt;
  border-bottom: 0.75pt solid #C9A84C;
  margin-bottom: 4pt;
}
.ms-left {
  flex: 0 0 auto;
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-size: 14pt;
  color: #0D2B1E;
  white-space: nowrap;
  align-self: center;
}
.ms-track { font-weight: bold; letter-spacing: 0.3pt; }
.ms-sep   { color: #C9A84C; margin: 0 4pt; font-weight: bold; }
.ms-date  { font-style: italic; color: #3D2B1E; }

.ms-mid {
  flex: 1 1 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  align-self: center;   /* align vertically with the track name on the left */
}
.ms-mid-inner {
  display: inline-block;
  text-align: center;   /* horizontally center "1st Post" and "Dirt/Turf" lines */
  font-size: 10.5pt;
  color: #3D2B1E;
  line-height: 1.4;
}
.ms-mid-inner b { color: #0D2B1E; }
.ms-fp   { display: block; white-space: nowrap; }
.ms-cond { display: block; white-space: nowrap; }
/* Change-feed freshness. Deliberately quieter than the two lines above it —
   Sage Mist, smaller, italic: informational provenance, not a headline. */
.ms-updated {
  display: block;
  white-space: nowrap;
  font-size: 8.5pt;
  font-style: italic;
  color: #8BAF8E;
  margin-top: 1pt;
}

.ms-right {
  flex: 0 0 32%;
  text-align: right;
}

/* ── Legacy wordmark (fallback when banner asset missing) ────────────── */
.dts-wordmark {
  display: inline-block;
  text-align: center;
  padding: 0;
  white-space: nowrap;
}
.dts-wordmark-line1 {
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-size: 17pt;
  font-weight: bold;
  color: #0D2B1E;
  letter-spacing: 3pt;
  line-height: 1;
  white-space: nowrap;
}
.dts-wordmark-line1 .amp {
  color: #C9A84C;
  font-style: italic;
  font-weight: normal;
  font-size: 14pt;
  letter-spacing: 1pt;
  padding: 0 4pt;
}
.dts-wordmark-rule {
  height: 0.75pt;
  background: #C9A84C;
  margin: 3pt auto 2pt auto;
  width: 60%;
}
.dts-wordmark-tag {
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-style: italic;
  font-size: 8pt;
  color: #8BAF8E;
  letter-spacing: 0.5pt;
}

/* ── Top 3 DTS Best Bets card (right side of meta strip) ────────────── */
.top3-card {
  background: #F5F0E8;
  border: 0.75pt solid #C9A84C;
  border-left: 2.5pt solid #C9A84C;
  border-radius: 2pt;
  padding: 3pt 5pt 4pt 6pt;
  text-align: left;
  font-size: 7pt;
  line-height: 1.3;
}
.top3-empty { background: #F5F5F2; border-color: #C8C8C0; border-left-color: #C8C8C0; }
.top3-title {
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-weight: bold; color: #0D2B1E; font-size: 7.5pt;
  letter-spacing: 0.6pt; margin-bottom: 2pt; text-align: center;
}
.bet-line { display: flex; gap: 3pt; align-items: baseline; }
.bet-line.empty { color: #888; font-style: italic; justify-content: center; }
.bet-race { font-weight: bold; color: #8A6D1F; min-width: 16pt; flex: 0 0 auto; }
.bet-prog { font-weight: bold; color: #0D2B1E; min-width: 18pt; flex: 0 0 auto; }
.bet-name { flex: 1 1 auto; font-weight: bold; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #1A1A1A; }
.bet-odds { font-size: 6.5pt; color: #3D2B1E; flex: 0 0 auto; }

/* ── Top picks strip ────────────────────────────────────────────────── */
.top-picks-strip {
  background: #F5F0E8;
  border: 0.5pt solid #C9A84C;
  border-left: 2.5pt solid #C9A84C;
  padding: 3pt 8pt;
  border-radius: 2pt;
  font-size: 8.5pt;
  margin-bottom: 4pt;
  line-height: 1.3;
}
.tp-title {
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-weight: bold; color: #0D2B1E; margin-right: 8pt;
  letter-spacing: 0.6pt;
}
.tp-race  { margin-right: 9pt; white-space: nowrap; }
.tp-label { color: #8A6D1F; font-weight: bold; }
.tp-nums  { font-weight: bold; color: #0D2B1E; }

/* ── Race header ────────────────────────────────────────────────────── */
.race-header {
  background: #0D2B1E;
  color: #F5F0E8;
  padding: 4pt 9pt;
  border-radius: 2pt 2pt 0 0;
  border-bottom: 1.5pt solid #C9A84C;
  display: flex;
  align-items: baseline;
  gap: 10pt;
}
.rh-num {
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-size: 12pt; font-weight: bold;
  color: #C9A84C;
  letter-spacing: 0.3pt;
}
.rh-title {
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-size: 11pt; font-style: italic; color: #F5F0E8;
}
.rh-middle { font-size: 9pt; color: #FFFFFF; font-weight: bold; flex: 1; text-align: center; }
.rh-detail {
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-size: 10pt; font-weight: bold; margin-left: auto;
  color: #F5F0E8;
}
.cont-marker {
  font-size: 8.5pt; font-style: italic; font-weight: normal;
  color: #8BAF8E; margin-left: 6pt;
  font-family: Constantia, "Hoefler Text", Georgia, serif;
}

.race-meta {
  background: #F5F0E8;
  padding: 2.5pt 9pt;
  font-size: 8pt;
  color: #3D2B1E;
  border-bottom: 0.5pt solid #C9A84C;
  margin-bottom: 3pt;
}
.wagers { color: #3D2B1E; font-style: italic; }

/* ── Horse row ──────────────────────────────────────────────────────── */
.horses { display: flex; flex-direction: column; gap: 0pt; }

/* Column header row (appears once above each race's horses) */
.horse-col-header {
  display: flex;
  align-items: center;
  gap: 5pt;
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-size: 7pt;
  font-weight: bold;
  color: #8A6D1F;
  text-transform: uppercase;
  letter-spacing: 0.5pt;
  padding: 2pt 6pt;
  border-bottom: 0.75pt solid #C9A84C;
  background: #FAF6EA;
}
.horse-col-header .ml-inline {
  font-weight: normal; color: #888; font-size: 6.5pt;
  text-transform: none; letter-spacing: 0;
  font-family: Calibri, sans-serif;
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
  border-bottom: 0.5pt solid #E8E2D4;
  break-inside: avoid;
}
/* Value tint — sage at low opacity (DTS beats ML) */
.horse-block.value-row {
  background: #EEF2EA;
}
/* Best Bet — Racing Gold left bar + warm ivory background */
.horse-block.best-bet {
  border-left: 3pt solid #C9A84C;
  background: #F5F0E8;
}

.horse-line1 {
  display: flex;
  align-items: center;
  gap: 5pt;
  font-size: 9pt;
  flex-wrap: nowrap;
}
.horse-line1 .hnum {
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-weight: bold; min-width: 20pt; color: #0D2B1E; flex: 0 0 auto;
}
.horse-line1 .hname  {
  font-weight: bold; min-width: 140pt; flex: 1 1 140pt;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  color: #1A1A1A;
}
.horse-line1 .hname .ml-inline {
  font-weight: normal; color: #888; font-size: 7.5pt; margin-left: 3pt;
}
.horse-line1 .hdts,
.horse-line1 .hprob,
.horse-line1 .hruns  {
  font-size: 9pt; flex: 0 0 auto;
  text-align: center;
}
.horse-line1 .hdts  { min-width: 32pt; color: #0D2B1E; }
.horse-line1 .hprob  { min-width: 32pt; color: #0D2B1E; }
.horse-line1 .hruns  { min-width: 32pt; font-size: 8pt; color: #3D2B1E; }
.horse-line1 .hcomment {
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-style: italic; color: #0D2B1E;
  text-align: left; font-size: 8pt;
  line-height: 1.15;
  flex: 0 0 auto;
  width: 128pt; max-width: 128pt;
  padding-right: 3pt;
}
.best-bet .hcomment  { font-weight: bold; color: #8A6D1F; }

.horse-col-header .hnum,
.horse-col-header .hname,
.horse-col-header .hdts,
.horse-col-header .hprob,
.horse-col-header .hruns,
.horse-col-header .hcomment {
  flex: 0 0 auto;
}
.horse-col-header .hnum    { min-width: 20pt; }
.horse-col-header .hname   {
  min-width: 140pt; flex: 1 1 140pt;
  color: #8A6D1F;
}
.horse-col-header .hdts   { min-width: 32pt; text-align: center; }
.horse-col-header .hprob   { min-width: 32pt; text-align: center; }
.horse-col-header .hruns   { min-width: 32pt; text-align: center; }
.horse-col-header .hcomment {
  width: 128pt; max-width: 128pt; text-align: left;
  padding-right: 3pt;
}

.horse-line2 {
  display: flex;
  font-size: 7.5pt;
  color: #3D2B1E;
  padding-left: 28pt;
  gap: 8pt;
  margin-top: 0pt;
  line-height: 1.2;
  font-style: italic;
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
  background: #E8E2D4; border-radius: 1pt; overflow: hidden;
  flex: 0 0 28pt;
  border: 0.25pt solid #D8CFB8;
}
.bar > span { display: block; height: 100%; max-width: 100%; }
/* Three distinct shades pulled from the brand palette */
.bar-speed > span { background: #0D2B1E; }   /* Deep Forest — speed figure  */
.bar-jock  > span { background: #8BAF8E; }   /* Sage Mist  — jockey win pct */
.bar-trn   > span { background: #C9A84C; }   /* Racing Gold — trainer ROI   */

/* ── Like / Fade panel ──────────────────────────────────────────────── */
.why-grid {
  display: flex;
  gap: 14pt;
  margin: 1pt 0 0 28pt;
  font-size: 7.8pt;
}
.why-like, .why-fade {
  list-style: none; padding: 0; margin: 0;
  flex: 1;
}
/* Like — Deep Forest text */
.why-like li { color: #0D2B1E; line-height: 1.2; }
/* Fade — Track Earth (warm brown), readable on warm backgrounds */
.why-fade li { color: #6B3410; line-height: 1.2; }
.why-like li.empty,
.why-fade li.empty { color: #B0A89A; }

/* ── Page footer ─────────────────────────────────────────────────────── */
.page-footer {
  position: absolute;
  left: 0; right: 0; bottom: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.8pt;
  padding-top: 3pt;
  border-top: 0.5pt solid #C9A84C;
}
.pf-brand {
  display: flex;
  justify-content: center;
  align-items: baseline;
  gap: 6pt;
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-size: 7.5pt;
  font-style: italic;
  color: #8BAF8E;
  letter-spacing: 0.3pt;
}
/* Legal line -- must appear on every page; sheets travel off-site. */
.pf-legal {
  font-family: Calibri, "Segoe UI", Arial, sans-serif;
  font-size: 5pt;
  font-style: normal;
  font-weight: normal;
  line-height: 1.2;
  letter-spacing: 0;
  color: #6F6F6F;
  text-align: center;
  max-width: 7.5in;
}
.pf-rights { color: #8A8A8A; }
/* keep the helpline number from ever breaking across lines */
.nb { white-space: nowrap; }
.pf-heritage { color: #3D2B1E; font-style: normal; }
.pf-sep      { color: #C9A84C; font-style: normal; }
.pf-est      { color: #8A6D1F; font-style: italic; font-weight: bold; }
.pf-url      { color: #0D2B1E; font-style: normal; font-weight: bold; }

/* ─────────────────────────────────────────────────────────────────────────
   Layout iteration overrides (May 2026)
   ─────────────────────────────────────────────────────────────────────────
   These rules sit at the end of the cascade so they take precedence over
   the base layout above. Changes made during the production iteration:
     - Narrower hname / hodds / hprob / hruns columns
     - Wider, taller SPD/JKY/TRN bars (36pt × 7pt each, 120pt container)
     - Bars vertically aligned to text middle (not inline-block baseline)
     - Bars use space-around so they sit under their column header labels
     - Comments column floats absolutely on the right of each horse-block
       so it can wrap downward without affecting the in-flow row height
     - Header row mirrors the data row's absolute Comments placement so
       ODDS / P(Win) / RUNS / SPD / JKY / TRN headers stay over their data
     - italic P(Win) inside the column header
     - Best-bet card styling for the .bet-ml + .bet-odds spans
   ───────────────────────────────────────────────────────────────────────── */

/* Bars: 36pt × 7pt each, 120pt container, vertically centered on text line */
.horse-line1 .hbars {
  width: 120pt; min-width: 120pt; flex: 0 0 120pt;
  gap: 2pt; vertical-align: middle; justify-content: space-around;
}
.horse-line1 .bar, .horse-line1 .bar > span { vertical-align: middle; }
.bar { width: 36pt; height: 7pt; flex: 0 0 36pt; }
.horse-col-header .hbars {
  width: 120pt; min-width: 120pt; flex: 0 0 120pt; gap: 2pt;
}
.horse-col-header .bar-hdr { width: 36pt; }

/* Tighter column gap, narrower name + numeric cells */
.horse-line1, .horse-col-header { gap: 4pt; }
.horse-line1 .hname,
.horse-col-header .hname { min-width: 102pt; flex: 1 1 102pt; }
.horse-line1 .hdts  { min-width: 32pt; text-align: center; }
.horse-col-header .hdts  { min-width: 32pt; text-align: center; }
.horse-line1 .hprob { min-width: 36pt; text-align: center; }
.horse-col-header .hprob { min-width: 36pt; text-align: center; }
.horse-line1 .hruns,
.horse-col-header .hruns { min-width: 28pt; }

/* Comments column: floats absolute on the right of each horse-block so
   long comments wrap downward without pushing the row taller.
   Header row mirrors the same layout so column headers stay aligned. */
.horse-block       { position: relative; padding-right: 170pt; }
.horse-col-header  { position: relative; padding-right: 170pt; }
.horse-line1 .hcomment {
  position: absolute; top: 3pt; right: 8pt;
  width: 146pt; max-width: 146pt; flex: 0 0 auto;
  line-height: 1.3;
}
.horse-col-header .hcomment {
  position: absolute; top: 2pt; right: 8pt;
  width: 146pt; max-width: 146pt; flex: 0 0 auto;
}

/* Italic P inside the P(Win) header so it reads as a probability function */
.horse-col-header .hprob i {
  font-style: italic;
  font-family: Constantia, "Hoefler Text", Georgia, serif;
  font-weight: bold;
  text-transform: none;
  letter-spacing: 0;
}

/* TOP DTS BETS — (ML X) inline with horse name, bold ODDS on the right */
.bet-ml   { font-weight: normal; color: #8A6D1F; font-size: 6.5pt; margin-left: 3pt; }
.bet-odds { font-weight: bold;   color: #0D2B1E; }

/* Tighter why-grid: cap width so ✗ fades sit close to ✓ likes, not spread
   across the full row width. */
.why-grid { max-width: 300pt; }
.why-like li, .why-fade li { line-height: 1.15; margin: 0; padding: 0; }
"""

# (end of pdf.py)

