"""Builds and runs collisions.ipynb (figures in figs/). Edit the cells in the
notebook itself afterwards, or edit this file and rebuild:
    python3 build_collisions_nb.py
"""
import nbformat as nbf
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', 'scripts'))
import nb_layout
from nbclient import NotebookClient

md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
C = []
C.append(md("""# Collisions: rates, effect on decoding, and the loss-cause split

Built by `build_collisions_nb.py`. Every figure is written to `figs/` as PNG and PDF.
Metric definitions are in the docstring of `collisions.py` (printed below).
Data: full campaign (0 and 60 background cars, all schemes, attempt logs) and the
density campaign (15, 30, 45 cars, six schemes, run-level counters only)."""))
C.append(code("""import os, numpy as np, pandas as pd, matplotlib.pyplot as plt
import collisions
print(collisions.__doc__)
OUT = os.environ.get('COLL_OUT', os.path.expanduser('~/mnt/harq_helper_study/out'))
runs, near = collisions.collect(OUT, 'coll')   # cached in coll_runs.csv / coll_near.csv
os.makedirs('figs', exist_ok=True)"""))
C.append(md("## Plot settings\nOne place for scheme names, colours, line styles and the panel layout."))
C.append(code("""LABEL = {'R1': 'Leader, blind', 'R1_bsc': 'Leader, blind + combining', 'R2': 'Leader, Option 1',
         'R3': 'Leader, Option 2', 'R4': 'Kuo helper, blind', 'R4_bsc': 'Kuo helper, blind + combining',
         'R4.1': 'Helper + Option 1, no takeover', 'R4.2': 'Helper + Option 2, no takeover',
         'R5': 'Cooperative, Option 1', 'R6': 'Cooperative, Option 2'}
COLOR = {'R1': '#9e9e9e', 'R1_bsc': '#616161', 'R2': '#90caf9', 'R3': '#1e88e5', 'R4': '#ffcc80',
         'R4_bsc': '#fb8c00', 'R4.1': '#ce93d8', 'R4.2': '#8e24aa', 'R5': '#a5d6a7', 'R6': '#2e7d32'}
ORDER = list(LABEL)
SC = {'hwy': 'Highway', 'rbt': 'Roundabout W', 'rbtE': 'Roundabout E'}
TR = {'periodic1tb': 'periodic', 'aperiodic1tb': 'aperiodic'}
plt.rcParams.update({'font.size': 8, 'axes.grid': True, 'grid.alpha': 0.3, 'figure.dpi': 130})

def panels(nrows=2, ncols=3, **kw):
    fig, ax = plt.subplots(nrows, ncols, figsize=(7.2, 2.2 * nrows), sharey=True, **kw)
    return fig, np.atleast_2d(ax)

def save(fig, name):
    fig.tight_layout()
    for ext in ('png', 'pdf'):
        fig.savefig(f'figs/{name}.{ext}', bbox_inches='tight')"""))
C.append(md("""## Fig. 1: how often a platoon transmission overlaps another vehicle's
Share of platoon transmissions (leader and helper copies) that share the slot and at least
one subchannel with any other PSSCH within 5 km. Mean over 10 seeds. Dotted black: ground-truth CBR
(de-duplicated occupancy) of the whole channel. Schemes without the density campaign
(Option 1 arms, Kuo + combining, helper + Option 1) appear as markers at 0 and 60 cars only."""))
C.append(code("""fig, ax = panels()
for i, tr in enumerate(TR):
    for j, sc in enumerate(SC):
        a = ax[i, j]
        d = runs[(runs.sc == sc) & (runs.tr == tr)]
        for arm in ORDER:
            g = d[d.arm == arm].groupby('nbg').overlap_rate.mean()
            # schemes run only at 0 and 60 cars: markers, no line (no data in between)
            ls = '-' if len(g) > 2 else 'none'
            if len(g):
                a.plot(g.index, g.values, marker='o', ms=3, ls=ls, color=COLOR[arm], label=LABEL[arm])
        c = d.groupby('nbg').cbr_occupied.mean()
        a.plot(c.index, c.values, 'k:', label='CBR (occupancy)')
        a.set_title(f'{SC[sc]}, {TR[tr]}')
        if i == 1: a.set_xlabel('background vehicles')
        if j == 0: a.set_ylabel('overlap rate')
ax[0, 0].legend(fontsize=6, ncol=1, loc='upper left')
save(fig, 'fig1_overlap_rate_vs_load')"""))
C.append(md("""## Fig. 2: does an overlap make a copy fail?
For every copy a member received before it held the TB: failure probability when the copy overlapped
(dark) and when it was clean (light). 60 background cars. The gap between the two bars is the part
of the failures that overlap adds; the light bar is what fails anyway (path loss, blockage, fading)."""))
C.append(code("""d60 = runs[(runs.nbg == 60) & (runs.camp == 'remote_full')]
fig, ax = panels()
x = np.arange(len(ORDER))
for i, tr in enumerate(TR):
    for j, sc in enumerate(SC):
        a = ax[i, j]
        g = d60[(d60.sc == sc) & (d60.tr == tr)].groupby('arm')[['p_fail_overlap', 'p_fail_clean']].mean().reindex(ORDER)
        a.bar(x - 0.2, g.p_fail_overlap, 0.4, color=[COLOR[k] for k in ORDER], edgecolor='k', lw=0.3)
        a.bar(x + 0.2, g.p_fail_clean, 0.4, color=[COLOR[k] for k in ORDER], alpha=0.35, edgecolor='k', lw=0.3)
        a.set_title(f'{SC[sc]}, {TR[tr]}'); a.set_ylim(0, 1)
        a.set_xticks(x); a.set_xticklabels([LABEL[k] for k in ORDER] if i == 1 else [], rotation=70, ha='right', fontsize=5.5)
        if j == 0: a.set_ylabel('P(copy fails)')
from matplotlib.patches import Patch
ax[0, 0].legend(handles=[Patch(color='grey', label='overlapped copy'), Patch(color='grey', alpha=0.35, label='clean copy')], fontsize=6)
save(fig, 'fig2_fail_given_overlap')
tab = d60.groupby(['sc', 'tr', 'arm'])[['att_overlap_share', 'p_fail_overlap', 'p_fail_clean']].mean()
tab['extra_fail_from_overlap'] = tab.att_overlap_share * (tab.p_fail_overlap - tab.p_fail_clean)
tab.round(3)"""))
C.append(md("""## Fig. 3: the simulator's loss-cause split
Lost (packet, member) pairs per packet and member, split as the simulator labels them: "collision" if any
failed copy of that packet overlapped, else "channel". Left of each pair: no background; right: 60 cars."""))
C.append(code("""fig, ax = panels()
for i, tr in enumerate(TR):
    for j, sc in enumerate(SC):
        a = ax[i, j]
        for k, nbg in enumerate((0, 60)):
            g = runs[(runs.sc == sc) & (runs.tr == tr) & (runs.nbg == nbg) & (runs.camp == 'remote_full')]
            g = g.groupby('arm')[['collision_loss', 'channel_loss']].mean().reindex(ORDER)
            xs = x * 2.4 + k
            a.bar(xs, g.channel_loss, 0.9, color=[COLOR[a_] for a_ in ORDER], edgecolor='k', lw=0.3, hatch='' if k else '')
            a.bar(xs, g.collision_loss, 0.9, bottom=g.channel_loss, color='white', edgecolor=[COLOR[a_] for a_ in ORDER], hatch='////', lw=0.6)
        a.set_title(f'{SC[sc]}, {TR[tr]}'); a.set_ylim(0, 0.7)
        a.set_xticks(x * 2.4 + 0.5); a.set_xticklabels([LABEL[k] for k in ORDER] if i == 1 else [], rotation=70, ha='right', fontsize=5.5)
        if j == 0: a.set_ylabel('loss rate')
from matplotlib.patches import Patch
ax[0, 0].legend(handles=[Patch(facecolor='grey', label='"channel" loss'),
                         Patch(facecolor='white', edgecolor='grey', hatch='////', label='"collision" loss')],
                title='per scheme: 0 cars | 60 cars', title_fontsize=6, fontsize=6)
save(fig, 'fig3_loss_split')"""))
C.append(md("""## Fig. 4: how far is the nearest overlapping transmitter?
Overlapped copies at 60 background cars, all schemes pooled: CDF of the distance from the transmitter to
the nearest vehicle sending on a common subchannel in the same slot, for copies that failed and copies
that were decoded anyway."""))
C.append(code("""fig, ax = panels(sharex=True)
n60 = near[near.nbg == 60]
for i, tr in enumerate(TR):
    for j, sc in enumerate(SC):
        a = ax[i, j]
        g = n60[(n60.sc == sc) & (n60.tr == tr)]
        for f, col, lab in ((1, '#c62828', 'copy failed'), (0, '#1565c0', 'copy decoded')):
            v = np.sort(g[g.fail == f].nearest_collider_m.values)
            a.plot(v, np.arange(1, len(v) + 1) / len(v), color=col, label=lab)
        a.set_xscale('log'); a.set_xlim(10, 5000); a.set_title(f'{SC[sc]}, {TR[tr]}')
        if i == 1: a.set_xlabel('nearest overlapping transmitter (m)')
        if j == 0: a.set_ylabel('CDF')
ax[0, 0].legend(fontsize=6)
save(fig, 'fig4_nearest_collider_cdf')"""))
C.append(md("""## Consistency checks
Result of `checks.py` over every run (counters of `_run.csv` against each other and against the
per-row logs). Explanations of the failing checks are in `CHECKS.md`."""))
C.append(code("""pd.read_csv('checks_summary.csv').query('failed > 0')"""))

nb = nbf.v4.new_notebook(cells=C)
NotebookClient(nb, timeout=1200, kernel_name='python3').execute()
nbf.write(nb_layout.add_preamble(nb, 'paper_notes/round3'), nb_layout.notebook_path('collisions.ipynb', 'paper_notes/round3'))
print('written collisions.ipynb')
