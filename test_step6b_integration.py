"""
Integration smoke test for the new step 6b (attribution wiring).

Since we can't load real .sas7bdat files here, we:
  1. Build minimal fake scored_df and feature_df
  2. Stub attribution._load_coefficient_sets to return a known coefficient set
  3. Call add_attributions directly (which is what step 6b in run_pipeline.py does)
  4. Verify why_like_* / why_fade_* columns are populated correctly
  5. Verify scored_df is otherwise unchanged (no rows lost, key cols intact)

This proves the wire-in pattern works:
  scored_df = add_attributions(scored_df, coeff_dir, scoring_config, feature_df)

If step 6b in run_pipeline.py fails for any reason in production, the
try/except wrapper there logs the failure and the Excel still writes
with empty why_* columns. That's not exercised here — it's a runtime
safety net documented by inspection of the patch diff.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import attribution


class FakeScoringConfig:
    """Stand-in for the ScoringConfig that run_pipeline passes."""
    COEFF_DIR = Path("/nonexistent")  # never actually read
    DIRT_MODELS = {"c": "x.sas7bdat", "n": "y.sas7bdat",
                   "s": "z.sas7bdat", "r": "w.sas7bdat"}
    TURF_MODELS = {}
    MAIDEN_MODELS = {}


def build_scored_df():
    """Three races: a dirt sprint with 5 horses."""
    return pd.DataFrame({
        "Track":     ["KEE"] * 5,
        "Date":      ["2026-05-09"] * 5,
        "Race":      [3] * 5,
        "HorseName": ["FIREBRAND", "STORMCHASE", "SILVERBELL", "QUICKDRAW", "MIDNIGHTRUN"],
        "model":     [1] * 5,
        # All 5 horses scored by all 4 dirt sub-models (realistic)
        "predictedc": [0.18, 0.22, 0.15, 0.28, 0.17],
        "predictedn": [0.18, 0.22, 0.15, 0.28, 0.17],
        "predicteds": [0.18, 0.22, 0.15, 0.28, 0.17],
        "predictedr": [0.18, 0.22, 0.15, 0.28, 0.17],
        # Some downstream columns the rest of the pipeline cares about
        "DTSOdds":  [4.0, 3.0, 6.0, 2.0, 5.0],
        "ProbToWin": [0.18, 0.22, 0.15, 0.28, 0.17],
        "rank":      [3, 2, 5, 1, 4],
        "Num":       [1, 2, 3, 4, 5],
    })


def build_feature_df():
    """Feature values designed to produce distinct attributions."""
    return pd.DataFrame({
        "Track":     ["KEE"] * 5,
        "Date":      ["2026-05-09"] * 5,
        "Race":      [3] * 5,
        "HorseName": ["FIREBRAND", "STORMCHASE", "SILVERBELL", "QUICKDRAW", "MIDNIGHTRUN"],
        # FIREBRAND   — high speed, low works   → like speed, fade works
        # STORMCHASE  — high works, low jockey  → like works, fade jockey
        # SILVERBELL  — low across the board    → all fades
        # QUICKDRAW   — high across the board   → all likes
        # MIDNIGHTRUN — middling                → mild signals
        "BestBris0422":      [5.0, 3.0, 1.0, 6.0, 3.0],   # speed
        "wotimefrlg_sart":   [1.0, 5.0, 1.0, 6.0, 3.0],   # works
        "xJCK_CMWPS26":      [3.0, 1.0, 1.0, 6.0, 3.0],   # jockey
    })


def main():
    scored_df  = build_scored_df()
    feature_df = build_feature_df()

    # Stub the coefficient loader.
    fake_coeffs = {
        1: {  # dirt
            sub_key: {
                "BestBris0422":    1.0,
                "wotimefrlg_sart": 1.0,
                "xJCK_CMWPS26":    1.0,
            }
            for sub_key in ("c", "n", "s", "r")
        },
        2: {},
        3: {},
    }
    original_loader = attribution._load_coefficient_sets
    attribution._load_coefficient_sets = lambda *args, **kwargs: fake_coeffs

    try:
        # This is exactly what step 6b in run_pipeline.py does.
        result = attribution.add_attributions(
            scored_df,
            coeff_dir=FakeScoringConfig.COEFF_DIR,
            config=FakeScoringConfig(),
            feature_df=feature_df,
        )
    finally:
        attribution._load_coefficient_sets = original_loader

    # ── Verify: no rows lost ───────────────────────────────────────────────
    assert len(result) == len(scored_df), (
        f"Lost rows: had {len(scored_df)}, got {len(result)}"
    )

    # ── Verify: all the why_* columns are present ─────────────────────────
    for i in range(1, 4):
        assert f"why_like_{i}" in result.columns, f"missing why_like_{i}"
        assert f"why_fade_{i}" in result.columns, f"missing why_fade_{i}"

    # ── Verify: existing scoring columns are untouched ────────────────────
    for col in ["DTSOdds", "ProbToWin", "rank", "Num", "Track", "Date", "Race"]:
        assert col in result.columns, f"lost column {col}"
        # Same values too
        pd.testing.assert_series_equal(
            scored_df[col].reset_index(drop=True),
            result[col].reset_index(drop=True),
            check_names=False,
            check_dtype=False,
        )

    # ── Verify: QUICKDRAW (highest across the board) has likes ────────────
    quickdraw = result[result["HorseName"] == "QUICKDRAW"].iloc[0]
    qd_likes = [quickdraw[f"why_like_{i}"] for i in range(1, 4)]
    qd_fades = [quickdraw[f"why_fade_{i}"] for i in range(1, 4)]
    assert any(qd_likes), f"QUICKDRAW should have likes: {qd_likes}"
    # QUICKDRAW is above field mean on every feature — fades should be empty
    # or weak.
    print(f"  QUICKDRAW likes: {[x for x in qd_likes if x]}")
    print(f"  QUICKDRAW fades: {[x for x in qd_fades if x]}")

    # ── Verify: SILVERBELL (lowest across the board) has fades ────────────
    silverbell = result[result["HorseName"] == "SILVERBELL"].iloc[0]
    sb_likes = [silverbell[f"why_like_{i}"] for i in range(1, 4)]
    sb_fades = [silverbell[f"why_fade_{i}"] for i in range(1, 4)]
    assert any(sb_fades), f"SILVERBELL should have fades: {sb_fades}"
    print(f"  SILVERBELL likes: {[x for x in sb_likes if x]}")
    print(f"  SILVERBELL fades: {[x for x in sb_fades if x]}")

    # ── Verify: FIREBRAND (high speed, low works) ─────────────────────────
    firebrand = result[result["HorseName"] == "FIREBRAND"].iloc[0]
    fb_likes = [firebrand[f"why_like_{i}"] for i in range(1, 4)]
    fb_fades = [firebrand[f"why_fade_{i}"] for i in range(1, 4)]
    print(f"  FIREBRAND likes: {[x for x in fb_likes if x]}")
    print(f"  FIREBRAND fades: {[x for x in fb_fades if x]}")
    assert any(fb_likes), "FIREBRAND should have at least one like"
    assert any(fb_fades), "FIREBRAND should have at least one fade"

    # ── Same sanity log line that run_pipeline.py emits ───────────────────
    n_with_like = int((result["why_like_1"].astype(str) != "").sum())
    n_with_fade = int((result["why_fade_1"].astype(str) != "").sum())
    print(
        f"\n  Step 6b log line would read:\n"
        f"    Attributions: {n_with_like}/{len(result)} horses with a like, "
        f"{n_with_fade}/{len(result)} with a fade"
    )

    assert n_with_like >= 4, f"only {n_with_like} horses got a like (expected most)"
    assert n_with_fade >= 4, f"only {n_with_fade} horses got a fade (expected most)"

    print("\nAll integration checks passed ✓")


if __name__ == "__main__":
    print("Integration smoke test for run_pipeline.py step 6b…")
    main()
