"""Does a member that missed the first copy's SCI ignore the later copies of the TB because it
still marks the HARQ process as decoded from the previous TB (5G-LENA keeps a 'previously decoded'
flag per (source, HARQ id) and clears it only on an SCI with NDI = 1)?

For every (TB, member) whose copy 1 has status 0 (no decode attempt) and which got later copies:
  prev_dec  = the member decoded the previous leader TB on the same HARQ id
  later_0   = share of the later copies with status 0 (no decode attempt)
If the flag is stale, later_0 is ~1 when prev_dec and lower otherwise.
Usage: python3 -I stale_harq_check.py <out_dir> <arm> <tag> [<tag> ...]
"""
import sys, os, collections
import pandas as pd

def rd(p):
    for q in (p, p + '.gz'):
        if os.path.exists(q):
            return pd.read_csv(q)

out, arm, tags = sys.argv[1], sys.argv[2], sys.argv[3:]
acc = collections.defaultdict(lambda: [0, 0, 0])   # key -> [pairs, later copies, later status 0]
for tag in tags:
    pre = f'{out}/{arm}/{tag}'
    a, l = rd(pre + '_att.csv'), rd(pre + '_ltx.csv')
    hid = l[l.tx_in_tb == 1].set_index('slot_abs').harq_id
    first = a[(a.side == 0) & (a.tx_in_tb == 1)].drop_duplicates('tb').set_index('tb').slot_abs
    tb_h = first.map(hid).dropna().astype(int)
    dec = a[a.status == 1].groupby('tb').member.apply(set)
    prev_tb, last_by_h = {}, {}
    for tb in sorted(tb_h.index):
        h = tb_h[tb]
        prev_tb[tb] = last_by_h.get(h)
        last_by_h[h] = tb
    for tb, g in a[a.tb.isin(tb_h.index)].groupby('tb'):
        slots = sorted(g.slot_abs.unique())
        if len(slots) < 2:
            continue
        c1 = g[g.slot_abs == slots[0]].set_index('member').status
        later = g[g.slot_abs != slots[0]]
        p = prev_tb.get(tb)
        for m, s1 in c1.items():
            if s1 not in (0, 2):
                continue
            pd_ = p is not None and m in dec.get(p, set())
            lm = later[later.member == m]
            k = (f'copy1={"SCI missed" if s1 == 0 else "SCI ok, data failed"}', f'prev TB decoded={pd_}')
            acc[k][0] += 1; acc[k][1] += len(lm); acc[k][2] += int((lm.status == 0).sum())
print(arm)
for k in sorted(acc):
    n, c, z = acc[k]
    print(f'  {k[0]:22s} {k[1]:22s} pairs {n:6d}  later copies {c:6d}  no decode attempt {z / max(c, 1):6.1%}')
