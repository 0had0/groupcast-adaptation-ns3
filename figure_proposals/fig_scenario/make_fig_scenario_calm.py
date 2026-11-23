#!/usr/bin/env python3
"""Scenario figure, SUMO style, in the paper's calm palette (the draw.io protocol figure / eval_figures PAL).

  python3 make_fig_scenario_calm.py  ->  fig_scenario_trim.{pdf,png}  (transparent background, trimmed view, panel labels under the images;
  fig_scenario_calm.{pdf,png} = the earlier grass-coloured, +-160 m square version)

Geometry and snapshot exactly as notebooks/paper_v2/scenario_sumo/scenario_sumo.ipynb: lanes and junctions from
sumo/roundabout/roundabout.net.xml, 98 buildings from buildings.csv, the v3 60-background-vehicle trace of seed 1 (reproducibility_bundle/data/mobility/traces_n60_v3_server) at the
instant the leader is closest to its position in the original figure (Roundabout W t0 = 34.3 s, Roundabout E t0 = 39.6 s
of the no-background trace). Leader links recomputed from the building footprints. Car headings follow the lane each car is on; background
cars off a lane centre or overlapping another car are not drawn (the platoon is always drawn). Vehicles drawn 2x longer and 1.5x
wider than 5 m x 1.8 m so they stay visible; a scale bar gives 50 m.
"""
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon

HERE = Path(__file__).resolve().parent
EV = HERE.parents[1] / "scratch_overlay/platoon_harq_robustness_eval"
# the 60-vehicle traces campaign v3 ran with (ease-rtx, SUMO 1.18.0); the laptop copies in EV/traces differ
N60 = HERE.parents[1] / "reproducibility_bundle/data/mobility/traces_n60_v3_server"
# paper palette: source = blue, ACK/clear = green, NACK/blocked = red, neutral = grey
PAL = dict(src='#E3F1F8', src_m='#A9CFE4', src_d='#3E7CA6', src_dd='#1F4F70', ack='#2E7D32', nack='#C62828',
           grey='#666666')
GRASS, ASPH, ASPH_EDGE, MARK, MEDIAN = '#FFFFFF', '#A3A3A3', '#8E8E8E', '#FFFFFF', '#EADFB4'
BCOL, BEDGE, SHADOW = '#E4E0DA', '#9A948B', '#00000018'
BGCAR, BGEDGE, CAREDGE, GLASS = '#F7F7F7', '#8A8A8A', '#2B2B2B', '#EAF2F8'
plt.rcParams.update({'font.family': 'serif', 'font.serif': ['Times New Roman', 'Times', 'DejaVu Serif'],
                     'mathtext.fontset': 'stix', 'font.size': 8, 'axes.titlesize': 8.5, 'legend.fontsize': 7.5,
                     'pdf.fonttype': 42, 'ps.fonttype': 42})

NET = ET.parse(EV / 'sumo/roundabout/roundabout.net.xml').getroot()
B = pd.read_csv(EV / 'sumo/roundabout/buildings.csv')
BLD = [np.array(list(zip(r[['x1', 'x2', 'x3', 'x4']], r[['y1', 'y2', 'y3', 'y4']]))) for _, r in B.iterrows()]
pts = lambda s: np.array([[float(v) for v in p.split(',')] for p in s.split()])
LANES = [dict(edge=e.get('id'), internal=e.get('function') == 'internal', idx=int(l.get('index')),
              n=len(e.findall('lane')), w=float(l.get('width', 3.2)), xy=pts(l.get('shape')))
         for e in NET.findall('edge') for l in e.findall('lane')]
JUNC = [pts(j.get('shape')) for j in NET.findall('junction') if j.get('shape') and j.get('type') != 'dead_end']
SEG = np.array([np.r_[l['xy'][k], l['xy'][k + 1]] for l in LANES for k in range(len(l['xy']) - 1)])
SEG_W = np.array([l['w'] for l in LANES for k in range(len(l['xy']) - 1)])


def snap(x, y, hm=None):
    """Distance to the nearest lane centre line and that lane's direction (SUMO lanes are one-way). With a motion
    heading hm, the lane is chosen among those within one lane width of the car as the one whose direction is closest
    to hm (avoids snapping to a crossing internal lane inside a junction)."""
    a, b = SEG[:, :2], SEG[:, 2:]
    ab = b - a
    u = np.clip(((np.array([x, y]) - a) * ab).sum(1) / ((ab ** 2).sum(1) + 1e-12), 0, 1)
    d = np.hypot(*(a + u[:, None] * ab - [x, y]).T)
    ang = np.arctan2(ab[:, 1], ab[:, 0])
    k = int(np.argmin(d))
    if hm is not None and np.isfinite(hm):
        near = np.where(d <= max(d[k], 0) + SEG_W)[0]
        k = int(near[np.argmin(np.abs(np.angle(np.exp(1j * (ang[near] - hm)))))])
    return d.min(), float(ang[k]), SEG_W[int(np.argmin(d))]   # off-lane test uses the nearest lane


def overlap(P, Q):
    """Separating-axis test for two convex polygons."""
    for poly in (P, Q):
        for i in range(len(poly)):
            e = poly[(i + 1) % len(poly)] - poly[i]
            n = np.array([-e[1], e[0]])
            pp, qq = P @ n, Q @ n
            if pp.max() < qq.min() or qq.max() < pp.min():
                return False
    return True


def clean(now):
    """Heading from the lane the car is on; drop background cars off a lane centre (inserted, leaving, mid
    lane change) or overlapping a car already kept (platoon first)."""
    keep, bodies, dropped = [], [], 0
    order = sorted(now.index, key=lambda n: (n > 8, n))
    for nid in order:
        r = now.loc[nid]
        if not (-170 < r.x < 170 and -170 < r.y < 170):
            continue
        d, h, w = snap(r.x, r.y, r.h)
        body, glass = car(r.x, r.y, h)
        if nid > 8 and (d > 0.35 * w or any(overlap(body, b) for b in bodies)):
            dropped += 1
            continue
        keep.append((nid, body, glass))
        bodies.append(body)
    return keep, dropped


def offset(xy, d):
    t = np.gradient(xy, axis=0)
    t /= np.linalg.norm(t, axis=1)[:, None] + 1e-12
    return xy + d * np.c_[-t[:, 1], t[:, 0]]


def seg_hits(p, q, poly):
    ccw = lambda a, b, c: (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])
    return any(ccw(p, a, b) != ccw(q, a, b) and ccw(p, q, a) != ccw(p, q, b)
               for a, b in zip(poly, np.roll(poly, -1, axis=0)))


def car(x, y, h, L=5 * 2.0, W=1.8 * 1.5):
    c, s = np.cos(h), np.sin(h)
    R = np.array([[c, -s], [s, c]])
    body = np.array([[-L, -W / 2], [-.12 * L, -W / 2], [0, -.32 * W], [0, .32 * W], [-.12 * L, W / 2], [-L, W / 2]])
    glass = np.array([[-.42 * L, -.38 * W], [-.22 * L, -.36 * W], [-.22 * L, .36 * W], [-.42 * L, .38 * W]])
    return body @ R.T + [x, y], glass @ R.T + [x, y]


def snapshot(route, t0):
    ref = pd.read_csv(EV / f'traces/{route}_ivd30_n0_s1.csv.gz', comment='#')
    p0 = ref[(ref.time - t0).abs() < 1e-6].set_index('node_id').loc[0, ['x', 'y']].values
    d = pd.read_csv(N60 / f'{route}_ivd30_n60_s1.csv.gz', comment='#')
    lead = d[d.node_id == 0]
    t = lead.time.values[np.argmin(np.hypot(lead.x - p0[0], lead.y - p0[1]))]
    now = d[(d.time - t).abs() < 1e-6].set_index('node_id')
    prev = d[(d.time - (t - .3)).abs() < 1e-6].set_index('node_id').reindex(now.index)
    now['h'] = np.arctan2(now.y - prev.y, now.x - prev.x)
    return now, t


XLIM, YLIM = (-150, 80), (-45, 165)   # trimmed view (m): platoon, ring and links; was +-160 square
fig, axs = plt.subplots(1, 2, figsize=(7.16, 3.75))
for ax, (route, t0, title) in zip(axs, [('rbt', 34.3, '(a) Roundabout W'),
                                        ('rbtE', 39.6, '(b) Roundabout E')]):
    ax.set_facecolor('none')   # transparent background
    for j in JUNC:
        ax.add_patch(Polygon(j, fc=ASPH, ec='none', zorder=1))
    for l in LANES:
        left, right = offset(l['xy'], l['w'] / 2), offset(l['xy'], -l['w'] / 2)
        ax.add_patch(Polygon(np.r_[left, right[::-1]], fc=ASPH, ec=ASPH, lw=.3, zorder=1))
    for l in LANES:
        if l['internal']:
            continue
        if l['idx'] > 0:
            ax.plot(*offset(l['xy'], -l['w'] / 2).T, color=MARK, lw=.5, ls=(0, (4, 4)), zorder=2)
        if l['idx'] == l['n'] - 1:
            ax.plot(*offset(l['xy'], l['w'] / 2).T, color=MARK if 'ring' in l['edge'] else MEDIAN, lw=.7, zorder=2)
        if l['idx'] == 0:
            ax.plot(*offset(l['xy'], -l['w'] / 2).T, color=MARK, lw=.6, zorder=2)
    for b in BLD:
        ax.add_patch(Polygon(b + [2.0, -2.0], fc=SHADOW, ec='none', zorder=3))
        ax.add_patch(Polygon(b, fc=BCOL, ec=BEDGE, lw=.5, zorder=4))
    now, t = snapshot(route, t0)
    L = now.loc[0, ['x', 'y']].values
    nblk = 0
    for m in range(1, 9):
        q = now.loc[m, ['x', 'y']].values
        blk = any(seg_hits(L, q, b) for b in BLD)
        nblk += blk
        ax.plot(*np.c_[L, q], color=PAL['nack'] if blk else PAL['ack'], lw=.9, ls=(0, (3, 2)) if blk else '-',
                zorder=5, alpha=.9, solid_capstyle='round')
    cars, dropped = clean(now)
    for nid, body, glass in cars:
        if nid == 0:
            fc, ec, z = PAL['src_d'], CAREDGE, 6.2
        elif nid <= 8:
            fc, ec, z = PAL['src_m'], CAREDGE, 6
        else:
            fc, ec, z = BGCAR, BGEDGE, 5.5
        ax.add_patch(Polygon(body, fc=fc, ec=ec, lw=.35, zorder=z))
        ax.add_patch(Polygon(glass, fc=GLASS, ec='none', zorder=z + .05))
    ax.plot([XLIM[0] + 8, XLIM[0] + 58], [YLIM[0] + 8] * 2, color='#333333', lw=1.3, solid_capstyle='butt', zorder=7)
    ax.text(XLIM[0] + 33, YLIM[0] + 11, '50 m', ha='center', va='bottom', fontsize=7, color='#333333', zorder=7)
    ax.set_xlim(*XLIM)
    ax.set_ylim(*YLIM)
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel(title, labelpad=4, fontsize=9)   # panel label under the image, above the legend
    for s in ax.spines.values():
        s.set_linewidth(.5)
        s.set_color('#555555')
    print(route, 't =', t, 'blocked links:', nblk, 'background cars drawn:', sum(n > 8 for n, _, _ in cars), 'dropped:', dropped)
h = [Line2D([], [], color=PAL['ack'], lw=1.1), Line2D([], [], color=PAL['nack'], lw=1.1, ls=(0, (3, 2))),
     Polygon([[0, 0]], fc=PAL['src_d'], ec=CAREDGE), Polygon([[0, 0]], fc=PAL['src_m'], ec=CAREDGE),
     Polygon([[0, 0]], fc=BGCAR, ec=BGEDGE), Polygon([[0, 0]], fc=BCOL, ec=BEDGE)]
fig.legend(h, ['leader link, line of sight', 'leader link, through a building', 'leader', 'platoon members',
               'background vehicles', 'buildings'], loc='lower center', ncol=3, frameon=False,
           bbox_to_anchor=(.5, -.02), handlelength=1.8)
fig.tight_layout(rect=(0, .15, 1, 1), w_pad=.6)
fig.patch.set_alpha(0)
for ext in ('pdf', 'png'):
    fig.savefig(HERE / f'fig_scenario_trim.{ext}', dpi=300, bbox_inches='tight', transparent=True)
