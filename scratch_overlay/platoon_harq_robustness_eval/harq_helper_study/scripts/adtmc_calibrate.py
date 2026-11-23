#!/usr/bin/env python3
"""Calibrate the ADTMC (adtmc_rewrite/adtmc.py, external, reviewed, imported unchanged) on the v3 0-car runs and
evaluate it for every scenario x traffic cell.

Reception trials (raw logs of R3, R4.2 and R6, one shared physical kernel for all arms):
  for every copy of a TB and every member j that is not its transmitter and has not decoded the TB yet:
    control usable  = the member decoded (status 1) or failed with a NACK (status 2 + _nack row within 3 ms)
    control missed  = status 0, or status 2 without a NACK (failed SCI-2A)
    data decoded    = status 1, given control usable
  the member's buffer = transmitters of its earlier copies with a usable control and failed data (the model's
  'N' events: 0 = leader, h = helper), as in adtmc.receiver_paths.
Estimates (counts on the calibration seeds; a level is used if it has >= MIN_N trials, else the next coarser one):
  control(tx, j)        : (role, tx id, j) -> (role, j) -> (role)
  data(tx, j, buffer)   : (role, tx id, j, buffer roles) -> (role, j, buffer roles) -> (role, j, len) -> (role, buffer roles)
                          -> (role)        where role = L (leader) / H (helper), buffer roles e.g. ('L', 'H')
A kernel is built per (cell, helper id) and evaluated with adtmc.evaluate; a cell's prediction is the mixture over
helper ids weighted by how often each was the named helper. R1 / R4 use the model's no-combining convention
(store_failed=False, as in the campaign).

  python3 scripts/adtmc_calibrate.py [--train 1-7] [--jobs 5]   -> out/adtmc_calib_v3/{trials,predictions}.csv
"""
import argparse, glob, os, sys
from concurrent.futures import ProcessPoolExecutor
from itertools import product
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # harq_helper_study/ (this file: scripts/)
OUT = os.path.join(HERE, 'out', 'remote_v3')
DEST = os.path.join(HERE, 'out', 'adtmc_calib_v3')
ADTMC_DIR = '/Users/hadih/Documents/Codex/2026-11-12/referenced-chatgpt-conversation-this-is-an/outputs/adtmc_rewrite'
NMEM, SLOT, NACK_WIN, MIN_N = 8, 0.5e-3, 0.003, 30
SCHEMES = ['R1', 'R3', 'R4', 'R4.2', 'R6', 'R6_no_fallback', 'R6_no_recovery']
CAL_ARMS = ['R3', 'R4.2', 'R6']


def rd(p):
    for q in (p, p + '.gz'):
        if os.path.exists(q):
            return pd.read_csv(q)
    return None


def trials_run(pre, arm):
    sc, _, tr, bg, s = os.path.basename(pre).split('_')
    r = rd(pre + '_run.csv').iloc[0]
    a, nk, hs = rd(pre + '_att.csv'), rd(pre + '_nack.csv'), rd(pre + '_hsel.csv')
    nacks = {m: np.sort(g.t.values) for m, g in nk.groupby('member')} if nk is not None and len(nk) else {}
    first = a[(a.side == 0) & (a.tx_in_tb == 1) & (a.t >= r.kpi_start) & (a.t < r.kpi_end)].drop_duplicates('tb')
    helper = {}
    if hs is not None and len(hs):
        hsl = dict(zip(np.round(hs.t_ndi / SLOT).astype(np.int64), hs.applied.astype(int)))
        helper = {tb: hsl.get(int(sl), 0) for tb, sl in zip(first.tb, first.slot_abs)}
    rows = []
    for tb, g in a[a.tb.isin(set(first.tb))].groupby('tb'):
        h = helper.get(tb, 0)
        cps = g.drop_duplicates(['side', 'slot_abs']).sort_values(['t', 'side'])
        st = {(sd, sl): dict(zip(x.member, x.status)) for (sd, sl), x in g.groupby(['side', 'slot_abs'])}
        dec, buf = set(), {j: () for j in range(1, NMEM + 1)}
        for k, (sd, sl, t) in enumerate(zip(cps.side, cps.slot_abs, cps.t), 1):
            tx = 0 if sd == 0 else h
            for j in range(1, NMEM + 1):
                if j == tx or j in dec:
                    continue
                s_ = st[(sd, sl)].get(j, 0)
                nack = s_ == 2 and j in nacks and ((nacks[j] >= t - 1e-9) & (nacks[j] < t + NACK_WIN)).any()
                ctrl = int(s_ == 1 or nack)
                rows.append((arm, sc, tr, int(s[1:]), int(tb), h, k, 'L' if tx == 0 else 'H', tx, j,
                             ''.join('L' if b == 0 else 'H' for b in buf[j]), ctrl, int(s_ == 1)))
                if s_ == 1:
                    dec.add(j)
                elif nack:
                    buf[j] = buf[j] + (tx,)
    return pd.DataFrame(rows, columns=['arm', 'sc', 'tr', 'seed', 'tb', 'helper', 'copy', 'role', 'tx', 'j', 'buf',
                                       'ctrl', 'data'])


def _job(args):
    return trials_run(*args)


class Est:
    """Hierarchical count estimates for one cell (calibration trials only)."""

    def __init__(self, T):
        self.T = T
        c = T.groupby(['role', 'tx', 'j']).ctrl.agg(['sum', 'size'])
        self.c = {1: c, 2: T.groupby(['role', 'j']).ctrl.agg(['sum', 'size']), 3: T.groupby('role').ctrl.agg(['sum', 'size'])}
        D = T[T.ctrl == 1].assign(blen=lambda x: x.buf.str.len())
        self.d = {1: D.groupby(['role', 'tx', 'j', 'buf']).data.agg(['sum', 'size']),
                  2: D.groupby(['role', 'j', 'buf']).data.agg(['sum', 'size']),
                  3: D.groupby(['role', 'j', 'blen']).data.agg(['sum', 'size']),
                  4: D.groupby(['role', 'buf']).data.agg(['sum', 'size']),
                  5: D.groupby('role').data.agg(['sum', 'size'])}
        self.levels = []

    @staticmethod
    def _get(tab, key):
        if key in tab.index:
            s, n = tab.loc[key]
            return s, n
        return 0, 0

    def control(self, role, tx, j):
        for lev, key in ((1, (role, tx, j)), (2, (role, j)), (3, role)):
            s, n = self._get(self.c[lev], key)
            if n >= MIN_N or lev == 3:
                self.levels.append(('c', lev))
                return s / n if n else 1.0

    def data(self, role, tx, j, buf):
        for lev, key in ((1, (role, tx, j, buf)), (2, (role, j, buf)), (3, (role, j, len(buf))), (4, (role, buf)),
                         (5, role)):
            s, n = self._get(self.d[lev], key)
            if n >= MIN_N or lev == 5:
                self.levels.append(('d', lev))
                return s / n if n else 0.5


def kernel(M, est, h):
    buffers = [()] + [b for L in (1, 2) for b in product((0, h), repeat=L)]
    role = lambda x: 'L' if x == 0 else 'H'
    data, ctrl = {}, {}
    for tx in (0, h):
        for j in range(1, NMEM + 1):
            if j == tx:
                continue
            c = est.control(role(tx), tx, j)
            for k in (1, 2, 3):
                ctrl[(tx, j, k)] = c
            for b in buffers:
                data[(tx, j, b)] = est.data(role(tx), tx, j, ''.join(role(x) for x in b))
    ld = tuple(data[(0, j, ())] for j in range(1, NMEM + 1))
    rdat = tuple(data[(h, j, ())] if j != h else 0.0 for j in range(1, NMEM + 1))
    one = (1.0,) * NMEM
    lc = tuple(ctrl[(0, j, 1)] for j in range(1, NMEM + 1))
    hc = tuple(ctrl[(h, j, 1)] if j != h else 1.0 for j in range(1, NMEM + 1))
    return M.Kernel(helper=h, leader_data=ld, relay_data=rdat, leader_sci1=lc, leader_sci2=one, relay_sci1=hc,
                    relay_sci2=one, data_table=data, control_table=ctrl)


def predict(M, T_cal, weights):
    """weights: {helper id: share of TBs}. Returns one row per scheme with mixture-averaged outputs."""
    est = Est(T_cal)
    ks = {h: kernel(M, est, h) for h in weights}
    rows = []
    keys = ('group_prr', 'all_member_prr', 'copies', 'nack_attempts', 'fallback_attempts', 'nack_rescued_pairs',
            'fallback_rescued_pairs', 'nack_recovering_attempts', 'fallback_recovering_attempts')
    for sch in SCHEMES:
        acc = {k: 0.0 for k in keys}
        mem = np.zeros(NMEM)
        for h, w in weights.items():
            o = M.evaluate(ks[h], sch)
            for k in keys:
                acc[k] += w * o[k]
            mem += w * np.asarray(o['member_prr'])
        rows.append(dict(scheme=sch, **acc, worst_member_prr=mem.min(), **{f'prr_m{i + 1}': v for i, v in enumerate(mem)}))
    lv = pd.Series([f'{a}{b}' for a, b in est.levels]).value_counts(normalize=True).round(3).to_dict()
    return pd.DataFrame(rows), lv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--train', default='1-7')
    ap.add_argument('--jobs', type=int, default=5)
    a = ap.parse_args()
    lo, hi = map(int, a.train.split('-'))
    os.makedirs(DEST, exist_ok=True)
    ft = os.path.join(DEST, 'trials.csv.gz')
    if not os.path.exists(ft):
        jobs = [(p[:-len('_run.csv')], arm) for arm in CAL_ARMS for p in sorted(glob.glob(f'{OUT}/{arm}/*_none_s*_run.csv'))]
        with ProcessPoolExecutor(a.jobs) as ex:
            pd.concat(ex.map(_job, jobs, chunksize=4), ignore_index=True).to_csv(ft, index=False)
    T = pd.read_csv(ft, keep_default_na=False)
    T['buf'] = T.buf.astype(str)
    sys.path.insert(0, ADTMC_DIR)
    import adtmc as M  # noqa: E402  (reviewed; pure numpy)
    out, levels = [], []
    for (sc, tr), g in T.groupby(['sc', 'tr']):
        cal = g[g.seed.between(lo, hi)]
        test = g[~g.seed.between(lo, hi) & g.arm.isin(['R4.2', 'R6'])]
        w = test[test['copy'] == 1].drop_duplicates(['arm', 'seed', 'tb']).helper.value_counts(normalize=True)
        p, lv = predict(M, cal, w.to_dict())
        out.append(p.assign(sc=sc, tr=tr))
        levels.append(dict(sc=sc, tr=tr, helpers=dict(w.round(3)), **lv))
        print(sc, tr, 'helpers', dict(w.round(2)), flush=True)
    pd.concat(out).to_csv(os.path.join(DEST, 'predictions.csv'), index=False)
    pd.DataFrame(levels).to_csv(os.path.join(DEST, 'estimate_levels.csv'), index=False)


if __name__ == '__main__':
    main()
