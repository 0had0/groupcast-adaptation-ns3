"""PSFCH reception errors: sensitivity of the cooperative protocol (R6) to feedback that is lost or misread.

Every run is the campaign-v3 command of the same arm, cell and seed (out/v3/<arm>/<cell>_s<seed>.cmd.json, local
paths and build) plus NrSpectrumPhy attributes (patches/nr/local.patch, default off = stock error-free PSFCH):
  --ns3::NrSpectrumPhy::SlPsfchErrorMode      1 forced, 2 SNR-based: the PSFCH at its 3GPP power on one PRB
                                              (format 0, TS 38.211 8.3.4; P - 10 log10 N_Tx,PSFCH, TS 38.213
                                              16.2.3) decoded like a PSCCH of the same link (NrEesmIrT1, MCS 0),
                                              over noise; other PSFCH do not interfere (orthogonal resources)
  --ns3::NrSpectrumPhy::SlPsfchTxPowerDbm     mode 2: PSFCH power P (23 dBm = P_CMAX, out of coverage)
  --ns3::NrSpectrumPhy::SlPsfchMissProb       mode 1: each received ACK/NACK is lost (DTX), per receiver
  --ns3::NrSpectrumPhy::SlPsfchNackToAckProb  a received NACK is detected as ACK (the harmful error)
Every UE draws on its own stream, so the leader and the helper overhearing the members both see the errors.

  python3 scripts/psfch_err_sweep.py list
  python3 scripts/psfch_err_sweep.py check [--jobs N]  # new build, no PSFCH flags: must reproduce campaign v3
  python3 scripts/psfch_err_sweep.py run   [--jobs N]  # resumable: skips runs with a _run.csv
  python3 scripts/psfch_err_sweep.py collect           # -> out/psfch_err/summary.csv
  python3 scripts/psfch_err_sweep.py status            # progress: done / total per setting, failures, ETA

Settings (label: attributes)
  miss<p>  : mode 1, SlPsfchMissProb = p          p in 0.01 0.05 0.1 0.2 0.3            arm R6
  n2a<q>   : mode 1, SlPsfchNackToAckProb = q     q in 0.001 0.01 0.05                  arm R6
  snr_p<P> : mode 2, SlPsfchTxPowerDbm = P      P in 23 10 0 dBm (distance / blockage dependent)  arm R6
  (R3 at snr_p23, miss0.1 and n2a0.01, as the source Option 2 reference.)
Cells: 3 routes x 2 traffic types x 0 / 60 background vehicles, seeds 1-10 (as campaign v3).
The error-free reference is campaign v3 itself (same commands); `check` proves the new build reproduces it.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # harq_helper_study/ (this file: scripts/)
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
REMOTE_REPO = '/home/ease/Documents/groupcast-adaptation-ns3'
OUT = os.path.join(HERE, 'out', 'psfch_err')
PFX = '--ns3::NrSpectrumPhy::'
SETTINGS = {f'miss{p}': {'SlPsfchErrorMode': 1, 'SlPsfchMissProb': p} for p in (0.01, 0.05, 0.1, 0.2, 0.3)}
SETTINGS |= {f'n2a{q}': {'SlPsfchErrorMode': 1, 'SlPsfchNackToAckProb': q} for q in (0.001, 0.01, 0.05)}
SETTINGS |= {f'snr_p{p}': {'SlPsfchErrorMode': 2, 'SlPsfchTxPowerDbm': p} for p in (23, 10, 0)}
ARM_SETTINGS = {'R6': list(SETTINGS), 'R3': ['snr_p23', 'miss0.1', 'n2a0.01']}
CELLS = [f'{sc}_ivd30_{tr}_{bg}' for sc in ('hwy', 'rbt', 'rbtE') for tr in ('periodic1tb', 'aperiodic1tb')
         for bg in ('none', 'moderate')]
SEEDS = tuple(range(1, 11))
V3 = next(d for d in (os.path.join(HERE, 'out', 'v3'), os.path.join(HERE, 'out', 'remote_v3')) if os.path.isdir(d))
CHECK = [('R6', 'hwy_ivd30_aperiodic1tb_none', 1), ('R6', 'rbt_ivd30_periodic1tb_none', 1),
         ('R3', 'hwy_ivd30_aperiodic1tb_none', 1)]


def template(arm, cell, s):
    cmd = [x.replace(REMOTE_REPO, REPO) for x in json.load(open(os.path.join(V3, arm, f'{cell}_s{s}.cmd.json')))]
    for suffix in ('-optimized', '-default'):
        b = cmd[0].rsplit('-', 1)[0] + suffix
        if os.path.exists(b):
            cmd[0] = b
            break
    return [x for x in cmd if not x.startswith('--out=')]


def plan():
    runs = []
    for arm, labels in ARM_SETTINGS.items():
        for lab in labels:
            for cell in CELLS:
                for s in SEEDS:
                    out = os.path.join(OUT, f'{arm}_{lab}', f'{cell}_s{s}')
                    cmd = template(arm, cell, s) + [f'{PFX}{k}={v}' for k, v in SETTINGS[lab].items()] + [f'--out={out}']
                    runs.append(dict(arm=arm, setting=lab, cell=cell, seed=s, out=out, cmd=cmd))
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
    """No PSFCH flags: every column v3 has must be identical (wall time aside)."""
    runs = [dict(out=os.path.join(OUT, f'check_{a}', f'{c}_s{s}'), cmd=template(a, c, s)
                 + [f'--out={os.path.join(OUT, f"check_{a}", f"{c}_s{s}")}'], ref=os.path.join(V3, a, f'{c}_s{s}'))
            for a, c, s in CHECK]
    with ThreadPoolExecutor(jobs) as ex:
        list(ex.map(run_one, runs))
    bad = 0
    for r in runs:
        new = pd.read_csv(r['out'] + '_run.csv').iloc[0]
        ref = pd.read_csv(glob.glob(r['ref'] + '_run.csv*')[0]).iloc[0]
        diff = [c for c in ref.index if c not in ('wall_s', 'sel_cpu_us_mean') and str(new.get(c)) != str(ref[c])]
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
        arm, lab = key.split('_', 1)
        r = pd.read_csv(f).iloc[0].to_dict()
        rows.append(dict(arm=arm, setting=lab, cell=os.path.basename(f)[:-len('_run.csv')]) | r)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'summary.csv'), index=False)
    print(len(df), 'runs ->', os.path.join(OUT, 'summary.csv'))


def status():
    import time
    runs = plan()
    done = [r for r in runs if os.path.exists(r['out'] + '_run.csv')]
    log = os.path.join(HERE, 'out', 'psfch_err_run.log')   # nohup log of `run`: one ok / FAIL(rc) line per run
    failed = set()
    if os.path.exists(log):
        failed = {l.split(' ', 1)[1].strip() for l in open(log, errors='replace') if l.startswith('FAIL')}
    fails = [r for r in runs if os.path.relpath(r['out'], OUT) in failed and not os.path.exists(r['out'] + '_run.csv')]
    running = [r for r in runs if not os.path.exists(r['out'] + '_run.csv') and os.path.exists(r['out'] + '.log')
               and r not in fails]
    print(time.strftime('%F %T'), f'done {len(done)}/{len(runs)}  running {len(running)}  failed {len(fails)}')
    by = {}
    for r in runs:
        k = f"{r['arm']}_{r['setting']}"
        by.setdefault(k, [0, 0])[1] += 1
        by[k][0] += r in done
    print('  '.join(f'{k} {a}/{b}' for k, (a, b) in by.items()))
    if len(done) >= 2:
        t = sorted(os.path.getmtime(r['out'] + '_run.csv') for r in done)
        rate = (len(t) - 1) / max(t[-1] - t[0], 1)
        print(f'rate {rate * 3600:.0f} runs/h, ETA {(len(runs) - len(done)) / rate / 3600:.1f} h')
    for r in fails[:10]:
        print('FAILED', os.path.relpath(r['out'], OUT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=('list', 'run', 'collect', 'check', 'status'))
    ap.add_argument('--jobs', type=int, default=6)
    a = ap.parse_args()
    if a.action == 'collect':
        return collect()
    if a.action == 'status':
        return status()
    if a.action == 'check':
        sys.exit(check(a.jobs))
    runs = plan()
    if a.action == 'list':
        for r in runs:
            print(os.path.relpath(r['out'], OUT))
        print(len(runs), 'runs', file=sys.stderr)
        return
    with ThreadPoolExecutor(a.jobs) as ex:
        rcs = list(ex.map(run_one, runs))
    print('done', sum(rc == 0 for rc in rcs), 'ok,', sum(rc != 0 for rc in rcs), 'failed')


if __name__ == '__main__':
    main()
