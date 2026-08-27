"""
verify_jockey_bars.py — QA tool for race-day jockey-change JKY bars.

Prints, for each race that has a jockey change today, every horse's rider,
current-meet win count, race-average meet-wins, and the JKY bar BEFORE (as
carded) vs AFTER (with the change applied) — so you can eyeball the math
behind what the FINAL sheet renders. Uses the SAME jockey_bar_reindex module
generate_pdf uses, so the AFTER column is exactly what the sheet shows.

    .venv\\Scripts\\python.exe verify_jockey_bars.py DEL 20260827

Run on the machine with the DRF + Equibase network (the desktop).
"""
import sys, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
import ingest_drf, features
import jockey_bar_reindex as J
from scratches import get_jockey_changes

def clean_prog(v):
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return str(v).strip()

def bar_of(jck):  # jckcm2_sarm -> 0..100 fixed scale
    return float(np.clip((jck - 1.0) / (20.25 - 1.0) * 100, 0, 100).round())

def main():
    track = sys.argv[1].upper()
    race_date = sys.argv[2]                     # YYYYMMDD
    year, mmdd = race_date[:4], race_date[4:]
    drf_path = f"DRF_Downloads/{race_date}_{track}_DRS.DRF"
    print(f"DRF: {drf_path}")
    fe = features.engineer_features(ingest_drf.load_drf(drf_path, track, mmdd, year)).copy()
    fe["_prog"] = fe["ProgramNumberifavailable"].apply(clean_prog)
    fe["_race"] = pd.to_numeric(fe["Race"], errors="coerce")

    changes = get_jockey_changes(track, mmdd, year)
    print(f"\nEquibase jockey changes for {track} {race_date}: {len(changes)}")
    for c in changes:
        print(f"  R{c['race']} #{c['program']} {c['horse']} -> {c['jockey']}")
    if not changes:
        print("No jockey changes today — nothing to verify.")
        return

    by_race = {}
    for c in changes:
        by_race.setdefault(int(c["race"]), {})[clean_prog(c["program"])] = c["jockey"]

    for r, chg in sorted(by_race.items()):
        g = fe[fe["_race"] == r].copy()
        std = float(pd.to_numeric(g["xjwins_std"], errors="coerce").dropna().iloc[0])
        raw_before = pd.to_numeric(g["JockeyWinsCurrentMeet"], errors="coerce")
        avg_before = raw_before.mean()
        bar_before = J.reindex_race_bars(g, {}, prog_col="_prog")

        subs, blanks = {}, []
        resolved = {}
        for p, name in chg.items():
            rawwins, matched = J.resolve_new_rider_raw(name, fe)
            if rawwins is None:
                blanks.append(p); resolved[p] = (name, None, None)
            else:
                subs[p] = rawwins; resolved[p] = (name, rawwins, matched)
        bar_after = J.reindex_race_bars(g, subs, blanks=blanks, prog_col="_prog")
        # field avg AFTER (resolved subs swapped in; blanks keep old raw)
        raw_after = raw_before.copy()
        for p, w in subs.items():
            raw_after[g["_prog"] == p] = w
        avg_after = raw_after.mean()

        print(f"\n{'='*76}\nRACE {r}  (card-wide std={std:.3f})")
        print(f"  field avg meet-wins:  before={avg_before:.2f}   after={avg_after:.2f}")
        for p, (name, rw, matched) in resolved.items():
            if rw is None:
                print(f"  change #{p}: '{name}' -> NOT matched on card -> neutral bar 27")
            else:
                print(f"  change #{p}: '{name}' -> matched {matched!r}, {rw:.0f} meet-wins (borrowed)")
        print(f"  {'#':>3} {'rider (carded)':22} {'wins':>5} {'BAR<':>5} {'BAR>':>5} {'chg':>4}")
        for i in g.sort_values("_prog").index:
            p = g.at[i, "_prog"]
            rider = str(g.at[i, "TodaysJockey"])[:22]
            w = raw_before[i]
            b0, b1 = bar_before[i], bar_after[i]
            flag = "  *" if p in chg else ""
            wtxt = "" if pd.isna(w) else f"{w:.0f}"
            print(f"  {p:>3} {rider:22} {wtxt:>5} {b0:>5.0f} {b1:>5.0f} {flag:>4}")

if __name__ == "__main__":
    main()
