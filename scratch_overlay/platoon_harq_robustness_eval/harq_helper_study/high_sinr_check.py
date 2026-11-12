#!/usr/bin/env python3
"""All-seed check of the high-SINR explanation in TAKEOVER_RESULTS_v3_audited.ipynb (Fig. 1).

For every run of the listed arms in out/remote_v3 (raw _att.csv + _run.csv only), counts the (TB, member) pairs whose
leader first copy (side 0, tx_in_tb 1) had SINR > THR dB inside the KPI window, how many of those first copies
failed (status 2), and how many of the pairs were never decoded by any copy. Writes
figures/takeover_v3_audit/check_high_sinr.csv (one row per run).

  python3 high_sinr_check.py [--thr 25] [--jobs 6]
"""
import argparse, glob, os
from concurrent.futures import ProcessPoolExecutor
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ARMS = ['R1', 'R3', 'R4', 'R4.2', 'R5', 'R6']
NBG = {'none': 0, 'n15': 15, 'n30': 30, 'n45': 45, 'moderate': 60}


def one(args):
    pre, arm, thr = args
    sc, _, tr, bg, s = os.path.basename(pre).split('_')
    r = pd.read_csv(pre + '_run.csv').iloc[0]
    a = pd.read_csv(pre + '_att.csv' if os.path.exists(pre + '_att.csv') else pre + '_att.csv.gz')
    first = a[(a.side == 0) & (a.tx_in_tb == 1) & (a.t >= r.kpi_start) & (a.t < r.kpi_end)]
    hi = first[first.sinr_db > thr]
    dec = set(zip(a[a.status == 1].tb, a[a.status == 1].member))
    never = sum((tb, m) not in dec for tb, m in zip(hi.tb, hi.member))
    return dict(arm=arm, sc=sc, tr=tr, nbg=NBG[bg], seed=int(s[1:]), pairs_hi=len(hi),
                first_fail=int((hi.status == 2).sum()), never_decoded=int(never))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--thr', type=float, default=25.0)
    ap.add_argument('--jobs', type=int, default=6)
    ap.add_argument('--out', default=os.path.join(HERE, 'out', 'remote_v3'))
    a = ap.parse_args()
    jobs = []
    for arm in ARMS:
        for p in sorted(glob.glob(f'{a.out}/{arm}/*_run.csv')):
            jobs.append((p[:-len('_run.csv')], arm, a.thr))
    with ProcessPoolExecutor(a.jobs) as ex:
        rows = list(ex.map(one, jobs, chunksize=8))
    d = pd.DataFrame(rows)
    os.makedirs(os.path.join(HERE, 'figures', 'takeover_v3_audit'), exist_ok=True)
    d.to_csv(os.path.join(HERE, 'figures', 'takeover_v3_audit', 'check_high_sinr.csv'), index=False)
    g = d.groupby('arm')[['pairs_hi', 'first_fail', 'never_decoded']].sum()
    g['first_fail_pct'] = 100 * g.first_fail / g.pairs_hi
    g['never_pct'] = 100 * g.never_decoded / g.pairs_hi
    print(len(d), 'runs'); print(g.round(3))


if __name__ == '__main__':
    main()
