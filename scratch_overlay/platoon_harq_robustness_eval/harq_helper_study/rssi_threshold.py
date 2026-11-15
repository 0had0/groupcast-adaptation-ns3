"""RSSI-gated fallback: what each fallback check led to, and what a threshold rule would change.

Reads, per R6 run in a folder: <cell>_fbchk.csv (one row per leader fallback check, written since commit 40a5601),
_ltx.csv / _htx.csv (every leader / helper PSSCH), _att.csv (every platoon PSSCH x member: 0 no attempt,
1 decoded, 2 failed) and _run.csv (KPI window).

Per check (checked slot h = the helper's handed slot):
  cls     A  helper transmitted in h and the leader decoded its SCI      (no fallback)
          B  helper transmitted in h, SCI not decoded                    (false fallback)
          C  helper silent in h                                          (true fallback)
  s3      the TB's next copy after h (the slot the fallback takes), who sent it: L, H or LH (double)
  n_need  members that had not decoded the TB up to h
  n_rec3  of them, decoded at s3
RSSI per RB on the handed subchannels = helper + other transmitters + noise (what a UE measures, no identity);
p_helper_rb: the helper's received PSSCH power per RB, the PSSCH-RSRP a UE measures when it decodes the SCI.
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

NOISE_DBM_PER_RB = -174 + 10 * np.log10(12 * 30e3) + 5.0   # 30 kHz SCS, UE noise figure 5 dB (NrUePhy default)
MEMBERS = 8


def _w(dbm):
    dbm = np.asarray(dbm, float)
    return np.where(dbm <= -998, 0.0, 10 ** ((dbm - 30) / 10))


def _db(w):
    with np.errstate(divide='ignore'):
        return 10 * np.log10(np.asarray(w, float)) + 30


def load_run(pre: str) -> pd.DataFrame:
    r = pd.read_csv(pre + '_run.csv').iloc[0]
    fc = pd.read_csv(pre + '_fbchk.csv')
    fc = fc[(fc.t >= r.kpi_start) & (fc.t < r.kpi_end) & fc.outcome.isin([0, 1, 2])].copy()
    lt = pd.read_csv(pre + '_ltx.csv')
    ht = pd.read_csv(pre + '_htx.csv')
    at = pd.read_csv(pre + '_att.csv')
    tb_of_slot = at.drop_duplicates(['side', 'slot_abs']).set_index(['side', 'slot_abs']).tb
    lslots, hslots = set(lt.slot_abs), set(ht.slot_abs)
    att_tb = {k: g for k, g in at.groupby('tb')}
    rows = []
    for c in fc.itertuples():
        prev = lt[(lt.harq_id == c.harq_id) & (lt.slot_abs < c.slot_abs) & (lt.slot_abs > c.slot_abs - 64)]
        if prev.empty:
            continue
        tb = tb_of_slot.get((0, int(prev.slot_abs.max())), -1)
        g = att_tb.get(tb)
        if g is None or tb < 0:
            continue
        later = g[g.slot_abs > c.slot_abs]
        s3 = int(later.slot_abs.min()) if len(later) else -1
        held = set(g[(g.slot_abs <= c.slot_abs) & (g.status == 1)].member)
        need = [m for m in range(1, MEMBERS + 1) if m not in held]
        rec3 = set(later[(later.slot_abs == s3) & (later.status == 1)].member) if s3 >= 0 else set()
        who = ('L' if s3 in lslots else '') + ('H' if s3 in hslots else '')
        rows.append(dict(t=c.t, slot=c.slot_abs, harq_id=c.harq_id, tb=tb, helper_rnti=c.helper_rnti,
                         outcome=c.outcome, sci_failed=c.sci_failed, n_rb=10 * c.sb_len,
                         p_helper=c.p_data_helper_dbm, p_other=c.p_data_other_dbm, s3=s3,
                         s3_gap=(s3 - c.slot_abs) if s3 >= 0 else -1, s3_who=who or '-',
                         n_need=len(need), n_rec3=len([m for m in need if m in rec3])))
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    helper_tx = d.p_helper > -998
    d['cls'] = np.where(helper_tx & (d.outcome == 0), 'A', np.where(helper_tx, 'B', 'C'))
    tot = _w(d.p_helper) + _w(d.p_other)
    d['rssi'] = _db(tot / d.n_rb + _w(NOISE_DBM_PER_RB))
    d['p_helper_rb'] = np.where(helper_tx, _db(_w(d.p_helper) / d.n_rb), np.nan)
    d['p_other_rb'] = np.where(d.p_other > -998, _db(_w(d.p_other) / d.n_rb), np.nan)
    d['tbs'] = int(r.pkts)
    return d


def load(folder: str) -> pd.DataFrame:
    out = []
    for f in sorted(glob.glob(f'{folder}/*_fbchk.csv')):
        pre = f[:-len('_fbchk.csv')]
        d = load_run(pre)
        cell = os.path.basename(pre)
        sc, _, tr, bg, s = cell.split('_')
        out.append(d.assign(cell=cell, route={'hwy': 'Highway', 'rbt': 'Roundabout W', 'rbtE': 'Roundabout E'}[sc],
                            traffic=tr.replace('1tb', ''), seed=int(s[1:])))
    return pd.concat(out, ignore_index=True)


def helper_reference(d: pd.DataFrame, alpha: float = 0.1) -> pd.Series:
    """Causal EWMA (dB) of the helper's PSSCH-RSRP over the earlier checks of the same run and helper in which the
    leader decoded the helper's SCI (class A): what the leader knows before the current check. NaN until one."""
    ref = pd.Series(np.nan, index=d.index)
    for _, g in d.sort_values('t').groupby(['cell', 'helper_rnti']):
        est = np.nan
        for i, row in g.iterrows():
            ref[i] = est
            if row.cls == 'A' and np.isfinite(row.p_helper_rb):
                est = row.p_helper_rb if not np.isfinite(est) else (1 - alpha) * est + alpha * row.p_helper_rb
    return ref


def rule_suppress(d: pd.DataFrame, kind: str, x: float) -> pd.Series:
    """True where the rule withholds the fallback (treats the helper as present). Only no-SCI checks matter.
    fixed:    RSSI >= x dBm
    relative: RSSI >= helper reference - x dB (fixed -95 dBm until the first reference)"""
    if kind == 'fixed':
        return d.rssi >= x
    ref = d['helper_ref']
    thr = np.where(np.isfinite(ref), ref - x, -95.0)
    return d.rssi >= thr


def evaluate(d: pd.DataFrame, suppress: pd.Series, r_alone: dict) -> dict:
    """Effect of withholding the fallback where `suppress` is True (B and C checks only).
    B with a double (s3 = LH): the helper's copy goes alone; its recoveries are estimated with r_alone[cell], the
    recovery rate of a helper copy sent alone at s3 after a decoded helper SCI (class A, s3 = H), per member in
    need. B with the leader alone at s3 (the helper had stopped): the slot goes silent, those recoveries are lost.
    C: the slot goes silent, the fallback's recoveries are lost."""
    acted = d[d.cls.isin(['B', 'C'])]
    s = suppress.loc[acted.index]
    b, c = acted[acted.cls == 'B'], acted[acted.cls == 'C']
    sb, sc = s.loc[b.index], s.loc[c.index]
    bd, bl = b[b.s3_who == 'LH'], b[b.s3_who != 'LH']
    gain = sum(row.n_need * r_alone.get(row.cell, np.nan) - row.n_rec3 for row in bd[sb.loc[bd.index]].itertuples())
    lost = bl[sb.loc[bl.index]].n_rec3.sum() + c[sc].n_rec3.sum()
    tbs = d.drop_duplicates('cell').tbs.sum()
    return dict(doubles_avoided=int(sb.loc[bd.index].sum()), doubles=len(bd),
                true_fb_lost=int(sc.sum()), true_fb=len(c),
                pairs_gained=gain, pairs_lost=float(lost), net_pairs=gain - lost,
                net_pp=100 * (gain - lost) / (tbs * MEMBERS),
                tx_saved=int(s.sum()))


def r_alone_by_cell(d: pd.DataFrame) -> dict:
    a = d[(d.cls == 'A') & (d.s3_who == 'H') & (d.n_need > 0)]
    g = a.groupby('cell')[['n_rec3', 'n_need']].sum()
    return (g.n_rec3 / g.n_need).to_dict()


if __name__ == '__main__':
    import sys
    d = load(sys.argv[1] if len(sys.argv) > 1 else 'out/rssi_v3')
    print(d.groupby(['cls', 's3_who']).agg(n=('t', 'size'), need=('n_need', 'sum'), rec=('n_rec3', 'sum')))
    ra = r_alone_by_cell(d)
    print({k[:24]: round(v, 3) for k, v in ra.items()})
    for x in (-105, -100, -95, -90, -85, -80):
        print(x, evaluate(d, rule_suppress(d, 'fixed', x), ra))
