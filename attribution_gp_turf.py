"""
DTS Pipeline -- attribution_gp_turf.py
======================================
Data + loader for the GP.Turf.NonMaiden attribution twin.

Parity rule (memory: dts-scoring-attribution-parity): score.py's GP turf branch
(score_gp_turf) needs a matching attribution path, or every GP turf horse ships
with "No standout attributes either way" and nothing errors. This module holds
ONLY the GP feature->theme groups, the phrase pools and the coefficient loader.
The blend/rank logic lives in attribution.py (_compute_attributions_gp_turf ->
_rank_contributions) so ranking, theme-dedupe and the 20% threshold stay
single-sourced.

ISOLATION: these pools are consumed ONLY on the GP turf path. attribution.py
layers them over the global pools into scoped maps (_GP_SYN / _GP_GRP) and never
merges them back, so every other track's comments are byte-for-byte unchanged
even where GP reuses a shared variable name.

Sign convention (same as every other pool): the "like" phrases are shown when
coef * value is ABOVE the race average, the "fade" phrases when it is below. For
variables with a NEGATIVE coefficient the like phrase therefore describes the
LOW value (fewer lifetime starts, nearer the front early, dropping in price).

Keys are FRAME columns, i.e. after gp_turf_vars.GP_TURF_VAR_ALIASES is applied
(ipurse16_gpt26, finpos1_gpt26, effpost_gpt26). baseprob2 / Intercept are excluded upstream.
"""

# ---------------------------------------------------------------------------
# feature -> theme group (dedupe: strongest signal per group per horse)
# ---------------------------------------------------------------------------
GP_TURF_FEATURE_GROUPS = {
    # speed / figures
    "xSpdBest3_rn26":     "speed",
    "xSpdLike2_sc26":     "speed",
    "xSpdTurfBest_rc26":  "speed",
    "turfspd_dmrc26":     "speed",
    # pace / early position
    "xLpLike_rn26":       "pace",
    "xFcPct3_rc26":       "pace",
    # class / earnings
    "EPS_SAR25":          "class",
    "epslt_dmrt26":       "class",
    "IEPSpy_gpt26":       "class",
    "iearningscyind":     "class",
    "ipurse16_gpt26":     "class",
    "xEPSfd_gptclm26":    "class",
    "xClmDrop_rc26":      "class",
    # form / record
    "xPY_WPpct_gpt26":    "form",
    "finpos1_gpt26":      "form",
    "ltstarts_dmrt26":    "form",
    "xStCY_gptsn26":      "form",
    # surface
    "xLTturfW_gptrn26":   "surface",
    "xAWwin_gptrn26":     "surface",
    # track
    "iEPS_TT_gptrc26":    "track",
    # trainer
    "xTrnCYwp_gpt26":     "trainer",
    "xTrnCYwpct_kst26":   "trainer",
    "xTrnCYwinp_kst26":   "trainer",
    # jockey
    "IJkyDTstr_gpt26":    "jockey",
    "IJkyCYst_gptnc26":   "jockey",
    "xJKYatDisJkyEPSc":   "jockey",
    # trainer/jockey combo
    "jtWP365_gptnc26":    "connections",
    # post
    "effpost_gpt26":      "post",
}

# ---------------------------------------------------------------------------
# feature -> ([like phrases], [fade phrases])
# Templates: {SUBJ} he/she, {POSS} his/her, {OBJ} him/her.
# ---------------------------------------------------------------------------
GP_TURF_SYNONYMS = {
    # -- speed ----------------------------------------------------------
    "xSpdBest3_rn26": (
        ["Best recent figure tops these", "Fast lately against this field", "Recent numbers stand out"],
        ["Recent figures trail the field", "Hasn't run fast lately", "Last three numbers come up light"],
    ),
    "xSpdLike2_sc26": (
        ["Fast in recent turf sprints", "Turf-sprint figures lead the way", "Has the numbers at this grass trip"],
        ["Turf-sprint figures come up short", "Slower than these in grass dashes", "Numbers at this trip trail the field"],
    ),
    "xSpdTurfBest_rc26": (
        ["Best turf figure fits", "Top grass number in the field", "Has run fast on the turf"],
        ["Turf figures trail these", "Best grass number is light", "Hasn't run fast on the turf"],
    ),
    "turfspd_dmrc26": (
        ["Proven turf speed", "Grass figures hold up here", "Has the turf numbers for this"],
        ["Turf speed is light", "Grass figures trail the field", "Unproven on the turf clock"],
    ),
    # -- pace -----------------------------------------------------------
    "xLpLike_rn26": (
        ["Finishes strongly in turf routes", "Late kick at this kind of trip", "Has been coming home fast on grass"],
        ["Late pace is light in turf routes", "Hasn't finished off these trips", "Lacks a closing punch going long on grass"],
    ),
    "xFcPct3_rc26": (
        ["Gets early position", "Has been forwardly placed", "Puts {OBJ}self in the race early"],
        ["Has been far back early", "Leaves {OBJ}self a lot to do", "Gives away early position"],
    ),
    # -- class / earnings -----------------------------------------------
    "EPS_SAR25": (
        ["Quality earnings profile", "Has earned {POSS} way up", "Bankroll says {SUBJ} can run"],
        ["Modest earnings profile", "Earnings don't back it up", "Thin bankroll for this spot"],
    ),
    "epslt_dmrt26": (
        ["Strong per-start earnings", "Earns at a high clip", "Money-per-start says class"],
        ["Light per-start earnings", "Earns below the field", "Per-start money is thin"],
    ),
    "IEPSpy_gpt26": (
        ["Earned well last year", "Last season's bankroll stands out", "Productive campaign a year ago"],
        ["Light earnings last year", "Last season was a lean one", "Didn't bank much a year ago"],
    ),
    "iearningscyind": (
        ["Earning well this year", "This season's bankroll leads these", "Has made money this campaign"],
        ["Light earnings this year", "This season has been a lean one", "Hasn't banked much this campaign"],
    ),
    "ipurse16_gpt26": (
        ["Has been facing better", "Comes out of richer races", "Recent purses say class"],
        ["Has been facing easier", "Comes out of cheaper races", "Recent purses were soft"],
    ),
    "xEPSfd_gptclm26": (
        ["Solid money-earner on the main track too", "Bankroll holds up off the turf", "Has earned {POSS} keep on dirt as well"],
        ["Dirt earnings are thin", "Hasn't earned much on the main track", "Light bankroll away from the grass"],
    ),
    "xClmDrop_rc26": (
        ["Gets class relief today", "Easier spot than last time", "Drops in for a softer tag"],
        ["Moves up in claiming price", "Tougher tag than last time", "Asked for more today"],
    ),
    # -- form / record --------------------------------------------------
    "xPY_WPpct_gpt26": (
        ["Hit the board often last year", "Reliable top-two finisher a year ago", "Last season's record holds up"],
        ["Rarely hit the top two last year", "Last season's record was light", "Didn't threaten much a year ago"],
    ),
    "finpos1_gpt26": (
        ["Finished well last out", "Good finish position last time", "Ran a strong race last out"],
        ["Finished poorly last out", "Buried at the wire last time", "Last-out finish was flat"],
    ),
    "ltstarts_dmrt26": (
        ["Lightly raced with upside", "Still has room to improve", "Fresh legs against these"],
        ["Well-exposed veteran", "Has had plenty of chances", "A lot of miles on the odometer"],
    ),
    "xStCY_gptsn26": (
        ["Fresh this season", "Lightly raced this year", "Hasn't been over-raced this campaign"],
        ["Busy campaign this year", "Has been to the well often this season", "Heavily raced this year"],
    ),
    # -- surface --------------------------------------------------------
    "xLTturfW_gptrn26": (
        ["Proven turf winner", "Wins {POSS} share on the grass", "Knows how to win on turf"],
        ["Light on turf wins", "Still looking for a turf win", "Grass win rate trails these"],
    ),
    "xAWwin_gptrn26": (
        ["Winner on synthetic too", "All-weather wins add to the resume", "Has won over the all-weather"],
        ["No help from the all-weather record", "Synthetic record is thin", "Hasn't won on the all-weather"],
    ),
    # -- track ----------------------------------------------------------
    "iEPS_TT_gptrc26": (
        ["Earns well over this course", "Gulfstream has been good to {OBJ}", "Bankroll is strong at this track"],
        ["Hasn't earned much here", "Light bankroll at this track", "Gulfstream hasn't been kind"],
    ),
    # -- trainer --------------------------------------------------------
    "xTrnCYwp_gpt26": (
        ["Barn is hitting the board this year", "Trainer's runners are live", "Stable has been in the money"],
        ["Barn has been off the board", "Trainer's year is quiet", "Stable hasn't been hitting"],
    ),
    "xTrnCYwpct_kst26": (
        ["Productive barn this year", "Trainer keeps finishing one-two", "Stable is firing this season"],
        ["Quiet barn this year", "Trainer's runners haven't threatened", "Stable is cold this season"],
    ),
    "xTrnCYwinp_kst26": (
        ["Trainer is winning this year", "High-percentage barn", "Stable wins at a strong clip"],
        ["Trainer's win rate is low", "Low-percentage barn this year", "Stable hasn't been winning"],
    ),
    # -- jockey ---------------------------------------------------------
    "IJkyDTstr_gpt26": (
        ["Rider knows this turf trip", "Pilot is a regular at the grass distance", "Jockey has plenty of experience at this trip"],
        ["Rider is light on mounts at this turf trip", "Pilot rarely rides this grass distance", "Jockey short on experience at the trip"],
    ),
    "IJkyCYst_gptnc26": (
        ["Busy, in-demand rider", "Pilot gets plenty of mounts", "Jockey is riding regularly"],
        ["Rider has been getting few mounts", "Pilot isn't riding much this year", "Low-volume jockey"],
    ),
    "xJKYatDisJkyEPSc": (
        ["Rider earns at this turf trip", "Pilot is money at the distance on grass", "Right rider for this turf trip"],
        ["Rider light at this turf trip", "Pilot thin at the grass distance", "Jockey-turf-distance combo a concern"],
    ),
    # -- post (same phrases as the shared effpost) -----------------------
    "effpost_gpt26": (
        ["Good gate today", "Liked the draw", "Post gives {OBJ} every chance"],
        ["Wide post a concern", "Outside post is a headache", "A lot of ground to make up from here"],
    ),
    # -- trainer/jockey combo ------------------------------------------
    "jtWP365_gptnc26": (
        ["Proven trainer-rider team", "Barn and jockey click together", "Connections have teamed up well"],
        ["Unproven trainer-rider pairing", "Barn and jockey rarely team up", "Little history between these connections"],
    ),
}


def load_gp_turf_attr_betas(coeff_dir):
    """Load the six GP turf coefficient CSVs via score_gp_turf's own loader, so
    attribution parses (and aliases) the betas exactly as scoring does.
    Returns {model_key: {column: beta}} or {} if the files aren't present."""
    try:
        from score_gp_turf import load_gp_turf_betas
        return load_gp_turf_betas(coeff_dir)
    except Exception:
        return {}
