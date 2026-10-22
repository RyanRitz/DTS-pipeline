"""Pick 3 (cum-50% legs, 3 consecutive in-scope races): champion vs tree vs UNION, chart payoffs. Usage: python p3_h2h.py [--union-only]"""
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
    g=df.sort_values(['date','race',p],ascending=[True,True,False])
    df['pa_'+p]=(g.groupby(['date','race'])[p].cumsum()-g[p]).reindex(df.index)
df['top_cp']=df.pa_p_cp<0.5; df['top_ch']=df.pa_p_ch<0.5; df['top_un']=df.top_cp|df.top_ch
scope=df[(df.in_scope==1)&(df.date!=20261011)]
WK={'wk1':(20261002,20261004),'wk2':(20261007,20261010),'wk3+':(20261014,20261021)}
def run(name,col,wager='Pick Three',nl=3,lo=0,hi=99999999):
    n=hits=0; mexp=kexp=cost=ret=0.0; combos=0
    for d,day in scope.groupby('date'):
        if not lo<=d<=hi: continue
        po=payoffs('KEE',d,wager); races=sorted(day.race.unique())
        for r1 in races:
            rs=[r1+i for i in range(nl)]
            if any(r not in races for r in rs) or rs[-1] not in po: continue
            legs=[day[(day.race==r)&day[col]] for r in rs]
            n+=1; hit=all((l.won==1).any() for l in legs); hits+=hit
            mexp+=np.prod([l.p_cp.sum() if col=='top_cp' else l.p_ch.sum() if col=='top_ch' else l.p_fin.sum() for l in legs]); kexp+=np.prod([l.p_fin.sum() for l in legs])
            k=int(np.prod([len(l) for l in legs])); combos+=k; rec=po[rs[-1]]; cost+=rec['base']*k
            if hit: ret+=rec['payoff']
    if n: print(f"  {name:10s} {n:3d} tix {hits:3d} hits ({hits/n:4.0%})  mkt-exp {kexp:5.1f}  avg combos {combos/n:5.1f}  cost ${cost:6.0f}  return ${ret:6.0f}  ROI {(ret-cost)/cost*100:+5.0f}%")
for w,(lo,hi) in [('ALL Oct 2-21',(0,99999999))]+list(WK.items()):
    print(w)
    for name,col in [('Champion','top_cp'),('Tree','top_ch'),('Union','top_un')]: run(name,col,lo=lo,hi=hi)
print("\nDouble, same legs, for reference (ALL):")
for name,col in [('Champion','top_cp'),('Tree','top_ch'),('Union','top_un')]: run(name,col,'Daily Double',2)
