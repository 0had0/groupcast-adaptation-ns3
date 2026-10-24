#!/usr/bin/env python3
"""Build + execute DENSITY_RESULTS.ipynb: background-density sensitivity of R1 / R3 / R6.

Density points: 0 and 60 cars from the full study (bg none / moderate), 15 / 30 / 45 cars from the
density profile; all at 1.67 flows per car (the moderate per-car load).
usage: build_density_notebook.py [--full-dir out/full] [--density-dir out/density] [--no-exec]
"""
import argparse, os, subprocess
import nbformat as nbf

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument('--full-dir', default='out/full')
ap.add_argument('--density-dir', default='out/density')
ap.add_argument('--no-exec', action='store_true')
a = ap.parse_args()
full = os.path.abspath(os.path.join(HERE, a.full_dir) if not os.path.isabs(a.full_dir) else a.full_dir)
dens = os.path.abspath(os.path.join(HERE, a.density_dir) if not os.path.isabs(a.density_dir) else a.density_dir)

md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
C = []
C.append(md("""# Sensitivity to the background density: R1 vs R3 vs R6

Background vehicles in the area: **0, 15, 30, 45, 60** at 1.67 flows per car (the moderate per-car load), so
the offered background load grows linearly with the density. 0 and 60 cars are the full study's `none` and
`moderate` cells; 15 / 30 / 45 come from the `density` profile. Arms: R1 leader blind, R3 leader HARQ
Option 2, R6 Kuo helper + Option 2 + leader takeover. Scenarios: highway (53 s), roundabout W exit and
E exit (curve-only KPI window). Every point: mean over seeds, bands / bars = 95 % CI (Student t over seeds);
gains are paired per seed (same trace and seed for both arms)."""))

C.append(code(f"""import glob, os, numpy as np, pandas as pd, matplotlib.pyplot as plt
from scipy import stats
from IPython.display import display
FULL, DENS = {full!r}, {dens!r}
ARMS = ['R1', 'R3', 'R6']
STYLE = {{'R1': ('#2a78d6', ':', 'o'), 'R3': ('#1baf7a', '--', 's'), 'R6': ('#e34948', '-', '^')}}
LABEL = {{'R1': 'R1 leader blind', 'R3': 'R3 leader HARQ Opt 2', 'R6': 'R6 Kuo helper + Opt 2 + takeover'}}
SCEN = {{'hwy': 'highway', 'rbt': 'roundabout W exit (curve)', 'rbtE': 'roundabout E exit (curve)'}}
TRAF = {{'periodic1tb': 'periodic', 'aperiodic1tb': 'aperiodic'}}
NBG = {{'none': 0, 'n15': 15, 'n30': 30, 'n45': 45, 'moderate': 60}}
GAP, EDGES = 35.0, np.arange(17.5, 315, 35.0)
plt.rcParams['figure.dpi'] = 110

def ci95(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    return stats.t.ppf(.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x)) if len(x) > 1 else np.nan

def rd(p):
    p = p if os.path.exists(p) else p + '.gz'
    return pd.read_csv(p) if os.path.exists(p) else None

rows, pk = [], []
for base in (FULL, DENS):
    for arm in ARMS:
        for f in sorted(glob.glob(f'{{base}}/{{arm}}/*_run.csv*')):
            tag = os.path.basename(f).split('_run.csv')[0]
            sc, _, tr, bg, s = tag.split('_')
            if bg not in NBG:
                continue
            r = rd(f[:f.index('_run.csv')] + '_run.csv').iloc[0]
            rows.append(dict(arm=arm, sc=sc, tr=tr, nbg=NBG[bg], seed=int(s[1:]), prr=r.prr_mean, worst=r.prr_min,
                             res=(r.res_used_leader + r.res_used_helper) / r.pkts, cbr=r.cbr,
                             reclaim=r.get('helper_reclaim', np.nan) / r.pkts,
                             fallback=r.get('fallback_tx', np.nan) / r.pkts,
                             sci_miss=r.get('helper_sci_missed_rate', np.nan)))
            p = rd(f[:f.index('_run.csv')] + '_pkt.csv')
            if p is not None:
                p = p[p.dist_m >= 0]
                if sc != 'hwy':
                    p = p[p.dist_m < p.member * GAP - .5]      # roundabout: curve packets only
                g = p.groupby(pd.cut(p.dist_m, EDGES), observed=True).rx.agg(['mean', 'size'])
                for b, x in g.iterrows():
                    pk.append(dict(arm=arm, sc=sc, tr=tr, nbg=NBG[bg], seed=int(s[1:]), d=b.mid, prr=x['mean'], n=x['size']))
D, P = pd.DataFrame(rows), pd.DataFrame(pk)
cnt = D.groupby(['sc', 'tr', 'nbg', 'arm']).seed.nunique().unstack('arm')
print(f'{{len(D)}} runs; seeds per cell: {{cnt.min().min()}}-{{cnt.max().max()}}')
cnt.unstack('nbg')"""))

C.append(code("""def by_density(metric, title, ylabel, ylim=None):
    fig, axes = plt.subplots(2, 3, figsize=(14, 7.5), sharex=True, squeeze=False)
    for i, tr in enumerate(TRAF):
        for j, sc in enumerate(SCEN):
            ax = axes[i, j]
            for arm in ARMS:
                g = D[(D.sc == sc) & (D.tr == tr) & (D.arm == arm)].groupby('nbg')[metric].agg(['mean', ci95])
                if g.empty:
                    continue
                col, ls, mk = STYLE[arm]
                ax.plot(g.index, g['mean'], ls, color=col, marker=mk, ms=6, lw=2, label=LABEL[arm])
                ax.fill_between(g.index, g['mean'] - g['ci95'], g['mean'] + g['ci95'], color=col, alpha=.15, lw=0)
            ax.set_title(f'{SCEN[sc]} / {TRAF[tr]}', fontsize=10)
            ax.grid(alpha=.3); ax.set_xticks(list(NBG.values()))
            if ylim: ax.set_ylim(*ylim)
            if j == 0: ax.set_ylabel(ylabel)
            if i == 1: ax.set_xlabel('background vehicles in the area')
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=3, fontsize=9, bbox_to_anchor=(.5, 1.0))
    fig.suptitle(title + '  (bands: 95 % CI over seeds)', y=1.04); fig.tight_layout(rect=(0, 0, 1, .95)); plt.show()"""))

C.append(md("## 1. Group PRR vs density"))
C.append(code("by_density('prr', 'Group PRR (mean over members) vs background density', 'PRR')"))
C.append(md("## 2. Worst-member PRR vs density"))
C.append(code("by_density('worst', 'Worst-member PRR vs background density', 'worst-member PRR')"))

C.append(md("## 3. Gain of R6 over R1 and R3 vs density (paired per seed)"))
C.append(code("""X = D.set_index(['sc', 'tr', 'nbg', 'seed', 'arm'])[['prr', 'worst']].unstack('arm')
G = []
for (sc, tr, nbg, seed), r in X.iterrows():
    for ref in ('R1', 'R3'):
        G.append(dict(sc=sc, tr=tr, nbg=nbg, ref=ref, d_prr=r[('prr', 'R6')] - r[('prr', ref)],
                      d_worst=r[('worst', 'R6')] - r[('worst', ref)]))
G = pd.DataFrame(G)
fig, axes = plt.subplots(2, 3, figsize=(14, 7.5), sharex=True, squeeze=False)
for i, tr in enumerate(TRAF):
    for j, sc in enumerate(SCEN):
        ax = axes[i, j]
        for ref, mk in (('R1', 'o'), ('R3', 's')):
            for met, ls in (('d_prr', '-'), ('d_worst', '--')):
                g = G[(G.sc == sc) & (G.tr == tr) & (G.ref == ref)].groupby('nbg')[met].agg(['mean', ci95])
                col = STYLE[ref][0]
                ax.errorbar(g.index, g['mean'], yerr=g['ci95'], fmt=ls, marker=mk, color=col, capsize=3, lw=1.6,
                            label=f'R6 - {ref}, {"group" if met == "d_prr" else "worst member"}')
        ax.axhline(0, color='k', lw=.8)
        ax.set_title(f'{SCEN[sc]} / {TRAF[tr]}', fontsize=10); ax.grid(alpha=.3); ax.set_xticks(list(NBG.values()))
        if j == 0: ax.set_ylabel('PRR gain of R6')
        if i == 1: ax.set_xlabel('background vehicles in the area')
h, l = axes[0, 0].get_legend_handles_labels()
fig.legend(h, l, loc='upper center', ncol=4, fontsize=9, bbox_to_anchor=(.5, 1.0))
fig.suptitle('PRR gain of R6 vs density, paired per seed (bars: 95 % CI)', y=1.04)
fig.tight_layout(rect=(0, 0, 1, .95)); plt.show()
G.groupby(['sc', 'tr', 'ref', 'nbg'])[['d_prr', 'd_worst']].agg(lambda x: f'{x.mean():+.3f} ± {ci95(x):.3f}').unstack('nbg')"""))

C.append(md("## 4. Cost: resources per packet and channel busy ratio vs density"))
C.append(code("by_density('res', 'Platoon resources used per packet vs background density', 'resources per packet')"))
C.append(code("by_density('cbr', 'Channel busy ratio vs background density', 'CBR')"))

C.append(md("## 5. R6 takeover activity vs density\nReclaims on a helper NACK and fallback retransmissions "
            "per packet, and the share of TBs whose SCI the helper missed (what the fallback covers)."))
C.append(code("""fig, axes = plt.subplots(1, 3, figsize=(14, 3.8))
for ax, (met, lab) in zip(axes, [('reclaim', 'NACK takeovers per packet'), ('fallback', 'fallback retx per packet'),
                                  ('sci_miss', 'helper SCI missed (share of TBs)')]):
    for sc, ls in zip(SCEN, ['-', '--', ':']):
        for tr, col in zip(TRAF, ['#2a78d6', '#eb6834']):
            g = D[(D.arm == 'R6') & (D.sc == sc) & (D.tr == tr)].groupby('nbg')[met].agg(['mean', ci95])
            ax.errorbar(g.index, g['mean'], yerr=g['ci95'], fmt=ls, marker='o', color=col, capsize=3,
                        label=f'{sc} / {TRAF[tr]}')
    ax.set_title(lab, fontsize=10); ax.grid(alpha=.3); ax.set_xticks(list(NBG.values()))
    ax.set_xlabel('background vehicles in the area')
axes[0].legend(fontsize=7)
fig.tight_layout(); plt.show()"""))

C.append(md("## 6. PRR vs distance at each density\nPer-seed binned PRR (35 m bins; roundabout: curve "
            "packets only), mean and 95 % CI over seeds."))
C.append(code("""for tr in TRAF:
    dens = sorted(P.nbg.unique())
    fig, axes = plt.subplots(len(SCEN), len(dens), figsize=(3.3 * len(dens), 8.5), sharex=True, sharey=True, squeeze=False)
    for i, sc in enumerate(SCEN):
        for j, nb in enumerate(dens):
            ax = axes[i, j]
            for arm in ARMS:
                g = P[(P.sc == sc) & (P.tr == tr) & (P.nbg == nb) & (P.arm == arm) & (P.n >= 10)].groupby('d').prr.agg(['mean', ci95])
                col, ls, mk = STYLE[arm]
                ax.plot(g.index, g['mean'], ls, color=col, marker=mk, ms=4, lw=1.6, label=LABEL[arm])
                ax.fill_between(g.index, g['mean'] - g['ci95'], g['mean'] + g['ci95'], color=col, alpha=.15, lw=0)
            ax.set_ylim(0, 1.02); ax.grid(alpha=.3)
            if i == 0: ax.set_title(f'{nb} bg vehicles', fontsize=10)
            if j == 0: ax.set_ylabel(f'{sc}\\nPRR')
            if i == len(SCEN) - 1: ax.set_xlabel('distance (m)')
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=3, fontsize=9, bbox_to_anchor=(.5, 1.0))
    fig.suptitle(f'PRR vs distance per density, {TRAF[tr]} traffic (bands: 95 % CI)', y=1.03)
    fig.tight_layout(rect=(0, 0, 1, .96)); plt.show()"""))

C.append(md("## 7. Table: mean ± 95 % CI"))
C.append(code("""T = D.groupby(['sc', 'tr', 'arm', 'nbg'])[['prr', 'worst', 'res']].agg(lambda x: f'{x.mean():.3f} ± {ci95(x):.3f}')
display(T['prr'].unstack('nbg')); display(T['worst'].unstack('nbg')); T['res'].unstack('nbg')"""))

nb = nbf.v4.new_notebook(cells=C, metadata={'kernelspec': {'name': 'python3', 'display_name': 'Python 3',
                                                           'language': 'python'}})
out = os.path.join(HERE, 'DENSITY_RESULTS.ipynb')
nbf.write(nb, out)
print(out)
if not a.no_exec:
    subprocess.run(['jupyter', 'nbconvert', '--to', 'notebook', '--execute', '--inplace',
                    '--ExecutePreprocessor.timeout=3600', out], check=True)
    print('executed')
