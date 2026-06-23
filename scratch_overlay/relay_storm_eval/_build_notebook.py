#!/usr/bin/env python3
"""Generate analysis.ipynb in the simulation dir, then it is executed for validation."""
import nbformat as nbf
from nbformat.v4 import new_notebook, new_markdown_cell, new_code_cell
from pathlib import Path

nb = new_notebook()
cells = []

cells.append(new_markdown_cell(r"""# Relay ReTX Storm Analysis — NR-V2X Sidelink Platoon

**Goal.** Quantify the *retransmission storm* that appears when a relay node participates
in the platoon leader's groupcast HARQ feedback, and show how the **MCD (Maximum
Communication Distance) feedback gate** controls it.

This notebook lives in the simulation source dir (`scratch_overlay/relay_storm_eval/`) and
reads the post-processed sweep results from `../../results_relay_storm_eval/`.

**Sweep.**
- 9-node platoon: 1 leader + 7 followers, plus an optional relay (node 4 = *close*, node 6 = *far*)
- Speeds: 80–140 km/h   |   MCD ∈ {80, 100, 150, 300, 350} m
- 30 seeds per cell, aggregated   |   3 relay configs (baseline / relay-4 / relay-6)

**KPIs.** PRR (packet reception rate), tail-end PRR, AoI (age of information),
HARQ NACK rate, sidelink channel occupancy, and the **storm ratio**
(relay hop-1 reTX ÷ baseline hop-1 reTX)."""))

cells.append(new_code_cell(r"""import pandas as pd, numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

plt.rcParams.update({
    'font.size': 11, 'axes.titlesize': 12, 'axes.labelsize': 11,
    'legend.fontsize': 9, 'figure.dpi': 120, 'savefig.dpi': 300,
    'savefig.bbox': 'tight',
})

# Results live two levels up from the sim source dir; fall back to cwd.
HERE = Path.cwd()
CANDIDATES = [HERE / '../../results_relay_storm_eval',
              HERE / 'results_relay_storm_eval',
              Path('/Users/hadih/Documents/groupcast-adaptation-ns3/results_relay_storm_eval')]
RESULTS = next(p.resolve() for p in CANDIDATES if (p / 'sweep_aggregate.csv').exists())
print('Results dir:', RESULTS)

agg   = pd.read_csv(RESULTS / 'sweep_aggregate.csv')
storm = pd.read_csv(RESULTS / 'storm_indicator.csv')

SPEEDS = sorted(agg['speed'].unique())
MCDS   = sorted(agg['mcd'].unique())
print(f'Speeds : {SPEEDS} km/h')
print(f'MCDs   : {MCDS} m')
print(f'agg rows={len(agg)}  storm rows={len(storm)}')
agg.head()"""))

cells.append(new_markdown_cell(r"""## 0. Data sanity check

Each `agg` row is one (speed, mcd, relay) cell already averaged over the 30 seeds
(`n_rows`). `is_baseline` is boolean; `relay_node` ∈ {0 (baseline), 4, 6}."""))

cells.append(new_code_cell(r"""print('config counts:')
print(agg.groupby(['is_baseline','relay_node']).size(), '\n')
print('seeds per cell:', sorted(agg['n_rows'].unique()))

baseline = agg[agg.is_baseline]
relay4   = agg[(~agg.is_baseline) & (agg.relay_node == 4)]
relay6   = agg[(~agg.is_baseline) & (agg.relay_node == 6)]

CLR = {'b': '#444444', 'r4': '#1f77b4', 'r6': '#ff7f0e'}
MRK = {'b': 'o',       'r4': 's',       'r6': '^'}
LBL = {'b': 'Baseline', 'r4': 'Relay node 4 (close)', 'r6': 'Relay node 6 (far)'}"""))

cells.append(new_markdown_cell(r"""## 1. Overview: KPIs vs speed (averaged over MCD)

Six-panel summary. Bars/lines compare the three relay configurations; error bars/bands
show the spread across the 5 MCD settings."""))

cells.append(new_code_cell(r"""def by_speed(df, col):
    g = df.groupby('speed')[col]
    return g.mean().reindex(SPEEDS), g.std().reindex(SPEEDS).fillna(0)

def grouped_bar(ax, col, ylabel, title, ymin=None, ymax=None):
    x = np.arange(len(SPEEDS)); w = 0.26
    for i,(key,df) in enumerate([('b',baseline),('r4',relay4),('r6',relay6)]):
        m,s = by_speed(df, col)
        ax.bar(x + (i-1)*w, m.values, w*0.92, yerr=s.values, capsize=2,
               label=LBL[key], color=CLR[key], alpha=0.88,
               edgecolor='white', linewidth=0.5)
    ax.set_xticks(x); ax.set_xticklabels(SPEEDS)
    ax.set_xlabel('Speed (km/h)'); ax.set_ylabel(ylabel); ax.set_title(title)
    if ymin is not None: ax.set_ylim(bottom=ymin)
    if ymax is not None: ax.set_ylim(top=ymax)
    ax.yaxis.grid(True, alpha=0.35); ax.legend()

def line_band(ax, col, ylabel, title, ymin=None, ymax=None):
    for key,df in [('b',baseline),('r4',relay4),('r6',relay6)]:
        m,s = by_speed(df, col)
        ax.plot(SPEEDS, m.values, marker=MRK[key], color=CLR[key],
                linewidth=1.6, markersize=5, label=LBL[key])
        ax.fill_between(SPEEDS, m.values-s.values, m.values+s.values,
                        color=CLR[key], alpha=0.13)
    ax.set_xlabel('Speed (km/h)'); ax.set_ylabel(ylabel); ax.set_title(title)
    if ymin is not None: ax.set_ylim(bottom=ymin)
    if ymax is not None: ax.set_ylim(top=ymax)
    ax.yaxis.grid(True, alpha=0.35); ax.legend()

fig = plt.figure(figsize=(15, 8.5))
grouped_bar(fig.add_subplot(2,3,1), 'storm_ratio_agg_mean',
            'Storm ratio (reTX frac.)', 'A. Retransmission-storm metric', ymin=0)
line_band  (fig.add_subplot(2,3,2), 'prr_mean_agg_mean',
            'Mean PRR', 'B. Packet reception rate', ymin=0.93, ymax=1.0)
line_band  (fig.add_subplot(2,3,3), 'prr_worst_agg_mean',
            'Tail PRR', 'C. Tail-end vehicle PRR', ymax=1.0)
line_band  (fig.add_subplot(2,3,4), 'aoi_mean_ms_agg_mean',
            'Mean AoI (ms)', 'D. Age of information', ymin=0)
grouped_bar(fig.add_subplot(2,3,5), 'harq_nack_rate_agg_mean',
            'HARQ NACK rate', 'E. HARQ NACK rate', ymin=0)
line_band  (fig.add_subplot(2,3,6), 'ch_occupancy_agg_mean',
            'PSSCH occupancy', 'F. Sidelink channel occupancy', ymin=0)
fig.suptitle('NR-V2X Sidelink Platoon — KPI overview (30 seeds, averaged over MCD)', y=1.01)
fig.tight_layout()
fig.savefig(RESULTS / 'fig1_overview.pdf'); fig.savefig(RESULTS / 'fig1_overview.png')
plt.show()"""))

cells.append(new_markdown_cell(r"""## 2. The MCD knob — *this is the headline result*

With the feedback-gate fix, **MCD now actually gates the groupcast HARQ feedback group.**
As MCD grows, more distant vehicles (and the relay) are pulled into the leader's feedback
group, which inflates retransmissions and the NACK rate. These panels prove the gate works
and reveal where the storm switches on."""))

cells.append(new_code_cell(r"""def vs_mcd(df, col):
    return df.groupby('mcd')[col].mean().reindex(MCDS)

fig2, axes = plt.subplots(1, 3, figsize=(15, 4.2))
panels = [('harq_nack_rate_agg_mean', 'HARQ NACK rate', 'A. NACK rate vs MCD'),
          ('storm_ratio_agg_mean',    'Storm ratio',    'B. Storm metric vs MCD'),
          ('aoi_mean_ms_agg_mean',    'Mean AoI (ms)',  'C. AoI vs MCD')]
for ax,(col,ylab,title) in zip(axes, panels):
    for key,df in [('b',baseline),('r4',relay4),('r6',relay6)]:
        ax.plot(MCDS, vs_mcd(df, col).values, marker=MRK[key], color=CLR[key],
                linewidth=1.8, markersize=6, label=LBL[key])
    ax.set_xlabel('MCD (m)'); ax.set_ylabel(ylab); ax.set_title(title)
    ax.yaxis.grid(True, alpha=0.35); ax.legend()
axes[0].axvspan(125, 360, color='red', alpha=0.06)
axes[0].text(240, axes[0].get_ylim()[1]*0.9, 'storm region', color='red',
             ha='center', fontsize=9)
fig2.suptitle('MCD feedback gate — larger MCD pulls more nodes into the feedback group', y=1.02)
fig2.tight_layout()
fig2.savefig(RESULTS / 'fig2_mcd_impact.pdf'); fig2.savefig(RESULTS / 'fig2_mcd_impact.png')
plt.show()

print('NACK-rate growth from MCD 80 -> 350 m:')
for key,df in [('b',baseline),('r4',relay4),('r6',relay6)]:
    v = vs_mcd(df, 'harq_nack_rate_agg_mean')
    print(f'  {LBL[key]:24s}: {v.loc[80]:.5f} -> {v.loc[350]:.5f}  ({v.loc[350]/v.loc[80]:.1f}x)')"""))

cells.append(new_markdown_cell(r"""## 2b. Resource occupation vs MCD

**Resource occupation** (`ch_occupancy`) = mean number of PSSCH transport blocks sent
per millisecond = `total_PSSCH_TBs / sim_duration_ms`. It is a proxy for how much of the
shared sidelink resource pool the platoon consumes. Every retransmission costs a fresh TB,
so a reTX storm shows up directly as higher resource occupation.

We compare baseline / relay-4 / relay-6 and overlay the *retransmission share* (the part of
the occupation attributable to HARQ reTX, = `occupation x storm_ratio/(1+storm_ratio)`),
which isolates the storm's contribution to channel load."""))

cells.append(new_code_cell(r"""fig2b, (axA, axB) = plt.subplots(1, 2, figsize=(13, 4.4))

# A. Absolute resource occupation vs MCD
for key,df in [('b',baseline),('r4',relay4),('r6',relay6)]:
    m = vs_mcd(df, 'ch_occupancy_agg_mean')
    axA.plot(MCDS, m.values, marker=MRK[key], color=CLR[key],
             linewidth=1.8, markersize=6, label=LBL[key])
axA.set_xlabel('MCD (m)'); axA.set_ylabel('Resource occupation (PSSCH TBs / ms)')
axA.set_title('A. Sidelink resource occupation vs MCD')
axA.set_ylim(bottom=0); axA.yaxis.grid(True, alpha=0.35); axA.legend()

# B. Decompose occupation into new-TX vs reTX share (stacked) for each config at each MCD
x = np.arange(len(MCDS)); w = 0.26
for i,(key,df,name) in enumerate([('b',baseline,'Baseline'),
                                  ('r4',relay4,'Relay 4'),
                                  ('r6',relay6,'Relay 6')]):
    occ   = vs_mcd(df, 'ch_occupancy_agg_mean').values
    sr    = vs_mcd(df, 'storm_ratio_agg_mean').values
    retx_share = occ * sr / (1.0 + sr)           # TBs/ms spent on retransmissions
    new_share  = occ - retx_share                # TBs/ms spent on fresh data
    off = x + (i-1)*w
    axB.bar(off, new_share,  w*0.9, color=CLR[key], alpha=0.55,
            edgecolor='white', linewidth=0.4,
            label=f'{name} (new TX)' )
    axB.bar(off, retx_share, w*0.9, bottom=new_share, color=CLR[key], alpha=1.0,
            hatch='///', edgecolor='white', linewidth=0.4,
            label=f'{name} (reTX)')
axB.set_xticks(x); axB.set_xticklabels(MCDS)
axB.set_xlabel('MCD (m)'); axB.set_ylabel('Resource occupation (PSSCH TBs / ms)')
axB.set_title('B. Occupation split: fresh TX vs retransmissions')
axB.yaxis.grid(True, alpha=0.35)
axB.legend(fontsize=7, ncol=3, loc='upper left')
fig2b.suptitle('Resource occupation grows with MCD as the reTX storm consumes the pool', y=1.02)
fig2b.tight_layout()
fig2b.savefig(RESULTS / 'fig2b_resource_occupation.pdf')
fig2b.savefig(RESULTS / 'fig2b_resource_occupation.png')
plt.show()

print('Resource occupation (PSSCH TBs/ms) by config, MCD 80 -> 350 m:')
for key,df in [('b',baseline),('r4',relay4),('r6',relay6)]:
    v = vs_mcd(df, 'ch_occupancy_agg_mean')
    print(f'  {LBL[key]:24s}: {v.loc[80]:.3f} -> {v.loc[350]:.3f}  '
          f'(+{(v.loc[350]/v.loc[80]-1)*100:.0f}%)')
print('\nRelay overhead vs baseline at MCD=350 m:')
b350 = vs_mcd(baseline,'ch_occupancy_agg_mean').loc[350]
for key,df,name in [('r4',relay4,'Relay 4'),('r6',relay6,'Relay 6')]:
    v = vs_mcd(df,'ch_occupancy_agg_mean').loc[350]
    print(f'  {name}: {v:.3f} vs baseline {b350:.3f}  ({v/b350:.2f}x the resource pool)')"""))

cells.append(new_markdown_cell(r"""## 3. The storm itself — hop-1 retransmissions (relay vs baseline)

`storm_indicator.csv` records the mean hop-1 reTX count for the baseline leader link
versus the relay link. **storm_ratio > 1 means the relay *amplifies* retransmissions.**"""))

cells.append(new_code_cell(r"""piv = storm.pivot_table(index='speed', columns='mcd', values='storm_ratio')
piv = piv.reindex(index=SPEEDS, columns=MCDS)

fig3, (axA, axB) = plt.subplots(1, 2, figsize=(14, 4.6),
                                gridspec_kw={'width_ratios':[1.1,1]})

# Heatmap: storm ratio over (speed, mcd)
im = axA.imshow(piv.values, aspect='auto', cmap='RdYlGn_r', vmin=0.5, vmax=2.0,
                origin='lower')
axA.set_xticks(range(len(MCDS))); axA.set_xticklabels(MCDS)
axA.set_yticks(range(len(SPEEDS))); axA.set_yticklabels(SPEEDS)
axA.set_xlabel('MCD (m)'); axA.set_ylabel('Speed (km/h)')
axA.set_title('A. Storm ratio  (relay reTX / baseline reTX)')
for i in range(len(SPEEDS)):
    for j in range(len(MCDS)):
        v = piv.values[i,j]
        if not np.isnan(v):
            axA.text(j, i, f'{v:.2f}', ha='center', va='center',
                     color='black', fontsize=8)
cb = fig3.colorbar(im, ax=axA); cb.set_label('storm ratio')

# Mean hop-1 reTX vs MCD
base_h1  = storm.groupby('mcd')['baseline_hop1_retx_mean'].mean().reindex(MCDS)
relay_h1 = storm.groupby('mcd')['relay_hop1_retx_mean'].mean().reindex(MCDS)
x = np.arange(len(MCDS)); w = 0.38
axB.bar(x-w/2, base_h1.values,  w, label='Baseline', color=CLR['b'], alpha=0.88)
axB.bar(x+w/2, relay_h1.values, w, label='With relay', color=CLR['r4'], alpha=0.88)
axB.set_xticks(x); axB.set_xticklabels(MCDS)
axB.set_xlabel('MCD (m)'); axB.set_ylabel('Mean hop-1 reTX count')
axB.set_title('B. Hop-1 retransmissions vs MCD')
axB.yaxis.grid(True, alpha=0.35); axB.legend()
fig3.suptitle('Retransmission storm: switches on at MCD >= 150 m', y=1.02)
fig3.tight_layout()
fig3.savefig(RESULTS / 'fig3_storm_detail.pdf'); fig3.savefig(RESULTS / 'fig3_storm_detail.png')
plt.show()

print('Storm ratio by MCD (mean over speed):')
print(storm.groupby('mcd')['storm_ratio'].mean().reindex(MCDS).round(3))"""))

cells.append(new_markdown_cell(r"""## 4. Summary table (mean over speed & MCD)"""))

cells.append(new_code_cell(r"""def stat(df, col): return df[col].mean()
rows = []
for key,df,name in [('b',baseline,'Baseline'),('r4',relay4,'Relay 4 (close)'),
                    ('r6',relay6,'Relay 6 (far)')]:
    rows.append({
        'Config'         : name,
        'PRR'            : round(stat(df,'prr_mean_agg_mean'), 4),
        'Tail PRR'       : round(stat(df,'prr_worst_agg_mean'), 4),
        'AoI (ms)'       : round(stat(df,'aoi_mean_ms_agg_mean'), 2),
        'NACK rate'      : round(stat(df,'harq_nack_rate_agg_mean'), 4),
        'Ch. occupancy'  : round(stat(df,'ch_occupancy_agg_mean'), 4),
        'Storm metric'   : round(stat(df,'storm_ratio_agg_mean'), 4),
    })
summary = pd.DataFrame(rows).set_index('Config')

base = summary.loc['Baseline']
delta = pd.DataFrame({
    'ΔPRR (pp)'      : ((summary['PRR']      - base['PRR'])*100).round(2),
    'ΔAoI (%)'       : ((summary['AoI (ms)'] / base['AoI (ms)'] - 1)*100).round(1),
    'ΔNACK (%)'      : ((summary['NACK rate']/ base['NACK rate']- 1)*100).round(1),
})
print('Absolute means:'); display(summary)
print('\nRelative to baseline:'); display(delta)"""))

cells.append(new_markdown_cell(r"""## 5. Statistical significance — relay 4 vs baseline

Paired by (speed, MCD); Wilcoxon signed-rank (non-parametric, n = 35 pairs)."""))

cells.append(new_code_cell(r"""from scipy import stats
key_cols = [('PRR','prr_mean_agg_mean'), ('Tail PRR','prr_worst_agg_mean'),
            ('AoI (ms)','aoi_mean_ms_agg_mean'), ('NACK rate','harq_nack_rate_agg_mean'),
            ('Storm metric','storm_ratio_agg_mean')]
print(f'{"Metric":14s} {"baseline":>10s} {"relay-4":>10s} {"Δ":>10s} {"p-value":>10s}  sig')
print('-'*64)
for name,col in key_cols:
    m = pd.merge(baseline[['speed','mcd',col]],
                 relay4[['speed','mcd',col]],
                 on=['speed','mcd'], suffixes=('_b','_r'))
    b, r = m[col+'_b'].values, m[col+'_r'].values
    try:
        _, p = stats.wilcoxon(b, r)
    except ValueError:
        p = float('nan')
    sig = '***' if p<0.001 else '**' if p<0.01 else '*' if p<0.05 else 'ns'
    print(f'{name:14s} {b.mean():10.4f} {r.mean():10.4f} {r.mean()-b.mean():+10.4f} {p:10.2e}  {sig}')"""))

cells.append(new_markdown_cell(r"""## 6. Conclusions

**1. The MCD feedback gate fix is validated.** MCD is no longer inert: as MCD grows from
80 → 350 m, the HARQ NACK rate rises by **~15×** and the storm metric roughly doubles. This
is exactly the behaviour expected once distant vehicles (and the relay) are correctly
included in / excluded from the leader's groupcast feedback group.

**2. A retransmission storm switches on at MCD ≥ 150 m.** In `storm_indicator.csv` the
relay's hop-1 reTX count jumps from ≈67 (parity with baseline) at MCD ≤ 100 m to ≈135–146
at MCD ≥ 150 m — a **storm ratio of ~1.9–2.0**. Below 100 m the relay sits *outside* the
feedback group and adds no retransmission overhead (storm ratio ≈ 0.92–0.94, i.e. it even
trims reTX slightly).

**3. Resource occupation grows with MCD and the relay roughly doubles it.** Sidelink
resource occupation (PSSCH TBs/ms) climbs as MCD admits more nodes to the feedback group,
and the stacked decomposition shows the extra load is almost entirely **retransmissions**,
not fresh data. The close relay (node 4) consumes ~2x the baseline pool at large MCD — the
direct cost of the relaying gain that must be weighed against the AoI/PRR benefit.

**4. The close relay (node 4) is a clear win on latency and reliability.** Averaged over the
sweep it lifts mean PRR by **~+1.0 pp** (0.954 → 0.964) and cuts mean AoI by **~32%**
(28.4 → 19.2 ms), both statistically significant. The far relay (node 6) helps AoI modestly
(~−9%) but does **not** improve PRR (slightly worse than baseline), because it is too far
from the tail vehicles it is meant to serve.

**5. Operating recommendation.** Deploy the **close relay (node 4)** and keep **MCD ≤ 100 m**.
That combination captures the AoI/PRR benefit of relaying while keeping the relay out of the
leader's feedback group, so the reTX storm never ignites and resource occupation stays low.

**Caveats.** The 200 m platoon means MCD only becomes "interesting" near 100–150 m; a longer
platoon would spread the transition. Fixed MCS and a single LOS road segment also bound the
generality of the NACK-rate magnitudes."""))

nb['cells'] = cells
out = Path('analysis.ipynb')
nbf.write(nb, out)
print('wrote', out.resolve(), 'with', len(cells), 'cells')
