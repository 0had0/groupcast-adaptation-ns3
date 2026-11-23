"""Upper bound of the PRR lost to the stale 'previously decoded' HARQ flag.

A lost (packet, member) pair is 'exposed' when: the member did not see the new-data SCI of copy 1
(status 0, or status 2 which includes a failed second-stage SCI), it had decoded the previous leader
TB on the same HARQ id, and at least one later copy of the TB got no decode attempt (status 0).
Exposed pairs / all pairs bounds the PRR the flag can have cost (it assumes every skipped copy
would have been decoded).
Usage: python3 -I stale_harq_bound.py <out_dir> <arm> <cell-prefix> (e.g. rbtE_ivd30_periodic1tb_none)
"""
import sys, os, glob
import pandas as pd

def rd(p):
    for q in (p, p + '.gz'):
        if os.path.exists(q):
            return pd.read_csv(q)

out, arm, cell = sys.argv[1:4]
tot = exp = lost = 0
for f in sorted(glob.glob(f'{out}/{arm}/{cell}_s*_run.csv')):
    pre = f[:-8]
    r = rd(pre + '_run.csv').iloc[0]
    a, l = rd(pre + '_att.csv'), rd(pre + '_ltx.csv')
    k0, k1 = r.kpi_start, r.kpi_end
    hid = l[l.tx_in_tb == 1].set_index('slot_abs').harq_id
    first = a[(a.side == 0) & (a.tx_in_tb == 1)].drop_duplicates('tb').set_index('tb')
    tb_h = first.slot_abs.map(hid).dropna().astype(int)
    win = set(first[(first.t >= k0) & (first.t < k1)].index)
    dec = a[a.status == 1].groupby('tb').member.apply(set)
    prev, last = {}, {}
    for tb in sorted(tb_h.index):
        prev[tb] = last.get(tb_h[tb]); last[tb_h[tb]] = tb
    for tb, g in a[a.tb.isin(win & set(tb_h.index))].groupby('tb'):
        slots = sorted(g.slot_abs.unique())
        got = dec.get(tb, set())
        for m in range(1, 9):
            tot += 1
            if m in got:
                continue
            lost += 1
            gm = g[g.member == m]
            s1 = gm[gm.slot_abs == slots[0]].status
            if not len(s1) or s1.iloc[0] not in (0, 2):
                continue
            p = prev.get(tb)
            if p is None or m not in dec.get(p, set()):
                continue
            if (gm[gm.slot_abs != slots[0]].status == 0).any():
                exp += 1
print(f'{arm:6s} {cell:34s} pairs {tot:7d}  lost {lost/tot:6.3f}  exposed (PRR bound) {exp/tot:6.3f}')
