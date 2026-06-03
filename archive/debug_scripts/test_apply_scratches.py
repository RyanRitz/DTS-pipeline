"""
End-to-end test for apply_scratches.py
=======================================
Loads the real CDX0508.DRF, mocks the Equibase RSS feed with the actual
May 8 2026 bulletin payloads, runs fetch_and_apply, and verifies:
  - 78 rows -> 69 rows (9 scratches dropped)
  - The 9 specific (race, program, horse) tuples are gone
  - The non-scratched horses are still there (no overshoot)
"""

import sys
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, "/home/claude/btsm")

import feedparser
import apply_scratches as aps


# ---------------------------------------------------------------------------
# Fixtures: real RSS bulletins (same as test_scratches.py)
# ---------------------------------------------------------------------------
def _t(h, m, s):
    return datetime(2026, 5, 8, h, m, s).timetuple()[:9]


REAL_BULLETINS = [
    {"title": "Equibase CD Changes & Scratches 05/08/2026 09:53:56 AM",
     "description": (
        "Race 01: <b># 5 Temporarilyforever</b> <i>Scratched</i> - PrivVet-Illness<br />"
        "Race 04: <b># 2 Yes Julia</b> <i>Scratched</i> - Trainer<br />"
        "Race 04: <b># 4 Fashion Quest</b> <i>Scratched</i> - Trainer<br />"
        "Race 04: <b>#11 Amazon Time</b> <i>Scratched</i> - Re-entered<br />"
        "Race 04: <b>#12 Spare Me</b> <i>Scratched</i> - Re-entered<br />"
        "Race 04: <b>#13 Vivianite</b> <i>Scratched</i> - Re-entered<br />"
        "Race 04: <b>#14 Cardiff Reef</b> <i>Scratched</i> - Re-entered<br />"
        "Race 08: <b># 2 Antrax</b> <i>Scratched</i> - RegVet-Unsound<br />"
        "Race 09: <b># 5 Sunrise</b> <i>Scratched</i> - Trainer<br />"),
     "published_parsed": _t(9, 53, 56)},
    {"title": "Equibase CD Changes & Scratches 05/08/2026 11:04:56 AM",
     "description": (
        "Race 09: <b>#12 Brave Force</b> <i>Scratched</i> - Reason Unavailable<br />"
        "Race 09: <b>#13 Chambersville</b> <i>Scratched</i> - Reason Unavailable<br />"
        "Race 09: <b>#14 Mischief Ride</b> <i>Scratched</i> - Reason Unavailable<br />"),
     "published_parsed": _t(11, 4, 56)},
    {"title": "Equibase CD Changes & Scratches 05/08/2026 12:46:25 PM",
     "description": (
        "Race 09: <b>#12 Brave Force</b> Scratch Reason - Reason Unavailable changed to Also-Eligible<br />"
        "Race 09: <b>#13 Chambersville</b> Scratch Reason - Reason Unavailable changed to Also-Eligible<br />"
        "Race 09: <b>#14 Mischief Ride</b> Scratch Reason - Reason Unavailable changed to Also-Eligible<br />"),
     "published_parsed": _t(12, 46, 25)},
    {"title": "Equibase CD Changes & Scratches 05/08/2026 01:14:10 PM",
     "description": (
        "Race 02: <b># 5 Phenomenal Dream</b> <i>Scratched</i> - Reason Unavailable<br />"),
     "published_parsed": _t(13, 14, 10)},
    {"title": "Equibase CD Changes & Scratches 05/08/2026 01:15:01 PM",
     "description": (
        "Race 02: <b># 5 Phenomenal Dream</b> Scratch Reason - Reason Unavailable changed to RegVet-Unsound<br />"),
     "published_parsed": _t(13, 15, 1)},
]


def _make_feed(items):
    feed = feedparser.FeedParserDict()
    feed["bozo"] = False
    feed["entries"] = [feedparser.FeedParserDict(i) for i in items]
    return feed


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
DRF_PATH = "/mnt/user-data/uploads/CDX0508.DRF"

# What we expect to be removed:
EXPECTED_REMOVED = [
    (1, "5",  "TEMPORARILYFOREVER"),
    (2, "5",  "PHENOMENAL DREAM"),
    (4, "2",  "YES JULIA"),
    (4, "4",  "FASHION QUEST"),
    (8, "2",  "ANTRAX"),
    (9, "5",  "SUNRISE"),
    (9, "12", "BRAVE FORCE"),
    (9, "13", "CHAMBERSVILLE"),
    (9, "14", "MISCHIEF RIDE"),
]

# What we expect to KEEP (the Re-entered horses):
EXPECTED_KEPT = [
    (4, "11", "AMAZON TIME"),
    (4, "12", "SPARE ME"),
    (4, "13", "VIVIANITE"),
    (4, "14", "CARDIFF REEF"),
]


print("=" * 70)
print("End-to-end test: CDX0508.DRF + mocked May 8 RSS")
print("=" * 70)

# 1. Read the real DRF
df = aps._minimal_drf_read(DRF_PATH)
print(f"\nLoaded DRF: {len(df)} horses, {df['Race'].nunique()} races")

# 2. Run the filter with mocked RSS
with patch("scratches.feedparser.parse", return_value=_make_feed(REAL_BULLETINS)):
    df_after, summary = aps.fetch_and_apply(df, "CD", "0508", "2026")

# 3. Print the summary
print(f"\nResult:")
print(f"  Before:           {summary.rows_before}")
print(f"  After:            {summary.rows_after}")
print(f"  Dropped:          {summary.rows_dropped}")
print(f"  Requested:        {summary.scratches_requested}")
print(f"  Matched in DRF:   {summary.scratches_matched}")
print(f"  Unmatched:        {len(summary.unmatched)}")

# 4. Verify counts
results = []
results.append(("rows_before == 78", summary.rows_before == 78))
results.append(("rows_after == 69",  summary.rows_after == 69))
results.append(("rows_dropped == 9", summary.rows_dropped == 9))
results.append(("scratches_requested == 9", summary.scratches_requested == 9))
results.append(("scratches_matched == 9",   summary.scratches_matched == 9))
results.append(("no unmatched",             len(summary.unmatched) == 0))

# 5. Verify the right horses were removed
print("\nVerifying expected horses were REMOVED:")
remaining_keys = {(int(r["Race"]), r["ProgramNumberifavailable"])
                  for _, r in df_after.iterrows()}
for race, prog, horse in EXPECTED_REMOVED:
    still_there = (race, prog) in remaining_keys
    status = "FAIL: still in dataframe" if still_there else "PASS"
    print(f"  Race {race} #{prog:<3} {horse:<22}  {status}")
    results.append((f"removed race {race} #{prog}", not still_there))

# 6. Verify the Re-entered horses were KEPT
print("\nVerifying Re-entered horses were KEPT:")
for race, prog, horse in EXPECTED_KEPT:
    still_there = (race, prog) in remaining_keys
    status = "PASS" if still_there else "FAIL: was removed"
    print(f"  Race {race} #{prog:<3} {horse:<22}  {status}")
    results.append((f"kept race {race} #{prog}", still_there))

# 7. Per-race field sizes
print("\nField sizes per race after scratches:")
expected_sizes = {1: 5, 2: 5, 3: 7, 4: 12, 5: 6, 6: 9, 7: 9, 8: 6, 9: 10}
actual_sizes = df_after.groupby("Race").size().to_dict()
print(f"  {'Race':<5}{'Expected':<10}{'Actual':<10}{'OK?'}")
for race in sorted(expected_sizes):
    exp = expected_sizes[race]
    act = actual_sizes.get(race, 0)
    ok = exp == act
    print(f"  {race:<5}{exp:<10}{act:<10}{'PASS' if ok else 'FAIL'}")
    results.append((f"race {race} field size", ok))

# 8. as_dict() should be JSON-serializable
import json
try:
    s = json.dumps(summary.as_dict(), default=str)
    print(f"\nSummary serialization: PASS ({len(s)} chars)")
    results.append(("summary serializable", True))
except Exception as e:
    print(f"\nSummary serialization: FAIL ({e})")
    results.append(("summary serializable", False))

# 9. No-scratches edge case (manual list empty, RSS empty)
print("\nEdge case: no scratches -> dataframe unchanged")
empty_feed = _make_feed([])
with patch("scratches.feedparser.parse", return_value=empty_feed):
    df_noop, summary_noop = aps.fetch_and_apply(df, "CD", "0508", "2026")
results.append(("no-op preserves all rows", len(df_noop) == len(df)))
print(f"  Before {len(df)}, after {len(df_noop)}: "
      f"{'PASS' if len(df_noop) == len(df) else 'FAIL'}")

# 10. Manual extra scratch that doesn't exist in DRF -> reported as unmatched
print("\nEdge case: bogus manual scratch -> reported as unmatched, dataframe unchanged")
bogus = [(99, "99")]   # race 99 doesn't exist
with patch("scratches.feedparser.parse", return_value=empty_feed):
    df_bogus, summary_bogus = aps.fetch_and_apply(
        df, "CD", "0508", "2026", manual_extra=bogus,
    )
ok = (len(df_bogus) == len(df) and
      len(summary_bogus.unmatched) == 1 and
      summary_bogus.unmatched[0].race == 99)
results.append(("bogus manual reported as unmatched", ok))
print(f"  rows {len(df_bogus)}, unmatched {len(summary_bogus.unmatched)}: "
      f"{'PASS' if ok else 'FAIL'}")

# Summary
print("\n" + "=" * 70)
passed = sum(1 for _, ok in results if ok)
total = len(results)
print(f"Tests passed: {passed} / {total}")
print("=" * 70)
sys.exit(0 if passed == total else 1)
