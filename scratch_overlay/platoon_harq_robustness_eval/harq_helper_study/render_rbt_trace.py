"""Render a roundabout mobility trace over the SUMO net + buildings to validate the curve KPI window.

usage: render_rbt_trace.py [trace_name ...]   (default rbt_ivd30_n60_s1)
writes figures/trace_<name>_snapshots.png and figures/trace_<name>.gif
"""
import os, sys, xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.patches import Polygon

import study

HERE = os.path.dirname(os.path.abspath(__file__))
NET = f'{study.EVAL}/sumo/roundabout/roundabout.net.xml'
BLD = f'{study.EVAL}/sumo/roundabout/buildings.csv'
P = 9  # platoon nodes 0..8
VIEW = 330


def background(ax):
    for ln in ET.parse(NET).getroot().iter('lane'):
        if ln.get('id', '').startswith(':'):
            continue
        xy = np.array([list(map(float, p.split(','))) for p in ln.get('shape').split()])
        ax.plot(xy[:, 0], xy[:, 1], color='0.8', lw=2.5, zorder=0, solid_capstyle='round')
    for _, b in pd.read_csv(BLD).iterrows():
        ax.add_patch(Polygon([(b.x1, b.y1), (b.x2, b.y2), (b.x3, b.y3), (b.x4, b.y4)],
                             fc='0.55', ec='none', zorder=1))
    ax.set_aspect('equal')
    ax.set_xlim(-VIEW, VIEW)
    ax.set_ylim(-VIEW, VIEW)
    ax.tick_params(labelsize=7)


def draw(ax, d, t, k0, k1):
    s = d[d.t == t]
    bg, pl = s[s.node >= P], s[s.node < P].sort_values('node')
    inwin = k0 <= t < k1
    a = [ax.scatter(bg.x, bg.y, s=6, c='C7', zorder=2),
         ax.plot(pl.x, pl.y, '-', color='C3' if inwin else 'C0', lw=1, zorder=3)[0],
         ax.scatter(pl.x[1:], pl.y[1:], s=14, c='C3' if inwin else 'C0', zorder=4),
         ax.scatter(pl.x[:1], pl.y[:1], s=40, marker='*', c='k', zorder=5)]
    ax.set_title(f't = {t:.1f} s  ' + ('IN KPI window' if inwin else 'outside window'),
                 fontsize=9, color='C3' if inwin else '0.3')
    return a


def main(name):
    tr = f'{study.EVAL}/traces/{name}.csv.gz'
    d = pd.read_csv(tr, comment='#')
    d.columns = ['t', 'node', 'x', 'y', 'v'][:len(d.columns)]
    d['t'] = d.t.round(1)
    d = d[d.y > -1e5]  # parked seats
    k0, tmax = study.curve_window(tr)
    k1 = tmax - study.TAIL_GUARD
    ts = np.sort(d.t.unique())

    # snapshots: before, entry, ring, exit, after + distance time series
    snap = [k0 - 6, k0, (k0 + k1) / 2 - 5, (k0 + k1) / 2 + 5, k1, k1 + 6]
    snap = [ts[np.argmin(abs(ts - s))] for s in snap]
    fig = plt.figure(figsize=(13, 10))
    for i, t in enumerate(snap):
        ax = fig.add_subplot(3, 3, i + 1)
        background(ax)
        draw(ax, d, t, k0, k1)
    ax = fig.add_subplot(3, 1, 3)
    p = d[d.node < P].pivot_table(index='t', columns='node', values=['x', 'y'])
    for m in range(1, P):
        e = np.hypot(p.x[m] - p.x[0], p.y[m] - p.y[0])
        ax.plot(p.index, e, lw=1, label=f'm{m}')
        ax.axhline(m * 35, color='0.85', lw=.5, zorder=0)
    ax.axvspan(k0, k1, color='C3', alpha=.12, label='KPI window')
    ax.set_xlabel('t (s)'); ax.set_ylabel('leader-member distance (m)')
    ax.legend(ncol=10, fontsize=7, loc='lower right')
    ax.set_title(f'{name}: KPI window {k0}-{k1:.1f} s (grey lines = straight-road gap m x 35 m)', fontsize=9)
    fig.tight_layout()
    out = f'{HERE}/figures/trace_{name}_snapshots.png'
    fig.savefig(out, dpi=110)
    print(out)

    # animation, 0.5 s steps from 8 s before to 8 s after the window
    frames = ts[(ts >= k0 - 8) & (ts <= k1 + 8)][::5]
    fig, ax = plt.subplots(figsize=(6, 6))
    def upd(t):
        ax.clear(); background(ax); draw(ax, d, t, k0, k1)
    anim = FuncAnimation(fig, upd, frames=frames)
    out = f'{HERE}/figures/trace_{name}.gif'
    anim.save(out, writer=PillowWriter(fps=8), dpi=80)
    print(out)


for n in sys.argv[1:] or ['rbt_ivd30_n60_s1']:
    main(n)
