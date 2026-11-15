#!/usr/bin/env python3
"""Build STUDY_RESULTS_v3_map.ipynb: the STUDY_RESULTS_v3.ipynb analysis (Kuo helper selection) redone with the
building-map helper selection (--helperSelect=map), plus a paired map-vs-Kuo comparison section.

Data, campaign v3 (commit 32e29dd):
  out/remote_v3      leader arms R1, R1_bsc, R2, R3 and the Kuo helper arms R4, R4.1, R4.2, R5, R6
  out/remote_v3_map  map helper arms R4_map ... R6_map (1500 runs)
The notebook reads out/study_v3_map/, a folder of symlinks made here, so that every cell of the Kuo notebook works
unchanged:
  R1, R1_bsc, R2, R3          -> remote_v3/<arm>
  R4, R4.1, R4.2, R5, R6      -> remote_v3_map/<arm>_map      (the analysed scheme: map selection)
  R4_kuo ... R6_kuo           -> remote_v3/<arm>              (Kuo selection, used by the comparison section)
R4_bsc (Kuo blind + soft combining) has no map counterpart and is left out.

  python3 scripts/build_study_map_notebook.py            # build and execute
  python3 scripts/build_study_map_notebook.py --no-exec
"""
import argparse
import os
import subprocess
import sys

import nbformat as nbf
import nb_layout

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # harq_helper_study/ (this file: scripts/)
SRC = nb_layout.notebook_path('STUDY_RESULTS_v3.ipynb')
DST = nb_layout.notebook_path('STUDY_RESULTS_v3_map.ipynb')
LINKS = os.path.join(HERE, 'out', 'study_v3_map')
FINDINGS = os.path.join(nb_layout.NOTEBOOKS, 'STUDY_RESULTS_v3_map_findings.md')   # section text, written after a run
CMP_CSV = os.path.join(HERE, 'out', 'study_v3_map_cmp.csv')
LEADER = ['R1', 'R1_bsc', 'R2', 'R3']
HELPER = ['R4', 'R4.1', 'R4.2', 'R5', 'R6']


def make_links():
    os.makedirs(LINKS, exist_ok=True)
    want = {a: f'../remote_v3/{a}' for a in LEADER}
    want |= {a: f'../remote_v3_map/{a}_map' for a in HELPER}
    want |= {f'{a}_kuo': f'../remote_v3/{a}' for a in HELPER}
    for name, target in want.items():
        p = os.path.join(LINKS, name)
        assert os.path.isdir(os.path.join(LINKS, target)), target
        if os.path.islink(p):
            if os.readlink(p) == target:
                continue
            os.remove(p)
        os.symlink(target, p)
    extra = set(os.listdir(LINKS)) - set(want)
    assert not extra, f'unexpected entries in {LINKS}: {extra}'


def rep(cell, old, new, count=1):
    s = ''.join(cell['source'])
    assert s.count(old) >= 1, old[:80]
    cell['source'] = s.replace(old, new) if count == 0 else s.replace(old, new, count)


INTRO = """# HARQ x map helper study — results (campaign v3)

Same analysis as `STUDY_RESULTS_v3.ipynb` (Kuo's helper selection), with the helper chosen on the **building map**
(`--helperSelect=map`), and a last section comparing the two selections arm by arm.

**Helper selection.** Kuo's rule (`kuo`) picks the helper from distances only. The map rule (`map`) scores every
member with the building map and the CAM positions: a building on the leader -> member or member -> tail segment
counts as NLOS, another vehicle as NLOSv. It uses exact positions (`posErrM` = 0, CAMs about 30-50 ms old) and a
perfect building map, so it is the best case of a map-based choice. Everything else is identical: every map run has
the same command as the Kuo run of the same cell and seed except `--helperSelect`.

**Data**, campaign v3 (commit 32e29dd, 10 seeds): SCI 1-A on every PSSCH (`--sciOnEveryTx=true`) and the leader's
aperiodic traffic on dynamic grants; every arm at 0, 15, 30, 45 and 60 background vehicles (bg `none`, `n15`,
`n30`, `n45`, `moderate`). The notebook reads `out/study_v3_map/`, symlinks made by `build_study_map_notebook.py`:

| folder | runs | source |
|---|---|---|
| `R1`, `R1_bsc`, `R2`, `R3` | leader only | `out/remote_v3` |
| `R4`, `R4.1`, `R4.2`, `R5`, `R6` | **map** helper | `out/remote_v3_map/<arm>_map` |
| `R4_kuo` ... `R6_kuo` | Kuo helper (comparison section only) | `out/remote_v3/<arm>` |

Arms (all 3 transmissions per TB; S1 = helper relays in slots the leader reserved). In every section before the
last, R4 ... R6 are the **map** arms:

| family | arms |
|---|---|
| leader only | **R1** blind (copies decoded independently), **R2** HARQ Option 1, **R3** HARQ Option 2 |
| map helper, no takeover | **R4** blind (Kuo's scheme with the map helper), **R4.1** HARQ Option 1, **R4.2** HARQ Option 2 |
| map helper + takeover | **R5** Option 1, **R6** Option 2 — helper NACK lets the leader retake the next handed slot (Option 1 via a dedicated helper NACK resource; Option 2 helper silent on success), plus the **leader fallback**: if the leader decodes no SCI of the helper in a handed slot where a relay was expected, it retransmits in the next handed slot |
| blind + soft combining | **R1_bsc**: R1 with the blind copies soft-combined at the receivers (R4_bsc exists for Kuo only) |

Cells: highway / roundabout (N -> W exit, 1/4 of the ring) / roundaboutE (N -> E exit, 3/4 of the ring) (IVD 30 m; roundabout KPIs over the curve only) x periodic1tb / aperiodic1tb x 0-60 background vehicles (1.67 flows each).
Metrics: PRR = delivered / sent per member (mean over members; *worst* = worst member); tx/pkt and reserved/pkt in subchannel-slots per packet
(transmitted PSSCH of the platoon, and leader SCI-1A reservations); platoon CBR = platoon subchannel-slots / available; PSFCH/TB = feedback received by the leader per TB."""

CMP_MD = """## Map vs Kuo, same arm (paired by seed)

For each helper arm, cell and seed: the map run minus the Kuo run (same command except `--helperSelect`, so the
same traces and random streams up to the first different helper choice). Mean over the 10 seeds and 95 % Student-t
CI; *better* / *worse* only when the CI excludes 0. Higher is better for the PRR metrics; lower is better for the
cost, latency, AoI and DT metrics.

| metric | meaning |
|---|---|
| `prr`, `prr_worst`, `prr_pdb` | group PRR, worst member, group PRR within the PDB |
| `ss_kB`, `tx_per_pkt` | transmitted subchannel-slots per kB of payload, transmitted subchannel-slots per packet |
| `lat_group`, `lat_worst` | mean latency (ms), group and worst receiver |
| `aoi_group`, `aoi_worst` | mean age of information (ms), group and worst receiver |
| `dt_mean`, `dt_worst` | mean disconnection time (ms; reception times, message never received), group and worst member |"""

CMP_CODE = r'''
HARMS = ['R4', 'R4.1', 'R4.2', 'R5', 'R6']
KUO = [a + '_kuo' for a in HARMS]
BG_N = {'none': 0, 'n15': 15, 'n30': 30, 'n45': 45, 'moderate': 60}
SCENS, TRAFS = ['highway', 'roundabout', 'roundaboutE'], ['periodic1tb', 'aperiodic1tb']
HIGHER = {'prr': True, 'prr_worst': True, 'prr_pdb': True, 'ss_kB': False, 'tx_per_pkt': False,
          'lat_group': False, 'lat_worst': False, 'aoi_group': False, 'aoi_worst': False,
          'dt_mean': False, 'dt_worst': False}
K = CELLS + ['seed', 'arm']
runs_m = R[['arm'] + CELLS + ['seed', 'prr', 'prr_worst', 'prr_pdb', 'ss_kB', 'tx_per_pkt']]
lat_m = pd.DataFrame(lat_rows)[['arm'] + CELLS + ['seed', 'lat_group', 'lat_worst']]
aoi_m = pd.DataFrame(aoi_rows)[['arm'] + CELLS + ['seed', 'aoi_group', 'aoi_worst']]
dt_m = DT[(DT.base == 'rx') & (DT.rule == 'lost')][['arm'] + CELLS + ['seed', 'dt_mean', 'dt_worst']]
PER_RUN = runs_m.merge(lat_m, on=['arm'] + CELLS + ['seed'], how='left') \
               .merge(aoi_m, on=['arm'] + CELLS + ['seed'], how='left') \
               .merge(dt_m, on=['arm'] + CELLS + ['seed'], how='left')
mp = PER_RUN[PER_RUN.arm.isin(HARMS)].set_index(K).sort_index()
kq = PER_RUN[PER_RUN.arm.isin(KUO)].assign(arm=lambda x: x.arm.str[:-4]).set_index(K).sort_index()
assert len(mp) == len(kq) == len(mp.index.intersection(kq.index)), 'map and Kuo runs do not pair'
DIFF = (mp - kq.loc[mp.index]).reset_index()
rows = []
for (sc, tr, bg, arm), g in DIFF.groupby(CELLS + ['arm']):
    for m, hi in HIGHER.items():
        x = g[m].dropna()
        d, ci = x.mean(), ci95(x)
        better = (d - ci > 0) if hi else (d + ci < 0)
        worse = (d + ci < 0) if hi else (d - ci > 0)
        rows.append(dict(scenario=sc, traffic=tr, bg=bg, nbg=BG_N[bg], arm=arm, metric=m, d=d, ci=ci, n=len(x),
                         kuo=kq.loc[(sc, tr, bg), m].xs(arm, level='arm').mean(),
                         map=mp.loc[(sc, tr, bg), m].xs(arm, level='arm').mean(),
                         verdict='better' if better else ('worse' if worse else 'tie')))
CMP = pd.DataFrame(rows)
CMP.to_csv('out/study_v3_map_cmp.csv', index=False)   # one row per (cell, arm, metric)
print(f'{len(DIFF)} paired (map, Kuo) runs, {DIFF.groupby(CELLS + ["arm"]).ngroups} (cell, arm) groups')
'''

CMP_COUNT_MD = """### Verdict counts
Cells (3 routes x 2 traffic types x 5 loads = 30 per arm) where map is better / tied / worse than Kuo for the same
arm, per metric, split into the curve (both roundabout routes, 20 cells) and the highway (10 cells)."""

CMP_COUNT = r'''
C2 = CMP.assign(zone=np.where(CMP.scenario == 'highway', 'highway', 'curve'))
V = C2.groupby(['metric', 'arm', 'zone']).verdict.value_counts().unstack(fill_value=0).reindex(
    columns=['better', 'tie', 'worse'], fill_value=0)
V = V.join(C2.groupby(['metric', 'arm', 'zone']).d.agg(['mean', 'min', 'max']))
with pd.option_context('display.max_rows', 500):
    display(V.reindex(list(HIGHER), level=0).round(4))
'''

CMP_PLOT_MD = """### Paired difference vs load
One panel per route and traffic type, one line per helper arm: mean of (map - Kuo) over seeds, shaded = 95 % CI,
x = background vehicles. Above zero is better for the PRR rows and worse for the others."""

CMP_PLOT = r'''
def cmp_plot(metrics, title):
    fig, axes = plt.subplots(len(metrics), 6, figsize=(19, 2.7 * len(metrics)), sharex=True, squeeze=False)
    for i, m in enumerate(metrics):
        for j, (sc, tr) in enumerate([(s, t) for s in SCENS for t in TRAFS]):
            ax = axes[i, j]
            x = CMP[(CMP.metric == m) & (CMP.scenario == sc) & (CMP.traffic == tr)]
            for arm in HARMS:
                g = x[x.arm == arm].sort_values('nbg')
                col, ls = STYLE[arm]
                ax.plot(g.nbg, g.d, ls, color=col, marker=MARK[ls], ms=3.5, lw=1.4, label=LABEL[arm].replace(' map', ''))
                ax.fill_between(g.nbg, g.d - g.ci, g.d + g.ci, color=col, alpha=.12, lw=0)
            ax.axhline(0, color='k', lw=.8)
            ax.grid(alpha=.3)
            if i == 0: ax.set_title(f'{sc} / {tr}', fontsize=9)
            if j == 0: ax.set_ylabel(f'{m}\nmap - Kuo')
            if i == len(metrics) - 1: ax.set_xlabel('background vehicles')
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=5, fontsize=8, bbox_to_anchor=(0.5, 1.0))
    fig.suptitle(title + '  (shaded: 95 % CI over seeds, paired)', y=1.02)
    fig.tight_layout(rect=(0, 0, 1, 0.97)); plt.show()

cmp_plot(['prr', 'prr_worst', 'prr_pdb'], 'Reliability: map - Kuo')
'''

CMP_PLOT2 = "cmp_plot(['ss_kB', 'lat_worst', 'aoi_worst', 'dt_mean', 'dt_worst'], 'Cost and timeliness: map - Kuo')"

LEVEL_MD = """### Levels: Opt 2 (R3) against the helper arms with each selection
Group and worst-member PRR vs load, mean over seeds (95 % CI shaded). Solid = map, dashed = Kuo, dotted = R3
(leader-only Option 2, the strongest leader scheme)."""

LEVEL = r'''
LV = mean_ci(PER_RUN.assign(nbg=PER_RUN.bg.map(BG_N)), ['prr', 'prr_worst'], ['arm'] + CELLS + ['nbg'])
def level_plot(metric, arms=('R4.2', 'R6')):
    fig, axes = plt.subplots(1, 6, figsize=(19, 3.3), sharex=True, squeeze=False)
    for j, (sc, tr) in enumerate([(s, t) for s in SCENS for t in TRAFS]):
        ax = axes[0, j]
        x = LV[(LV.scenario == sc) & (LV.traffic == tr)]
        lines = [('R3', 'R3', ':', 'R3 leader Opt2')]
        lines += [(a, a, '-', LABEL[a]) for a in arms] + [(a + '_kuo', a, '--', LABEL[a].replace('map', 'Kuo')) for a in arms]
        for key, base, ls, lab in lines:
            g = x[x.arm == key].sort_values('nbg')
            col = STYLE[base][0]
            ax.plot(g.nbg, g[metric], ls, color=col, lw=1.6, marker='o' if ls == ':' else ('^' if ls == '-' else 's'),
                    ms=3.5, mfc='white' if ls == '--' else col, label=lab)
            ax.fill_between(g.nbg, g[metric] - g[metric + '_ci'], g[metric] + g[metric + '_ci'], color=col, alpha=.1, lw=0)
        ax.set_title(f'{sc} / {tr}', fontsize=9); ax.grid(alpha=.3); ax.set_xlabel('background vehicles')
    axes[0, 0].set_ylabel(metric)
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=5, fontsize=8, bbox_to_anchor=(0.5, 1.08))
    fig.tight_layout(); plt.show()

level_plot('prr'); level_plot('prr_worst')
gain = LV.pivot_table(index=CELLS + ['nbg'], columns='arm', values='prr')
pd.DataFrame({'R6 map - R3': gain['R6'] - gain['R3'], 'R6 Kuo - R3': gain['R6_kuo'] - gain['R3'],
              'R4.2 map - R3': gain['R4.2'] - gain['R3'], 'R4.2 Kuo - R3': gain['R4.2_kuo'] - gain['R3']}).round(3)
'''

DIST_MD = """### PRR vs distance, map vs Kuo
R6 (cooperative protocol) and R4.2 (helper + Option 2, no takeover), map (solid) against Kuo (dashed), with R3
(dotted) as reference; one point per member, shaded = 95 % CI over seeds."""

DIST_CMP = r'''
def dist_cmp(arms=('R4.2', 'R6')):
    trafs, bgs = TRAFS, ['none', 'moderate']
    fig, axes = plt.subplots(len(SCENS) * len(trafs), len(bgs), figsize=(12, 3.3 * len(SCENS) * len(trafs)),
                             sharey=True, squeeze=False)
    lines = [('R3', 'R3', ':', 'R3 leader Opt2')] + [(a, a, '-', LABEL[a]) for a in arms] + \
            [(a + '_kuo', a, '--', LABEL[a].replace('map', 'Kuo')) for a in arms]
    for i, (sc, tr) in enumerate([(s, t) for s in SCENS for t in trafs]):
        for j, bg in enumerate(bgs):
            ax = axes[i, j]
            d = M[(M.scenario == sc) & (M.traffic == tr) & (M.bg == bg)]
            for key, base, ls, lab in lines:
                g = mean_ci(d[d.arm == key], ['dist', 'prr'], 'member').sort_values('dist')
                col = STYLE[base][0]
                ax.plot(g.dist, g.prr, ls, color=col, lw=1.6, marker='o' if ls == ':' else ('^' if ls == '-' else 's'),
                        ms=4, mfc='white' if ls == '--' else col, label=lab)
                ax.fill_between(g.dist, g.prr - g.prr_ci, g.prr + g.prr_ci, color=col, alpha=.1, lw=0)
            ax.set_title(f'{sc} / {tr} / bg {bg}', fontsize=9); ax.grid(alpha=.3)
            if j == 0: ax.set_ylabel('PRR')
            if i == len(SCENS) * len(trafs) - 1: ax.set_xlabel('mean distance from the leader (m)')
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=5, fontsize=8, bbox_to_anchor=(0.5, 1.0))
    fig.suptitle('PRR vs distance: map (solid) vs Kuo (dashed), R3 dotted; 95 % CI over seeds', y=1.02)
    fig.tight_layout(rect=(0, 0, 1, 0.97)); plt.show()
dist_cmp()
'''

HSEL_MD = """### Which helper each selection picks (R6)
Share of the TBs in which each member is named helper (`helper_share_m1..m8`), helper changes per second, and the
share of named TBs whose helper failed the leader's first copy (`helper_corrupt_tx / helper_named_tx`); mean over
seeds, R6, 0 and 60 background vehicles."""

HSEL = r'''
hrows = []
for key in ('R6', 'R6_kuo'):
    for p in sorted(glob.glob(os.path.join(OUT, key, '*_run.csv*'))):
        tag = os.path.basename(p).split('_run.csv')[0]
        r = rd(os.path.join(OUT, key, tag) + '_run.csv').iloc[0]
        hrows.append(dict(sel='map' if key == 'R6' else 'kuo', **cell_of(tag),
                          **{f'm{i}': r[f'helper_share_m{i}'] for i in range(1, 9)},
                          changes_per_s=r.helper_changes_per_s,
                          missed_copy1=r.helper_corrupt_tx / r.helper_named_tx if r.helper_named_tx else np.nan))
HS = pd.DataFrame(hrows)
HS = HS[HS.bg.isin(['none', 'moderate'])].groupby(CELLS + ['sel']).mean(numeric_only=True).drop(columns='seed')
(100 * HS[[f'm{i}' for i in range(1, 9)]]).round(0).join(HS[['changes_per_s', 'missed_copy1']].round(3))
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-exec', action='store_true')
    a = ap.parse_args()
    make_links()
    nb = nbf.read(SRC, as_version=4)
    if nb.cells and nb_layout.TAG in nb.cells[0].get('metadata', {}).get('tags', []):
        nb.cells.pop(0)   # the source's path-setup cell; add_preamble puts it back on write
    C = nb.cells
    for c in C:
        if c.cell_type == 'code':
            c.outputs, c.execution_count = [], None
    C[0].source = INTRO
    rep(C[1], "out/remote_v3')", "out/study_v3_map')")
    rep(C[1], """LABEL = {'R1': 'R1 leader blind', 'R2': 'R2 leader Opt1', 'R3': 'R3 leader Opt2',
         'R4': 'R4 Kuo blind', 'R4.1': 'R4.1 Kuo+Opt1', 'R4.2': 'R4.2 Kuo+Opt2',
         'R5': 'R5 Kuo+Opt1+takeover', 'R6': 'R6 Kuo+Opt2+takeover'}""",
        """LABEL = {'R1': 'R1 leader blind', 'R2': 'R2 leader Opt1', 'R3': 'R3 leader Opt2',
         'R4': 'R4 map blind', 'R4.1': 'R4.1 map+Opt1', 'R4.2': 'R4.2 map+Opt2',
         'R5': 'R5 map+Opt1+takeover', 'R6': 'R6 map+Opt2+takeover'}
KUO = ['R4_kuo', 'R4.1_kuo', 'R4.2_kuo', 'R5_kuo', 'R6_kuo']   # Kuo selection, comparison section only
LABEL.update({k: LABEL[k[:-4]].replace('map', 'Kuo') for k in KUO})""")
    rep(C[1], "# per family as secondary encoding: dotted / circle leader only, dashed / square Kuo helper,\n# solid / triangle Kuo helper + takeover",
        "# per family as secondary encoding: dotted / circle leader only, dashed / square map helper,\n# solid / triangle map helper + takeover")
    # cost_metrics keys the HARQ option (and the R5 helper NACK resource) on the arm name
    rep(C[1], "PD.cost_metrics(pre, arm)", "PD.cost_metrics(pre, arm[:-4] if arm.endswith('_kuo') else arm)")
    rep(C[2], 'Dotted: leader only; dashed: Kuo helper without takeover; solid: Kuo helper with takeover.',
        'Dotted: leader only; dashed: map helper without takeover; solid: map helper with takeover.')
    rep(C[4], "'PRR vs distance: leader only, Kuo helper, Kuo helper + takeover'",
        "'PRR vs distance: leader only, map helper, map helper + takeover'")
    rep(C[5], '*PRR vs distance, HARQ-IR on vs off*: removed. The `_noIR` arms exist only in the pre-fix campaign (`pre_ndi/STUDY_RESULTS_remote_full.ipynb`); mixing',
        '*PRR vs distance, HARQ-IR on vs off*: removed. The `_noIR` arms were run only in the pre-fix campaign (deleted); mixing')
    s = ''.join(C[7].source)
    i = s.index('This section adds')
    j = s.index('One point per member')
    C[7].source = s[:i] + ('Arms R1, R1_bsc, R2, R3 and the map helper arms R4 ... R6, at every load. ') + s[j:]
    rep(C[8], "ARMS_V2 = ['R1', 'R1_bsc', 'R2', 'R3', 'R4', 'R4_bsc', 'R4.1', 'R4.2', 'R5', 'R6']",
        "ARMS_V2 = ['R1', 'R1_bsc', 'R2', 'R3', 'R4', 'R4.1', 'R4.2', 'R5', 'R6']")
    rep(C[8], """'R3': 'R3 leader Opt2', 'R4': 'R4 Kuo blind', 'R4_bsc': 'R4_bsc Kuo blind + comb.',
            'R4.1': 'R4.1 Kuo+Opt1', 'R4.2': 'R4.2 Kuo+Opt2', 'R5': 'R5 Kuo+Opt1+takeover',
            'R6': 'R6 Kuo+Opt2+takeover (proposed)'}""",
        """'R3': 'R3 leader Opt2', 'R4': 'R4 map blind',
            'R4.1': 'R4.1 map+Opt1', 'R4.2': 'R4.2 map+Opt2', 'R5': 'R5 map+Opt1+takeover',
            'R6': 'R6 map+Opt2+takeover (proposed)'}""")
    rep(C[8], "STYLE_V2 = dict(STYLE, **{'R1_bsc': ('#2a78d6', '-.'), 'R4_bsc': ('#eda100', '-.')})",
        "STYLE_V2 = dict(STYLE, **{'R1_bsc': ('#2a78d6', '-.')})")
    rep(C[8], "for p in sorted(glob.glob(os.path.join(d0, '*', '*_member.csv*'))):\n        arm = os.path.basename(os.path.dirname(p))",
        "for p in sorted(glob.glob(os.path.join(d0, '*', '*_member.csv*'))):\n        arm = os.path.basename(os.path.dirname(p))\n        if arm not in ARMS_V2:\n            continue")
    rep(C[9], " (15-45 vehicles: 6 arms only; shaded: 95 % CI)", " (shaded: 95 % CI)")
    rep(C[9], " (R2, R4_bsc, R4.1, R5: 0 and 60 vehicles only)", "")
    rep(C[39], '--profile remote_v3 ', '--profile study_v3_map ')
    rep(C[53], "    if arm not in ORDER:\n", "    if arm not in ORDER + KUO:\n")
    rep(C[59], "DT_ARMS = [a for a in ORDER + ['R1_bsc', 'R4_bsc'] if os.path.isdir(os.path.join(OUT, a))]",
        "DT_ARMS = [a for a in ORDER + ['R1_bsc'] if os.path.isdir(os.path.join(OUT, a))]   # plotted\n"
        "DT_ALL = DT_ARMS + KUO                                                             # computed")
    rep(C[59], "for arm in DT_ARMS:\n", "for arm in DT_ALL:\n")
    rep(C[60], "    t = DTCI[(DTCI.base == base) & (DTCI.rule == rule)].copy()",
        "    t = DTCI[(DTCI.base == base) & (DTCI.rule == rule) & DTCI.arm.isin(DT_ARMS)].copy()")
    rep(C[63], '(pre-fix result: `pre_ndi/STUD', '(pre-fix result, deleted: `pre_ndi/STUD')
    s = ''.join(C[63].source)
    C[63].source = s.replace('Not available on the v2 data', 'Not available on the v3 data')
    C.append(nbf.v4.new_markdown_cell(CMP_MD))
    C.append(nbf.v4.new_code_cell(CMP_CODE))
    C.append(nbf.v4.new_markdown_cell(CMP_COUNT_MD))
    C.append(nbf.v4.new_code_cell(CMP_COUNT))
    C.append(nbf.v4.new_markdown_cell(CMP_PLOT_MD))
    C.append(nbf.v4.new_code_cell(CMP_PLOT))
    C.append(nbf.v4.new_code_cell(CMP_PLOT2))
    C.append(nbf.v4.new_markdown_cell(LEVEL_MD))
    C.append(nbf.v4.new_code_cell(LEVEL))
    C.append(nbf.v4.new_markdown_cell(DIST_MD))
    C.append(nbf.v4.new_code_cell(DIST_CMP))
    C.append(nbf.v4.new_markdown_cell(HSEL_MD))
    C.append(nbf.v4.new_code_cell(HSEL))
    if os.path.exists(FINDINGS):
        C.append(nbf.v4.new_markdown_cell(open(FINDINGS).read()))
    nbf.write(nb_layout.add_preamble(nb), DST)
    print('wrote', DST)
    if not a.no_exec:
        subprocess.check_call([sys.executable, '-m', 'jupyter', 'nbconvert', '--to', 'notebook', '--execute',
                               '--inplace', '--ExecutePreprocessor.timeout=7200', DST], cwd=HERE)


if __name__ == '__main__':
    main()
