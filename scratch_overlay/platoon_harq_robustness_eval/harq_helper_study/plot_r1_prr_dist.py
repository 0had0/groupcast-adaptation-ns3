"""R1 (leader-only blind) PRR vs distance, highway vs roundabout, moderate background.

Per-packet data (_pkt.csv: one row per packet x member, dist_m = leader-member
distance at the first attempt). Roundabout keeps only the curve: packets whose
leader-member link bends on the ring, i.e. dist_m < member * (IVD + L) - CURVE_TOL.
PRR is binned by the actual distance.

usage: plot_r1_prr_dist.py [highway_profile] [roundabout_profile]   (default: local1 full)
"""
import glob, os, sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
GAP = 35.0        # IVD 30 m + vehicle length 5 m (arc-length gap per member)
CURVE_TOL = 0.5   # m below the arc gap = link not straight
EDGES = np.arange(0, 280 + GAP, GAP) + GAP / 2 - GAP  # bins centred on 35, 70, ...
EDGES = np.append(EDGES, EDGES[-1] + GAP)
MIN_PKTS = 30     # hide bins with fewer member-packets

prof_h = sys.argv[1] if len(sys.argv) > 1 else 'local1'
prof_r = sys.argv[2] if len(sys.argv) > 2 else 'full'


def load(prof, scen, traf):
    fs = sorted(glob.glob(f'{HERE}/out/{prof}/R1/{scen}_ivd30_{traf}_moderate_s*_pkt.csv'))
    if not fs:
        return None, 0
    return pd.concat(pd.read_csv(f) for f in fs), len(fs)


os.makedirs(f'{HERE}/figures', exist_ok=True)
for traf in ['periodic1tb', 'aperiodic1tb']:
    fig, ax = plt.subplots(figsize=(6, 4))
    for scen, prof, lab, col in [('hwy', prof_h, 'Highway', 'C0'), ('rbt', prof_r, 'Roundabout (curve only)', 'C3')]:
        p, ns = load(prof, scen, traf)
        if p is None:
            print(f'no data: {prof} {scen} {traf}')
            continue
        p = p[p.dist_m >= 0]
        if scen == 'rbt':
            p = p[p.dist_m < p.member * GAP - CURVE_TOL]
        p = p.assign(bin=pd.cut(p.dist_m, EDGES))
        g = p.groupby('bin', observed=True).agg(d=('dist_m', 'mean'), prr=('rx', 'mean'), n=('rx', 'size'))
        g = g[g.n >= MIN_PKTS]
        ax.plot(g.d, g.prr, marker='o', color=col, label=f'{lab}, {ns} seed{"s" * (ns > 1)}, {g.n.sum()} pkts')
        print(scen, traf, prof, '\n', g.round(3).to_string())
    ax.set_xlabel('leader-member distance (m)')
    ax.set_ylabel('PRR')
    ax.set_ylim(0, 1.02)
    ax.grid(alpha=.3)
    ax.legend()
    ax.set_title(f'R1 PRR vs distance, moderate background, {traf.replace("1tb", "")}')
    fig.tight_layout()
    out = f'{HERE}/figures/r1_prr_dist_{traf.replace("1tb", "")}.png'
    fig.savefig(out, dpi=150)
    print(out)
