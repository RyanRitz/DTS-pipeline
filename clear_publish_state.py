"""
clear_publish_state.py — surgically clear publish state so a card re-posts.

run_pipeline.py has NO force flag; publishing is gated by pipeline_state.json
(`published[YYYYMMDD][TRACK]`). Deleting an entry makes the pipeline treat that
card as never-published and re-post it.

Prefer this over clearing a whole date range — it only touches what you name.

DRY-RUN BY DEFAULT.

    python clear_publish_state.py --card 20260719:DMR --card 20260721:DEL
    python clear_publish_state.py --card 20260719:DMR --apply
"""
from __future__ import annotations
import sys, json, shutil
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
STATE = _HERE / "pipeline_state.json"
APPLY = "--apply" in sys.argv

cards: list[tuple[str, str]] = []
argv = sys.argv[1:]
for i, a in enumerate(argv):
    if a == "--card" and i + 1 < len(argv):
        raw = argv[i + 1]
        if ":" not in raw:
            print(f"bad --card {raw!r}; expected YYYYMMDD:TRACK")
            sys.exit(2)
        d, t = raw.split(":", 1)
        cards.append((d.strip(), t.strip().upper()))

if not cards:
    print(__doc__)
    sys.exit(2)

state = json.loads(STATE.read_text(encoding="utf-8"))
pub = state.get("published", {})

print(f"State file: {STATE}")
print("MODE:", "APPLY" if APPLY else "DRY-RUN (no changes)")
print()

hits = []
for date, track in cards:
    entry = pub.get(date, {}).get(track)
    if entry is None:
        print(f"  {date} {track:5s} -> not present (nothing to clear)")
        continue
    kinds = ", ".join(sorted(entry.keys()))
    print(f"  {date} {track:5s} -> WILL CLEAR ({kinds})")
    hits.append((date, track))

if not hits:
    print("\nNothing to do.")
    sys.exit(0)

if not APPLY:
    print("\nDRY-RUN: nothing changed. Re-run with --apply.")
    sys.exit(0)

bak = STATE.with_name(
    f"pipeline_state.backup-{datetime.now():%Y%m%d-%H%M%S}.json")
shutil.copy2(STATE, bak)
print(f"\nBacked up -> {bak.name}")

for date, track in hits:
    del pub[date][track]
    if not pub[date]:
        del pub[date]
    print(f"  cleared {date} {track}")

state["published"] = pub
STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
print("\nState written. Next run_pipeline.py will re-post those cards.")
