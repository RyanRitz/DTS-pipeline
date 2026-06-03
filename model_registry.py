"""
DTS Automation Pipeline — Model Registry
============================================
Maps tracks to scoring model families.  As you build new model families
(Saratoga, Churchill, Del Mar, etc.) register them here.  Tracks without
a dedicated family fall back to the default ("KEE" today).

The registry lets the orchestrator score every DRF in the queue using the
right model family, while config.py stays the single source of truth for
the actual coefficient filenames within each family.

Design:
    - Each family is a dict of {DIRT_MODELS, TURF_MODELS, MAIDEN_MODELS,
      SCORE_WEIGHTS, COEFF_DIR}.
    - The registry is a track -> family-name lookup.
    - DEFAULT_FAMILY catches anything not explicitly registered.
    - get_scoring_models(track) returns a "scoring config" object compatible
      with score.run_scoring(): an object that exposes the same attributes
      as config.py but pointing at the right family's coefficients.

Public API:
    register_family(name, *, dirt, turf, maiden, weights, coeff_dir)
    register_track(track, family_name)
    get_scoring_models(track) -> ScoringConfig
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Family + per-track config wrapper
# ---------------------------------------------------------------------------
@dataclass
class _Family:
    """One model family: KEE, future SAR, future CD, etc."""
    name: str
    dirt_models:   dict
    turf_models:   dict
    maiden_models: dict
    score_weights: dict
    coeff_dir:     Path


@dataclass
class ScoringConfig:
    """
    A drop-in stand-in for the `config` module that score.run_scoring()
    expects. It exposes DIRT_MODELS, TURF_MODELS, MAIDEN_MODELS,
    SCORE_WEIGHTS, COEFF_DIR — all the attributes score.py reads.

    Other config attributes the user code references (TRACK, RACE_DATE,
    YEAR, OUTPUT_DIR, etc.) are pass-through from the underlying real
    config module. We don't override TRACK; the original stays intact.
    """
    family_name: str
    DIRT_MODELS:   dict
    TURF_MODELS:   dict
    MAIDEN_MODELS: dict
    SCORE_WEIGHTS: dict
    COEFF_DIR:     Path

    # Reference to the underlying real config module for pass-through access
    _underlying: Any = None

    def __getattr__(self, name: str):
        """Pass through unknown attributes to the underlying config module."""
        # __getattr__ only runs if the attribute isn't found via normal lookup,
        # so DIRT_MODELS et al. are returned directly without going through here.
        if self._underlying is not None and hasattr(self._underlying, name):
            return getattr(self._underlying, name)
        raise AttributeError(
            f"ScoringConfig (family={self.family_name!r}) has no attribute {name!r}"
        )


# ---------------------------------------------------------------------------
# Registry state (module-level)
# ---------------------------------------------------------------------------
_FAMILIES: dict[str, _Family] = {}
_TRACK_TO_FAMILY: dict[str, str] = {}
_DEFAULT_FAMILY: Optional[str] = None


def register_family(
    name: str,
    *,
    dirt_models: dict,
    turf_models: dict,
    maiden_models: dict,
    score_weights: dict,
    coeff_dir,
    set_as_default: bool = False,
) -> None:
    """
    Register a model family.

    Parameters
    ----------
    name : str
        Identifier (e.g. "KEE", "SAR", "CD").  Case-insensitive.
    dirt_models, turf_models, maiden_models : dict
        Same shape as config.DIRT_MODELS et al.
        e.g. {"c": "keedirt042026c.sas7bdat", "n": "keedirt042026n.sas7bdat", ...}
    score_weights : dict
        Same shape as config.SCORE_WEIGHTS.
    coeff_dir : Path or str
        Directory containing the .sas7bdat files for this family.
    set_as_default : bool
        If True, this family becomes the fallback for unregistered tracks.
    """
    global _DEFAULT_FAMILY
    name = name.upper()
    _FAMILIES[name] = _Family(
        name=name,
        dirt_models=dict(dirt_models),
        turf_models=dict(turf_models),
        maiden_models=dict(maiden_models),
        score_weights=dict(score_weights),
        coeff_dir=Path(coeff_dir),
    )
    if set_as_default or _DEFAULT_FAMILY is None:
        _DEFAULT_FAMILY = name
    logger.debug("Registered model family %r (default=%s)", name, set_as_default)


def register_track(track: str, family_name: str) -> None:
    """Map a track code to a model family."""
    family_name = family_name.upper()
    if family_name not in _FAMILIES:
        raise ValueError(
            f"Cannot map track {track!r} to unknown family {family_name!r}. "
            f"Known families: {sorted(_FAMILIES)}"
        )
    _TRACK_TO_FAMILY[track.upper()] = family_name


def get_family_for_track(track: str) -> str:
    """Return the family name that should score this track."""
    fam = _TRACK_TO_FAMILY.get(track.upper())
    if fam:
        return fam
    if _DEFAULT_FAMILY is None:
        raise RuntimeError(
            "No default model family registered. "
            "Call register_family(..., set_as_default=True) first."
        )
    logger.info(
        "Track %r not in registry; using default family %r",
        track.upper(), _DEFAULT_FAMILY,
    )
    return _DEFAULT_FAMILY


def get_scoring_models(track: str, underlying_config: Any) -> ScoringConfig:
    """
    Get a ScoringConfig wrapper for the given track, with model attributes
    pointed at the right family. Pass-through access falls back to
    `underlying_config` (your real config.py module).
    """
    family_name = get_family_for_track(track)
    fam = _FAMILIES[family_name]
    return ScoringConfig(
        family_name=family_name,
        DIRT_MODELS=fam.dirt_models,
        TURF_MODELS=fam.turf_models,
        MAIDEN_MODELS=fam.maiden_models,
        SCORE_WEIGHTS=fam.score_weights,
        COEFF_DIR=fam.coeff_dir,
        _underlying=underlying_config,
    )


def list_registered() -> dict:
    """Return a snapshot of the current registry state, for diagnostics."""
    return {
        "families": list(_FAMILIES),
        "default": _DEFAULT_FAMILY,
        "track_overrides": dict(_TRACK_TO_FAMILY),
    }


# ---------------------------------------------------------------------------
# Bootstrap from config.py
# ---------------------------------------------------------------------------
# Most of the time you'll just call this once at startup and forget about it.
# It seeds the registry with whatever's currently in your config.py: a single
# "KEE" family containing the active dirt/turf/maiden dicts. As you build new
# families, add register_family() calls here OR in a separate model_setup.py.

def bootstrap_from_config(config_module, *, default_family: str = "KEE") -> None:
    """
    Seed the registry with the current config.py settings as a single family.
    Idempotent: safe to call multiple times.
    """
    if default_family.upper() in _FAMILIES:
        return  # already bootstrapped

    # Pull the dicts off config.py — these are the names ScoringConfig
    # exposes back to score.py.
    dirt   = getattr(config_module, "DIRT_MODELS",   {})
    turf   = getattr(config_module, "TURF_MODELS",   {})
    maiden = getattr(config_module, "MAIDEN_MODELS", {})
    weights = getattr(config_module, "SCORE_WEIGHTS", {})
    coeff_dir = getattr(config_module, "COEFF_DIR", Path("."))

    register_family(
        default_family,
        dirt_models=dirt,
        turf_models=turf,
        maiden_models=maiden,
        score_weights=weights,
        coeff_dir=coeff_dir,
        set_as_default=True,
    )
    logger.info(
        "Bootstrapped model registry from config: family=%r, "
        "%d dirt + %d turf + %d maiden models, coeff_dir=%s",
        default_family.upper(), len(dirt), len(turf), len(maiden), coeff_dir,
    )


# ---------------------------------------------------------------------------
# CLI / smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    p = argparse.ArgumentParser(description="Model registry diagnostic")
    p.add_argument("--track", help="Track to look up", default=None)
    args = p.parse_args()

    import config
    bootstrap_from_config(config)

    print(f"\nRegistry:")
    for k, v in list_registered().items():
        print(f"  {k}: {v}")

    if args.track:
        sc = get_scoring_models(args.track, config)
        print(f"\nScoring config for {args.track!r}:")
        print(f"  family:        {sc.family_name}")
        print(f"  COEFF_DIR:     {sc.COEFF_DIR}")
        print(f"  DIRT_MODELS:   {len(sc.DIRT_MODELS)} models")
        print(f"  TURF_MODELS:   {len(sc.TURF_MODELS)} models")
        print(f"  MAIDEN_MODELS: {len(sc.MAIDEN_MODELS)} models")
        print(f"  SCORE_WEIGHTS: {sc.SCORE_WEIGHTS}")
        # Pass-through demo
        print(f"  TRACK (passthrough): {sc.TRACK}")
        print(f"  YEAR  (passthrough): {sc.YEAR}")
