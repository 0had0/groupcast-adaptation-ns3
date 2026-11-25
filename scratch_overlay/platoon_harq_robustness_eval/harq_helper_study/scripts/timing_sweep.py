"""Timing sweep: PSFCH processing delay x minimum processing gap, source Option 2 (R3) vs cooperative (R6).

Every run is the campaign-v3 command of the same arm, cell and seed (out/remote_v3/<arm>/<cell>.cmd.json, local
paths and build), plus three attributes:
  --ns3::NrUePhy::SlPsfchProcessingDelay   end of PSFCH -> feedback at the MAC (every UE; v3: one slot = 500 us)
  --ns3::NrSlUeMac::CoopActivationDelay    a decision acts only on slots starting at least this long later
                                           (source takeover / fallback / withholding a copy, helper relay gating)
  --ns3::NrSlUeMac::MinTimeGapProcessing   processing gap of the selection (slots; v3: 2, i.e. 5-slot spacing)

  python3 scripts/timing_sweep.py list  [--stage A|B]
  python3 scripts/timing_sweep.py run   [--stage A|B] [--jobs N] [--seeds 4-10]   # resumable: skips runs with a _run.csv
  python3 scripts/timing_sweep.py collect                             # -> out/timing/summary.csv
  python3 scripts/timing_sweep.py check [--jobs N]   # new build, no timing flags: must reproduce campaign v3 exactly

Stage A: 0 background vehicles, 3 routes x 2 traffic types, seeds 1-10 (as v3).
Stage B: 60 background vehicles, 3 routes, aperiodic traffic, seeds 1-3 (laptop); 4-10 added on ease-rtx (--seeds 4-10).
Templates: out/v3 (remote) or out/remote_v3 (laptop copy); the build suffix found on this machine is used.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import genie  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # harq_helper_study/ (this file: scripts/)
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
REMOTE_REPO = '/home/ease/Documents/groupcast-adaptation-ns3'
OUT = os.path.join(HERE, 'out', 'timing')
ARMS = ('R3', 'R6')
DELAYS_US = (100, 250, 500)
GAPS = (2, 3, 4)
ACTIVATION_US = 50
CELLS_A = [f'{sc}_ivd30_{tr}_none' for sc in ('hwy', 'rbt', 'rbtE') for tr in ('periodic1tb', 'aperiodic1tb')]
CELLS_B = [f'{sc}_ivd30_aperiodic1tb_moderate' for sc in ('hwy', 'rbt', 'rbtE')]
STAGES = {'A': (CELLS_A, tuple(range(1, 11))), 'B': (CELLS_B, (1, 2, 3))}
V3 = next(d for d in (os.path.join(HERE, 'out', 'v3'), os.path.join(HERE, 'out', 'remote_v3')) if os.path.isdir(d))
CHECK = [('R6', 'hwy_ivd30_aperiodic1tb_none', 1), ('R6', 'rbt_ivd30_periodic1tb_none', 1),
         ('R3', 'hwy_ivd30_aperiodic1tb_none', 1)]


def template(arm, cell, s):
    if genie.is_genie(arm):     # R6_genie: the R6 (Kuo) v3 command, selector replaced by the case's schedule
        return genie.to_genie(template(genie.base_arm(arm), cell, s), f'{cell}_s{s}')
    cmd = [x.replace(REMOTE_REPO, REPO) for x in json.load(open(os.path.join(V3, arm, f'{cell}_s{s}.cmd.json')))]
    for suffix in ('-optimized', '-default'):
        b = cmd[0].rsplit('-', 1)[0] + suffix
        if os.path.exists(b):
            cmd[0] = b
            break
    return [x for x in cmd if not x.startswith('--out=')]


def setting(d, g):
    return f'd{d}_g{g}'


def plan(stage, seeds=None):
    cells, seeds0 = STAGES[stage]
    seeds = seeds or seeds0
    runs = []
    for arm in ARMS:
        for d in DELAYS_US:
            for g in GAPS:
                for cell in cells:
                    for s in seeds:
                        out = os.path.join(OUT, f'{arm}_{setting(d, g)}', f'{cell}_s{s}')
                        cmd = template(arm, cell, s) + [
                            f'--ns3::NrUePhy::SlPsfchProcessingDelay={d}us',
                            f'--ns3::NrSlUeMac::CoopActivationDelay={ACTIVATION_US}us',
                            f'--ns3::NrSlUeMac::MinTimeGapProcessing={g}',
                            f'--out={out}']
                        runs.append(dict(arm=arm, delay_us=d, gap=g, cell=cell, seed=s, out=out, cmd=cmd))
    return runs


def run_one(r):
    if os.path.exists(r['out'] + '_run.csv'):
        return 0
    os.makedirs(os.path.dirname(r['out']), exist_ok=True)
    with open(r['out'] + '.log', 'w') as log:
        rc = subprocess.call(r['cmd'], stdout=log, stderr=subprocess.STDOUT, cwd=os.path.dirname(r['out']))
    print(('ok ' if rc == 0 else f'FAIL({rc}) ') + os.path.relpath(r['out'], OUT), flush=True)
    return rc


def check(jobs):
    """No timing flags: every column v3 has must be identical (wall time aside)."""
    runs = [dict(out=os.path.join(OUT, f'check_{a}', f'{c}_s{s}'), cmd=template(a, c, s)
                 + [f'--out={os.path.join(OUT, f"check_{a}", f"{c}_s{s}")}'], ref=os.path.join(V3, a, f'{c}_s{s}'))
            for a, c, s in CHECK]
    with ThreadPoolExecutor(jobs) as ex:
        list(ex.map(run_one, runs))
    bad = 0
    for r in runs:
        new = pd.read_csv(r['out'] + '_run.csv').iloc[0]
        ref = pd.read_csv(glob.glob(r['ref'] + '_run.csv*')[0]).iloc[0]
        diff = [c for c in ref.index if c != 'wall_s' and str(new.get(c)) != str(ref[c])]
        bad += bool(diff)
        print(os.path.relpath(r['out'], OUT), 'identical' if not diff else f'DIFFERS in {diff[:12]}')
    print('check', 'passed' if bad == 0 else 'FAILED')
    return 1 if bad else 0


def collect():
    rows = []
    for f in sorted(glob.glob(os.path.join(OUT, '*', '*_run.csv'))):
        key = os.path.basename(os.path.dirname(f))
        if key.startswith('check_'):
            continue
        arm, d, g = key.split('_')
        r = pd.read_csv(f).iloc[0].to_dict()
        rows.append(dict(arm=arm, delay_us=int(d[1:]), gap=int(g[1:]),
                         cell=os.path.basename(f)[:-len('_run.csv')]) | r)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'summary.csv'), index=False)
    print(len(df), 'runs ->', os.path.join(OUT, 'summary.csv'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=('list', 'run', 'collect', 'check'))
    ap.add_argument('--stage', default='A', choices=sorted(STAGES))
    ap.add_argument('--jobs', type=int, default=6)
    ap.add_argument('--shard', default='', help='i/n: run only every n-th run starting at the i-th (1-based), so '
                                                 'n jobs on n nodes split the sweep without overlap')
    ap.add_argument('--genie', action='store_true',
                    help='R6_genie only (genie helper: schedule replay, scripts/genie.py) into out/<sweep>_genie; '
                         'the R3 reference has no helper and is not re-run')
    ap.add_argument('--seeds', default='', help='e.g. 4-10 (default: the stage seeds)')
    a = ap.parse_args()
    if a.genie:
        global OUT, ARMS
        OUT, ARMS = OUT + '_genie', ('R6_genie',)
    if a.action == 'collect':
        return collect()
    if a.action == 'check':
        sys.exit(check(a.jobs))
    seeds = None
    if a.seeds:
        lo, _, hi = a.seeds.partition('-')
        seeds = tuple(range(int(lo), int(hi or lo) + 1))
    runs = plan(a.stage, seeds)
    if a.action == 'list':
        for r in runs:
            print(os.path.relpath(r['out'], OUT))
        print(len(runs), 'runs', file=sys.stderr)
        return
    if a.shard:
        i, n = (int(x) for x in a.shard.split('/'))
        runs = runs[i - 1::n]
        print(f'shard {i}/{n}: {len(runs)} runs', flush=True)
    with ThreadPoolExecutor(a.jobs) as ex:
        rcs = list(ex.map(run_one, runs))
    print('done', sum(rc == 0 for rc in rcs), 'ok,', sum(rc != 0 for rc in rcs), 'failed')


if __name__ == '__main__':
    main()
