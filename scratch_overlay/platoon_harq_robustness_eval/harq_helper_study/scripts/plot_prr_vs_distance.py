#!/usr/bin/env python3
"""PRR against the leader-member distance for R3, R4+C (R4_bsc), R4 + C + fallback (R4_bsc_fb) and R6, one figure per
route and traffic type, no background vehicles. For the helper arms the lines go through the forced helper drop
probability p (helper_rx_campaign controlled sweep, periodic traffic only): one row for a payload drop (mode data:
SCI-2A decoded, TB failed) and one for an SCI drop (mode sci: the helper misses the SCI and is silent). R3 has no
helper and is the dashed reference in every panel. Aperiodic traffic has no forced-drop runs: those figures show p = 0.

Distance: the leader-member distance of each packet (_pkt.csv dist_m), in 35 m bins centred on the members' highway
positions (35, 70, ..., 280 m). PRR of a bin = packets received within the PDB / packets, per seed; points: mean over
the 10 seeds, bars: 95 % Student-t half-width over seeds. PDB: 100 ms periodic, 50 ms aperiodic.

Data: p = 0 from campaign v3 / v3_fb (out/remote_v3, out/remote_v3_fb); p > 0 from out/local_helperrx (R4_bsc data,
R6 data and sci) and out/local_helperrx_fb (R4_bsc sci, R4_bsc_fb data and sci).

  python3 scripts/plot_prr_vs_distance.py      -> figures/prr_vs_distance/{hwy,rbt,rbtE}_{periodic,aperiodic}.{pdf,png}
"""
import glob
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import cm, colors
from scipy import stats

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, 'out')
FIG = os.path.join(HERE, 'figures', 'prr_vs_distance')
SCN = {'hwy': 'Highway', 'rbt': 'Roundabout W', 'rbtE': 'Roundabout E'}
TRN = {'periodic1tb': 'periodic', 'aperiodic1tb': 'aperiodic'}
ARMS = {'R4_bsc': 'R4+C', 'R4_bsc_fb': 'R4+C+fb', 'R6': 'R6'}
P0_DIR = {'R3': 'remote_v3', 'R4_bsc': 'remote_v3', 'R6': 'remote_v3', 'R4_bsc_fb': 'remote_v3_fb'}
MISS_DIR = {('R4_bsc', 'data'): 'local_helperrx', ('R4_bsc', 'sci'): 'local_helperrx_fb',
            ('R4_bsc_fb', 'data'): 'local_helperrx_fb', ('R4_bsc_fb', 'sci'): 'local_helperrx_fb',
            ('R6', 'data'): 'local_helperrx', ('R6', 'sci'): 'local_helperrx'}
PROBS = (0.1, 0.2, 0.3, 0.5, 0.7)
MODES = {'data': 'helper drops the payload', 'sci': 'helper drops the SCI'}
PDB_MS = {'periodic1tb': 100.0, 'aperiodic1tb': 50.0}   # simulator: periodic1 0.1 s, aperiodic 0.05 s
EDGES = np.arange(17.5, 280 + 35, 35)      # bins centred on 35, 70, ..., 280 m
CENTRES = (EDGES[:-1] + EDGES[1:]) / 2
CMAP = plt.get_cmap('viridis_r')
NORM = colors.Normalize(0, 0.7)


def ci95(v):
    v = np.asarray(v, float)
    return stats.t.ppf(.975, len(v) - 1) * v.std(ddof=1) / np.sqrt(len(v)) if len(v) > 1 else np.nan


def curve(files, pdb_ms):
    """per-seed PRR per distance bin -> mean and CI per bin"""
    per_seed = []
    for f in files:
        p = pd.read_csv(f, usecols=['rx', 'delay_ms', 'dist_m'])
        ok = (p.rx == 1) & (p.delay_ms <= pdb_ms)
        b = np.digitize(p.dist_m, EDGES) - 1
        s = pd.Series(ok.values).groupby(b).mean()
        per_seed.append(s.reindex(range(len(CENTRES))))
    if not per_seed:
        return None
    M = pd.concat(per_seed, axis=1)
    out = pd.DataFrame({'mean': 100 * M.mean(axis=1), 'ci': 100 * M.apply(lambda r: ci95(r.dropna()), axis=1),
                        'n': M.notna().sum(axis=1)})
    out.index = CENTRES[out.index]
    return out


def files(folder, arm, sc, tr, tag=''):
    return sorted(glob.glob(os.path.join(OUT, folder, arm, f'{sc}_ivd30_{tr}_none{tag}_s*_pkt.csv*')))


def draw(a, c, **kw):
    c = c[c.n >= 3]
    a.errorbar(c.index, c['mean'], yerr=c['ci'], capsize=2, elinewidth=.7, ms=3.5, **kw)


def figure(sc, tr):
    periodic = tr == 'periodic1tb'
    rows = list(MODES) if periodic else [None]
    fig, axs = plt.subplots(len(rows), 3, figsize=(11, 3.3 * len(rows) + 0.4), sharex=True, sharey=True,
                            squeeze=False)
    pdb = PDB_MS[tr]
    r3 = curve(files(P0_DIR['R3'], 'R3', sc, tr), pdb)
    for i, mode in enumerate(rows):
        for j, (arm, lab) in enumerate(ARMS.items()):
            a = axs[i, j]
            if r3 is not None:
                draw(a, r3, color='k', ls='--', marker='s', mfc='white', label='R3 (no helper)')
            c0 = curve(files(P0_DIR[arm], arm, sc, tr), pdb)
            if c0 is not None:
                draw(a, c0, color=CMAP(NORM(0)), marker='o', label='p = 0')
            if mode:
                for p in PROBS:
                    c = curve(files(os.path.join(MISS_DIR[(arm, mode)], 'controlled'), arm, sc, tr, f'_p{p:g}_{mode}'), pdb)
                    if c is not None:
                        draw(a, c, color=CMAP(NORM(p)), marker='o', label=f'p = {p:g}')
            a.grid(alpha=.3)
            a.set_title(lab + (f' — {MODES[mode]}' if mode else ''), fontsize=9)
            if i == len(rows) - 1:
                a.set_xlabel('leader–member distance (m)')
            if j == 0:
                a.set_ylabel('PRR (%)')
    axs[0, 0].legend(fontsize=7, loc='lower left')
    fig.suptitle(f'{SCN[sc]}, {TRN[tr]} traffic, no background vehicles'
                 + ('' if periodic else ' (no forced-drop runs for aperiodic traffic: p = 0 only)'), fontsize=10)
    fig.tight_layout(rect=(0, 0, .93 if periodic else 1, 1))
    if periodic:
        cax = fig.add_axes([.945, .12, .012, .76])
        fig.colorbar(cm.ScalarMappable(NORM, CMAP), cax=cax, label='helper drop probability p')
    return fig


def main():
    plt.switch_backend('Agg')
    plt.rcParams.update({'font.size': 9, 'font.family': 'serif'})
    os.makedirs(FIG, exist_ok=True)
    for sc in SCN:
        for tr in TRN:
            fig = figure(sc, tr)
            for ext in ('pdf', 'png'):
                fig.savefig(os.path.join(FIG, f'{sc}_{TRN[tr]}.{ext}'), dpi=160)
            plt.close(fig)
            print('wrote', f'{sc}_{TRN[tr]}')


if __name__ == '__main__':
    main()
