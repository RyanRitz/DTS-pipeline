"""
DTS Pipeline — attribution_dmr_turf.py
======================================
Data + loader for the DMR.Turf.NonMaiden config-F attribution twin.

Parity note (see memory dts-scoring-attribution-parity): score.py's config-F
turf branch (score_dmr_turf) needs a matching attribution path, or every
DMR-turf horse ships with blank why_like/why_fade comments. This module holds
ONLY the config-F feature->theme groups, the synonym phrase pools, and the
coefficient loader. The blend/rank logic lives in attribution.py so the ranking,
theme-dedupe, and 20%-threshold scoring stay single-sourced.

ISOLATION: these groups/synonyms are consumed ONLY by attribution's config-F
path (attribution._compute_attributions_configf). They are deliberately NOT
merged into the global SYNONYMS/FEATURE_GROUPS, so KEE / SAR / DMR-dirt / maiden
comments are byte-for-byte unchanged even though some config-F vars reuse shared
names (EPS_SAR25, xHBL4c, xJkyWCMstd_sarm, ...). This is the #38 isolation rule
enforced by construction.

Coefficients are the 8 coef_dmr_turf_2026*.csv (CSV, one beta row each) — loaded
via score_dmr_turf._load_betas so the coefficient parsing is identical to
scoring. Every var below is one of those files' selected columns; baseprob2 and
Intercept are excluded upstream (attribution.EXCLUDE), so they need no phrase.
"""

# ---------------------------------------------------------------------------
# feature -> theme group (dedupe: keep the strongest signal per group/horse)
# ---------------------------------------------------------------------------
CONFIGF_FEATURE_GROUPS = {
    # speed / figures
    "XBPPR_tc12":            "speed",
    "XBPPR_tc12_3":          "speed",
    "xBRISPd3":              "speed",
    "spdlf_sard26":          "speed",
    "bestspdtrk_dmrt26":     "speed",
    "xbrispy_dmrt":          "speed",
    "histspd_dmrd":          "speed",
    "turfspd_dmrt26":        "speed",
    "turfspd_dmrc26":        "speed",
    # class / earnings
    "EPS_SAR25":             "class",
    "epslt_dmrt26":          "class",
    "ipurse1c":              "class",
    "ipurse3c":              "class",
    "dirtearn_dmrt26":       "class",
    "lrclass_avg3_alt":      "class",
    # surface (turf / all-weather)
    "turfform_dmrt26":       "surface",
    "turfrecwps_dmrt26":     "surface",
    "xBRISSpeedAW_dmrt":     "surface",
    "awwins_dmrt26":         "surface",
    # track record
    "trkwps_dmrt26":         "track",
    "trkrecwp_dmrt26":       "track",
    # distance
    "distspd_dmrc26":        "distance",
    "distrecwp_dmrt26":      "distance",
    # pace
    "latepacec_dmrt26":      "pace",
    "pace4f_dmrt26":         "pace",
    # post
    "pp_raw26":              "post",
    # form / trip / experience
    "xHBL4c":                "form",
    "xHBL3c":                "form",
    "finpos1_dmrc26":        "form",
    "strbtn_dmrt26":         "form",
    "xStretchBtnLngthsonly1_keeod": "form",
    "ltstarts_dmrt26":       "form",
    "xLTrecWPpct_kta13":     "form",
    # trainer
    "trnturfst_dmrt26":      "trainer",
    "trncmwps_c":            "trainer",
    "trn102021":             "trainer",
    "trncystrts26":          "trainer",
    "trnmtstrts26":          "trainer",
    "trnitmturf_dmrt26":     "trainer",
    "trncmwin_dmrc26":       "trainer",
    "xtran_wpct_58c":        "trainer",
    "xtran_wpct_58cc":       "trainer",
    # jockey
    "jcky_d":                "jockey",
    "jwps_sarm":             "jockey",
    "jckcmwps_dmrc26":       "jockey",
    "jckcmwin_dmrn26":       "jockey",
    "xJockeyCurMtWPpctkaaw13": "jockey",
    "xJkyWCMstd_sarm":       "jockey",
    "IJKYatDisJkyonTurfEPS_keeom": "jockey",
    # workouts
    "havebullet_dmrt26":     "works",
    "bullet3_dmrt26":        "works",
    "xWorkoutDate3_kma13":   "works",
    "wotimefrlg_sart":       "works",
}

# ---------------------------------------------------------------------------
# synonym pools — (likes_variants, fades_variants); first item primary, rest
# rotate across the card. {SUBJ}/{POSS}/{OBJ} tokens are filled by horse sex.
# Phrasing is surface-neutral where the var is a cross-surface proxy
# (jcky_d, jwps_sarm) per memory dts-jcky-d-lineage.
# ---------------------------------------------------------------------------
CONFIGF_SYNONYMS = {
    # ── speed / figures ──────────────────────────────────────────────────
    "XBPPR_tc12": (
        ["Prime Power tops the field", "Highest Prime Power here", "Owns the Prime Power edge"],
        ["Prime Power lags the field", "Outgunned on Prime Power", "Prime Power comes up short"],
    ),
    "xBRISPd3": (
        ["Prime Power says {SUBJ}'s best", "Prime Power advantage is real", "Sits on top of the power ratings"],
        ["Prime Power falls short here", "Power ratings favor others", "Behind the field on power"],
    ),
    "spdlf_sard26": (
        ["Best lifetime figure fits", "Career-top number belongs here", "Lifetime best stacks up well"],
        ["Lifetime best is light", "Career figure trails the field", "Top number doesn't measure up"],
    ),
    "bestspdtrk_dmrt26": (
        ["Fastest over this track", "Best figure at this oval", "Track figure leads the field"],
        ["Slow over this track", "Track figure trails here", "Best number at the oval is light"],
    ),
    "xbrispy_dmrt": (
        ["Sharp figure this year", "Current-year speed is there", "Running fast numbers lately"],
        ["Dull figures this year", "Current-year speed is soft", "This year's numbers disappoint"],
    ),
    "turfspd_dmrt26": (
        ["Fast turf figures", "Turf speed is real", "Numbers say {SUBJ}'s a turf horse"],
        ["Soft turf figures", "Turf speed lags the field", "Numbers don't say turf horse"],
    ),
    "turfspd_dmrc26": (
        ["Turf speed fits these claimers", "Best on grass in this claiming spot", "Turf figure leads the claiming field"],
        ["Turf speed light for this level", "Grass figure trails the claimers", "Turf numbers below the level"],
    ),
    # ── class / earnings ─────────────────────────────────────────────────
    "EPS_SAR25": (
        ["Quality earnings profile", "Has earned {POSS} way up", "Bankroll says {SUBJ} can run"],
        ["Modest earnings profile", "Earnings don't back it up", "Thin bankroll for this spot"],
    ),
    "epslt_dmrt26": (
        ["Strong per-start earnings", "Earns at a high clip", "Money-per-start says class"],
        ["Light per-start earnings", "Earns below the field", "Per-start money is thin"],
    ),
    "ipurse1c": (
        ["Steps in off a good purse", "Last purse was class-appropriate", "Comes from a quality race"],
        ["Steps up off a soft purse", "Last purse was cheap", "Class drop-off from last"],
    ),
    "ipurse3c": (
        ["Recent purses show class", "Has been running for good money", "Purse profile fits up here"],
        ["Recent purses are light", "Has faced cheaper company", "Purse profile is a concern"],
    ),
    "dirtearn_dmrt26": (
        ["Dirt bankroll is a bonus", "Backed by solid dirt earnings", "Proven earner on the main track"],
        ["Dirt earnings are thin", "Bankroll built on the dirt only", "Dirt money doesn't help on grass"],
    ),
    "lrclass_avg3_alt": (
        ["Comfortable at this class", "Class fits like a glove", "Has handled this level"],
        ["Class concern today", "May be in over {POSS} head", "Moving up asks a question"],
    ),
    # ── surface (turf / all-weather) ─────────────────────────────────────
    "turfform_dmrt26": (
        ["Genuine turf form", "Runs {POSS} best on grass", "Turf is clearly {POSS} game"],
        ["Turf form is light", "Grass hasn't been {POSS} game", "Turf record is a question"],
    ),
    "turfrecwps_dmrt26": (
        ["Hits the board on turf", "Consistent grass record", "Turf WPS record is strong"],
        ["Misses on turf", "Grass record is spotty", "Turf WPS record is thin"],
    ),
    "xBRISSpeedAW_dmrt": (
        ["All-weather figures carry over", "Synthetic speed is legit", "Proven on the all-weather"],
        ["All-weather figures are light", "Synthetic speed unproven", "All-weather is a new look"],
    ),
    "awwins_dmrt26": (
        ["Wins on the all-weather", "Has the synthetic won before", "All-weather winner"],
        ["No all-weather wins", "Synthetic win still MIA", "Hasn't won on the all-weather"],
    ),
    # ── track record ─────────────────────────────────────────────────────
    "trkwps_dmrt26": (
        ["Hits the board at this track", "Track record is encouraging", "Runs well over this oval"],
        ["Board record here is light", "Track hasn't been kind", "Struggles over this oval"],
    ),
    "trkrecwp_dmrt26": (
        ["Wins at this track", "Has this oval figured out", "Track win record stands out"],
        ["No wins at this track", "Oval has been tough", "Track win record is thin"],
    ),
    # ── distance ─────────────────────────────────────────────────────────
    "distspd_dmrc26": (
        ["Fast at today's trip", "Distance figure leads here", "Best number comes at this trip"],
        ["Slow at today's trip", "Distance figure is light", "Trip figure trails the field"],
    ),
    "distrecwp_dmrt26": (
        ["Wins at today's distance", "Trip is proven", "Has the distance won before"],
        ["No wins at today's trip", "Distance is uncharted", "Trip win still missing"],
    ),
    # ── pace ─────────────────────────────────────────────────────────────
    "latepacec_dmrt26": (
        ["Strong late-pace figure", "Finishes with energy", "Closing kick is real"],
        ["Weak late-pace figure", "Late energy is a question", "Empty late last time"],
    ),
    "pace4f_dmrt26": (
        ["Sharp early-pace figure", "Gets away quick", "Early speed fits the setup"],
        ["Slow early-pace figure", "Lacks early foot", "Away slowly, uphill trip"],
    ),
    # ── post ─────────────────────────────────────────────────────────────
    "pp_raw26": (
        ["Favorable draw", "Post sets {OBJ} up well", "Drew a good gate"],
        ["Tough draw to overcome", "Wide post costs ground", "Gate is a headache today"],
    ),
    # ── form / trip / experience ─────────────────────────────────────────
    "xHBL4c": (
        ["Runs close in recent starts", "Beaten small margins lately", "Stays in touch late"],
        ["Beaten far back lately", "Struggles to stay close", "Loses ground late"],
    ),
    "xHBL3c": (
        ["In the hunt last few out", "Beaten only a bit recently", "Keeps {OBJ} in the picture"],
        ["Well beaten recently", "Drops out of it late", "Recent margins are ugly"],
    ),
    "finpos1_dmrc26": (
        ["Finished well last out", "Good finish position last time", "Ran a strong race last out"],
        ["Finished poorly last out", "Buried at the wire last time", "Last-out finish was flat"],
    ),
    "strbtn_dmrt26": (
        ["Running at the end last time", "Closed into the stretch well", "Was gaining late last out"],
        ["Fell back in the stretch", "Flattened out last time", "Didn't finish off last race"],
    ),
    "xStretchBtnLngthsonly1_keeod": (
        ["Stayed close in the lane", "Held {POSS} ground late", "Finished in the mix"],
        ["Lost the stretch battle", "Faded through the lane", "Gave ground late"],
    ),
    "ltstarts_dmrt26": (
        ["Battle-tested veteran", "Experience counts here", "Has seen it all"],
        ["Lightly raced, unknown", "Thin record leaves questions", "Still learning the job"],
    ),
    "xLTrecWPpct_kta13": (
        ["Strong recent win rate", "Winning at a healthy clip lately", "Recent form is on the board"],
        ["Thin recent win rate", "Not winning often lately", "Recent form is quiet"],
    ),
    # ── trainer ──────────────────────────────────────────────────────────
    "trnturfst_dmrt26": (
        ["Barn with turf volume", "Trainer runs plenty on grass", "Stable knows the turf game"],
        ["Barn light on turf starts", "Trainer rarely runs turf", "Grass is new for this shedrow"],
    ),
    "trncmwps_c": (
        ["Hot barn at the meet", "Trainer clicking right now", "Shedrow is on the board often",
         "Barn is tough to beat lately", "Trainer has the meet going"],
        ["Cold barn at the meet", "Trainer has gone quiet", "Shedrow searching for a winner",
         "Barn is in a cold spell", "Not the meet for this stable"],
    ),
    "trn102021": (
        ["Productive barn this year", "Trainer's year is strong", "Stable cashing at a good clip"],
        ["Quiet barn this year", "Trainer's year is light", "Stable hasn't been productive"],
    ),
    "trncystrts26": (
        ["Active, well-campaigned barn", "Trainer runs a busy string", "Stable stays in action"],
        ["Low-volume barn", "Trainer runs a small string", "Stable is quiet on starts"],
    ),
    "trnmtstrts26": (
        ["Barn active at this meet", "Trainer getting plenty in", "Stable is well-represented"],
        ["Barn light at this meet", "Trainer rarely here", "Stable thin on meet starts"],
    ),
    "trnitmturf_dmrt26": (
        ["Trainer hits the board on turf", "Barn is money on grass", "Reliable turf trainer"],
        ["Trainer light on turf ITM", "Barn misses on grass", "Turf hasn't paid for this barn"],
    ),
    "trncmwin_dmrc26": (
        ["Barn winning at the meet", "Trainer finding the winner's circle", "Stable win rate is up"],
        ["Barn not winning at the meet", "Trainer missing lately", "Stable win rate has dried up"],
    ),
    "xtran_wpct_58c": (
        ["Trainer wins at a good rate", "Barn's win percentage stands out", "Reliable win clip for this trainer"],
        ["Trainer win rate is low", "Barn's percentage is light", "Win clip is a concern"],
    ),
    "xtran_wpct_58cc": (
        ["Solid trainer win percentage", "Barn gets them home often", "Trainer strike rate fits"],
        ["Soft trainer win percentage", "Barn doesn't win often enough", "Strike rate is a question"],
    ),
    # ── jockey ───────────────────────────────────────────────────────────
    "jcky_d": (
        ["Rider is a proven winner", "Pilot is in top form", "Right rider aboard today"],
        ["Lighter booking here", "Rider has been cold lately", "Not this jockey's strongest spot"],
    ),
    "jwps_sarm": (
        ["Rider hits the board at this trip", "Pilot is reliable at the distance", "Right rider for this route"],
        ["Rider light at this trip", "Pilot thin at the distance", "Jockey-distance combo a concern"],
    ),
    "jckcmwps_dmrc26": (
        ["Hot rider at the meet", "Pilot is on the board often", "Jockey clicking right now"],
        ["Cold rider at the meet", "Pilot has gone quiet", "Jockey searching for a winner"],
    ),
    "jckcmwin_dmrn26": (
        ["Rider winning at this meet", "Pilot finding the winner's circle", "Jockey win rate is up"],
        ["Rider not winning here", "Pilot missing at the meet", "Jockey win rate has dried up"],
    ),
    "xJockeyCurMtWPpctkaaw13": (
        ["Rider firing at this meet", "Pilot on a good run", "Jockey's name is all over the board"],
        ["Rider quiet at this meet", "Pilot has gone cold", "Meet hasn't been kind to this jock"],
    ),
    "xJkyWCMstd_sarm": (
        ["Consistent rider at the meet", "Pilot is steady on the board", "Reliable jockey this meet"],
        ["Erratic rider at the meet", "Pilot's meet has been up and down", "Jockey form is hard to trust"],
    ),
    "IJKYatDisJkyonTurfEPS_keeom": (
        ["Rider earns at this turf trip", "Pilot is money at the distance on grass", "Right rider for this turf route"],
        ["Rider light at this turf trip", "Pilot thin at the grass distance", "Jockey-turf-distance combo a concern"],
    ),
    # ── workouts ─────────────────────────────────────────────────────────
    "havebullet_dmrt26": (
        ["Bullet work in the tab", "Clocked the bullet, barn is happy", "Fastest of the morning recently"],
        ["No bullets in the tab", "Works lack a standout", "No bullet to speak of"],
    ),
    "bullet3_dmrt26": (
        ["Sharp recent bullet", "Standout work in the last three", "Tab features a crisp bullet"],
        ["No recent bullet", "Recent works are quiet", "Work tab lacks a standout"],
    ),
    "xWorkoutDate3_kma13": (
        ["Well-timed work pattern", "Trainer spaced the works right", "Work timing is on schedule"],
        ["Work timing a question", "Spacing of works is off", "Last work raises an eyebrow"],
    ),
    "wotimefrlg_sart": (
        ["Sharp work tab", "Works have been crisp", "Tab says {SUBJ}'s ready to fire"],
        ["Unimpressive works", "Works haven't turned heads", "Tab is underwhelming"],
    ),
}


def load_configf_betas(coeff_dir):
    """Load the 8 config-F coefficient CSVs via score_dmr_turf's own loader, so
    attribution parses the betas exactly as scoring does. Returns
    {model_key: {feat: beta}} or {} if the files aren't present."""
    try:
        from score_dmr_turf import _load_betas
        return _load_betas(coeff_dir)
    except Exception:
        return {}
