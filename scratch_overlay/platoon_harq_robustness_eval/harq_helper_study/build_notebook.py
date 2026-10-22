#!/usr/bin/env python3
"""Build and execute STUDY_RESULTS.ipynb from a study profile's outputs.

Usage: build_notebook.py [--profile local1|full|smoke] [--out DIR] [--no-exec]
"""
import argparse, os, subprocess, sys
import nbformat as nbf

HERE = os.path.dirname(os.path.abspath(__file__))

LOAD = r'''
import csv, glob, gzip, json, os
import numpy as np, pandas as pd, matplotlib.pyplot as plt
OUT = os.environ.get('STUDY_OUT', %(out)r)
pd.set_option('display.width', 200); pd.set_option('display.max_columns', 40)
SLOT = 0.5e-3; NSUB = 5; NMEM = 8
ORDER = ['R1', 'R2', 'R3', 'R4', 'R4.1', 'R4.2', 'R5', 'R6']
LABEL = {'R1': 'R1 leader blind', 'R2': 'R2 leader Opt1', 'R3': 'R3 leader Opt2',
         'R4': 'R4 Kuo blind', 'R4.1': 'R4.1 Kuo+Opt1', 'R4.2': 'R4.2 Kuo+Opt2',
         'R5': 'R5 Kuo+Opt1+takeover', 'R6': 'R6 Kuo+Opt2+takeover'}
STYLE = {'R1': ('tab:gray', ':'), 'R2': ('tab:blue', ':'), 'R3': ('tab:green', ':'),
         'R4': ('tab:gray', '--'), 'R4.1': ('tab:blue', '--'), 'R4.2': ('tab:green', '--'),
         'R5': ('tab:blue', '-'), 'R6': ('tab:green', '-')}

def rd(p):
    p = p if os.path.exists(p) else p + '.gz'
    if not os.path.exists(p):
        return None
    return pd.read_csv(p, compression='gzip' if p.endswith('.gz') else None)

def cell_of(tag):
    sc, ivd, tr, bg, s = tag.split('_')
    return dict(scenario={'hwy': 'highway', 'rbt': 'roundabout', 'rbtE': 'roundaboutE'}[sc], traffic=tr, bg=bg, seed=int(s[1:]))

def reserved_subch_slots(gr, k0, k1):
    """Leader reservations announced in SCI-1A over the KPI window [k0, k1) (s): every created grant
    reserves sb_len x slots per period; an SB-SPS grant repeats every RRI until the next creation."""
    if gr is None or len(gr) == 0:
        return np.nan
    c = gr[(gr.node == 0) & (gr.event == 'c')].sort_values('t').reset_index(drop=True)
    tot = 0.0
    for i, g in c.iterrows():
        per = g.sb_len * g.slots_or_counter
        if g.sps == 1 and g.rri_ms > 0:
            t_end = c.t[i + 1] if i + 1 < len(c) else k1
            a, b = max(g.t, k0), min(t_end, k1)
            if b > a:
                tot += per * (b - a) / (g.rri_ms * 1e-3)
        elif k0 <= g.t < k1:
            tot += per
    return tot

runs, members, rec = [], [], []
for armdir in sorted(glob.glob(os.path.join(OUT, '*', ''))):
    arm = os.path.basename(armdir.rstrip('/'))
    for p in sorted(glob.glob(armdir + '*_run.csv*')):
        tag = os.path.basename(p).split('_run.csv')[0]
        pre = os.path.join(armdir, tag)
        r = rd(pre + '_run.csv').iloc[0].to_dict()
        c = cell_of(tag)
        k0, k1 = r['kpi_start'], r['kpi_end']
        slots = (k1 - k0) / SLOT
        used = r['res_used_leader'] + r['res_used_helper']
        resv = reserved_subch_slots(rd(pre + '_grants.csv'), k0, k1)
        ntb = max(r['leader_new_tb'], 1)
        row = dict(arm=arm, tag=tag, **c, prr=r['prr_mean'], prr_worst=r['prr_min'], pkts=r['pkts'],
                   tx_per_pkt=used / r['pkts'], resv_per_pkt=resv / r['pkts'],
                   cbr_platoon=used / (slots * NSUB), cbr_total=r['cbr'],
                   psfch_per_tb=(r['nack_rx'] + r['explicit_ack_rx']) / ntb,
                   helper_nack=r['helper_nack'], helper_reclaim=r['helper_reclaim'],
                   fallback_check=r['fallback_check'], fallback_heard=r['fallback_heard'],
                   fallback_tx=r['fallback_tx'], fallback_no_slot=r['fallback_no_slot'])
        m = rd(pre + '_member.csv')
        m = m[m.member != 'all'].copy()
        m['member'] = m.member.astype(int)
        row['prr_pdb'] = m.prr_pdb.mean()
        row['prr_pdb_worst'] = m.prr_pdb.min()
        for _, x in m.iterrows():
            members.append(dict(arm=arm, **c, member=x.member, dist=x.mean_dist_m, prr=x.prr, prr_pdb=x.prr_pdb))
        # member-packets first delivered by a leader retransmission in a handed slot it took back
        ltx, att = rd(pre + '_ltx.csv'), rd(pre + '_att.csv')
        rec1 = rec2 = 0
        if ltx is not None and att is not None and 'origin' in ltx and (ltx.origin > 0).any():
            org = dict(zip(ltx.slot_abs, ltx.origin))
            a = att[(att.t >= k0) & (att.t < k1)].sort_values('t')
            first = a[a.status == 1].groupby(['tb', 'member']).first().reset_index()
            for _, x in first.iterrows():
                if x.side == 0:
                    o = org.get(x.slot_abs, 0)
                    rec1 += o == 1
                    rec2 += o == 2
        row['recovered_nack'] = rec1 / (r['pkts'] * NMEM)
        row['recovered_fallback'] = rec2 / (r['pkts'] * NMEM)
        runs.append(row)
R = pd.DataFrame(runs); M = pd.DataFrame(members)
CELLS = ['scenario', 'traffic', 'bg']
print(f'{len(R)} runs, seeds per cell: {R.groupby(CELLS + ["arm"]).seed.nunique().min()}-{R.groupby(CELLS + ["arm"]).seed.nunique().max()}')
'''

DIST = r'''
def dist_plot(arms, title, metric='prr'):
    scen = [s for s in ['highway', 'roundabout', 'roundaboutE'] if s in set(M.scenario)]
    trafs, bgs = ['periodic1tb', 'aperiodic1tb'], ['none', 'moderate']
    fig, axes = plt.subplots(len(scen) * len(trafs), len(bgs), figsize=(12, 13), sharey=True)
    for i, (sc, tr) in enumerate([(s, t) for s in scen for t in trafs]):
        for j, bg in enumerate(bgs):
            ax = axes[i, j]
            d = M[(M.scenario == sc) & (M.traffic == tr) & (M.bg == bg)]
            for arm in arms:
                g = d[d.arm == arm].groupby('member')[['dist', metric]].mean().sort_values('dist')
                if len(g):
                    col, ls = STYLE[arm.replace('_noIR', '')]
                    ax.plot(g.dist, g[metric], ls if '_noIR' not in arm else '-.', color=col, marker='o', ms=3,
                            label=LABEL.get(arm, arm))
            ax.set_title(f'{sc} / {tr} / bg {bg}', fontsize=9)
            ax.grid(alpha=.3)
            if j == 0: ax.set_ylabel(metric.upper())
            if i == len(scen) * len(trafs) - 1: ax.set_xlabel('mean distance from the leader (m)')
    axes[0, 0].legend(fontsize=7, loc='lower left')
    fig.suptitle(title); fig.tight_layout(); plt.show()
'''

SUMMARY = r'''
agg = R.groupby(CELLS + ['arm']).agg(seeds=('seed', 'nunique'), prr=('prr', 'mean'), prr_worst=('prr_worst', 'mean'),
        prr_pdb=('prr_pdb', 'mean'), prr_pdb_worst=('prr_pdb_worst', 'mean'), tx_per_pkt=('tx_per_pkt', 'mean'), resv_per_pkt=('resv_per_pkt', 'mean'),
        cbr_platoon=('cbr_platoon', 'mean'), cbr_total=('cbr_total', 'mean'), psfch_per_tb=('psfch_per_tb', 'mean')).reset_index()
main = agg[agg.arm.isin(ORDER)].copy()
main['arm'] = pd.Categorical(main.arm, ORDER)
main.sort_values(CELLS + ['arm']).round(3)
'''

CBR = r'''
ref = {'R1': 'R1', 'R2': 'R1', 'R3': 'R1', 'R4': 'R4', 'R4.1': 'R4', 'R4.2': 'R4', 'R5': 'R4', 'R6': 'R4'}
rows = []
for (sc, tr, bg), g in agg[agg.arm.isin(ORDER)].groupby(CELLS):
    g = g.set_index('arm')
    for arm in ORDER:
        if arm in g.index and ref[arm] in g.index:
            b = g.loc[ref[arm]]
            rows.append(dict(scenario=sc, traffic=tr, bg=bg, arm=arm, vs=ref[arm],
                cbr_platoon=g.loc[arm, 'cbr_platoon'],
                cbr_saving_pct=100 * (1 - g.loc[arm, 'cbr_platoon'] / b.cbr_platoon),
                tx_saving_pct=100 * (1 - g.loc[arm, 'tx_per_pkt'] / b.tx_per_pkt),
                resv_saving_pct=100 * (1 - g.loc[arm, 'resv_per_pkt'] / b.resv_per_pkt),
                d_prr=g.loc[arm, 'prr'] - b.prr, d_prr_worst=g.loc[arm, 'prr_worst'] - b.prr_worst))
CB = pd.DataFrame(rows)
CB.round(3)
'''

IR = r'''
rows = []
for (sc, tr, bg), g in agg.groupby(CELLS):
    g = g.set_index('arm')
    for arm in ['R2', 'R3', 'R4.1', 'R4.2']:
        if arm in g.index and arm + '_noIR' in g.index:
            a, b = g.loc[arm], g.loc[arm + '_noIR']
            rows.append(dict(scenario=sc, traffic=tr, bg=bg, arm=arm, prr_IR=a.prr, prr_noIR=b.prr,
                             gain_prr=a.prr - b.prr, gain_prr_worst=a.prr_worst - b.prr_worst,
                             tx_per_pkt_IR=a.tx_per_pkt, tx_per_pkt_noIR=b.tx_per_pkt))
IRG = pd.DataFrame(rows)
display(IRG.round(4))
if len(IRG):
    p = IRG.pivot_table(index=['scenario', 'traffic', 'bg'], columns='arm', values='gain_prr')
    p.plot.bar(figsize=(10, 4), title='HARQ-IR (soft combining) PRR gain: arm minus arm_noIR'); plt.grid(alpha=.3); plt.show()
'''

TAKE = r'''
tk = R[R.arm.isin(['R5', 'R6'])].groupby(CELLS + ['arm']).agg(
        helper_nack=('helper_nack', 'mean'), helper_reclaim=('helper_reclaim', 'mean'),
        fallback_check=('fallback_check', 'mean'), fallback_heard=('fallback_heard', 'mean'),
        fallback_tx=('fallback_tx', 'mean'), fallback_no_slot=('fallback_no_slot', 'mean'),
        recovered_nack=('recovered_nack', 'mean'), recovered_fallback=('recovered_fallback', 'mean')).reset_index()
base = {'R5': 'R4.1', 'R6': 'R4.2'}
a = agg.set_index(CELLS + ['arm'])
tk['d_prr_vs_no_takeover'] = [a.prr.get((r.scenario, r.traffic, r.bg, r.arm), np.nan) -
                              a.prr.get((r.scenario, r.traffic, r.bg, base[r.arm]), np.nan) for r in tk.itertuples()]
tk['d_prr_worst_vs_no_takeover'] = [a.prr_worst.get((r.scenario, r.traffic, r.bg, r.arm), np.nan) -
                                    a.prr_worst.get((r.scenario, r.traffic, r.bg, base[r.arm]), np.nan) for r in tk.itertuples()]
display(tk.round(4))
x = tk.set_index(CELLS + ['arm'])[['recovered_nack', 'recovered_fallback']]
x.plot.bar(stacked=True, figsize=(11, 4), title='PRR recovered by the leader takeover (member-packets / packets x members)')
plt.grid(alpha=.3); plt.show()
'''

SPECTRUM = r'''
# Spectrum use per generated packet, split by what occupies it (PRB x symbols, KPI window).
# PSSCH (+PSCCH) of a transmission: rb_len PRBs x 9 symbols (every slot carries PSFCH: psfchPeriod 1).
# PSFCH: 1 PRB x 2 symbols per resource. Option 1: one shared NACK resource per transmission that drew
# >= 1 member NACK (the R5 helper NACKs on its own resource); Option 2: one resource per ACK / NACK.
PSSCH_SYM, PSFCH_RE = 9, 2
OPT = {'R1': 0, 'R2': 1, 'R3': 2, 'R4': 0, 'R4.1': 1, 'R4.2': 2, 'R5': 1, 'R6': 2}
COMP = ['leader initial', 'leader retx', 'leader takeover (helper NACK)', 'leader takeover (fallback)',
        'helper relay', 'helper feedback (NACK)', 'members feedback']
CCOL = ['#4c72b0', '#8fa9d6', '#dd8452', '#c44e52', '#55a868', '#937860', '#8172b3']

def helper_at(hs, harq, t):
    if hs is None:
        return None
    h = hs[(hs.harq_id == harq) & (hs.t_ndi <= t + 1e-9)]
    return int(h.applied.iloc[-1]) if len(h) else None

comp_rows = []
for armdir in sorted(glob.glob(os.path.join(OUT, '*', ''))):
    arm = os.path.basename(armdir.rstrip('/'))
    if arm not in OPT:
        continue
    for p in sorted(glob.glob(armdir + '*_run.csv*')):
        tag = os.path.basename(p).split('_run.csv')[0]
        pre = os.path.join(armdir, tag)
        r = rd(pre + '_run.csv').iloc[0]
        k0, k1 = r.kpi_start, r.kpi_end
        win = lambda df: df[(df.t >= k0) & (df.t < k1)] if df is not None and len(df) else df
        ltx, htx = win(rd(pre + '_ltx.csv')), win(rd(pre + '_htx.csv'))
        nack, hs = win(rd(pre + '_nack.csv')), rd(pre + '_hsel.csv')
        c = dict.fromkeys(COMP, 0.0)
        if ltx is not None and len(ltx):
            re_ = ltx.rb_len * PSSCH_SYM
            org = ltx.origin if 'origin' in ltx else 0 * ltx.ndi
            c['leader initial'] = re_[ltx.ndi == 1].sum()
            c['leader retx'] = re_[(ltx.ndi == 0) & (org == 0)].sum()
            c['leader takeover (helper NACK)'] = re_[org == 1].sum()
            c['leader takeover (fallback)'] = re_[org == 2].sum()
        if htx is not None and len(htx):
            c['helper relay'] = (htx.rb_len * PSSCH_SYM).sum()
        takeover = arm in ('R5', 'R6')
        n_help = n_mem = 0
        occ = set()
        if nack is not None and len(nack):
            for x in nack.itertuples():
                h = helper_at(hs, x.harq_id, x.t) if takeover else None
                if takeover and x.member == h:
                    n_help += 1
                else:
                    n_mem += 1
                    occ.add((x.tx_rnti, x.harq_id, round(x.t / SLOT)))
        if OPT[arm] == 1:
            c['members feedback'] = PSFCH_RE * len(occ)
        elif OPT[arm] == 2:
            # ACKs are only counted for the whole run: scale to the KPI window by the share of TBs
            all_ltx = rd(pre + '_ltx.csv')
            frac = (ltx.ndi == 1).sum() / max((all_ltx.ndi == 1).sum(), 1)
            c['members feedback'] = PSFCH_RE * (n_mem + r.explicit_ack_rx * frac)
        c['helper feedback (NACK)'] = PSFCH_RE * n_help
        cc = cell_of(tag)
        comp_rows.append(dict(arm=arm, **cc, **{k: v / r.pkts for k, v in c.items()}))
SP = pd.DataFrame(comp_rows).groupby(CELLS + ['arm'])[COMP].mean().reset_index()

SUBCH_SLOT = 10 * PSSCH_SYM  # one subchannel (10 PRB) over the PSSCH symbols of a slot

def spectrum_plot(cols, title, ylabel, scale=1.0, prr=False):
    prr_handles = ([], [])
    cells = SP[CELLS].drop_duplicates().sort_values(CELLS).values.tolist()
    fig, axes = plt.subplots(2, 4, figsize=(17, 8.5), sharey=False)
    for ax, (sc, tr, bg) in zip(axes.flat, cells):
        d = SP[(SP.scenario == sc) & (SP.traffic == tr) & (SP.bg == bg)].set_index('arm').reindex(
            [a for a in ORDER if a in set(SP.arm)])
        d = d[COMP] / scale
        bottom = np.zeros(len(d))
        for comp, col in zip(COMP, CCOL):
            if comp in cols:
                ax.bar(d.index, d[comp].fillna(0), bottom=bottom, color=col, label=comp, edgecolor='white', lw=.5)
                bottom += d[comp].fillna(0).values
        for i, tot in enumerate(bottom):
            ax.text(i, tot, f'{tot:.0f}' if tot >= 100 else (f'{tot:.1f}' if tot >= 1 else f'{tot:.3f}'),
                    ha='center', va='bottom', fontsize=7)
        if prr:
            q = agg[(agg.scenario == sc) & (agg.traffic == tr) & (agg.bg == bg)].set_index('arm').reindex(d.index)
            ax2 = ax.twinx()
            x = np.arange(len(d))
            pm, pw, tag = (q.prr_pdb, q.prr_pdb_worst, 'PRR within PDB') if prr == 'pdb' else (q.prr, q.prr_worst, 'PRR')
            ax2.plot(x, pm, 'k-o', ms=4, lw=1.2, label=f'{tag} group (mean)')
            ax2.plot(x, pw, 'k--^', ms=4, lw=1.2, mfc='white', label=f'{tag} worst member')
            ax2.set_ylim(0, 1.05)
            ax2.tick_params(labelsize=8)
            if (sc, tr, bg) == tuple(cells[-1]) or ax in axes[:, -1]:
                ax2.set_ylabel('PRR')
            prr_handles = ax2.get_legend_handles_labels()
        ax.set_title(f'{sc} / {tr} / bg {bg}', fontsize=9)
        ax.tick_params(axis='x', rotation=45, labelsize=8)
        ax.grid(axis='y', alpha=.3)
    for ax in axes[:, 0]:
        ax.set_ylabel(ylabel)
    h, l = axes.flat[0].get_legend_handles_labels()
    h, l = h + prr_handles[0], l + prr_handles[1]
    fig.legend(h, l, loc='upper center', ncol=min(len(h), 9), fontsize=8, bbox_to_anchor=(0.5, 1.02))
    fig.suptitle(title, y=1.06); fig.tight_layout(); plt.show()
'''


LATENCY = r'''
# Latency and PIR per member, from _pkt.csv (one row per packet x member, KPI window).
#  latency = first reception at the member - application send time (includes the wait for the
#            reservation, every failed attempt, HARQ / helper / takeover round trips).
#  attempt = n_att_fail + 1 of a delivered packet: the transmission that delivered it, counting the
#            leader's initial, its retransmissions, helper copies and takeovers (>= 4 possible with a helper).
#  PIR     = gap between consecutive receptions at a member, receptions sorted by time; a lost packet
#            widens the gap, a retransmission-delivered packet arrives later (and may overtake none:
#            receptions are sorted, so out-of-order delivery is handled).
PDB_MS = {'periodic1tb': 100.0, 'aperiodic1tb': 50.0}
lat_rows, att_rows = [], []
for armdir in sorted(glob.glob(os.path.join(OUT, '*', ''))):
    arm = os.path.basename(armdir.rstrip('/'))
    for p in sorted(glob.glob(armdir + '*_pkt.csv*')):
        tag = os.path.basename(p).split('_pkt.csv')[0]
        cc = cell_of(tag)
        d = rd(os.path.join(armdir, tag) + '_pkt.csv')
        rx = d[d.rx == 1].copy()
        rx['t_rx'] = rx.t_tx + rx.delay_ms / 1e3
        rx['attempt'] = (rx.n_att_fail + 1).clip(upper=4)
        per = []
        for m, g in rx.groupby('member'):
            g = g.sort_values('t_rx')
            pir = np.diff(g.t_rx.values) * 1e3
            per.append(dict(member=m, lat_mean=g.delay_ms.mean(), lat_p95=g.delay_ms.quantile(.95),
                            pir_mean=pir.mean() if len(pir) else np.nan,
                            pir_p95=np.quantile(pir, .95) if len(pir) else np.nan,
                            pir_max=pir.max() if len(pir) else np.nan))
        per = pd.DataFrame(per)
        lat_rows.append(dict(arm=arm, **cc,
            lat_group=rx.delay_ms.mean(), lat_p95_group=rx.delay_ms.quantile(.95),
            lat_worst=per.lat_mean.max(), lat_p95_worst=per.lat_p95.max(),
            late_share=(rx.delay_ms > PDB_MS.get(cc['traffic'], 1e9)).mean(),
            pir_group=per.pir_mean.mean(), pir_worst=per.pir_mean.max(),
            pir_p95_worst=per.pir_p95.max(), pir_max=per.pir_max.max()))
        for k, n in rx.attempt.value_counts().items():
            att_rows.append(dict(arm=arm, **cc, attempt=int(k), share=n / len(rx)))
LAT = pd.DataFrame(lat_rows).groupby(CELLS + ['arm']).mean(numeric_only=True).drop(columns='seed').reset_index()
ATT = pd.DataFrame(att_rows).groupby(CELLS + ['arm', 'attempt']).share.mean().unstack(fill_value=0).reset_index()

def cell_grid(fn, title):
    cells = LAT[CELLS].drop_duplicates().sort_values(CELLS).values.tolist()
    fig, axes = plt.subplots(2, 4, figsize=(17, 8))
    for ax, (sc, tr, bg) in zip(axes.flat, cells):
        fn(ax, sc, tr, bg)
        ax.set_title(f'{sc} / {tr} / bg {bg}', fontsize=9)
        ax.tick_params(axis='x', rotation=45, labelsize=8); ax.grid(axis='y', alpha=.3)
    h, l = axes.flat[0].get_legend_handles_labels()
    eh, el = getattr(axes.flat[0], '_extra', ([], []))
    fig.legend(h + eh, l + el, loc='upper center', ncol=len(h) + len(eh), fontsize=8, bbox_to_anchor=(0.5, 1.02))
    fig.suptitle(title, y=1.06); fig.tight_layout(); plt.show()

def pick(df, sc, tr, bg):
    return df[(df.scenario == sc) & (df.traffic == tr) & (df.bg == bg)].set_index('arm').reindex(
        [a for a in ORDER if a in set(df.arm)])

def lat_panel(ax, sc, tr, bg):
    d = pick(LAT, sc, tr, bg); x = np.arange(len(d)); w = .38
    ax.bar(x - w / 2, d.lat_group, w, color='#4c72b0', label='latency, group mean')
    ax.bar(x + w / 2, d.lat_worst, w, color='#c44e52', label='latency, worst receiver (mean)')
    ax.plot(x + w / 2, d.lat_p95_worst, 'k_', ms=12, mew=2, label='worst receiver p95')
    ax.set_xticks(x); ax.set_xticklabels(d.index); ax.set_ylabel('ms')
    ax2 = ax.twinx(); ax2.plot(x, d.pir_group, 'g-o', ms=4, label='PIR, group mean')
    ax2.plot(x, d.pir_worst, 'g--^', ms=4, mfc='white', label='PIR, worst receiver')
    ax2.set_ylabel('PIR (ms)', color='g')
    ax._extra = ax2.get_legend_handles_labels()

def att_panel(ax, sc, tr, bg):
    d = pick(ATT, sc, tr, bg); bottom = np.zeros(len(d))
    for k, col in zip([1, 2, 3, 4], ['#4c72b0', '#55a868', '#dd8452', '#c44e52']):
        if k in d:
            ax.bar(d.index, d[k], bottom=bottom, color=col, label=f'delivered at attempt {k}' + ('+' if k == 4 else ''))
            bottom += d[k].values
    ax.set_ylim(0, 1); ax.set_ylabel('share of delivered packets')
'''

AOI = r"""
# Latency CDF with the PRR as ceiling, and age of information (AoI), per member, KPI window.
#  CDF: F(t) = P(a sent (packet, member) is received within t ms) over ALL sent pairs: lost packets never
#       arrive, so the curve saturates at the PRR; F(PDB) is the PRR within the PDB.
#  AoI: at time tau a member's age = tau - (send time of the freshest packet it has received); an older
#       packet arriving late does not lower it, a loss lets it grow until the next reception.
#       Time-average over [first reception, end of the KPI window]; peak AoI = age just before each update.
TGRID = np.linspace(0, 100, 401)
cdf_rows, aoi_rows = [], []
for armdir in sorted(glob.glob(os.path.join(OUT, '*', ''))):
    arm = os.path.basename(armdir.rstrip('/'))
    if arm not in ORDER:
        continue
    for p in sorted(glob.glob(armdir + '*_pkt.csv*')):
        tag = os.path.basename(p).split('_pkt.csv')[0]
        cc = cell_of(tag)
        pre = os.path.join(armdir, tag)
        k1 = rd(pre + '_run.csv').iloc[0].kpi_end
        d = rd(pre + '_pkt.csv')
        dl = np.where(d.rx == 1, d.delay_ms, np.inf)
        cdf_all = (dl[:, None] <= TGRID[None, :]).mean(axis=0)
        worst_m = d.groupby('member').rx.mean().idxmin()
        w = d.member == worst_m
        cdf_w = (dl[w.values][:, None] <= TGRID[None, :]).mean(axis=0)
        cdf_rows.append(dict(arm=arm, **cc, cdf=cdf_all, cdf_worst=cdf_w))
        per = []
        for m, g in d[d.rx == 1].groupby('member'):
            t_rx = (g.t_tx + g.delay_ms / 1e3).values
            o = np.argsort(t_rx); t_rx, t_gen = t_rx[o], g.t_tx.values[o]
            fresh = np.maximum.accumulate(t_gen)
            ts = np.append(t_rx, k1)
            area = peak = 0.0; peaks = []
            for i in range(len(t_rx)):
                a0, a1 = t_rx[i] - fresh[i], ts[i + 1] - fresh[i]
                area += (a0 + a1) / 2 * (ts[i + 1] - t_rx[i])
                peaks.append(a1)
            span = k1 - t_rx[0]
            per.append(dict(member=m, aoi=1e3 * area / span if span > 0 else np.nan, peak=1e3 * np.mean(peaks)))
        per = pd.DataFrame(per)
        aoi_rows.append(dict(arm=arm, **cc, aoi_group=per.aoi.mean(), aoi_worst=per.aoi.max(),
                             paoi_group=per.peak.mean(), paoi_worst=per.peak.max()))
CDF = pd.DataFrame(cdf_rows); AOIT = pd.DataFrame(aoi_rows).groupby(CELLS + ['arm']).mean(numeric_only=True).drop(columns='seed').reset_index()

def cdf_panel(which):
    def f(ax, sc, tr, bg):
        d = CDF[(CDF.scenario == sc) & (CDF.traffic == tr) & (CDF.bg == bg)]
        for arm in ORDER:
            g = d[d.arm == arm]
            if len(g):
                col, ls = STYLE[arm]
                ax.plot(TGRID, np.mean(np.stack(g[which].values), axis=0), ls, color=col, label=LABEL[arm])
        ax.axvline(PDB_MS[tr], color='k', lw=.8, ls=':')
        ax.set_xlabel('latency t (ms)'); ax.set_ylabel('P(received within t)'); ax.set_ylim(0, 1.01)
    return f

def aoi_panel(ax, sc, tr, bg):
    d = pick(AOIT, sc, tr, bg); x = np.arange(len(d)); w = .38
    ax.bar(x - w / 2, d.aoi_group, w, color='#4c72b0', label='AoI, group mean')
    ax.bar(x + w / 2, d.aoi_worst, w, color='#c44e52', label='AoI, worst receiver')
    ax.plot(x + w / 2, d.paoi_worst, 'k_', ms=12, mew=2, label='peak AoI, worst receiver')
    ax.set_xticks(x); ax.set_xticklabels(d.index); ax.set_ylabel('ms')
"""

MD = {
    'intro': """# HARQ x Kuo helper study — results

Arms (all 3 transmissions per TB; helper arms use Kuo's selection, S1 = helper relays in slots the leader reserved):

| family | arms |
|---|---|
| leader only | **R1** blind (copies decoded independently), **R2** HARQ Option 1, **R3** HARQ Option 2 |
| Kuo helper, no takeover | **R4** blind (Kuo's scheme), **R4.1** HARQ Option 1, **R4.2** HARQ Option 2 |
| Kuo helper + takeover | **R5** Option 1, **R6** Option 2 — helper NACK lets the leader retake the next handed slot (Option 1 via a dedicated helper NACK resource; Option 2 helper silent on success), plus the **leader fallback**: if the leader decodes no SCI of the helper in a handed slot where a relay was expected, it retransmits in the next handed slot |
| HARQ-IR ablation | **R2/R3/R4.1/R4.2 _noIR**: same, receivers do not soft-combine |

Cells: highway / roundabout (IVD 30 m) x periodic1tb / aperiodic1tb x background none / moderate (60 cars, 1.67 flows each).
Metrics: PRR = delivered / sent per member (mean over members; *worst* = worst member); tx/pkt and reserved/pkt in subchannel-slots per packet
(transmitted PSSCH of the platoon, and leader SCI-1A reservations); platoon CBR = platoon subchannel-slots / available; PSFCH/TB = feedback received by the leader per TB.""",
    'dist': "## PRR versus distance from the leader\nOne curve per arm, one point per member (x = its mean distance). Dotted: leader only; dashed: Kuo helper without takeover; solid: Kuo helper with takeover.",
    'summary': "## Summary per cell and arm",
    'cbr': "## Spectrum and CBR savings\nSavings against the blind counterpart of the same family (leader arms vs R1, helper arms vs R4): transmitted subchannel-slots (interference / energy), platoon CBR, and reserved (announced) subchannel-slots (capacity for sensing UEs).",
    'spectrum': """## Spectrum use by component
Each bar is the total spectrum one retransmission method uses per generated packet in one scenario, stacked by what occupies it.
Unit: PRB x symbols (PSSCH+PSCCH of a transmission = its PRBs x 9 symbols; one PSFCH resource = 1 PRB x 2 symbols).
Feedback: Option 1 counts one shared NACK resource per transmission with >= 1 member NACK (the R5 helper NACKs on a dedicated
resource); Option 2 counts one resource per ACK / NACK (re-ACKs included; Option-2 ACKs are whole-run counts scaled to the KPI window).
In R4.1 / R4.2 the helper is a plain member, so its feedback is in *members feedback*.""",
    'lat': """## Latency, PIR and how packets are delivered
* **Latency**: first reception at a member minus the application send time. It includes the wait for the reserved resource,
  every failed attempt, and the HARQ / helper / takeover round trips. *Group*: mean over all delivered (packet, member);
  *worst receiver*: the member with the highest mean latency (its p95 also shown).
* **PIR** (packet inter-reception): gap between consecutive receptions at a member, receptions sorted by time (a packet
  delivered by a retransmission can arrive after a later packet's first copy; sorting handles that). A lost packet widens
  the gap. *Group*: mean over members of each member's mean PIR; *worst*: largest member mean PIR (`pir_max`: longest gap).
* **Delivered at attempt k**: share of delivered packets decoded at the k-th transmission they received (leader initial,
  leader retransmissions, helper copies, takeovers; 4+ is possible with a helper).
* **Gains** below are against the blind counterpart (leader arms vs R1, helper arms vs R4): negative latency change = faster.""",
    'aoi': """## Latency CDF with the PRR as ceiling, and age of information
* **Latency CDF**: P(a sent packet reaches a member within t ms), over **all** sent (packet, member) pairs, so dropped packets
  count (they never arrive): the curve saturates at the PRR, and its value at the PDB (dotted line) is the PRR within the PDB.
  Second figure: the worst receiver only (lowest PRR member).
* **Age of information (AoI)**: at each instant, the age of the freshest packet a member holds (now minus its send time).
  It grows linearly between receptions and drops at each fresher reception; a loss lets it keep growing, a late
  retransmission adds its latency. Time-averaged per member; *group* = mean over members, *worst* = largest; peak AoI =
  mean of the age just before each update. Lower is better; it combines latency and loss in one number.""",
    'ir': "## HARQ-IR (soft-combining) gain\nArm with soft combining minus the same arm with every copy decoded independently, on the arms without helper takeover.",
    'take': """## Helper takeover (R5 / R6): helper NACKs, leader fallback, PRR gain
* `helper_nack`: NACKs of the named helper received by the leader (each lets it retake a handed slot; `helper_reclaim`).
* `fallback_check`: handed slots in which a relay was expected; `fallback_heard`: the leader decoded the helper's SCI there;
  `fallback_tx`: nothing heard, the leader retransmitted in the next handed slot; `fallback_no_slot`: nothing heard, no slot left.
* `recovered_*`: member-packets first delivered by those leader retransmissions (PRR points attributable to each mechanism).
* `d_prr_vs_no_takeover`: R5 - R4.1 and R6 - R4.2 (whole takeover effect, including retransmission timing).""",
}


def build(out, path, profile='local1'):
    nb = nbf.v4.new_notebook()
    c = nb.cells
    c.append(nbf.v4.new_markdown_cell(MD['intro']))
    c.append(nbf.v4.new_code_cell(LOAD % {'out': out}))
    c.append(nbf.v4.new_markdown_cell(MD['dist']))
    c.append(nbf.v4.new_code_cell(DIST))
    c.append(nbf.v4.new_code_cell("dist_plot(ORDER, 'PRR vs distance: leader only, Kuo helper, Kuo helper + takeover')"))
    c.append(nbf.v4.new_code_cell("dist_plot(['R2', 'R2_noIR', 'R3', 'R3_noIR', 'R4.1', 'R4.1_noIR', 'R4.2', 'R4.2_noIR'], "
                                  "'PRR vs distance: HARQ-IR on (solid / dotted / dashed) vs off (dash-dot)')"))
    c.append(nbf.v4.new_code_cell("dist_plot(ORDER, 'PRR within the PDB vs distance', metric='prr_pdb')"))
    c.append(nbf.v4.new_markdown_cell(MD['summary']))
    c.append(nbf.v4.new_code_cell(SUMMARY))
    c.append(nbf.v4.new_markdown_cell(MD['cbr']))
    c.append(nbf.v4.new_code_cell(CBR))
    c.append(nbf.v4.new_code_cell(
        "p = CB.pivot_table(index=['scenario','traffic','bg'], columns='arm', values='cbr_saving_pct')[[a for a in ORDER if a not in ('R1','R4')]]\n"
        "p.plot.bar(figsize=(11,4), title='Platoon CBR saving vs blind counterpart (%)'); plt.grid(alpha=.3); plt.show()"))
    c.append(nbf.v4.new_markdown_cell(MD['spectrum']))
    c.append(nbf.v4.new_code_cell(SPECTRUM))
    c.append(nbf.v4.new_code_cell("spectrum_plot(COMP, 'Spectrum use per packet by component (all)', 'PRB x symbols per packet')"))
    c.append(nbf.v4.new_code_cell("spectrum_plot(['helper feedback (NACK)', 'members feedback'], "
                                  "'Feedback (PSFCH) use per packet: zoom', 'PRB x symbols per packet')"))
    c.append(nbf.v4.new_code_cell("spectrum_plot(COMP, 'Spectrum use per packet by component, in subchannel-slots "
                                  "(1 = 10 PRB x 9 symbols; one PSFCH = 2/90)', 'subchannel-slots per packet', scale=SUBCH_SLOT)"))
    c.append(nbf.v4.new_code_cell("spectrum_plot(['helper feedback (NACK)', 'members feedback'], "
                                  "'Feedback (PSFCH) use per packet, in subchannel-slots: zoom', 'subchannel-slots per packet', scale=SUBCH_SLOT)"))
    c.append(nbf.v4.new_markdown_cell("### Spectrum and PRR together (script `plot_spectrum_prr.py`)\n"
        "Rendered by the standalone script, which also saves `figures/spectrum_prr_{prb,subch}_<profile>.png`."))
    c.append(nbf.v4.new_code_cell(f"%run plot_spectrum_prr.py --profile {profile} --out {{OUT}} --show"))
    c.append(nbf.v4.new_code_cell("spectrum_plot(COMP, 'Spectrum use per packet (bars, left) and PRR (lines, right)', "
                                  "'PRB x symbols per packet', prr=True)"))
    c.append(nbf.v4.new_code_cell("spectrum_plot(COMP, 'Spectrum use per packet in subchannel-slots (bars, left) and PRR (lines, right)', "
                                  "'subchannel-slots per packet', scale=SUBCH_SLOT, prr=True)"))
    c.append(nbf.v4.new_markdown_cell("Same two figures with **PRR within the PDB** (packets delivered after the PDB count as lost)."))
    c.append(nbf.v4.new_code_cell("spectrum_plot(COMP, 'Spectrum use per packet (bars, left) and PRR within PDB (lines, right)', "
                                  "'PRB x symbols per packet', prr='pdb')"))
    c.append(nbf.v4.new_code_cell("spectrum_plot(COMP, 'Spectrum use per packet in subchannel-slots (bars, left) and PRR within PDB (lines, right)', "
                                  "'subchannel-slots per packet', scale=SUBCH_SLOT, prr='pdb')"))
    c.append(nbf.v4.new_code_cell("SP.assign(total=SP[COMP].sum(axis=1)).round(1)"))
    c.append(nbf.v4.new_code_cell("(SP.set_index(CELLS + ['arm'])[COMP] / SUBCH_SLOT).assign(total=lambda x: x.sum(axis=1)).round(3)"))
    c.append(nbf.v4.new_markdown_cell(MD['lat']))
    c.append(nbf.v4.new_code_cell(LATENCY))
    c.append(nbf.v4.new_code_cell("cell_grid(lat_panel, 'Latency (bars, left) and PIR (lines, right) per method')"))
    c.append(nbf.v4.new_code_cell("cell_grid(att_panel, 'Which transmission delivered the packet')"))
    c.append(nbf.v4.new_code_cell(
        "ref = {'R1': 'R1', 'R2': 'R1', 'R3': 'R1', 'R4': 'R4', 'R4.1': 'R4', 'R4.2': 'R4', 'R5': 'R4', 'R6': 'R4'}\n"
        "L = LAT[LAT.arm.isin(ORDER)].set_index(CELLS + ['arm'])\n"
        "G = []\n"
        "for (sc, tr, bg, arm), r in L.iterrows():\n"
        "    b = L.loc[(sc, tr, bg, ref[arm])]\n"
        "    G.append(dict(scenario=sc, traffic=tr, bg=bg, arm=arm, vs=ref[arm],\n"
        "        d_lat_group_ms=r.lat_group - b.lat_group, d_lat_worst_ms=r.lat_worst - b.lat_worst,\n"
        "        d_lat_p95_worst_ms=r.lat_p95_worst - b.lat_p95_worst,\n"
        "        d_pir_group_ms=r.pir_group - b.pir_group, d_pir_worst_ms=r.pir_worst - b.pir_worst))\n"
        "LG = pd.DataFrame(G)\n"
        "display(LAT[LAT.arm.isin(ORDER)].round(2))\n"
        "LG.round(2)"))
    c.append(nbf.v4.new_markdown_cell(MD['aoi']))
    c.append(nbf.v4.new_code_cell(AOI))
    c.append(nbf.v4.new_code_cell("cell_grid(cdf_panel('cdf'), 'Latency CDF over all sent (packet, member): saturates at the group PRR (dotted: PDB)')"))
    c.append(nbf.v4.new_code_cell("cell_grid(cdf_panel('cdf_worst'), 'Latency CDF of the worst receiver: saturates at its PRR (dotted: PDB)')"))
    c.append(nbf.v4.new_code_cell("cell_grid(aoi_panel, 'Age of information per method')"))
    c.append(nbf.v4.new_code_cell("AOIT[AOIT.arm.isin(ORDER)].round(1)"))
    c.append(nbf.v4.new_markdown_cell(MD['ir']))
    c.append(nbf.v4.new_code_cell(IR))
    c.append(nbf.v4.new_markdown_cell(MD['take']))
    c.append(nbf.v4.new_code_cell(TAKE))
    nbf.write(nb, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', default='local1')
    ap.add_argument('--out', default=None)
    ap.add_argument('--no-exec', action='store_true')
    a = ap.parse_args()
    out = a.out or os.path.join(HERE, 'out', a.profile)
    path = os.path.join(HERE, f'STUDY_RESULTS_{a.profile}.ipynb')
    build(out, path, a.profile)
    if not a.no_exec:
        subprocess.check_call([sys.executable, '-m', 'jupyter', 'nbconvert', '--to', 'notebook', '--execute',
                               '--inplace', '--ExecutePreprocessor.timeout=1800', path])
    print(path)


if __name__ == '__main__':
    main()
