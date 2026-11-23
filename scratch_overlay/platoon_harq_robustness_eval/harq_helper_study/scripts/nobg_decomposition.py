"""PRR decomposition and helper-reception analysis at 0 background cars (campaign v3, out/remote_v3).

At 0 cars a lost (TB, member) pair is due to the channel and decoding only (no background interference). For every
pair the delivery is attributed to the copy that COMPLETED the decode (first status 1 in _att.csv):
  L1     the leader's first transmission
  Lrep   a later leader transmission in its own slot (_ltx origin 0): HARQ retransmission (R3, R4.2 after a helper
         NACK has no own slot, so R4.2 has none), blind repetition (R1, R1_bsc)
  H      a helper copy (_htx)
  T1     a leader takeover after a helper NACK (_ltx origin 1)
  T2     a leader takeover by the fallback: no helper SCI detected in the handed slot (_ltx origin 2)
  LH     leader and helper copies in the same slot both decoded (ambiguous; rare)
  lost   not delivered by the PDB
Attribution is not counterfactual: a copy completes a decode that may rely on combining with earlier copies (HARQ-IR,
blind soft combining), and a member decoded by a later copy might have been reached by another one. The shares add
up to 1 per arm; differences between arms add up to the PRR difference exactly.

Helper reception: the helper named for the TB (_hsel applied) decoded the leader's first transmission (status 1).

Per-run outputs (cached in out/cache_nobg_v3/<arm>_{cat,win,run}.csv):
  cat   pair counts per (member, is_helper, st1, helper_ok, src); st1 = first-copy status at that member (0 SCI missed,
        1 decoded, 2 decoding failed), helper_ok -1 = no helper (leader-only arm)
  win   per 2 s window (by packet generation time): TBs with a helper, helper received, pairs / delivered pairs, all
        members and non-helper members only
  run   transmissions per kind and transmitted subchannel-slots, copies sent after all 8 members had the TB

  python3 scripts/nobg_decomposition.py [--arms R1,R3,R4_bsc,R4.2,R6] [--jobs 5]
"""
import argparse, glob, os
from concurrent.futures import ProcessPoolExecutor
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # harq_helper_study/ (this file: scripts/)
OUT = os.path.join(HERE, 'out', 'remote_v3')
CACHE = os.path.join(HERE, 'out', 'cache_nobg_v3')
ARMS = ['R1', 'R1_bsc', 'R3', 'R4', 'R4.2', 'R4_bsc', 'R6']
NMEM = 8
SLOT = 0.5e-3
WIN_S = 2.0
PDB = {'periodic1tb': 0.100, 'aperiodic1tb': 0.050}
SRC = ['L1', 'Lrep', 'H', 'T1', 'T2', 'LH', 'lost']


def rd(p):
    for q in (p, p + '.gz'):
        if os.path.exists(q):
            return pd.read_csv(q)
    return None


def run_tables(pre, arm):
    sc, _, tr, bg, s = os.path.basename(pre).split('_')
    assert bg == 'none', pre
    key = dict(arm=arm, sc=sc, tr=tr, seed=int(s[1:]))
    r = rd(pre + '_run.csv').iloc[0]
    k0, k1 = float(r.kpi_start), float(r.kpi_end)
    pdb = PDB[tr]
    a, k, l = rd(pre + '_att.csv'), rd(pre + '_pkt.csv'), rd(pre + '_ltx.csv')
    h, hs = rd(pre + '_htx.csv'), rd(pre + '_hsel.csv')

    # TB <-> packet (one TB per packet in the *1tb classes): the first leader transmission within the PDB after
    # the packet's generation; one to one (asserted)
    first = a[(a.side == 0) & (a.tx_in_tb == 1)].drop_duplicates('tb')[['tb', 't', 'slot_abs']].sort_values('t')
    pk = k.drop_duplicates('seq')[['seq', 't_tx']].sort_values('t_tx')
    m = pd.merge_asof(first, pk, left_on='t', right_on='t_tx', direction='backward', tolerance=pdb).dropna()
    m = m.sort_values('t').drop_duplicates('seq', keep='first')
    assert not m.tb.duplicated().any(), pre
    k = k.assign(tb=k.seq.map(dict(zip(m.seq.astype(int), m.tb))))
    k['deliv'] = ((k.rx == 1) & (k.delay_ms <= pdb * 1e3 + 1e-6)).astype(int)
    k['w'] = np.floor((k.t_tx - k0) / WIN_S).astype(int)

    # copy kind of every transmission
    org = dict(zip(l.slot_abs.astype(np.int64), l.origin.astype(int))) if 'origin' in l else {}
    aa = a[a.tb.isin(m.tb)].copy()
    lk = np.where(aa.tx_in_tb == 1, 'L1', aa.slot_abs.map(org).fillna(0).astype(int)
                  .map({0: 'Lrep', 1: 'T1', 2: 'T2'}))
    aa['kind'] = np.where(aa.side == 1, 'H', lk)

    # source of the first decode per pair
    ok = aa[aa.status == 1].sort_values('slot_abs')
    fs = ok.groupby(['tb', 'member']).slot_abs.min().rename('fslot').reset_index()
    kinds = ok.groupby(['tb', 'member', 'slot_abs']).kind.agg(lambda x: 'LH' if x.nunique() > 1 and 'H' in set(x)
                                                              else x.iloc[0]).rename('src').reset_index()
    src = fs.merge(kinds, left_on=['tb', 'member', 'fslot'], right_on=['tb', 'member', 'slot_abs'])[['tb', 'member', 'src']]
    k = k.merge(src, on=['tb', 'member'], how='left')
    late = (k.deliv == 0) & k.src.notna()
    nodec = (k.deliv == 1) & k.src.isna()
    assert not nodec.any(), f'{pre}: {int(nodec.sum())} delivered pairs without a decoded copy'
    k['src'] = np.where(k.deliv == 1, k.src, 'lost')

    # first-copy status and helper
    f1 = aa[aa.kind == 'L1'][['tb', 'member', 'status']].rename(columns={'status': 'st1'})
    k = k.merge(f1, on=['tb', 'member'], how='left')
    k['st1'] = k.st1.fillna(-1).astype(int)          # -1: packet never transmitted
    helper = {}
    if hs is not None and len(hs):
        hsl = dict(zip(np.round(hs.t_ndi / SLOT).astype(np.int64), hs.applied.astype(int)))
        helper = {tb: hsl.get(sl) for tb, sl in zip(m.tb, m.slot_abs.astype(np.int64))}
    st = f1.set_index(['tb', 'member']).st1
    hok = {tb: int(st.get((tb, hm), 0) == 1) for tb, hm in helper.items() if hm}
    k['helper'] = k.tb.map({tb: hm for tb, hm in helper.items() if hm}).fillna(0).astype(int)
    k['helper_ok'] = k.tb.map(hok).fillna(-1).astype(int)
    k['is_helper'] = (k.member == k.helper).astype(int)

    cat = (k.groupby(['member', 'is_helper', 'st1', 'helper_ok', 'src']).size().rename('n').reset_index())
    for c, v in key.items():
        cat.insert(0, c, v)

    rows = []
    for w, g in k.groupby('w'):
        tbw = g.drop_duplicates('seq')
        nh = g[g.is_helper == 0]
        rows.append(key | dict(w=int(w), n_pkt=len(tbw), n_helper=int((tbw.helper_ok >= 0).sum()),
                               n_helper_ok=int((tbw.helper_ok == 1).sum()), n_pairs=len(g), n_deliv=int(g.deliv.sum()),
                               n_pairs_nh=len(nh), n_deliv_nh=int(nh.deliv.sum())))
    win = pd.DataFrame(rows)

    # transmissions and redundancy (copies after every member had the TB)
    all8 = ok.groupby('tb').apply(lambda g: g.groupby('member').slot_abs.min().max()
                                  if g.member.nunique() == NMEM else np.inf, include_groups=False)
    tx = aa.drop_duplicates(['side', 'slot_abs'])
    tx = tx.assign(redundant=tx.slot_abs > tx.tb.map(all8))
    rb = pd.concat([d[['slot_abs', 'rb_len']].assign(side=i) for i, d in enumerate((l, h)) if d is not None])
    tx = tx.merge(rb, on=['side', 'slot_abs'], how='left')
    run = key | dict(n_pkt=k.seq.nunique(), n_pairs=len(k), n_late=int(late.sum()),
                     ss_tx=float(tx.rb_len.sum() / r.subch_prb),
                     **{f'tx_{c}': int((tx.kind == c).sum()) for c in ['L1', 'Lrep', 'H', 'T1', 'T2']},
                     **{f'red_{c}': int(((tx.kind == c) & tx.redundant).sum()) for c in ['L1', 'Lrep', 'H', 'T1', 'T2']})
    return dict(cat=cat, win=win, run=pd.DataFrame([run]))


def _job(args):
    return run_tables(*args)


def collect(arms=ARMS, jobs=5, out=OUT, cache=CACHE):
    os.makedirs(cache, exist_ok=True)
    res = {t: [] for t in ('cat', 'win', 'run')}
    for arm in arms:
        pres = sorted(p[:-len('_run.csv')] for p in glob.glob(f'{out}/{arm}/*_none_s*_run.csv'))
        fs = {t: f'{cache}/{arm}_{t}.csv' for t in res}
        if not (os.path.exists(fs['run']) and len(pd.read_csv(fs['run'])) == len(pres)):
            with ProcessPoolExecutor(jobs) as ex:
                rr = list(ex.map(_job, [(p, arm) for p in pres]))
            for t in res:
                pd.concat([x[t] for x in rr], ignore_index=True).to_csv(fs[t], index=False)
            print(arm, len(pres), 'runs', flush=True)
        for t in res:
            res[t].append(pd.read_csv(fs[t]))
    return {t: pd.concat(v, ignore_index=True) for t, v in res.items()}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--arms', default=','.join(ARMS))
    ap.add_argument('--jobs', type=int, default=5)
    ap.add_argument('--one', default=None, help='one run prefix: print its tables')
    a = ap.parse_args()
    if a.one:
        t = run_tables(a.one, os.path.basename(os.path.dirname(a.one)))
        for n, d in t.items():
            print('==', n); print(d.to_string(max_rows=60))
    else:
        d = collect(a.arms.split(','), a.jobs)
        print({t: len(v) for t, v in d.items()})
