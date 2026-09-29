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
    # Optional family-specific dirt ensemble composition.
    #   dirt_ensemble : list of (model_key, filter_name) — see score._score_dirt.
    #                   None => legacy [c,n,s,r] blend (no core).
    #   dirt_ny_model : model_key scoring NY-bred-restricted races alone.
    dirt_ensemble: Optional[list] = None
    dirt_ny_model: Optional[str] = None
    # Dirt-scoring variable swaps. None => legacy KEE swap; {} => none (SAR).
    dirt_var_overrides: Optional[dict] = None
    # Optional family-specific TURF ensemble (the SAR hierarchy). None => legacy
    # KEE 4-model (s/r/hp/lp) turf blend — KEE scores exactly as before.
    #   turf_ensemble : list of (model_key, course, dist, cls) cells consumed by
    #                   score._score_turf. course in {'i','o',None} keyed off the
    #                   Surface case ('t'=inner, 'T'=Mellon); dist in
    #                   {'sp','rt',None}; cls in {'cl','nc',None}. The Mellon fix
    #                   is expressed simply by NOT listing any 'o' cells.
    #   turf_ny_model       : model_key scoring NY-bred turf races (all).
    #   turf_ny_route_model : model_key scoring NY-bred turf ROUTES, averaged
    #                         with turf_ny_model for NY routes (NY sprints score
    #                         on turf_ny_model alone — matches the SAS).
    turf_ensemble: Optional[list] = None
    turf_ny_model: Optional[str] = None
    turf_ny_route_model: Optional[str] = None
    # DMR config-F turf: True => score turf non-maiden via score._score_turf_dmr
    # (cell + 2 parents, coef_dmr_turf_2026*.csv in coeff_dir). None/False =>
    # the family's normal turf path (KEE legacy or SAR hierarchy). DMR only.
    turf_configf: Optional[bool] = None

    #   maiden_ensemble : list of maiden cells for the SAR 3-suite blend. Each
    #                     entry = (coeff_file, suite, racetype, dist, surf, ny):
    #                     suite in {1,2,3} (0.5/0.25/0.25), racetype in {'S','M'},
    #                     dist in {'sp','rt',None}, surf in {'T','D',None}, ny 0/1.
    #                     None => legacy KEE maiden blend.
    maiden_ensemble: Optional[list] = None
    # DMR config-F maiden: True => score maidens via score._score_maiden_dmr
    # (cell + 3 parents, coef_maid_*.csv in coeff_dir). None/False => the
    # family's normal maiden path (KEE legacy or SAR ensemble). DMR only.
    maiden_configf: Optional[bool] = None


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

    # Family-specific dirt ensemble composition (None => legacy c/n/s/r blend)
    DIRT_ENSEMBLE: Optional[list] = None
    DIRT_NY_MODEL: Optional[str] = None
    DIRT_VAR_OVERRIDES: Optional[dict] = None

    # Family-specific turf ensemble (None => legacy KEE s/r/hp/lp blend)
    TURF_ENSEMBLE: Optional[list] = None
    TURF_NY_MODEL: Optional[str] = None
    TURF_NY_ROUTE_MODEL: Optional[str] = None
    # DMR config-F turf flag (read by score._score_turf and attribution)
    TURF_CONFIGF: Optional[bool] = None

    # Family-specific maiden ensemble (None => legacy KEE maiden blend)
    MAIDEN_ENSEMBLE: Optional[list] = None
    # DMR config-F maiden flag (read by score._score_maiden)
    MAIDEN_CONFIGF: Optional[bool] = None

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
# track -> {season: family}, where season is 'apr'/'oct' (see _season_for_date).
# Lets one track (KEE) route to different families by the CARD's date, so the
# Spring and Fall meets score as completely separate models. Takes precedence
# over _TRACK_TO_FAMILY when a race_date is supplied.
_TRACK_SEASONAL: dict[str, dict[str, str]] = {}
_DEFAULT_FAMILY: Optional[str] = None
# Season -> family for the DEFAULT fallback, so unmodeled tracks (Churchill,
# etc.) also follow the meet: Spring uses KEE, Fall uses KEE_OCT. Consulted
# only when a track has no explicit family/seasonal mapping AND a race_date is
# supplied; without a date it falls back to _DEFAULT_FAMILY.
_DEFAULT_SEASONAL: dict[str, str] = {}


def register_family(
    name: str,
    *,
    dirt_models: dict,
    turf_models: dict,
    maiden_models: dict,
    score_weights: dict,
    coeff_dir,
    set_as_default: bool = False,
    dirt_ensemble: Optional[list] = None,
    dirt_ny_model: Optional[str] = None,
    dirt_var_overrides: Optional[dict] = None,
    turf_ensemble: Optional[list] = None,
    turf_ny_model: Optional[str] = None,
    turf_ny_route_model: Optional[str] = None,
    turf_configf: Optional[bool] = None,
    maiden_ensemble: Optional[list] = None,
    maiden_configf: Optional[bool] = None,
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
        dirt_ensemble=dirt_ensemble,
        dirt_ny_model=dirt_ny_model,
        dirt_var_overrides=dirt_var_overrides,
        turf_ensemble=turf_ensemble,
        turf_ny_model=turf_ny_model,
        turf_ny_route_model=turf_ny_route_model,
        turf_configf=turf_configf,
        maiden_ensemble=maiden_ensemble,
        maiden_configf=maiden_configf,
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


def register_track_seasonal(track: str, season_map: dict) -> None:
    """Map a track to different families by SEASON, e.g.
        register_track_seasonal("KEE", {"apr": "KEE_APR", "oct": "KEE_OCT"})
    Each family in season_map must already be registered. When a race_date is
    passed to get_family_for_track / get_scoring_models, the season is resolved
    from the card's month (see _season_for_date) and the matching family is used;
    without a race_date it falls back to _TRACK_TO_FAMILY / the default."""
    track = track.upper()
    for fam in season_map.values():
        if fam.upper() not in _FAMILIES:
            raise ValueError(
                f"Cannot map track {track!r} season to unknown family {fam!r}. "
                f"Known families: {sorted(_FAMILIES)}"
            )
    _TRACK_SEASONAL[track] = {k.lower(): v.upper() for k, v in season_map.items()}


def register_default_seasonal(season_map: dict) -> None:
    """Make the DEFAULT fallback seasonal, e.g.
        register_default_seasonal({"apr": "KEE", "oct": "KEE_OCT"})
    Then any track with no explicit family/seasonal mapping follows the meet:
    Spring cards score on KEE, Fall cards on KEE_OCT. Each family must already
    be registered."""
    global _DEFAULT_SEASONAL
    for fam in season_map.values():
        if fam.upper() not in _FAMILIES:
            raise ValueError(
                f"Cannot set default season to unknown family {fam!r}. "
                f"Known families: {sorted(_FAMILIES)}"
            )
    _DEFAULT_SEASONAL = {k.lower(): v.upper() for k, v in season_map.items()}


def _season_for_date(race_date) -> Optional[str]:
    """Resolve a card date to a meet season: 'apr' (Spring, Jan-Jun) or
    'oct' (Fall, Jul-Dec). Accepts 'YYYYMMDD' or 'MMDD'. Returns None if the
    date can't be parsed (caller then uses the non-seasonal mapping)."""
    if not race_date:
        return None
    s = str(race_date).strip()
    try:
        mm = int(s[4:6]) if len(s) >= 6 else int(s[:2])  # YYYYMMDD else MMDD
    except (ValueError, IndexError):
        return None
    if not 1 <= mm <= 12:
        return None
    return "oct" if mm >= 7 else "apr"


def get_family_for_track(track: str, race_date=None) -> str:
    """Return the family name that should score this track.

    If the track has a seasonal mapping AND a race_date is supplied, the season
    (from the card's month) selects the family. Otherwise the plain track->family
    map (then the default) is used — identical to the pre-seasonal behavior."""
    track = track.upper()
    seasonal = _TRACK_SEASONAL.get(track)
    if seasonal:
        season = _season_for_date(race_date)
        if season and season in seasonal:
            return seasonal[season]
        # seasonal track but no/again-unmatched date: fall through to defaults
    fam = _TRACK_TO_FAMILY.get(track)
    if fam:
        return fam
    if _DEFAULT_FAMILY is None:
        raise RuntimeError(
            "No default model family registered. "
            "Call register_family(..., set_as_default=True) first."
        )
    # The default fallback may itself be seasonal (Spring=KEE, Fall=KEE_OCT), so
    # unmodeled tracks (Churchill, etc.) also follow the meet when a date is given.
    if _DEFAULT_SEASONAL:
        season = _season_for_date(race_date)
        if season and season in _DEFAULT_SEASONAL:
            logger.info(
                "Track %r not in registry; using seasonal default %r",
                track.upper(), _DEFAULT_SEASONAL[season],
            )
            return _DEFAULT_SEASONAL[season]
    logger.info(
        "Track %r not in registry; using default family %r",
        track.upper(), _DEFAULT_FAMILY,
    )
    return _DEFAULT_FAMILY


def get_scoring_models(track: str, underlying_config: Any, race_date=None) -> ScoringConfig:
    """
    Get a ScoringConfig wrapper for the given track, with model attributes
    pointed at the right family. Pass-through access falls back to
    `underlying_config` (your real config.py module).

    race_date (optional, 'YYYYMMDD' or 'MMDD') selects the seasonal family for
    tracks registered via register_track_seasonal (KEE Spring vs Fall). Omitting
    it preserves the original non-seasonal behavior.
    """
    family_name = get_family_for_track(track, race_date)
    fam = _FAMILIES[family_name]
    return ScoringConfig(
        family_name=family_name,
        DIRT_MODELS=fam.dirt_models,
        TURF_MODELS=fam.turf_models,
        MAIDEN_MODELS=fam.maiden_models,
        SCORE_WEIGHTS=fam.score_weights,
        COEFF_DIR=fam.coeff_dir,
        DIRT_ENSEMBLE=fam.dirt_ensemble,
        DIRT_NY_MODEL=fam.dirt_ny_model,
        DIRT_VAR_OVERRIDES=fam.dirt_var_overrides,
        TURF_ENSEMBLE=fam.turf_ensemble,
        TURF_NY_MODEL=fam.turf_ny_model,
        TURF_NY_ROUTE_MODEL=fam.turf_ny_route_model,
        TURF_CONFIGF=fam.turf_configf,
        MAIDEN_ENSEMBLE=fam.maiden_ensemble,
        MAIDEN_CONFIGF=fam.maiden_configf,
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
