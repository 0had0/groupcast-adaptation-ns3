"""Publication figures from paper/data (deterministic; no numbers are typed by hand)."""
import json, os
import numpy as np, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker
from matplotlib.patches import Polygon

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data'); FIG = os.path.join(HERE, '..', 'figures')
os.makedirs(FIG, exist_ok=True)
plt.rcParams.update({'font.family': 'serif', 'font.serif': ['Times New Roman', 'Times', 'Nimbus Roman', 'DejaVu Serif'],
                     'mathtext.fontset': 'stix', 'font.size': 8, 'axes.labelsize': 8, 'axes.titlesize': 8,
                     'legend.fontsize': 7, 'xtick.labelsize': 7, 'ytick.labelsize': 7, 'axes.linewidth': .6,
                     'lines.linewidth': 1.2, 'pdf.fonttype': 42, 'savefig.bbox': 'tight', 'savefig.pad_inches': .02})
COL, WIDE = 3.45, 7.1
ARMS = ['R1', 'R3', 'R4', 'R4.2', 'R6']
NAME = {'R1': 'R1 leader blind', 'R3': 'R3 leader HARQ', 'R4': 'R4 helper blind',
        'R4.2': 'R4.2 helper HARQ', 'R6': 'R6 helper HARQ + takeover'}
STY = {'R1': dict(color='#7f7f7f', ls=':', marker='o'), 'R3': dict(color='#1f77b4', ls=':', marker='s'),
       'R4': dict(color='#d62728', ls='--', marker='v'), 'R4.2': dict(color='#ff7f0e', ls='--', marker='^'),
       'R6': dict(color='#2ca02c', ls='-', marker='D')}
SC = {'hwy': 'Highway', 'rbt': 'Roundabout W', 'rbtE': 'Roundabout E'}
TR = {'periodic1tb': 'periodic', 'aperiodic1tb': 'aperiodic'}
S = pd.read_csv(f'{DATA}/S_cells.csv'); Dd = pd.read_csv(f'{DATA}/S_dist.csv'); K = pd.read_csv(f'{DATA}/S_tko.csv')


def cell(arm, sc, tr, nbg, col):
    x = S[(S.arm == arm) & (S.sc == sc) & (S.tr == tr) & (S.nbg == nbg)]
    return float(x[col].iloc[0])


# ---------- Fig. 2: scenario ----------
def seg_hits(p, q, poly):
    def ccw(a, b, c): return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        if ccw(p, a, b) != ccw(q, a, b) and ccw(p, q, a) != ccw(p, q, b):
            return True
    return False


def fig_scenario():
    g = json.load(open(f'{DATA}/geo.json'))
    blds = [list(zip(b[0::2], b[1::2])) for b in g['blds']]
    fig, axes = plt.subplots(1, 2, figsize=(COL, 1.85))
    for ax, tr, title in zip(axes, ['rbt', 'rbtE'], ['(a) W exit (1/4 ring)', '(b) E exit (3/4 ring)']):
        for ln in g['lanes']:
            xy = np.array(ln); ax.plot(xy[:, 0], xy[:, 1], color='0.82', lw=2.2, solid_capstyle='round', zorder=0)
        for b in blds:
            ax.add_patch(Polygon(b, fc='0.45', ec='none', zorder=1))
        snaps = g['snaps'][tr]; t = sorted(snaps, key=float)[1]
        pts = snaps[t]; L = pts[0]
        nblk = 0
        for m in range(1, 9):
            blk = any(seg_hits(L, pts[m], b) for b in blds); nblk += blk
            ax.plot([L[0], pts[m][0]], [L[1], pts[m][1]], color='#d62728' if blk else '#2ca02c',
                    lw=.7, ls='--' if blk else '-', zorder=2)
        print(tr, 't', t, 'blocked links', nblk, 'leader', L)
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        ax.scatter(xs[1:], ys[1:], s=9, c='#1f77b4', zorder=3, edgecolors='white', linewidths=.3)
        ax.scatter([xs[0]], [ys[0]], s=30, marker='*', c='k', zorder=4)
        ax.set_xlim(-160, 160); ax.set_ylim(-160, 160); ax.set_aspect('equal')
        ax.set_xticks([]); ax.set_yticks([]); ax.set_title(title, pad=2)
        for s in ax.spines.values(): s.set_linewidth(.4)
    h = [plt.Line2D([], [], color='#2ca02c', lw=.8), plt.Line2D([], [], color='#d62728', lw=.8, ls='--'),
         plt.Line2D([], [], marker='*', color='k', ls='', ms=6), plt.Line2D([], [], marker='o', color='#1f77b4', ls='', ms=3)]
    fig.legend(h, ['leader link, clear', 'leader link, through a building', 'leader', 'members'],
               loc='lower center', ncol=2, frameon=False, bbox_to_anchor=(.5, -.13), handlelength=1.6, columnspacing=1)
    fig.subplots_adjust(wspace=.04)
    fig.savefig(f'{FIG}/fig_scenario.pdf')


# ---------- Fig. 3: PRR vs distance, three regimes ----------
def fig_dist():
    panels = [('hwy', 'aperiodic1tb', 60, '(a) interference only\nhighway, aperiodic, 60 cars'),
              ('rbt', 'periodic1tb', 0, '(b) blockage only\nroundabout W, periodic, 0 cars'),
              ('rbtE', 'aperiodic1tb', 60, '(c) both\nroundabout E, aperiodic, 60 cars')]
    fig, axes = plt.subplots(1, 3, figsize=(WIDE, 1.95), sharey=True)
    for ax, (sc, tr, nb, title) in zip(axes, panels):
        for a in ARMS:
            d = Dd[(Dd.arm == a) & (Dd.sc == sc) & (Dd.tr == tr) & (Dd.nbg == nb) & (Dd['size'] >= 5)].sort_values('d')
            st = STY[a]
            ax.plot(d.d, d['mean'], color=st['color'], ls=st['ls'], marker=st['marker'], ms=3.2, label=NAME[a],
                    mfc='white' if a in ('R4', 'R4.2') else st['color'])
            ax.fill_between(d.d, d['mean'] - d.ci, d['mean'] + d.ci, color=st['color'], alpha=.13, lw=0)
        ax.set_title(title, pad=3); ax.set_xlabel('leader to member distance (m)')
        ax.set_xlim(15, 295); ax.set_xticks([50, 100, 150, 200, 250]); ax.set_ylim(0, 1.02); ax.grid(alpha=.3, lw=.4)
    axes[0].set_ylabel('PRR')
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=5, frameon=False, bbox_to_anchor=(.5, 1.13), columnspacing=1.2)
    fig.subplots_adjust(wspace=.07)
    fig.savefig(f'{FIG}/fig_prr_dist.pdf')


# ---------- Fig. 4: group and worst-member PRR at 60 cars ----------
CELLS = [(s, t) for s in ['hwy', 'rbt', 'rbtE'] for t in ['periodic1tb', 'aperiodic1tb']]


def fig_bars():
    fig, axes = plt.subplots(2, 1, figsize=(WIDE, 2.9), sharex=True)
    w = .16; x = np.arange(len(CELLS))
    for ax, col, lab in zip(axes, ['prr', 'worst'], ['group PRR', 'worst-member PRR']):
        for i, a in enumerate(ARMS):
            v = [cell(a, s, t, 60, col) for s, t in CELLS]; e = [cell(a, s, t, 60, col + '_ci') for s, t in CELLS]
            v0 = [cell(a, s, t, 0, col) for s, t in CELLS]
            ax.bar(x + (i - 2) * w, v, w, color=STY[a]['color'], label=NAME[a], yerr=e,
                   error_kw=dict(lw=.6, capsize=1.2), zorder=2, alpha=.95 if a == 'R6' else .8,
                   hatch='//' if a in ('R4', 'R4.2') else None, edgecolor='white', linewidth=.3)
            ax.scatter(x + (i - 2) * w, v0, marker='_', color='k', s=18, lw=.9, zorder=3,
                       label='same arm, 0 cars' if i == 0 else None)
        ax.set_ylabel(lab); ax.set_ylim(0, 1.0); ax.set_yticks([0, .2, .4, .6, .8] if col == 'worst' else [0, .2, .4, .6, .8, 1]); ax.grid(axis='y', alpha=.3, lw=.4, zorder=0)
    axes[1].set_xticks(x); axes[1].set_xticklabels([f'{SC[s]}\n{TR[t]}' for s, t in CELLS])
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=6, frameon=False, bbox_to_anchor=(.5, 1.05), columnspacing=1)
    fig.subplots_adjust(hspace=.08)
    fig.savefig(f'{FIG}/fig_bars.pdf')


# ---------- Fig. 5: PRR vs cost ----------
def fig_cost():
    fig, ax = plt.subplots(figsize=(COL, 1.9))
    mk = {'hwy': 'o', 'rbt': 's', 'rbtE': '^'}
    for tr in ['periodic1tb', 'aperiodic1tb']:
        for a in ARMS:
            for sc in ['hwy', 'rbt', 'rbtE']:
                ax.scatter(cell(a, sc, tr, 60, 'res'), cell(a, sc, tr, 60, 'prr'), marker=mk[sc], s=18,
                           color=STY[a]['color'], edgecolors='k' if a == 'R6' else 'none', linewidths=.5, zorder=3)
    ax.text(4.9, 1.0, 'periodic', ha='center', va='bottom', fontsize=7, style='italic')
    ax.text(11.2, 1.0, 'aperiodic', ha='center', va='bottom', fontsize=7, style='italic')
    ax.axvline(6.7, color='0.6', lw=.5, ls=':')
    ax.set_xlabel('subchannel-slots transmitted per packet (60 cars)'); ax.set_ylabel('group PRR')
    ax.set_ylim(0.35, 1.08); ax.set_xlim(3, 15.5); ax.grid(alpha=.3, lw=.4)
    h = [plt.Line2D([], [], marker='o', ls='', color=STY[a]['color'], ms=4) for a in ARMS] + \
        [plt.Line2D([], [], marker=mk[s], ls='', color='k', mfc='none', ms=4) for s in mk]
    ax.legend(h, ARMS + [SC[s] for s in mk], loc='upper center', ncol=4, frameon=False,
              bbox_to_anchor=(.5, 1.33), columnspacing=.8, handletextpad=.2)
    fig.savefig(f'{FIG}/fig_cost.pdf')


# ---------- Fig. 6: how R6 delivers ----------
def fig_takeover():
    parts = [('leader_regular', 'leader, own slot', '#9ecae1'), ('helper', 'helper copy', '#fdae6b'),
             ('leader_nack_takeover', 'leader, slot taken back on helper NACK', '#31a354'),
             ('leader_fallback', 'leader, slot taken back by fallback', '#006d2c')]
    k = K[K.arm == 'R6']
    labels, rows = [], []
    for s, t in CELLS:
        for nb in (0, 60):
            r = k[(k.sc == s) & (k.tr == t) & (k.nbg == nb)].iloc[0]
            rows.append([r[p[0]] for p in parts]); labels.append(f'{nb}')
    rows = np.array(rows); x = np.arange(len(rows)) + np.repeat(np.arange(len(CELLS)) * .6, 2)
    fig, ax = plt.subplots(figsize=(COL, 1.9))
    bottom = np.zeros(len(rows))
    for j, (_, lab, c) in enumerate(parts):
        ax.bar(x, rows[:, j], .8, bottom=bottom, color=c, label=lab, edgecolor='white', linewidth=.3)
        bottom += rows[:, j]
    for i, (s, t) in enumerate(CELLS):
        ax.text(x[2 * i: 2 * i + 2].mean(), -0.2, f'{SC[s].replace("Roundabout", "Rbt.")}\n{TR[t][:4]}.', ha='center', va='top', fontsize=6.5)
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=6.5)
    ax.set_ylim(0, 1.0); ax.set_ylabel('share of member-packets\ndelivered (= PRR)')
    ax.text(x[0] - .9, -0.075, 'cars:', ha='right', va='top', fontsize=6.5)
    ax.grid(axis='y', alpha=.3, lw=.4); ax.set_axisbelow(True)
    ax.legend(loc='upper center', bbox_to_anchor=(.5, 1.3), ncol=2, frameon=False, columnspacing=.8, handlelength=1.2)
    fig.savefig(f'{FIG}/fig_takeover.pdf')


if __name__ == '__main__':
    fig_scenario(); fig_dist(); fig_bars(); fig_cost(); fig_takeover()
    print('figures written')
