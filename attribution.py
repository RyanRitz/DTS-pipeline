"""
DTS Pipeline — attribution.py
================================
Computes "why" reasons for each horse's odds using feature attribution,
with synonym rotation so the same idea doesn't read identically all day.

Method:
  1. coefficient × feature_value per feature
  2. Subtract race average → relative contribution
  3. Top positive deltas = reasons to LIKE, top negative = reasons to FADE
  4. Synonym pools rotate per race card so phrases don't repeat verbatim
"""

import numpy as np
import pandas as pd
import pyreadstat
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Theme groups for deduplication (keep best signal per group per horse)
# ---------------------------------------------------------------------------
FEATURE_GROUPS = {
    "BestBris0422":           "speed",
    "LRbris_25":              "speed",
    "xBRISPd2":               "speed",
    "xBRISPd6":               "speed",
    "XBPPR_tc12_3":           "speed",
    "BBtrck_kaaw13":          "speed",
    "BrisRelatedKEEOct17D":   "speed",
    "BrisRelated_dmrd":       "speed",
    "BrisRelated_sarm":       "speed",
    "drf1_sart":              "speed",
    "histspd_dmrd":           "speed",
    "xBRISSpeedAWc_keeod":    "speed",
    "xdrfsp1m_sard":          "speed",
    "xDRF2_Ko25":             "speed",
    "EarlySpeed":             "pace",
    "xBRISRunstyle_EP":       "pace",
    "IEPSLTFDKEE1017":        "class",
    "lrclass_kma13":          "class",
    "eps4dalt_sard":          "class",
    "EPS3_SARD15":            "class",
    "ieps_LTCYR26":           "class",
    "xks_w_winpcta":          "class",
    "KS_itmm_26":             "class",
    "xEarnLTDist":            "class",
    "WinsatDist26":           "class",
    "trnwcm_sart":            "trainer",
    "XTrnPYROI":              "trainer",
    "TRNJCKCM_kaaw13":        "connections",
    "trncurWPSKo25":          "trainer",
    "HC_1stongrass":          "trainer",
    "HC_ShipperToUS":         "trainer",
    "jcky_d":                 "jockey",
    "IJKYe_Ko25":             "jockey",
    "IJKYe_kta13":            "jockey",
    "JCK_PY_WPS":             "jockey",
    "JKY_CM_WINSAPR25":       "jockey",
    "xJCK_CMWPS26":           "jockey",
    "iJCK_StrtCM":            "jockey",
    "jckcm2_sarm":            "jockey",
    "jntWP365Ko25":           "connections",
    "xR308_KEE25":            "connections",
    "xR309c":                 "connections",
    "wotimefrlg_sart":        "works",
    "wotimefrlg_keeom":       "works",
    "BullLast3WO":            "works",
    "numbulls3":              "works",
    "LastWOatTT":             "works",
    "xwrkdate_kaaw13":        "works",
    "xwrkdateind":            "works",
    "iworkoutpctrnk1_ckta13": "works",
    "woalone_dmrm":           "works",
    "xdaysoff26":             "form",
    "xNumDaysSinceLRcut":     "form",
    "xNumDaysSinceLRcut2":    "form",
    "StretchBL_LR26":         "form",
    "ShowedLateSP_LR":        "form",
    "ltstr_sart":             "form",
    "xBRIS_DsPRn_dc":         "distance",
    "xturffy_last5":          "surface",
    "brisAW_c":               "surface",
    "PPt12":                  "post",
    "xPostPosition":          "post",
    "xNumEntLast5cut":        "field",
    "xNumEntLast5":           "field",
    "IAucPri_keeA25":         "breeding",
    "xAP_KEE25":              "breeding",
    "xcMonths_old":           "age",
    "xsexcolt0425":           "age",
    "Weight_LR":              "form",
}

# ---------------------------------------------------------------------------
# Intangibles bar (sheet redesign 2026-09)
# ---------------------------------------------------------------------------
# The sheet draws Speed / Jockey / Trainer as bars. Intangibles is everything
# ELSE the model weighs (class, works, form, breeding, distance, pace, post,
# age, field, ...), summed from the per-horse contributions, relative to the
# field, mapped to 0-100 with 50 = field average. Vetted on IND 2026-09-28:
# ~52% of model weight, corr 0.33 with the Spd/Jky/Trn part (new information).
INTANGIBLE_EXCLUDE = {"speed", "jockey", "trainer", "connections"}


class _RaceAttributions(dict):
    """{row_idx: (likes, fades)} plus .intangibles = {row_idx: raw contribution}."""
    intangibles = None


# ---------------------------------------------------------------------------
# Synonym pools — (likes_variants, fades_variants)
# First item in each list is the primary; rest rotate in when repeating
# ---------------------------------------------------------------------------
SYNONYMS = {
    # Speed / figures
    "BestBris0422": (
        ["Elite lifetime figures", "Career-best form is right here", "Tops the field on figures"],
        ["Modest career figures", "Outclassed on the numbers", "Figures don't add up today"],
    ),
    "LRbris_25": (
        ["Sharp last race number", "Last out effort was no fluke", "Came to run last time"],
        ["Dull last race number", "Last effort left something to be desired", "Figures trending wrong way"],
    ),
    "xBRISPd2": (
        ["Simply the fastest horse", "Speed advantage is real", "Brings the most foot"],
        ["Outgunned on pure speed", "Lacks the kick to keep up", "Gets left flat-footed early"],
    ),
    "xBRISPd6": (
        ["Speed to burn vs this field", "Figures say {SUBJ}'s faster", "Raw speed advantage here"],
        ["Lacks the foot here", "Doesn't have the gears", "Speed edge goes the other way"],
    ),
    "XBPPR_tc12_3": (
        ["Fastest at this oval, period", "Owns this track on numbers", "The speed figure leader here"],
        ["Can't match the speedsters", "Gets outfigured at this oval", "Speed profile doesn't fit"],
    ),
    "BBtrck_kaaw13": (
        ["Best figure suits this track", "Track record says {SUBJ} can fire", "History here is encouraging"],
        ["Lacks speed for this track", "Figures don't translate here", "Track has been unkind"],
    ),
    "BrisRelatedKEEOct17D": (
        ["Proven at Keeneland", "Has the receipts at this oval", "Keeneland form is legit"],
        ["Untested at Keeneland", "First look at this track", "Unproven at this venue"],
    ),
    "BrisRelated_dmrd": (
        ["Strong speed figures", "Numbers are there", "Figures stack up well"],
        ["Soft speed figures", "Numbers a concern", "Figures don't inspire"],
    ),
    "BrisRelated_sarm": (
        ["Sharp speed numbers", "Speed tab says {SUBJ}'s live", "Figures point {POSS} way"],
        ["Thin speed numbers", "Speed tab is light", "Figures are a question mark"],
    ),
    "drf1_sart": (
        ["Quick last-out figure", "Good number last time out", "Last race figure is solid"],
        ["Slow last-out figure", "Last figure was ordinary", "Last-out number doesn't cut it"],
    ),
    "histspd_dmrd": (
        ["Consistent speed history", "Has run big figures before", "Back figures are there"],
        ["Inconsistent speed history", "Figure history is erratic", "Can't pin down {POSS} best"],
    ),
    "xBRISSpeedAWc_keeod": (
        ["Proven all-weather form", "Has figured it out on synthetic", "Likes the all-weather surface"],
        ["Limited all-weather form", "All-weather is a new look", "Synthetic surface unproven"],
    ),
    "xdrfsp1m_sard": (
        ["Fast turf figures", "Turf numbers are sharp", "Figures say {SUBJ}'s a turf horse"],
        ["Slow turf figures", "Turf numbers lag the field", "Figures don't say turf horse"],
    ),
    "xDRF2_Ko25": (
        ["Back class shows up", "Second-last was a good effort", "Shows {SUBJ} can run"],
        ["Declining recent figures", "Numbers going in wrong direction", "Form is trending down"],
    ),
    # Pace / style
    "EarlySpeed": (
        ["Tactical speed", "Gets the jump out of the gate", "Rates off the pace nicely"],
        ["No early position", "Gets away slowly, uphill battle", "Will need some luck from the back"],
    ),
    "xBRISRunstyle_EP": (
        ["Presser style fits the pace", "Running style made for this setup", "Will be in the right spot"],
        ["Running style fights pace", "Style is a mismatch today", "Faces an uncomfortable trip"],
    ),
    # Class / earnings
    "IEPSLTFDKEE1017": (
        ["Stakes-caliber earner", "Has earned at this level", "Class is not a question"],
        ["Light stakes earnings", "Hasn't earned at stakes level", "Class is a real question"],
    ),
    "lrclass_kma13": (
        ["Comfortable at this level", "Class fits like a glove", "Has handled similar today"],
        ["Class concern today", "May be in over {POSS} head", "Moving up asks a question"],
    ),
    "eps4dalt_sard": (
        ["Quality earnings profile", "Has earned {POSS} way here", "Bankroll says {SUBJ} can run"],
        ["Modest earnings profile", "Earnings don't back it up", "Thin earnings for this level"],
    ),
    "EPS3_SARD15": (
        ["Proven earner vs field", "Outearns most in here", "The money says {SUBJ}'s legit"],
        ["Earns below field average", "Field outearns {OBJ}", "Earnings lag the competition"],
    ),
    "ieps_LTCYR26": (
        ["Productive year to date", "Has been cashing checks this year", "Good season so far"],
        ["Quiet year to date", "Hasn't found the winner's circle this year", "Year has been a disappointment"],
    ),
    "xks_w_winpcta": (
        ["Strong win percentage", "Wins at a healthy clip", "Know how to get to the wire first"],
        ["Thin win percentage", "Doesn't win often enough", "Hasn't figured out how to win"],
    ),
    "KS_itmm_26": (
        ["Consistent ITM record", "Hits the board consistently", "In-the-money horse"],
        ["Misses the board often", "Doesn't hit the board enough", "Tough to find a check here"],
    ),
    "xEarnLTDist": (
        ["Earns well at this distance", "Distance has been the key", "Loves this trip"],
        ["Hasn't earned at this trip", "Distance earnings are thin", "Trip may not suit"],
    ),
    "WinsatDist26": (
        ["Wins at today's distance", "Has the distance won before", "Trip is proven"],
        ["No wins at today's trip", "Distance is uncharted", "Distance win is still MIA"],
    ),
    # Trainer
    "trnwcm_sart": (
        ["Hot trainer at the meet", "Barn is on fire right now", "Don't fight this shedrow",
         "Trainer is tough to beat lately", "Stable has the meet going"],
        ["Cold trainer at the meet", "Barn has gone quiet", "Shedrow hasn't found the winner's circle",
         "Trainer is in a cold spell", "Not the meet for this barn"],
    ),
    "XTrnPYROI": (
        ["Profitable barn to follow", "Bet this trainer and you'll be rewarded", "History says trust this barn"],
        ["Lean ROI barn historically", "Betting this barn historically hurts", "Return on investment has been poor",
         "Wallet's been thinner following this barn", "History says: tread carefully"],
    ),
    "trncurWPSKo25": (
        ["Trainer in top form", "Barn is clicking right now", "Trainer hitting at a high rate"],
        ["Trainer running cold", "Barn has gone cold", "Trainer not getting them there lately",
         "Shedrow is in a funk", "Trainer win rate has dried up"],
    ),
    "HC_1stongrass": (
        ["Trainer excels on turf debut", "Barn knows how to debut on grass", "First-time turf? Trust this trainer"],
        ["First-time turf, risky", "Turf debut is always a question", "Going to school on the grass today"],
    ),
    "HC_ShipperToUS": (
        ["Respected shipper connections", "Barn makes travel work", "Shippers from this barn arrive ready"],
        ["Shipper adjustment concern", "Long trip can be an excuse", "Travel takes something out of them"],
    ),
    # Jockey
    "jcky_d": (
        ["Rider is a proven winner", "Pilot is in top form", "Right rider aboard today"],
        ["Lighter booking here", "Rider has been cold lately", "Not this jockey's strongest spot"],
    ),
    "IJKYe_Ko25": (
        ["Jockey earns at this trip", "Pilot is money at this distance", "Right rider for this route"],
        ["Jockey struggles at trip", "Pilot has a thin record at this distance", "Jockey-distance combo is a concern"],
    ),
    "IJKYe_kta13": (
        ["Money rider at this oval", "Jockey cashes at this track", "Pilot gets it done here"],
        ["Jockey light record here", "Track has been tough for this rider", "Oval hasn't been kind to this jock"],
    ),
    "JCK_PY_WPS": (
        ["Elite jockey last season", "Coming off a strong year in the irons", "Pilot has a winning pedigree"],
        ["Jockey off form last year", "Last season was a step back", "Recent year in the irons was quiet"],
    ),
    "JKY_CM_WINSAPR25": (
        ["Jockey riding a hot streak", "Pilot is in the zone right now", "Call this rider, firing right now",
         "Hot hand in the irons", "Jock's been cashing tickets all meet"],
        ["Jockey quiet this meet", "Pilot has gone cold at the meet", "Meet hasn't been kind to this jockey",
         "Jockey searching for a winner", "Quiet meet for this rider"],
    ),
    "xJCK_CMWPS26": (
        ["Hot jockey", "Live rider up", "Jockey's name is all over the entry box",
         "The pilot to beat this meet"],
        ["Cold jockey", "Rider has gone ice cold", "Jockey not getting them home lately",
         "Hard to trust this pilot right now"],
    ),
    "iJCK_StrtCM": (
        ["Busy meet book, active rider", "Jockey is getting the calls", "Riders want this pilot"],
        ["Thin meet book", "Not getting many calls this meet", "Jockey on the outside looking in"],
    ),
    "jckcm2_sarm": (
        ["Meet's top pilot", "Best jockey at the meet is up", "A-team rider in the irons"],
        ["Journeyman booking", "Journeyman up, connections going budget", "Not the first call",
         "Rider is available for a reason", "Better riders were busy"],
    ),
    # Connections
    "jntWP365Ko25": (
        ["Trainer & jockey clicking", "Dynamic duo firing together", "These two win when they hook up"],
        ["Trainer/jockey combo cold", "This combo hasn't found the winner's circle lately",
         "Partnership has cooled off"],
    ),
    "TRNJCKCM_kaaw13": (
        ["Barn and rider in sync", "Trainer and jock are dialed in", "Right people in the right spots"],
        ["Barn and rider off sync", "Trainer and jock haven't been connecting", "Partnership needs a win"],
    ),
    "xR308_KEE25": (
        ["Winning connections here", "These connections own this track", "Connections know how to win here"],
        ["Connections struggle here", "Track hasn't been good to these connections", "This oval has been unkind"],
    ),
    "xR309c": (
        ["Power connections", "Heavy hitters in the corners", "Deep pockets and sharp eyes backing {OBJ}"],
        ["Connections lack pop", "Connections haven't been getting it done", "Light connection profile"],
    ),
    # Workouts
    "wotimefrlg_sart": (
        ["Sharp work tab", "Works have been crisp", "Tab says {SUBJ}'s ready to fire"],
        ["Unimpressive works", "Works haven't turned heads", "Tab is underwhelming"],
    ),
    "wotimefrlg_keeom": (
        ["Eye-catching workouts", "Clockers have noticed", "Works have been the talk of the barn area"],
        ["Lackluster work tab", "Clockers aren't impressed", "Works leave something to be desired"],
    ),
    "BullLast3WO": (
        ["Bullet work in the books", "Fastest of the morning recently", "Clocked the bullet, trainer is happy"],
        ["No bullets recently", "No bullets in the recent tab", "Works haven't featured a bullet"],
    ),
    "numbulls3": (
        ["Multiple bullets, crisp", "Stacking bullets, ready to fire", "Bullets galore in the tab"],
        ["Work tab lacks bullets", "No bullets to speak of", "Tab is light on standout works"],
    ),
    "LastWOatTT": (
        ["Worked over today's track", "Has schooled on this surface", "Tuned up right here at home"],
        ["No time over this surface", "Zero experience over today's track", "First look at this surface"],
    ),
    "xwrkdate_kaaw13": (
        ["Well-timed last work", "Trainer timed this perfectly", "Last work was right on schedule"],
        ["Work timing a question", "Spacing of works is a bit off", "Timing of last work raises an eyebrow"],
    ),
    "xwrkdateind": (
        ["Recent work before today", "Fresh off a work, sharp", "Got a good blowout before today"],
        ["Stale between works", "Been a while since {SUBJ} worked", "Could use another work"],
    ),
    "iworkoutpctrnk1_ckta13": (
        ["Top-ranked work tab", "Works grade out at the top", "Training tab is among the best"],
        ["Work tab ranks low", "Works rank near the bottom", "Training tab doesn't grade out well"],
    ),
    "woalone_dmrm": (
        ["Works with company, sharp", "Working with horses around {OBJ}, good sign", "Company works say {SUBJ}'s fit"],
        ["Solo works only", "Has only worked alone, company unknown", "No company in {POSS} works"],
    ),
    # Form / spacing
    "xdaysoff26": (
        ["Ideal spacing off last race", "Trainer has {OBJ} perfectly placed", "Days between races is just right"],
        ["Spacing looks a touch off", "Spacing is a bit unusual", "Days off raises a question"],
    ),
    "xNumDaysSinceLRcut": (
        ["Well-placed off recent race", "Back quickly, fresh off a race", "Short rest has worked before"],
        ["Long layoff to overcome", "Rust is a factor after this absence", "Needs to be sharp off the bench"],
    ),
    "xNumDaysSinceLRcut2": (
        ["Good freshness profile", "Comes in with a clean slate", "Spacing sets {OBJ} up well"],
        ["Extended absence", "Long time between starts, can {SUBJ} fire fresh?", "Layoff is a real question"],
    ),
    "StretchBL_LR26": (
        ["Finished well last out", "Was running at the end last time", "Closed into the stretch well"],
        ["Fell back in the stretch", "Flattened out last time", "Didn't finish off last race"],
    ),
    "ShowedLateSP_LR": (
        ["Showed late energy last out", "Had a kick at the end, promising", "Late energy last out is encouraging"],
        ["No late kick last time", "Didn't have a gear change", "Empty in the stretch last time"],
    ),
    "ltstr_sart": (
        ["Battle-tested veteran", "Has seen it all, experience counts", "Grizzled veteran knows the job"],
        ["Lightly raced, unknown", "Thin record leaves questions", "We don't know much about {OBJ} yet"],
    ),
    # Distance / surface
    "xBRIS_DsPRn_dc": (
        ["Distance pedigree fits today", "Bred to love this trip", "Pedigree page says the distance is right"],
        ["Distance pedigree a question", "Pedigree doesn't scream this distance", "Trip may expose a pedigree concern"],
    ),
    "xturffy_last5": (
        ["Genuine turf horse", "Born to run on grass", "Turf is clearly {POSS} best surface"],
        ["Prefers off the lawn", "Turf has not been {POSS} game", "Numbers say {SUBJ}'d rather be on dirt"],
    ),
    "brisAW_c": (
        ["All-weather specialist", "Synthetic is {POSS} happy place", "All-weather form is legitimate"],
        ["Unproven on synthetic", "Synthetic is a new question", "All-weather is unknown territory"],
    ),
    # Post / field
    "PPt12": (
        ["Favorable post position", "Drew well today", "Post position sets {OBJ} up perfectly"],
        ["Tough draw to overcome", "Stuck on the outside, extra ground", "Wide draw will cost {OBJ} ground"],
    ),
    "xPostPosition": (
        ["Good gate today", "Liked the draw", "Post gives {OBJ} every chance"],
        ["Wide post a concern", "Outside post is a headache", "A lot of ground to make up from here"],
    ),
    "xNumEntLast5cut": (
        ["Ran in full competitive fields", "Has faced numbers before", "Seasoned in full fields"],
        ["Light field experience", "Hasn't faced many horses before", "Big fields may be a new experience"],
    ),
    "xNumEntLast5": (
        ["Seasoned in full fields", "Traffic is nothing new for {OBJ}", "Full fields don't rattle {OBJ}"],
        ["Mostly small fields", "Small fields have been {POSS} comfort zone", "Big field is a step into the unknown"],
    ),
    # Breeding / auction
    "IAucPri_keeA25": (
        ["Blue-blood purchase price", "Cost a fortune, now here to earn it back", "Expensive yearling, class is in there"],
        ["Modest yearling value", "Didn't cost much as a yearling, and it shows", "Budget purchase in a pricey field"],
    ),
    "xAP_KEE25": (
        ["Pricey pedigree, class", "The pedigree page is loaded", "Bred to be a runner"],
        ["Modest pedigree", "Pedigree is unremarkable", "Pedigree page isn't going to impress anyone"],
    ),
    # Age / physical
    "xcMonths_old": (
        ["Peak racing age", "Right in {POSS} prime", "Age and experience working in {POSS} favor"],
        ["Age may factor today", "Time takes its toll, {SUBJ} may be feeling it", "Father Time is undefeated"],
    ),
    "xsexcolt0425": (
        ["Physical profile an edge", "Physical is right for this spot", "Type who should handle this"],
        ["Physical profile a question", "Physical raises some doubt", "Physical may not fit the conditions"],
    ),
    "Weight_LR": (
        ["Comfortable weight", "Weight is right in {POSS} wheelhouse", "Carries today's weight well"],
        ["Weight shift to note", "Carrying more weight than {SUBJ}'d like", "Weight change is worth watching"],
    ),
}

EXCLUDE = {"baseprob2", "Intercept"}

# ---------------------------------------------------------------------------
# DMR.Turf.NonMaiden config-F twin (parity with score.py's TURF_CONFIGF branch)
# ---------------------------------------------------------------------------
# score_dmr_turf blends, per horse, mean(cell, distance-parent, class-parent) of
# 3 of 8 logistic models. Its attribution twin recomputes that same routing and
# blend below. The config-F feature groups + synonym phrases live in a separate
# module and are used ONLY on the config-F path — they are deliberately NOT
# merged into the global FEATURE_GROUPS / SYNONYMS, so KEE / SAR / DMR-dirt /
# maiden comments are unchanged even where config-F reuses a shared var name.
try:
    from attribution_dmr_turf import (
        CONFIGF_FEATURE_GROUPS, CONFIGF_SYNONYMS, load_configf_betas,
    )
    # Scoped lookups: global pools first, config-F additions layered on top.
    _CONFIGF_SYN = {**SYNONYMS, **CONFIGF_SYNONYMS}
    _CONFIGF_GRP = {**FEATURE_GROUPS, **CONFIGF_FEATURE_GROUPS}
except Exception:                      # module absent (KEE/SAR-only deploy)
    CONFIGF_FEATURE_GROUPS = {}
    CONFIGF_SYNONYMS = {}
    load_configf_betas = lambda _cd: {}
    _CONFIGF_SYN = SYNONYMS
    _CONFIGF_GRP = FEATURE_GROUPS

# ---------------------------------------------------------------------------
# DMR.Maiden config-F twin (parity with score.py's MAIDEN_CONFIGF branch)
# ---------------------------------------------------------------------------
# score_dmr_maiden blends, per horse, mean(cell, surface-parent, racetype-parent,
# distance-parent) of 4 of 14 logistic models. Same isolation as the turf twin:
# maiden config-F groups/synonyms are scoped to that path only.
try:
    from attribution_dmr_maiden import (
        CONFIGF_MAIDEN_FEATURE_GROUPS, CONFIGF_MAIDEN_SYNONYMS, load_maiden_configf_betas,
    )
    _MAIDEN_SYN = {**SYNONYMS, **CONFIGF_MAIDEN_SYNONYMS}
    _MAIDEN_GRP = {**FEATURE_GROUPS, **CONFIGF_MAIDEN_FEATURE_GROUPS}
except Exception:                      # module absent (KEE/SAR-only deploy)
    CONFIGF_MAIDEN_FEATURE_GROUPS = {}
    CONFIGF_MAIDEN_SYNONYMS = {}
    load_maiden_configf_betas = lambda _cd: {}
    _MAIDEN_SYN = SYNONYMS
    _MAIDEN_GRP = FEATURE_GROUPS


# ---------------------------------------------------------------------------
# Sub-model definitions — must mirror score.py exactly
# ---------------------------------------------------------------------------
# Dirt / turf use equal-mean ensembling over whichever variants scored a horse
# (matches score._merge_scored_parts: mean(axis=1, skipna=True)).
#
# Maiden uses a weighted blend that mirrors score._score_maiden lines 207–221:
#     score1 = mean over {1,2,3,4,6,8}      weight 0.50
#     score2 = mean over {9,10,12}          weight 0.25
#     score3 = mean over {13,14,15,16}      weight 0.25
#     predicted = 0.50*score1 + 0.25*score2 + 0.25*score3
# The "M" and "S" maiden buckets feed score4, which is NOT used in predicted —
# so we exclude them from attribution. If score.py's maiden blend changes,
# update MAIDEN_BUCKETS / MAIDEN_BUCKET_WEIGHTS below to match.

DIRT_SUBMODELS = ("c", "n", "s", "r")
TURF_SUBMODELS = ("s", "r", "hp", "lp")

# Maiden bucket → list of MAIDEN_MODELS keys feeding that bucket
MAIDEN_BUCKETS = {
    "score1": [1, 2, 3, 4, 6, 8],
    "score2": [9, 10, 12],
    "score3": [13, 14, 15, 16],
}
MAIDEN_BUCKET_WEIGHTS = {"score1": 0.50, "score2": 0.25, "score3": 0.25}


def _maiden_plan(config):
    """
    Resolve the family's maiden blend into (buckets, weights, pred_col_fn).

    Two shapes, mirroring score._score_maiden:

      * SAR — `config.MAIDEN_ENSEMBLE` is a list of
        (filename, suite, racetype, dist, surface, ny) tuples describing a
        3-suite / 32-cell blend. Sub-model keys are the filename stems and
        score.py marks a fired cell with `pred_m_{key}`.

      * KEE (legacy) — `config.MAIDEN_MODELS` keyed 1..16/M/S, bucketed by
        MAIDEN_BUCKETS, fired cells marked with `predicted{key}`.

    Both blend as 0.50*score1 + 0.25*score2 + 0.25*score3.
    """
    ens = getattr(config, "MAIDEN_ENSEMBLE", None)
    if ens:
        buckets = {"score1": [], "score2": [], "score3": []}
        for row in ens:
            fname, suite = row[0], row[1]
            key = str(fname).replace(".sas7bdat", "")
            buckets.setdefault(f"score{suite}", []).append(key)
        return buckets, MAIDEN_BUCKET_WEIGHTS, (lambda k: f"pred_m_{k}")
    return MAIDEN_BUCKETS, MAIDEN_BUCKET_WEIGHTS, (lambda k: f"predicted{k}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Pronoun substitution
# ---------------------------------------------------------------------------
# Comment phrases are authored with tokens ({SUBJ}/{POSS}/{OBJ}) so one phrase
# renders correctly for either sex. The horse's sex comes from the DRF `Sex`
# field: colt/horse/gelding/ridgling -> he/his/him; filly/mare -> she/her.
# Codes seen in real feeds: C, H, G (and lowercase g), R = male; F, M = female.
# (features.py names the "M" dummy sex_male, but M = Mare = female — that label
# is a model-dummy misnomer, not our source of truth here.) Unknown/blank sex
# falls back to female, which is the historical default, so nothing regresses.
_MALE_SEX   = {"C", "H", "G", "R"}
_FEMALE_SEX = {"F", "M"}
_PRONOUNS = {
    "male":   {"SUBJ": "he",  "POSS": "his", "OBJ": "him"},
    "female": {"SUBJ": "she", "POSS": "her", "OBJ": "her"},
}


def _sex_to_gender(sex) -> str:
    s = ("" if sex is None else str(sex)).strip().upper()
    if s in _MALE_SEX:
        return "male"
    if s in _FEMALE_SEX:
        return "female"
    return "female"   # unknown / blank -> historical default


def _apply_pronouns(text: str, sex) -> str:
    """Fill {SUBJ}/{POSS}/{OBJ} tokens in a phrase from the horse's sex."""
    if not text or "{" not in text:
        return text
    p = _PRONOUNS[_sex_to_gender(sex)]
    for tok, word in p.items():
        text = text.replace("{" + tok + "}", word)
    return text


def add_attributions(
    scored_df: pd.DataFrame,
    coeff_dir,
    config,
    feature_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Add why_like_1..3 and why_fade_1..3 to scored_df with synonym rotation
    across the card.

    Math (tightened version):
      Per horse, we replicate score.py's blend exactly so the attribution
      coefficients match the coefficients actually used to score the horse.

      For each sub-model k that fired (its `predicted{k}` column is non-NaN
      on this horse), we contribute `coef_k(feat) * feature_value`. These
      contributions are then combined with the same blend score.py uses:

        Dirt / turf: equal-mean over firing sub-models
        Maiden:      0.50*score1 + 0.25*score2 + 0.25*score3,
                     where each bucket is a mean over firing sub-models

      Then the per-horse contribution per feature is subtracted from the
      race-mean contribution for that feature, yielding the relative
      contribution (positive = above-field strength, negative = below).
    """
    coeff_dir = Path(coeff_dir)
    df = scored_df.copy()

    for i in range(1, 4):
        df[f"why_like_{i}"] = ""
        df[f"why_fade_{i}"] = ""
        # Per-reason magnitude — used by pdf.py to apply the 20%
        # threshold. The value is |delta / race_avg_contribution| for
        # the feature that produced this reason. NaN where empty.
        df[f"why_like_{i}_score"] = float("nan")
        df[f"why_fade_{i}_score"] = float("nan")
    df["int_bar"] = 50          # Intangibles; 50 = field average (see INTANGIBLE_EXCLUDE)
    _int_raw = []               # (race, df_index, raw contribution)

    if feature_df is None:
        logger.warning("attribution: no feature_df — skipping")
        return df

    # ── Merge feature_df fields not already on scored_df ────────────────────
    key_cols = ["Track", "Date", "Race", "HorseName"]
    extra    = [c for c in feature_df.columns if c not in df.columns or c in key_cols]
    merged   = df.merge(feature_df[extra], on=key_cols, how="left", suffixes=("", "_feat"))

    # ── Load every coefficient file once, keyed by sub-model ────────────────
    # Structure: coeff_sets[model_id][sub_key] = {feat_name: coef_value}
    coeff_sets = _load_coefficient_sets(config, coeff_dir, merged.columns)

    # DMR config-F turf twin: when the family runs the config-F turf branch
    # (score.py's TURF_CONFIGF), turf non-maiden races (model_id 2) are scored
    # by score_dmr_turf, NOT the sas7bdat s/r/hp/lp blend. Load its 8 CSV betas
    # once and route those races to _compute_attributions_configf below. Every
    # other family / model is untouched (configf_betas stays None).
    configf_betas = (
        load_configf_betas(coeff_dir)
        if getattr(config, "TURF_CONFIGF", False) else None
    )
    # DMR config-F maiden twin: same idea for maiden races (model_id 3) when the
    # family runs score.py's MAIDEN_CONFIGF branch (14 coef_maid_*.csv betas).
    maiden_configf_betas = (
        load_maiden_configf_betas(coeff_dir)
        if getattr(config, "MAIDEN_CONFIGF", False) else None
    )

    if (not any(coeff_sets.values()) and not configf_betas
            and not maiden_configf_betas):
        logger.warning("attribution: no coefficient files loaded — skipping")
        return df

    # How this family blends its maiden sub-models, and what column marks a
    # fired cell. SAR = 3-suite MAIDEN_ENSEMBLE (pred_m_*); KEE = legacy.
    maiden_plan = _maiden_plan(config)

    # Track synonym usage across the whole card (like and fade separately)
    like_usage: dict[str, int] = {}
    fade_usage: dict[str, int] = {}

    for (_trk, _dt, race), rg in merged.groupby(["Track", "Date", "Race"]):
        model_id = rg["model"].iloc[0]

        # config-F turf twin: parity with score.py's TURF_CONFIGF branch.
        use_configf = (model_id == 2 and configf_betas)
        if use_configf:
            attributions = _compute_attributions_configf(rg, configf_betas)
            pool_map = _CONFIGF_SYN
        elif model_id == 3 and maiden_configf_betas:
            # config-F maiden twin: parity with score.py's MAIDEN_CONFIGF branch.
            attributions = _compute_attributions_configf_maiden(rg, maiden_configf_betas)
            pool_map = _MAIDEN_SYN
        else:
            sub_coefs = coeff_sets.get(model_id, {})
            if not sub_coefs:
                continue
            attributions = _compute_attributions(rg, model_id, sub_coefs, maiden_plan)
            pool_map = None

        if not attributions:
            continue

        for midx, (like_feats, fade_feats) in attributions.items():
            horse = merged.at[midx, "HorseName"]
            orig  = df[(df["Race"] == race) & (df["HorseName"] == horse)]
            if orig.empty:
                continue
            oidx = orig.index[0]
            _ints = getattr(attributions, "intangibles", None)
            if _ints is not None and midx in _ints:
                _int_raw.append((race, oidx, _ints[midx]))

            # Horse sex drives pronoun substitution in the phrase templates
            # (colt/horse/gelding/ridgling -> he/his/him; filly/mare -> she/her).
            sex = merged.at[midx, "Sex"] if "Sex" in merged.columns else (
                  df.at[oidx, "Sex"] if "Sex" in df.columns else "")

            for rank, item in enumerate(like_feats[:3], 1):
                feat = item[0]
                score = item[2] if len(item) >= 3 else float("nan")
                label = _apply_pronouns(
                    _pick_synonym(feat, side="like", usage=like_usage,
                                  pool_map=pool_map), sex)
                df.at[oidx, f"why_like_{rank}"] = label
                df.at[oidx, f"why_like_{rank}_score"] = float(score)

            for rank, item in enumerate(fade_feats[:3], 1):
                feat = item[0]
                score = item[2] if len(item) >= 3 else float("nan")
                label = _apply_pronouns(
                    _pick_synonym(feat, side="fade", usage=fade_usage,
                                  pool_map=pool_map), sex)
                df.at[oidx, f"why_fade_{rank}"] = label
                df.at[oidx, f"why_fade_{rank}_score"] = float(score)


    # Intangibles -> 0-100 bar: relative to each race's field, scaled by the
    # card-wide spread (tanh keeps extremes on the bar; 50 = field average).
    if _int_raw:
        import math
        from collections import defaultdict
        by_race = defaultdict(list)
        for rk, oi, v in _int_raw:
            by_race[rk].append((oi, v))
        rel = []
        for items in by_race.values():
            mean = sum(v for _, v in items) / len(items)
            rel += [(oi, v - mean) for oi, v in items]
        vals = [r for _, r in rel]
        if len(vals) > 1:
            mu = sum(vals) / len(vals)
            sd = (sum((x - mu) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5
        else:
            sd = 0.0
        scale = max(sd, 1e-6) * 1.5
        for oi, r in rel:
            df.at[oi, "int_bar"] = int(round(50 + 50 * math.tanh(r / scale)))
    return df


# ---------------------------------------------------------------------------
# Synonym picker
# ---------------------------------------------------------------------------

def _pick_synonym(feat: str, side: str, usage: dict, pool_map: dict = None) -> str:
    """
    Return the next unused (or least-used) synonym for this feature+side.
    Rotates through the pool so the same phrase doesn't dominate the card.

    pool_map lets the config-F path pass its scoped {global + config-F} pools
    without mutating the global SYNONYMS (keeps other families unchanged).
    """
    pool = (pool_map if pool_map is not None else SYNONYMS).get(feat)
    if not pool:
        return ""

    variants = pool[0] if side == "like" else pool[1]
    if not variants:
        return ""

    # Find which variant has been used least today
    best_label = variants[0]
    best_count = usage.get(variants[0], 0)

    for v in variants[1:]:
        c = usage.get(v, 0)
        if c < best_count:
            best_label = v
            best_count = c

    usage[best_label] = usage.get(best_label, 0) + 1
    return best_label


# ---------------------------------------------------------------------------
# Coefficient loading
# ---------------------------------------------------------------------------

def _load_coefficient_sets(config, coeff_dir: Path, available_columns) -> dict:
    """
    Load every .sas7bdat coefficient file once.

    Returns
    -------
    dict
        coeff_sets[model_id][sub_key] = {feature_name: coefficient}
        model_id: 1 (dirt), 2 (turf), 3 (maiden)
        sub_key: dirt keys "c","n","s","r"; turf keys "s","r","hp","lp";
                 maiden keys are the same keys used in config.MAIDEN_MODELS
                 (ints 1..16 plus "M","S")
    """
    available = set(available_columns)

    def _read_one(filename: str) -> dict | None:
        path = coeff_dir / filename
        if not path.exists():
            return None
        try:
            cdf, _ = pyreadstat.read_sas7bdat(str(path))
        except Exception as e:
            logger.debug(f"  cannot load {path.name}: {e}")
            return None
        if len(cdf) == 0:
            return None
        row = cdf.iloc[0]
        coefs = {}
        for col in cdf.columns:
            if col in EXCLUDE:
                continue
            if col not in available:
                continue
            val = row[col]
            if pd.notna(val):
                coefs[col] = float(val)
        return coefs

    out = {1: {}, 2: {}, 3: {}}

    # Dirt — load every model key the family declares, not just the legacy
    # c/n/s/r.  KEE => c/n/s/r; SAR => core/core_ny/c/n/s/r; future families
    # may add purse splits etc.  The per-horse firing logic keys off the
    # predicted{sub_key} columns score.py emits, so whatever loads here is
    # blended exactly as the ensemble was (incl. NY races -> core_ny alone).
    for sub_key in getattr(config, "DIRT_MODELS", {}):
        fname = getattr(config, "DIRT_MODELS", {}).get(sub_key)
        if fname:
            coefs = _read_one(fname)
            if coefs is not None:
                out[1][sub_key] = coefs

    # Turf — load every turf model key the family declares, not just the
    # legacy KEE s/r/hp/lp.  KEE => s/r/hp/lp; SAR => the v8 hierarchy cells
    # (core/c_i/d_sp/.../x_i_rt_nc) plus the NY-bred models (coreNY/NYr).
    # Iterating the dict (as the dirt loader does) keeps attribution in step
    # with whatever ensemble score._score_turf actually blended.  The
    # per-horse firing logic in _blend_contribution keys off the predicted
    # columns score.py emits (predicted_t_{key} for the hierarchy,
    # predicted{key} for the legacy blend), so whatever loads here is
    # attributed exactly as it was scored (incl. NY races -> coreNY/NYr).
    for sub_key in getattr(config, "TURF_MODELS", {}):
        fname = getattr(config, "TURF_MODELS", {}).get(sub_key)
        if fname:
            coefs = _read_one(fname)
            if coefs is not None:
                out[2][sub_key] = coefs

    # Maiden — SAR declares MAIDEN_ENSEMBLE (3-suite, 32 cells, keys are the
    # coefficient-file stems). KEE uses the legacy MAIDEN_MODELS map keyed
    # 1..16/M/S, of which only the buckets feeding predicted (score1/2/3) are
    # attributed; "M"/"S" feed score4, which isn't in predicted, so skip them.
    maiden_ens = getattr(config, "MAIDEN_ENSEMBLE", None)
    if maiden_ens:
        for row in maiden_ens:
            fname = row[0]
            coefs = _read_one(fname)
            if coefs is not None:
                out[3][str(fname).replace(".sas7bdat", "")] = coefs
        _maiden_legacy = {}
    else:
        _maiden_legacy = getattr(config, "MAIDEN_MODELS", {})
    maiden_keys_in_use = {k for ks in MAIDEN_BUCKETS.values() for k in ks}
    for sub_key, fname in _maiden_legacy.items():
        if sub_key not in maiden_keys_in_use:
            continue
        if fname:
            coefs = _read_one(fname)
            if coefs is not None:
                out[3][sub_key] = coefs

    n_dirt = len(out[1])
    n_turf = len(out[2])
    n_maid = len(out[3])
    logger.info(
        f"attribution: loaded {n_dirt} dirt + {n_turf} turf + "
        f"{n_maid} maiden coefficient sets"
    )
    return out


# ---------------------------------------------------------------------------
# Attribution computation
# ---------------------------------------------------------------------------

def _compute_attributions(race_df, model_id, sub_coefs, maiden_plan=None):
    """
    Compute per-horse relative feature contributions for one race.

    Parameters
    ----------
    race_df : DataFrame
        Rows for one race (one row per horse) with feature columns + the
        `predicted{sub_key}` columns that score.py emitted, so we can tell
        which sub-models actually scored each horse.
    model_id : int
        1 = dirt, 2 = turf, 3 = maiden.
    sub_coefs : dict
        {sub_key: {feature_name: coef}} for the relevant model family.

    Returns
    -------
    dict
        {row_index: (like_list, fade_list)}, where each list is
        [(feature_name, delta), ...] sorted by impact descending.
    """
    if not sub_coefs:
        return None

    # Union of every feature any active sub-model uses (so we have a stable
    # feature axis for the per-horse contribution vectors).
    all_feats = set()
    for coefs in sub_coefs.values():
        all_feats.update(coefs.keys())
    all_feats = [f for f in all_feats if f in race_df.columns]
    if not all_feats:
        return None

    # ── Build per-horse contribution rows ──────────────────────────────────
    contrib_rows = {}  # idx → {feat: contribution}
    for idx in race_df.index:
        row = race_df.loc[idx]
        contrib_rows[idx] = _blend_contribution(
            row, model_id, sub_coefs, all_feats, maiden_plan
        )

    if not contrib_rows:
        return None

    # Diagnostic: a horse whose blend found no firing sub-model gets an empty
    # contribution dict. If that happens to every horse the race produces no
    # reasons at all, which is the "No standout attributes either way" bug.
    n_empty = sum(1 for v in contrib_rows.values() if not v)
    if n_empty:
        pred_cols = [c for c in race_df.columns if str(c).startswith("predicted")]
        logger.warning(
            "attribution: model=%s — %d/%d horses had NO firing sub-model. "
            "sub_coefs keys=%s ; predicted* cols present=%s",
            model_id, n_empty, len(contrib_rows),
            sorted(map(str, sub_coefs.keys()))[:20],
            sorted(pred_cols)[:20],
        )

    # Rank / dedupe / threshold is shared with the config-F twin.
    return _rank_contributions(race_df, contrib_rows, all_feats)


def _rank_contributions(
    race_df,
    contrib_rows: dict,
    all_feats: list,
    synonyms: dict = None,
    feature_groups: dict = None,
):
    """
    Turn per-horse feature contributions into ranked like/fade reason lists.

    Shared by the legacy dirt/turf/maiden path (_compute_attributions) and the
    DMR config-F twin (_compute_attributions_configf). The config-F caller
    passes its SCOPED {global + config-F} synonym / group maps so config-F vars
    resolve to phrases without those additions leaking into the other families.

    Returns {row_index: (like_list, fade_list)} where each item is
    (feature_name, |delta|_or_delta, score).
    """
    syn = synonyms if synonyms is not None else SYNONYMS
    grp = feature_groups if feature_groups is not None else FEATURE_GROUPS

    # ── Subtract race average per feature ──────────────────────────────────
    # NOTE: build with an explicit index/column axis. pandas' from_dict with
    # dict values silently DROPS rows whose dict is empty (and returns an
    # empty RangeIndex frame when every row is empty), which used to blow up
    # `rel.loc[idx]` below with KeyError. Reindexing pins the row axis to the
    # race's own index so non-firing horses survive as all-zero rows.
    cdf2  = pd.DataFrame.from_dict(contrib_rows, orient="index")
    cdf2  = cdf2.reindex(index=list(race_df.index), columns=all_feats).fillna(0.0)
    r_avg = cdf2.mean(axis=0)
    rel   = cdf2.subtract(r_avg, axis=1)

    # ── Rank features per horse, dedupe by theme group, threshold ──────────
    results = {}
    for idx in race_df.index:
        row = rel.loc[idx]

        # Deduplicate by theme group — keep the feature with largest |delta|
        best = {}
        for feat, delta in row.items():
            d = float(delta)
            g = grp.get(feat, feat)
            if g not in best or abs(d) > abs(best[g][1]):
                best[g] = (feat, d)

        items = sorted(best.values(), key=lambda x: -x[1])

        likes, fades = [], []
        for feat, delta in items:
            if feat not in syn:
                continue
            # Score: |delta / r_avg[feat]|. Used by pdf.py to threshold
            # at 20% (i.e. show only reasons where the horse differs from
            # the race average by more than 20%). Falls back to absolute
            # delta when r_avg for that feature is near zero (avoids
            # divide-by-zero blowups).
            ravg_feat = float(r_avg.get(feat, 0.0))
            if abs(ravg_feat) > 1e-9:
                score = abs(delta) / abs(ravg_feat)
            else:
                # Race-average contribution ≈ 0. Fall back to a large
                # score so the reason isn't dropped; pdf.py will keep it.
                score = abs(delta) * 1000.0
            if delta > 0.005:
                likes.append((feat, delta, score))
            elif delta < -0.005:
                fades.append((feat, abs(delta), score))

        # Fallback when no signal clears threshold — pick the strongest
        # one in each direction that has a synonym.
        if not likes and items:
            for feat, delta in items:
                if feat in syn and delta > 0:
                    ravg_feat = float(r_avg.get(feat, 0.0))
                    score = (abs(delta) / abs(ravg_feat)) if abs(ravg_feat) > 1e-9 else abs(delta) * 1000.0
                    likes.append((feat, delta, score))
                    break
        if not fades and items:
            for feat, delta in reversed(items):
                if feat in syn and delta < 0:
                    ravg_feat = float(r_avg.get(feat, 0.0))
                    score = (abs(delta) / abs(ravg_feat)) if abs(ravg_feat) > 1e-9 else abs(delta) * 1000.0
                    fades.append((feat, abs(delta), score))
                    break

        results[idx] = (likes, fades)

    out = _RaceAttributions(results)
    out.intangibles = {
        idx: float(sum(v for f, v in (c or {}).items()
                       if grp.get(f, "other") not in INTANGIBLE_EXCLUDE))
        for idx, c in contrib_rows.items()
    }
    return out


def _compute_attributions_configf(race_df, betas: dict):
    """
    DMR.Turf.NonMaiden config-F attribution — the twin of score_dmr_turf.

    Mirrors the scoring blend exactly: for each horse route to (cell, distance
    parent, class parent) via score_dmr_turf._route, then the per-feature
    contribution is the equal-mean of coef*value across those three models —
    matching cf = (p_cell + p_dist + p_cls)/3. Uses the SCOPED config-F synonym
    / group maps so nothing leaks into the other families.

    Returns {row_index: (like_list, fade_list)} (same shape as
    _compute_attributions) or None if nothing to attribute.
    """
    if not betas:
        return None

    # Import here so attribution.py still imports when the config-F modules are
    # absent (KEE/SAR-only deploys).
    from score_dmr_turf import _route

    # Stable feature axis = every non-meta beta any of the 8 models carries,
    # intersected with what's actually on the frame. baseprob2 / Intercept are
    # excluded (EXCLUDE) to match _load_coefficient_sets' convention.
    all_feats = set()
    for b in betas.values():
        all_feats.update(k for k in b.keys() if k not in EXCLUDE)
    all_feats = [f for f in all_feats if f in race_df.columns]
    if not all_feats:
        return None

    cellkey, distkey, clskey = _route(race_df)

    contrib_rows = {}
    for pos, idx in enumerate(race_df.index):
        row = race_df.loc[idx]
        # Pre-extract feature values once (NaN -> 0, matching score's fillna(0)).
        fvals = {}
        for f in all_feats:
            v = row.get(f)
            try:
                fvals[f] = 0.0 if pd.isna(v) else float(v)
            except (TypeError, ValueError):
                fvals[f] = 0.0
        # The three models this horse blends (cell + distance + class parent).
        keys = (str(cellkey[pos]), str(distkey[pos]), str(clskey[pos]))
        contrib = {f: 0.0 for f in all_feats}
        for k in keys:
            b = betas.get(k, {})
            for f in all_feats:
                c = b.get(f)
                if c is not None:
                    contrib[f] += c * fvals[f]
        for f in all_feats:
            contrib[f] /= 3.0            # equal-mean of the 3, == cf blend
        contrib_rows[idx] = contrib

    if not contrib_rows:
        return None

    return _rank_contributions(
        race_df, contrib_rows, all_feats,
        synonyms=_CONFIGF_SYN, feature_groups=_CONFIGF_GRP,
    )


def _compute_attributions_configf_maiden(race_df, betas: dict):
    """
    DMR.Maiden config-F attribution — the twin of score_dmr_maiden.

    Mirrors the scoring blend exactly: for each horse route to (cell, surface
    parent, racetype parent, distance parent) via score_dmr_maiden._route, then
    the per-feature contribution is the equal-mean of coef*value across those
    four models — matching cf = (p_cell + p_surf + p_rt + p_dist)/4. Uses the
    SCOPED maiden config-F synonym / group maps so nothing leaks into the other
    families.

    Returns {row_index: (like_list, fade_list)} (same shape as
    _compute_attributions) or None if nothing to attribute.
    """
    if not betas:
        return None

    # Import here so attribution.py still imports when the config-F modules are
    # absent (KEE/SAR-only deploys).
    from score_dmr_maiden import _route

    # Stable feature axis = every non-meta beta any of the 14 models carries,
    # intersected with what's actually on the frame. baseprob2 / Intercept are
    # excluded (EXCLUDE) to match _load_coefficient_sets' convention.
    all_feats = set()
    for b in betas.values():
        all_feats.update(k for k in b.keys() if k not in EXCLUDE)
    all_feats = [f for f in all_feats if f in race_df.columns]
    if not all_feats:
        return None

    cellkey, surfkey, rtkey, distkey = _route(race_df)

    contrib_rows = {}
    for pos, idx in enumerate(race_df.index):
        row = race_df.loc[idx]
        # Pre-extract feature values once (NaN -> 0, matching score's fillna(0)).
        fvals = {}
        for f in all_feats:
            v = row.get(f)
            try:
                fvals[f] = 0.0 if pd.isna(v) else float(v)
            except (TypeError, ValueError):
                fvals[f] = 0.0
        # The four models this horse blends (cell + surface + racetype + distance parent).
        keys = (str(cellkey[pos]), str(surfkey[pos]), str(rtkey[pos]), str(distkey[pos]))
        contrib = {f: 0.0 for f in all_feats}
        for k in keys:
            b = betas.get(k, {})
            for f in all_feats:
                c = b.get(f)
                if c is not None:
                    contrib[f] += c * fvals[f]
        for f in all_feats:
            contrib[f] /= 4.0            # equal-mean of the 4, == cf blend
        contrib_rows[idx] = contrib

    if not contrib_rows:
        return None

    return _rank_contributions(
        race_df, contrib_rows, all_feats,
        synonyms=_MAIDEN_SYN, feature_groups=_MAIDEN_GRP,
    )


def _blend_contribution(
    horse_row,
    model_id: int,
    sub_coefs: dict,
    feats: list[str],
    maiden_plan=None,
) -> dict:
    """
    Compute per-feature contribution for a single horse, replicating
    score.py's blend math.

    For dirt (1) and turf (2): equal-mean over sub-models that fired
    (i.e., where the horse has a non-NaN predicted{sub_key} value).

    For maiden (3): score.py uses the weighted formula
        predicted = 0.50*score1 + 0.25*score2 + 0.25*score3
    where each scoreN is a mean over its bucket of sub-models. We mirror
    that exactly: contributions are first averaged within each bucket
    over firing sub-models, then weighted-summed across buckets.

    Returns {feature_name: blended_contribution_value}.
    """
    # Pre-extract feature values once.
    fvals = {}
    for f in feats:
        v = horse_row.get(f)
        try:
            fvals[f] = 0.0 if pd.isna(v) else float(v)
        except (TypeError, ValueError):
            fvals[f] = 0.0

    # Which sub-models actually scored this horse?
    # score.py emits the per-sub-model probability column that tells us a cell
    # fired.  The turf HIERARCHY (SAR) writes predicted_t_{key}; every other
    # path (dirt, maiden, and the legacy KEE turf blend) writes predicted{key}.
    # Prefer the _t_ column for turf when it exists, else fall back — this keeps
    # KEE turf and all dirt/maiden scoring detected exactly as before.
    m_buckets, m_weights, m_pred_col = (
        maiden_plan if maiden_plan
        else (MAIDEN_BUCKETS, MAIDEN_BUCKET_WEIGHTS, (lambda k: f"predicted{k}"))
    )

    firing = []
    for sub_key in sub_coefs.keys():
        pred_col = f"predicted{sub_key}"
        if model_id == 2:
            t_col = f"predicted_t_{sub_key}"
            if t_col in horse_row.index:
                pred_col = t_col
        elif model_id == 3:
            # SAR's 3-suite maiden marks a fired cell with pred_m_{key};
            # the legacy KEE maiden uses predicted{key}.
            pred_col = m_pred_col(sub_key)
        v = horse_row.get(pred_col)
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            firing.append(sub_key)
    if not firing:
        return {}

    # ── Dirt or turf: equal mean over firing sub-models ────────────────────
    if model_id in (1, 2):
        contrib = {f: 0.0 for f in feats}
        for sub_key in firing:
            coefs = sub_coefs[sub_key]
            for f in feats:
                c = coefs.get(f)
                if c is not None:
                    contrib[f] += c * fvals[f]
        n = len(firing)
        if n > 1:
            for f in feats:
                contrib[f] /= n
        return contrib

    # ── Maiden: weighted bucket blend mirroring _score_maiden ──────────────
    if model_id == 3:
        bucket_contribs = {}  # bucket_name → {feat: contribution} or None
        for bucket_name, bucket_keys in m_buckets.items():
            firing_in_bucket = [k for k in bucket_keys if k in firing]
            if not firing_in_bucket:
                bucket_contribs[bucket_name] = None
                continue
            bc = {f: 0.0 for f in feats}
            for sub_key in firing_in_bucket:
                coefs = sub_coefs.get(sub_key, {})
                for f in feats:
                    c = coefs.get(f)
                    if c is not None:
                        bc[f] += c * fvals[f]
            n = len(firing_in_bucket)
            if n > 1:
                for f in feats:
                    bc[f] /= n
            bucket_contribs[bucket_name] = bc

        # Apply weights. score.py uses `.fillna(0)` on the score columns
        # before weighting, which is equivalent to: a missing bucket
        # contributes 0 to predicted. We mirror that behavior here so the
        # attribution profile reflects what actually went into the score.
        contrib = {f: 0.0 for f in feats}
        for bucket_name, weight in m_weights.items():
            bc = bucket_contribs.get(bucket_name)
            if bc is None:
                continue
            for f in feats:
                contrib[f] += weight * bc[f]
        return contrib

    # Unknown model_id — shouldn't happen
    logger.warning(f"attribution: unknown model_id {model_id}")
    return {}
