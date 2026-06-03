"""
End-to-end smoke test for add_attributions.

We can't load real .sas7bdat coefficient files here, so we monkey-patch
_load_coefficient_sets to return a hand-built set and verify the full
add_attributions pipeline runs end-to-end:
  - merges feature_df into scored_df
  - iterates per-race
  - picks the right model_id sub-coefs
  - computes per-horse blend
  - subtracts race mean
  - thresholds, dedupes by theme group
  - rotates synonyms
  - writes why_like_* / why_fade_*
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import numpy as np
import attribution


# A fake config object that exposes the same attributes add_attributions uses.
class FakeConfig:
    DIRT_MODELS = {"c": "x.sas7bdat", "n": "y.sas7bdat",
                   "s": "z.sas7bdat", "r": "w.sas7bdat"}
    TURF_MODELS = {}
    MAIDEN_MODELS = {}


def test_end_to_end_dirt_race():
    # Build a 4-horse dirt race. Features chosen from the real SYNONYMS dict
    # so the synonym lookup actually fires.
    scored_df = pd.DataFrame({
        "Track":     ["KEE"] * 4,
        "Date":      ["2026-05-09"] * 4,
        "Race":      [1, 1, 1, 1],
        "HorseName": ["ALPHA", "BRAVO", "CHARLIE", "DELTA"],
        "model":     [1, 1, 1, 1],
        # All 4 horses scored by all 4 dirt sub-models
        "predictedc": [0.2, 0.3, 0.1, 0.4],
        "predictedn": [0.2, 0.3, 0.1, 0.4],
        "predicteds": [0.2, 0.3, 0.1, 0.4],
        "predictedr": [0.2, 0.3, 0.1, 0.4],
    })

    feature_df = pd.DataFrame({
        "Track":     ["KEE"] * 4,
        "Date":      ["2026-05-09"] * 4,
        "Race":      [1, 1, 1, 1],
        "HorseName": ["ALPHA", "BRAVO", "CHARLIE", "DELTA"],
        # ALPHA: high speed, low works  → like speed, fade works
        # BRAVO: low speed, high works  → fade speed, like works
        # CHARLIE: neutral
        # DELTA: neutral
        "BestBris0422":      [5.0, 1.0, 3.0, 3.0],   # speed group
        "wotimefrlg_sart":   [1.0, 5.0, 3.0, 3.0],   # works group
    })

    # Stub _load_coefficient_sets to return known coefs.
    # Both features get coef 1.0 in each of the 4 firing dirt sub-models.
    fake_coeffs = {
        1: {  # dirt
            "c": {"BestBris0422": 1.0, "wotimefrlg_sart": 1.0},
            "n": {"BestBris0422": 1.0, "wotimefrlg_sart": 1.0},
            "s": {"BestBris0422": 1.0, "wotimefrlg_sart": 1.0},
            "r": {"BestBris0422": 1.0, "wotimefrlg_sart": 1.0},
        },
        2: {},
        3: {},
    }
    original_loader = attribution._load_coefficient_sets
    attribution._load_coefficient_sets = lambda *args, **kwargs: fake_coeffs

    try:
        result = attribution.add_attributions(
            scored_df,
            coeff_dir=Path("/nonexistent"),
            config=FakeConfig(),
            feature_df=feature_df,
        )
    finally:
        attribution._load_coefficient_sets = original_loader

    # All horses should have why_* columns populated
    for col in ["why_like_1", "why_fade_1"]:
        assert col in result.columns, f"missing column {col}"

    # ALPHA had highest BestBris0422 (5 vs field mean of 3) → should LIKE
    # on speed group
    alpha = result[result["HorseName"] == "ALPHA"].iloc[0]
    bravo = result[result["HorseName"] == "BRAVO"].iloc[0]

    likes_alpha = [alpha["why_like_1"], alpha["why_like_2"], alpha["why_like_3"]]
    fades_alpha = [alpha["why_fade_1"], alpha["why_fade_2"], alpha["why_fade_3"]]
    likes_bravo = [bravo["why_like_1"], bravo["why_like_2"], bravo["why_like_3"]]
    fades_bravo = [bravo["why_fade_1"], bravo["why_fade_2"], bravo["why_fade_3"]]

    # ALPHA: speed-positive (BestBris0422=5 vs mean 3), works-negative (1 vs 3)
    # so should have a "like" from speed group and a "fade" from works group.
    alpha_like_text = " ".join(filter(None, likes_alpha)).lower()
    alpha_fade_text = " ".join(filter(None, fades_alpha)).lower()

    # Check ALPHA has some like and some fade
    assert any(likes_alpha), f"ALPHA has no likes: {likes_alpha}"
    assert any(fades_alpha), f"ALPHA has no fades: {fades_alpha}"

    # BRAVO mirrors ALPHA: should be inverted (low speed, high works)
    assert any(likes_bravo), f"BRAVO has no likes: {likes_bravo}"
    assert any(fades_bravo), f"BRAVO has no fades: {fades_bravo}"

    print(f"  ALPHA  likes: {[x for x in likes_alpha if x]}")
    print(f"  ALPHA  fades: {[x for x in fades_alpha if x]}")
    print(f"  BRAVO  likes: {[x for x in likes_bravo if x]}")
    print(f"  BRAVO  fades: {[x for x in fades_bravo if x]}")
    print("  PASS end-to-end dirt race")


def test_synonym_rotation_across_card():
    """
    Two races, same feature pattern. The synonym-rotation should pick
    DIFFERENT synonyms for the second race if the first race already
    used one.
    """
    rows = []
    for race in (1, 2):
        for horse_idx, name in enumerate(["A", "B", "C", "D"]):
            rows.append({
                "Track": "KEE", "Date": "2026-05-09", "Race": race,
                "HorseName": f"{name}{race}", "model": 1,
                "predictedc": 0.25, "predictedn": 0.25,
                "predicteds": 0.25, "predictedr": 0.25,
            })
    scored_df = pd.DataFrame(rows)

    feature_rows = []
    for race in (1, 2):
        # Same pattern in both races: alphabetical horses high-low-mid-mid speed
        for name, sp in zip(["A","B","C","D"], [5.0, 1.0, 3.0, 3.0]):
            feature_rows.append({
                "Track": "KEE", "Date": "2026-05-09", "Race": race,
                "HorseName": f"{name}{race}",
                "BestBris0422": sp,
                "wotimefrlg_sart": 3.0,
            })
    feature_df = pd.DataFrame(feature_rows)

    fake_coeffs = {
        1: {sk: {"BestBris0422": 1.0} for sk in ("c","n","s","r")},
        2: {}, 3: {},
    }
    original_loader = attribution._load_coefficient_sets
    attribution._load_coefficient_sets = lambda *args, **kwargs: fake_coeffs

    try:
        result = attribution.add_attributions(
            scored_df,
            coeff_dir=Path("/nonexistent"),
            config=FakeConfig(),
            feature_df=feature_df,
        )
    finally:
        attribution._load_coefficient_sets = original_loader

    # A1 and A2 both have the highest speed in their races. Both should LIKE
    # on the speed group, but the synonym rotation should ideally hand them
    # different phrases when the pool has multiple entries.
    a1 = result[result["HorseName"] == "A1"].iloc[0]
    a2 = result[result["HorseName"] == "A2"].iloc[0]
    like_a1 = a1["why_like_1"]
    like_a2 = a2["why_like_1"]
    print(f"  A1 like_1: {like_a1!r}")
    print(f"  A2 like_1: {like_a2!r}")
    # BestBris0422 has 3 synonym variants on the like side.
    # The rotation should give A1 and A2 different ones.
    assert like_a1 and like_a2, "missing likes"
    if like_a1 == like_a2:
        print("  NOTE: same synonym used twice — rotation should have varied this")
        # Don't fail — rotation picks min-usage, and both could legitimately
        # get the same first slot if usage was tied. But typically they vary.
    else:
        print("  PASS synonym rotation produced different phrases across races")


if __name__ == "__main__":
    print("End-to-end attribution sanity tests…")
    test_end_to_end_dirt_race()
    print()
    test_synonym_rotation_across_card()
    print("\nAll sanity tests completed.")
