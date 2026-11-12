"""Per-run tables for TAKEOVER_RESULTS_v3.ipynb: reliability vs channel quality, vs helper failure, resource
efficiency, takeover recovery and latency, from the per-transmission logs of campaign v3.

Unit of analysis: a (TB, member) pair. Every leader packet of the *1tb traffic classes is one TB; the TB of a
packet is the leader's first transmission (_att.csv side 0, tx_in_tb 1) that follows the packet's generation
within the PDB (one to one, asserted). A pair is delivered when the member decoded the TB within the PDB;
decoding in _att.csv (status 1 on any copy) agrees with _pkt.csv rx for every pair (checked on R1, R3, R4, R5, R6).

Definitions
  sinr1        SINR (dB) of the leader's first transmission of the TB at the member (_att.csv). A member that did
               not decode its SCI 1-A has no SINR (logged -99): own bin 'SCI missed'.
  helper       the member named in the TB's SCI-2A (_hsel.csv applied, matched to the first transmission's slot).
  helper fail  the helper did not decode the leader's first transmission, so it cannot relay in its first handed
               slot (it is the member that would retransmit). Helper arms only (R4*, R5, R6).
  takeover     a leader transmission in a slot it had handed to the helper (_ltx.csv origin): trigger 1 = the
               helper NACKed, trigger 2 = fallback (the leader decided NACK and heard no helper SCI in the handed
               slot). One takeover attempt = one such transmission.
  rescued      a member that lacked the TB before the takeover transmission and decoded it at that transmission
               (first decode in that slot, on the leader's copy; if the helper also sent in the same slot and the
               member decoded the helper's copy, the helper is credited).
  recovered TB a TB in which at least one takeover transmission of the trigger rescued at least one member.
  ss_resv      subchannel-slots the leader reserved (_grants.csv; SB-SPS prorated per period), the helper's relays
               use the leader's handed slots (S1), so this is the whole platoon's allocation.
  ss_tx        subchannel-slots transmitted by leader and helper (_ltx + _htx rb_len / subchannel PRBs).
Windows: WIN_S seconds from the KPI-window start, by packet generation time (transmissions by their own time).
"""
import glob, os, sys
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'paper_v2', 'analysis'))
from paper_data import rd, _reserved_subch_slots, NBG  # noqa: E402

NMEM = 8
SLOT = 0.5e-3
WIN_S = 2.0
PDB = {'periodic1tb': 0.100, 'aperiodic1tb': 0.050}
SINR_LO, SINR_HI, SINR_STEP = -12, 40, 2
SINR_MISS = -999          # bin of the pairs whose member missed the SCI of the first copy
LAT_STEP_MS = 0.25        # latency histogram bin; bin -1 = not delivered within the PDB


def _slot(t):
    return np.round(np.asarray(t) / SLOT).astype(np.int64)


def run_tables(pre, arm):
    """Returns dict(sinr=, win=, lat=) DataFrames for one run (prefix pre, arm folder name)."""
    sc, _, tr, bg, s = os.path.basename(pre).split('_')
    key = dict(arm=arm, sc=sc, tr=tr, nbg=NBG[bg], seed=int(s[1:]))
    r = rd(pre + '_run.csv').iloc[0]
    k0, k1 = float(r.kpi_start), float(r.kpi_end)
    pdb = PDB[tr]
    a = rd(pre + '_att.csv')
    k = rd(pre + '_pkt.csv')
    l = rd(pre + '_ltx.csv')
    h = rd(pre + '_htx.csv')
    hs = rd(pre + '_hsel.csv')
    gr = rd(pre + '_grants.csv')

    # --- TB <-> packet
    first = a[(a.side == 0) & (a.tx_in_tb == 1)].drop_duplicates('tb')[['tb', 't', 'slot_abs']].sort_values('t')
    pk = k.drop_duplicates('seq')[['seq', 't_tx']].sort_values('t_tx')
    m = pd.merge_asof(first, pk, left_on='t', right_on='t_tx', direction='backward', tolerance=pdb).dropna()
    m = m.sort_values('t').drop_duplicates('seq', keep='first')   # a TB before the window cannot steal a packet
    m['seq'] = m.seq.astype(int)
    tb_of = dict(zip(m.seq, m.tb))
    k = k.assign(tb=k.seq.map(tb_of))
    assert not m.seq.duplicated().any() and not m.tb.duplicated().any(), pre
    k['deliv'] = ((k.rx == 1) & (k.delay_ms <= pdb * 1e3 + 1e-6)).astype(int)
    k['w'] = np.floor((k.t_tx - k0) / WIN_S).astype(int)

    # --- first-copy SINR / status per pair, helper and its first-copy status per TB
    f1 = a[(a.side == 0) & (a.tx_in_tb == 1)][['tb', 'member', 'status', 'sinr_db']]
    k = k.merge(f1.rename(columns={'status': 'st1', 'sinr_db': 'sinr1'}), on=['tb', 'member'], how='left')
    helper = {}
    if hs is not None and len(hs):
        hsl = dict(zip(_slot(hs.t_ndi), hs.applied.astype(int)))
        helper = {tb: hsl.get(sl) for tb, sl in zip(m.tb, m.slot_abs.astype(np.int64))}
    hst = f1.set_index(['tb', 'member']).status
    hfail = {}
    for tb, hm in helper.items():
        if hm is not None and hm > 0:
            hfail[tb] = int(hst.get((tb, hm), 0) != 1)
    k['hfail'] = k.tb.map(hfail).fillna(-1).astype(int)        # -1: no helper (leader-only arm / no TB)

    # --- takeover attribution (arms whose _ltx has origin 1 / 2)
    to_rows = []
    if l is not None and 'origin' in l and (l.origin > 0).any():
        org = dict(zip(l.slot_abs.astype(np.int64), l.origin.astype(int)))
        aa = a[a.tb.isin(m.tb)].copy()
        aa['org'] = np.where(aa.side == 0, aa.slot_abs.map(org).fillna(0), -1).astype(int)
        for tb, g in aa.groupby('tb'):
            to = g[(g.side == 0) & (g.org > 0)]
            if to.empty:
                continue
            ok = g[g.status == 1]
            fdec = ok.groupby('member').slot_abs.min()                         # first decode slot per member
            hdec = set(zip(ok[ok.side == 1].member, ok[ok.side == 1].slot_abs))
            for sl, x in to.groupby('slot_abs'):
                trig = int(x.org.iloc[0])
                lacking = [mm for mm in range(1, NMEM + 1) if not (mm in fdec.index and fdec[mm] < sl)]
                resc = [mm for mm in x[x.status == 1].member
                        if fdec.get(mm) == sl and (mm, sl) not in hdec]
                to_rows.append(dict(tb=tb, trig=trig, lacking=len(lacking), rescued=len(resc)))
    to = pd.DataFrame(to_rows, columns=['tb', 'trig', 'lacking', 'rescued'])

    # --- window table
    seq_w = k.drop_duplicates('seq').set_index('tb').w
    rows = []
    wmax = int(np.floor((k1 - k0) / WIN_S - 1e-9))
    txs = pd.concat([d[['t', 'rb_len']] for d in (l, h) if d is not None])
    for w in range(wmax + 1):
        w0, w1 = k0 + w * WIN_S, min(k0 + (w + 1) * WIN_S, k1)
        kw = k[k.w == w]
        tbs_w = kw.drop_duplicates('seq')
        row = key | dict(w=w, w0=w0, w1=w1, n_pkt=len(tbs_w), n_pairs=len(kw), n_deliv=int(kw.deliv.sum()),
                         n_tb_all=int((kw.groupby('seq').deliv.sum() == NMEM).sum()),
                         n_notx=int(tbs_w.tb.isna().sum()),
                         n_helper=int((tbs_w.hfail >= 0).sum()), n_hfail=int((tbs_w.hfail == 1).sum()),
                         n_pairs_hfail=int((kw.hfail == 1).sum()), n_deliv_hfail=int(kw.deliv[kw.hfail == 1].sum()),
                         n_pairs_hok=int((kw.hfail == 0).sum()), n_deliv_hok=int(kw.deliv[kw.hfail == 0].sum()),
                         ss_resv=_reserved_subch_slots(gr, w0, w1),
                         ss_tx=txs.rb_len[(txs.t >= w0) & (txs.t < w1)].sum() / r.subch_prb)
        tw = to[to.tb.map(seq_w) == w] if len(to) else to
        any_tb = set()
        for trig in (1, 2):
            x = tw[tw.trig == trig]
            any_tb |= set(x.tb)
            row |= {f'to{trig}_tx': len(x), f'to{trig}_tb': x.tb.nunique(),
                    f'to{trig}_rec_tb': x[x.rescued > 0].tb.nunique(),
                    f'to{trig}_useful_tx': int((x.rescued > 0).sum()),
                    f'to{trig}_nolack_tx': int((x.lacking == 0).sum()),
                    f'to{trig}_lacking': int(x.lacking.sum()), f'to{trig}_rescued': int(x.rescued.sum())}
        row['to_any_tb'] = len(any_tb)
        rows.append(row)
    win = pd.DataFrame(rows)
    assert win.n_pairs.sum() == len(k) and win.n_deliv.sum() == k.deliv.sum(), pre

    # --- SINR table (pairs with a first copy)
    kk = k[k.st1.notna()]
    b = np.where(kk.st1 == 0, SINR_MISS,
                 (np.floor(np.clip(kk.sinr1, SINR_LO, SINR_HI - 1e-9) / SINR_STEP) * SINR_STEP).astype(int))
    sinr = (kk.assign(bin=b).groupby(['bin', 'hfail']).deliv.agg(['size', 'sum'])
            .rename(columns={'size': 'n_pairs', 'sum': 'n_deliv'}).reset_index())
    for c, v in key.items():
        sinr.insert(0, c, v)

    # --- latency histogram of the pairs (delivered within the PDB; bin -1 = not)
    lb = np.where(k.deliv == 1, np.floor(k.delay_ms / LAT_STEP_MS), -1).astype(int)
    lat = pd.Series(lb).value_counts().rename_axis('bin').reset_index(name='n')
    for c, v in key.items():
        lat.insert(0, c, v)
    return dict(sinr=sinr, win=win, lat=lat)


def _job(args):
    pre, arm = args
    try:
        return run_tables(pre, arm)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f'{pre}: {e!r}') from e


def collect(out_dir, cache_dir, arms=None, jobs=4):
    """run_tables of every run in out_dir/<arm>/, cached per arm in cache_dir (<arm>_{sinr,win,lat}.csv);
    an arm is recomputed when its number of runs changes. Returns dict of concatenated frames."""
    from concurrent.futures import ProcessPoolExecutor
    os.makedirs(cache_dir, exist_ok=True)
    out = {'sinr': [], 'win': [], 'lat': []}
    for armdir in sorted(glob.glob(f'{out_dir}/*/')):
        arm = os.path.basename(armdir.rstrip('/'))
        if arms is not None and arm not in arms:
            continue
        pres = sorted({p.split('_run.csv')[0] for p in glob.glob(f'{armdir}*_run.csv*')})
        if not pres:
            continue
        fs = {t: f'{cache_dir}/{arm}_{t}.csv' for t in out}
        nrun = None
        if os.path.exists(fs['win']):
            wv = pd.read_csv(fs['win'], usecols=['sc', 'tr', 'nbg', 'seed'])
            nrun = len(wv.drop_duplicates())
        if nrun != len(pres):
            with ProcessPoolExecutor(jobs) as ex:
                res = list(ex.map(_job, [(p, arm) for p in pres], chunksize=4))
            for t in out:
                pd.concat([x[t] for x in res], ignore_index=True).to_csv(fs[t], index=False)
            print(arm, len(pres), 'runs', flush=True)
        for t in out:
            out[t].append(pd.read_csv(fs[t]))
    return {t: pd.concat(v, ignore_index=True) for t, v in out.items()}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(HERE, 'out', 'remote_v3'))
    ap.add_argument('--cache', default=os.path.join(HERE, 'out', 'cache_takeover_v3'))
    ap.add_argument('--arms', default=None, help='comma list')
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--one', default=None, help='run one prefix and print the tables')
    a = ap.parse_args()
    if a.one:
        t = run_tables(a.one, os.path.basename(os.path.dirname(a.one)))
        for n, d in t.items():
            print('==', n); print(d.to_string(max_rows=40))
    else:
        d = collect(a.out, a.cache, a.arms.split(',') if a.arms else None, a.jobs)
        print({k: len(v) for k, v in d.items()})
