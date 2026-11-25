#!/usr/bin/env python3
"""Genie (oracle) helper schedule from a probe run: offline, with the whole window in hindsight.

Input: <tag>_links.csv of a probe run (simulator --linkProbeMs): per window (default 1 s) and platoon link
(leader 0, members 1..M; one row per unordered pair), the mean large-scale loss the channel computed for it
(path loss of the link's condition + shadowing + NLOSv blockage; no fast fading, no interference, no
collisions).

Per window:
  worst receiver  w = the member with the highest mean loss from the leader (lowest SNR)
  helper          h = argmin over the members h != w of max(loss(leader, h), loss(h, w)):
                  the best two-hop path leader -> h -> w, judged by its weaker hop (bottleneck)
Ties go to the lower leader -> h loss, then to the lower index.

Output: <tag>_genie.csv next to the input (or in --out): t0, window_s, worst, helper, bottleneck_db
(the helper path's weaker hop), direct_db (leader -> w), gain_db = direct_db - bottleneck_db, runner_up,
margin_db (runner-up bottleneck - helper bottleneck). The simulator replays it with
--helperSelect=schedule --helperSchedule=<tag>_genie.csv.

  python3 scripts/genie_schedule.py out/v3_genie_probe/R3_probe
  python3 scripts/genie_schedule.py PROBE_A --compare PROBE_B    # does the schedule carry over between runs?

--compare: the same tags probed in a second run (e.g. another arm): per window, whether the two schedules
pick the same helper, and the regret of A's helper on B's channel (A's helper's bottleneck in B - B's best).
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd


def read_links(path):
    d = pd.read_csv(path)
    need = {'t0', 'window_s', 'a', 'b', 'n', 'loss_db'}
    if not need <= set(d.columns):
        raise SystemExit(f'{path}: missing columns {sorted(need - set(d.columns))}')
    return d


def windows(links, min_frac=0.5):
    """{t0: (window_s, M, loss matrix (M + 1) x (M + 1))} for the windows where every link was probed at least
    min_frac of the window's largest sample count (drops the partial first / last windows)."""
    out = {}
    m = int(links[['a', 'b']].max().max())
    for t0, g in links.groupby('t0'):
        if len(g) < m * (m + 1) // 2 or g.n.min() < min_frac * g.n.max():
            continue
        L = np.full((m + 1, m + 1), np.nan)
        L[g.a.values, g.b.values] = g.loss_db.values
        L[g.b.values, g.a.values] = g.loss_db.values
        out[float(t0)] = (float(g.window_s.iloc[0]), m, L)
    return out


def choose(L, m):
    """(worst, helper, bottleneck, direct, runner_up, runner_bottleneck) for one window's loss matrix."""
    members = np.arange(1, m + 1)
    w = int(members[np.argmax(L[0, members])])
    cand = []
    for h in members:
        if h == w:
            continue
        cand.append((max(L[0, h], L[h, w]), L[0, h], int(h)))
    cand.sort()
    (b1, _, h1), (b2, _, h2) = cand[0], cand[1]
    return w, h1, b1, L[0, w], h2, b2


def schedule(links):
    rows = []
    for t0, (ws, m, L) in sorted(windows(links).items()):
        w, h, b, direct, h2, b2 = choose(L, m)
        rows.append(dict(t0=t0, window_s=ws, worst=w, helper=h, bottleneck_db=round(b, 3),
                         direct_db=round(direct, 3), gain_db=round(direct - b, 3), runner_up=h2,
                         margin_db=round(b2 - b, 3)))
    return pd.DataFrame(rows)


def compare(dir_a, dir_b):
    rows = []
    for pa in sorted(glob.glob(os.path.join(dir_a, '*_links.csv'))):
        tag = os.path.basename(pa)[:-len('_links.csv')]
        pb = os.path.join(dir_b, tag + '_links.csv')
        if not os.path.exists(pb):
            continue
        wa, wb = windows(read_links(pa)), windows(read_links(pb))
        for t0 in sorted(set(wa) & set(wb)):
            (_, m, La), (_, _, Lb) = wa[t0], wb[t0]
            a, b = choose(La, m), choose(Lb, m)
            wB, hB, bB = b[0], b[1], b[2]
            hA = a[1] if a[1] != wB else a[4]           # A's helper, on B's worst receiver
            rows.append(dict(tag=tag, t0=t0, same_worst=a[0] == wB, same_helper=a[1] == hB,
                             regret_db=max(Lb[0, hA], Lb[hA, wB]) - bB))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('probe_dir', help='folder with <tag>_links.csv of a probe run')
    ap.add_argument('--out', default=None, help='output folder (default: probe_dir)')
    ap.add_argument('--compare', default=None, help='second probe folder (same tags): schedule carry-over check')
    a = ap.parse_args()
    if a.compare:
        c = compare(a.probe_dir, a.compare)
        if c.empty:
            raise SystemExit('no common tags / windows')
        print(c.groupby('tag').agg(windows=('t0', 'size'), same_worst=('same_worst', 'mean'),
                                   same_helper=('same_helper', 'mean'), regret_mean_db=('regret_db', 'mean'),
                                   regret_max_db=('regret_db', 'max')).round(3).to_string())
        print(f'\nall: {len(c)} windows, same worst {c.same_worst.mean():.3f}, same helper {c.same_helper.mean():.3f},'
              f' regret mean {c.regret_db.mean():.3f} dB, P95 {c.regret_db.quantile(0.95):.3f} dB')
        return
    out = a.out or a.probe_dir
    os.makedirs(out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(a.probe_dir, '*_links.csv')))
    if not files:
        raise SystemExit(f'no *_links.csv in {a.probe_dir}')
    for p in files:
        s = schedule(read_links(p))
        tag = os.path.basename(p)[:-len('_links.csv')]
        if s.empty:
            print(f'{tag}: no complete window', file=sys.stderr)
            continue
        s.to_csv(os.path.join(out, tag + '_genie.csv'), index=False)
        print(f'{tag}: {len(s)} windows, helpers {s.helper.value_counts().sort_index().to_dict()}, '
              f'gain over direct {s.gain_db.mean():.1f} dB')


if __name__ == '__main__':
    main()
