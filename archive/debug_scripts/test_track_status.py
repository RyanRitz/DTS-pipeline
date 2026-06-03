"""
Tests for track_status.py — uses mock HTML that mimics the observed
Equibase per-track page layout.
"""

import sys
from datetime import datetime, date
from unittest.mock import patch

sys.path.insert(0, "/home/claude/btsm")

import track_status as ts


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
# Constructed from the screenshot of the actual CD-USA page on May 8 2026.
# Equibase pages use nested tables with inline styles; the parser strips tags
# before matching, so we don't need to fight DOM structure here — we just
# need labels and values in the right places.

REAL_LIKE_CD = """<!DOCTYPE html>
<html>
<head><title>Equibase | Today's Race Day Changes</title></head>
<body>
<h1>Today's Race Day Changes</h1>
<table><tr><td>
    <h2><a href="...">Churchill Downs</a></h2>
    <p><i>Last Updated: May 8, 1:14 PM ET</i></p>
    <p><b>Current Track Conditions:</b><br/>
       <b>Dirt:</b> Fast<br/>
       <b>Turf:</b> Firm</p>
    <p><b>Scheduled First Post:</b> 12:45 PM ET</p>
    <table><tr><td>Current Weather Conditions</td></tr>
    <tr><td>Fair</td><td>Temperature 71&deg;F</td></tr>
    <tr><td>Wind SSW at 15 mph</td><td>Humidity 31%</td></tr>
    </table>
</td></tr></table>

<table>
<tr><th>Race: 1</th><th>Changes</th><th>Time Posted</th></tr>
<tr><td>No Superfecta or Odd or Even Wagering</td><td></td><td>12:44 PM ET</td></tr>
<tr><td>#5</td><td>Temporarilyforever <i>Scratched</i> - PrivVet-Illness</td><td>9:53 AM ET</td></tr>
</table>
</body></html>
"""

# A turf-only track, just to make sure absent-dirt is handled
TURF_ONLY = """<html><body>
<h2>Some Turf Track</h2>
<p>Last Updated: May 8, 11:00 AM ET</p>
<p>Current Track Conditions:<br/>Turf: Yielding</p>
<p>Scheduled First Post: 1:30 PM ET</p>
</body></html>
"""

# All-weather track (e.g. Turfway) — parser should populate dirt_condition
# from the all-weather marker if no actual dirt is present.
ALL_WEATHER = """<html><body>
<h2>Turfway Park</h2>
<p>Last Updated: May 8, 12:00 PM ET</p>
<p>Current Track Conditions:<br/>All Weather: Fast<br/>Turf: Firm</p>
<p>Scheduled First Post: 6:25 PM ET</p>
</body></html>
"""

# Edge case: 12:00 PM (noon) and 12:30 AM (midnight) handling
NOON_FIRST_POST = """<html><body>
<p>Scheduled First Post: 12:00 PM ET</p>
<p>Dirt: Fast</p>
</body></html>
"""

MIDNIGHT_HOUR = """<html><body>
<p>Scheduled First Post: 12:30 AM ET</p>
<p>Dirt: Fast</p>
</body></html>
"""

# Page where conditions changed mid-day — simulate "Sloppy" dirt
SLOPPY_DIRT = """<html><body>
<p>Last Updated: May 8, 2:30 PM ET</p>
<p>Dirt: Sloppy</p>
<p>Turf: Soft</p>
<p>Scheduled First Post: 12:45 PM ET</p>
</body></html>
"""

# Page where the fetch returns nothing (timeout etc.)
EMPTY_FETCH = None


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------
results = []


def section(name):
    print(f"\n=== {name} ===")


# ---------------------------------------------------------------------------
# Test 1: Realistic Churchill Downs page
# ---------------------------------------------------------------------------
section("Test 1: realistic CD page (May 8 2026)")
with patch("track_status._fetch_html", return_value=REAL_LIKE_CD):
    s = ts.get_track_status("CD", "0508", "2026")

print(f"  first_post:      {s.first_post}")
print(f"  dirt_condition:  {s.dirt_condition!r}")
print(f"  turf_condition:  {s.turf_condition!r}")
print(f"  last_updated:    {s.last_updated_raw!r}")

t1_ok = (
    s.first_post == datetime(2026, 5, 8, 12, 45) and
    s.dirt_condition == "Fast" and
    s.turf_condition == "Firm" and
    s.last_updated_raw == "May 8, 1:14 PM ET"
)
print(f"  {'PASS' if t1_ok else 'FAIL'}")
results.append(t1_ok)


# ---------------------------------------------------------------------------
# Test 2: Turf-only track
# ---------------------------------------------------------------------------
section("Test 2: turf-only track (no dirt condition)")
with patch("track_status._fetch_html", return_value=TURF_ONLY):
    s = ts.get_track_status("CD", "0508", "2026")
print(f"  first_post:      {s.first_post}")
print(f"  dirt_condition:  {s.dirt_condition!r}")
print(f"  turf_condition:  {s.turf_condition!r}")

t2_ok = (
    s.first_post == datetime(2026, 5, 8, 13, 30) and
    s.dirt_condition is None and
    s.turf_condition == "Yielding"
)
print(f"  {'PASS' if t2_ok else 'FAIL'}")
results.append(t2_ok)


# ---------------------------------------------------------------------------
# Test 3: All-weather track populates dirt_condition
# ---------------------------------------------------------------------------
section("Test 3: all-weather track")
with patch("track_status._fetch_html", return_value=ALL_WEATHER):
    s = ts.get_track_status("TP", "0508", "2026")
print(f"  first_post:      {s.first_post}")
print(f"  dirt_condition:  {s.dirt_condition!r}  (from All Weather)")
print(f"  turf_condition:  {s.turf_condition!r}")

t3_ok = (
    s.first_post == datetime(2026, 5, 8, 18, 25) and
    s.dirt_condition == "Fast" and
    s.turf_condition == "Firm"
)
print(f"  {'PASS' if t3_ok else 'FAIL'}")
results.append(t3_ok)


# ---------------------------------------------------------------------------
# Test 4: Noon and midnight first post times
# ---------------------------------------------------------------------------
section("Test 4: noon and midnight first-post handling")
with patch("track_status._fetch_html", return_value=NOON_FIRST_POST):
    s_noon = ts.get_track_status("CD", "0508", "2026")
with patch("track_status._fetch_html", return_value=MIDNIGHT_HOUR):
    s_mid = ts.get_track_status("CD", "0508", "2026")

print(f"  noon  -> {s_noon.first_post}")
print(f"  12:30 AM -> {s_mid.first_post}")

t4_ok = (
    s_noon.first_post == datetime(2026, 5, 8, 12, 0) and
    s_mid.first_post == datetime(2026, 5, 8, 0, 30)
)
print(f"  {'PASS' if t4_ok else 'FAIL'}")
results.append(t4_ok)


# ---------------------------------------------------------------------------
# Test 5: Track conditions can change (Sloppy/Soft after rain)
# ---------------------------------------------------------------------------
section("Test 5: changed conditions (sloppy dirt, soft turf)")
with patch("track_status._fetch_html", return_value=SLOPPY_DIRT):
    s = ts.get_track_status("CD", "0508", "2026")
print(f"  dirt: {s.dirt_condition!r}, turf: {s.turf_condition!r}")
t5_ok = s.dirt_condition == "Sloppy" and s.turf_condition == "Soft"
print(f"  {'PASS' if t5_ok else 'FAIL'}")
results.append(t5_ok)


# ---------------------------------------------------------------------------
# Test 6: Network failure returns object with all None fields, no crash
# ---------------------------------------------------------------------------
section("Test 6: graceful failure on fetch error")
with patch("track_status._fetch_html", return_value=EMPTY_FETCH):
    s = ts.get_track_status("CD", "0508", "2026")
print(f"  first_post:     {s.first_post}")
print(f"  dirt:           {s.dirt_condition}")
print(f"  turf:           {s.turf_condition}")
print(f"  source_url set: {bool(s.source_url)}")

t6_ok = (
    s.first_post is None and
    s.dirt_condition is None and
    s.turf_condition is None and
    s.track == "CD" and
    s.race_date == date(2026, 5, 8) and
    bool(s.source_url)
)
print(f"  {'PASS' if t6_ok else 'FAIL'}")
results.append(t6_ok)


# ---------------------------------------------------------------------------
# Test 7: Track code mapping (URL construction)
# ---------------------------------------------------------------------------
section("Test 7: URL construction by track code")
mapping_failures = []
for code, expected_part in [
    ("CD",  "latechangesCD-USA.html"),
    ("CDX", "latechangesCD-USA.html"),
    ("KEE", "latechangesKEE-USA.html"),
    ("GP",  "latechangesGP-USA.html"),
    ("GPX", "latechangesGP-USA.html"),
    ("WO",  "latechangesWO-CAN.html"),
]:
    url = ts._build_html_url(code)
    if expected_part in url:
        print(f"  PASS: {code} -> {url}")
    else:
        print(f"  FAIL: {code} -> {url} (expected {expected_part})")
        mapping_failures.append(code)
results.append(len(mapping_failures) == 0)


# ---------------------------------------------------------------------------
# Test 8: as_dict() is JSON-serializable
# ---------------------------------------------------------------------------
section("Test 8: TrackStatus.as_dict() round-trips through JSON")
with patch("track_status._fetch_html", return_value=REAL_LIKE_CD):
    s = ts.get_track_status("CD", "0508", "2026")
import json
try:
    encoded = json.dumps(s.as_dict())
    decoded = json.loads(encoded)
    t8_ok = (decoded["track"] == "CD" and
             decoded["dirt_condition"] == "Fast" and
             decoded["turf_condition"] == "Firm" and
             "12:45" in decoded["first_post"])
    print(f"  Encoded ({len(encoded)} chars), round-trip OK")
    print(f"  {'PASS' if t8_ok else 'FAIL'}")
    results.append(t8_ok)
except Exception as e:
    print(f"  FAIL: {e}")
    results.append(False)


# ---------------------------------------------------------------------------
# Test 9: Bad race_date is rejected
# ---------------------------------------------------------------------------
section("Test 9: malformed race_date raises ValueError")
try:
    ts.get_track_status("CD", "5/8", "2026")
    print(f"  FAIL: should have raised on '5/8'")
    results.append(False)
except ValueError as e:
    print(f"  PASS: raised ValueError({e})")
    results.append(True)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
passed = sum(1 for r in results if r)
total = len(results)
print(f"Tests passed: {passed} / {total}")
print("=" * 60)
sys.exit(0 if passed == total else 1)
