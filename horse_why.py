"""Per-variable contributions (champion, field-relative) for one horse, post-scratch. Usage: python horse_why.py 20261023 8 3 "2"  (date race pn scratched-pns-comma)"""
import sys; from pathlib import Path
import numpy as np, pandas as pd
HERE=Path(__file__).resolve().parent; sys.path.insert(0,str(HERE))
import config, model_setup, score, model_registry as mr, attribution as at
from ingest_drf import load_drf; from features import engineer_features
model_setup.setup_registry(config)
date,race,pn=sys.argv[1],int(sys.argv[2]),sys.argv[3]; scr=set(sys.argv[4].split(',')) if len(sys.argv)>4 and sys.argv[4] else set()
cdir=Path(config.COEFF_DIR); cdir=cdir if cdir.exists() else HERE/"coefficients"
drf=load_drf(str(HERE.parent/"KEE"/"RAW_DATA"/"RACINGFORM"/date[:4]/f"KEE{date[4:8]}.DRF"),"KEE",date[4:],date[:4],validate=False)
drf['pn']=drf['ProgramNumberifavailable'].astype(str).str.strip(); drf['race']=pd.to_numeric(drf['Race'],errors='coerce').astype(int)
drf=drf[~((drf.race==race)&drf.pn.isin(scr))].reset_index(drop=True)
feats=engineer_features(drf.copy()); sc=mr.get_scoring_models("KEE",config,date); cp=score.run_scoring(feats.copy(),cdir,sc)
cp['pn']=cp['ProgramNumberifavailable'].astype(str).str.strip(); cp['race']=cp['Race'].astype(int)
feats['pn']=feats['ProgramNumberifavailable'].astype(str).str.strip(); feats['race']=pd.to_numeric(feats['Race'],errors='coerce').astype(int)
key=["Track","Date","Race","HorseName"]; extra=[c for c in feats.columns if c not in cp.columns or c in key]; merged=cp.merge(feats[extra],on=key,how='left')
coeff_sets=at._load_coefficient_sets(sc,cdir,merged.columns); mplan=at._maiden_plan(sc); turf26=at._is_oct26_turf(sc)
rg=merged[merged.race==race]; mid=rg['model'].iloc[0]; sub=coeff_sets.get(mid,{})
allf=sorted({f for c in sub.values() for f in c}&set(rg.columns))
cr={i:at._blend_contribution(rg.loc[i],mid,sub,allf,mplan,turf26) for i in rg.index}
cdf=pd.DataFrame([cr[i] or {} for i in rg.index],index=rg.index).reindex(columns=allf).fillna(0.0); rel=cdf-cdf.mean()
i=rg.index[rg.pn==pn][0]
print(f"R{race} #{pn} {rg.at[i,'HorseName']}  model {rg.at[i,'ProbToWin']:.3f}  field: "+", ".join(f"#{r.pn} {r.ProbToWin:.2f}" for r in rg.sort_values('ProbToWin',ascending=False).itertuples()))
out=pd.DataFrame({'raw':{f:rg.at[i,f] for f in allf},'contrib':cdf.loc[i],'vs_field':rel.loc[i]}).sort_values('vs_field',ascending=False)
out['field_mean_raw']=[rg[f].mean() for f in out.index]
print(out.round(3).to_string())
print("sum vs field:", round(rel.loc[i].sum(),3))
