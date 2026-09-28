"""
DTS Automation Pipeline — Model Setup
========================================
This is where you tell the orchestrator which model family to use for
which track.

Today there's one active family: KEE (Keeneland models, currently April 2026).
The registry is bootstrapped from config.py — whatever's in config.DIRT_MODELS,
TURF_MODELS, MAIDEN_MODELS, SCORE_WEIGHTS, COEFF_DIR becomes the "KEE"
family, and KEE is the default fallback for every track.

When you build models for a new track family (Saratoga, Churchill, etc.):
  1. Drop the .sas7bdat files into COEFF_DIR / "SAR" (or wherever)
  2. Uncomment + edit the register_family/register_track block below
  3. Restart the pipeline — that track now uses the new models, others
     still fall back to KEE

When the active KEE meet rotates (April -> October):
  Just edit config.DIRT_MODELS / TURF_MODELS / MAIDEN_MODELS to point at
  the new filenames. The registry re-bootstraps from config on next run.
  No changes needed in this file unless you also want to keep the old
  meet's models around as a separate family for backtesting.

Public API:
    setup_registry(config) -> None
        Idempotent. Called once during run_pipeline.py startup. Bootstraps
        from config and applies any per-track overrides defined below.
"""

from __future__ import annotations

import logging
from pathlib import Path

from model_registry import (
    bootstrap_from_config,
    register_family,
    register_track,
    register_track_seasonal,
    list_registered,
)

logger = logging.getLogger(__name__)

# KEE maiden cell -> filename suffix (mirrors config.MAIDEN_MODELS' April names,
# which are hardcoded to kee_maid_0426_*; parameterized here so the Fall meet can
# resolve kee_maid_10yy_* instead of being pinned to April).
_KEE_MAID_CELLS = {
    1: "spst", 2: "spsd", 3: "sprt", 4: "sprd", 6: "msd", 8: "mrd",
    9: "spt", 10: "spd", 12: "md", 13: "sps", 14: "spr", 15: "ms",
    16: "mr", "M": "m", "S": "s",
}


def _kee_family_models(config, mm: str):
    """Build (dirt, turf, maiden) coef-filename dicts for a KEE meet month.
    mm = '04' (Spring) or '10' (Fall). Mirrors config.py's KEE naming exactly,
    but with the month/season swapped in — so KEE_OCT is the same structure as
    April with '10' filenames. Year comes from config.YEAR."""
    yyyy = str(config.YEAR)[-4:]
    yy = yyyy[-2:]
    dirt = {k: f"keedirt{mm}{yyyy}{k}.sas7bdat" for k in ("c", "n", "s", "r")}
    turf = {k: f"keeturf{mm}{yyyy}{k}.sas7bdat" for k in ("s", "r", "hp", "lp")}
    maiden = {k: f"kee_maid_{mm}{yy}_{sfx}.sas7bdat" for k, sfx in _KEE_MAID_CELLS.items()}
    return dirt, turf, maiden


def setup_registry(config) -> None:
    """
    Build the model registry. Called once at pipeline startup.
    Idempotent — safe to call multiple times in the same process.
    """
    # ── 1. Bootstrap KEE family from config.py (the active meet's models) ──
    # This "KEE" family is the SPRING/April meet (config.RACE_DATE is an April
    # date) and stays the universal fallback for unregistered tracks.
    bootstrap_from_config(config, default_family="KEE")

    # ── 1b. Keeneland FALL/October 2026 as a SEPARATE model family ─────────
    # KEE runs two meets a year (Spring/April, Fall/October) that Ryan treats as
    # completely different models. The Fall-2026 build is NOT a filename clone of
    # April — it is a DIFFERENT ensemble ARCHITECTURE (locked 2026-09):
    #   * DIRT  : 5-cell equal blend  mean(core, clm, nc, s, r)   [April had no core]
    #   * TURF  : core/s/r + graded override  0.7*g + 0.3*mean(core,s,r)
    #   * MAIDEN: 7-cell 4-way blend  (core + racetype-pooled + dist/surf cell)
    # Coefficients are the PROC LOGISTIC outest= tables copied into COEFF_DIR:
    #   keedirt102026_{core,c,n,s,r}, keeturf102026_{core,s,r,g},
    #   kee_maid_{core1026, 1026_m/s/st/sd/msp/mrt}.
    #
    # DIRT reuses the existing SAR-style ensemble scorer (_score_dirt reads
    # DIRT_ENSEMBLE) — mean of the 5 cells, no NY-restricted core at KEE.
    # TURF and MAIDEN Oct26 blends are NEW shapes; their score.py branches +
    # the ~86 model-var ports are staged (tasks 63c/63d) — until those land the
    # family is registered but DORMANT (KEE is not in the live poller).
    # A KEE card routes to KEE (April) or KEE_OCT (October>=Jul) by its own
    # date-month via register_track_seasonal — the poller passes race_date/card.
    register_family(
        "KEE_OCT",
        dirt_models={
            "core": "keedirt102026_core.sas7bdat",
            "c":    "keedirt102026c.sas7bdat",
            "n":    "keedirt102026n.sas7bdat",
            "s":    "keedirt102026s.sas7bdat",
            "r":    "keedirt102026r.sas7bdat",
        },
        dirt_ensemble=[
            ("core", "all"),
            ("c",    "claim"),
            ("n",    "nonclaim"),
            ("s",    "sprint"),
            ("r",    "route"),
        ],
        dirt_ny_model=None,          # KEE has no NY-restricted dirt core
        dirt_var_overrides={},       # Oct26 cells built on their own vars (no swap)
        turf_models={
            "core": "keeturf102026_core.sas7bdat",
            "s":    "keeturf102026s.sas7bdat",
            "r":    "keeturf102026r.sas7bdat",
            "g":    "keeturf102026g.sas7bdat",
        },
        maiden_models={
            "core": "kee_maid_core1026.sas7bdat",
            "M":    "kee_maid_1026_m.sas7bdat",
            "S":    "kee_maid_1026_s.sas7bdat",
            "ST":   "kee_maid_1026_st.sas7bdat",
            "SD":   "kee_maid_1026_sd.sas7bdat",
            "MSp":  "kee_maid_1026_msp.sas7bdat",
            "MRt":  "kee_maid_1026_mrt.sas7bdat",
        },
        score_weights=config.SCORE_WEIGHTS,
        coeff_dir=Path(config.COEFF_DIR),
    )
    register_track_seasonal("KEE", {"apr": "KEE", "oct": "KEE_OCT"})

    # ── 2. Future per-track families (currently all stubbed) ──────────────
    # When you have models for a new track, replace the empty dicts below
    # with real filenames, drop the .sas7bdat files into the matching
    # subdirectory under config.COEFF_DIR, and uncomment the block.

    # ── Saratoga (NYRA summer meet) — DIRT v1 (2026) ──────────────────────
    # Dirt scores on the SAR family: a [core + claim/nonclaim + sprint/route]
    # ensemble, with NY-bred-restricted races routed to the core_ny model alone
    # (see score._score_dirt / config.DIRT_ENSEMBLE / DIRT_NY_MODEL).
    # Turf & maiden are NOT built yet -> they fall back to the KEE universal
    # models, so SAR turf/maiden races still score (on KEE) until those SAR
    # families are added.  All coefficient files (SAR dirt + KEE turf/maiden)
    # live in COEFF_DIR root.
    register_family(
        "SAR",
        dirt_models={
            "core":    "sardirt2026_core.sas7bdat",
            "core_ny": "sardirt2026_core_ny.sas7bdat",
            "c":       "sardirt2026c.sas7bdat",
            "n":       "sardirt2026n.sas7bdat",
            "s":       "sardirt2026s.sas7bdat",
            "r":       "sardirt2026r.sas7bdat",
        },
        dirt_ensemble=[
            ("core", "all"),
            ("c",    "claim"),
            ("n",    "nonclaim"),
            ("s",    "sprint"),
            ("r",    "route"),
        ],
        dirt_ny_model="core_ny",
        # SAR models were built on the plain trnwcm_sart (general meet-std),
        # so skip the KEE dirt-std swap.
        dirt_var_overrides={},
        # ── Turf: SAR v8 hierarchy (course x distance x class) ────────────
        # 15 used cells (the Mellon-fix v8 build: NO outer-course 'o' cells —
        # Mellon horses fall to the pooled cells). + coreNY / NYr for NY-bred
        # turf races. Coefficient files are the PROC LOGISTIC outest= datasets
        # written by SAR_TURF_HIER_v8_FINAL.sas to SAS_DATA (SAS lowercases the
        # physical .sas7bdat names) — copy them into COEFF_DIR before a run.
        turf_models={
            "core":    "sar_turf_2026_core.sas7bdat",
            "c_i":     "sar_turf_2026_c_i.sas7bdat",
            "d_sp":    "sar_turf_2026_d_sp.sas7bdat",
            "d_rt":    "sar_turf_2026_d_rt.sas7bdat",
            "k_cl":    "sar_turf_2026_k_cl.sas7bdat",
            "k_nc":    "sar_turf_2026_k_nc.sas7bdat",
            "tD_i_rt": "sar_turf_2026_td_i_rt.sas7bdat",
            "tK_i_cl": "sar_turf_2026_tk_i_cl.sas7bdat",
            "tK_i_nc": "sar_turf_2026_tk_i_nc.sas7bdat",
            "dK_sp_cl":"sar_turf_2026_dk_sp_cl.sas7bdat",
            "dK_sp_nc":"sar_turf_2026_dk_sp_nc.sas7bdat",
            "dK_rt_cl":"sar_turf_2026_dk_rt_cl.sas7bdat",
            "dK_rt_nc":"sar_turf_2026_dk_rt_nc.sas7bdat",
            "x_i_rt_cl":"sar_turf_2026_x_i_rt_cl.sas7bdat",
            "x_i_rt_nc":"sar_turf_2026_x_i_rt_nc.sas7bdat",
            # NY-bred turf models
            "coreNY":  "sar_turf_core2026ny.sas7bdat",
            "NYr":     "sar_turf_2026nyr.sas7bdat",
        },
        # (model_key, course, dist, cls): course 'i'=inner('t')/'o'=Mellon('T'),
        # dist 'sp'/'rt', cls 'cl'/'nc'. No 'o' cells => Mellon fix.
        turf_ensemble=[
            ("core",     None, None, None),
            ("c_i",      "i",  None, None),
            ("d_sp",     None, "sp", None),
            ("d_rt",     None, "rt", None),
            ("k_cl",     None, None, "cl"),
            ("k_nc",     None, None, "nc"),
            ("tD_i_rt",  "i",  "rt", None),
            ("tK_i_cl",  "i",  None, "cl"),
            ("tK_i_nc",  "i",  None, "nc"),
            ("dK_sp_cl", None, "sp", "cl"),
            ("dK_sp_nc", None, "sp", "nc"),
            ("dK_rt_cl", None, "rt", "cl"),
            ("dK_rt_nc", None, "rt", "nc"),
            ("x_i_rt_cl","i",  "rt", "cl"),
            ("x_i_rt_nc","i",  "rt", "nc"),
        ],
        turf_ny_model="coreNY",
        turf_ny_route_model="NYr",
        # ── Maiden: SAR 3-suite blend (0.5 leaf + 0.25 rt*surf + 0.25 rt*dist) ──
        # 32 cells = (racetype S/M) x (dist/surf) x (NY 0/1), mirroring
        # BTSM_SAR_MadienModel_2026.sas. Each entry = (coeff_file, suite,
        # racetype, dist{'sp','rt',None}, surf{'T','D',None}, ny). maiden_models
        # stays as the KEE dict for the loader's file-count log; the ensemble
        # below is what _score_maiden_sar actually consumes.
        maiden_models=dict(config.MAIDEN_MODELS),
        maiden_ensemble=[
            # suite 1 — leaf: racetype x dist x surface (open, then NY)
            ("sar_maid_2026_spst.sas7bdat", 1, "S", "sp", "T", 0),
            ("sar_maid_2026_spsd.sas7bdat", 1, "S", "sp", "D", 0),
            ("sar_maid_2026_sprt.sas7bdat", 1, "S", "rt", "T", 0),
            ("sar_maid_2026_sprd.sas7bdat", 1, "S", "rt", "D", 0),
            ("sar_maid_2026_mst.sas7bdat",  1, "M", "sp", "T", 0),
            ("sar_maid_2026_msd.sas7bdat",  1, "M", "sp", "D", 0),
            ("sar_maid_2026_mrt.sas7bdat",  1, "M", "rt", "T", 0),
            ("sar_maid_2026_mrd.sas7bdat",  1, "M", "rt", "D", 0),
            ("sar_maid_2026_spstny.sas7bdat", 1, "S", "sp", "T", 1),
            ("sar_maid_2026_spsdny.sas7bdat", 1, "S", "sp", "D", 1),
            ("sar_maid_2026_sprtny.sas7bdat", 1, "S", "rt", "T", 1),
            ("sar_maid_2026_sprdny.sas7bdat", 1, "S", "rt", "D", 1),
            ("sar_maid_2026_mstny.sas7bdat",  1, "M", "sp", "T", 1),
            ("sar_maid_2026_msdny.sas7bdat",  1, "M", "sp", "D", 1),
            ("sar_maid_2026_mrtny.sas7bdat",  1, "M", "rt", "T", 1),
            ("sar_maid_2026_mrdny.sas7bdat",  1, "M", "rt", "D", 1),
            # suite 2 — racetype x surface (pooled over distance)
            ("sar_maid_2026_spt.sas7bdat", 2, "S", None, "T", 0),
            ("sar_maid_2026_spd.sas7bdat", 2, "S", None, "D", 0),
            ("sar_maid_2026_mt.sas7bdat",  2, "M", None, "T", 0),
            ("sar_maid_2026_md.sas7bdat",  2, "M", None, "D", 0),
            ("sar_maid_2026_sptny.sas7bdat", 2, "S", None, "T", 1),
            ("sar_maid_2026_spdny.sas7bdat", 2, "S", None, "D", 1),
            ("sar_maid_2026_mtny.sas7bdat",  2, "M", None, "T", 1),
            ("sar_maid_2026_mdny.sas7bdat",  2, "M", None, "D", 1),
            # suite 3 — racetype x distance (pooled over surface)
            ("sar_maid_2026_sps.sas7bdat", 3, "S", "sp", None, 0),
            ("sar_maid_2026_spr.sas7bdat", 3, "S", "rt", None, 0),
            ("sar_maid_2026_ms.sas7bdat",  3, "M", "sp", None, 0),
            ("sar_maid_2026_mr.sas7bdat",  3, "M", "rt", None, 0),
            ("sar_maid_2026_spsny.sas7bdat", 3, "S", "sp", None, 1),
            ("sar_maid_2026_sprny.sas7bdat", 3, "S", "rt", None, 1),
            ("sar_maid_2026_msny.sas7bdat",  3, "M", "sp", None, 1),
            ("sar_maid_2026_mrny.sas7bdat",  3, "M", "rt", None, 1),
        ],
        score_weights=dict(config.SCORE_WEIGHTS),
        coeff_dir=Path(config.COEFF_DIR),
    )
    register_track("SAR", "SAR")

    # ── Churchill Downs ───────────────────────────────────────────────────
    # register_family(
    #     "CD",
    #     dirt_models={...},
    #     turf_models={...},
    #     maiden_models={...},
    #     score_weights={"score1": 0.50, "score2": 0.25, "score3": 0.25},
    #     coeff_dir=Path(config.COEFF_DIR) / "CD",
    # )
    # register_track("CD",  "CD")
    # register_track("CDX", "CD")   # BRIS code variant -> same family

    # ── Del Mar (dirt + TURF + MAIDEN = own config-F families) ──
    register_family(
        "DMR",
        dirt_models={
            "core": "dmrdirt2026_core.sas7bdat",
            "c":    "dmrdirt2026c.sas7bdat",
            "n":    "dmrdirt2026n.sas7bdat",
            "s":    "dmrdirt2026s.sas7bdat",
            "r":    "dmrdirt2026r.sas7bdat",
            "ss":   "dmrdirt2026_ss.sas7bdat",
            "sn":   "dmrdirt2026_sn.sas7bdat",
            "rc":   "dmrdirt2026_rc.sas7bdat",
            "rn":   "dmrdirt2026_rn.sas7bdat",
        },
        dirt_var_overrides={},                      # DMR built on plain trnwcm_sart
        # ── Turf: DMR config-F (cell + 2 parents, no core). score._score_turf_dmr
        # reads the 8 coef_dmr_turf_2026*.csv from coeff_dir; validated bit-exact
        # vs Scoring_DMR_2026.sas on 09/04-09/05. turf_models stays as the KEE dict
        # for the loader's file-count log only — it is NOT used when TURF_CONFIGF.
        turf_models=dict(config.TURF_MODELS),
        turf_configf=True,
        # ── Maiden: DMR config-F (cell + 3 parents, no core). score._score_maiden_dmr
        # reads the 14 coef_maid_*.csv from coeff_dir. maiden_models stays as the
        # KEE dict for the loader's file-count log only — NOT used when MAIDEN_CONFIGF.
        maiden_models=dict(config.MAIDEN_MODELS),
        maiden_configf=True,
        score_weights=dict(config.SCORE_WEIGHTS),
        coeff_dir=Path(config.COEFF_DIR),
    )
    register_track("DMR", "DMR")

    # ── Gulfstream Park ───────────────────────────────────────────────────
    # register_family("GP", dirt_models={...}, turf_models={...},
    #                 maiden_models={...},
    #                 score_weights={"score1": 0.50, "score2": 0.25, "score3": 0.25},
    #                 coeff_dir=Path(config.COEFF_DIR) / "GP")
    # register_track("GP",  "GP")
    # register_track("GPX", "GP")

    # ──────────────────────────────────────────────────────────────────────
    state = list_registered()
    logger.info(
        "Model registry ready: %d families (%s), default=%s, "
        "%d per-track overrides",
        len(state["families"]), ", ".join(state["families"]),
        state["default"], len(state["track_overrides"]),
    )


# ---------------------------------------------------------------------------
# CLI: print the registry state for diagnostics
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    import config
    setup_registry(config)
    print()
    state = list_registered()
    print(f"  Default family:       {state['default']}")
    print(f"  Registered families:  {state['families']}")
    print(f"  Per-track overrides:  {state['track_overrides']}")
    print()
    print("  All other tracks fall back to the default family.")
