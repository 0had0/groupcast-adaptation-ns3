"""Option 2 (R3): why a leader copy goes out after all 8 members had the TB.
Usage: python3 -I opt2_redundant.py <out_dir>/remote_full_v2
The per-member cause (SCI-2A failure of an already-decoded member, so no re-ACK) was confirmed on the traced runs
(out/remote_slots_trace, _harq.csv event 6); see HANDOFF_RESULTS_V2.md."""
import sys, glob, os, collections
import numpy as np, pandas as pd
def rd(p):
    for q in (p, p+'.gz'):
        if os.path.exists(q): return pd.read_csv(q)
C = collections.Counter(); ex = []
for f in sorted(glob.glob(sys.argv[1] + '/R3/*_run.csv')):
    pre = f[:-8]; r = rd(f).iloc[0]
    a, l = rd(pre+'_att.csv'), rd(pre+'_ltx.csv')
    k0, k1 = r.kpi_start, r.kpi_end
    first = a[(a.side==0)&(a.tx_in_tb==1)&(a.t>=k0)&(a.t<k1-0.05)]
    a = a[a.tb.isin(first.tb.unique())].sort_values('t')
    lrow = l.set_index('slot_abs')
    C['tbs'] += first.tb.nunique()
    for tb, g in a.groupby('tb'):
        have = set(); prev = None
        for sl, x in g.groupby('slot_abs', sort=True):
            if len(have) == 8:
                C['redundant'] += 1
                p = prev
                # members that already had the TB before the previous copy and did not decode / see it
                miss0 = set(p[(p.status==0)].member); miss2 = set(p[(p.status==2)].member)
                lp = lrow.loc[p.slot_abs.iloc[0]]
                key = ('prev copy: some member status0 (missed SCI-1A)' if miss0 else
                       'prev copy: some member status2 (SCI-2A/data fail)' if miss2 else 'prev copy: all 8 decoded/ok')
                C[key] += 1
                C[f'prev n_nack_rx={min(int(lp.n_nack),2)}'] += 1
                C[f'prev decision={int(lp.decision)}'] += 1
                C[f'gap slots={min(int(sl - p.slot_abs.iloc[0]),9)}'] += 1
                if len(ex) < 6: ex.append((os.path.basename(pre), tb, int(sl), sorted(miss0), sorted(miss2), int(lp.n_nack), int(lp.n_nack_sent), int(lp.decision)))
            have |= set(x[x.status==1].member); prev = x
for k, v in sorted(C.items()): print(f'{k:55s} {v}')
for e in ex: print(e)
