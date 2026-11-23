#!/usr/bin/env python3
"""Step 1 of the ADTMC validation: deterministic protocol replay on the v3 logs at 0 background cars.

For every TB of R3, R4.2 and R6, the observed outcomes are fed to the ADTMC protocol rules
(adtmc_rewrite/adtmc.py: helper_branches + Branch.active, 3 opportunities, fixed helper) and the model's next
actions are compared with the copies the simulator actually sent. No probability is fitted.

Observed events (raw logs only)
  copies       _att.csv rows grouped by (side, slot): leader first copy L1, leader own retransmission Lrep (_ltx origin
               0, R3), leader takeover after helper NACK T1 (origin 1), leader fallback T2 (origin 2), helper copy H
  outcome      per member and copy: D = decoded (status 1); N = decode failed and a NACK was emitted (status 2 and a
               _nack.csv row of that member within 3 ms); M = no usable control (status 0, or status 2 without a NACK:
               SCI-2A failed, no feedback possible). Members that decoded earlier are logged with status 0 on later copies
               and are not used as outcomes.
  e1           the leader's own decision after copy 1 (_ltx decision: 1 = every expected member ACKed, 0 = not)
  e_k (k > 1)  the leader's decision after its own copy k when it sent one. After a helper copy the stop test is the
               helper's and is not logged; but if an expected member still lacks the TB after copy 2 it cannot have
               ACKed, so the test is false and copy 3 is required (strict). Only when every expected member has the TB
               are both continuations admissible ("free" in the output: depends on unlogged ACK reception)
Model rules (ADTMC section 4) with these events:
  R3    copy 2 = Lrep iff e1 = 0; copy 3 = Lrep iff copy 2 sent and e2 = 0
  R4.2  helper D on copy 1: copy 2 = H, copy 3 = H or nothing (free); otherwise no further copy
  R6    helper D: copy 2 = H iff e1 = 0, copy 3 = H or nothing (free) if copy 2 was sent
        helper N: copy 2 = T1; then helper on copy 2: D -> copy 3 = H iff e2 = 0, N -> copy 3 = T1, M -> nothing
        helper M: no copy 2; copy 3 = T2 iff e1 = 0
Copies are compared as ordered action sequences: the logs do not record which reserved slot was left empty, so the
model's position of the fallback (opportunity 3, opportunity 2 empty) is not checked.
Also: the model's predicted e1 from the member outcomes of copy 1 (all expected members D, nobody N) against the
logged decision, and the reserved slot count of the TB's grant (2-slot grants cannot carry a third copy).

  python3 scripts/adtmc_replay.py [--arms R3,R4.2,R6] [--jobs 5]   -> out/adtmc_replay_v3/{tb,summary}.csv
"""
import argparse, glob, os
from concurrent.futures import ProcessPoolExecutor
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # harq_helper_study/ (this file: scripts/)
OUT = os.path.join(HERE, 'out', 'remote_v3')
DEST = os.path.join(HERE, 'out', 'adtmc_replay_v3')
NMEM = 8
SLOT = 0.5e-3
NACK_WIN = 0.003


def rd(p):
    for q in (p, p + '.gz'):
        if os.path.exists(q):
            return pd.read_csv(q)
    return None


def reserved_slots(gr, first_t):
    """slots of the leader's last grant created before each TB's first copy (3 if not logged)."""
    if gr is None:
        return {}
    c = gr[(gr.node == 0) & (gr.event == 'c')].sort_values('t')
    idx = np.searchsorted(c.t.values, np.asarray(first_t), side='right') - 1
    n = c.slots_or_counter.values
    return [int(n[i]) if i >= 0 else 3 for i in idx]


def predict(arm, h1, e1, h2, e2_leader, lack2=None):
    """Admissible continuations (copy 2, copy 3) as a set of tuples; '' = no copy; e2_leader None if not logged."""
    if arm == 'R3':
        if e1 == 1:
            return {('', '')}
        if e2_leader is None:
            return {('Lrep', ''), ('Lrep', 'Lrep')}
        return {('Lrep', '' if e2_leader == 1 else 'Lrep')}
    if arm == 'R4.2':
        if h1 != 'D':
            return {('', '')}
        return {('H', 'H')} if lack2 else {('H', ''), ('H', 'H')}
    if arm == 'R6':
        if h1 == 'D':
            if e1 == 1:
                return {('', '')}
            return {('H', 'H')} if lack2 else {('H', ''), ('H', 'H')}
        if h1 == 'N':
            if h2 is None:
                return {('T1', ''), ('T1', 'H'), ('T1', 'T1')}
            if h2 == 'D':
                if lack2:
                    return {('T1', 'H')}
                if e2_leader is None:
                    return {('T1', ''), ('T1', 'H')}
                return {('T1', '' if e2_leader == 1 else 'H')}
            if h2 == 'N':
                return {('T1', 'T1')}
            return {('T1', '')}
        return {('', '')} if e1 == 1 else {('', 'T2')}
    raise ValueError(arm)


def replay_run(pre, arm):
    sc, _, tr, bg, s = os.path.basename(pre).split('_')
    r = rd(pre + '_run.csv').iloc[0]
    k0, k1 = float(r.kpi_start), float(r.kpi_end)
    a, l, nk, hs, gr = (rd(pre + x) for x in ('_att.csv', '_ltx.csv', '_nack.csv', '_hsel.csv', '_grants.csv'))
    org = dict(zip(l.slot_abs.astype(np.int64), l.origin.astype(int)))
    dec = dict(zip(l.slot_abs.astype(np.int64), l.decision.astype(int)))
    nacks = {m: np.sort(g.t.values) for m, g in nk.groupby('member')} if nk is not None and len(nk) else {}

    def event(m, st, t):
        if st == 1:
            return 'D'
        if st == 2:
            v = nacks.get(m)
            return 'N' if v is not None and ((v >= t - 1e-9) & (v < t + NACK_WIN)).any() else 'M'
        return 'M'

    first = a[(a.side == 0) & (a.tx_in_tb == 1) & (a.t >= k0) & (a.t < k1)].drop_duplicates('tb')
    helper = {}
    if hs is not None and len(hs):
        hsl = dict(zip(np.round(hs.t_ndi / SLOT).astype(np.int64), hs.applied.astype(int)))
        helper = {tb: hsl.get(int(sl)) for tb, sl in zip(first.tb, first.slot_abs)}
    nres = dict(zip(first.tb, reserved_slots(gr, first.t.values)))
    aa = a[a.tb.isin(set(first.tb))]
    rows = []
    for tb, g in aa.groupby('tb'):
        cps = g.drop_duplicates(['side', 'slot_abs']).sort_values(['t', 'side'])
        kinds = []
        for side, k, sl in zip(cps.side, cps.tx_in_tb, cps.slot_abs):
            kinds.append('H' if side == 1 else 'L1' if k == 1 else {0: 'Lrep', 1: 'T1', 2: 'T2'}[org.get(int(sl), 0)])
        h = helper.get(tb) or 0
        c1 = g[(g.side == 0) & (g.tx_in_tb == 1)]
        st1 = dict(zip(c1.member, c1.status))
        t1 = c1.t.iloc[0]
        ev1 = {m: event(m, st1.get(m, 0), t1) for m in range(1, NMEM + 1)}
        h1 = ev1[h] if h else None
        expected = [m for m in range(1, NMEM + 1) if m != h]
        e1_model = int(all(ev1[m] == 'D' for m in expected) and not any(v == 'N' for v in ev1.values()))
        e1 = dec.get(int(c1.slot_abs.iloc[0]), -1)
        # helper outcome on copy 2 when copy 2 is a leader takeover, and the leader's decision after it
        h2 = e2 = None
        if len(cps) > 1 and kinds[1] in ('T1', 'Lrep'):
            sl2 = int(cps.slot_abs.iloc[1])
            c2 = g[(g.side == 0) & (g.slot_abs == sl2)]
            if h:
                s2 = dict(zip(c2.member, c2.status)).get(h, 0)
                h2 = 'D' if s2 == 1 else event(h, s2, c2.t.iloc[0]) if s2 == 2 else ('M' if h1 != 'D' else None)
            d2 = dec.get(sl2, -1)
            e2 = d2 if d2 in (0, 1) else None
        # lack2: an expected member still lacks the TB after the TB's second copy -> it cannot have ACKed copy 2,
        # so the stop test after copy 2 is false whoever performs it (observable even when the ACKs are not)
        lack2 = None
        if len(cps) > 1:
            t2 = cps.t.iloc[1]
            got = set(g[(g.status == 1) & (g.t <= t2 + 1e-9)].member)
            lack2 = int(any(m not in got for m in expected))
        obs = tuple(kinds[1:3]) + ('',) * (2 - len(kinds[1:3]))
        pred = predict(arm, h1, e1 if e1 in (0, 1) else e1_model, h2, e2, lack2)
        free = len(pred) > 1
        extra = kinds[3:]
        rows.append(dict(arm=arm, sc=sc, tr=tr, seed=int(s[1:]), tb=int(tb), helper=h, h1=h1, h2=h2,
                         e1_log=e1, e1_model=e1_model, e2_log=e2, lack2=lack2, reserved=nres.get(tb, 3),
                         observed='-'.join(kinds), obs2=obs[0], obs3=obs[1], n_copies=len(kinds),
                         predicted='|'.join(sorted('-'.join(['L1'] + [x for x in p if x]) for p in pred)),
                         match=int(tuple(x for x in obs if x) in {tuple(x for x in q if x) for q in pred} and not extra),
                         free=int(free), extra_copies=len(extra)))
    return pd.DataFrame(rows)


def _job(args):
    return replay_run(*args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--arms', default='R3,R4.2,R6')
    ap.add_argument('--jobs', type=int, default=5)
    ap.add_argument('--bg', default='none')
    ap.add_argument('--one', default=None)
    a = ap.parse_args()
    if a.one:
        d = replay_run(a.one, os.path.basename(os.path.dirname(a.one)))
        print(d.match.mean(), len(d)); print(d[d.match == 0].head(30).to_string()); return
    jobs = [(p[:-len('_run.csv')], arm) for arm in a.arms.split(',')
            for p in sorted(glob.glob(f'{OUT}/{arm}/*_{a.bg}_s*_run.csv'))]
    with ProcessPoolExecutor(a.jobs) as ex:
        d = pd.concat(ex.map(_job, jobs, chunksize=4), ignore_index=True)
    os.makedirs(DEST, exist_ok=True)
    d.to_csv(os.path.join(DEST, f'tb_{a.bg}.csv'), index=False)
    print(len(jobs), 'runs,', len(d), 'TBs')


if __name__ == '__main__':
    main()
