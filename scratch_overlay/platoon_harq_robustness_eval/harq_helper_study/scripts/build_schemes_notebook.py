"""Builds notebooks/SCHEMES_COMPARISON.ipynb (data catalogue, schemes, metrics, analysis vs time / distance /
load / helper drop / feedback issues, R6 gain heatmaps).

  python3 scripts/build_schemes_notebook.py
  python3 -m jupyter nbconvert --to notebook --execute --inplace notebooks/SCHEMES_COMPARISON.ipynb
"""
import os
import nbformat as nbf

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

# ================================================================================================ 0. paths
code(r'''
# ---- PATHS: edit this cell only when the notebook or the data moves -------------------------------------------
from pathlib import Path
STUDY = Path('/Users/hadih/Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study')
CODE  = STUDY / 'scripts'                     # hhs_data.py (loader), hhs_plot.py (styles, plots)
OUT   = STUDY / 'out'
CACHE = OUT / 'cache_schemes'                 # parquet cache, one set per campaign; delete to re-read
FIGS  = STUDY / 'figures' / 'schemes_comparison'
SAVE_FIGS = False                             # True: every figure also written to FIGS as .png
FIGS_PAPER = STUDY / 'figures' / 'paper_schemes'   # section 10 writes the paper figures (.pdf + .png) here
JOBS = 6

# campaign key -> folder, kind (how folder / file names are parsed), arms to load
CAMPAIGNS = {
    'load':         dict(path=OUT / 'remote_v3',                    arms=['R3', 'R4_bsc', 'R6']),
    'load_fbs':     dict(path=OUT / 'remote_v3_fbs',                arms=['R4_bsc_fbs']),
    'hmiss':        dict(path=OUT / 'local_helperrx' / 'controlled',    kind='helper_miss'),
    'hmiss_fb':     dict(path=OUT / 'local_helperrx_fb' / 'controlled', kind='helper_miss', arms=['R4_bsc', 'R4_bsc_fbs']),
    'psfch_err':    dict(path=OUT / 'psfch_err',                    kind='psfch_err'),
    'psfch_period': dict(path=OUT / 'remote_psfch',                 kind='psfch_period', arms=['R3', 'R6']),
    'timing':       dict(path=OUT / 'timing',                       kind='timing'),
}
''')

md(r'''
# HARQ schemes comparison: R3, R4+C, R4+C+fb, R6

Every path is in the cell above. Loader: `scripts/hhs_data.py`. Plots and styles: `scripts/hhs_plot.py`.
Builder: `scripts/build_schemes_notebook.py`.

## 1. Data: folders, formats, origin
| key | folder | arms loaded | grid | command | run on | commit |
|---|---|---|---|---|---|---|
| `load` | `out/remote_v3` | R3, R4_bsc, R6 (folder also has R1, R1_bsc, R2, R4, R4.1, R4.2, R5) | 3 routes x 2 traffic x 5 loads (0/15/30/45/60 cars) x 10 seeds | `scripts/launch_v3.sh` -> `study.py run --profile v3 --jobs 32 --out out/v3` | ease-rtx, 32 cores; rsync to laptop | `32e29dd` |
| `load_fbs` | `out/remote_v3_fbs` | R4_bsc_fbs (= R4+C+fb) | as `load` | `scripts/launch_v3.sh --fbs` (profile `v3_fbs`) | ease-rtx | `a54a785` |
| `hmiss` | `out/local_helperrx/controlled` | R4_bsc (data), R6 (data, sci) | 3 routes x periodic x 0 cars x p = 0.1/0.2/0.3/0.5/0.7 x 10 seeds | `helper_rx_campaign/launch.sh` (`campaign.py`: v3 command + `--helperMissProb=p --helperMissMode=data\|sci`) | laptop, debug build | not recorded |
| `hmiss_fb` | `out/local_helperrx_fb/controlled` | R4_bsc (sci), R4_bsc_fbs (data, sci) | as `hmiss` | `HRX_OUT=out/local_helperrx_fb helper_rx_campaign/launch.sh --set fb\|fbs` | laptop | not recorded |
| `psfch_err` | `out/psfch_err` | R6: miss 0.01-0.3, n2a 0.001-0.05, power 23/10/0 dBm; R3: miss 0.1, n2a 0.01, 23 dBm | 3 routes x 2 traffic x 0 / 60 cars x 10 seeds | `python3 scripts/psfch_err_sweep.py run --jobs N` (v3 command + `NrSpectrumPhy::SlPsfch*`), then `collect` | laptop, NR `local.patch` | not recorded |
| `timing` | `out/timing` | R3, R6 at PSFCH processing delay 100 / 250 / 500 µs x processing gap 2 / 3 / 4 slots (+50 µs cooperative activation delay in every setting; (100 or 250 µs, 2) = load campaign, 500 µs = +1 slot) | stage A: 3 routes x 2 traffic x 0 cars x 10 seeds; stage B: 3 routes x aperiodic x 60 cars x 10 seeds | `python3 scripts/timing_sweep.py run --stage A\|B --jobs N` (v3 command + `NrUePhy::SlPsfchProcessingDelay`, `NrSlUeMac::CoopActivationDelay`, `NrSlUeMac::MinTimeGapProcessing`), then `collect` | laptop; stage B seeds 4-10 on ease-rtx (`--seeds 4-10`) | not recorded |
| `psfch_period` | `out/remote_psfch` | R3_p2/p4, R6_p2/p4 (folder also has R1_p0, R2, R4.2) | 3 routes x 2 traffic x 0 / 60 cars x 10 seeds | `scripts/launch_v3.sh --psfch --no-smoke --jobs 32` | ease-rtx | `a515e14` |

p = 0 of the helper-drop sweep and the error-free / period-1 reference of the feedback sweeps are the `load*` runs
(same commands; the local build reproduces v3 bit for bit).

**File names.** `{hwy|rbt|rbtE}_ivd30_{periodic1tb|aperiodic1tb}_{none|n15|n30|n45|moderate}[_p{p}_{data|sci}]_s{seed}_{kind}.csv[.gz]`.
`rbt` = roundabout W, `rbtE` = roundabout E, `moderate` = 60 background cars. Feedback sweeps: arm folder
`{arm}_{miss|n2a|snr_p}{value}` or `{arm}_p{period}`.

| file | rows | columns used here |
|---|---|---|
| `_run.csv` | 1 per run | `kpi_start/end` (3 / 52.7 s), `pkts`, `prr_mean`, `prr_min` (no deadline), `cbr_occupied` (SL-RP occupancy), `res_used_leader/helper` (subch.-slots), `subch_prb` |
| `_pkt.csv` | packet x member | `seq, t_tx, size, member, rx, delay_ms, dist_m` |
| `_ltx.csv` / `_htx.csv` | leader / helper transmission | `t, rb_len, tx_in_tb, origin` (0 own slot, 1 takeover after helper NACK, 2 fallback, helper silent) |
| `.cmd.json` | command | full ns-3 command line |

`psfch_err` keeps only `_run.csv` and `.log` per run: no packet traces there (no PIR, no time / distance, P5 over
seeds, resources leader / helper only).
''')

code(r'''
import sys, json, importlib, warnings
import numpy as np, pandas as pd, matplotlib.pyplot as plt
from IPython.display import display, Markdown
sys.path.insert(0, str(CODE))
import hhs_data as H, hhs_plot as P, hhs_paper as PP
importlib.reload(H); importlib.reload(P); importlib.reload(PP)
warnings.filterwarnings('ignore', category=RuntimeWarning)
pd.set_option('display.width', 200, 'display.max_columns', 40)
FIGS.mkdir(parents=True, exist_ok=True)

def show(fig, name):
    if SAVE_FIGS:
        fig.savefig(FIGS / f'{name}.png', bbox_inches='tight', dpi=150)
    plt.show()

rows = []
for k, s in CAMPAIGNS.items():
    c = s['path'] / 'COMMIT'
    rows.append(dict(key=k, folder=str(s['path'].relative_to(STUDY)) if s['path'].is_relative_to(STUDY) else str(s['path']),
                     exists=s['path'].is_dir(), commit=c.read_text()[:7] if c.exists() else '-',
                     runs_on_disk=len(H.campaign_runs(k, {**s, 'path': str(s['path'])}))))
pd.DataFrame(rows)
''')

# ================================================================================================ schemes
md(r'''
## 2. Schemes
| arm | name | leader HARQ | helper | what differs from the row above |
|---|---|---|---|---|
| R1 | blind repetition | blind, no feedback | none | - |
| R1_bsc | R1 + combining | blind | none | receivers soft-combine the blind copies |
| R2 | Option 1 HARQ | NACK-only (shared PSFCH) | none | feedback-driven retransmissions |
| **R3** | **Option 2 HARQ** | ACK/NACK per member | none | per-member feedback, leader retransmits on any NACK |
| R4 | blind helper | blind | Kuo's helper relays blindly in slots the leader hands over | second transmitter, no feedback |
| **R4_bsc (R4+C)** | **blind helper relaying** | blind | as R4 | receivers soft-combine leader + helper copies |
| **R4_bsc_fbs (R4+C+fb)** | **+ fallback** | blind | as R4+C | leader listens to the helper's first handed slot and reclaims the next one if the helper is silent; handed slots keep the PSFCH minimum gap (3 + 2 slots), so the reclaim is always possible |
| R4.1 / R4.2 | helper relay on NACK | Option 1 / Option 2 | relays on NACK | no takeover |
| R5 | cooperative, Option 1 | Option 1 | relay on NACK | takeover, Option 1 feedback |
| **R6** | **cooperative HARQ** | Option 2 | relays on NACK | takeover on helper NACK, fallback when the helper is silent, stop on all-ACK |

Plots use R3, R4+C, R4+C+fb, R6. Gain tables use R6 against R3, R4+C, R4+C+fb.
The fallback arm without the PSFCH gap (`R4_bsc_fb`, `out/remote_v3_fb`) is not used.
R4 arms send no HARQ feedback: PSFCH errors do not reach them.
''')

code(r'''
fig, ax = plt.subplots(1, 2, figsize=(11, 1.8))
for i, (a, s) in enumerate(P.STYLE.items()):
    ax[0].plot([0, 1], [-i, -i], color=s['color'], ls=s['ls'], lw=s['lw'], marker=s['marker'])
    ax[0].text(1.1, -i, s['label'], va='center')
for i, (c, s) in enumerate(P.CLASS_STYLE.items()):
    ax[1].bar(i, 1, color=s['color'], hatch=s['hatch'], edgecolor='k')
    ax[1].text(i, 1.05, s['label'], rotation=20, ha='left', fontsize=7)
ax[0].set_xlim(0, 3); [a.axis('off') for a in ax]
ax[0].set_title('scheme styles'); ax[1].set_title('resource classes', pad=25)
plt.show()
''')

# ================================================================================================ load
md(r'''
## 3. Data objects
`D = H.load(CAMPAIGNS, CACHE)` returns:
- `D['runs']`: one row per run. Keys `campaign, arm, sc, scg (Highway / Roundabouts), tr, nbg, seed, hm_p, hm_mode,
  fb_issue, fb_val`; metrics `prr_group, prr_worst, pir_*, ss_<class>, ss_data_ctrl, kB, ss_kB, cbr`.
- `D['win']`: one row per (run, 2 s window): pairs `n`, delivered `d`, `worst` = worst-member PRR of the window.
- `D['dist']`: one row per (run, 2 s window, 30 m distance bin): `n`, `d`.
''')

code(r'''
D = H.load({k: {**s, 'path': str(s['path'])} for k, s in CAMPAIGNS.items()}, str(CACHE), jobs=JOBS)
R, W, DI = D['runs'], D['win'], D['dist']
print('read errors:', len(D['errors']))
R.pivot_table(index=['campaign', 'arm'], columns='tr', values='seed', aggfunc='size', fill_value=0)
''')

# ================================================================================================ metrics
md(r'''
## 4. Metrics
Pair = (packet, member). Delivered = received within the PDB (100 ms periodic, 50 ms aperiodic). KPI window: highway 3-52.7 s; roundabouts
from the first member entering the curve to the last one leaving it (about 20-40 s long, start and length differ per seed).

**Reliability**
- **Group PRR**: delivered pairs / pairs, per run; mean over seeds (95 % t-CI).
- **Worst-receiver PRR**: lowest member PRR of the run; mean over seeds.
- **P5 worst-receiver PRR**: 5th percentile, over runs (seeds; both roundabouts pooled in the gain tables), of the
  worst-receiver PRR. Tail of the worst receiver across mobility realisations. (Over 2 s windows it is 0 on the
  roundabouts for every scheme: a member behind a building gets nothing for several seconds.)

**Packet inter-reception (PIR)**: gap between two consecutive delivered packets at one member (reception time =
generation + delay).
- **Group PIR**: mean of all gaps of the run; mean over seeds.
- **Worst-receiver PIR**: largest member mean gap; mean over seeds.
- **P95 worst PIR**: 95th percentile of the gaps of that worst member; mean over seeds.

**Channel resources**: subchannel x slot units (10 PRB x 1 slot) transmitted in the KPI window, PSCCH + PSSCH
(SCI 1-A, SCI 2-A and data share them). PSFCH is left out. Classes (from `_ltx.origin`, `tx_in_tb`, `_htx`):
`L1` leader 1st transmission, `Lrep` leader HARQ retx / blind repetition, `H` helper relay, `T1` leader takeover after
a helper NACK, `T2` leader fallback (helper silent). Normalised by generated data: **subch.-slots per kB** of
application payload generated in the window (`ss_kB`).

Untraced runs (`psfch_err`): PRR = `prr_mean` / `prr_min` of `_run.csv` (no deadline), P5 over seeds, classes leader /
helper only, kB taken from the same command in `load`. Feedback sections 9.1-9.3 and the processing-time section 8 compare everything on these
run-level metrics, the references included.
''')

code(r'''
ALL   = ['R3', 'R4_bsc', 'R4_bsc_fbs', 'R6']
SET_A = ['R3', 'R4_bsc', 'R6']
SET_B = ['R3', 'R4_bsc_fbs', 'R6']
PRR = ['prr_group', 'prr_worst', 'prr_p5']
PIR = ['pir_mean', 'pir_worst', 'pir_p95']
KEYC = ['campaign', 'arm', 'sc', 'tr', 'nbg', 'seed', 'hm_p', 'hm_mode', 'fb_issue', 'fb_val']

LOAD = R[R.campaign.str.startswith('load')].copy()
# background vehicle density in the KPI area: highway 2 km stretch (veh/km), roundabout 500 m disc (veh/km^2)
KPI_AREA = {'hwy': 2.0, 'rbt': np.pi * 0.5 ** 2, 'rbtE': np.pi * 0.5 ** 2}
DENS_LABEL = {'hwy': 'background density (veh/km)', 'rbt': 'background density (veh/km$^2$)',
              'rbtE': 'background density (veh/km$^2$)', 'Highway': 'veh/km', 'Roundabouts': 'veh/km$^2$'}
LOAD['dens'] = (LOAD.nbg / LOAD.sc.map(KPI_AREA)).round(1)
WL = W.merge(LOAD[KEYC], on=KEYC)
DL = DI.merge(LOAD[KEYC], on=KEYC)

def setcols(runs, win, **kv):
    """Copy of runs / win with columns overwritten (used to move reference runs onto a sweep axis)."""
    return runs.assign(**kv), (win.assign(**kv) if win is not None else None)

def runlevel(runs):
    """Run-level PRR (from _run.csv) in place of the packet-level one: for comparisons with untraced runs."""
    return runs.assign(prr_group=runs.prr_run_mean, prr_worst=runs.prr_run_min)
''')

# ================================================================================================ 5 time / distance
md(r'''
## 5. Reliability vs time and vs distance
Load campaigns, no background and the highest density (60 cars: 30 veh/km highway, 76 veh/km² roundabouts). Time: 2 s
windows counted from the start of each run's KPI window (the roundabout window starts at a different time per seed), mean
over the runs that cover the window (windows covered by fewer than half the runs are dropped); rows group PRR and
worst-receiver PRR. Distance: leader-member distance, 30 m bins; rows group PRR (pooled pairs),
worst seed, P5 over (seed x 2 s window).
''')

for title, arms, tag in [('all schemes', 'ALL', 'all'), ('R3, R4+C, R6', 'SET_A', 'A'), ('R3, R4+C+fb, R6', 'SET_B', 'B')]:
    md(f'### 5.{["all", "A", "B"].index(tag) + 1} {title}')
    code(f'''
for nbg in (0, 60):
    w = WL[WL.nbg == nbg]
    show(P.plot_series(H.time_series(w, ['arm', 'sc', 'tr']), 't_rel', ['prr_group', 'prr_worst'], {arms},
                       'time since KPI window start (s)', f'PRR vs time, {{nbg}} background cars ({title})'), f'time_{tag}_n{{nbg}}')
for nbg in (0, 60):
    d = DL[DL.nbg == nbg]
    show(P.plot_series(H.dist_series(d, ['arm', 'sc', 'tr']), 'dbin', PRR, {arms},
                       'leader-member distance (m)', f'PRR vs distance, {{nbg}} background cars ({title})'), f'dist_{tag}_n{{nbg}}')
''')

# ================================================================================================ 6 load
md(r'''
## 6. Reliability vs background vehicle density
x = background vehicles per km of the 2 km highway KPI stretch (0 / 7.5 / 15 / 22.5 / 30 veh/km), per km² of the
500 m roundabout KPI disc (0 / 19 / 38 / 57 / 76 veh/km²). The table below gives the SL-RP occupancy each density produces.
''')
code(r'''
S_LOAD = H.summarize(LOAD, WL, ['arm', 'sc', 'tr', 'nbg'])
S_LOAD = S_LOAD.merge(LOAD[['sc', 'nbg', 'dens']].drop_duplicates(), on=['sc', 'nbg'])
S_LOAD_G = H.summarize(LOAD, WL, ['arm', 'scg', 'tr', 'dens'])
S_LOAD.pivot_table(index=['sc', 'tr', 'dens'], columns='arm', values='cbr').round(2)   # SL-RP occupancy
''')
md('### 6.1 All schemes')
code(r'''
show(P.plot_x(S_LOAD, 'dens', PRR, ALL, DENS_LABEL, 'Reliability vs density, all schemes'), 'dens_prr_all')
show(P.plot_x(S_LOAD, 'dens', PIR, ALL, DENS_LABEL, 'PIR vs density, all schemes', pct=False), 'dens_pir_all')
''')
md('### 6.2 R3, R4+C, R6')
code(r'''show(P.plot_x(S_LOAD, 'dens', PRR, SET_A, DENS_LABEL, 'Reliability vs density: R3, R4+C, R6'), 'dens_prr_A')''')
md('### 6.3 R3, R4+C+fb, R6')
code(r'''show(P.plot_x(S_LOAD, 'dens', PRR, SET_B, DENS_LABEL, 'Reliability vs density: R3, R4+C+fb, R6'), 'dens_prr_B')''')
md('### 6.4 Resources vs density (data + control, no PSFCH)')
code(r'''
show(P.plot_resources(S_LOAD, 'dens', ALL, DENS_LABEL, 'Subchannel-slots vs density', xcat=True), 'dens_res_all')
S_LOAD.pivot_table(index=['sc', 'tr', 'dens'], columns='arm', values='ss_kB').round(1)
''')

# ================================================================================================ 7 helper drop
md(r'''
## 7. Reliability vs helper drop probability
Forced miss of the leader's first copy at the selected helper, probability p (periodic traffic, 0 cars).
`sci`: SCI 2-A lost, helper silent. `data`: SCI 2-A decoded, TB lost, helper NACKs and drops its buffer.
p = 0 from the load campaigns. R3 has no helper: its p = 0 value is repeated on every p.
''')
code(r'''
HM_P = [0.0, 0.1, 0.2, 0.3, 0.5, 0.7]
def hm_frame(mode):
    hm = R[R.campaign.str.startswith('hmiss') & (R.hm_mode == mode)]
    hw = W.merge(hm[KEYC], on=KEYC)
    ref = LOAD[(LOAD.tr == 'periodic1tb') & (LOAD.nbg == 0)]
    rw = WL.merge(ref[KEYC], on=KEYC)
    parts = [(hm, hw)]
    for arm in ['R4_bsc', 'R4_bsc_fbs', 'R6']:
        a = ref[ref.arm == arm]
        parts.append(setcols(a, rw.merge(a[KEYC], on=KEYC), hm_mode=mode, hm_p=0.0))
    r3 = ref[ref.arm == 'R3']
    for p in HM_P:
        parts.append(setcols(r3, rw.merge(r3[KEYC], on=KEYC), hm_mode=mode, hm_p=p))
    return pd.concat([p[0] for p in parts]), pd.concat([p[1] for p in parts])

HM = {m: hm_frame(m) for m in ('sci', 'data')}
S_HM = {m: H.summarize(*HM[m], ['arm', 'sc', 'tr', 'hm_p']) for m in HM}
S_HM_G = {m: H.summarize(*HM[m], ['arm', 'scg', 'tr', 'hm_p']) for m in HM}
''')
for i, (m, name) in enumerate([('sci', 'SCI drop (helper silent)'), ('data', 'payload drop (helper NACKs)')]):
    md(f'### 7.{i + 1} Helper {name}')
    code(f'''
show(P.plot_x(S_HM['{m}'], 'hm_p', PRR, ALL, 'helper {m} drop probability p', 'Reliability vs helper {name}, all schemes'), 'hm_{m}_prr_all')
show(P.plot_x(S_HM['{m}'], 'hm_p', PIR, ALL, 'helper {m} drop probability p', 'PIR vs helper {name}, all schemes', pct=False), 'hm_{m}_pir_all')
show(P.plot_x(S_HM['{m}'], 'hm_p', PRR, SET_A, 'helper {m} drop probability p', 'R3, R4+C, R6'), 'hm_{m}_prr_A')
show(P.plot_x(S_HM['{m}'], 'hm_p', PRR, SET_B, 'helper {m} drop probability p', 'R3, R4+C+fb, R6'), 'hm_{m}_prr_B')
show(P.plot_resources(S_HM['{m}'], 'hm_p', ALL, 'p', 'Subchannel-slots vs helper {name}', xcat=True), 'hm_{m}_res')
''')

# ================================================================================================ 8 feedback
md(r"""
## 8. Reliability vs processing time
Feedback timing at the receiving MAC (campaign `timing`, R3 and R6 only):
- **PSFCH processing delay**: end of the PSFCH to the feedback at the MAC, 100 / 250 / 500 µs. With the 50 µs
  cooperative activation delay of the sweep, 100 and 250 µs keep the feedback in the same slot; 500 µs crosses the 0.5 ms
  slot boundary and delays every feedback-driven decision by one slot. The axis is therefore 0 / 0 / +1 slot of latency.
- **Processing gap** (`MinTimeGapProcessing`): slots the MAC needs between the feedback and a retransmission it
  triggers, 2 / 3 / 4 slots (main campaigns: 2). With the 3-slot PSFCH gap it sets the minimum spacing of feedback-driven
  retransmissions (5 / 6 / 7 slots).

Reference: (100 µs, 2 slots) and (250 µs, 2 slots) reproduce the load campaign (180 / 180 runs each: same packet counts, group and worst PRR equal to 1e-6);
(500 µs, 2 slots) does not. So the load campaign has no extra feedback slot, and 500 µs is the "+1 slot" case. Runs are untraced: run-level PRR, P5 over runs, no PIR, resources
leader / helper. 0 cars: all routes and traffic types, 10 seeds; 60 cars: aperiodic traffic, 10 seeds per route.
R4+C and R4+C+fb were not run in this sweep: horizontal lines at their load-campaign values.
""")
code(r'''
TM = R[R.fb_issue == 'timing'].copy()
print(TM.pivot_table(index=['arm', 'tm_delay'], columns=['nbg', 'tm_gap'], values='seed', aggfunc='size'))

def tm_frame(x, fixed):
    """Runs of the sweep along x ('tm_delay' or 'tm_gap') with the other knob at `fixed`."""
    other = 'tm_gap' if x == 'tm_delay' else 'tm_delay'
    return TM[TM[other] == fixed]

def tm_section(x, fixed, xlabel, xt=None):
    r = tm_frame(x, fixed)
    for nbg in (0, 60):
        rn = r[r.nbg == nbg]
        s = H.summarize(rn, None, ['arm', 'sc', 'tr', x], p5_from='runs')
        r4 = runlevel(LOAD[(LOAD.nbg == nbg) & LOAD.arm.isin(['R4_bsc', 'R4_bsc_fbs'])])
        ref = H.summarize(r4, None, ['arm', 'sc', 'tr'], p5_from='runs')
        kw = dict(xcat=True, xticklabels=xt, ref=ref, ref_label='load campaign, not in this sweep')
        show(P.plot_x(s, x, PRR, ALL, xlabel, f'Reliability vs {xlabel}, {nbg} background cars, all schemes', **kw),
             f'tm_{x}_prr_all_n{nbg}')
        for nm, arms in (('A', SET_A), ('B', SET_B)):
            names = ', '.join(P.STYLE[a]['label'].split(' ')[0] for a in arms)
            show(P.plot_x(s, x, PRR, arms, xlabel, f'{nbg} background cars: {names}', **kw), f'tm_{x}_prr_{nm}_n{nbg}')
        show(P.plot_resources(s, x, ['R3', 'R6'], xlabel, f'Subchannel-slots vs {xlabel}, {nbg} background cars',
                              xcat=True, xticklabels=xt, classes=['L', 'H']), f'tm_{x}_res_n{nbg}')

def tm_group(x, fixed):
    """Gain-table input: sweep runs + R4 arms (load campaign, run-level) repeated on every x, same cells."""
    r = tm_frame(x, fixed)
    r4 = runlevel(LOAD[LOAD.arm.isin(['R4_bsc', 'R4_bsc_fbs'])]).merge(r[['sc', 'tr', 'nbg']].drop_duplicates())
    rr = pd.concat([r] + [r4.assign(**{x: v}) for v in sorted(r[x].unique())])
    rr['x'] = rr.nbg.astype(int).astype(str) + ' cars · ' + rr[x].map('{:g}'.format)
    return H.summarize(rr, None, ['arm', 'scg', 'tr', 'x'], p5_from='runs')
''')
md('### 8.1 PSFCH processing delay: +1 slot at 500 µs (processing gap 2 slots)')
code(r'''tm_section('tm_delay', 2, 'PSFCH processing delay (µs)', xt=['100', '250', '500 (+1 slot)'])''')
md('### 8.2 Processing gap (PSFCH processing delay 100 µs = load campaign timing)')
code(r'''tm_section('tm_gap', 100, 'processing gap (slots)')''')
md('### 8.3 R6 - R3 group PRR over the whole grid (pp)')
code(r'''
g = H.summarize(TM, None, ['arm', 'scg', 'tr', 'nbg', 'tm_delay', 'tm_gap'], p5_from='runs')
g = g.pivot_table(index=['scg', 'tr', 'nbg', 'tm_delay'], columns=['tm_gap', 'arm'], values='prr_group')
(100 * (g.xs('R6', axis=1, level='arm') - g.xs('R3', axis=1, level='arm'))).round(1)
''')

md(r'''
## 9. Reliability vs feedback (PSFCH) issues
| issue | model | values | arms |
|---|---|---|---|
| PSFCH miss (DTX) | each received ACK/NACK lost, per receiver | 0.01, 0.05, 0.1, 0.2, 0.3 | R6; R3 at 0.1 |
| NACK -> ACK | a received NACK read as ACK | 0.001, 0.01, 0.05 | R6; R3 at 0.01 |
| PSFCH power | SNR-based PSFCH decoding (1 PRB, format 0) at power P | 23, 10, 0 dBm | R6; R3 at 23 dBm |
| PSFCH period | PSFCH every N slots | 2, 4 | R3, R6 |

Error-free / period 1 = load campaign. R4 arms (no feedback) are drawn as horizontal lines at their error-free value.
9.1-9.3 use run-level PRR (untraced runs); 9.4 is traced.
''')
code(r'''
FB_REF = LOAD[LOAD.nbg.isin([0, 60])]
def fb_frame(issue, traced=False):
    x = R[R.fb_issue == issue]
    ref = FB_REF[FB_REF.arm.isin(['R3', 'R6'])]
    if traced:
        xw = W.merge(x[KEYC], on=KEYC)
        rr, rw = setcols(ref, WL.merge(ref[KEYC], on=KEYC), fb_issue=issue, fb_val=1.0 if issue == 'psfch_period' else 0.0)
        return pd.concat([x, rr]), pd.concat([xw, rw])
    rr, _ = setcols(runlevel(ref), None, fb_issue=issue, fb_val=99.0 if issue == 'psfch_power' else 0.0)
    return pd.concat([runlevel(x), rr]), None

def fb_section(issue, xlabel, xcat=False, xt=None, traced=False, ref_label='error-free'):
    r, w = fb_frame(issue, traced)
    p5 = 'runs'
    cls = None if traced else ['L', 'H']
    for nbg in (0, 60):
        s = H.summarize(r[r.nbg == nbg], w, ['arm', 'sc', 'tr', 'fb_val'], p5_from=p5)
        r4 = FB_REF[(FB_REF.nbg == nbg) & ~FB_REF.arm.isin(['R3', 'R6'])]
        ref = H.summarize(r4 if traced else runlevel(r4), WL if traced else None, ['arm', 'sc', 'tr'], p5_from=p5)
        kw = dict(xcat=xcat, xticklabels=xt, ref=ref, ref_label=ref_label)
        show(P.plot_x(s, 'fb_val', PRR, ALL, xlabel, f'Reliability vs {xlabel}, {nbg} background cars, all schemes', **kw),
             f'fb_{issue}_prr_all_n{nbg}')
        if traced:
            show(P.plot_x(s, 'fb_val', PIR, ALL, xlabel, f'PIR vs {xlabel}, {nbg} background cars', pct=False, **kw),
                 f'fb_{issue}_pir_n{nbg}')
        for nm, arms in (('A', SET_A), ('B', SET_B)):
            names = ', '.join(P.STYLE[a]['label'].split(' ')[0] for a in arms)
            show(P.plot_x(s, 'fb_val', PRR, arms, xlabel, f'{nbg} background cars: {names}', **kw), f'fb_{issue}_prr_{nm}_n{nbg}')
        show(P.plot_resources(s, 'fb_val', ['R3', 'R6'], xlabel, f'Subchannel-slots vs {xlabel}, {nbg} background cars',
                              xcat=True, xticklabels=xt, classes=cls), f'fb_{issue}_res_n{nbg}')
    return r, w
''')
md('### 9.1 PSFCH miss (DTX) probability')
code(r'''FB = {}; FB['psfch_miss'] = fb_section('psfch_miss', 'PSFCH miss probability')''')
md('### 9.2 NACK-to-ACK error probability')
code(r'''FB['psfch_n2a'] = fb_section('psfch_n2a', 'NACK->ACK probability', xcat=True)''')
md('### 9.3 PSFCH transmit power (SNR-based decoding)')
code(r'''FB['psfch_power'] = fb_section('psfch_power', 'PSFCH power', xcat=True, xt=['0 dBm', '10 dBm', '23 dBm', 'error-free'])''')
md('### 9.4 PSFCH period')
code(r'''FB['psfch_period'] = fb_section('psfch_period', 'PSFCH period (slots)', xcat=True, traced=True,
                                  ref_label='period 1, not simulated at 2 / 4')''')

# ================================================================================================ 9 gains
md(r'''
## 10. R6 gain tables
Cell = R6 - X. PRR in percentage points; resources = (R6 - X) / X of subch.-slots per kB generated, in %.
Green = R6 better (higher PRR, fewer resources). One colour scale per metric column. Rows: Highway, then both
roundabouts pooled; traffic; sweep value. Feedback tables: R4+C and R4+C+fb at their error-free value
(no feedback); R3 only where simulated.
''')
code(r'''
OTH = ['R3', 'R4_bsc', 'R4_bsc_fbs']
def table(summ, x, title, xlabel, xfmt='{:g}'):
    g = P.gains(summ, x, OTH)
    show(P.gain_heatmap(g, title, xlabel, xfmt=xfmt), 'gain_' + title.split(':')[0].replace(' ', '_'))
    return g

def fb_group(issue):
    r, w = FB[issue]
    traced = w is not None
    r4 = FB_REF[FB_REF.arm.isin(['R4_bsc', 'R4_bsc_fbs'])]
    r4 = r4 if traced else runlevel(r4)
    parts, wparts = [r], [w] if traced else []
    for v in sorted(r.fb_val.unique()):
        a, b = setcols(r4, WL.merge(r4[KEYC], on=KEYC) if traced else None, fb_issue=issue, fb_val=v)
        parts.append(a); wparts.append(b) if traced else None
    rr = pd.concat(parts)
    val = rr.fb_val.map(lambda v: 'error-free' if issue == 'psfch_power' and v == 99 else f'{v:g}')
    rr['x'] = rr.nbg.astype(str) + ' cars · ' + val
    ww = None
    if traced:
        ww = pd.concat(wparts)
        ww = ww.merge(rr[KEYC + ['x']].drop_duplicates(KEYC), on=KEYC)
    return H.summarize(rr, ww, ['arm', 'scg', 'tr', 'x'], p5_from='runs')
''')
md('### 10.1 Background vehicle density')
code(r'''G = {}; G['dens'] = table(S_LOAD_G, 'dens', 'Density: R6 - X (veh/km highway, veh/km² roundabouts)', '', '{:g}')''')
md('### 10.2 Helper SCI drop')
code(r'''G['hm_sci'] = table(S_HM_G['sci'], 'hm_p', 'Helper SCI drop: R6 - X', 'p=')''')
md('### 10.3 Helper payload drop')
code(r'''G['hm_data'] = table(S_HM_G['data'], 'hm_p', 'Helper payload drop: R6 - X', 'p=')''')
md('### 10.4 PSFCH processing delay\nRow label: background cars · delay (µs; 500 = one extra slot), processing gap 2 slots. 60 cars: aperiodic, 10 seeds per route.')
code(r'''G['tm_delay'] = table(tm_group('tm_delay', 2), 'x', 'PSFCH processing delay: R6 - X', '', '{}')''')
md('### 10.5 Processing gap\nRow label: background cars · gap (slots), processing delay 100 µs (load-campaign timing).')
code(r'''G['tm_gap'] = table(tm_group('tm_gap', 100), 'x', 'Processing gap: R6 - X', '', '{}')''')
for i, (iss, lab) in enumerate([('psfch_miss', 'PSFCH miss'), ('psfch_n2a', 'NACK->ACK'),
                                ('psfch_power', 'PSFCH power'), ('psfch_period', 'PSFCH period')]):
    md(f'### 10.{i + 6} {lab.split(" (")[0]}')
    code(f'''G['{iss}'] = table(fb_group('{iss}'), 'x', '{lab.split(" (")[0]}: R6 - X', '', '{{}}')''')

md('### 10.10 All gain tables as data')
code(r'''
GT = pd.concat({k: v for k, v in G.items()}, names=['table'])
GT.round(2)
''')

md(r"""
## 11. Paper figures
Written to `FIGS_PAPER` as PDF and PNG; `paper_skeleton/main.tex` includes them from there. Schemes: R3, R4+C,
R4+C+fb, R6. Error bars: 95 % t-CI over seeds (paired by seed for the fallback gain).
""")
code(r'''
FIGS_PAPER.mkdir(parents=True, exist_ok=True)
PAPER_ARMS = ['R3', 'R4_bsc', 'R4_bsc_fbs', 'R6']
def save_paper(fig, name):
    for ext in ('pdf', 'png'):
        fig.savefig(FIGS_PAPER / f'{name}.{ext}', dpi=300, bbox_inches='tight', pad_inches=0.02)
    plt.show()

save_paper(PP.fig_load(S_LOAD, 'prr_group', PAPER_ARMS, ylabel='group PRR (%)'), 'prr_vs_density')
save_paper(PP.fig_load(S_LOAD, 'prr_worst', PAPER_ARMS, width=PP.COL_W, height=4.6, ylabel='worst-member PRR (%)'),
           'worst_vs_density')
save_paper(PP.fig_fallback_gain(LOAD), 'fallback_gain_vs_density')
r3 = LOAD[(LOAD.arm == 'R3') & (LOAD.tr == 'periodic1tb') & (LOAD.nbg == 0)].groupby('sc').prr_group.mean()
save_paper(PP.fig_helper_miss(S_HM, r3), 'prr_vs_p')
sorted(p.name for p in FIGS_PAPER.iterdir())
''')

nb = nbf.v4.new_notebook()
nb['cells'] = cells
nb['metadata'] = {'kernelspec': {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}}
p = os.path.join(HERE, 'notebooks', 'SCHEMES_COMPARISON.ipynb')
nbf.write(nb, p)
print('wrote', p, len(cells), 'cells')
