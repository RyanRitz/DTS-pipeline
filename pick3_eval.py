#!/usr/bin/env python3
"""
pick3_eval.py — grade rolling Pick 3 tickets built from the model's top-50%.

For every rolling Pick 3 (races r, r+1, r+2) on a logged card, the ticket is
"every horse in the model's top-50% by win probability" in each leg.
  cost   = (#leg1 x #leg2 x #leg3) x base bet
  hit    = all three winners were in their leg's top-50%
  return = chart payoff for one base unit (from results/{TRK}_{date}_exotics.csv)

Reads results/performance_log.csv (which already has pct = rank percentile and
finish) and the exotics CSVs written by ccf_results.py. Prints one row per
Pick 3 and a rolling summary. Does not touch the models.

    python pick3_eval.py                      # all cards in the log
    python pick3_eval.py --date 20261002      # one card
    python pick3_eval.py --example 20261002 1 # show the R1-R3 ticket worked out
    python pick3_eval.py --pct 0.5            # leg = top-50% (default); try 0.34, 0.67...
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
RES = HERE / "results"


def load_log(path: Path, pct: float) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["won"] = df["won"].astype(bool)
    df["inleg"] = df["pct"] <= pct
    df["pn"] = df["pn"].astype(str)
    return df


def p3_payoffs(track: str, date: int) -> dict[int, tuple[float, float, str]]:
    """race -> (base, payoff, winning_numbers) for the standard rolling Pick 3 ending in that race."""
    p = RES / f"{track}_{date}_exotics.csv"
    out = {}
    if not p.exists():
        return out
    ex = pd.read_csv(p)
    ex = ex[ex["wager_type"].str.replace(" ", "").str.lower() == "pickthree"]
    for _, r in ex.iterrows():
        wn = str(r["winning_numbers"])
        if re.search(r"[A-Za-z]", wn):          # named/special P3 (e.g. "KEE TURF P3 ...") — skip
            continue
        out[int(r["race"])] = (float(r["bet_amount"]), float(r["payoff"]), wn)
    return out


def grade_card(df: pd.DataFrame, track: str, date: int) -> pd.DataFrame:
    c = df[(df["track"] == track) & (df["date"] == date)]
    races = sorted(c["race"].unique())
    pay = p3_payoffs(track, date)
    rows = []
    for r in races:
        legs = [r, r + 1, r + 2]
        if legs[2] not in races or (r + 2) not in pay:
            continue
        sizes, hits, winners, legtxt = [], [], [], []
        for lg in legs:
            x = c[c["race"] == lg]
            top = x[x["inleg"]].sort_values("rank")
            w = x[x["won"]]
            sizes.append(len(top))
            hits.append(bool(len(w) and w["inleg"].all()))
            winners.append(w["pn"].iloc[0] if len(w) else "?")
            legtxt.append("/".join(top["pn"].tolist()))
        base, payoff, wn = pay[r + 2]
        combos = sizes[0] * sizes[1] * sizes[2]
        cost = combos * base
        hit = all(hits)
        ret = payoff if hit else 0.0
        rows.append(dict(date=date, track=track, p3=f"R{r}-R{r+2}", legs=" x ".join(map(str, sizes)),
                         combos=combos, base=base, cost=round(cost, 2), hit=hit,
                         payoff=payoff if hit else 0.0, net=round(ret - cost, 2),
                         winners="-".join(winners), ticket=" | ".join(legtxt),
                         legs_hit=sum(hits)))
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(RES / "performance_log.csv"))
    ap.add_argument("--track", default="KEE")
    ap.add_argument("--date", type=int)
    ap.add_argument("--pct", type=float, default=0.5)
    ap.add_argument("--example", nargs=2, metavar=("DATE", "RACE"))
    a = ap.parse_args()
    df = load_log(Path(a.log), a.pct)

    if a.example:
        d, r = int(a.example[0]), int(a.example[1])
        c = df[(df["track"] == a.track) & (df["date"] == d)]
        pay = p3_payoffs(a.track, d)
        print(f"\n=== {a.track} {d} Pick 3, races {r}-{r+2}: ticket = model top-{int(a.pct*100)}% in each leg ===\n")
        combos = 1
        for lg in (r, r + 1, r + 2):
            x = c[c["race"] == lg].sort_values("rank")
            x = x.assign(prob=x["ProbToWin"].round(3), ML=(1 / x["ml_novig"] - 1).round(1))
            print(f"Race {lg}  (field {len(x)})")
            for _, h in x.iterrows():
                tag = "USE " if h["inleg"] else "    "
                win = "  <-- WON" if h["won"] else ""
                print(f"  {tag}#{h['pn']:<3} {h['HorseName']:<22} prob {h['prob']:.3f}  ML {h['ML']:>5}-1  fin {int(h['finish']) if pd.notna(h['finish']) else '-':>2}{win}")
            n = int(x["inleg"].sum()); combos *= n
            print(f"  -> {n} horses in leg\n")
        base, payoff, wn = pay.get(r + 2, (None, None, None))
        print(f"combos = {combos}  x ${base} base = ${combos*base:.2f} ticket")
        print(f"chart: ${base} Pick 3 ({wn}) paid ${payoff}")
        g = grade_card(df, a.track, d); row = g[g["p3"] == f"R{r}-R{r+2}"].iloc[0]
        print(f"result: {'HIT' if row.hit else 'MISS'} ({row.legs_hit}/3 legs)  net ${row.net:+.2f}")
        return 0

    cards = df[["track", "date"]].drop_duplicates().sort_values("date")
    if a.date:
        cards = cards[cards["date"] == a.date]
    allg = pd.concat([grade_card(df, t, d) for t, d in cards.itertuples(index=False)], ignore_index=True)
    pd.set_option("display.width", 220)
    show = ["date", "p3", "legs", "combos", "cost", "legs_hit", "hit", "payoff", "net", "winners"]
    print(allg[show].to_string(index=False))
    n, hits = len(allg), int(allg["hit"].sum())
    cost, ret = allg["cost"].sum(), allg["payoff"].sum()
    print(f"\nRolling Pick 3 @ top-{int(a.pct*100)}%: {n} tickets, {hits} hit ({100*hits/n:.0f}%), "
          f"cost ${cost:,.2f}, returned ${ret:,.2f}, ROI {100*(ret-cost)/cost:+.1f}%  "
          f"| mean ticket ${cost/n:.2f}, mean combos {allg['combos'].mean():.0f}")
    print("legs hit distribution:", allg["legs_hit"].value_counts().sort_index().to_dict())
    by = allg.groupby("date").agg(tickets=("hit", "size"), hits=("hit", "sum"), cost=("cost", "sum"), ret=("payoff", "sum"))
    by["roi_%"] = (100 * (by.ret - by.cost) / by.cost).round(1)
    print("\n" + by.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
