#!/usr/bin/env python3
"""Group and worst-member PRR against the pool CBR, per route and traffic type, for leader Option 2 (R3), the blind
helper with soft combining (R4_bsc, "R4+C") and the cooperative protocol (R6), plus "R4 + C + fallback" (R4_bsc_fb)
when its campaign has been copied to out/remote_v3_fb. Campaign v3 (out/remote_v3), loads 0 / 15 / 30 / 45 / 60
background vehicles, 10 seeds. Notebook: notebooks/PRR_VS_CBR.ipynb (scripts/build_cbr_notebook.py).

Pool CBR = `cbr_occupied` of the run: the share of (slot, subchannel) cells of the pool used by any vehicle in the
KPI window, each cell counted once (<= 1). The run's `cbr` column is the offered load (every transmitted
subchannel-slot, overlaps counted twice), which can exceed 1 and is not used here.

Figures (figures/prr_vs_cbr/):
  prr_vs_cbr.{pdf,png}        group PRR (mean member PRR) against pool CBR
  dprr_vs_cbr.{pdf,png}       change of group PRR against the scheme's own 0-vehicle value
  worst_vs_cbr.{pdf,png}      worst-member PRR against pool CBR
  dworst_vs_cbr.{pdf,png}     change of worst-member PRR against the scheme's own 0-vehicle value
  prr_vs_cbr.csv              the plotted means and 95 % t half-widths
Points: mean over seeds; bars: 95 % Student-t half-width on both axes.

  python3 scripts/plot_prr_vs_cbr.py
"""
import glob
import os

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # harq_helper_study/ (this file: scripts/)
DATA = os.path.join(HERE, 'out', 'remote_v3')
DATA_OF = {'R4_bsc_fb': os.path.join(HERE, 'out', 'remote_v3_fb')}   # arms of other campaigns (same commands + flag)
FIG = os.path.join(HERE, 'figures', 'prr_vs_cbr')
ARMS = {'R3': ('Leader Opt. 2 (R3)', '#1565c0', 's', '-'),
        'R4_bsc': ('Blind helper + C (R4+C)', '#e65100', '^', '--'),
        'R4_bsc_fb': ('R4 + C + fallback', '#8e24aa', 'v', '-.'),
        'R6': ('Cooperative (R6)', '#1b5e20', 'D', '-')}
SCN = {'hwy': 'Highway', 'rbt': 'Roundabout W', 'rbtE': 'Roundabout E'}
TRN = {'periodic1tb': 'periodic', 'aperiodic1tb': 'aperiodic'}
NBG = {'none': 0, 'n15': 15, 'n30': 30, 'n45': 45, 'moderate': 60}


def ci95(v):
    v = np.asarray(v, float)
    return stats.t.ppf(.975, len(v) - 1) * v.std(ddof=1) / np.sqrt(len(v)) if len(v) > 1 else np.nan


def load():
    rows = []
    for arm in ARMS:
        for f in glob.glob(os.path.join(DATA_OF.get(arm, DATA), arm, '*_run.csv*')):
            sc, _, tr, bg, s = os.path.basename(f).split('_run.csv')[0].split('_')
            r = pd.read_csv(f).iloc[0]
            rows.append(dict(arm=arm, sc=sc, tr=tr, nbg=NBG[bg], seed=int(s[1:]), cbr=r.cbr_occupied,
                             cbr_offered=r.cbr, prr=100 * r.prr_mean, worst=100 * r.prr_min))
    return pd.DataFrame(rows)


def means(D):
    G = D.groupby(['arm', 'sc', 'tr', 'nbg'])
    M = G[['cbr', 'cbr_offered', 'prr', 'worst']].mean().join(G[['cbr', 'prr', 'worst']].agg(ci95).add_suffix('_ci'))
    M = M.join(G.size().rename('runs')).reset_index()
    base = M[M.nbg == 0].set_index(['arm', 'sc', 'tr'])
    b = M.set_index(['arm', 'sc', 'tr']).index
    M['dprr'] = M.prr.values - base.loc[b, 'prr'].values
    # 0-vehicle and loaded runs use different traces: unpaired CI of the difference
    M['dprr_ci'] = np.where(M.nbg == 0, 0, np.hypot(M.prr_ci.values, base.loc[b, 'prr_ci'].values))
    M['dworst'] = M.worst.values - base.loc[b, 'worst'].values
    M['dworst_ci'] = np.where(M.nbg == 0, 0, np.hypot(M.worst_ci.values, base.loc[b, 'worst_ci'].values))
    return M


def grid(M, y, ylab, fname=None, hline=None):
    plt.rcParams.update({'font.size': 9, 'font.family': 'serif'})
    fig, axs = plt.subplots(3, 2, figsize=(8.2, 8.6), sharex=True)
    for i, sc in enumerate(SCN):
        for j, tr in enumerate(TRN):
            a = axs[i, j]
            for arm, (lab, col, mk, ls) in ARMS.items():
                q = M[(M.arm == arm) & (M.sc == sc) & (M.tr == tr)].sort_values('nbg')
                if q.empty:
                    continue
                a.errorbar(100 * q.cbr, q[y], xerr=100 * q.cbr_ci, yerr=q[y + '_ci'], color=col, marker=mk, ls=ls,
                           lw=1.2, ms=5, capsize=2, elinewidth=.8, label=lab,
                           mfc='white' if arm == 'R4_bsc' else col)
            if hline is not None:
                a.axhline(hline, color='k', lw=.6)
            a.grid(alpha=.3)
            a.set_title(f'{SCN[sc]}, {TRN[tr]}')
            if i == 2:
                a.set_xlabel('pool CBR (%)')
            if j == 0:
                a.set_ylabel(ylab)
    axs[0, 0].legend(fontsize=7, loc='lower left')
    fig.tight_layout()
    if fname:
        os.makedirs(FIG, exist_ok=True)
        for ext in ('pdf', 'png'):
            fig.savefig(os.path.join(FIG, f'{fname}.{ext}'), dpi=160)
    return fig


FIGS = [('prr', 'group PRR (%)', 'prr_vs_cbr', None),
        ('dprr', 'Δ group PRR vs 0 vehicles (pp)', 'dprr_vs_cbr', 0),
        ('worst', 'worst-member PRR (%)', 'worst_vs_cbr', None),
        ('dworst', 'Δ worst-member PRR vs 0 vehicles (pp)', 'dworst_vs_cbr', 0)]


def main():
    plt.switch_backend('Agg')
    os.makedirs(FIG, exist_ok=True)
    plt.rcParams.update({'font.size': 9, 'font.family': 'serif'})
    D = load()
    n = D.groupby(['arm', 'sc', 'tr', 'nbg']).size()
    assert n.eq(10).all(), f'expected 10 seeds per point:\n{n[n != 10]}'
    M = means(D)
    M.round(4).to_csv(os.path.join(FIG, 'prr_vs_cbr.csv'), index=False)
    for y, lab, fn, h in FIGS:
        plt.close(grid(M, y, lab, fn, hline=h))
    print(f'{len(D)} runs -> {FIG}')
    with pd.option_context('display.width', 200):
        print(M.pivot_table(index=['sc', 'tr', 'nbg'], columns='arm', values=['cbr', 'prr']).round(3))


if __name__ == '__main__':
    main()
