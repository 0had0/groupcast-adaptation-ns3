import pandas as pd, numpy as np
T=2.262
def ci(x): x=np.asarray(x,float); return T*x.std(ddof=1)/np.sqrt(len(x)) if len(x)>1 else np.nan
R=pd.read_csv('runs.csv'); D=pd.read_csv('dist.csv'); K=pd.read_csv('takeover.csv'); M=pd.read_csv('members.csv')
cols=['prr','worst','res','res_resv','cbr','coll_loss','chan_loss','helper_tx','leader_tx','reclaim','fallback','fb_check','fb_heard','helper_nack','sci_miss','leader_tx_coll','helper_tx_coll']
g=R.groupby(['arm','sc','tr','nbg'])
S=g[cols].mean().round(4); C=g[['prr','worst','res','cbr']].agg(ci).round(4).add_suffix('_ci')
S=S.join(C).join(g.seed.nunique().rename('n')).reset_index(); S.to_csv('S_cells.csv',index=False)
P=R.pivot_table(index=['sc','tr','nbg','seed'],columns='arm',values=['prr','worst'])
rows=[]
for a,b in [('R6','R1'),('R6','R3'),('R6','R4.2'),('R4','R1'),('R5','R4.1'),('R2','R2_noIR'),('R3','R3_noIR'),('R4.1','R4.1_noIR'),('R4.2','R4.2_noIR')]:
    for m in ['prr','worst']:
        d=(P[(m,a)]-P[(m,b)]).dropna()
        for k,x in d.groupby(level=[0,1,2]):
            rows.append(dict(a=a,b=b,m=m,sc=k[0],tr=k[1],nbg=k[2],d=round(x.mean(),4),ci=round(ci(x),4),n=len(x)))
pd.DataFrame(rows).to_csv('S_paired.csv',index=False)
D=D[D.n>=10]
D.groupby(['arm','sc','tr','nbg','d']).prr.agg(['mean',ci,'size']).round(4).reset_index().to_csv('S_dist.csv',index=False)
for c in ['leader_regular','leader_nack_takeover','leader_fallback','helper']: K[c]=K[c]/K.denom
K.groupby(['arm','sc','tr','nbg'])[['leader_regular','leader_nack_takeover','leader_fallback','helper']].mean().round(4).reset_index().to_csv('S_tko.csv',index=False)
Mg=M.groupby(['arm','sc','tr','nbg','seed']).agg(dm=('d_mean','mean'),dp95=('d_p95','max')).reset_index()
Mg.groupby(['arm','sc','tr','nbg'])[['dm','dp95']].mean().round(2).reset_index().to_csv('S_lat.csv',index=False)
