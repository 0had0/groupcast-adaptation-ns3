"""Builds notebooks/SCHEMES_COMPARISON_map.ipynb: SCHEMES_COMPARISON.ipynb with the map helper selection
(--helperSelect=map, building map + exact CAM positions) in place of Kuo's selection.

Source: the cells of notebooks/SCHEMES_COMPARISON.ipynb (run scripts/build_schemes_notebook.py first), outputs cleared.
Changes:
  - paths cell: load campaign = R3 from out/remote_v3 (no helper, selection-independent) + R6_map from
    out/remote_v3_map, read as arm 'R6' (hhs_data spec 'rename') so every style / table works unchanged
  - arm lists cut to the arms that have map data (R4+C and R4+C+fb have none)
  - sections without map data (7 helper drop, 8 processing time, 9 feedback issues, their gain tables, the
    fallback and helper-drop paper figures) replaced by a note; section 12 lists the missing simulations

  python3 scripts/build_schemes_map_notebook.py
  python3 -m jupyter nbconvert --to notebook --execute --inplace notebooks/SCHEMES_COMPARISON_map.ipynb
"""
import os
import re
import nbformat as nbf

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, 'notebooks', 'SCHEMES_COMPARISON.ipynb')
DST = os.path.join(HERE, 'notebooks', 'SCHEMES_COMPARISON_map.ipynb')

PATHS = r'''
# ---- PATHS: edit this cell only when the notebook or the data moves -------------------------------------------
from pathlib import Path
STUDY = Path('/Users/hadih/Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study')
CODE  = STUDY / 'scripts'                     # hhs_data.py (loader), hhs_plot.py (styles, plots)
OUT   = STUDY / 'out'
CACHE = OUT / 'cache_schemes_map'             # parquet cache, one set per campaign; delete to re-read
FIGS  = STUDY / 'figures' / 'schemes_comparison_map'
SAVE_FIGS = False                             # True: every figure also written to FIGS as .png
FIGS_PAPER = STUDY / 'figures' / 'paper_schemes_map'   # section 11 writes the paper figures (.pdf + .png) here
JOBS = 6

# campaign key -> folder, kind, arms to load (folder names), rename (folder name -> arm used in the notebook).
# Map selection exists only for the load grid. R3 has no helper: the Kuo campaign's R3 is the R3 of this notebook.
CAMPAIGNS = {
    'load':     dict(path=OUT / 'remote_v3',     arms=['R3']),
    'load_map': dict(path=OUT / 'remote_v3_map', arms=['R6_map'], rename={'R6_map': 'R6'}),
}
'''

DATA_MD = r'''
# HARQ schemes comparison with the map helper selection: R3, R6 (map)

Copy of `SCHEMES_COMPARISON.ipynb` with the helper chosen by the **map selector** (`--helperSelect=map`: Kuo's
objective computed with the building map and exact CAM positions, a best case) instead of Kuo's selector. In this
notebook **R6 = R6 with map selection** (`R6_map`). R3 has no helper and is the same run as in the Kuo notebook.
Every path is in the cell above. Builder: `scripts/build_schemes_map_notebook.py` (from the cells of
`SCHEMES_COMPARISON.ipynb`).

**Only the load grid has map data.** R4+C and R4+C+fb were never run with the map selector, and none of the helper-drop,
processing-time or PSFCH sweeps were. Those sections are left as notes; section 12 lists the runs needed.

## 1. Data: folders, formats, origin
| key | folder | arms loaded | grid | command | run on | commit |
|---|---|---|---|---|---|---|
| `load` | `out/remote_v3` | R3 (folder also has R1, R1_bsc, R2, R4, R4_bsc, R4.1, R4.2, R5, R6 with Kuo) | 3 routes x 2 traffic x 5 loads (0/15/30/45/60 cars) x 10 seeds | `scripts/launch_v3.sh` -> `study.py run --profile v3 --jobs 32 --out out/v3` | ease-rtx, 32 cores; rsync to laptop | `32e29dd` |
| `load_map` | `out/remote_v3_map` | R6_map, read as R6 (folder also has R4_map, R4.1_map, R4.2_map, R5_map) | as `load` | `scripts/launch_v3.sh --map` (profile `v3_map`) | ease-rtx | `32e29dd` |

Every map command equals the Kuo command of the same cell and seed except `--helperSelect`, so map and Kuo runs pair
by seed.

**File names.** `{hwy|rbt|rbtE}_ivd30_{periodic1tb|aperiodic1tb}_{none|n15|n30|n45|moderate}_s{seed}_{kind}.csv[.gz]`.
`rbt` = roundabout W, `rbtE` = roundabout E, `moderate` = 60 background cars.

| file | rows | columns used here |
|---|---|---|
| `_run.csv` | 1 per run | `kpi_start/end` (3 / 52.7 s), `pkts`, `prr_mean`, `prr_min` (no deadline), `cbr_occupied` (SL-RP occupancy), `res_used_leader/helper` (subch.-slots), `subch_prb` |
| `_pkt.csv` | packet x member | `seq, t_tx, size, member, rx, delay_ms, dist_m` |
| `_ltx.csv` / `_htx.csv` | leader / helper transmission | `t, rb_len, tx_in_tb, origin` (0 own slot, 1 takeover after helper NACK, 2 fallback, helper silent) |
| `_hsel.csv` | helper selection | selected helper per decision (map runs) |
| `.cmd.json` | command | full ns-3 command line |
'''

MISSING_MD = r'''
## 12. Missing simulations (map helper selection)
Counts mirror the Kuo campaigns of `SCHEMES_COMPARISON.ipynb`. R3 never needs a re-run (no helper). Map arms need a
`--helperSelect=map` variant of each arm in the corresponding launcher.

| # | needed for | arm(s) with map | grid | runs | Kuo source | launcher change |
|---|---|---|---|---|---|---|
| 1 | sections 5, 6, 10.1, 11 (R4+C, R4+C+fb lines; fallback-gain figure) | `R4_bsc_map`, `R4_bsc_fbs_map` | load grid: 3 routes x 2 traffic x 5 loads x 10 seeds | 2 x 300 = **600** | `out/remote_v3/R4_bsc`, `out/remote_v3_fbs/R4_bsc_fbs` | `study.py`: add `R4_bsc`, `R4_bsc_fbs` to `MAP_ARMS` (profile `v3_map`, or a new profile with only these two) |
| 2 | section 7, 10.2-10.3, helper-drop paper figure | `R4_bsc_map` (data, sci), `R4_bsc_fbs_map` (data, sci), `R6_map` (data, sci) | 3 routes x periodic x 0 cars x p = 0.1/0.2/0.3/0.5/0.7 x 10 seeds | 6 x 150 = **900** | `out/local_helperrx/controlled`, `out/local_helperrx_fb/controlled` | `helper_rx_campaign/campaign.py`: no map option yet (add a `--set map` with `--helperSelect=map`) |
| 3 | section 8, 10.4-10.5 | `R6_map` | 9 (delay, gap) settings x (0 cars: 3 routes x 2 traffic x 10 seeds + 60 cars: 3 routes x aperiodic x 10 seeds) = 90 per setting | **810** (630 if (100 µs, 2) and (250 µs, 2) are taken from the load campaign, which they reproduce) | `out/timing/R6_d*_g*` | `timing_sweep.py`: add an `R6_map` arm |
| 4 | section 9.1-9.3, 10.6-10.8 | `R6_map` | miss 0.01/0.05/0.1/0.2/0.3, NACK->ACK 0.001/0.01/0.05, power 0/10/23 dBm (11 settings) x 3 routes x 2 traffic x 0 / 60 cars x 10 seeds | 11 x 120 = **1320** | `out/psfch_err/R6_*` | `psfch_err_sweep.py`: add an `R6_map` arm |
| 5 | section 9.4, 10.9 | `R6_map` | PSFCH period 2 / 4 x 3 routes x 2 traffic x 0 / 60 cars x 10 seeds | 2 x 120 = **240** | `out/remote_psfch/R6_p2`, `R6_p4` | `launch_v3.sh --psfch` / `study.py` profile `psfch`: add `R6_map` |

Total: 3870 runs (3690 with the timing reuse). Items 1 and 2 are the minimum for the four-scheme comparison of
sections 5-7; items 3-5 involve R6 only (R4+C / R4+C+fb have no feedback and are drawn at their error-free values).
'''


def src(c):
    return c.source if isinstance(c.source, str) else ''.join(c.source)


def main():
    nb = nbf.read(SRC, as_version=4)
    cells = nb.cells
    # split into top-level sections (markdown cells starting with '## ' or '# ')
    out, keep = [], True
    i = 0
    for c in cells:
        s = src(c)
        if c.cell_type == 'code':
            c.outputs, c.execution_count = [], None
        first = s.lstrip().splitlines()[0] if s.strip() else ''
        if i == 0:                                           # paths
            c.source = PATHS.strip()
        elif c.cell_type == 'markdown' and first.startswith('# HARQ schemes comparison'):
            c.source = DATA_MD.strip()
        m = re.match(r'#{2,3} (\d+)(?:\.(\d+))?', first) if c.cell_type == 'markdown' else None
        if m:
            sec, sub = int(m.group(1)), m.group(2) and int(m.group(2))
            if sec in (7, 8, 9):
                keep = False                                 # keep the section heading, replace its body
                if sub is None:
                    out.append(c)
                    out.append(nbf.v4.new_markdown_cell(
                        f'**Not available with the map selector**: no map runs for this sweep (section 12, item '
                        f'{ {7: 2, 8: 3, 9: "4-5"}[sec] }). The Kuo results are in `SCHEMES_COMPARISON.ipynb`.'))
                    i += 1
                    continue
            elif sec == 10:
                keep = sub in (None, 1, 10)
            elif sec in (5, 6) and sub in (2, 3):            # R3, R4+C(+fb), R6 subsets: same as 'all' here
                keep = False
            else:
                keep = True
        if keep:
            s = src(c)
            if c.cell_type == 'code' and s.lstrip().startswith("ALL   = ['R3'"):
                s += ("\n\n# map notebook: only the arms that have map data\n"
                      "_have = set(R.arm)\n"
                      "ALL, SET_A, SET_B = ([a for a in A if a in _have] for A in (ALL, SET_A, SET_B))\n"
                      "print('arms with map data:', ALL)")
            if c.cell_type == 'code' and "OTH = ['R3', 'R4_bsc', 'R4_bsc_fbs']" in s:
                s = s.replace("OTH = ['R3', 'R4_bsc', 'R4_bsc_fbs']",
                              "OTH = [a for a in ['R3', 'R4_bsc', 'R4_bsc_fbs'] if a in set(R.arm)]")
            if c.cell_type == 'markdown' and first.startswith('## 10.'):
                s += ('\n\nMap notebook: only R6 (map) - R3 over density; the helper-drop, timing and feedback '
                      'tables need the runs of section 12.')
            if c.cell_type == 'code' and 'PAPER_ARMS' in s:
                s = s.replace("PAPER_ARMS = ['R3', 'R4_bsc', 'R4_bsc_fbs', 'R6']", 'PAPER_ARMS = ALL')
                s = '\n'.join(l for l in s.splitlines()
                              if not any(k in l for k in ('fig_fallback_gain', 'fig_helper_miss', 'r3 = LOAD[')))
                s = s.replace("save_paper(PP.fig_load(S_LOAD, 'prr_group'",
                              "# fallback-gain and helper-drop figures need section 12 items 1-2\n"
                              "save_paper(PP.fig_load(S_LOAD, 'prr_group'")
            if c.cell_type == 'markdown' and first.startswith('## 11.'):
                s = s.replace('Schemes: R3, R4+C,\nR4+C+fb, R6.', 'Schemes: R3, R6 (map).')
            c.source = s
            out.append(c)
        i += 1
    out.append(nbf.v4.new_markdown_cell(MISSING_MD.strip()))
    nb.cells = out
    nbf.write(nb, DST)
    print('wrote', DST, len(out), 'cells')


if __name__ == '__main__':
    main()
