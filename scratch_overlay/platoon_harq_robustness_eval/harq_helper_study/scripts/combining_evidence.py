#!/usr/bin/env python3
"""combining_evidence.py -- does cross-source soft combining show in the campaign data? (read-only on out/remote_full)

Compares R4.2 vs R4.2_noIR (helper copies, cross-source combining) and R3 vs R3_noIR (leader retransmissions,
same-source combining, positive control), per scenario / traffic / load, from _att.csv:

  check 1  among (TB, member) whose leader first copy was attempted and corrupt (status 2), the success rate of the
           next copy of the TB vs that copy's SINR (2 dB bins). With combining the curve must shift left.
           A second panel splits the next copy's success by the first copy's SINR (the EESM-IR averaging check:
           a very weak first copy lowers the combined effective SINR of a strong next copy).
  check 2  the failed first attempts (no success on the leader's first copy): SCI miss (status 0 and the member
           had not decoded the TB before) vs corrupt decode (status 2). Status 0 is also written for a member
           that already decoded the TB (no PHY trace for a held TB); the first copy cannot be such a case, and
           check 1's next copies only follow a corrupt first copy, so a status 0 there is always an SCI miss.

_att.csv status: 0 = no PSSCH decode attempt (SCI-1 missed, below MaxLossDb, or TB already decoded), 1 = ok,
2 = corrupt (data or SCI-2A failure; an SCI-2A failure leaves nothing in the HARQ buffer, not separable here).

Usage: python3 scripts/combining_evidence.py [--data out/remote_full] [--out figures/combining]
Writes <out>/check1_next_copy.csv, check1_by_first_sinr.csv, check2_first_failures.csv, the figures, summary.md.
"""
import argparse, glob, os
import numpy as np, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

PAIRS = [('R3', 'R3_noIR'), ('R4.2', 'R4.2_noIR')]
SC = {'hwy': 'highway', 'rbt': 'roundabout', 'rbtE': 'roundaboutE'}
BIN = 2.0


def cell_of(tag):
    sc, _, tr, bg, s = tag.split('_')
    return SC[sc], tr, bg, int(s[1:])


def load_arm(data, arm):
    """per (tb, member): first leader copy and the next transmission of the TB, with the KPI window applied."""
    rows_next, rows_first = [], []
    for f in sorted(glob.glob(f'{data}/{arm}/*_att.csv*')):
        tag = os.path.basename(f).split('_att.csv')[0]
        sc, tr, bg, seed = cell_of(tag)
        run = pd.read_csv(f'{data}/{arm}/{tag}_run.csv' + ('' if os.path.exists(f'{data}/{arm}/{tag}_run.csv') else '.gz'))
        k0, k1 = run.kpi_start.iloc[0], run.kpi_end.iloc[0]
        a = pd.read_csv(f, usecols=['side', 't', 'tb', 'tx_in_tb', 'ndi', 'member', 'status', 'sinr_db'])
        a = a[a.tb >= 0]
        first_t = a[(a.side == 0) & (a.ndi == 1)].groupby('tb').t.min()
        a = a[a.tb.map(first_t).between(k0, k1, inclusive='left')]
        a = a.sort_values(['tb', 'member', 't', 'side'])
        g = a.groupby(['tb', 'member'], sort=False)
        a['k'] = g.cumcount()
        first = a[(a.k == 0)]
        first = first[(first.side == 0) & (first.ndi == 1)]
        nxt = a[a.k == 1].set_index(['tb', 'member'])
        f0 = first.set_index(['tb', 'member'])
        # check 2 rows: first copy outcome
        rows_first.append(pd.DataFrame(dict(scenario=sc, traffic=tr, bg=bg, seed=seed, status=f0.status.values,
                                            sinr=f0.sinr_db.values)))
        # check 1 rows: first copy corrupt, the next transmission of the TB
        bad = f0[f0.status == 2]
        j = bad.join(nxt[['side', 'status', 'sinr_db']], rsuffix='_n', how='inner')
        rows_next.append(pd.DataFrame(dict(scenario=sc, traffic=tr, bg=bg, seed=seed, sinr0=j.sinr_db.values,
                                           side_n=j.side_n.values, status_n=j.status_n.values,
                                           sinr_n=j.sinr_db_n.values)))
    return pd.concat(rows_next, ignore_index=True), pd.concat(rows_first, ignore_index=True)


def wilson(k, n):
    if n == 0:
        return np.nan, np.nan, np.nan
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, c - h, c + h


def sinr50(df):
    """SINR (dB) where the binned success crosses 0.5 (linear interpolation over 2 dB bins with n >= 30)."""
    b = df.groupby('bin').ok.agg(['mean', 'size'])
    b = b[b['size'] >= 30]
    x, y = b.index.values * BIN + BIN / 2, b['mean'].values
    for i in range(1, len(x)):
        if y[i - 1] < 0.5 <= y[i]:
            return x[i - 1] + (0.5 - y[i - 1]) * (x[i] - x[i - 1]) / (y[i] - y[i - 1])
    return np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='out/remote_full')
    ap.add_argument('--out', default='figures/combining')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    N, F = {}, {}
    for pair in PAIRS:
        for arm in pair:
            N[arm], F[arm] = load_arm(a.data, arm)
            print(arm, len(N[arm]), 'failed-first (tb, member) pairs,', len(F[arm]), 'first copies')
    cells = ['scenario', 'traffic', 'bg']

    # ---------------- check 1: next copy after a corrupt first copy
    t1 = []
    for ir, no in PAIRS:
        for key, g in pd.concat([N[ir].assign(arm=ir), N[no].assign(arm=no)]).groupby(cells):
            row = dict(zip(cells, key), pair=f'{ir} vs {no}')
            for arm in (ir, no):
                d = g[g.arm == arm]
                att = d[d.status_n > 0].copy()
                att['ok'] = (att.status_n == 1).astype(int)
                att['bin'] = np.floor(att.sinr_n / BIN).astype(int)
                p, lo, hi = wilson(att.ok.sum(), len(att))
                sfx = 'IR' if arm == ir else 'noIR'
                row.update({f'n_{sfx}': len(d), f'next_sci_miss_{sfx}': round((d.status_n == 0).mean(), 3),
                            f'next_helper_{sfx}': round((d.side_n == 1).mean(), 3),
                            f'p_ok_{sfx}': round(p, 3), f'sinr50_{sfx}': round(sinr50(att), 1)})
            row['shift_db'] = round(row['sinr50_noIR'] - row['sinr50_IR'], 1)
            t1.append(row)
    T1 = pd.DataFrame(t1)
    T1.to_csv(f'{a.out}/check1_next_copy.csv', index=False)

    # pooled curves per pair and traffic (figure 1)
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), sharex=True, sharey=True)
    for i, (ir, no) in enumerate(PAIRS):
        for j, tr in enumerate(['periodic1tb', 'aperiodic1tb']):
            ax = axes[i, j]
            for arm, col in ((ir, '#e34948'), (no, '#2a78d6')):
                d = N[arm][(N[arm].traffic == tr) & (N[arm].status_n > 0)].copy()
                d['ok'] = (d.status_n == 1).astype(int)
                d['bin'] = np.floor(d.sinr_n / BIN).astype(int)
                b = d.groupby('bin').ok.agg(['sum', 'size'])
                b = b[b['size'] >= 30]
                ci = np.array([wilson(k, n) for k, n in zip(b['sum'], b['size'])])
                x = b.index.values * BIN + BIN / 2
                ax.plot(x, ci[:, 0], 'o-', color=col, label=f'{arm} (n={len(d)}, SINR50 {sinr50(d):.1f} dB)')
                ax.fill_between(x, ci[:, 1], ci[:, 2], color=col, alpha=.15, lw=0)
            # reference: success of a fresh first copy at the same SINR (no history), pooled IR + noIR
            fr = pd.concat([F[ir], F[no]])
            fr = fr[(fr.traffic == tr) & (fr.status > 0)].copy()
            fr['ok'] = (fr.status == 1).astype(int)
            fr['bin'] = np.floor(fr.sinr / BIN).astype(int)
            b = fr.groupby('bin').ok.agg(['mean', 'size'])
            b = b[b['size'] >= 30]
            ax.plot(b.index.values * BIN + BIN / 2, b['mean'], 'k:', lw=1.2, label=f'first copy, no history (SINR50 {sinr50(fr):.1f} dB)')
            ax.set_title(f'{ir} vs {no}, {tr}: next copy after a corrupt first copy', fontsize=9)
            ax.set_xlim(-6, 30); ax.grid(alpha=.3); ax.legend(fontsize=7, loc='lower right')
            ax.set_xlabel('SINR of the next copy (dB)'); ax.set_ylabel('P(next copy decodes)')
    fig.suptitle('Check 1: success of the next copy vs its SINR (all scenarios / loads pooled; Wilson 95 % CI; bins n >= 30)')
    fig.tight_layout(); fig.savefig(f'{a.out}/check1_next_copy.png', dpi=130); plt.close(fig)

    # check 1b: next copy's success by the FIRST copy's SINR, at a fixed next-copy SINR band (EESM-IR averaging)
    t1b = []
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
    for ax, (ir, no) in zip(axes, PAIRS):
        for arm, col in ((ir, '#e34948'), (no, '#2a78d6')):
            d = N[arm][N[arm].status_n > 0].copy()
            d['ok'] = (d.status_n == 1).astype(int)
            for lo_n, hi_n, ls in ((8, 12, '-'), (12, 18, '--')):
                s = d[(d.sinr_n >= lo_n) & (d.sinr_n < hi_n) & (d.sinr0 > -90)].copy()
                s['b0'] = np.floor(s.sinr0 / 3.0).astype(int)
                b = s.groupby('b0').ok.agg(['sum', 'size'])
                b = b[b['size'] >= 30]
                for b0, (k, n) in b.iterrows():
                    p, l, h = wilson(k, n)
                    t1b.append(dict(arm=arm, next_sinr=f'[{lo_n},{hi_n})', first_sinr_bin=f'[{3 * b0},{3 * b0 + 3})',
                                    n=n, p_ok=round(p, 3), lo=round(l, 3), hi=round(h, 3)))
                ax.plot(b.index.values * 3 + 1.5, b['sum'] / b['size'], ls, marker='o', color=col,
                        label=f'{arm}, next copy SINR [{lo_n},{hi_n}) dB')
        ax.set_title(f'{ir} vs {no}', fontsize=9); ax.grid(alpha=.3); ax.legend(fontsize=7)
        ax.set_xlabel('SINR of the corrupt first copy (dB)'); ax.set_ylabel('P(next copy decodes)')
    fig.suptitle('Check 1b: next-copy success vs the SINR of the failed first copy (EESM-IR combining averages the copies)')
    fig.tight_layout(); fig.savefig(f'{a.out}/check1b_by_first_sinr.png', dpi=130); plt.close(fig)
    pd.DataFrame(t1b).to_csv(f'{a.out}/check1_by_first_sinr.csv', index=False)

    # ---------------- check 2: failed first attempts: SCI miss vs corrupt decode
    t2 = []
    for ir, no in PAIRS:
        for arm in (ir, no):
            for key, g in F[arm].groupby(cells):
                fail = g[g.status != 1]
                t2.append(dict(zip(cells, key), arm=arm, first_copies=len(g), failed=len(fail),
                               sci_miss=int((fail.status == 0).sum()), corrupt=int((fail.status == 2).sum()),
                               sci_miss_share=round((fail.status == 0).mean(), 3) if len(fail) else np.nan))
    T2 = pd.DataFrame(t2)
    T2.to_csv(f'{a.out}/check2_first_failures.csv', index=False)
    fig, ax = plt.subplots(figsize=(8, 6))
    p = T2[T2.arm.isin(['R3', 'R4.2'])].pivot_table(index=cells, columns='arm', values='sci_miss_share')
    short = {'highway': 'hwy', 'roundabout': 'rbt W', 'roundaboutE': 'rbt E', 'periodic1tb': 'periodic',
             'aperiodic1tb': 'aperiodic', 'none': '0 cars', 'moderate': '60 cars'}
    p.index = [' / '.join(short[k] for k in key) for key in p.index]
    p[::-1].plot.barh(ax=ax, color=['#1baf7a', '#008300'])
    ax.set_xlabel('SCI miss / failed first attempts'); ax.set_xlim(0, 0.5); ax.grid(axis='x', alpha=.3)
    ax.set_title('Check 2: failed first attempts with no decode attempt\n(SCI miss: nothing in the buffer to combine)', fontsize=10)
    fig.tight_layout(); fig.savefig(f'{a.out}/check2_sci_miss_share.png', dpi=130); plt.close(fig)

    # ---------------- check 4: decomposition of the PRR effect of combining: share of member-TBs whose first copy was
    # corrupt AND got a next copy (combining can act only there) x the gain on that copy, vs the observed PRR change
    t4 = []
    for ir, no in PAIRS:
        prr = {}
        for arm in (ir, no):
            rr = []
            for f in sorted(glob.glob(f'{a.data}/{arm}/*_run.csv*')):
                tag = os.path.basename(f).split('_run.csv')[0]
                sc, tr, bg, seed = cell_of(tag)
                rr.append(dict(scenario=sc, traffic=tr, bg=bg, prr=pd.read_csv(f).prr_mean.iloc[0]))
            prr[arm] = pd.DataFrame(rr).groupby(cells).prr.mean()
        for key, g in T1[T1.pair == f'{ir} vs {no}'].groupby(cells):
            row = g.iloc[0]
            nf = F[ir].set_index(cells).loc[key]
            pairs_tot = len(nf)
            share = row.n_IR / pairs_tot
            dp = row.p_ok_IR - row.p_ok_noIR
            t4.append(dict(zip(cells, key), pair=f'{ir} vs {no}', member_tbs=pairs_tot,
                           first_corrupt_share=round((nf.status == 2).mean(), 3),
                           with_next_copy_share=round(share, 3), next_copy_gain=round(dp, 3),
                           implied_dprr=round(share * dp, 3),
                           observed_dprr=round(prr[ir].loc[key] - prr[no].loc[key], 3)))
    T4 = pd.DataFrame(t4)
    T4.to_csv(f'{a.out}/check4_decomposition.csv', index=False)
    print(T4.to_string(index=False))

    # ---------------- check 3: HARQ-id reuse before a helper copy (copy of TB k after the leader's NDI = 1 of a newer TB
    # on the same HARQ id: the members' buffer would hold the newer TB when the copy arrives)
    t3 = []
    for arm in ('R4', 'R4.1', 'R4.2', 'R5', 'R6', 'R4.1_noIR', 'R4.2_noIR'):
        n_copies = n_reuse = runs = 0
        lag_max = 0.0
        for f in sorted(glob.glob(f'{a.data}/{arm}/*_htx.csv*')):
            tag = os.path.basename(f).split('_htx.csv')[0]
            x = pd.read_csv(f)
            l = pd.read_csv(f'{a.data}/{arm}/{tag}_ltx.csv' + ('' if os.path.exists(f'{a.data}/{arm}/{tag}_ltx.csv') else '.gz'))
            at = pd.read_csv(f'{a.data}/{arm}/{tag}_att.csv' + ('' if os.path.exists(f'{a.data}/{arm}/{tag}_att.csv') else '.gz'),
                             usecols=['side', 'slot_abs', 'tb', 'ndi'])
            tb_of = at.drop_duplicates(['side', 'slot_abs']).set_index(['side', 'slot_abs']).tb
            x['tb'] = [tb_of.get((1, s_), -1) for s_ in x.slot_abs]
            l1 = l[l.ndi == 1].copy()
            l1['tb_new'] = [tb_of.get((0, s_), -1) for s_ in l1.slot_abs]
            x = x[x.tb >= 0].sort_values('slot_abs')
            l1 = l1.sort_values('slot_abs')
            m = pd.merge_asof(x, l1[['slot_abs', 'harq_id', 'tb_new']], on='slot_abs', by='harq_id',
                              direction='backward', allow_exact_matches=False)
            first = l1.set_index('tb_new').slot_abs
            lag = (x.slot_abs - x.tb.map(first)) * 0.5
            lag_max = max(lag_max, lag.max() if len(lag) else 0.0)
            n_copies += len(m)
            n_reuse += int(((m.tb_new.notna()) & (m.tb_new != m.tb)).sum())
            runs += 1
        t3.append(dict(arm=arm, runs=runs, helper_copies=n_copies, copies_after_newer_tb_same_harq=n_reuse,
                       max_copy_lag_ms=lag_max))
    T3 = pd.DataFrame(t3)
    T3.to_csv(f'{a.out}/check3_harq_reuse.csv', index=False)
    print(T3.to_string(index=False))

    with open(f'{a.out}/summary.md', 'w') as fo:
        fo.write('## Check 1: next copy after a corrupt first copy (SINR50 = SINR where the binned success crosses 0.5)\n\n')
        fo.write('```\n' + T1.to_string(index=False) + '\n```\n\n')
        fo.write('## Check 2: failed first attempts, SCI miss vs corrupt decode\n\n')
        fo.write('```\n' + T2.to_string(index=False) + '\n```\n\n')
        fo.write('## Check 4: PRR effect of combining = share of member-TBs with a corrupt first copy and a next copy x gain on that copy\n\n')
        fo.write('```\n' + T4.to_string(index=False) + '\n```\n\n')
        fo.write('## Check 3: helper copies of TB k sent after the leader started a newer TB on the same HARQ id\n\n')
        fo.write('```\n' + T3.to_string(index=False) + '\n```\n')
    print(T1.to_string(index=False))
    print(T2[T2.arm.isin(['R3', 'R4.2'])].to_string(index=False))


if __name__ == '__main__':
    main()
