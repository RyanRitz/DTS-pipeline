"""
Tests for scratches.py — uses the REAL Equibase bulletin format observed
in the live CD-USA.rss feed on 2026-05-08.

The fixture below reproduces the seven bulletins seen in your CLI run so we
can verify end-to-end parsing without hitting the network.
"""

import sys
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, "/home/claude/btsm")

import feedparser
import scratches as sc


# ---------------------------------------------------------------------------
# Fixture: real CD-USA bulletins from 2026-05-08 (in chronological order)
# ---------------------------------------------------------------------------
def _t(h, m, s):
    """Build a feedparser-style time tuple for 2026-05-08 at HH:MM:SS."""
    return datetime(2026, 5, 8, h, m, s).timetuple()[:9]


REAL_BULLETINS = [
    # 05:04:21 AM — race-level rail distance updates (no horse changes)
    {
        "title": "Equibase CD Changes & Scratches 05/08/2026 05:04:21 AM",
        "description": (
            "Race 04: Temp Rail Distance set at 36 ft.<br />"
            "Race 06: Temp Rail Distance set at 36 ft.<br />"
            "Race 09: Temp Rail Distance set at 36 ft.<br />"
        ),
        "published_parsed": _t(5, 4, 21),
    },
    # 09:53:56 AM — initial scratches batch (some are Re-entered = reinstated)
    {
        "title": "Equibase CD Changes & Scratches 05/08/2026 09:53:56 AM",
        "description": (
            "Race 01: <b># 5 Temporarilyforever</b> <i>Scratched</i> - PrivVet-Illness<br />"
            "Race 04: <b># 2 Yes Julia</b> <i>Scratched</i> - Trainer<br />"
            "Race 04: <b># 4 Fashion Quest</b> <i>Scratched</i> - Trainer<br />"
            "Race 04: <b>#11 Amazon Time</b> <i>Scratched</i> - Re-entered<br />"
            "Race 04: <b>#12 Spare Me</b> <i>Scratched</i> - Re-entered<br />"
            "Race 04: <b>#13 Vivianite</b> <i>Scratched</i> - Re-entered<br />"
            "Race 04: <b>#14 Cardiff Reef</b> <i>Scratched</i> - Re-entered<br />"
            "Race 08: <b># 2 Antrax</b> <i>Scratched</i> - RegVet-Unsound<br />"
            "Race 09: <b># 5 Sunrise</b> <i>Scratched</i> - Trainer<br />"
        ),
        "published_parsed": _t(9, 53, 56),
    },
    # 11:04:56 AM — three more scratches in race 9
    {
        "title": "Equibase CD Changes & Scratches 05/08/2026 11:04:56 AM",
        "description": (
            "Race 09: <b>#12 Brave Force</b> <i>Scratched</i> - Reason Unavailable<br />"
            "Race 09: <b>#13 Chambersville</b> <i>Scratched</i> - Reason Unavailable<br />"
            "Race 09: <b>#14 Mischief Ride</b> <i>Scratched</i> - Reason Unavailable<br />"
        ),
        "published_parsed": _t(11, 4, 56),
    },
    # 12:45:06 PM — track conditions, wager cancellations, jockey changes
    {
        "title": "Equibase CD Changes & Scratches 05/08/2026 12:45:06 PM",
        "description": (
            "Race 01: Current Dirt Track Condition -   changed to Fast<br />"
            "Race 01: <i>Superfecta Wagering Cancelled</i><br />"
            "Race 01: <i>Odd or Even Wagering Cancelled</i><br />"
            "Race 02: Current Dirt Track Condition -   changed to Fast<br />"
            "Race 03: Current Dirt Track Condition -   changed to Fast<br />"
            "Race 04: Current Turf Track Condition -   changed to Firm<br />"
            "Race 04: <b># 8 Victor Valley</b> Jockey - Andres Calleja changed to Alex Achard<br />"
            "Race 05: Current Dirt Track Condition -   changed to Fast<br />"
            "Race 06: Current Turf Track Condition -   changed to Firm<br />"
            "Race 07: Current Dirt Track Condition -   changed to Fast<br />"
            "Race 08: Current Dirt Track Condition -   changed to Fast<br />"
            "Race 09: Current Turf Track Condition -   changed to Firm<br />"
            "Race 09: <b># 8 Mendels Mate</b> Jockey - Tyler Gaffalione changed to Ben Curtis<br />"
        ),
        "published_parsed": _t(12, 45, 6),
    },
    # 12:46:25 PM — REINSTATEMENT: race 9 #12, #13, #14 changed to Also-Eligible
    # These three horses were scratched at 11:04 but are now back in the race.
    {
        "title": "Equibase CD Changes & Scratches 05/08/2026 12:46:25 PM",
        "description": (
            "Race 09: <b>#12 Brave Force</b> Scratch Reason - Reason Unavailable changed to Also-Eligible<br />"
            "Race 09: <b>#13 Chambersville</b> Scratch Reason - Reason Unavailable changed to Also-Eligible<br />"
            "Race 09: <b>#14 Mischief Ride</b> Scratch Reason - Reason Unavailable changed to Also-Eligible<br />"
        ),
        "published_parsed": _t(12, 46, 25),
    },
    # 01:14:10 PM — new scratch in race 2
    {
        "title": "Equibase CD Changes & Scratches 05/08/2026 01:14:10 PM",
        "description": (
            "Race 02: <b># 5 Phenomenal Dream</b> <i>Scratched</i> - Reason Unavailable<br />"
        ),
        "published_parsed": _t(13, 14, 10),
    },
    # 01:15:01 PM — same horse, reason updated (still scratched, just better info)
    {
        "title": "Equibase CD Changes & Scratches 05/08/2026 01:15:01 PM",
        "description": (
            "Race 02: <b># 5 Phenomenal Dream</b> Scratch Reason - Reason Unavailable changed to RegVet-Unsound<br />"
        ),
        "published_parsed": _t(13, 15, 1),
    },
]


def _make_feed(items):
    feed = feedparser.FeedParserDict()
    feed["bozo"] = False
    feed["entries"] = [feedparser.FeedParserDict(i) for i in items]
    return feed


# ---------------------------------------------------------------------------
# Expected outcomes for the real CD bulletins
# ---------------------------------------------------------------------------
# Per BTSM scoring rule: every horse in the DRF gets scored UNLESS scratched.
# Also-Eligible horses are not running (unless promoted) so they remain on
# the scratch list. Only "Re-entered" horses come off the scratch list,
# because they're back in the field as confirmed runners.
EXPECTED_ACTIVE_SCRATCHES = {
    (1, "5"),    # Temporarilyforever - PrivVet-Illness
    (2, "5"),    # Phenomenal Dream - reason updated to RegVet-Unsound
    (4, "2"),    # Yes Julia - Trainer
    (4, "4"),    # Fashion Quest - Trainer
    (8, "2"),    # Antrax - RegVet-Unsound
    (9, "5"),    # Sunrise - Trainer
    (9, "12"),   # Brave Force - reason updated to Also-Eligible (NOT running)
    (9, "13"),   # Chambersville - reason updated to Also-Eligible (NOT running)
    (9, "14"),   # Mischief Ride - reason updated to Also-Eligible (NOT running)
}

# Should NOT appear (these were Re-entered = back in the field as runners):
EXPECTED_NOT_SCRATCHED = {
    (4, "11"),   # Amazon Time - Re-entered
    (4, "12"),   # Spare Me - Re-entered
    (4, "13"),   # Vivianite - Re-entered
    (4, "14"),   # Cardiff Reef - Re-entered
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
results = []


def section(name):
    print(f"\n=== {name} ===")


# Test 1: end-to-end against real bulletins
section("Test 1: real CD-USA bulletins from 2026-05-08")
feed = _make_feed(REAL_BULLETINS)
with patch("scratches.feedparser.parse", return_value=feed):
    scr = sc.get_scratches("CD", "0508", "2026")

actual = {(s.race, s.program_number) for s in scr}
print(f"  Got {len(scr)} active scratch(es):")
for s in scr:
    print(f"    Race {s.race:>2}  #{s.program_number:<3}  {s.horse_name:<22}  ({s.reason})")

missing = EXPECTED_ACTIVE_SCRATCHES - actual
extra = actual - EXPECTED_ACTIVE_SCRATCHES
spurious = actual & EXPECTED_NOT_SCRATCHED

t1_ok = True
if missing:
    print(f"  FAIL: missing scratches: {missing}")
    t1_ok = False
if extra:
    print(f"  FAIL: unexpected scratches: {extra}")
    t1_ok = False
if spurious:
    print(f"  FAIL: returned reinstated horses as scratches: {spurious}")
    t1_ok = False
if t1_ok:
    print("  PASS")
results.append(t1_ok)


# Test 2: reason-update preserves the scratch but updates the reason
section("Test 2: 1:15 PM reason update on race 2 #5 keeps scratch + new reason")
phenom = next((s for s in scr if s.race == 2 and s.program_number == "5"), None)
if phenom and "RegVet-Unsound" in phenom.reason:
    print(f"  PASS: race 2 #5 reason = {phenom.reason!r}")
    results.append(True)
else:
    print(f"  FAIL: race 2 #5 = {phenom!r}")
    results.append(False)


# Test 3: change-type classification on the full feed
section("Test 3: change-type classification")
with patch("scratches.feedparser.parse", return_value=_make_feed(REAL_BULLETINS)):
    all_changes = sc.fetch_all_changes("CD")

type_counts: dict[str, int] = {}
for c in all_changes:
    type_counts[c.change_type] = type_counts.get(c.change_type, 0) + 1
print(f"  Counts: {type_counts}")

# We expect:
#  - 9 scratches (5 from 9:53, 3 from 11:04, 1 from 1:14)
#  - 7 reinstates (4 Re-entered from 9:53, 3 Also-Eligible from 12:46)
#  - 1 reason-update on 1:15 (classified as scratch since it starts with
#    "Scratch Reason ... changed to ..." but is NOT Also-Eligible — wait,
#    actually our classifier currently returns "other" for that line. Let's see.)
# We expect:
#  - 9 original "Scratched - X" events from 9:53/11:03/1:14 bulletins
#  - 4 "Scratch Reason - X changed to Y" events (1 RegVet update on race 2 #5,
#    plus 3 Also-Eligible updates on race 9 #12/#13/#14) — all classified as
#    'scratch' so they update reason in cumulative state
#  - Total: 13 'scratch' events
#  - 4 'reinstate' events (Re-entered: race 4 #11/#12/#13/#14)
expected_min = {"scratch": 13, "reinstate": 4, "jockey": 2,
                "track_cond": 9, "wager": 2, "rail": 3}
t3_ok = True
for k, v in expected_min.items():
    actual_v = type_counts.get(k, 0)
    if actual_v != v:
        print(f"  FAIL: expected {v} {k!r}, got {actual_v}")
        t3_ok = False
if t3_ok:
    print("  PASS")
results.append(t3_ok)


# Test 4: track code mapping unchanged
section("Test 4: track code mapping (regression)")
mapping_failures = []
for code, expected in [
    ("KEE", "KEE-USA"),
    ("CDX", "CD-USA"),
    ("CD",  "CD-USA"),
    ("GPX", "GP-USA"),
    ("GP",  "GP-USA"),
    ("WO",  "WO-CAN"),
]:
    url = sc._build_rss_url(code)
    if expected in url:
        print(f"  PASS: {code} -> {url}")
    else:
        print(f"  FAIL: {code} -> {url} (expected {expected})")
        mapping_failures.append(code)
results.append(len(mapping_failures) == 0)


# Test 5: manual scratch merge
section("Test 5: manual scratch merge")
rss_in = [sc.ScratchEntry(race=1, program_number="5", horse_name="X")]
manual_in = [(2, "3"), (1, "5")]
merged = sc.merge_with_manual(rss_in, manual_in)
keys = sorted((s.race, s.program_number) for s in merged)
if keys == [(1, "5"), (2, "3")]:
    print(f"  PASS: merged keys = {keys}")
    results.append(True)
else:
    print(f"  FAIL: merged keys = {keys}")
    results.append(False)


# Test 6: empty feed
section("Test 6: empty feed returns no scratches")
with patch("scratches.feedparser.parse", return_value=_make_feed([])):
    empty = sc.get_scratches("CD", "0508", "2026")
if not empty:
    print(f"  PASS")
    results.append(True)
else:
    print(f"  FAIL: got {len(empty)} entries")
    results.append(False)


# Test 7: line splitter handles HTML entities and whitespace
section("Test 7: HTML splitter")
html = (
    "Race 01: <b># 5 Temporarilyforever</b> <i>Scratched</i> - PrivVet-Illness<br />"
    "Race 04: Temp Rail Distance set at 36 ft.<br/>"
    "Race&nbsp;06: junk"
)
lines = sc._split_bulletin_lines(html)
expected_lines = [
    "Race 01: # 5 Temporarilyforever Scratched - PrivVet-Illness",
    "Race 04: Temp Rail Distance set at 36 ft.",
    "Race 06: junk",
]
if lines == expected_lines:
    print(f"  PASS: {len(lines)} lines parsed correctly")
    results.append(True)
else:
    print(f"  FAIL")
    print(f"    expected: {expected_lines}")
    print(f"    got:      {lines}")
    results.append(False)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for r in results if r)
total = len(results)
print(f"Tests passed: {passed} / {total}")
print("=" * 60)
sys.exit(0 if passed == total else 1)
