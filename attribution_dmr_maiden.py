"""
DTS Pipeline — attribution_dmr_maiden.py
========================================
Data + loader for the DMR.Maiden config-F attribution twin.

Parity note (memory dts-scoring-attribution-parity): score.py's config-F MAIDEN
branch (score_dmr_maiden, gated on MAIDEN_CONFIGF) needs a matching attribution
path, or every DMR maiden horse ships with blank why_like/why_fade comments —
the exact regression that once shipped 84 blank comments. This module holds ONLY
the maiden config-F feature->theme groups, the synonym phrase pools, and the
coefficient loader. The blend/rank logic lives in attribution.py
(_compute_attributions_configf_maiden) so ranking, theme-dedupe, and the
20%-threshold scoring stay single-sourced.

ISOLATION: these groups/synonyms are consumed ONLY by attribution's maiden
config-F path. They are deliberately NOT merged into the global
SYNONYMS/FEATURE_GROUPS, so KEE / SAR / DMR-dirt / DMR-turf comments are
byte-for-byte unchanged even though maiden config-F reuses many shared names.

We REUSE the DMR-turf twin's phrase pools for every var the two models share
(spdlf_sard26, XBPPR_tc12, trn102021, jwps_sarm, ...), and add below ONLY the
maiden-unique selected vars (auction/pedigree/layoff/workout-rank/small-field
angles). Coeffs = the 14 coef_maid_*.csv, loaded via score_dmr_maiden._load_betas
so parsing is identical to scoring. baseprob2 / Intercept are excluded upstream.
"""

# Reuse the DMR-turf config-F pools as the base, then layer maiden-only entries.
try:
    from attribution_dmr_turf import (
        CONFIGF_FEATURE_GROUPS as _TURF_GRP,
        CONFIGF_SYNONYMS as _TURF_SYN,
    )
except Exception:                       # turf module absent
    _TURF_GRP, _TURF_SYN = {}, {}


# ---------------------------------------------------------------------------
# maiden-unique feature -> theme group
# ---------------------------------------------------------------------------
_MAIDEN_ONLY_GROUPS = {
    # speed / figures
    "DRF1_SART":            "speed",
    "lastbris_dmrd":        "speed",
    "BestBris0422":         "speed",
    "xBRISPd2a":            "speed",
    "xdrfsp1m_sard":        "speed",
    "xDRFSPR2SAR":          "speed",
    "BRISDist_dmr26alt":    "distance",
    "xBRIS_DsPRn_dc":       "distance",
    # class / earnings
    "lrclass_kma13":        "class",
    "ipurseSAR25":          "class",
    # pace
    "ShowedLateSP_LR":      "pace",
    "latepace_kma13":       "pace",
    # pedigree / breeding / sale
    "xBRISAvePedRatKEE1017": "pedigree",
    "auct_kma13":           "pedigree",
    "IAucPri_keeA25":       "pedigree",
    "xCABred":              "pedigree",
    # form / experience
    "xstrtsFT_kta13":       "form",
    "finbtn_dmrn":          "form",
    "xks_w_winpcta":        "form",
    # layoff / freshness
    "xRaceDate1_25":        "layoff",
    "xdaysoff5":            "layoff",
    # field / draw
    "xhnumsar":             "field",
    "PPt12":                "post",
    # trainer
    "SMW_trn_strs":         "trainer",
    "trnwcm_sart":          "trainer",
    "xHC_MdntoMdnClm_C":    "trainer",
    # jockey
    "iJCK_EPS2025":         "jockey",
    "jcky_keeapraw13":      "jockey",
    # workouts
    "wotimefrlg_keeom":     "works",
    "wobulls_keeot":        "works",
    "iworkout_kta13":       "works",
    "iworkout_dmrm":        "works",
    "xLastWOatTT":          "works",
    "xWorkoutDate3_keeod":  "works",
    "numbulls3":            "works",
}

CONFIGF_MAIDEN_FEATURE_GROUPS = {**_TURF_GRP, **_MAIDEN_ONLY_GROUPS}


# ---------------------------------------------------------------------------
# maiden-unique synonym pools — (likes, fades). {SUBJ}/{POSS}/{OBJ} filled by sex.
# Phrasing stays surface-neutral (maidens span dirt + turf) and debut-aware
# (many maidens are first-time starters, so "figure" language is soft).
# ---------------------------------------------------------------------------
_MAIDEN_ONLY_SYNONYMS = {
    # ── speed / figures ──────────────────────────────────────────────────
    "DRF1_SART": (
        ["Fast last-out figure", "Ran a sharp number last time", "Speed figure leads this field"],
        ["Slow last-out figure", "Last number trails the field", "Figure comes up short here"],
    ),
    "lastbris_dmrd": (
        ["Strong last-race BRIS", "Last figure fits well", "Recent number stacks up"],
        ["Soft last-race BRIS", "Last figure is light", "Recent number trails"],
    ),
    "BestBris0422": (
        ["Best figure tops the field", "Career-top number belongs", "Lifetime best measures up"],
        ["Best figure is light", "Career number trails", "Top figure doesn't fit"],
    ),
    "xBRISPd2a": (
        ["Prime Power says {SUBJ} fits", "Power rating is real", "Sits high on the power figures"],
        ["Prime Power falls short", "Power rating favors others", "Behind on the power figures"],
    ),
    "xdrfsp1m_sard": (
        ["Fastest of these last out", "Best last-race speed in the field", "Out-figured the field last time"],
        ["Out-figured last time", "Trailed the field's speed last out", "Last-race speed was light"],
    ),
    "xDRFSPR2SAR": (
        ["Solid figure two back", "Backs it up two starts ago", "Second-out number holds up"],
        ["Weak figure two back", "Two-back number disappoints", "Form dips two starts ago"],
    ),
    # ── distance ─────────────────────────────────────────────────────────
    "BRISDist_dmr26alt": (
        ["Fast at today's trip", "Best figure comes at this distance", "Distance figure leads here"],
        ["Slow at today's trip", "Distance figure is light", "Trip number trails the field"],
    ),
    "xBRIS_DsPRn_dc": (
        ["Bred to handle the trip", "Pedigree says the distance fits", "Distance breeding is a plus"],
        ["Pedigree questions the trip", "Distance breeding is thin", "May want a different distance"],
    ),
    # ── class / earnings ─────────────────────────────────────────────────
    "lrclass_kma13": (
        ["Comfortable at this class", "Class fits the spot", "Has faced this level"],
        ["Class concern today", "May be in over {POSS} head", "Steps up in class"],
    ),
    "ipurseSAR25": (
        ["Comes from quality races", "Purse profile fits up here", "Has run for good money"],
        ["Comes from cheaper spots", "Purse profile is light", "Class drop-off in the past"],
    ),
    # ── pace ─────────────────────────────────────────────────────────────
    "ShowedLateSP_LR": (
        ["Showed a late kick last time", "Finished with energy last out", "Closed into the wire last race"],
        ["No late kick last time", "Emptied out late last out", "Failed to finish last race"],
    ),
    "latepace_kma13": (
        ["Late-pace figure is there", "Finishes with something left", "Has a closing gear"],
        ["Late-pace figure is flat", "Little left late", "Closing gear is missing"],
    ),
    # ── pedigree / breeding / sale ───────────────────────────────────────
    "xBRISAvePedRatKEE1017": (
        ["Bred to run", "Pedigree rating is a plus", "Page says {SUBJ} can handle it"],
        ["Pedigree rating is light", "Page raises questions", "Breeding is a concern here"],
    ),
    "auct_kma13": (
        ["Brought a strong sale price", "Well-bought at auction", "Sale ring liked {OBJ}"],
        ["Modest sale price", "Cheaply bought at auction", "Sale ring passed {OBJ} over"],
    ),
    "IAucPri_keeA25": (
        ["Pricey, well-regarded prospect", "Auction tag says class", "Market backed {OBJ} at the sale"],
        ["Bargain-tag prospect", "Auction price was light", "Market was cool on {OBJ}"],
    ),
    "xCABred": (
        ["Suits the local program", "Bred for this circuit", "Home-state breeding fits"],
        ["Faces open-company breeding", "Breeding is a shade below", "Out-of-state page is a question"],
    ),
    # ── form / experience ────────────────────────────────────────────────
    "xstrtsFT_kta13": (
        ["Has the experience edge", "Battle-tested on dirt", "Seasoning counts here"],
        ["Light on dirt starts", "Still learning the job", "Thin experience is a question"],
    ),
    "finbtn_dmrn": (
        ["Finished close last out", "Beaten only a bit last time", "Ran to the wire last race"],
        ["Beaten far back last out", "Well-beaten last time", "Lost touch late last race"],
    ),
    "xks_w_winpcta": (
        ["Profiles like a winner", "Key stats point up", "Numbers say {SUBJ} fits"],
        ["Key stats point down", "Profile is a question", "Numbers don't back {OBJ}"],
    ),
    # ── layoff / freshness ───────────────────────────────────────────────
    "xRaceDate1_25": (
        ["Sharp off the recent race", "Comes in fresh and ready", "Good spacing since last start"],
        ["Off a long layoff", "Freshness is a question", "Time since last start is a concern"],
    ),
    "xdaysoff5": (
        ["Well-spaced campaign", "Trainer times the layoffs well", "Freshness is on {POSS} side"],
        ["Layoff pattern is a question", "Spacing has been erratic", "Comes in off an awkward gap"],
    ),
    # ── field / draw ─────────────────────────────────────────────────────
    "xhnumsar": (
        ["Handy in a small field", "Compact field sets up well", "Short field is a plus"],
        ["Small field cuts the value", "Little cover in a short field", "Field size works against {OBJ}"],
    ),
    "PPt12": (
        ["Favorable draw", "Post sets {OBJ} up well", "Drew a good gate"],
        ["Tough draw to overcome", "Wide post costs ground", "Gate is a headache today"],
    ),
    # ── trainer ──────────────────────────────────────────────────────────
    "SMW_trn_strs": (
        ["Barn runs plenty of maidens", "Trainer campaigns a busy string", "Stable stays in action"],
        ["Low-volume barn", "Trainer runs a small string", "Stable is quiet on starts"],
    ),
    "trnwcm_sart": (
        ["Hot barn at the meet", "Trainer clicking right now", "Shedrow is winning lately"],
        ["Cold barn at the meet", "Trainer has gone quiet", "Shedrow searching for a winner"],
    ),
    "xHC_MdntoMdnClm_C": (
        ["Sharp maiden-claiming drop", "Trainer's drop-in-class angle fits", "Right spot for this barn's move"],
        ["Class drop raises a flag", "Barn's move is a question", "Maiden-claim drop looks defensive"],
    ),
    # ── jockey ───────────────────────────────────────────────────────────
    "iJCK_EPS2025": (
        ["Rider earns at a high clip", "Pilot is a proven producer", "Right rider aboard today"],
        ["Rider light on earnings", "Pilot has been quiet", "Not this jockey's strongest spot"],
    ),
    "jcky_keeapraw13": (
        ["Live rider booking", "Pilot is in good form", "Strong jockey engagement"],
        ["Lighter rider booking", "Pilot has been cold", "Jockey choice is a question"],
    ),
    # ── workouts ─────────────────────────────────────────────────────────
    "wotimefrlg_keeom": (
        ["Sharp work tab", "Works have been crisp", "Tab says {SUBJ}'s ready"],
        ["Unimpressive works", "Works haven't turned heads", "Tab is underwhelming"],
    ),
    "wobulls_keeot": (
        ["Bullets in the work tab", "Fast works catch the eye", "Clocking with the best of the morning"],
        ["No bullets in the tab", "Works lack a standout", "Morning drills are ordinary"],
    ),
    "iworkout_kta13": (
        ["Works rank near the top", "Drilling with the sharpest", "Work-rank stands out"],
        ["Works rank near the bottom", "Drills are slow of the group", "Work-rank is a concern"],
    ),
    "iworkout_dmrm": (
        ["Strong recent work pattern", "Drills point to readiness", "Work profile is encouraging"],
        ["Soft recent work pattern", "Drills leave doubt", "Work profile is a question"],
    ),
    "xLastWOatTT": (
        ["Breezed at the track lately", "Recent work over this surface", "Worked here, knows the ground"],
        ["No recent work over the track", "Missing a local breeze", "Hasn't drilled over this surface"],
    ),
    "xWorkoutDate3_keeod": (
        ["Well-timed work pattern", "Trainer spaced the works right", "Work timing is on schedule"],
        ["Work timing a question", "Spacing of works is off", "Last work raises an eyebrow"],
    ),
    "numbulls3": (
        ["Multiple bullets recently", "Stacking fast works", "Bullet drills in the last three"],
        ["No recent bullets", "Recent works are quiet", "Work tab lacks a standout"],
    ),
}

CONFIGF_MAIDEN_SYNONYMS = {**_TURF_SYN, **_MAIDEN_ONLY_SYNONYMS}


def load_maiden_configf_betas(coeff_dir):
    """Load the 14 blended maiden coef CSVs via score_dmr_maiden's own loader, so
    attribution parses betas exactly as scoring does. Returns
    {model_key: {feat: beta}} or {} if the files aren't present."""
    try:
        from score_dmr_maiden import _load_betas
        return _load_betas(coeff_dir)
    except Exception:
        return {}
