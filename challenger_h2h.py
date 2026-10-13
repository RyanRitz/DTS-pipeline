"""Head-to-head: champion (performance_log.csv) vs challenger (../Challenger/predictions/*.csv) vs market.
Challenger probs are renormalized per race over horses that ran (scratches dropped), then prob_above/edge/gold
are recomputed with the champion's MornOddsAdj. Usage: python challenger_h2h.py [--all-scope] [--glob "../Challenger/predictions_noml/KEE_*_challenger_noML.csv"]"""
import sys, glob, re
import numpy as np, pandas as pd
from multirace_eval import payoffs

HERE = __import__("pathlib").Path(__file__).resolve().parent
log = pd.read_csv(HERE / "results/performance_log.csv")
log["pn"] = log["pn"].astype(str)
log["MornOddsAdj"] = log["value_prod"] / log["ProbToWin"] - 1          # value_prod=(ML+1)/(DTS+1), DTS+1=1/p
log["p_fin"] = 1 / (log["final_odds"] + 1); log["p_fin"] = log["p_fin"] / log.groupby(["date", "race"])["p_fin"].transform("sum")   # no-vig

rows = []
PAT = sys.argv[sys.argv.index("--glob")+1] if "--glob" in sys.argv else "../Challenger/predictions/KEE_*_challenger.csv"
print("challenger files:", PAT)
for f in sorted(glob.glob(str(HERE / PAT))):
    d = int(re.search(r"KEE_(\d{8})", f).group(1))
    c = pd.read_csv(f); c["pn"] = c["program"].astype(str)
    l = log[log["date"] == d].merge(c[["race", "pn", "ProbToWin"]].rename(columns={"ProbToWin": "p_ch"}),
                                    on=["race", "pn"], how="left")
    rows.append(l)
df = pd.concat(rows, ignore_index=True)
assert df["p_ch"].notna().all()
df["p_ch"] = df["p_ch"] / df.groupby(["date", "race"])["p_ch"].transform("sum")
df = df.rename(columns={"ProbToWin": "p_cp"})

def add_gates(df, p):
    g = df.sort_values(["date", "race", p], ascending=[True, True, False]).copy()
    g["rank_" + p] = g.groupby(["date", "race"]).cumcount() + 1
    g["pa_" + p] = g.groupby(["date", "race"])[p].cumsum() - g[p]
    g["edge_" + p] = (g["MornOddsAdj"] + 1) * g[p]            # (ML+1)/(DTS+1) = (ML+1)*p
    g["gold_" + p] = (g["edge_" + p] >= 1.5) & (g["pa_" + p] < 0.5)
    return g.sort_index()
df = add_gates(add_gates(df, "p_cp"), "p_ch")

scope = df if "--all-scope" in sys.argv else df[df["in_scope"] == 1]
R = scope.groupby(["date", "race"]).ngroups
print(f"{'ALL' if '--all-scope' in sys.argv else 'IN-SCOPE'}: {R} races, {len(scope)} runners\n")

def ll(p): return -np.mean(np.where(scope["won"] == 1, np.log(p.clip(1e-6)), np.log((1 - p).clip(1e-6))))
def wll(p):  # race-level log-loss of winner (conditional logit style)
    return -np.mean(np.log(scope.loc[scope["won"] == 1, p].clip(1e-6)))
print("== Headline ==")
print(f"{'':34s}{'Champion':>10s}{'Challenger':>12s}{'Market':>10s}{'ML-only':>10s}")
print(f"{'log-loss per runner':34s}{ll(scope['p_cp']):10.4f}{ll(scope['p_ch']):12.4f}{ll(scope['p_fin']):10.4f}{ll(scope['ml_novig']):10.4f}")
print(f"{'-log p(winner) per race':34s}{wll('p_cp'):10.4f}{wll('p_ch'):12.4f}{wll('p_fin'):10.4f}{wll('ml_novig'):10.4f}")
odds = lambda p: 1 / p - 1
print(f"{'corr(model odds, final odds)':34s}{np.corrcoef(odds(scope['p_cp']), scope['final_odds'])[0,1]:10.3f}{np.corrcoef(odds(scope['p_ch']), scope['final_odds'])[0,1]:12.3f}")
print(f"{'corr(model odds, ML adj)':34s}{np.corrcoef(odds(scope['p_cp']), scope['MornOddsAdj'])[0,1]:10.3f}{np.corrcoef(odds(scope['p_ch']), scope['MornOddsAdj'])[0,1]:12.3f}")
w = scope[scope["won"] == 1]
print(f"{'winners in top-50% prob mass':34s}{(w['pa_p_cp']<0.5).sum():7d}/{R}{(w['pa_p_ch']<0.5).sum():9d}/{R}")
half = scope.groupby(["date","race"])["pn"].transform("count") / 2
print(f"{'winners in positional top half':34s}{(w['rank_p_cp']<=half[w.index]).sum():7d}/{R}{(w['rank_p_ch']<=half[w.index]).sum():9d}/{R}")
print(f"{'top pick (rank 1) wins':34s}{(w['rank_p_cp']==1).sum():7d}/{R}{(w['rank_p_ch']==1).sum():9d}/{R}")
print(f"{'rank-1 agreement':34s}{'':10s}{((scope['rank_p_cp']==1)&(scope['rank_p_ch']==1)).sum():9d}/{R}")
print(f"{'sum p vs actual winners':34s}{scope['p_cp'].sum():10.1f}{scope['p_ch'].sum():12.1f}{scope['p_fin'].sum():10.1f}   actual {R}")

print("\n== Gold set (edge>=1.5 vs MornOddsAdj & prob_above<0.5) ==")
print(f"{'':12s}{'plays':>6s}{'wins':>6s}{'mod-exp':>8s}{'mkt-exp':>8s}{'$2 ROI':>8s}{'CLV':>6s}{'avg ML':>7s}{'avg fin':>8s}")
def gold_line(lbl, g, p):
    if len(g) == 0: print(f"{lbl:12s}{0:6d}"); return
    roi = (g.loc[g["won"]==1, "win_payout"].sum() - 2*len(g)) / (2*len(g))
    print(f"{lbl:12s}{len(g):6d}{int(g['won'].sum()):6d}{g[p].sum():8.1f}{g['p_fin'].sum():8.1f}{roi*100:7.1f}%{(g[p]/g['p_fin']).mean():6.2f}{g['MornOddsAdj'].mean():7.1f}{g['final_odds'].mean():8.1f}")
for name, p in [("Champion", "p_cp"), ("Challenger", "p_ch")]:
    g = scope[scope["gold_" + p]]
    gold_line(name, g, p)
    for s, sub in [("  turf", g[(g["Surface"]=="T")&(~g["RaceType"].isin(["M","S"]))]),
                   ("  dirt", g[(g["Surface"]=="D")&(~g["RaceType"].isin(["M","S"]))]),
                   ("  maiden", g[g["RaceType"].isin(["M","S"])])]:
        gold_line(s, sub, p)
    g2 = g[g["rank_" + p] == 2]; print(f"  rank-2 gold: {int(g2['won'].sum())}-for-{len(g2)} (mod-exp {g2[p].sum():.1f}, mkt-exp {g2['p_fin'].sum():.1f})")
both = scope[scope["gold_p_cp"] & scope["gold_p_ch"]]
print(f"Gold by BOTH: {len(both)} plays, {int(both['won'].sum())} wins; champion-only gold: {int((scope['gold_p_cp']&~scope['gold_p_ch']).sum())}, challenger-only: {int((~scope['gold_p_cp']&scope['gold_p_ch']).sum())}")

print("\n== Calibration by band ==")
bands = [(0,.1),(.1,.2),(.2,.3),(.3,.4),(.4,1)]
for name, p in [("Champion", "p_cp"), ("Challenger", "p_ch")]:
    print(f"{name}:  band        n   model   actual  market")
    for lo, hi in bands:
        b = scope[(scope[p] >= lo) & (scope[p] < hi)]
        if len(b): print(f"   {int(lo*100):3d}-{int(hi*100):3d}%  {len(b):5d}  {b[p].mean()*100:5.1f}%  {b['won'].mean()*100:5.1f}%  {b['p_fin'].mean()*100:5.1f}%")

print("\n== By rank ==")
for name, p in [("Champion", "p_cp"), ("Challenger", "p_ch")]:
    print(f"{name}:  rank    n  wins  mod-exp  mkt-exp")
    for r, lbl in [(1,"1"),(2,"2"),(3,"3"),(None,"4+")]:
        b = scope[scope["rank_"+p]==r] if r else scope[scope["rank_"+p]>=4]
        print(f"   {lbl:4s}{len(b):5d}{int(b['won'].sum()):6d}{b[p].sum():9.1f}{b['p_fin'].sum():9.1f}")

print("\n== Validation bet: top-50% mass + >=25% overlay vs FINAL (no-vig) ==")
for name, p in [("Champion", "p_cp"), ("Challenger", "p_ch")]:
    v = scope[(scope["pa_"+p] < 0.5) & (scope[p] / scope["p_fin"] >= 1.25)]
    gold_line(name, v, p)

print("\n== Short-priced winners (<4-1) outside top-50% mass ==")
for name, p in [("Champion", "p_cp"), ("Challenger", "p_ch")]:
    m = w[(w["final_odds"] < 4) & (w["pa_"+p] >= 0.5)]
    print(f"{name}: {len(m)} of {(w['final_odds']<4).sum()} winners <4-1 missed")

print("\n== Ryan's Daily Double ticket (cum-50% per leg, consecutive races, in-scope legs only) ==")
for name, p in [("Champion", "p_cp"), ("Challenger", "p_ch")]:
    n = hits = 0; mexp = kexp = cost = ret = 0.0; combos = 0
    for d, day in scope.groupby("date"):
        po = payoffs("KEE", d, "Daily Double")
        races = sorted(day["race"].unique())
        for r1 in races:
            r2 = r1 + 1
            if r2 not in races or r2 not in po: continue
            legs = []
            for r in (r1, r2):
                x = day[day["race"]==r].sort_values(p, ascending=False)
                legs.append(x[x["pa_"+p] < 0.5])
            n += 1
            hit = all((l["won"]==1).any() for l in legs)
            hits += hit
            mexp += np.prod([l[p].sum() for l in legs]); kexp += np.prod([l["p_fin"].sum() for l in legs])
            k = len(legs[0]) * len(legs[1]); combos += k
            rec = po[r2]; cost += rec["base"] * k
            if hit: ret += rec["payoff"]
    print(f"{name}: {n} tickets, {hits} hits, model-exp {mexp:.1f}, market-exp {kexp:.1f}, avg combos {combos/n:.1f}, cost ${cost:.0f}, return ${ret:.0f}, ROI {(ret-cost)/cost*100:+.0f}%")

print("\n== §4 decision-rule checks (challenger vs champion, this sample) ==")
gc, gh = scope[scope["gold_p_cp"]], scope[scope["gold_p_ch"]]
print(f"(a) log-loss: challenger {ll(scope['p_ch']):.4f} vs champion {ll(scope['p_cp']):.4f} -> {'PASS' if ll(scope['p_ch'])<ll(scope['p_cp']) else 'FAIL'}")
dev = lambda g,p: (g[p].sum()/max(g['won'].sum(),1)-1)*100
print(f"(b) gold-set model-exp vs actual: challenger {gh['p_ch'].sum():.1f} vs {int(gh['won'].sum())} ({dev(gh,'p_ch'):+.0f}%); champion {gc['p_cp'].sum():.1f} vs {int(gc['won'].sum())} ({dev(gc,'p_cp'):+.0f}%) -> {'PASS' if abs(dev(gh,'p_ch'))<=15 else 'FAIL'} (±15%)")
print(f"(c) containment (top-50% mass): challenger {(w['pa_p_ch']<0.5).sum()} vs champion {(w['pa_p_cp']<0.5).sum()} of {R} -> {'PASS' if (w['pa_p_ch']<0.5).sum()>=(w['pa_p_cp']<0.5).sum() else 'FAIL'}")
