"""Builds notebooks/GENIE_HELPER.ipynb: the genie (oracle) helper per 1 s window for every case of campaign v3
(3 routes x 5 background loads x 10 seeds), from one probe run per case, next to the Kuo and map choices of
campaign v3 in the same cells.

  python3 scripts/build_genie_notebook.py
  python3 -m jupyter nbconvert --to notebook --execute --inplace notebooks/GENIE_HELPER.ipynb
"""
import os
import nbformat as nbf

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

code(r'''
# ---- PATHS: edit this cell only when the notebook or the data moves -------------------------------------------
from pathlib import Path
STUDY = Path('/Users/hadih/Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study')
CODE  = STUDY / 'scripts'                              # genie_schedule.py (criterion)
PROBE = STUDY / 'out' / 'genie_probe_all' / 'R3_probe'  # <tag>_links.csv, <tag>_genie.csv, <tag>_run.csv
KUO   = STUDY / 'out' / 'remote_v3' / 'R6'              # campaign v3, Kuo selection: <tag>_hsel.csv
MAP   = STUDY / 'out' / 'remote_v3_map' / 'R6_map'      # campaign v3, map selection
LOG   = PROBE.parent / 'best_helper_per_window.csv'     # the per-window log of every case (written here)
CASES = PROBE.parent / 'best_helper_per_case.csv'       # one row per case (written here)
FIGS  = STUDY / 'figures' / 'genie_helper'             # helper-per-window maps (written by section 5)
REPLAY = STUDY / 'out' / 'genie_replay_nobg'           # section 6: <arm>_genie runs with --helperSelect=schedule
KUO_FBS = STUDY / 'out' / 'remote_v3_fbs' / 'R4_bsc_fbs' # campaign v3, R4+C+fb with Kuo selection
ROUTES = {'hwy': 'Highway', 'rbt': 'Roundabout W', 'rbtE': 'Roundabout E'}
LOADS = {'none': 0, 'n15': 15, 'n30': 30, 'n45': 45, 'moderate': 60}   # background cars
SEEDS = range(1, 11)
TAG = '{sc}_ivd30_periodic1tb_{bg}_s{seed}'
''')

md(r'''
# Genie helper: the best helper per 1 s window, every case

**Experiment.** Interference and collisions left out, a link's SINR is the same whatever scheme runs: one probe
run per case tells which member is the best helper in every window. Cases = the cells of campaign v3:
3 routes x 5 background loads (0 / 15 / 30 / 45 / 60 cars) x 10 seeds = 150 (the traffic type does not change the
links). Arm `R3_probe` (R3, no helper, `--linkProbeMs=10`) on the case's own mobility trace, with the background
cars present as vehicles (they block links: NLOSv) but sending nothing (`--bgFlowsPerCar=0`):

    python3 scripts/study.py run --profile v3_genie_probe --seeds 1-10 --only '^R3_probe/.*_periodic1tb_' \
        --extra=--bgFlowsPerCar=0 --out out/genie_probe_all
    python3 scripts/genie_schedule.py out/genie_probe_all/R3_probe

Every 10 ms the probe takes the large-scale loss of every platoon link (leader 0, members 1-8; member 1 is right
behind the leader, 8 the tail) as the channel computes it for a reception: path loss of the link's LOS / NLOSv /
NLOS condition + shadowing + NLOSv blockage. 1 s window means, absolute simulation time.

**Criterion** (per window):
- worst receiver w = the member with the highest loss from the leader (lowest SNR);
- best helper h = argmin over h != w of max(loss(leader -> h), loss(h -> w)): the best two-hop path leader -> h -> w,
  judged by its weaker hop. Runner-up = second best; margin = runner-up bottleneck - best bottleneck (dB).

Windows: every window that overlaps the case's KPI window (highway 3-52.7 s; roundabouts: the curve window, per
seed and load), the partial first and last ones included (`kpi_frac` = the part of the window inside the KPI
window). The schedules `<tag>_genie.csv` cover the whole run without gaps (window 0 s to the run's end); they
replay with `--helperSelect=schedule --helperSchedule=...`.
''')

code(r'''
import sys, os, importlib
import numpy as np, pandas as pd, matplotlib.pyplot as plt
from IPython.display import display, Markdown
sys.path.insert(0, str(CODE))
import genie_schedule as GS
importlib.reload(GS)
pd.set_option('display.width', 220, 'display.max_rows', 400, 'display.max_columns', 40)

W, S, missing = {}, {}, []
for sc in ROUTES:
    for bg in LOADS:
        for seed in SEEDS:
            tag = TAG.format(sc=sc, bg=bg, seed=seed)
            if not (PROBE / f'{tag}_links.csv').exists():
                missing.append(tag); continue
            run = pd.read_csv(PROBE / f'{tag}_run.csv').iloc[0]
            k0, k1 = float(run.kpi_start), float(run.kpi_end)
            links = GS.read_links(PROBE / f'{tag}_links.csv')
            win = {t0: v for t0, v in GS.windows(links).items() if t0 < k1 - 1e-9 and t0 + v[0] > k0 + 1e-9}
            s = GS.schedule(links)
            s = s[s.t0.isin(list(win))].reset_index(drop=True)
            s['kpi_frac'] = ((np.minimum(s.t0 + s.window_s, k1) - np.maximum(s.t0, k0)) / s.window_s).round(2)
            W[sc, bg, seed] = win
            S[sc, bg, seed] = s.assign(route=sc, nbg=LOADS[bg], seed=seed)
print(f'{len(S)} cases loaded, {len(missing)} missing', missing[:5])
''')

md('## 1. Best helper per window (the log, every case)')
code(r'''
LOGT = pd.concat(S.values(), ignore_index=True)[['route', 'nbg', 'seed', 't0', 'kpi_frac', 'worst', 'helper', 'runner_up',
                                                 'margin_db', 'bottleneck_db', 'direct_db', 'gain_db']]
LOGT.to_csv(LOG, index=False)
print('written', LOG, len(LOGT), 'windows')
# helper ID per window: one row per case, one column per window start (s)
for sc in ROUTES:
    display(Markdown(f'**{ROUTES[sc]}**: best helper per window (rows: background cars, seed)'))
    t = LOGT[LOGT.route == sc].pivot_table(index=['nbg', 'seed'], columns='t0', values='helper', aggfunc='first')
    t.columns = [f'{c:g}' for c in t.columns]
    display(t.astype('Int64'))
''')

md('## 2. Summary per case')
code(r'''
def summary(s):
    h = s.helper.values
    return pd.Series({'windows': len(s), 'switches': int((h[1:] != h[:-1]).sum()),
                      'main helper': int(s.helper.mode()[0]), 'main helper share': round((h == s.helper.mode()[0]).mean(), 2),
                      'helpers used': ' '.join(f'{k}:{v}' for k, v in s.helper.value_counts().sort_index().items()),
                      'worst receivers': ' '.join(f'{k}:{v}' for k, v in s.worst.value_counts().sort_index().items()),
                      'gain_db_mean': round(s.gain_db.mean(), 1), 'margin_db_median': round(s.margin_db.median(), 2)})
CASET = pd.DataFrame({k: summary(s) for k, s in S.items()}).T
for c in ('windows', 'switches', 'main helper', 'main helper share', 'gain_db_mean', 'margin_db_median'):
    CASET[c] = pd.to_numeric(CASET[c])
CASET.index = CASET.index.set_names(['route', 'bg', 'seed'])
CASET = CASET.reset_index()
CASET['nbg'] = CASET.bg.map(LOADS)
CASET = CASET.drop(columns='bg').set_index(['route', 'nbg', 'seed']).sort_index()
CASET.to_csv(CASES)
print('written', CASES)
CASET
''')
code(r'''
# per route and load, over the 10 seeds
g = LOGT.groupby(['route', 'nbg'])
pd.DataFrame({'windows': g.size(),
              'helper changes per case': CASET.groupby(['route', 'nbg']).switches.mean().round(1),
              'helper shares': g.helper.apply(lambda h: ' '.join(f'{k}:{v:.0%}' for k, v in
                                                                h.value_counts(normalize=True).sort_index().items())),
              'gain over direct (dB)': g.gain_db.mean().round(1),
              'margin < 1 dB': g.margin_db.apply(lambda m: f'{(m < 1).mean():.0%}')}).rename(index=ROUTES, level=0)
''')

md(r'''
## 3. Over time (seed 1)
Best helper per window for every route (columns) and background load (rows); dashed: worst receiver.
''')
code(r'''
fig, ax = plt.subplots(len(LOADS), len(ROUTES), figsize=(5 * len(ROUTES), 1.7 * len(LOADS)), squeeze=False,
                       sharey=True)
for j, sc in enumerate(ROUTES):
    for i, bg in enumerate(LOADS):
        a = ax[i, j]
        s = S.get((sc, bg, 1))
        if s is None or s.empty:
            a.set_visible(False); continue
        t = s.t0.values + 0.5
        a.step(t, s.helper, where='mid', color='C3', lw=2, label='best helper')
        a.step(t, s.worst, where='mid', color='k', ls='--', lw=1, label='worst receiver')
        a.set_yticks(range(1, 9)); a.set_ylim(0.5, 8.5)
        a.set_title(f'{ROUTES[sc]}, {LOADS[bg]} cars', fontsize=9)
        if j == 0: a.set_ylabel('member')
        if i == len(LOADS) - 1: a.set_xlabel('time (s)')
ax[0, 0].legend(fontsize=7, loc='lower left')
fig.tight_layout()
plt.show()
''')

md(r'''
## 4. Kuo and map against the genie
The helper Kuo and map applied in campaign v3, R6, same cell (periodic, same load and seed): the most frequent
applied helper among the TBs whose new transmission falls in the window. Regret = bottleneck loss of that helper on
the probe's channel (towards the window's worst receiver; the worst receiver itself as helper counts as its direct
link) - the best bottleneck, in dB.
''')
code(r'''
def applied_per_window(path, t0s, ws=1.0):
    h = pd.read_csv(path)
    h['t0'] = np.floor(h.t_ndi / ws) * ws
    return h[h.t0.isin(t0s)].groupby('t0').applied.agg(lambda x: x.value_counts().idxmax())

rows = []
for (sc, bg, seed), s in S.items():
    win, tag = W[sc, bg, seed], TAG.format(sc=sc, bg=bg, seed=seed)
    c = s[['t0', 'worst', 'helper', 'bottleneck_db']].set_index('t0')
    for name, d in (('kuo', KUO), ('map', MAP)):
        p = d / f'{tag}_hsel.csv'
        c[name] = applied_per_window(p, list(win)) if p.exists() else np.nan
        reg = []
        for t0, r in c.iterrows():
            h = r[name]
            if pd.isna(h):
                reg.append(np.nan); continue
            L, w, h = win[t0][2], int(r.worst), int(h)
            reg.append((L[0, w] if h == w else max(L[0, h], L[h, w])) - r.bottleneck_db)
        c[name + '_regret_db'] = reg
    rows.append(c.reset_index().assign(route=sc, nbg=LOADS[bg], seed=seed))
CMP = pd.concat(rows, ignore_index=True)
agg = CMP.groupby(['route', 'nbg']).apply(lambda c: pd.Series({
    'windows': len(c),
    'kuo = genie': f"{(c.kuo == c.helper).mean():.0%}", 'map = genie': f"{(c['map'] == c.helper).mean():.0%}",
    'kuo regret mean / P95 (dB)': f"{c.kuo_regret_db.mean():.1f} / {c.kuo_regret_db.quantile(0.95):.1f}",
    'map regret mean / P95 (dB)': f"{c.map_regret_db.mean():.1f} / {c.map_regret_db.quantile(0.95):.1f}"}),
    include_groups=False)
agg.rename(index=ROUTES, level=0)
''')

md(r'''
## 5. Best helper per simulation-time window, every case
One map per route: rows = case (background cars, seed), columns = 1 s window (absolute simulation time, window
start), cell = the best helper's member ID (1 = right behind the leader, 8 = tail); hatched = the partial first / last
window, only partly inside the KPI window. Blank = before / after the case's KPI window (the schedule still covers
it; it is just not measured). Written to `FIGS` as PNG.
''')
code(r'''
FIGS.mkdir(parents=True, exist_ok=True)
from matplotlib.colors import ListedColormap, BoundaryNorm
CMAP = ListedColormap(plt.get_cmap('tab10').colors[:8])
NORM = BoundaryNorm(np.arange(0.5, 9.5), 8)
for sc in ROUTES:
    d = LOGT[LOGT.route == sc]
    t = d.pivot_table(index=['nbg', 'seed'], columns='t0', values='helper', aggfunc='first')
    t = t.reindex(columns=np.arange(t.columns.min(), t.columns.max() + 1))
    fig, ax = plt.subplots(figsize=(max(8, 0.32 * t.shape[1] + 2), 0.27 * t.shape[0] + 1.6))
    im = ax.imshow(t.values, aspect='auto', cmap=CMAP, norm=NORM, interpolation='nearest')
    fr = d.pivot_table(index=['nbg', 'seed'], columns='t0', values='kpi_frac', aggfunc='first').reindex(
        index=t.index, columns=t.columns)
    for (i, j), f in np.ndenumerate(fr.values):
        if not np.isnan(f) and f < 0.999:
            ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False, hatch='////', ec='k', lw=0))
    for (i, j), v in np.ndenumerate(t.values):
        if not np.isnan(v):
            ax.text(j, i, int(v), ha='center', va='center', fontsize=6.5, color='white' if v in (1, 4, 5, 8) else 'black')
    ax.set_xticks(range(t.shape[1]))
    ax.set_xticklabels([f'{c:g}' for c in t.columns], fontsize=6.5, rotation=90)
    ax.set_yticks(range(t.shape[0]))
    ax.set_yticklabels([f'{n} cars, s{s}' for n, s in t.index], fontsize=6.5)
    for k in range(1, len(LOADS)):                       # separators between background loads
        ax.axhline(k * len(SEEDS) - 0.5, color='k', lw=1.2)
    ax.set_xlabel('window start, simulation time (s)')
    ax.set_title(f'{ROUTES[sc]}: best helper per 1 s window (leader -> helper -> worst receiver, weaker hop)')
    cb = fig.colorbar(im, ax=ax, ticks=range(1, 9), pad=0.01, fraction=0.02)
    cb.set_label('best helper (member)')
    fig.tight_layout()
    fig.savefig(FIGS / f'best_helper_{sc}.png', dpi=150, bbox_inches='tight')
    plt.show()
print('written', sorted(p.name for p in FIGS.iterdir()))
''')

md(r'''
## 6. Replay: the genie schedule signalled by the controller
The controller signals, for every TB, the helper of the window holding its new transmission, read from the case's
schedule (`--helperSelect=schedule --helperSchedule=R3_probe/<tag>_genie.csv`; aperiodic runs use the periodic
probe's schedule: the links do not depend on the traffic), in place of Kuo's optimization. Everything else is the
campaign v3 command of the arm. Runs: R6 and R4+C+fb, 0 background cars, 3 routes x 2 traffic x 10 seeds
(`study.py run --profile v3_genie --only '^(R6|R4_bsc_fbs)_genie/.*_none_s[0-9]+$'`), paired by seed with the Kuo
and map runs of campaign v3 (the local build reproduces v3 bit for bit). PRR within the PDB, KPI window, as in
SCHEMES_COMPARISON.
''')
code(r'''
import hhs_data as H
importlib.reload(H)
REF = {('R6', 'kuo'): KUO, ('R6', 'map'): MAP, ('R6', 'genie'): REPLAY / 'R6_genie',
       ('R4_bsc_fbs', 'kuo'): KUO_FBS, ('R4_bsc_fbs', 'genie'): REPLAY / 'R4_bsc_fbs_genie'}
rows, chk = [], []
for (arm, sel), d in REF.items():
    for sc in ROUTES:
        for tr in ('periodic1tb', 'aperiodic1tb'):
            for seed in SEEDS:
                tag = f'{sc}_ivd30_{tr}_none_s{seed}'
                pre = str(d / tag)
                if not (os.path.exists(pre + '_run.csv') or os.path.exists(pre + '_run.csv.gz')):
                    continue
                r = H.run_tables(pre, dict(sc=sc, tr=tr, seed=seed))['runs'].iloc[0]
                rows.append(dict(arm=arm, sel=sel, route=sc, tr=tr, seed=seed, prr_group=r.prr_group,
                                 prr_worst=r.prr_worst, ss_kB=(r.ss_L + r.ss_H) / r.kB))
                if sel == 'genie':          # the signalled helper is the file's
                    h = pd.read_csv(pre + '_hsel.csv')
                    s = pd.read_csv(PROBE / f'{sc}_ivd30_periodic1tb_none_s{seed}_genie.csv')
                    idx = np.searchsorted(s.t0.values, h.t_ndi.values, side='right') - 1
                    exp = np.where((idx >= 0) & (h.t_ndi.values < s.t0.values[idx] + s.window_s.values[idx]),
                                   s.helper.values[idx], 4)
                    chk.append((h.applied.values == exp).mean())
R = pd.DataFrame(rows)
print(f'{len(R)} runs; genie runs whose every TB got the scheduled helper: {sum(c == 1 for c in chk)}/{len(chk)}')
''')
code(r'''
# mean over seeds (%), and the paired per-seed difference genie - kuo / genie - map (pp)
m = R.pivot_table(index=['arm', 'route', 'tr'], columns='sel', values=['prr_group', 'prr_worst'], aggfunc='mean') * 100
p = R.set_index(['arm', 'route', 'tr', 'seed', 'sel'])[['prr_group', 'prr_worst']].unstack('sel')
for met in ('prr_group', 'prr_worst'):
    for o in ('kuo', 'map'):
        if (met, o) in p:
            m[(met, f'genie - {o} (pp)')] = ((p[(met, 'genie')] - p[(met, o)]) * 100).groupby(['arm', 'route', 'tr']).mean()
m = m.sort_index(axis=1)
m.rename(index=ROUTES, level=1).round(2)
''')
code(r'''
# paired 95 % CI of genie - kuo, worst-member PRR, every arm x route (traffic pooled)
out = []
for (arm, sc), g in p.groupby(level=['arm', 'route']):
    for o in ('kuo', 'map'):
        if ('prr_worst', o) not in g:
            continue
        dlt = ((g[('prr_worst', 'genie')] - g[('prr_worst', o)]) * 100).dropna()
        dg = ((g[('prr_group', 'genie')] - g[('prr_group', o)]) * 100).dropna()
        if dlt.empty:
            continue
        out.append(dict(arm=arm, route=ROUTES[sc], vs=o, n=len(dlt),
                        worst_pp=f'{dlt.mean():+.2f} ± {H.ci95(dlt):.2f}', worst_wins=f'{(dlt > 0).sum()}/{(dlt != 0).sum()}',
                        group_pp=f'{dg.mean():+.2f} ± {H.ci95(dg):.2f}'))
pd.DataFrame(out).set_index(['arm', 'route', 'vs'])
''')

nb = nbf.v4.new_notebook()
nb['cells'] = cells
nb['metadata'] = {'kernelspec': {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}}
p = os.path.join(HERE, 'notebooks', 'GENIE_HELPER.ipynb')
nbf.write(nb, p)
print('wrote', p, len(cells), 'cells')
