"""Parlay vs pool: for every in-scope consecutive sequence (Double, Pick 3) Oct 2-21, compare the pool payoff per $1 to a win parlay of
the leg winners (product of $2 win payouts / 2). Reported for ALL sequences (not just model hits) and for model-ticket hits."""
import pandas as pd, numpy as np, glob, re
from multirace_eval import payoffs
log=pd.read_csv('results/performance_log.csv'); log['pn']=log['pn'].astype(str)
log['p_fin']=1/(log.final_odds+1); log['p_fin']=log.p_fin/log.groupby(['date','race']).p_fin.transform('sum')
rows=[]
for f in sorted(glob.glob('../Challenger/predictions_sastree/KEE_*_sastree.csv')):
    d=int(re.search(r'KEE_(\d{8})',f).group(1)); c=pd.read_csv(f); c['pn']=c['program'].astype(str)
    rows.append(log[log.date==d].merge(c[['race','pn','ProbToWin']].rename(columns={'ProbToWin':'p_ch'}),on=['race','pn']))
df=pd.concat(rows,ignore_index=True); df['p_ch']=df.p_ch/df.groupby(['date','race']).p_ch.transform('sum'); df=df.rename(columns={'ProbToWin':'p_cp'})
for p in ['p_cp','p_ch']:
    g=df.sort_values(['date','race',p],ascending=[True,True,False]); df['pa_'+p]=(g.groupby(['date','race'])[p].cumsum()-g[p]).reindex(df.index)
df['top_cp']=df.pa_p_cp<0.5; df['top_ch']=df.pa_p_ch<0.5; df['top_un']=df.top_cp|df.top_ch
scope=df[(df.in_scope==1)&(df.date!=20261011)]
out=[]
for wager,nl in [('Daily Double',2),('Pick Three',3)]:
    for d,day in scope.groupby('date'):
        po=payoffs('KEE',d,wager); races=sorted(day.race.unique())
        for r1 in races:
            rs=[r1+i for i in range(nl)]
            if any(r not in races for r in rs) or rs[-1] not in po: continue
            legs=[day[day.race==r] for r in rs]; wins=[l[l.won==1] for l in legs]
            if any(len(w)!=1 for w in wins): continue
            wp=[float(w.win_payout.iloc[0]) for w in wins]
            if any(np.isnan(x) or x<=0 for x in wp): continue
            parlay=np.prod([x/2 for x in wp]); rec=po[rs[-1]]; pool=rec['payoff']/rec['base']
            modp=np.prod([float(w.p_cp.iloc[0]) for w in wins]); mkt=np.prod([float(w.p_fin.iloc[0]) for w in wins])
            out.append(dict(wager=wager,date=d,end=rs[-1],winners='-'.join(w.pn.iloc[0] for w in wins),parlay=parlay,pool=pool,ratio=pool/parlay,
                 hit_cp=all(bool(w.top_cp.iloc[0]) for w in wins),hit_ch=all(bool(w.top_ch.iloc[0]) for w in wins),hit_un=all(bool(w.top_un.iloc[0]) for w in wins),
                 overlay=modp/mkt, fav=all(bool(w.favorite.iloc[0]) for w in wins) if 'favorite' in day else np.nan))
r=pd.DataFrame(out); r.to_csv('results/parlay_vs_pool_oct.csv',index=False)
TO={'Daily Double':(0.85/0.84**2),'Pick Three':(0.78/0.84**3)}
for w,x in r.groupby('wager'):
    print(f"\n== {w}: {len(x)} sequences (all in-scope, every outcome)  — takeout-only ratio would be {TO[w]:.2f} ==")
    def line(l,g):
        if len(g): print(f"  {l:26s} n={len(g):3d}  mean ratio {g.ratio.mean():5.2f}  median {g.ratio.median():5.2f}  pool>parlay {(g.ratio>1).mean():4.0%}  pool>takeout-par {(g.ratio>TO[w]).mean():4.0%}   avg pool ${g.pool.mean():7.1f}  avg parlay ${g.parlay.mean():7.1f}")
    line('all',x); line('champion ticket hit',x[x.hit_cp]); line('tree ticket hit',x[x.hit_ch]); line('union ticket hit',x[x.hit_un]); line('no model hit',x[~x.hit_un])
    line('pool pay < $20',x[x.pool<20]); line('pool pay $20-100',x[(x.pool>=20)&(x.pool<100)]); line('pool pay $100-500',x[(x.pool>=100)&(x.pool<500)]); line('pool pay $500+',x[x.pool>=500])
    line('model overlay >=1.25',x[x.overlay>=1.25]); line('model overlay <1.25',x[x.overlay<1.25])
    # $ bet on every sequence: pool vs parlay return on the union ticket
    u=x[x.hit_un]; print(f"  union hits: pool total ${u.pool.sum():.0f} vs parlay total ${u.parlay.sum():.0f}")
print("\nWorst / best ratios (Pick 3):")
p3=r[r.wager=='Pick Three'].sort_values('ratio'); print(pd.concat([p3.head(5),p3.tail(5)])[['date','end','winners','parlay','pool','ratio','hit_un']].round(2).to_string(index=False))
