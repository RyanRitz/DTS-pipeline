"""
Tests for the tightened attribution blend math.

We don't load real .sas7bdat files — we exercise _blend_contribution
directly with hand-calculated coefficient sets so we can verify the
blend exactly matches what score.py would do.

Three things we're checking:
  1. Dirt/turf equal-mean only counts FIRING sub-models (matches
     mean(axis=1, skipna=True) on predicted{key} columns).
  2. Maiden weighted blend matches the 0.50/0.25/0.25 formula
     in score._score_maiden, including the "missing bucket = 0
     contribution" behavior (.fillna(0) in score.py).
  3. The old pairwise-running-average bug is gone:
     for sub-coefs A=2, B=4, C=6 on the same feat,
     old (pairwise): ((2+4)/2 + 6)/2 = 4.5  WRONG
     new (true mean):              (2+4+6)/3 = 4.0  RIGHT
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import math
import pandas as pd
from attribution import _blend_contribution


def make_row(values: dict, firing_subs: list[str]) -> pd.Series:
    """
    Helper: construct a horse row where:
      - feature values come from `values`
      - any sub-key in firing_subs gets predicted{key} = 0.5 (non-NaN)
      - any other sub-key gets predicted{key} = NaN (didn't fire)
    """
    data = dict(values)
    # Add predicted{key} columns. Use a wide set so we always cover what
    # we test — any not listed gets NaN.
    all_keys = ["c","n","s","r","hp","lp",
                1,2,3,4,6,8,9,10,12,13,14,15,16,"M","S"]
    for k in all_keys:
        data[f"predicted{k}"] = 0.5 if k in firing_subs else float("nan")
    return pd.Series(data)


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return math.isclose(a, b, abs_tol=tol)


# ---------------------------------------------------------------------------
# Test 1 — dirt, equal-mean over firing sub-models
# ---------------------------------------------------------------------------
def test_dirt_equal_mean_over_firing_submodels():
    # Three dirt sub-models have a coefficient for "speed_feat":
    sub_coefs = {
        "c": {"speed_feat": 2.0},
        "n": {"speed_feat": 4.0},
        "s": {"speed_feat": 6.0},
        "r": {"speed_feat": 8.0},
    }
    feats = ["speed_feat"]

    # Only c, n, s fired (r got NaN'd because horse didn't match its filter).
    row = make_row({"speed_feat": 1.0}, firing_subs=["c", "n", "s"])
    out = _blend_contribution(row, model_id=1, sub_coefs=sub_coefs, feats=feats)

    # Expected: equal mean of (2*1 + 4*1 + 6*1)/3 = 4.0
    # NOT (2*1 + 4*1 + 6*1 + 8*1)/4 = 5.0 (r didn't fire)
    # NOT the broken pairwise running average ((2+4)/2 + 6)/2 = 4.5
    expected = 4.0
    assert approx(out["speed_feat"], expected), (
        f"Dirt 3-firing: expected {expected}, got {out['speed_feat']}"
    )
    print(f"  PASS dirt equal-mean over 3 firing sub-models: {out['speed_feat']}")


def test_dirt_only_one_submodel_fires():
    sub_coefs = {
        "c": {"speed_feat": 2.0},
        "n": {"speed_feat": 4.0},
    }
    feats = ["speed_feat"]
    row = make_row({"speed_feat": 1.5}, firing_subs=["c"])
    out = _blend_contribution(row, model_id=1, sub_coefs=sub_coefs, feats=feats)
    # Just c: 2.0 * 1.5 = 3.0
    expected = 3.0
    assert approx(out["speed_feat"], expected), (
        f"Dirt 1-firing: expected {expected}, got {out['speed_feat']}"
    )
    print(f"  PASS dirt only one sub-model fires: {out['speed_feat']}")


def test_dirt_zero_firing_returns_empty():
    sub_coefs = {"c": {"speed_feat": 2.0}}
    feats = ["speed_feat"]
    row = make_row({"speed_feat": 1.5}, firing_subs=[])  # nothing fired
    out = _blend_contribution(row, model_id=1, sub_coefs=sub_coefs, feats=feats)
    assert out == {}, f"Zero-firing: expected empty dict, got {out}"
    print(f"  PASS dirt zero firing → empty dict")


# ---------------------------------------------------------------------------
# Test 2 — Maiden weighted blend (this is where the old code was very wrong)
# ---------------------------------------------------------------------------
def test_maiden_all_buckets_one_submodel_each():
    # One sub-model per bucket fires.
    # score1 bucket: model 1 fires, coef=10, val=1, score1=10*1=10
    # score2 bucket: model 9 fires, coef=20, val=1, score2=20*1=20
    # score3 bucket: model 13 fires, coef=30, val=1, score3=30*1=30
    # Weighted: 0.5*10 + 0.25*20 + 0.25*30 = 5 + 5 + 7.5 = 17.5
    sub_coefs = {
        1:  {"feat_x": 10.0},
        9:  {"feat_x": 20.0},
        13: {"feat_x": 30.0},
    }
    feats = ["feat_x"]
    row = make_row({"feat_x": 1.0}, firing_subs=[1, 9, 13])
    out = _blend_contribution(row, model_id=3, sub_coefs=sub_coefs, feats=feats)
    expected = 0.5 * 10.0 + 0.25 * 20.0 + 0.25 * 30.0  # 17.5
    assert approx(out["feat_x"], expected), (
        f"Maiden all-buckets: expected {expected}, got {out['feat_x']}"
    )
    print(f"  PASS maiden one sub-model per bucket: {out['feat_x']} (expected {expected})")


def test_maiden_bucket_with_multiple_firing_submodels():
    # score1 bucket has 3 firing sub-models with coefs 1, 3, 5
    # Within-bucket mean: (1+3+5)/3 = 3
    # Only score1 fires, so predicted = 0.5 * 3 + 0.25*0 + 0.25*0 = 1.5
    sub_coefs = {
        1: {"a": 1.0},
        2: {"a": 3.0},
        3: {"a": 5.0},
    }
    feats = ["a"]
    row = make_row({"a": 1.0}, firing_subs=[1, 2, 3])
    out = _blend_contribution(row, model_id=3, sub_coefs=sub_coefs, feats=feats)
    expected = 0.5 * ((1.0 + 3.0 + 5.0) / 3.0)  # 1.5
    assert approx(out["a"], expected), (
        f"Maiden within-bucket mean: expected {expected}, got {out['a']}"
    )
    print(f"  PASS maiden mean within score1 bucket: {out['a']} (expected {expected})")


def test_maiden_missing_bucket_zero_contribution():
    # Only score3 bucket has firing sub-models. score1 and score2 should
    # contribute zero (mirrors .fillna(0) in score._score_maiden).
    sub_coefs = {
        13: {"feat_y": 4.0},
        14: {"feat_y": 8.0},
    }
    feats = ["feat_y"]
    row = make_row({"feat_y": 2.0}, firing_subs=[13, 14])
    out = _blend_contribution(row, model_id=3, sub_coefs=sub_coefs, feats=feats)
    # score3 mean: (4+8)/2 * 2 = 12
    # predicted = 0 + 0 + 0.25 * 12 = 3
    expected = 0.25 * ((4.0 + 8.0) / 2.0) * 2.0  # 3.0
    assert approx(out["feat_y"], expected), (
        f"Maiden missing-buckets: expected {expected}, got {out['feat_y']}"
    )
    print(f"  PASS maiden missing buckets zero-fill: {out['feat_y']} (expected {expected})")


# ---------------------------------------------------------------------------
# Test 3 — feature not present in a sub-model: it just doesn't contribute
# from that sub-model (different sub-models can have different feature sets).
# ---------------------------------------------------------------------------
def test_feature_in_some_submodels_only():
    # Two firing dirt sub-models. "shared" is in both. "only_in_c" only
    # in c. So for "only_in_c": ONE sub-model contributes; but the divisor
    # is still len(firing) = 2 because we're computing the mean over the
    # firing sub-models (matching mean(skipna) on the predicted{key} cols
    # exactly — a missing predicted{key} would make a row drop out, but
    # a feature missing from a sub-model just adds nothing for that
    # sub-model). This mirrors what _proc_score does: features absent
    # from a coefficient file simply aren't used by that sub-model.
    sub_coefs = {
        "c": {"shared": 1.0, "only_in_c": 4.0},
        "n": {"shared": 3.0},
    }
    feats = ["shared", "only_in_c"]
    row = make_row({"shared": 1.0, "only_in_c": 1.0}, firing_subs=["c", "n"])
    out = _blend_contribution(row, model_id=1, sub_coefs=sub_coefs, feats=feats)
    # shared: (1*1 + 3*1)/2 = 2.0
    # only_in_c: (4*1 + 0)/2 = 2.0   (n contributes 0 for this feat)
    assert approx(out["shared"], 2.0), f"shared: {out['shared']}"
    assert approx(out["only_in_c"], 2.0), f"only_in_c: {out['only_in_c']}"
    print(f"  PASS feature in some sub-models only: shared={out['shared']}, only_in_c={out['only_in_c']}")


# ---------------------------------------------------------------------------
# Test 4 — explicit comparison against the old broken pairwise-average
# ---------------------------------------------------------------------------
def test_no_pairwise_running_average_bug():
    # Three sub-models, same feature, coefs 2, 4, 6.
    # OLD code: coefs[col] = (coefs[col] + new_val) / 2
    #   step 1: coefs = {f: 2}
    #   step 2: coefs[f] = (2 + 4) / 2 = 3
    #   step 3: coefs[f] = (3 + 6) / 2 = 4.5
    # NEW code: true mean = (2+4+6)/3 = 4.0
    sub_coefs = {
        "c": {"f": 2.0},
        "n": {"f": 4.0},
        "s": {"f": 6.0},
    }
    feats = ["f"]
    row = make_row({"f": 1.0}, firing_subs=["c", "n", "s"])
    out = _blend_contribution(row, model_id=1, sub_coefs=sub_coefs, feats=feats)
    old_buggy = 4.5
    new_correct = 4.0
    assert approx(out["f"], new_correct), (
        f"Pairwise bug check: expected {new_correct}, got {out['f']}"
    )
    assert not approx(out["f"], old_buggy), (
        f"Got old buggy value {old_buggy} — bug not fixed!"
    )
    print(f"  PASS pairwise bug fixed: got {out['f']}, not buggy {old_buggy}")


if __name__ == "__main__":
    print("Running attribution blend tests…")
    test_dirt_equal_mean_over_firing_submodels()
    test_dirt_only_one_submodel_fires()
    test_dirt_zero_firing_returns_empty()
    test_maiden_all_buckets_one_submodel_each()
    test_maiden_bucket_with_multiple_firing_submodels()
    test_maiden_missing_bucket_zero_contribution()
    test_feature_in_some_submodels_only()
    test_no_pairwise_running_average_bug()
    print("All tests passed ✓")
