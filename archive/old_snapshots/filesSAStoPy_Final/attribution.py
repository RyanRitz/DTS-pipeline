"""
BTSM Pipeline — attribution.py
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
        ["Speed to burn vs this field", "Figures say she's faster", "Raw speed advantage here"],
        ["Lacks the foot here", "Doesn't have the gears", "Speed edge goes the other way"],
    ),
    "XBPPR_tc12_3": (
        ["Fastest at this oval — period", "Owns this track on numbers", "The speed figure leader here"],
        ["Can't match the speedsters", "Gets outfigured at this oval", "Speed profile doesn't fit"],
    ),
    "BBtrck_kaaw13": (
        ["Best figure suits this track", "Track record says she can fire", "History here is encouraging"],
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
        ["Sharp speed numbers", "Speed tab says she's live", "Figures point her way"],
        ["Thin speed numbers", "Speed tab is light", "Figures are a question mark"],
    ),
    "drf1_sart": (
        ["Quick last-out figure", "Good number last time out", "Last race figure is solid"],
        ["Slow last-out figure", "Last figure was ordinary", "Last-out number doesn't cut it"],
    ),
    "histspd_dmrd": (
        ["Consistent speed history", "Has run big figures before", "Back figures are there"],
        ["Inconsistent speed history", "Figure history is erratic", "Can't pin down her best"],
    ),
    "xBRISSpeedAWc_keeod": (
        ["Proven all-weather form", "Has figured it out on synthetic", "Likes the all-weather surface"],
        ["Limited all-weather form", "All-weather is a new look", "Synthetic surface unproven"],
    ),
    "xdrfsp1m_sard": (
        ["Fast turf figures", "Turf numbers are sharp", "Figures say she's a turf horse"],
        ["Slow turf figures", "Turf numbers lag the field", "Figures don't say turf horse"],
    ),
    "xDRF2_Ko25": (
        ["Back class shows up", "Second-last was a good effort", "Shows she can run"],
        ["Declining recent figures", "Numbers going in wrong direction", "Form is trending down"],
    ),
    # Pace / style
    "EarlySpeed": (
        ["Tactical speed", "Gets the jump out of the gate", "Rates off the pace nicely"],
        ["No early position", "Gets away slowly — uphill battle", "Will need some luck from the back"],
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
        ["Class concern today", "May be in over her head", "Moving up asks a question"],
    ),
    "eps4dalt_sard": (
        ["Quality earnings profile", "Has earned her way here", "Bankroll says she can run"],
        ["Modest earnings profile", "Earnings don't back it up", "Thin earnings for this level"],
    ),
    "EPS3_SARD15": (
        ["Proven earner vs field", "Outearns most in here", "The money says she's legit"],
        ["Earns below field average", "Field outearns her", "Earnings lag the competition"],
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
        ["First-time turf — risky", "Turf debut is always a question", "Going to school on the grass today"],
    ),
    "HC_ShipperToUS": (
        ["Respected shipper connections", "Barn makes travel work", "Shippers from this barn arrive ready"],
        ["Shipper adjustment concern", "Long trip can be an excuse", "Travel takes something out of them"],
    ),
    # Jockey
    "jcky_d": (
        ["Accomplished turf rider", "Knows her way around a turf course", "Pilot is at home on grass"],
        ["Lighter turf booking", "Not known as a grass rider", "Jockey less effective on turf"],
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
        ["Jockey riding a hot streak", "Pilot is in the zone right now", "Call this rider — he's firing",
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
        ["Busy meet book — active rider", "Jockey is getting the calls", "Riders want this pilot"],
        ["Thin meet book", "Not getting many calls this meet", "Jockey on the outside looking in"],
    ),
    "jckcm2_sarm": (
        ["Meet's top pilot", "Best jockey at the meet is up", "A-team rider in the irons"],
        ["Journeyman booking", "Journeyman up — connections going budget", "Not the first call",
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
        ["Power connections", "Heavy hitters in the corners", "Deep pockets and sharp eyes backing her"],
        ["Connections lack pop", "Connections haven't been getting it done", "Light connection profile"],
    ),
    # Workouts
    "wotimefrlg_sart": (
        ["Sharp work tab", "Works have been crisp", "Tab says she's ready to fire"],
        ["Unimpressive works", "Works haven't turned heads", "Tab is underwhelming"],
    ),
    "wotimefrlg_keeom": (
        ["Eye-catching workouts", "Clockers have noticed", "Works have been the talk of the barn area"],
        ["Lackluster work tab", "Clockers aren't impressed", "Works leave something to be desired"],
    ),
    "BullLast3WO": (
        ["Bullet work in the books", "Fastest of the morning recently", "Clocked the bullet — trainer is happy"],
        ["No bullets recently", "No bullets in the recent tab", "Works haven't featured a bullet"],
    ),
    "numbulls3": (
        ["Multiple bullets — crisp", "Stacking bullets — ready to fire", "Bullets galore in the tab"],
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
        ["Recent work before today", "Fresh off a work — sharp", "Got a good blowout before today"],
        ["Stale between works", "Been a while since she worked", "Could use another work"],
    ),
    "iworkoutpctrnk1_ckta13": (
        ["Top-ranked work tab", "Works grade out at the top", "Training tab is among the best"],
        ["Work tab ranks low", "Works rank near the bottom", "Training tab doesn't grade out well"],
    ),
    "woalone_dmrm": (
        ["Works with company — sharp", "Working with horses around her — good sign", "Company works say she's fit"],
        ["Solo works only", "Has only worked alone — company unknown", "No company in her works"],
    ),
    # Form / spacing
    "xdaysoff26": (
        ["Ideal spacing off last race", "Trainer has her perfectly placed", "Days between races is just right"],
        ["Spacing looks a touch off", "Spacing is a bit unusual", "Days off raises a question"],
    ),
    "xNumDaysSinceLRcut": (
        ["Well-placed off recent race", "Back quickly — fresh off a race", "Short rest has worked before"],
        ["Long layoff to overcome", "Rust is a factor after this absence", "Needs to be sharp off the bench"],
    ),
    "xNumDaysSinceLRcut2": (
        ["Good freshness profile", "Comes in with a clean slate", "Spacing sets her up well"],
        ["Extended absence", "Long time between starts — can she fire fresh?", "Layoff is a real question"],
    ),
    "StretchBL_LR26": (
        ["Finished well last out", "Was running at the end last time", "Closed into the stretch well"],
        ["Fell back in the stretch", "Flattened out last time", "Didn't finish off last race"],
    ),
    "ShowedLateSP_LR": (
        ["Showed late energy last out", "Had a kick at the end — promising", "Late energy last out is encouraging"],
        ["No late kick last time", "Didn't have a gear change", "Empty in the stretch last time"],
    ),
    "ltstr_sart": (
        ["Battle-tested veteran", "Has seen it all — experience counts", "Grizzled veteran knows the job"],
        ["Lightly raced — unknown", "Thin record leaves questions", "We don't know much about her yet"],
    ),
    # Distance / surface
    "xBRIS_DsPRn_dc": (
        ["Distance pedigree fits today", "Bred to love this trip", "Pedigree page says the distance is right"],
        ["Distance pedigree a question", "Pedigree doesn't scream this distance", "Trip may expose a pedigree concern"],
    ),
    "xturffy_last5": (
        ["Genuine turf horse", "Born to run on grass", "Turf is clearly her best surface"],
        ["Prefers off the lawn", "Turf has not been her game", "Numbers say she'd rather be on dirt"],
    ),
    "brisAW_c": (
        ["All-weather specialist", "Synthetic is her happy place", "All-weather form is legitimate"],
        ["Unproven on synthetic", "Synthetic is a new question", "All-weather is unknown territory"],
    ),
    # Post / field
    "PPt12": (
        ["Favorable post position", "Drew well today", "Post position sets her up perfectly"],
        ["Tough draw to overcome", "Stuck on the outside — extra ground", "Wide draw will cost her ground"],
    ),
    "xPostPosition": (
        ["Good gate today", "Liked the draw", "Post gives her every chance"],
        ["Wide post a concern", "Outside post is a headache", "A lot of ground to make up from here"],
    ),
    "xNumEntLast5cut": (
        ["Ran in full competitive fields", "Has faced numbers before", "Seasoned in full fields"],
        ["Light field experience", "Hasn't faced many horses before", "Big fields may be a new experience"],
    ),
    "xNumEntLast5": (
        ["Seasoned in full fields", "Traffic is nothing new for her", "Full fields don't rattle her"],
        ["Mostly small fields", "Small fields have been her comfort zone", "Big field is a step into the unknown"],
    ),
    # Breeding / auction
    "IAucPri_keeA25": (
        ["Blue-blood purchase price", "Cost a fortune — now here to earn it back", "Expensive yearling — class is in there"],
        ["Modest yearling value", "Didn't cost much as a yearling — and it shows", "Budget purchase in a pricey field"],
    ),
    "xAP_KEE25": (
        ["Pricey pedigree — class", "The pedigree page is loaded", "Bred to be a runner"],
        ["Modest pedigree", "Pedigree is unremarkable", "Pedigree page isn't going to impress anyone"],
    ),
    # Age / physical
    "xcMonths_old": (
        ["Peak racing age", "Right in her prime", "Age and experience working in her favor"],
        ["Age may factor today", "Time takes its toll — she may be feeling it", "Father Time is undefeated"],
    ),
    "xsexcolt0425": (
        ["Physical profile an edge", "Physical is right for this spot", "Type who should handle this"],
        ["Physical profile a question", "Physical raises some doubt", "Physical may not fit the conditions"],
    ),
    "Weight_LR": (
        ["Comfortable weight", "Weight is right in her wheelhouse", "Carries today's weight well"],
        ["Weight shift to note", "Carrying more weight than she'd like", "Weight change is worth watching"],
    ),
}

EXCLUDE = {"baseprob2", "Intercept"}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def add_attributions(
    scored_df: pd.DataFrame,
    coeff_dir,
    config,
    feature_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Add why_like_1..3 and why_fade_1..3 with synonym rotation across the card."""
    coeff_dir = Path(coeff_dir)
    df = scored_df.copy()

    for i in range(1, 4):
        df[f"why_like_{i}"] = ""
        df[f"why_fade_{i}"] = ""

    if feature_df is None:
        logger.warning("attribution: no feature_df — skipping")
        return df

    key_cols = ["Track", "Date", "Race", "HorseName"]
    extra    = [c for c in feature_df.columns if c not in df.columns or c in key_cols]
    merged   = df.merge(feature_df[extra], on=key_cols, how="left", suffixes=("", "_feat"))

    model_map = _build_model_map(config, coeff_dir)

    # Track synonym usage across the whole card (like and fade separately)
    like_usage: dict[str, int] = {}  # label_key → times used today
    fade_usage: dict[str, int] = {}

    for (_, _, race), rg in merged.groupby(["Track", "Date", "Race"]):
        model_id    = rg["model"].iloc[0]
        coeff_files = model_map.get(model_id, [])
        if not coeff_files:
            continue

        attributions = _compute_attributions(rg, coeff_files, merged.columns)
        if not attributions:
            continue

        for midx, (like_feats, fade_feats) in attributions.items():
            horse = merged.at[midx, "HorseName"]
            orig  = df[(df["Race"] == race) & (df["HorseName"] == horse)]
            if orig.empty:
                continue
            oidx = orig.index[0]

            for rank, (feat, _) in enumerate(like_feats[:3], 1):
                label = _pick_synonym(feat, side="like", usage=like_usage)
                df.at[oidx, f"why_like_{rank}"] = label

            for rank, (feat, _) in enumerate(fade_feats[:3], 1):
                label = _pick_synonym(feat, side="fade", usage=fade_usage)
                df.at[oidx, f"why_fade_{rank}"] = label

    return df


# ---------------------------------------------------------------------------
# Synonym picker
# ---------------------------------------------------------------------------

def _pick_synonym(feat: str, side: str, usage: dict) -> str:
    """
    Return the next unused (or least-used) synonym for this feature+side.
    Rotates through the pool so the same phrase doesn't dominate the card.
    """
    pool = SYNONYMS.get(feat)
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
# Attribution computation
# ---------------------------------------------------------------------------

def _compute_attributions(race_df, coeff_files, all_columns):
    coefs = {}
    for path in coeff_files:
        try:
            cdf, _ = pyreadstat.read_sas7bdat(str(path))
        except Exception as e:
            logger.debug(f"Cannot load {path}: {e}")
            continue
        row = cdf.iloc[0]
        for col in cdf.columns:
            if col in EXCLUDE:
                continue
            val = row[col]
            if pd.notna(val) and col in all_columns:
                fv = float(val)
                coefs[col] = (coefs[col] + fv) / 2 if col in coefs else fv

    if not coefs:
        return None

    contrib = {}
    for feat, coef in coefs.items():
        if feat not in race_df.columns:
            continue
        vals = pd.to_numeric(race_df[feat], errors="coerce").fillna(0)
        contrib[feat] = vals * coef

    if not contrib:
        return None

    cdf2  = pd.DataFrame(contrib, index=race_df.index)
    r_avg = cdf2.mean(axis=0)
    rel   = cdf2.subtract(r_avg, axis=1)

    results = {}
    for idx in race_df.index:
        row = rel.loc[idx]

        # Deduplicate by theme group
        best = {}
        for feat, delta in row.items():
            d = float(delta)
            g = FEATURE_GROUPS.get(feat, feat)
            if g not in best or abs(d) > abs(best[g][1]):
                best[g] = (feat, d)

        items = sorted(best.values(), key=lambda x: -x[1])

        likes, fades = [], []
        for feat, delta in items:
            if feat not in SYNONYMS:
                continue
            if delta > 0.005:
                likes.append((feat, delta))
            elif delta < -0.005:
                fades.append((feat, abs(delta)))

        # Fallback when no signals clear threshold
        if not likes and items:
            for feat, delta in items:
                if feat in SYNONYMS and delta > 0:
                    likes.append((feat, delta))
                    break
        if not fades and items:
            for feat, delta in reversed(items):
                if feat in SYNONYMS and delta < 0:
                    fades.append((feat, abs(delta)))
                    break

        results[idx] = (likes, fades)

    return results


def _build_model_map(config, coeff_dir):
    def resolve(d):
        return [coeff_dir / v for v in d.values()
                if isinstance(v, str) and (coeff_dir / v).exists()]
    return {
        1: resolve(config.DIRT_MODELS),
        2: resolve(config.TURF_MODELS),
        3: resolve(config.MAIDEN_MODELS),
    }
