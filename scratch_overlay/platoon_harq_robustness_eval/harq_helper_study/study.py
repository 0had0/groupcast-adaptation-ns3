#!/usr/bin/env python3
"""HARQ x Kuo-helper study: leader-only vs Kuo helper (blind / HARQ) vs Kuo helper with takeover.

Arms (all --maxNumTx=3, helper arms --helperSched=S1 --helperSelect=kuo):
  R1   leader blind (copies decoded independently)      R2 / R3   leader HARQ Option 1 / 2
  R4   Kuo helper, blind (Kuo's scheme)                  R4.1 / R4.2  Kuo helper, HARQ Opt 1 / 2, no takeover
  R5 / R6  Kuo helper, HARQ Opt 1 / 2, helper NACK takeover + leader fallback (G08)
  *_noIR   R2, R3, R4.1, R4.2 with --softCombining=false (HARQ-IR ablation)
  *_bsc    R1, R4 with --blindSoftCombining=true (receivers combine the blind copies of a TB, 3GPP-allowed;
           R4_bsc: leader and helper copies, HARQ owner = SCI-2A relayedFromRnti)
Cells: {highway, roundabout (N->W, 1/4 ring), roundaboutE (N->E, 3/4 ring; full profile)} ivd 30 x bg {none, moderate} x {periodic1tb, aperiodic1tb}.

Usage:
  study.py run   --profile smoke|local1|full [--jobs N] [--seeds 1-5] [--out DIR]
  study.py check --profile smoke [--out DIR]      # invariants of finished runs (smoke tests)
  study.py list  --profile ...                    # print the planned runs
"""
import argparse, csv, gzip, json, os, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.dirname(HERE)                       # scratch_overlay/platoon_harq_robustness_eval
REPO = os.path.abspath(os.path.join(EVAL, '..', '..'))
_BDIR = os.path.join(REPO, 'ns-3-dev', 'build', 'scratch', 'platoon_harq_robustness_eval')
# STUDY_BIN overrides; otherwise the optimized build if present, else the default build
BIN = os.environ.get('STUDY_BIN') or next(
    (os.path.join(_BDIR, f'ns3.42-platoon_harq_robustness_eval-{p}') for p in ('optimized', 'default')
     if os.path.exists(os.path.join(_BDIR, f'ns3.42-platoon_harq_robustness_eval-{p}'))),
    os.path.join(_BDIR, 'ns3.42-platoon_harq_robustness_eval-default'))
sys.path.insert(0, EVAL)
import run_retx  # noqa: E402  (BASE_FLAGS and background levels of the campaign)

KUO = ['--helperSched=S1', '--helperSelect=kuo', '--helper=4']
ARMS = {
    'R1': ['--retx=R1'],
    'R2': ['--retx=R2'],
    'R3': ['--retx=R3'],
    'R4': ['--retx=R4'] + KUO,
    'R4.1': ['--retx=R4.1'] + KUO,
    'R4.2': ['--retx=R4.2'] + KUO,
    'R5': ['--retx=R5'] + KUO,
    'R6': ['--retx=R6'] + KUO,
    'R2_noIR': ['--retx=R2', '--softCombining=false'],
    'R3_noIR': ['--retx=R3', '--softCombining=false'],
    'R4.1_noIR': ['--retx=R4.1', '--softCombining=false'] + KUO,
    'R4.2_noIR': ['--retx=R4.2', '--softCombining=false'] + KUO,
    'R1_bsc': ['--retx=R1', '--blindSoftCombining=true'],
    'R4_bsc': ['--retx=R4'] + KUO + ['--blindSoftCombining=true'],
}
FAMILY = {'R1': 'leader', 'R2': 'leader', 'R3': 'leader', 'R4': 'kuo', 'R4.1': 'kuo', 'R4.2': 'kuo',
          'R5': 'kuo+takeover', 'R6': 'kuo+takeover', 'R1_bsc': 'leader', 'R4_bsc': 'kuo'}
DIAG = ['--writeAtt=true', '--writeAllTx=true', '--writeGrants=true']


def profile_spec(profile, seeds_arg=None):
    """cells, seeds and simulated time (s; 0 = whole trace) per (scenario, bg)."""
    if profile == 'smoke':
        return dict(scen=['highway'], bgs=['none'], trafs=['periodic1tb'], seeds=[1],
                    tmax=lambda sc, bg: 8.0)
    if profile == 'local1':
        # one seed; background cells shortened to keep the laptop run to a few hours
        return dict(scen=['highway', 'roundabout'], bgs=['none', 'moderate'],
                    trafs=['periodic1tb', 'aperiodic1tb'], seeds=[1],
                    tmax=lambda sc, bg: (20.0 if bg != 'none' else (53.0 if sc == 'highway' else 0.0)))
    if profile == 'full':
        seeds = parse_seeds(seeds_arg or '1-5')
        return dict(scen=['highway', 'roundabout', 'roundaboutE'], bgs=['none', 'moderate'],
                    trafs=['periodic1tb', 'aperiodic1tb'], seeds=seeds,
                    tmax=lambda sc, bg: 53.0 if sc == 'highway' else 0.0,
                    rbt_curve=True)   # roundabout KPI window = the curve only (curve_window)
    if profile == 'density':
        # sensitivity: background density at the moderate per-car load (1.67 flows / car), arms
        # R1 / R3 / R6, then R4 / R4.2 / R1_bsc. 0 and 60 cars are the full profile's none / moderate
        # cells (not re-run).
        seeds = parse_seeds(seeds_arg or '1-10')
        flows = run_retx.get_bg_levels()['moderate'][1]
        return dict(scen=['highway', 'roundabout', 'roundaboutE'], bgs=list(DENSITY),
                    trafs=['periodic1tb', 'aperiodic1tb'], seeds=seeds,
                    tmax=lambda sc, bg: 53.0 if sc == 'highway' else 0.0, rbt_curve=True,
                    arms=['R1', 'R3', 'R6', 'R4', 'R4.2', 'R1_bsc'],
                    bglevels={k: (n, flows) for k, n in DENSITY.items()})
    if profile == 'density_smoke':
        # launcher check (launch_density.sh --smoke): R6, 15 cars, seed 1, every scenario x traffic, short runs
        # (highway 8 s; roundabout: the first SMOKE_RBT_S s of the curve window)
        flows = run_retx.get_bg_levels()['moderate'][1]
        return dict(scen=['highway', 'roundabout', 'roundaboutE'], bgs=['n15'],
                    trafs=['periodic1tb', 'aperiodic1tb'], seeds=[1],
                    tmax=lambda sc, bg: 8.0 if sc == 'highway' else 0.0, rbt_curve=True, rbt_smoke=SMOKE_RBT_S,
                    arms=['R6'], bglevels={'n15': (15, flows)})
    raise SystemExit('unknown profile ' + profile)


SMOKE_RBT_S = 6.0   # density_smoke: roundabout run length after the curve-window start (s)
DENSITY = {'n15': 15, 'n30': 30, 'n45': 45}   # density profile: bg name -> cars in the area
CURVE_MARGIN = 1.0   # s kept before the first / after the last bent link
TAIL_GUARD = 0.3     # simulator --tailGuard default: kpiEnd = maxTime - tailGuard
_CURVE = {}


def curve_window(trace, gap=35.0, tol=0.5, members=8):
    """(kpiStart, maxTime) covering the roundabout curve only: from the first sample where
    any leader-member link bends (straight distance < arc gap member * gap - tol, i.e. the
    leader enters the ring) to the last one (the tail leaves the curve), +/- CURVE_MARGIN."""
    if trace not in _CURVE:
        first = last = None
        with gzip.open(trace, 'rt') as f:
            pos = {}
            for line in f:
                if not line[:1].isdigit():
                    continue
                t, n, x, y = line.split(',')[:4]
                n = int(n)
                if n > members:
                    continue
                t = float(t)
                pos.setdefault(t, {})[n] = (float(x), float(y))
        for t in sorted(pos):
            p = pos[t]
            if len(p) <= members or 0 not in p:
                continue
            x0, y0 = p[0]
            if any(((p[m][0] - x0) ** 2 + (p[m][1] - y0) ** 2) ** .5 < m * gap - tol
                   for m in range(1, members + 1)):
                first = t if first is None else first
                last = t
        if first is None:
            raise SystemExit(f'no curve in {trace}')
        _CURVE[trace] = (round(max(first - CURVE_MARGIN, 3.0), 1),
                         round(last + CURVE_MARGIN + TAIL_GUARD, 1))
    return _CURVE[trace]


# study scenario -> (trace / tag prefix, simulator --scenario)
SCEN = {'highway': ('hwy', 'highway'), 'roundabout': ('rbt', 'roundabout'),
        'roundaboutE': ('rbtE', 'roundabout')}   # rbtE traces: gen_mobility.py --rbt-exit E


def parse_seeds(s):
    if '-' in s:
        a, b = s.split('-')
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in s.split(',')]


def plan(profile, seeds_arg=None, only=None):
    import re
    spec = profile_spec(profile, seeds_arg)
    bgl = spec.get('bglevels') or run_retx.get_bg_levels()
    runs = []
    for arm, aflags in ARMS.items():
        if 'arms' in spec and arm not in spec['arms']:
            continue
        for sc in spec['scen']:
            for bg in spec['bgs']:
                for tr in spec['trafs']:
                    for seed in spec['seeds']:
                        short, simsc = SCEN[sc]
                        nbg = bgl[bg][0]
                        trace = os.path.join(EVAL, 'traces', f'{short}_ivd30_n{nbg}_s{seed}.csv.gz')
                        tag = f'{short}_ivd30_{tr}_{bg}_s{seed}'
                        cmd = [BIN, f'--scenario={simsc}', f'--mobility={trace}', f'--traffic={tr}',
                               f'--seed={seed}', f'--run={seed}', f'--bgFlowsPerCar={bgl[bg][1]}',
                               '--maxNumTx=3'] + run_retx.BASE_FLAGS + aflags + DIAG
                        if tr == 'aperiodic1tb':
                            cmd.append('--aperiodicRri=20')
                        if simsc == 'roundabout':
                            cmd.append(f'--sumoNet={EVAL}/sumo/roundabout/roundabout.net.xml')
                        t = spec['tmax'](sc, bg)
                        if simsc == 'roundabout' and spec.get('rbt_curve') and os.path.exists(trace):
                            k0, t = curve_window(trace)
                            if spec.get('rbt_smoke'):
                                t = round(k0 + spec['rbt_smoke'], 1)
                            cmd.append(f'--kpiStart={k0}')
                        if t > 0:
                            cmd.append(f'--maxTime={t}')
                        runs.append(dict(arm=arm, scenario=sc, bg=bg, traffic=tr, seed=seed, tag=tag,
                                         trace=trace, cmd=cmd))
    if only:
        runs = [r for r in runs if re.search(only, f"{r['arm']}/{r['tag']}")]
    return runs


def done(prefix, cmd):
    """A run is complete when its _run.csv exists and was made by the same command."""
    meta = prefix + '.cmd.json'
    have = os.path.exists(prefix + '_run.csv') or os.path.exists(prefix + '_run.csv.gz')
    return have and os.path.exists(meta) and json.load(open(meta)) == cmd


def run_one(r, outdir):
    d = os.path.join(outdir, r['arm'])
    os.makedirs(d, exist_ok=True)
    prefix = os.path.join(d, r['tag'])
    cmd = r['cmd'] + [f'--out={prefix}']
    if done(prefix, cmd):
        return r, 0, 0.0, 'skip'
    if not os.path.exists(r['trace']):
        return r, -1, 0.0, 'missing trace'
    env = {k: v for k, v in os.environ.items() if k not in ('MAGIC_HELPER_ID', 'NUM_MEMBERS')}
    t0 = time.time()
    with open(prefix + '.log', 'w') as lg:
        rc = subprocess.call(cmd, cwd=d, stdout=lg, stderr=subprocess.STDOUT, env=env)
    wall = time.time() - t0
    if rc == 0:
        for suf in ('_pkt.csv', '_att.csv', '_alltx.csv', '_coll.csv', '_ltx.csv', '_htx.csv', '_nack.csv',
                    '_grants.csv', '_hsel.csv'):
            p = prefix + suf
            if os.path.exists(p) and os.path.getsize(p) > 1 << 20:
                with open(p, 'rb') as fi, gzip.open(p + '.gz', 'wb') as fo:
                    shutil.copyfileobj(fi, fo)
                os.remove(p)
        json.dump(cmd, open(prefix + '.cmd.json', 'w'))
    for junk in ('NrDlMacStats.txt', 'NrUlMacStats.txt'):
        if os.path.exists(os.path.join(d, junk)):
            os.remove(os.path.join(d, junk))
    return r, rc, wall, 'ran'


def cmd_run(a):
    # absolute: every simulation runs with its arm folder as working directory
    outdir = os.path.abspath(a.out) if a.out else os.path.join(HERE, 'out', a.profile)
    os.makedirs(outdir, exist_ok=True)
    if not os.path.exists(BIN):
        raise SystemExit('simulator not built: ' + BIN)
    runs = plan(a.profile, a.seeds, a.only)
    print(f'[{a.profile}] {len(runs)} runs -> {outdir} (jobs {a.jobs}) bin {os.path.basename(BIN)}', flush=True)
    t0 = time.time()
    rows, n = [], 0
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = [ex.submit(run_one, r, outdir) for r in runs]
        for f in as_completed(futs):
            r, rc, wall, how = f.result()
            n += 1
            rows.append(dict(arm=r['arm'], scenario=r['scenario'], bg=r['bg'], traffic=r['traffic'],
                             seed=r['seed'], tag=r['tag'], exit=rc, wall_s=round(wall, 1), how=how))
            el = time.time() - t0
            print(f'[{n}/{len(runs)}] {how:4} exit {rc:3} {wall:7.1f}s {r["arm"]:9} {r["tag"]}  '
                  f'(elapsed {el/60:.1f} min)', flush=True)
    with open(os.path.join(outdir, 'manifest.csv'), 'w', newline='') as fo:
        w = csv.DictWriter(fo, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: (x['arm'], x['tag'])))
    bad = [x for x in rows if x['exit'] != 0]
    print(f'done: {len(rows) - len(bad)} ok, {len(bad)} failed', flush=True)
    return 1 if bad else 0


def read_csv(p):
    p = p if os.path.exists(p) else p + '.gz'
    with (gzip.open(p, 'rt') if p.endswith('.gz') else open(p)) as f:
        return list(csv.DictReader(f))


def cmd_check(a):
    """Smoke-test invariants of every finished run of the profile."""
    outdir = os.path.abspath(a.out) if a.out else os.path.join(HERE, 'out', a.profile)
    fails = []

    def chk(cond, msg):
        if not cond:
            fails.append(msg)

    for r in plan(a.profile, a.seeds, a.only):
        pre = os.path.join(outdir, r['arm'], r['tag'])
        name = f"{r['arm']}/{r['tag']}"
        if not (os.path.exists(pre + '_run.csv') or os.path.exists(pre + '_run.csv.gz')):
            fails.append(name + ': no _run.csv')
            continue
        x = read_csv(pre + '_run.csv')[0]
        arm = r['arm']
        base = arm.replace('_noIR', '').replace('_bsc', '')
        f = lambda k: float(x[k])
        chk(0.0 <= f('prr_mean') <= 1.0 and 0.0 <= f('prr_min') <= f('prr_mean') + 1e-9, name + ': PRR range')
        chk(x['blind_soft_combining'] == ('1' if arm.endswith('_bsc') else '0'), name + ': blind_soft_combining flag')
        chk(x['soft_combining'] == ('0' if arm.endswith('_noIR') else '1'), name + ': soft_combining flag')
        chk(x['psfch_exact_attr'] == '1', name + ': exact PSFCH attribution')
        takeover = base in ('R5', 'R6')
        chk(x['leader_fallback'] == ('1' if takeover else '0'), name + ': leader_fallback flag')
        chk(x['opt1_helper_nack'] == ('1' if base == 'R5' else '0'), name + ': opt1_helper_nack flag')
        if base in ('R1', 'R2', 'R3'):
            chk(f('helper_tx') == 0 and f('res_used_helper') == 0, name + ': leader-only arm has helper copies')
        if base in ('R4', 'R4.1', 'R4.2'):
            chk(f('helper_reclaim') == 0 and f('fallback_tx') == 0, name + ': no takeover expected')
        if base in ('R4', 'R4.1', 'R4.2', 'R5', 'R6'):
            chk(f('relay_by_unchosen') == 0, name + ': relay by a non-selected member')
            chk(f('relay_without_decode') == 0, name + ': relay without decode')
        if base in ('R2', 'R4.1', 'R5'):
            chk(f('explicit_ack_rx') == 0, name + ': Option 1 member sent an ACK')
        if base == 'R2':
            chk(f('leader_retx_without_nack') == 0, name + ': Option 1 retx without NACK')
        if takeover:
            chk(f('fallback_heard') + f('fallback_tx') + f('fallback_no_slot') <= f('fallback_check'),
                name + ': fallback accounting')
        if base == 'R6':
            chk(f('helper_ack_rx') == 0, name + ': silent helper ACKed')
        m = read_csv(pre + '_member.csv')
        for row in m:
            if row['member'] != 'all':
                chk(int(row['rx']) <= int(row['sent']), name + ': rx > sent')
        ltx = read_csv(pre + '_ltx.csv')
        chk('origin' in ltx[0], name + ': _ltx origin column')
        if not takeover:
            chk(all(t['origin'] == '0' for t in ltx), name + ': takeover origin in a no-takeover arm')
    for msg in fails:
        print('FAIL', msg)
    print(f'smoke check: {len(fails)} failure(s)')
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('action', choices=['run', 'check', 'list'])
    ap.add_argument('--profile', default='smoke', choices=['smoke', 'local1', 'full', 'density', 'density_smoke'])
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--seeds', default=None, help='full / density profile seeds, e.g. 1-5 or 1,2,3')
    ap.add_argument('--out', default=None)
    ap.add_argument('--only', default=None,
                    help='regex on "arm/tag", e.g. "^R6/hwy_ivd30_periodic1tb_moderate_s1$"')
    ap.add_argument('--bin', default=None, help='simulator binary (default: optimized build if present)')
    a = ap.parse_args()
    if a.bin:
        global BIN
        BIN = a.bin
    if a.action == 'list':
        for r in plan(a.profile, a.seeds, a.only):
            print(r['arm'], r['tag'])
        return 0
    return cmd_run(a) if a.action == 'run' else cmd_check(a)


if __name__ == '__main__':
    sys.exit(main())
