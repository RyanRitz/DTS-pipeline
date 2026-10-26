import pandas as pd, numpy as np, glob
l=pd.read_csv('results/performance_log.csv'); l=l[(l.date>=20261002)&(l.in_scope==1)].copy(); l['pn']=l.pn.astype(str)
l['MornOddsAdj']=l.value_prod/l.ProbToWin-1; l['p_fin']=1/(l.final_odds+1); l['p_fin']=l.p_fin/l.groupby(['date','race']).p_fin.transform('sum')
for c,src in [('rk','ProbToWin'),('mlrk','ml_novig'),('finrk','p_fin')]: l[c]=l.groupby(['date','race'])[src].rank(ascending=False,method='first')
for c,src in [('pa','ProbToWin'),('mlpa','ml_novig')]:
    g=l.sort_values(['date','race',src],ascending=[True,True,False]); l[c]=(g.groupby(['date','race'])[src].cumsum()-g[src]).reindex(l.index)
l['top']=l.pa<0.5; l['mltop']=l.mlpa<0.5; l['gold']=((l.MornOddsAdj+1)*l.ProbToWin>=1.5)&l.top
l['seg']=np.where(l.RaceType.isin(['M','S']),'maiden',np.where(l.Surface.str.upper()=='T','turf','dirt'))
l['wk']=pd.cut(l.date,[0,20261004,20261011,20261018,20261231],labels=['wk1 Oct2-4','wk2 Oct7-11','wk3 Oct14-18','wk4 Oct21-24'])
res=[]
for f in sorted(glob.glob('results/KEE_2026*_results.csv')):
    r=pd.read_csv(f); r['date']=int(f.split('_')[1]); res.append(r)
res=pd.concat(res); res['program']=res.program.astype(str)
l=l.merge(res[['date','race','program','beaten_lengths','place_payout','show_payout']],left_on=['date','race','pn'],right_on=['date','race','program'],how='left')
l['near']=(l.won==0)&(l.beaten_lengths<=0.1)&((l.place_payout.fillna(0)>0)|(l.show_payout.fillna(0)>0))
def ll(x,c): return -np.mean(np.where(x.won==1,np.log(x[c].clip(1e-6)),np.log((1-x[c]).clip(1e-6))))
def roi(g): return ((g.won*2*(g.final_odds+1)).sum()/(2*len(g))-1)*100 if len(g) else np.nan
def wc(x,col):
    conc=ties=tot=0
    for _,g in x.groupby(['date','race']):
        w=g[g.won==1][col].values; lo=g[g.won==0][col].values
        for a in w: conc+=(a>lo).sum(); ties+=(a==lo).sum(); tot+=len(lo)
    return (conc+0.5*ties)/tot
R=l.groupby(['date','race']).ngroups; w=l[l.won==1]
print(f"KEE Oct 2-24 2026: {l.date.nunique()} cards, {R} in-scope races, {len(l)} runners")
print(f"{'':30s}{'Model':>8s}{'ML adj':>8s}{'Final':>8s}")
print(f"{'within-race c':30s}{wc(l,'ProbToWin'):8.3f}{wc(l,'ml_novig'):8.3f}{wc(l,'p_fin'):8.3f}")
print(f"{'log-loss':30s}{ll(l,'ProbToWin'):8.4f}{ll(l,'ml_novig'):8.4f}{ll(l,'p_fin'):8.4f}")
print(f"{'top pick / fav wins':30s}{int((w.rk==1).sum()):8d}{int((w.mlrk==1).sum()):8d}{int((w.finrk==1).sum()):8d}")
print(f"{'winners in top-50% mass':30s}{int(w.top.sum()):8d}{int(w.mltop.sum()):8d}")
a=l[l.top&(l.ProbToWin/l.p_fin>=1.25)]; b=l[l.mltop&(l.ml_novig/l.p_fin>=1.25)]
print(f"{'top50+25% over final pl/W/ROI':30s} {len(a)}/{int(a.won.sum())}/{roi(a):+.0f}%   {len(b)}/{int(b.won.sum())}/{roi(b):+.0f}%")
d=l[(l.rk==1)|(l.mlrk==1)].groupby(['date','race']).filter(lambda x: len(x)==2); m1=d[d.rk==1]; f1=d[d.mlrk==1]
print(f"disagree on #1: {d.groupby(['date','race']).ngroups} races — model's won {int(m1.won.sum())} ({roi(m1):+.0f}%), ML's won {int(f1.won.sum())} ({roi(f1):+.0f}%)")
print(f"near-misses (<=0.1L, paid): {int(l.near.sum())}; top pick wins+noses {int((w.rk==1).sum())+int(l[(l.rk==1)&l.near].shape[0])}; gold wins+noses {int(l[l.gold].won.sum())+int(l[l.gold&l.near].shape[0])}")
print("\nBY WEEK      races  LL model/ML/final   top1 m/ML  top50 m/ML   gold pl/W/exp/ROI    c model/ML")
for k,x in l.groupby('wk',observed=True):
    Rk=x.groupby(['date','race']).ngroups; wk=x[x.won==1]; G=x[x.gold]
    print(f"{k:12s} {Rk:4d}   {ll(x,'ProbToWin'):.4f}/{ll(x,'ml_novig'):.4f}/{ll(x,'p_fin'):.4f}   {int((wk.rk==1).sum()):2d}/{int((wk.mlrk==1).sum()):2d}     {int(wk.top.sum()):2d}/{int(wk.mltop.sum()):2d}     {len(G):3d}/{int(G.won.sum()):2d}/{G.ProbToWin.sum():5.1f}/{roi(G):+4.0f}%   {wc(x,'ProbToWin'):.3f}/{wc(x,'ml_novig'):.3f}")
print("\nBY CARD   races LL model  LL ML  top1 m/ML  top50 m/ML  gold pl/W")
for k,x in l.groupby('date'):
    Rk=x.groupby('race').ngroups; wk=x[x.won==1]; G=x[x.gold]
    print(f"{k}  {Rk:3d}   {ll(x,'ProbToWin'):.4f}  {ll(x,'ml_novig'):.4f}   {int((wk.rk==1).sum()):2d}/{int((wk.mlrk==1).sum()):2d}      {int(wk.top.sum()):2d}/{int(wk.mltop.sum()):2d}      {len(G):2d}/{int(G.won.sum())}")
print("\nmodel/market ratio   n   model  actual  market   ROI")
l['ratio']=l.ProbToWin/l.p_fin
for lo,hi in [(0,0.6),(0.6,0.8),(0.8,1.0),(1.0,1.25),(1.25,1.5),(1.5,2),(2,99)]:
    b=l[(l.ratio>=lo)&(l.ratio<hi)]; print(f"  {lo:4.2f}-{hi:4.2f}  {len(b):5d}  {b.ProbToWin.mean():.3f}  {b.won.mean():.3f}  {b.p_fin.mean():.3f}  {roi(b):+6.0f}%")
print("\nfield size   races  LL model/mkt  top1  top50  gold pl/W/ROI")
for lo,hi,lab in [(0,7,'<=6'),(7,9,'7-8'),(9,99,'9+')]:
    x=l[(l.field>=lo)&(l.field<hi)]; Rk=x.groupby(['date','race']).ngroups; wk=x[x.won==1]; G=x[x.gold]
    print(f"  {lab:6s} {Rk:4d}   {ll(x,'ProbToWin'):.4f}/{ll(x,'p_fin'):.4f}   {int((wk.rk==1).sum()):3d}   {int(wk.top.sum()):3d}   {len(G)}/{int(G.won.sum())}/{roi(G):+.0f}%")
print("\nrace type   races  top1  top50  gold pl/W/ROI")
for k,x in l.groupby('RaceType'):
    Rk=x.groupby(['date','race']).ngroups; wk=x[x.won==1]; G=x[x.gold]
    print(f"  {k:4s} {Rk:4d}   {int((wk.rk==1).sum()):3d}   {int(wk.top.sum()):3d}    {len(G)}/{int(G.won.sum())}/{roi(G) if len(G) else 0:+.0f}%")
