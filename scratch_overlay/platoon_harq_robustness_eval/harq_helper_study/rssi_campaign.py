"""RSSI-gated fallback, 10-seed confirmation: R6 at 60 background vehicles, three routes x two traffic types,
seeds 1-10 (60 runs). Each run is the campaign-v3 command unchanged; the build since commit 40a5601 adds the
<out>_fbchk.csv log (no random draw: the outputs are otherwise those of v3). Analysis: rssi_threshold.py /
RSSI_FALLBACK.ipynb on the copied folder.

  python3 rssi_campaign.py list
  python3 rssi_campaign.py run [--jobs N]        # resumable: skips runs with a _run.csv
"""
import argparse
import os
from concurrent.futures import ThreadPoolExecutor

import timing_sweep as T

OUT = os.path.join(T.HERE, 'out', 'rssi10')
CELLS = [f'{sc}_ivd30_{tr}_moderate' for sc in ('hwy', 'rbt', 'rbtE') for tr in ('periodic1tb', 'aperiodic1tb')]


def plan():
    runs = []
    for cell in CELLS:
        for s in range(1, 11):
            out = os.path.join(OUT, f'{cell}_s{s}')
            runs.append(dict(out=out, cmd=T.template('R6', cell, s) + [f'--out={out}']))
    return runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=('list', 'run'))
    ap.add_argument('--jobs', type=int, default=32)
    a = ap.parse_args()
    runs = plan()
    if a.action == 'list':
        print('\n'.join(os.path.relpath(r['out'], OUT) for r in runs))
        print(len(runs), 'runs')
        return
    with ThreadPoolExecutor(a.jobs) as ex:
        rcs = list(ex.map(T.run_one, runs))
    print('done', sum(rc == 0 for rc in rcs), 'ok,', sum(rc != 0 for rc in rcs), 'failed')


if __name__ == '__main__':
    main()
