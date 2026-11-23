"""Kuo helper vs what a choice made after copy 1 could have used (R4 runs: leader copy 1 is the same in every
helper arm up to the seed). Per TB of the window:
  h_miss        the named helper did not decode copy 1
  alt           ... and another member did decode copy 1
  alt_clear     ... and one of those decoders has no building on its path to the last member (geo_member_tail > 0)
  h_ok_blocked  the helper decoded copy 1 but has a building between it and the last member
  tail_lacks    the last member did not decode copy 1
  tail_kuo_bad         tail lacks copy 1 and Kuo's helper cannot serve it (missed copy 1, or building to the tail)
  tail_kuo_bad_better  ... and another member that decoded copy 1 has no building to the tail
  tail_nobody_clear    tail lacks copy 1 and no member that decoded copy 1 has a clear path to the tail
Geometry proxy only (GeoCond building yes / no, tail only): an upper-bound indication, not a simulated scheme.
Usage: python3 -I helper_oracle.py <out_dir>/remote_full_v2 R4
"""
import sys, glob, os, collections
import numpy as np, pandas as pd
def rd(p):
    for q in (p, p+'.gz'):
        if os.path.exists(q): return pd.read_csv(q)
rows = []
for f in sorted(glob.glob(sys.argv[1] + f'/{sys.argv[2]}/*_run.csv')):
    pre = f[:-8]; tag = os.path.basename(pre); cell, seed = tag.rsplit('_s', 1)
    sc, _, tr, bg = cell.split('_')
    r = rd(f).iloc[0]; a = rd(pre+'_att.csv'); h = rd(pre+'_hsel.csv')
    k0, k1 = r.kpi_start, r.kpi_end
    c1 = a[(a.side==0)&(a.tx_in_tb==1)&(a.t>=k0)&(a.t<k1-0.05)]
    hk = dict(zip(h.t_ndi.round(4), h.applied))
    C = collections.Counter()
    for tb, g in c1.groupby('tb'):
        hm = hk.get(round(g.t.iloc[0], 4))
        if hm is None: continue
        C['tbs'] += 1
        dec = set(g[g.status==1].member)
        tail_ok = 8 in dec
        C['tail_lacks'] += not tail_ok
        geo = dict(zip(g.member, g.geo_member_tail))
        clear = {m for m in dec - {8} if geo.get(m, -1) > 0}
        kuo_bad = (hm not in dec) or geo.get(hm, -1) == 0
        if not tail_ok:
            C['tail_kuo_bad'] += kuo_bad
            C['tail_kuo_bad_better'] += kuo_bad and bool(clear - {hm})
            C['tail_nobody_clear'] += not clear
        if hm in dec:
            C['h_ok_blocked'] += geo.get(hm, -1) == 0 and not tail_ok
        else:
            C['h_miss'] += 1
            alt = dec - {hm, 8}
            C['alt'] += bool(alt)
            C['alt_clear'] += any(geo.get(m, -1) > 0 for m in alt)
    rows.append(dict(sc=sc, tr=tr, bg=bg, seed=int(seed)) | {k: C[k]/C['tbs'] for k in C if k != 'tbs'})
d = pd.DataFrame(rows).fillna(0)
pd.set_option('display.width', 200)
print(d.groupby(['sc','tr','bg']).mean(numeric_only=True).drop(columns='seed').round(3).to_string())
