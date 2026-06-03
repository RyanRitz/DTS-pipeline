"""
Integration test for run_pipeline.generate_pdf.

We don't run the whole pipeline — that needs the BRIS scoring artifacts,
.sas7bdat coefficient files, etc. Instead we hand-craft a scored_df with
the production BRIS column names, build a ScoringResult around it, and
call generate_pdf directly. The success criterion is that a real PDF
lands on disk and has nontrivial size.

This guards against regressions in the BRIS-to-pdf.py column mapping
without needing the full coeff-file environment.
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

import types
import os

# Patch import paths so attribution / pdf / config resolve from outputs
sys.path.insert(0, "/mnt/user-data/outputs")
sys.path.insert(0, "/home/claude/work")

# Stub out the heavy project modules that aren't on sys.path here.
# In production they all import fine.
sys.modules.setdefault("config", types.SimpleNamespace(
    OUTPUT_DIR=Path(tempfile.mkdtemp(prefix="pdf_test_")),
    TRACK_FULL_NAMES={"LRL": "Laurel Park"},
    DTS_TRACK_WHITELIST={"LRL"},
))
sys.modules.setdefault("scratches", types.SimpleNamespace(
    get_scratches=lambda *a, **kw: [],
    merge_with_manual=lambda a, b: a,
    ScratchEntry=type("ScratchEntry", (), {}),
))
sys.modules.setdefault("apply_scratches", types.SimpleNamespace(
    apply_scratches=lambda df, *a, **kw: df,
))
sys.modules.setdefault("ingest_drf", types.SimpleNamespace(
    ingest_drf=lambda *a, **kw: pd.DataFrame(),
))
sys.modules.setdefault("features", types.SimpleNamespace(
    engineer_features=lambda df, *a, **kw: df,
))
sys.modules.setdefault("score", types.SimpleNamespace(
    score_dataframe=lambda df, *a, **kw: df,
))
sys.modules.setdefault("track_geometry", types.SimpleNamespace(
    get_turns=lambda *a, **kw: 1,
))
sys.modules.setdefault("track_status", types.SimpleNamespace(
    TrackStatus=type("TrackStatus", (), {
        "first_post": None, "dirt_condition": "", "turf_condition": "",
        "fetch_ok": False,
    }),
    fetch_track_status=lambda *a, **kw: None,
    get_track_status=lambda *a, **kw: None,
))
sys.modules.setdefault("notify", types.SimpleNamespace(
    send_failure_email=lambda *a, **kw: None,
))
sys.modules.setdefault("model_registry", types.SimpleNamespace(
    load_models=lambda *a, **kw: {},
))
sys.modules.setdefault("output", types.SimpleNamespace(
    write_xlsx=lambda *a, **kw: Path("/tmp/dummy.xlsx"),
))
sys.modules.setdefault("attribution", types.SimpleNamespace(
    add_attributions=lambda df, *a, **kw: df,
))

# Now we can import run_pipeline
import importlib
if "run_pipeline" in sys.modules:
    del sys.modules["run_pipeline"]


def build_fake_scored_df() -> pd.DataFrame:
    """
    Two horses in Race 1, two in Race 2. Production-shaped column names
    so generate_pdf's translation layer gets exercised.
    """
    rows = []
    for race, prog, horse, btsm, ml, prob, rank in [
        (1, "1", "ALPHA",   3.5, 5.0,  0.35, 1),
        (1, "2", "BRAVO",   8.0, 6.0,  0.25, 2),
        (1, "3", "CHARLIE", 12.0, 10.0, 0.20, 3),
        (2, "1", "DELTA",   2.5, 4.0,  0.40, 1),
        (2, "2", "ECHO",    9.0, 8.0,  0.30, 2),
    ]:
        rows.append({
            "Track": "LRL", "Date": "2026-05-15",
            "Race": race,
            "ProgramNumberifavailable": prog,
            "HorseName": horse,
            "Distanceinyards": 1320,            # 6 furlongs
            "Surface": "D",
            "RaceType": "C",                    # Claiming
            "AgeSexRestrictions": "BUN",        # Open 3YO+
            "TodaysRaceClassification": "Clm 20000n2x",
            "Purse": 41000,
            "TodaysJockey": f"JOCKEY {prog}",
            "TodaysTrainer": f"TRAINER {prog}",
            "MornLineOddsifavailable": ml,
            "DTSOdds": btsm,
            "ProbToWin": prob,
            "rank": rank,
            "SmartComment": "DTS Best ValueBet $$$" if rank == 1 else "Good shot; need a price",
            "WagerType1": "DAILY DOUBLE / EXACTA / TRIFECTA",
            "WagerType2": "PICK 3 (RACES 1-2-3) / PICK 5 (RACES 1-5)",
            # Attribution with scores
            "why_like_1": "Sharp work tab",
            "why_like_1_score": 0.45,            # > 0.20 -> kept
            "why_like_2": "Bred for the distance",
            "why_like_2_score": 0.15,            # < 0.20 -> dropped
            "why_like_3": "Hot jockey",
            "why_like_3_score": 0.32,            # > 0.20 -> kept
            "why_fade_1": "Wide draw concern",
            "why_fade_1_score": 0.28,
            "why_fade_2": "Class jump questionable",
            "why_fade_2_score": 0.08,            # below threshold
            "why_fade_3": "",                     # missing
        })
    return pd.DataFrame(rows)


def test_generate_pdf_basic():
    """PREVIEW path: no track_status, conditions show TBD."""
    import run_pipeline
    df = build_fake_scored_df()
    scoring_result = run_pipeline.ScoringResult(
        out_path=Path("/tmp/not_used.xlsx"),
        scored_df=df,
        feature_df=df,
        track="LRL",
        race_date="20260515",
        is_final=False,
        track_status=None,
    )
    pdf_path = run_pipeline.generate_pdf(scoring_result, is_final=False)
    assert pdf_path is not None, "generate_pdf returned None"
    assert pdf_path.exists(), f"PDF not on disk: {pdf_path}"
    size = pdf_path.stat().st_size
    assert size > 10_000, f"PDF too small ({size} bytes) — likely empty / failed"
    print(f"  PASS PREVIEW: {pdf_path.name} ({size:,} bytes)")
    return pdf_path


def test_generate_pdf_final_with_conditions():
    """FINAL path: track_status with real conditions and first post."""
    import run_pipeline

    df = build_fake_scored_df()

    # Build a minimal TrackStatus-shaped object. The real TrackStatus
    # class is in track_status.py; we use a duck-typed stub here so we
    # don't need the full module loaded.
    class _FakeTS:
        first_post     = datetime(2026, 5, 15, 11, 30)
        dirt_condition = "Fast"
        turf_condition = "Firm"
        fetch_ok       = True
        def as_dict(self):
            return {
                "first_post": self.first_post.isoformat(),
                "dirt_condition": self.dirt_condition,
                "turf_condition": self.turf_condition,
            }

    scoring_result = run_pipeline.ScoringResult(
        out_path=Path("/tmp/not_used.xlsx"),
        scored_df=df,
        feature_df=df,
        track="LRL",
        race_date="20260515",
        is_final=True,
        track_status=_FakeTS(),
    )
    pdf_path = run_pipeline.generate_pdf(scoring_result, is_final=True)
    assert pdf_path is not None, "generate_pdf returned None"
    assert pdf_path.exists(), f"PDF not on disk: {pdf_path}"
    size = pdf_path.stat().st_size
    assert size > 10_000, f"PDF too small ({size} bytes)"
    assert pdf_path.name.endswith("FINAL.pdf"), f"unexpected name: {pdf_path.name}"
    print(f"  PASS FINAL:   {pdf_path.name} ({size:,} bytes)")
    return pdf_path


def test_empty_scored_df_returns_None():
    """An empty DataFrame should fail cleanly, not crash."""
    import run_pipeline
    scoring_result = run_pipeline.ScoringResult(
        out_path=Path("/tmp/not_used.xlsx"),
        scored_df=pd.DataFrame(),
        feature_df=pd.DataFrame(),
        track="LRL",
        race_date="20260515",
        is_final=False,
        track_status=None,
    )
    result = run_pipeline.generate_pdf(scoring_result, is_final=False)
    assert result is None, f"empty df should return None, got {result}"
    print(f"  PASS empty df returns None cleanly")


if __name__ == "__main__":
    print("run_pipeline.generate_pdf integration tests…\n")
    test_generate_pdf_basic()
    test_generate_pdf_final_with_conditions()
    test_empty_scored_df_returns_None()
    print("\nAll tests passed ✓")
