import pandas as pd, numpy as np
def load(p,mind):
    l=pd.read_csv(p); l=l[(l.date>=mind)&(l.in_scope==1)].copy(); l['pn']=l.pn.astype(str)
    l['MornOddsAdj']=l.value_prod/l.ProbToWin-1; l['p_fin']=1/(l.final_odds+1); l['p_fin']=l.p_fin/l.groupby(['date','race']).p_fin.transform('sum')
    p='ProbToWin'; g=l.sort_values(['date','race',p],ascending=[True,True,False])
    l['pa']=(g.groupby(['date','race'])[p].cumsum()-g[p]).reindex(l.index); l['rk']=(g.groupby(['date','race']).cumcount()+1).reindex(l.index)
    l['top']=l.pa<0.5; l['gold']=((l.MornOddsAdj+1)*l[p]>=1.5)&l.top
    l['seg']=np.where(l.RaceType.isin(['M','S']),'maiden',np.where(l.Surface.str.upper()=='T','turf','dirt'))
    l['mlrk']=l.groupby(['date','race']).ml_novig.rank(ascending=False,method='first'); l['finrk']=l.groupby(['date','race']).p_fin.rank(ascending=False,method='first')
    return l
def ll(x,c): return -np.mean(np.where(x.won==1,np.log(x[c].clip(1e-6)),np.log((1-x[c]).clip(1e-6))))
def roi(g): return ((g.won*2*(g.final_odds+1)).sum()/(2*len(g))-1)*100 if len(g) else np.nan
def lo(p): return np.log(p/(1-p))
for name,l in [('KEE Oct26',load('results/performance_log.csv',20261002)),('SAR 2026',load('results/performance_log_SAR.csv',20260709))]:
    l=l[l.date!=20261011] if 'KEE' in name else l
    R=l.groupby(['date','race']).ngroups; w=l[l.won==1]
    print(f"\n===== {name}: {R} races =====")
    print(f"corr(model logit, ML logit) {np.corrcoef(lo(l.ProbToWin),lo(l.ml_novig))[0,1]:.3f}   corr(model logit, final logit) {np.corrcoef(lo(l.ProbToWin),lo(l.p_fin))[0,1]:.3f}")
    print(f"model top pick == ML fav: {(w.mlrk[l.loc[w.index,'rk']==1]==1).sum() if False else ((l.rk==1)&(l.mlrk==1)).sum()/R:.0%} of races;  == final fav: {((l.rk==1)&(l.finrk==1)).sum()/R:.0%}")
    t1=l[l.rk==1]; print(f"top pick: n={len(t1)} wins {int(t1.won.sum())} ({t1.won.mean():.0%}) model-p {t1.ProbToWin.mean():.3f} mkt-p {t1.p_fin.mean():.3f} ROI {roi(t1):+.0f}%")
    a=t1[t1.mlrk==1]; b=t1[t1.mlrk!=1]; print(f"  top pick IS ML fav: n={len(a)} win {a.won.mean():.0%} model {a.ProbToWin.mean():.3f} mkt {a.p_fin.mean():.3f} ROI {roi(a):+.0f}% | NOT ML fav: n={len(b)} win {b.won.mean():.0%} model {b.ProbToWin.mean():.3f} mkt {b.p_fin.mean():.3f} ROI {roi(b):+.0f}%")
    f=l[l.finrk==1]; print(f"final favorite: wins {f.won.mean():.0%}, model gives {f.ProbToWin.mean():.3f}, market {f.p_fin.mean():.3f}")
    # disagreement buckets: model/market ratio
    l['ratio']=l.ProbToWin/l.p_fin
    print("  model/market ratio   n   model  actual  market   ROI")
    for lo_,hi in [(0,0.6),(0.6,0.8),(0.8,1.0),(1.0,1.25),(1.25,1.5),(1.5,2),(2,99)]:
        b=l[(l.ratio>=lo_)&(l.ratio<hi)]; print(f"   {lo_:4.2f}-{hi:4.2f}  {len(b):5d}  {b.ProbToWin.mean():.3f}  {b.won.mean():.3f}  {b.p_fin.mean():.3f}  {roi(b):+6.0f}%")
    print("  by segment: races  LL model / mkt / ML   top1  top50  gold pl/W/exp/ROI")
    for s in ['dirt','turf','maiden']:
        x=l[l.seg==s]; Rk=x.groupby(['date','race']).ngroups; wk=x[x.won==1]; G=x[x.gold]
        print(f"   {s:7s} {Rk:4d}  {ll(x,'ProbToWin'):.4f} / {ll(x,'p_fin'):.4f} / {ll(x,'ml_novig'):.4f}   {int((wk.rk==1).sum()):3d}  {int(wk.top.sum()):3d}   {len(G)}/{int(G.won.sum())}/{G.ProbToWin.sum():.1f}/{roi(G):+.0f}%")
    if 'SAR' in name:
        # NY-bred proxy: can't see restriction in log; use race_type? skip. Field size & month
        for lo_,hi,lab in [(0,7,'field<=6'),(7,9,'7-8'),(9,99,'9+')]:
            x=l[(l.field>=lo_)&(l.field<hi)]; Rk=x.groupby(['date','race']).ngroups; wk=x[x.won==1]; G=x[x.gold]
            print(f"   {lab:8s} {Rk:4d}  LL {ll(x,'ProbToWin'):.4f}/{ll(x,'p_fin'):.4f}  top1 {int((wk.rk==1).sum())}  gold {len(G)}/{int(G.won.sum())}/{roi(G):+.0f}%")
    else:
        for lo_,hi,lab in [(0,7,'field<=6'),(7,9,'7-8'),(9,99,'9+')]:
            x=l[(l.field>=lo_)&(l.field<hi)]; Rk=x.groupby(['date','race']).ngroups; wk=x[x.won==1]; G=x[x.gold]
            print(f"   {lab:8s} {Rk:4d}  LL {ll(x,'ProbToWin'):.4f}/{ll(x,'p_fin'):.4f}  top1 {int((wk.rk==1).sum())}  gold {len(G)}/{int(G.won.sum())}/{roi(G):+.0f}%")
