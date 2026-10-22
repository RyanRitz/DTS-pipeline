import pandas as pd, numpy as np, glob, re
log=pd.read_csv('results/performance_log.csv'); log['pn']=log['pn'].astype(str)
log['p_fin']=1/(log.final_odds+1); log['p_fin']=log.p_fin/log.groupby(['date','race']).p_fin.transform('sum')
rows=[]
for f in sorted(glob.glob('../Challenger/predictions_sastree/KEE_*_sastree.csv')):
    d=int(re.search(r'KEE_(\d{8})',f).group(1)); c=pd.read_csv(f); c['pn']=c['program'].astype(str)
    rows.append(log[log.date==d].merge(c[['race','pn','ProbToWin']].rename(columns={'ProbToWin':'p_ch'}),on=['race','pn']))
df=pd.concat(rows,ignore_index=True); df['p_ch']=df.p_ch/df.groupby(['date','race']).p_ch.transform('sum'); df=df.rename(columns={'ProbToWin':'p_cp'})
for p in ['p_cp','p_ch']:
    g=df.sort_values(['date','race',p],ascending=[True,True,False])
    df['pa_'+p]=(g.groupby(['date','race'])[p].cumsum()-g[p]).reindex(df.index); df['rk_'+p]=(g.groupby(['date','race']).cumcount()+1).reindex(df.index)
s=df[(df.in_scope==1)&(df.date!=20261011)].copy()
s['seg']=np.where(s.RaceType.isin(['M','S']),'maiden',np.where(s.Surface=='T','turf','dirt'))
def roi(g): return ((g.won*2*(g.final_odds+1)).sum()/(2*len(g))-1)*100
def rep(name,p):
    v=s[(s['pa_'+p]<0.5)&(s[p]/s.p_fin>=1.25)]
    def line(l,g):
        if len(g): print(f"  {l:14s}{len(g):5d}{int(g.won.sum()):5d}{g[p].sum():8.1f}{g.p_fin.sum():8.1f}{roi(g):8.1f}%{g.final_odds.mean():8.1f}")
    print(name); print(f"  {'':14s}plays wins mod-exp mkt-exp  $2 ROI avg fin")
    line('all',v)
    for k in ['dirt','turf','maiden']: line(k,v[v.seg==k])
    for k in [1,2,3]: line(f'rank {k}',v[v['rk_'+p]==k])
    line('rank 4+',v[v['rk_'+p]>=4])
    for lo,hi,l in [(1.25,1.5,'edge 1.25-1.5'),(1.5,2,'edge 1.5-2'),(2,99,'edge 2+')]: line(l,v[(v[p]/v.p_fin>=lo)&(v[p]/v.p_fin<hi)])
    for w,l in [((20261002,20261004),'week1'),((20261007,20261010),'week2'),((20261014,20261021),'wk3+10/21')]: line(l,v[(v.date>=w[0])&(v.date<=w[1])])
    return v
a=rep('CHAMPION','p_cp'); b=rep('TREE','p_ch')
both=a.index.intersection(b.index); bb=s.loc[both]; ta=s.loc[a.index.difference(both)]; tb=s.loc[b.index.difference(both)]
print(f"\nBOTH qualify: {len(bb)} pl {int(bb.won.sum())} W ROI {roi(bb):+.0f}%  | champ-only: {len(ta)} pl {int(ta.won.sum())} W ROI {roi(ta):+.0f}%  | tree-only: {len(tb)} pl {int(tb.won.sum())} W ROI {roi(tb):+.0f}%")
print("tree-only winners:"); print(tb[tb.won==1][['date','race','pn','HorseName','final_odds','p_cp','p_ch','p_fin']].round(3).to_string(index=False))
