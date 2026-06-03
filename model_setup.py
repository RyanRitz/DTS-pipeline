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
    list_registered,
)

logger = logging.getLogger(__name__)


def setup_registry(config) -> None:
    """
    Build the model registry. Called once at pipeline startup.
    Idempotent — safe to call multiple times in the same process.
    """
    # ── 1. Bootstrap KEE family from config.py (the active meet's models) ──
    bootstrap_from_config(config, default_family="KEE")

    # ── 2. Future per-track families (currently all stubbed) ──────────────
    # When you have models for a new track, replace the empty dicts below
    # with real filenames, drop the .sas7bdat files into the matching
    # subdirectory under config.COEFF_DIR, and uncomment the block.

    # ── Saratoga (NYRA summer meet) ───────────────────────────────────────
    # register_family(
    #     "SAR",
    #     dirt_models={
    #         "c": "sardirt072026c.sas7bdat",
    #         "n": "sardirt072026n.sas7bdat",
    #         "s": "sardirt072026s.sas7bdat",
    #         "r": "sardirt072026r.sas7bdat",
    #     },
    #     turf_models={
    #         "s":  "sarturf072026s.sas7bdat",
    #         "r":  "sarturf072026r.sas7bdat",
    #         "hp": "sarturf072026hp.sas7bdat",
    #         "lp": "sarturf072026lp.sas7bdat",
    #     },
    #     maiden_models={
    #         # 1: "sar_maid_0726_spst.sas7bdat",
    #         # ... fill in as built
    #     },
    #     score_weights={"score1": 0.50, "score2": 0.25, "score3": 0.25},
    #     coeff_dir=Path(config.COEFF_DIR) / "SAR",
    # )
    # register_track("SAR", "SAR")

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

    # ── Del Mar ───────────────────────────────────────────────────────────
    # register_family("DMR", dirt_models={...}, turf_models={...},
    #                 maiden_models={...},
    #                 score_weights={"score1": 0.50, "score2": 0.25, "score3": 0.25},
    #                 coeff_dir=Path(config.COEFF_DIR) / "DMR")
    # register_track("DMR", "DMR")

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
