"""
Phase-2 (bars) helpers for race-day jockey changes.

Display-only. Rebuilds the JKY bar for a horse whose rider changed on race day,
using the new rider's CURRENT-MEET win count borrowed from another of their
mounts on the same card, then re-indexes the whole race against the post-swap
field average (matching how scoring builds xJockeyWinsCurrentMeet = raw - race
mean, then jckcm2_sarm = (2.5 + clip(x/std, -1.5, 2))**2, then a fixed 0-100
scale). Never re-runs scoring, so model score / DTS odds are untouched.
"""
from __future__ import annotations
import re
import unicodedata
import difflib
import numpy as np
import pandas as pd

# Fixed-scale bounds for the JKY bar (must match run_pipeline).
_JCKCM2_MIN, _JCKCM2_MAX = 1.0, 20.25
# Neutral "no signal" midpoint: jckcm2_sarm at x=0 -> (2.5)**2 = 6.25.
_NO_SIGNAL_JCK = 6.25

_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}


def _norm_tokens(name: str) -> list[str]:
    """Accent-strip, upper-case, drop punctuation, split into tokens."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"[.,'`-]", " ", s).upper()
    return [t for t in s.split() if t]


def _split_suffix(toks: list[str]) -> tuple[list[str], str]:
    if len(toks) > 2 and toks[-1] in _SUFFIXES:
        return toks[:-1], toks[-1]
    return toks, ""


def key_from_drf(name: str):
    """DRF format: 'LAST FIRST [MID] [SUFFIX]' -> (last, first_initial, suffix)."""
    toks, suf = _split_suffix(_norm_tokens(name))
    if not toks:
        return None
    last = toks[0]
    first_init = toks[1][0] if len(toks) > 1 else ""
    return (last, first_init, suf)


def key_from_rss(name: str):
    """RSS feed format: 'First [Mid] Last[, Suffix]' -> (last, first_initial, suffix)."""
    toks, suf = _split_suffix(_norm_tokens(name))
    if not toks:
        return None
    last = toks[-1]
    first_init = toks[0][0] if toks else ""
    return (last, first_init, suf)


def resolve_new_rider_raw(
    rss_name: str,
    card_df: pd.DataFrame,
    *,
    raw_col: str = "JockeyWinsCurrentMeet",
    jk_col: str = "TodaysJockey",
    thresh: float = 0.88,
):
    """
    Match the RSS new-rider name to a jockey riding elsewhere on the card and
    return (raw_meet_wins, matched_drf_name). Returns (None, None) when the
    match is missing, ambiguous (>=2 distinct riders), or the raw value is NaN.
    """
    tgt = key_from_rss(rss_name)
    if tgt is None:
        return None, None
    tgt_last, tgt_init, tgt_suf = tgt

    matches = []  # (drf_name, raw, ratio)
    for name, sub in card_df.groupby(jk_col):
        key = key_from_drf(name)
        if key is None:
            continue
        last, init, suf = key
        if init and tgt_init and init != tgt_init:
            continue
        if tgt_suf and suf and tgt_suf != suf:
            continue
        ratio = 1.0 if last == tgt_last else difflib.SequenceMatcher(None, last, tgt_last).ratio()
        if last == tgt_last or ratio >= thresh:
            raws = pd.to_numeric(sub[raw_col], errors="coerce").dropna().unique()
            raw = float(raws[0]) if len(raws) else float("nan")
            matches.append((str(name), raw, ratio))

    distinct = {m[0] for m in matches}
    if len(distinct) != 1:
        return None, None
    name, raw, _ = max(matches, key=lambda m: m[2])
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None, None
    return raw, name


def _fixed_scale_jck(jck: pd.Series) -> pd.Series:
    pct = ((jck - _JCKCM2_MIN) / (_JCKCM2_MAX - _JCKCM2_MIN) * 100).clip(lower=0, upper=100).round()
    return pct.fillna(0)


_NO_SIGNAL_BAR = float(
    round((_NO_SIGNAL_JCK - _JCKCM2_MIN) / (_JCKCM2_MAX - _JCKCM2_MIN) * 100)
)  # == 27, the neutral "no signal" bar the pipeline shows for missing jockey data


def reindex_race_bars(
    race_df: pd.DataFrame,
    subs: dict,
    *,
    blanks=None,
    raw_col: str = "JockeyWinsCurrentMeet",
    std_col: str = "xjwins_std",
    prog_col: str = "program",
) -> pd.Series:
    """
    Recompute the JKY bar (0-100) for every horse in one race.

    subs   : {program -> new raw meet-wins (float)} for CONFIDENT changes. Each
             replaces that horse's raw meet-wins, so it enters the race average
             and the whole race re-indexes against the post-swap field.
    blanks : iterable of programs whose rider changed but could NOT be resolved.
             Those horses display the neutral midpoint (27); the race average is
             left UNDISTURBED (their originally-carded raw still contributes), so
             the rest of the race does not move on the basis of an unknown rider.
    """
    blanks = {str(p) for p in (blanks or [])}
    raw = pd.to_numeric(race_df[raw_col], errors="coerce").copy()
    prog = race_df[prog_col].astype(str)
    for p, new_raw in subs.items():
        if new_raw is None:
            continue
        raw.loc[prog == str(p)] = float(new_raw)

    new_avg = raw.mean(skipna=True)  # blanks keep old raw here -> average undisturbed
    std = pd.to_numeric(race_df[std_col], errors="coerce")
    x = raw - new_avg
    z = np.where(std.isna() | (std == 0) | raw.isna(), 0.0, np.clip(x / std, -1.5, 2.0))
    bar = _fixed_scale_jck(pd.Series((2.5 + z) ** 2, index=race_df.index))
    if blanks:
        bar = bar.mask(prog.isin(blanks), _NO_SIGNAL_BAR)
    return bar
