"""Paper figures (IEEE two-column) from the SCHEMES_COMPARISON data objects. Same scheme styles as hhs_plot.

  fig_load(S, 'prr_group' | 'prr_worst', ...)   3 scenarios (rows) x 2 traffic types (columns) vs density
  fig_fallback_gain(LOAD, ...)                   paired R4+C+fb - R4+C: group (solid) and worst member (dashed)
  fig_helper_miss(S_HM, R3ref, ...)              group PRR vs forced helper miss p; solid payload, dashed SCI
"""
import contextlib

import matplotlib.pyplot as plt
import numpy as np

import hhs_data as H
import hhs_plot as P

COL_W = 3.5          # IEEE column width (in)
TEXT_W = 7.16        # IEEE text width (in)
RC = {'font.family': 'serif', 'font.size': 7.5, 'axes.titlesize': 8, 'axes.labelsize': 7.5,
      'legend.fontsize': 6.5, 'xtick.labelsize': 7, 'ytick.labelsize': 7, 'lines.markersize': 3.5,
      'axes.grid': True, 'grid.alpha': 0.3, 'axes.spines.top': True, 'axes.spines.right': True,
      'legend.frameon': True, 'legend.framealpha': 0.9, 'savefig.bbox': 'tight', 'savefig.pad_inches': 0.02}
LABEL = {'R3': 'Source Opt. 2 HARQ', 'R4_bsc': 'Blind helper', 'R4_bsc_fbs': 'Blind helper + fallback',
         'R6': 'Cooperative HARQ'}


def _fig_legend(fig, ax, ncol, y=0.0):
    h, l = ax.flat[0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=ncol, bbox_to_anchor=(0.5, y), frameon=False)
DENS_UNIT = {'hwy': 'veh/km', 'rbt': 'veh/km$^2$', 'rbtE': 'veh/km$^2$'}


@contextlib.contextmanager
def paper_style():
    with plt.rc_context(RC):
        yield


def _style(arm):
    s = P.STYLE[arm]
    return dict(color=s['color'], marker=s['marker'], ls=s['ls'], lw=max(1.0, s['lw'] * 0.6), capsize=1.5,
                label=LABEL.get(arm, s['label']))


def fig_load(S, metric, arms, width=TEXT_W, height=5.2, ylabel=None):
    """S: hhs_data.summarize by ['arm', 'sc', 'tr', 'nbg'] with a 'dens' column."""
    with paper_style():
        fig, ax = plt.subplots(3, 2, figsize=(width, height), squeeze=False)
        for i, sc in enumerate(P.SC_ORDER):
            for j, tr in enumerate(P.TR_ORDER):
                a = ax[i, j]
                for arm in arms:
                    g = S[(S.arm == arm) & (S.sc == sc) & (S.tr == tr)].sort_values('dens')
                    a.errorbar(g.dens, 100 * g[metric], yerr=100 * g[metric + '_ci'], **_style(arm))
                a.set_title(f'{P.SC_LABEL[sc]}, {P.TR_LABEL[tr]}')
                a.set_xlabel(('background density' if width > 5 else 'density') + f' ({DENS_UNIT[sc]})')
                if j == 0:
                    a.set_ylabel(ylabel or P.METRIC_LABEL[metric] + ' (%)')
        fig.tight_layout(h_pad=0.6, w_pad=0.8)
        _fig_legend(fig, ax, ncol=4 if width > 5 else 2)
    return fig


def fig_fallback_gain(LOAD, width=COL_W, height=4.6, fb='R4_bsc_fbs', base='R4_bsc'):
    """Paired by seed: fb - base, group PRR (solid) and worst-member PRR (dashed), 95 % t-CI."""
    keys = ['sc', 'tr', 'nbg', 'seed']
    x = LOAD[LOAD.arm == fb].set_index(keys)
    y = LOAD[LOAD.arm == base].set_index(keys)
    dens = LOAD.drop_duplicates(['sc', 'nbg']).set_index(['sc', 'nbg']).dens
    with paper_style():
        fig, ax = plt.subplots(3, 2, figsize=(width, height), squeeze=False, sharex='row')
        for i, sc in enumerate(P.SC_ORDER):
            for j, tr in enumerate(P.TR_ORDER):
                a = ax[i, j]
                for m, ls, c, lab in (('prr_group', '-', '#ff7f00', '$\\Delta$ group PRR'),
                                      ('prr_worst', '--', '#7f3b08', '$\\Delta$ worst member')):
                    d = (x[m] - y[m]).dropna() * 100
                    d = d.xs((sc, tr), level=['sc', 'tr'])
                    g = d.groupby(level='nbg')
                    xs = [dens[(sc, n)] for n in g.mean().index]
                    a.errorbar(xs, g.mean().values, yerr=g.apply(H.ci95).values, color=c, ls=ls, marker='o',
                               lw=1.0, capsize=1.5, label=lab)
                a.axhline(0, color='k', lw=0.6)
                a.set_title(f'{P.SC_LABEL[sc]}, {P.TR_LABEL[tr]}')
                a.set_xlabel(f'density ({DENS_UNIT[sc]})')
                if j == 0:
                    a.set_ylabel('gain of fallback (pp)')
        fig.tight_layout(h_pad=0.4, w_pad=0.6)
        _fig_legend(fig, ax, ncol=2)
    return fig


def fig_helper_miss(S_HM, r3, arms=('R4_bsc', 'R4_bsc_fbs', 'R6'), width=TEXT_W, height=2.0):
    """S_HM: {'data': summary, 'sci': summary} by ['arm', 'sc', 'tr', 'hm_p']; r3: R3 group PRR per sc
    (no helper, flat reference)."""
    with paper_style():
        fig, ax = plt.subplots(1, 3, figsize=(width, height), squeeze=False)
        for j, sc in enumerate(P.SC_ORDER):
            a = ax[0, j]
            for arm in arms:
                st = _style(arm)
                for mode, ls, fill in (('data', '-', st['color']), ('sci', '--', 'white')):
                    g = S_HM[mode]
                    g = g[(g.arm == arm) & (g.sc == sc)].sort_values('hm_p')
                    a.errorbar(g.hm_p, 100 * g.prr_group, yerr=100 * g.prr_group_ci, color=st['color'],
                               marker=st['marker'], mfc=fill, ls=ls, lw=1.0, capsize=1.5,
                               label=f"{st['label']} ({'payload' if mode == 'data' else 'SCI'})")
            a.axhline(100 * r3[sc], color=P.STYLE['R3']['color'], ls=':', lw=1.0, label=LABEL['R3'] + ' (no helper)')
            a.set_title(P.SC_LABEL[sc])
            a.set_xlabel('forced helper miss probability $p$')
            if j == 0:
                a.set_ylabel('group PRR (%)')
        fig.tight_layout(w_pad=0.8)
        _fig_legend(fig, ax, ncol=4)
    return fig
