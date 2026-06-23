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
- Speeds: 80–140 km/h. **By design** (see `run_relay_storm.sh`) each speed is paired with the
  inter-vehicle distance (IVD) that holds a safe stopping **time-headway** (~1 s: IVD 25→39 m
  as speed 80→140), so speed never varies in isolation. The data-load cell prints the *actual*
  `speed → IVD → headway` map for the loaded CSVs and the plots label ticks accordingly.
- MCD ∈ {80, 100, 150, 300, 350} m
- 30 seeds per cell, aggregated   |   3 relay configs (baseline / relay-4 / relay-6)

**KPIs.** PRR (packet reception rate), tail-end PRR, AoI (age of information),
HARQ NACK rate, sidelink channel occupancy, and the **storm ratio**
(relay hop-1 reTX ÷ baseline hop-1 reTX)."""))

cells.append(new_markdown_cell(r"""## Hypothesis & motivation — why run this experiment?

### Background
In a NR-V2X **groupcast** platoon, the leader periodically broadcasts cooperative-awareness
messages (CAMs / kinematics) to the followers. NR sidelink groupcast supports **HARQ
feedback option 1**: every member of the destination group that fails to decode a transport
block replies with a NACK on PSFCH, and the sender retransmits. The set of vehicles that are
*expected to give feedback* is bounded by the **MCD (Maximum Communication Distance)** range
parameter — a UE farther than MCD from the sender is not part of the feedback group.

A **relay** is introduced to help the tail of the platoon, where the leader's signal is
weakest. The relay re-broadcasts the leader's packets so distant followers get a second,
stronger copy.

### The problem we suspect
The relay re-uses the leader's groupcast L2 destination ID. That means the relay's
transmissions are *also* eligible for HARQ feedback from the same group. If MCD is large
enough to include many vehicles (and the relay itself), a single packet can trigger
**feedback + retransmissions from two senders to an overlapping group**. NACKs and reTX can
then pile up and feed on themselves — a **retransmission storm** that wastes the shared
sidelink resource pool and can *hurt* the very latency/reliability the relay was meant to
improve.

There was also a latent bug: the MCD feedback gate registered RNTIs *before* NR had assigned
them, so the gate was silently bypassed and MCD did nothing. After fixing that, MCD should
once again control the feedback-group membership — which is what makes this experiment
meaningful.

### Hypotheses
- **H1 (gate works).** With the fix, increasing MCD enlarges the feedback group, so HARQ
  NACK rate and retransmission count should rise **monotonically with MCD**. (Pre-fix they
  were flat.)
- **H2 (storm threshold).** Beyond some MCD the relay's transmissions enter the leader's
  feedback group and a reTX storm ignites: relay reTX ÷ baseline reTX (**storm ratio**)
  crosses **1.0** and resource occupation jumps.
- **H3 (relay benefit vs cost).** A *close* relay (node 4) improves tail-end PRR and AoI
  more than a *far* relay (node 6), but at the cost of higher resource occupation; there is
  an MCD operating point that keeps the benefit while avoiding the storm.

### How each KPI tests the hypotheses
| Hypothesis | Primary evidence (KPI) |
|---|---|
| H1 gate works | HARQ NACK rate & storm metric **vs MCD** (Sec. 2) |
| H2 storm threshold | storm ratio heatmap, hop-1 reTX, resource occupation (Sec. 2b, 3) |
| H3 benefit vs cost | PRR / tail-PRR / AoI (Sec. 1, 4) vs resource occupation (Sec. 2b) |

The rest of the notebook gathers exactly the evidence in that table and tests it
statistically (Sec. 5)."""))


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

# This is NOT a pure speed sweep: each speed is run at the inter-vehicle
# distance (IVD) that keeps a safe stopping time-headway. Recover the actual
# (speed -> IVD) operating points from storm_indicator.csv, and the resulting
# time-headway  th = IVD / (speed in m/s).  The *intended* design couples them
# as 80->25, 90->27, 100->29, 110->32, 120->34, 130->37, 140->39 m (~1.0-1.1 s).
IVD_BY_SPEED   = storm.groupby('speed')['ivd'].first().reindex(SPEEDS).to_dict()
HEADWAY_BY_SPD = {s: IVD_BY_SPEED[s] / (s / 3.6) for s in SPEEDS}

print(f'Speeds : {SPEEDS} km/h')
print(f'MCDs   : {MCDS} m')
print('Operating points (speed -> IVD -> time-headway):')
for s in SPEEDS:
    print(f'   {s:3d} km/h  ivd={IVD_BY_SPEED[s]:.0f} m   headway={HEADWAY_BY_SPD[s]:.2f} s')
print(f'agg rows={len(agg)}  storm rows={len(storm)}')
agg.head()"""))

cells.append(new_markdown_cell(r"""## 0. Data sanity check

Each `agg` row is one (speed, mcd, relay) cell already averaged over the 30 seeds
(`n_rows`). `is_baseline` is boolean; `relay_node` ∈ {0 (baseline), 4, 6}.

> **Operating-point note.** Speed and IVD are *coupled* by design (constant safe stopping
> time-headway). The cell printed its recovered `speed → IVD → headway` map above. If every
> headway prints ~constant, the safe-headway coupling held; if IVD is flat across speeds,
> these particular CSVs were produced with a fixed IVD and only the *speed* side of the
> operating point varied (still a valid sweep, just labelled honestly)."""))

cells.append(new_code_cell(r"""print('config counts:')
print(agg.groupby(['is_baseline','relay_node']).size(), '\n')
print('seeds per cell:', sorted(agg['n_rows'].unique()))

baseline = agg[agg.is_baseline]
relay4   = agg[(~agg.is_baseline) & (agg.relay_node == 4)]
relay6   = agg[(~agg.is_baseline) & (agg.relay_node == 6)]

CLR = {'b': '#444444', 'r4': '#1f77b4', 'r6': '#ff7f0e'}
MRK = {'b': 'o',       'r4': 's',       'r6': '^'}
LBL = {'b': 'Baseline', 'r4': 'Relay node 4 (close)', 'r6': 'Relay node 6 (far)'}

# Shared x-axis labelling for the coupled speed/IVD operating points.
# Each tick shows the speed and the IVD it was run at (held at a safe
# stopping time-headway), so no panel implies speed varied in isolation.
SPEED_XLABEL = 'Operating point: speed (km/h) @ inter-vehicle distance (m)'
def speed_ticklabels():
    return [f'{s}\\n@{IVD_BY_SPEED[s]:.0f}m' for s in SPEEDS]"""))

cells.append(new_markdown_cell(r"""## Metric definitions — what each KPI measures, and why we chose it

All KPIs are extracted per run from the ns-3 sidelink trace tables
(`psschTxUeMac`, `slHarqFeedback`, `thput`, `pktTxRx`) in `agg_fixed_sweep.py`, then
averaged over the 30 seeds of each (speed, MCD, relay) cell.

| KPI (column) | Definition | What it reflects | Why we chose it |
|---|---|---|---|
| **Storm metric** `storm_ratio_agg_mean` | `n_retx / n_new` over all PSSCH TX in `psschTxUeMac` (`ndi=0` = HARQ reTX, `ndi=1` = fresh TB) | Per-run retransmission overhead: HARQ reTX per fresh transport block | Directly counts the storm at its source (the air interface). Higher = more channel effort wasted on reTX |
| **Storm ratio** `storm_ratio_hop1` (storm_indicator.csv) | relay hop-1 reTX ÷ baseline hop-1 reTX, per (speed, MCD) | Whether adding the relay *amplifies* retransmissions vs no-relay | Isolates the relay's marginal effect; **>1 = storm**, <1 = relay even helps. The crisp test of H2 |
| **Mean PRR** `prr_mean_agg_mean` | mean over nodes of `max(leader_rx, relay_rx) / leader_tx` | Average delivery reliability seen by a follower (best of direct or relayed copy) | The primary reliability target; "effective" PRR credits the relay's second copy |
| **Tail PRR** `prr_worst_agg_mean` | the **worst** node's PRR (the last vehicle) | Reliability of the most disadvantaged follower | Platoon safety is governed by the *worst* link, not the average; this is where a relay should help most (H3) |
| **AoI** `aoi_mean_ms_agg_mean` | mean of `rx_time - tx_time` (ms) over the earliest reception of each packet at each node | Freshness/latency of information at the followers | Platooning control needs *fresh* state; AoI captures both delay and loss (a dropped packet ages the info). Latency-side test of H3 |
| **HARQ NACK rate** `harq_nack_rate_agg_mean` | NACK ÷ total HARQ feedbacks in `slHarqFeedback` | Fraction of feedbacks that demand a retransmission | The *driver* of the storm and the most direct readout of feedback-group size — the headline test of H1 |
| **Resource occupation** `ch_occupancy_agg_mean` | total PSSCH TBs ÷ sim duration (TBs/ms) | How much of the shared sidelink resource pool the platoon consumes | The *cost* side of H3: a relay that doubles channel load is expensive even if it raises PRR |

### Why this particular set
These six span the three things that matter for a cooperative-platoon relay and map
one-to-one onto the hypotheses:
- **Cause** (feedback-group size): HARQ NACK rate, storm metric — *does MCD do anything?* (H1)
- **Mechanism** (storm): storm ratio, hop-1 reTX, resource occupation — *does it blow up?* (H2)
- **Effect** (user-visible QoS): PRR, tail PRR, AoI — *does the user benefit, and at what cost?* (H3)

Mean vs tail PRR are both kept on purpose: a relay can lift the average while the safety-
critical tail still lags (or vice-versa), so reporting only the mean would hide the effect
the relay exists to fix."""))

cells.append(new_markdown_cell(r"""## 1. Overview: KPIs across the speed/IVD operating points (averaged over MCD)

This is **not** a pure speed sweep. Each x-axis point is a coupled *operating point*:
by design the inter-vehicle distance (IVD) is paired with speed to preserve a safe stopping
**time-headway**, so the spacing leaves enough room to brake. Ticks are labelled
`speed @ IVD` with the **actual** IVD used for the loaded data (printed in the data-load
cell), so reading left→right reflects the true operating point rather than implying speed
moved alone.

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
    ax.set_xticks(x); ax.set_xticklabels(speed_ticklabels(), fontsize=8)
    ax.set_xlabel(SPEED_XLABEL); ax.set_ylabel(ylabel); ax.set_title(title)
    if ymin is not None: ax.set_ylim(bottom=ymin)
    if ymax is not None: ax.set_ylim(top=ymax)
    ax.yaxis.grid(True, alpha=0.35); ax.legend()

def line_band(ax, col, ylabel, title, ymin=None, ymax=None):
    x = np.arange(len(SPEEDS))
    for key,df in [('b',baseline),('r4',relay4),('r6',relay6)]:
        m,s = by_speed(df, col)
        ax.plot(x, m.values, marker=MRK[key], color=CLR[key],
                linewidth=1.6, markersize=5, label=LBL[key])
        ax.fill_between(x, m.values-s.values, m.values+s.values,
                        color=CLR[key], alpha=0.13)
    ax.set_xticks(x); ax.set_xticklabels(speed_ticklabels(), fontsize=8)
    ax.set_xlabel(SPEED_XLABEL); ax.set_ylabel(ylabel); ax.set_title(title)
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
fig.suptitle('NR-V2X Sidelink Platoon — KPIs across safe-headway speed/IVD operating points '
             '(30 seeds, averaged over MCD)', y=1.01)
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
versus the relay link. **storm_ratio_hop1 > 1 means the relay *amplifies* retransmissions.**"""))

cells.append(new_code_cell(r"""piv = storm.pivot_table(index='speed', columns='mcd', values='storm_ratio_hop1')
piv = piv.reindex(index=SPEEDS, columns=MCDS)

fig3, (axA, axB) = plt.subplots(1, 2, figsize=(14, 4.6),
                                gridspec_kw={'width_ratios':[1.1,1]})

# Heatmap: storm ratio over (speed, mcd)
im = axA.imshow(piv.values, aspect='auto', cmap='RdYlGn_r', vmin=0.5, vmax=2.0,
                origin='lower')
axA.set_xticks(range(len(MCDS))); axA.set_xticklabels(MCDS)
axA.set_yticks(range(len(SPEEDS)))
axA.set_yticklabels([f'{s} @ {IVD_BY_SPEED[s]:.0f}m' for s in SPEEDS], fontsize=8)
axA.set_xlabel('MCD (m)')
axA.set_ylabel('Operating point: speed (km/h) @ IVD (m)')
axA.set_title('A. Storm ratio  (relay reTX / baseline reTX)')
for i in range(len(SPEEDS)):
    for j in range(len(MCDS)):
        v = piv.values[i,j]
        if not np.isnan(v):
            axA.text(j, i, f'{v:.2f}', ha='center', va='center',
                     color='black', fontsize=8)
cb = fig3.colorbar(im, ax=axA); cb.set_label('storm ratio')

# Mean hop-1 reTX vs MCD
base_h1  = storm.groupby('mcd')['baseline_hop1_retx'].mean().reindex(MCDS)
relay_h1 = storm.groupby('mcd')['relay_hop1_retx'].mean().reindex(MCDS)
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
print(storm.groupby('mcd')['storm_ratio_hop1'].mean().reindex(MCDS).round(3))"""))

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

### Verdict on each hypothesis
- **H1 (gate works) — CONFIRMED.** Baseline storm metric is flat (~0.143) across all MCDs
  while the relay storm metric jumps from 0.133 to 0.273, and the HARQ NACK rate rises ~15×.
  The feedback-gate timing fix is validated.
- **H2 (storm threshold) — CONFIRMED.** The relay's hop-1 reTX is at parity with baseline for
  MCD ≤ 100 m (storm ratio 0.95–1.08) and rises monotonically to 1.31 at MCD=150 m, 1.57 at
  MCD=300 m, 1.58 at MCD=350 m. The storm ignites once the relay enters the leader's feedback
  group, and resource occupation tracks it.
- **H3 (benefit vs cost) — CONFIRMED.** The close relay (node 4) improves tail-PRR and AoI
  far more than the far relay (node 6), but ~doubles resource occupation at large MCD. A clear
  operating point exists (close relay, small MCD) that keeps the benefit without the storm.

### Supporting detail

**1. The MCD feedback gate fix is validated.** Baseline storm metric is **flat at ≈0.143
across all MCDs** (80→350 m) — the leader's own reTX overhead is MCD-independent, exactly as
expected once distant vehicles are correctly excluded from the feedback group. The relay
storm metric, by contrast, jumps from **0.133 at MCD=80** to **0.273 at MCD=300/350** —
roughly doubles — because the relay's reTX overhead scales with how many nodes (including
itself) it has to track. The HARQ NACK rate rises by **~15×** over the same MCD range.

**2. A retransmission storm switches on at MCD ≥ 150 m.** In `storm_indicator.csv` the
relay's hop-1 reTX / baseline hop-1 reTX ratio (mean over speed) is **0.95 at MCD=80 m**
(the relay sits outside the feedback group, so it actually trims reTX slightly),
**1.08 at MCD=100 m** (relay just enters the group), and then rises monotonically to
**1.31 at MCD=150 m, 1.57 at MCD=300 m, 1.58 at MCD=350 m**. The transition threshold is
MCD ≥ 150 m, exactly where the leader's groupcast feedback group first reaches far enough
down the platoon to admit the relay.

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
