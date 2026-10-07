#!/usr/bin/env python3
"""
multirace_eval.py — grade rolling multi-race tickets (Double, Pick 3, Pick 4...)
built from the model's top-N% in every leg.

  ticket = every horse with rank percentile <= --pct in each leg
  cost   = product(leg sizes) x base bet
  hit    = each leg's winning number(s) covered. Chart notation "2/8/10" in a
           leg means scratched 2 and 8 were moved onto the favorite (10), so
           ANY of those on the ticket counts as a winner for that leg.
  return = chart payoff for one base unit (ccf_results.py exotics CSV)

    python multirace_eval.py --wager "Daily Double"
    python multirace_eval.py --wager "Pick Three" --pct 0.5
    python multirace_eval.py --wager "Daily Double" --date 20261003 --detail
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
LEGS = {"dailydouble": 2, "pickthree": 3, "pickfour": 4, "pickfive": 5, "picksix": 6}


def norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", str(s).lower())


def payoffs(track: str, date: int, wager: str) -> dict[int, dict]:
    """ending race -> {base, payoff, legs:[set(winning numbers)], consolation}"""
    p = RES / f"{track}_{date}_exotics.csv"
    if not p.exists():
        return {}
    ex = pd.read_csv(p)
    ex = ex[ex["wager_type"].map(norm) == norm(wager)]
    out = {}
    nlegs = LEGS[norm(wager)]
    for _, r in ex.iterrows():
        wn = str(r["winning_numbers"]).strip()
        if re.search(r"[A-Za-z]", wn):
            continue                                    # named special wager
        legs = [set(x.split("/")) for x in wn.split("-")]
        if len(legs) != nlegs:
            continue
        rec = out.setdefault(int(r["race"]), {"base": float(r["bet_amount"]), "legs": legs,
                                              "payoff": 0.0, "consolation": 0.0})
        nc = int(r["number_correct"]) if pd.notna(r["number_correct"]) else 0
        if nc and nc < nlegs:
            rec["consolation"] = float(r["payoff"])
        else:
            rec["payoff"] = float(r["payoff"])
    return out


def leg_horses(x: pd.DataFrame, pct: float, value_only: bool = False, single: float = 0.0) -> set:
    """Pick the horses for one leg.
    base        : model top-pct by win probability
    value_only  : drop horses the market prices SHORTER than the model (value_ml < 1) —
                  the chalk the model doesn't like; never empties a leg (keeps rank 1)
    single      : if rank-1 prob >= single x rank-2 prob, use rank 1 alone
    """
    x = x.sort_values("rank")
    if single and len(x) > 1 and x["ProbToWin"].iloc[0] >= single * x["ProbToWin"].iloc[1]:
        return {str(x["pn"].iloc[0])}
    top = x[x["pct"] <= pct]
    if value_only:
        keep = top[top["value_ml"] >= 1.0]
        top = keep if len(keep) else top.head(1)
    return set(top["pn"].astype(str))


def grade_card(df: pd.DataFrame, track: str, date: int, wager: str, pct: float,
               value_only: bool = False, single: float = 0.0) -> pd.DataFrame:
    c = df[(df["track"] == track) & (df["date"] == date)]
    races = sorted(c["race"].unique())
    pay = payoffs(track, date, wager)
    n = LEGS[norm(wager)]
    rows = []
    for end, rec in sorted(pay.items()):
        legs = list(range(end - n + 1, end + 1))
        if any(l not in races for l in legs):
            continue
        sizes, hit_legs, tickets = [], 0, []
        for lg, winset in zip(legs, rec["legs"]):
            x = c[c["race"] == lg]
            top = leg_horses(x, pct, value_only, single)
            sizes.append(len(top))
            hit_legs += bool(top & winset)
            tickets.append("/".join(sorted(top, key=lambda s: int(re.sub(r"\D", "", s) or 0))))
        combos = 1
        for s in sizes:
            combos *= s
        cost = combos * rec["base"]
        hit = hit_legs == n
        ret = rec["payoff"] if hit else 0.0
        # consolations (n-1 of n): one per losing horse in a leg when every other leg is hit
        cons = 0
        if rec["consolation"] and hit_legs >= n - 1:
            for lg, winset, sz in zip(legs, rec["legs"], sizes):
                x = c[c["race"] == lg]
                top = leg_horses(x, pct, value_only, single)
                others_hit = hit_legs - bool(top & winset) == n - 1
                if others_hit:
                    cons += len(top - winset)
            ret += cons * rec["consolation"]
        rows.append(dict(date=date, seq=f"R{legs[0]}-R{legs[-1]}", legs=" x ".join(map(str, sizes)),
                         combos=combos, base=rec["base"], cost=round(cost, 2), legs_hit=hit_legs,
                         hit=hit, payoff=round(ret, 2), net=round(ret - cost, 2),
                         winners="-".join("/".join(sorted(w)) for w in rec["legs"]),
                         ticket=" | ".join(tickets)))
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wager", default="Daily Double")
    ap.add_argument("--log", default=str(RES / "performance_log.csv"))
    ap.add_argument("--track", default="KEE")
    ap.add_argument("--date", type=int)
    ap.add_argument("--pct", type=float, default=0.5)
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--value-only", action="store_true", help="drop horses with ML shorter than model price from each leg")
    ap.add_argument("--single", type=float, default=0.0, help="single rank-1 when its prob >= K x rank-2 prob (e.g. 1.5)")
    a = ap.parse_args()
    df = pd.read_csv(a.log)
    df["pn"] = df["pn"].astype(str)
    cards = df[["track", "date"]].drop_duplicates().sort_values("date")
    if a.date:
        cards = cards[cards["date"] == a.date]
    g = pd.concat([grade_card(df, t, d, a.wager, a.pct, a.value_only, a.single) for t, d in cards.itertuples(index=False)], ignore_index=True)
    pd.set_option("display.width", 240)
    cols = ["date", "seq", "legs", "combos", "cost", "legs_hit", "hit", "payoff", "net", "winners"] + (["ticket"] if a.detail else [])
    print(g[cols].to_string(index=False))
    n, hits, cost, ret = len(g), int(g["hit"].sum()), g["cost"].sum(), g["payoff"].sum()
    tag = (" value-only" if a.value_only else "") + (f" single>={a.single}x" if a.single else "")
    print(f"\n{a.wager} @ model top-{int(a.pct*100)}%{tag}: {n} tickets, {hits} hit ({100*hits/n:.0f}%), "
          f"cost ${cost:,.2f}, returned ${ret:,.2f}, ROI {100*(ret-cost)/cost:+.1f}% | mean ticket ${cost/n:.2f}")
    by = g.groupby("date").agg(tickets=("hit", "size"), hits=("hit", "sum"), cost=("cost", "sum"), ret=("payoff", "sum"))
    by["roi_%"] = (100 * (by.ret - by.cost) / by.cost).round(1)
    print(by.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
