#!/usr/bin/env python3
"""
ccf_results.py — BRISnet CCF (Comprehensive Chart File) -> results CSVs.
==========================================================================
Turns a chart pack (zip of TRKMMDDYYYY.1 .. .6, or the unzipped files in a
RESULTS dir) into the two CSVs the Model Performance thread consumes:

  results/{TRK}_{YYYYMMDD}_results.csv   one row per horse that RAN
      race, program, finish, final_odds, win_payout        <- evaluate_card.py contract
      + place_payout, show_payout, horse_name, post, favorite, dq, dq_placing,
        beaten_lengths, trip_comment, surface, race_type, field_size, track_cond

  results/{TRK}_{YYYYMMDD}_exotics.csv   one row per exotic/multi-race payout
      race, wager_type, bet_amount, payoff, number_correct, winning_numbers,
      pool, carryover
      (Pick 5 has a 4-of-5 consolation row AND the 5-of-5 row; Super High Five
       / Pick Six carry a carryover when nobody hit.)

Field layout is taken verbatim from the SAS read-in (BTSM\2.sas, macro ryan2)
so the Python and SAS worlds agree on what every column means.

USAGE
    python ccf_results.py DRF_Downloads/kee10032026c.zip
    python ccf_results.py ../KEE/RAW_DATA/RESULTS/2026 KEE 20261002
    python ccf_results.py <zip|dir> [TRK YYYYMMDD] [--out results/]

A horse is "ran" if it has a finish position > 0 in the .2 record. Scratches
never appear in the chart, so anything in the DRF but not here is a scratch —
exactly the contract evaluate_card.py expects.
"""
from __future__ import annotations
import argparse, csv, io, re, sys, zipfile
from pathlib import Path

# --- .1 race header (2.sas RES1) ------------------------------------------
R1 = ["track","date","race","dayflag","distance","dist_unit","dist_about","surface",
      "surface2","r1","aw_flag","chute","bris_racetype","eqb_racetype","grade",
      "agesex","restrict","statebred","raceclass","breed","country","purse","totvalue",
      "r2","r3","r4","r5","max_clm","r6","cond1","cond2","cond3","cond4","cond5",
      "r7","r8","field_size","track_cond","frac1","frac2","frac3","frac4","frac5",
      "final_time"]
# --- .2 horse record (2.sas RES2) -----------------------------------------
R2 = ["track","date","race","dayflag","horse_name","foreign","statebred","post",
      "program","birth_year","breed","coupling","jockey","jky_l","jky_f","jky_m","r999",
      "trainer","trn_l","trn_f","trn_m","trip_comment","r99","owner","own_f","own_m",
      "claim_price","med","equip","earnings","odds","nonbet","favorite","r98","r9",
      "dq","dq_placing","weight","corr_wt","overwt","claimed","clm_trn","clm_trn_l",
      "clm_trn_f","clm_trn_m","r97","clm_own","clm_own_l","clm_own_f","clm_own_m",
      "win_payout","place_payout","show_payout","r96","call_start","call_1","call_2",
      "call_3","call_stretch","finish","money_pos",
      "lo_start","lo_1","lo_2","lo_3","lo_stretch","lo_finish",
      "bhd_start","bhd_1","bhd_2","bhd_3","bhd_stretch","beaten_lengths"]
# --- .3 W/P/S payoffs ------------------------------------------------------
R3 = ["track","date","race","dayflag","horse_name","foreign","statebred","program",
      "win_payoff","place_payoff","show_payoff"]
# --- .4 exotics -------------------------------------------------------------
R4 = ["track","date","race","dayflag","wager_type","bet_amount","payoff",
      "number_correct","winning_numbers","pool","carryover"]


def _rows(text: str, names: list[str]) -> list[dict]:
    out = []
    for rec in csv.reader(io.StringIO(text)):
        if not rec or not rec[0].strip():
            continue
        d = {n: (rec[i].strip() if i < len(rec) else "") for i, n in enumerate(names)}
        out.append(d)
    return out


def _num(s, default=None):
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _int(s, default=0):
    v = _num(s)
    return int(v) if v is not None else default


def load_pack(src: Path, track: str | None, date: str | None) -> dict[int, str]:
    """Return {ext_number: text} for the .1..6 files of one card."""
    files: dict[int, str] = {}
    if src.is_file() and zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            for n in z.namelist():
                m = re.search(r"\.(\d)$", n)
                if m:
                    files[int(m.group(1))] = z.read(n).decode("latin1")
    elif src.is_dir():
        if not (track and date):
            sys.exit("directory source needs TRK and YYYYMMDD")
        stem = f"{track.upper()}{date[4:8]}{date[:4]}"
        for k in range(1, 7):
            p = src / f"{stem}.{k}"
            if p.exists():
                files[k] = p.read_text(encoding="latin1")
    else:
        sys.exit(f"not a zip or directory: {src}")
    if 2 not in files:
        sys.exit(f"no .2 (horse) record found in {src}")
    return files


def parse(files: dict[int, str]):
    r1 = {int(r["race"]): r for r in _rows(files.get(1, ""), R1)}
    r2 = _rows(files[2], R2)
    r3 = {(int(r["race"]), r["program"]): r for r in _rows(files.get(3, ""), R3)}
    r4 = _rows(files.get(4, ""), R4)

    # identity from file CONTENTS, never the filename
    track = r2[0]["track"].strip()
    date = r2[0]["date"].strip()

    results = []
    for h in r2:
        race = int(h["race"])
        fin = _int(h["finish"])
        if fin <= 0:            # never ran (chart lists only starters, but be safe)
            continue
        pn = h["program"].strip()
        wps = r3.get((race, pn), {})
        win = _num(h["win_payout"]) or _num(wps.get("win_payoff"))
        plc = _num(h["place_payout"]) or _num(wps.get("place_payoff"))
        shw = _num(h["show_payout"]) or _num(wps.get("show_payoff"))
        hdr = r1.get(race, {})
        results.append({
            "race": race,
            "program": pn,
            "finish": fin,
            "final_odds": _num(h["odds"]),
            "win_payout": win if (fin == 1 and win) else "",
            "place_payout": plc or "",
            "show_payout": shw or "",
            "horse_name": h["horse_name"],
            "post": _int(h["post"]),
            "favorite": _int(h["favorite"]),
            "dq": h["dq"],
            "dq_placing": _int(h["dq_placing"]) or "",
            "beaten_lengths": _num(h["beaten_lengths"], ""),
            "trip_comment": h["trip_comment"],
            "surface": hdr.get("surface", ""),
            "race_type": hdr.get("bris_racetype", ""),
            "field_size": _int(hdr.get("field_size")) or "",
            "track_cond": hdr.get("track_cond", ""),
        })
    results.sort(key=lambda r: (r["race"], r["finish"], r["program"]))

    exotics = []
    for e in r4:
        exotics.append({
            "race": int(e["race"]),
            "wager_type": re.sub(r"\s+", " ", e["wager_type"]).strip(),
            "bet_amount": _num(e["bet_amount"], ""),
            "payoff": _num(e["payoff"], ""),
            "number_correct": _int(e["number_correct"]) or "",
            "winning_numbers": e["winning_numbers"],
            "pool": _num(e["pool"], ""),
            "carryover": _num(e["carryover"], ""),
        })
    return track, date, results, exotics


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="BRISnet CCF chart -> results + exotics CSVs")
    ap.add_argument("src", help="chart zip, or RESULTS directory holding TRKMMDDYYYY.1-6")
    ap.add_argument("track", nargs="?")
    ap.add_argument("date", nargs="?", help="YYYYMMDD (with a directory src)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    a = ap.parse_args()

    files = load_pack(Path(a.src), a.track, a.date)
    track, date, results, exotics = parse(files)
    if a.track and a.track.upper() != track:
        print(f"WARNING: filename says {a.track} but chart contents say {track} — using {track}")
    if a.date and a.date != date:
        print(f"WARNING: argument date {a.date} but chart contents say {date} — using {date}")

    out = Path(a.out)
    rp = out / f"{track}_{date}_results.csv"
    ep = out / f"{track}_{date}_exotics.csv"
    write_csv(rp, results)
    if exotics:
        write_csv(ep, exotics)

    races = sorted({r["race"] for r in results})
    print(f"{track} {date}: {len(races)} races, {len(results)} starters -> {rp}")
    for rc in races:
        w = next(r for r in results if r["race"] == rc and r["finish"] == 1)
        n = sum(1 for r in results if r["race"] == rc)
        print(f"  R{rc:>2}: #{w['program']:<3} {w['horse_name']:<24} {w['final_odds']:>6}-1  "
              f"${w['win_payout']:<6} field {n}")
    if exotics:
        kinds = sorted({e["wager_type"] for e in exotics})
        print(f"{len(exotics)} exotic payouts -> {ep}\n  types: {', '.join(kinds)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
