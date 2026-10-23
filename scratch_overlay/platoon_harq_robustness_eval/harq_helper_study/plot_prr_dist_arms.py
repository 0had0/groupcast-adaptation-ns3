"""PRR vs leader-member distance, one line per arm, one figure per traffic type.

Per-packet data (_pkt.csv[.gz]), PRR binned by the actual distance (35 m bins).
usage: plot_prr_dist_arms.py [--profile local1] [--scen hwy] [--bg moderate] [--arms R1 R4]
"""
import argparse, glob, os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
GAP = 35.0
EDGES = np.arange(GAP / 2, 280 + GAP, GAP)
MIN_PKTS = 30
LABEL = {'R1': 'R1 leader only, blind', 'R4': 'R4 Kuo helper, blind'}
SCEN = {'hwy': 'highway', 'rbt': 'roundabout (W exit)', 'rbtE': 'roundabout (E exit)'}

ap = argparse.ArgumentParser()
ap.add_argument('--profile', default='local1')
ap.add_argument('--scen', default='hwy', choices=list(SCEN))
ap.add_argument('--bg', default='moderate')
ap.add_argument('--arms', nargs='+', default=['R1', 'R4'])
a = ap.parse_args()


def load(arm, traf):
    pre = {f.split('_pkt.csv')[0] for f in
           glob.glob(f'{HERE}/out/{a.profile}/{arm}/{a.scen}_ivd30_{traf}_{a.bg}_s*_pkt.csv*')}
    fs = [p + '_pkt.csv' if os.path.exists(p + '_pkt.csv') else p + '_pkt.csv.gz' for p in sorted(pre)]
    return (pd.concat(pd.read_csv(f) for f in fs), len(fs)) if fs else (None, 0)


os.makedirs(f'{HERE}/figures', exist_ok=True)
for traf in ['periodic1tb', 'aperiodic1tb']:
    fig, ax = plt.subplots(figsize=(6, 4))
    for i, arm in enumerate(a.arms):
        p, ns = load(arm, traf)
        if p is None:
            print(f'no data: {arm} {traf}')
            continue
        p = p[p.dist_m >= 0].assign(bin=lambda d: pd.cut(d.dist_m, EDGES))
        g = p.groupby('bin', observed=True).agg(d=('dist_m', 'mean'), prr=('rx', 'mean'), n=('rx', 'size'))
        g = g[g.n >= MIN_PKTS]
        # measured bins as markers, least-squares linear fit as the line
        k, c = np.polyfit(g.d, g.prr, 1)
        xs = np.array([g.d.min(), g.d.max()])
        ax.plot(g.d, g.prr, 'o', color=f'C{i}', alpha=.45, ms=5)
        ax.plot(xs, k * xs + c, '-', color=f'C{i}', lw=2,
                label=f'{LABEL.get(arm, arm)} ({ns} seed{"s" * (ns > 1)}): {k * 100:+.3f} per 100 m')
        print(arm, traf, f'fit {c:.3f} {k * 100:+.4f}/100m |', ' '.join(f'{d:.0f}:{v:.3f}' for d, v in zip(g.d, g.prr)))
    ax.set_xlabel('leader-member distance (m)')
    ax.set_ylabel('PRR')
    ax.set_ylim(*((0.75, 1.0) if traf == 'periodic1tb' else (0, 1.02)))
    ax.grid(alpha=.3)
    ax.legend(fontsize=8)
    ax.set_title(f'PRR vs distance, {SCEN[a.scen]}, {a.bg} background, {traf.replace("1tb", "")}')
    fig.tight_layout()
    out = f'{HERE}/figures/prr_dist_{"_".join(a.arms)}_{a.scen}_{a.bg}_{traf.replace("1tb", "")}_{a.profile}.png'
    fig.savefig(out, dpi=150)
    print(out)
