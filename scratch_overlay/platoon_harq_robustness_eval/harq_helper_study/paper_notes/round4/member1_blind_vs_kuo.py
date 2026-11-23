"""Member 1 (closest to the leader): why Kuo's PRR is below leader blind under load.
Per TB of the window: copy 1 failed at member 1; of those, the named helper also missed copy 1 (no helper copy
follows, the handed slots stay dead) and member 1 lost the TB.
Usage: python3 -I member1_blind_vs_kuo.py <out_dir>   (reads <out_dir>/remote_full_v2)
"""
import sys, glob, os, collections
import pandas as pd

def rd(p):
    for q in (p, p + '.gz'):
        if os.path.exists(q):
            return pd.read_csv(q)

rows = []
for arm in ['R1', 'R1_bsc', 'R4', 'R4_bsc']:
    for cell in ['hwy_ivd30_aperiodic1tb_none', 'hwy_ivd30_aperiodic1tb_moderate', 'hwy_ivd30_periodic1tb_moderate',
                 'rbt_ivd30_periodic1tb_moderate', 'rbt_ivd30_aperiodic1tb_moderate']:
        for f in glob.glob(f'{sys.argv[1]}/remote_full_v2/{arm}/{cell}_s*_run.csv'):
            pre = f[:-8]; r = rd(f).iloc[0]; a = rd(pre + '_att.csv'); h = rd(pre + '_hsel.csv')
            c1 = a[(a.side == 0) & (a.tx_in_tb == 1) & (a.t >= r.kpi_start) & (a.t < r.kpi_end - 0.05)]
            a = a[a.tb.isin(set(c1.tb))]
            hk = dict(zip(h.t_ndi.round(4), h.applied)) if h is not None else {}
            C = collections.Counter()
            for tb, g in a.groupby('tb'):
                x1 = g[(g.side == 0) & (g.tx_in_tb == 1)]
                ok1 = (x1[x1.member == 1].status == 1).any()
                got = (g[g.member == 1].status == 1).any()
                C['n'] += 1; C['copy1_fail'] += not ok1; C['lost'] += not got
                if not ok1:
                    hm = hk.get(round(x1.t.iloc[0], 4))
                    hmiss = hm is not None and not (x1[x1.member == hm].status == 1).any()
                    C['helper_also_missed'] += hmiss
                    C['helper_also_missed_lost'] += hmiss and not got
            rows.append(dict(arm=arm, cell=cell) | {k: C[k] / C['n'] for k in C if k != 'n'})
pd.set_option('display.width', 200)
print(pd.DataFrame(rows).groupby(['cell', 'arm']).mean().round(3).to_string())
