"""
Unit test for _filter_to_whitelist using the actual May 9 download set.

We don't actually launch Brisnet; we just build a fake `tracks` list that
mirrors what extract_tracks() returned on May 9, then verify that the
whitelist filter keeps exactly the DTS-relevant subset.

Two scenarios:
  1. DTS_TRACK_WHITELIST set → drops the 27 non-DTS tracks, keeps 15
  2. DTS_TRACK_WHITELIST = None → keeps all 42 (pass-through behavior)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import brisnet_download_v3 as bd


# Reconstructed from May 9 log — these are the trackCodes that got
# downloaded that day (one entry per track since we only care about
# the filter behavior, not date counts).
MAY9_DOWNLOADS = [
    "AJX","ASD","BAQ","BTP","CCP","CD","CT","CTM","DED","EMD","EQB","EQK",
    "EQZ","EVD","FL","FP","GP","HAW","HOU","IND","LA","LAD","LBG","LRL",
    "LS","MC","MNR","MTH","PEN","POD","PRM","PRX","PW","RP","SA","SRP",
    "SWA","SWL","TDN","WBR","WIL","WO",
]


def make_tracks(codes):
    """Build a minimal fake tracks list matching extract_tracks output."""
    return [
        {
            "trackCode": c,
            "trackName": f"{c} Track",
            "country":   "USA",
            "trackType": "TB",
            "dayEvening": "D",
            "dates": [{"productDate": "2026-05-09T19:00:00.000Z", "raceNumber": 0}],
        }
        for c in codes
    ]


def test_whitelist_drops_non_btsm_tracks():
    # Use the same DTS whitelist that's in config.py
    dts_set = {
        "CD","CDX","SAR","BEL","SA","GP","GPX","OP","OPX","DMR","AQU",
        "KEE","KD","MTH","PRX","FG","FGX","PIM","TAM","WO","RP","IND",
        "LRL","LS","DEL","PID","HOU","MVR","MNR","ZIA","ELP","TDN",
        "SUN","EVD","CNL","PRM","TP","TPX",
    }
    bd.DTS_TRACK_WHITELIST = dts_set

    tracks = make_tracks(MAY9_DOWNLOADS)
    out = bd._filter_to_whitelist(tracks)
    out_codes = {t["trackCode"] for t in out}

    expected_kept = {
        "CD","EVD","GP","HOU","IND","LRL","LS","MNR","MTH","PRM","PRX",
        "RP","SA","TDN","WO",
    }
    assert out_codes == expected_kept, (
        f"\n  Expected kept: {sorted(expected_kept)}"
        f"\n  Actually kept: {sorted(out_codes)}"
        f"\n  Missing:       {sorted(expected_kept - out_codes)}"
        f"\n  Extra:         {sorted(out_codes - expected_kept)}"
    )
    print(f"  PASS: kept {len(out)}/{len(tracks)} tracks (expected 15/42)")
    print(f"        Kept tracks: {sorted(out_codes)}")


def test_whitelist_None_passes_everything():
    bd.DTS_TRACK_WHITELIST = None
    tracks = make_tracks(MAY9_DOWNLOADS)
    out = bd._filter_to_whitelist(tracks)
    assert len(out) == len(tracks), (
        f"With whitelist=None, expected {len(tracks)}, got {len(out)}"
    )
    print(f"  PASS: whitelist=None pass-through ({len(out)} kept)")


def test_lowercase_track_codes_still_match():
    # Defensive — make sure code matching is case-insensitive on the input side
    bd.DTS_TRACK_WHITELIST = {"CD", "KEE"}
    tracks = [
        {"trackCode": "cd", "trackName": "Churchill", "country": "USA",
         "trackType": "TB", "dayEvening": "D", "dates": []},
        {"trackCode": "BTP", "trackName": "Belterra", "country": "USA",
         "trackType": "TB", "dayEvening": "D", "dates": []},
    ]
    out = bd._filter_to_whitelist(tracks)
    assert len(out) == 1, f"expected 1, got {len(out)}"
    assert out[0]["trackCode"] == "cd", f"expected 'cd', got {out[0]['trackCode']!r}"
    print(f"  PASS: lowercase track code matched whitelist (case-insensitive)")


if __name__ == "__main__":
    print("Testing _filter_to_whitelist against May 9 download set…\n")
    test_whitelist_drops_non_btsm_tracks()
    print()
    test_whitelist_None_passes_everything()
    print()
    test_lowercase_track_codes_still_match()
    print("\nAll tests passed ✓")
