"""Champion-only in-market summary for any logged track. Usage: python champ_summary.py results/performance_log_SAR.csv SAR [mindate]"""
import sys, pandas as pd, numpy as np
from multirace_eval import payoffs
log=pd.read_csv(sys.argv[1]); TR=sys.argv[2]; mind=int(sys.argv[3]) if len(sys.argv)>3 else 0
log=log[log.date>=mind].copy(); log['pn']=log['pn'].astype(str)
log['MornOddsAdj']=log['value_prod']/log['ProbToWin']-1
log['p_fin']=1/(log.final_odds+1); log['p_fin']=log.p_fin/log.groupby(['date','race']).p_fin.transform('sum')
p='ProbToWin'; g=log.sort_values(['date','race',p],ascending=[True,True,False])
log['pa']=(g.groupby(['date','race'])[p].cumsum()-g[p]).reindex(log.index); log['rk']=(g.groupby(['date','race']).cumcount()+1).reindex(log.index)
log['top']=log.pa<0.5; log['goldx']=((log.MornOddsAdj+1)*log[p]>=1.5)&log.top
log['seg']=np.where(log.RaceType.isin(['M','S']),'maiden',np.where(log.Surface.str.upper()=='T','turf','dirt'))
s=log[log.in_scope==1].copy(); R=s.groupby(['date','race']).ngroups; w=s[s.won==1]
def ll(x,c): return -np.mean(np.where(x.won==1,np.log(x[c].clip(1e-6)),np.log((1-x[c]).clip(1e-6))))
def roi(g): return ((g.won*2*(g.final_odds+1)).sum()/(2*len(g))-1)*100 if len(g) else np.nan
print(f"{TR}: {s.date.nunique()} cards, {R} in-scope races, {len(s)} runners  (all logged: {log.groupby(['date','race']).ngroups} races)")
print(f"\n== Headline ==            Champion   Market  ML-only\nlog-loss/runner           {ll(s,p):.4f}   {ll(s,'p_fin'):.4f}   {ll(s,'ml_novig'):.4f}")
half=s.groupby(['date','race']).pn.transform('size')//2+ (s.groupby(['date','race']).pn.transform('size')%2)
print(f"winners in top-50% mass   {int(w.top.sum())}/{R} ({w.top.mean():.0%})\nwinners in pos. top half  {int((w.rk<=half[w.index]).sum())}/{R}\ntop pick wins             {int((w.rk==1).sum())}/{R} ({(w.rk==1).mean():.0%})   favorite wins {int(w.favorite.sum()) if 'favorite' in w else '?'}/{R}")
print(f"sum p vs winners          {s[p].sum():.1f} vs {R}")
print("\n== Gold (edge>=1.5 vs ML adj, top-50% mass) ==\n             plays wins mod-exp mkt-exp  $2 ROI   CLV avg ML avg fin")
def gl(l,g):
    if len(g): print(f"  {l:10s}{len(g):5d}{int(g.won.sum()):5d}{g[p].sum():8.1f}{g.p_fin.sum():8.1f}{roi(g):8.1f}%{(g[p]/g.p_fin).mean():6.2f}{g.MornOddsAdj.mean():7.1f}{g.final_odds.mean():8.1f}")
G=s[s.goldx]; gl('all',G)
for k in ['dirt','turf','maiden']: gl(k,G[G.seg==k])
gl('rank1',G[G.rk==1]); gl('rank2',G[G.rk==2]); gl('rank3+',G[G.rk>=3])
for m in sorted((s.date//100%100).unique()): gl(f"month {m:02d}",G[(G.date//100%100)==m])
print("\n== Validation bet (top-50% mass, >=25% over FINAL) ==")
V=s[s.top&(s[p]/s.p_fin>=1.25)]; gl('all',V)
for k in ['dirt','turf','maiden']: gl(k,V[V.seg==k])
print("\n== Calibration ==\n   band      n  model  actual  market")
for lo,hi in [(0,.1),(.1,.2),(.2,.3),(.3,.4),(.4,1)]:
    b=s[(s[p]>=lo)&(s[p]<hi)]
    if len(b): print(f"  {int(lo*100):3d}-{int(hi*100):3d}% {len(b):5d}  {b[p].mean()*100:5.1f}%  {b.won.mean()*100:5.1f}%  {b.p_fin.mean()*100:5.1f}%")
print("\n== By rank ==\n  rank    n  wins  mod-exp  mkt-exp   ROI")
for k in [1,2,3]:
    b=s[s.rk==k]; print(f"  {k:4d}{len(b):5d}{int(b.won.sum()):6d}{b[p].sum():9.1f}{b.p_fin.sum():9.1f}{roi(b):7.1f}%")
b=s[s.rk>=4]; print(f"  4+  {len(b):5d}{int(b.won.sum()):6d}{b[p].sum():9.1f}{b.p_fin.sum():9.1f}{roi(b):7.1f}%")
print("\n== By segment (log-loss, top pick, containment) ==")
for k in ['dirt','turf','maiden']:
    x=s[s.seg==k]; Rk=x.groupby(['date','race']).ngroups; wk=x[x.won==1]
    print(f"  {k:7s} {Rk:4d} races  LL {ll(x,p):.4f} vs mkt {ll(x,'p_fin'):.4f}   top pick {int((wk.rk==1).sum())}/{Rk}   top-50% {int(wk.top.sum())}/{Rk}")
print("\n== Multi-race, cum-50% legs, in-scope consecutive, chart payoffs ==")
for wager,nl in [('Daily Double',2),('Pick Three',3)]:
    n=hits=0; kexp=cost=ret=combos=0.0; par=0.0
    for d,day in s.groupby('date'):
        po=payoffs(TR,d,wager); races=sorted(day.race.unique())
        for r1 in races:
            rs=[r1+i for i in range(nl)]
            if any(r not in races for r in rs) or rs[-1] not in po: continue
            legs=[day[(day.race==r)&day.top] for r in rs]; n+=1; hit=all((l.won==1).any() for l in legs); hits+=hit
            kexp+=np.prod([l.p_fin.sum() for l in legs]); k=int(np.prod([len(l) for l in legs])); combos+=k; rec=po[rs[-1]]; cost+=rec['base']*k
            if hit: ret+=rec['payoff']
    if n: print(f"  {wager:13s} {n:3d} tix {hits:3d} hits ({hits/n:4.0%})  mkt-exp {kexp:5.1f}  avg combos {combos/n:5.1f}  cost ${cost:6.0f}  return ${ret:6.0f}  ROI {(ret-cost)/cost*100:+5.0f}%")
