"""The paper's scenario figure (make_figs.fig_scenario), one standalone figure per roundabout route.
Writes figures/scenario_rbtW.{pdf,png} and figures/scenario_rbtE.{pdf,png} (paper/figures)."""
import json
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
import make_figs as mf


def route(tr, title, out):
    g = json.load(open(f'{mf.DATA}/geo.json'))
    blds = [list(zip(b[0::2], b[1::2])) for b in g['blds']]
    fig, ax = plt.subplots(figsize=(2.3, 2.55))
    fig.subplots_adjust(bottom=.17)
    for ln in g['lanes']:
        xy = np.array(ln); ax.plot(xy[:, 0], xy[:, 1], color='0.82', lw=2.2, solid_capstyle='round', zorder=0)
    for b in blds:
        ax.add_patch(Polygon(b, fc='0.45', ec='none', zorder=1))
    snaps = g['snaps'][tr]; t = sorted(snaps, key=float)[1]
    pts = snaps[t]; L = pts[0]
    for m in range(1, 9):
        blk = any(mf.seg_hits(L, pts[m], b) for b in blds)
        ax.plot([L[0], pts[m][0]], [L[1], pts[m][1]], color='#d62728' if blk else '#2ca02c',
                lw=.7, ls='--' if blk else '-', zorder=2)
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    ax.scatter(xs[1:], ys[1:], s=9, c='#1f77b4', zorder=3, edgecolors='white', linewidths=.3)
    ax.scatter([xs[0]], [ys[0]], s=30, marker='*', c='k', zorder=4)
    ax.set_xlim(-160, 160); ax.set_ylim(-160, 160); ax.set_aspect('equal')
    ax.set_xticks([]); ax.set_yticks([]); ax.set_title(title, pad=2)
    for s in ax.spines.values(): s.set_linewidth(.4)
    h = [plt.Line2D([], [], color='#2ca02c', lw=.8), plt.Line2D([], [], color='#d62728', lw=.8, ls='--'),
         plt.Line2D([], [], marker='*', color='k', ls='', ms=6), plt.Line2D([], [], marker='o', color='#1f77b4', ls='', ms=3)]
    fig.legend(h, ['link, clear', 'link, through a building', 'leader', 'members'],
               loc='lower center', ncol=2, frameon=False, bbox_to_anchor=(.5, .0), handlelength=1.6, columnspacing=1)
    for ext in ('pdf', 'png'):
        fig.savefig(f'{mf.FIG}/{out}.{ext}', dpi=300)
    plt.close(fig)
    print(out, 't =', t)


if __name__ == '__main__':
    route('rbt', 'Roundabout W exit (1/4 ring)', 'scenario_rbtW')
    route('rbtE', 'Roundabout E exit (3/4 ring)', 'scenario_rbtE')
