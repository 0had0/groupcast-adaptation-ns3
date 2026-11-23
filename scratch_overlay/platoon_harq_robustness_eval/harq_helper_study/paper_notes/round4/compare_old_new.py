"""Old (pre stale-ndi-fix) vs new campaign, per cell (campaign, arm, scenario, traffic, background).

PRR new - old paired by seed (mean, 95 % t interval over the seeds), the same for the worst member's PRR, and the
'loose' upper bound of the PRR the stale HARQ state could cost in the OLD run: a lost (packet, member) pair whose
member missed the new-data SCI of copy 1 (status 0 or 2), decoded an earlier leader TB on the same HARQ id, and got
no decode attempt on at least one later copy (stale_harq_bound.py requires the immediately previous TB; the mark
survives several missed copy-1s, so that bound is too low for the helper arms).
Usage: python3 -I compare_old_new.py <old_full> <new_full> <old_density> <new_density> <out_csv> [--no-bound]
"""
import sys, os, glob, collections
import numpy as np, pandas as pd

T975 = {9: 2.262}   # 10 seeds


def rd(p):
    for q in (p, p + '.gz'):
        if os.path.exists(q):
            return pd.read_csv(q)


def loose_bound(pre):
    r = rd(pre + '_run.csv').iloc[0]
    a, l = rd(pre + '_att.csv'), rd(pre + '_ltx.csv')
    if a is None or l is None:
        return np.nan
    k0, k1 = r.kpi_start, r.kpi_end
    hid = l[l.tx_in_tb == 1].set_index('slot_abs').harq_id
    first = a[(a.side == 0) & (a.tx_in_tb == 1)].drop_duplicates('tb').set_index('tb')
    tb_h = first.slot_abs.map(hid).dropna().astype(int)
    win = set(first[(first.t >= k0) & (first.t < k1)].index)
    dec = a[a.status == 1].groupby('tb').member.apply(set).to_dict()
    # members that decoded an earlier TB on the same HARQ id, per TB
    seen, earlier = collections.defaultdict(set), {}
    for tb in sorted(tb_h.index):
        h = tb_h[tb]
        earlier[tb] = set(seen[h])
        seen[h] |= dec.get(tb, set())
    tot = exp = 0
    for tb, g in a[a.tb.isin(win & set(tb_h.index))].groupby('tb'):
        slot1 = first.slot_abs[tb]
        got = dec.get(tb, set())
        for m in range(1, 9):
            tot += 1
            if m in got or m not in earlier[tb]:
                continue
            gm = g[g.member == m]
            s1 = gm[gm.slot_abs == slot1].status
            if not len(s1) or s1.iloc[0] not in (0, 2):
                continue
            if (gm[gm.slot_abs != slot1].status == 0).any():
                exp += 1
    return exp / tot if tot else np.nan


def main():
    of, nf, od, nd, out = sys.argv[1:6]
    bound = '--no-bound' not in sys.argv
    rows = []
    for camp, old, new in (('full', of, nf), ('density', od, nd)):
        for f in sorted(glob.glob(f'{new}/*/*_run.csv*')):
            arm = f.split('/')[-2]
            tag = os.path.basename(f).split('_run.csv')[0]
            po = f'{old}/{arm}/{tag}'
            if rd(po + '_run.csv') is None:
                continue
            o, n = rd(po + '_run.csv').iloc[0], rd(f'{new}/{arm}/{tag}_run.csv').iloc[0]
            cell, seed = tag.rsplit('_s', 1)
            sc, _, traffic, bg = cell.split('_')
            rows.append(dict(camp=camp, arm=arm, scen=sc, traffic=traffic, bg=bg, seed=int(seed),
                             prr_old=o.prr_mean, prr_new=n.prr_mean, min_old=o.prr_min, min_new=n.prr_min,
                             toggle=n.ndi_toggle_new_tb, bound=loose_bound(po) if bound else np.nan))
        print(camp, len(rows), flush=True)
    d = pd.DataFrame(rows)
    d['dprr'] = d.prr_new - d.prr_old
    d['dmin'] = d.min_new - d.min_old
    d.to_csv(out.replace('.csv', '_runs.csv'), index=False)

    def agg(g):
        n = len(g)
        ci = T975.get(n - 1, 1.96) * g.dprr.std(ddof=1) / np.sqrt(n)
        return pd.Series(dict(n=n, prr_old=g.prr_old.mean(), prr_new=g.prr_new.mean(), dprr=g.dprr.mean(),
                              ci=ci, dmin=g.dmin.mean(), bound=g.bound.mean(), toggle=g.toggle.mean()))
    c = d.groupby(['camp', 'arm', 'scen', 'traffic', 'bg']).apply(agg).reset_index()
    c['drop_beyond_ci'] = c.dprr + c.ci < 0
    c['above_bound'] = c.dprr - c.ci > c.bound
    c.to_csv(out, index=False)
    pd.set_option('display.width', 220)
    print(c.round(4).to_string(index=False))
    print('cells:', len(c), ' significant drop:', int(c.drop_beyond_ci.sum()),
          ' gain significantly above the loose bound:', int(c.above_bound.sum()))


if __name__ == '__main__':
    main()
