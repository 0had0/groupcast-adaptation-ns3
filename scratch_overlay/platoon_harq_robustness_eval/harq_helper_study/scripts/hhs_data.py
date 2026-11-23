"""Data layer of notebooks/SCHEMES_COMPARISON.ipynb: reads every campaign folder into three tables.

  runs  one row per simulation run: keys, sweep variables, reliability, PIR and resource metrics
  win   one row per (run, 2 s window): pairs, delivered pairs, worst-member PRR of the window
  dist  one row per (run, 2 s window, distance bin): pairs, delivered pairs

A run is "traced" when its _pkt / _ltx / _htx files exist (PRR from packets with the PDB, PIR, resource classes).
An "untraced" run has only _run.csv (campaign psfch_err): PRR = run.prr_mean / prr_min, resources = leader / helper.

Campaigns are described by a dict (see CAMPAIGNS_DEFAULT); every path comes from the notebook's first cell.
"""
import glob
import multiprocessing as mp
import os
import re
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

PDB_MS = {'periodic1tb': 100.0, 'aperiodic1tb': 50.0}
WIN_S = 2.0
DIST_BIN_M = 30.0
NBG = {'none': 0, 'n15': 15, 'n30': 30, 'n45': 45, 'moderate': 60}
SC_GROUP = {'hwy': 'Highway', 'rbt': 'Roundabouts', 'rbtE': 'Roundabouts'}
RES_CLASSES = ['L1', 'Lrep', 'H', 'T1', 'T2']          # traced runs
RES_CLASSES_RUN = ['L', 'H']                            # untraced runs (leader / helper only)
CELL_RE = re.compile(r'^(?P<sc>hwy|rbtE|rbt)_ivd30_(?P<tr>periodic1tb|aperiodic1tb)_(?P<bg>none|n15|n30|n45|moderate)'
                     r'(?:_p(?P<hp>[0-9.]+)_(?P<hm>data|sci))?_s(?P<seed>\d+)$')


def rd(p):
    for q in (p, p + '.gz'):
        if os.path.exists(q):
            return pd.read_csv(q)
    return None


# ------------------------------------------------------------------------------------------------ per run
def _pir(k):
    """Packet inter-reception gaps (ms) per member from delivered pairs."""
    d = k[k.deliv == 1].assign(t_rx=lambda x: x.t_tx * 1e3 + x.delay_ms).sort_values('t_rx')
    gaps = {m: np.diff(g.t_rx.values) for m, g in d.groupby('member')}
    gaps = {m: g for m, g in gaps.items() if len(g)}
    if not gaps:
        return dict(pir_mean=np.nan, pir_worst=np.nan, pir_p95=np.nan)
    allg = np.concatenate(list(gaps.values()))
    worst = max(gaps, key=lambda m: gaps[m].mean())
    return dict(pir_mean=allg.mean(), pir_worst=gaps[worst].mean(), pir_p95=np.percentile(gaps[worst], 95))


def run_tables(pre, keys):
    r = rd(pre + '_run.csv').iloc[0]
    k0, k1 = float(r.kpi_start), float(r.kpi_end)
    base = keys | dict(cbr=float(r.cbr_occupied), pkts=int(r.pkts),
                       prr_run_mean=float(r.prr_mean), prr_run_min=float(r.prr_min))
    k = rd(pre + '_pkt.csv')
    l = rd(pre + '_ltx.csv')
    if k is None or l is None:                       # untraced run
        row = base | dict(traced=0, prr_group=float(r.prr_mean), prr_worst=float(r.prr_min),
                          ss_L=float(r.res_used_leader), ss_H=float(r.res_used_helper), kB=np.nan,
                          pir_mean=np.nan, pir_worst=np.nan, pir_p95=np.nan)
        return dict(runs=pd.DataFrame([row]), win=None, dist=None)

    pdb = PDB_MS[keys['tr']]
    k = k[(k.t_tx >= k0) & (k.t_tx < k1)].copy()
    k['deliv'] = ((k.rx == 1) & (k.delay_ms <= pdb + 1e-6)).astype(int)
    k['w'] = np.floor((k.t_tx - k0) / WIN_S).astype(int)
    k['dbin'] = (np.floor(k.dist_m / DIST_BIN_M) * DIST_BIN_M + DIST_BIN_M / 2).astype(float)
    mem = k.groupby('member').deliv.mean()

    # resources: subchannel-slots (PSCCH + PSSCH) transmitted in the KPI window, by class
    h = rd(pre + '_htx.csv')
    win = lambda d: d[(d.t >= k0) & (d.t < k1)] if d is not None else None
    l, h = win(l), win(h)
    org = l.origin if 'origin' in l else pd.Series(0, index=l.index)
    cls = np.where(l.tx_in_tb == 1, 'L1', org.map({0: 'Lrep', 1: 'T1', 2: 'T2'}).fillna('Lrep'))
    ss = {f'ss_{c}': float(l.rb_len[cls == c].sum() / r.subch_prb) for c in ['L1', 'Lrep', 'T1', 'T2']}
    ss['ss_H'] = float(h.rb_len.sum() / r.subch_prb) if h is not None and len(h) else 0.0
    ss['ss_L'] = ss['ss_L1'] + ss['ss_Lrep'] + ss['ss_T1'] + ss['ss_T2']
    kB = k.drop_duplicates('seq')['size'].sum() / 1e3

    row = base | dict(traced=1, prr_group=float(k.deliv.mean()), prr_worst=float(mem.min()), kB=float(kB),
                      **ss, **_pir(k))
    wm = k.groupby(['w', 'member']).deliv.mean().groupby('w').min().rename('worst')
    w = k.groupby('w').agg(n=('deliv', 'size'), d=('deliv', 'sum')).join(wm).reset_index()
    w['t'] = k0 + (w.w + 0.5) * WIN_S
    dd = k.groupby(['w', 'dbin']).agg(n=('deliv', 'size'), d=('deliv', 'sum')).reset_index()
    for c, v in keys.items():
        w[c] = v
        dd[c] = v
    return dict(runs=pd.DataFrame([row]), win=w, dist=dd)


def _job(a):
    try:
        return run_tables(*a)
    except Exception as e:                           # keep going; report the run
        return dict(error=f'{a[0]}: {e!r}')


# ------------------------------------------------------------------------------------------------ campaigns
def _keys_from_name(pre):
    m = CELL_RE.match(os.path.basename(pre))
    if not m:
        return None
    g = m.groupdict()
    return dict(sc=g['sc'], tr=g['tr'], nbg=NBG[g['bg']], seed=int(g['seed']),
                hm_p=float(g['hp']) if g['hp'] else 0.0, hm_mode=g['hm'] or 'none')


def _arm_variant(dirname, kind):
    """Folder name -> (arm, variant columns) for each campaign kind."""
    if kind == 'psfch_err':                          # R6_miss0.1, R3_n2a0.01, R6_snr_p23
        arm, setting = dirname.split('_', 1)
        m = re.match(r'(miss|n2a|snr_p)([0-9.]+)$', setting)
        issue = {'miss': 'psfch_miss', 'n2a': 'psfch_n2a', 'snr_p': 'psfch_power'}[m.group(1)]
        return arm, dict(fb_issue=issue, fb_val=float(m.group(2)))
    if kind == 'psfch_period':                       # R6_p2, R4.2_p4
        arm, p = dirname.rsplit('_p', 1)
        return arm, dict(fb_issue='psfch_period', fb_val=float(p))
    return dirname, {}


def campaign_runs(name, spec):
    """(prefix, keys) of every run of one campaign spec: {path, kind, arms}."""
    out = []
    for d in sorted(glob.glob(os.path.join(spec['path'], '*/'))):
        dn = os.path.basename(d.rstrip('/'))
        if dn.startswith('check_'):
            continue
        try:
            arm, var = _arm_variant(dn, spec.get('kind', 'plain'))
        except Exception:
            continue
        if spec.get('arms') and arm not in spec['arms']:
            continue
        for p in sorted(glob.glob(os.path.join(d, '*_run.csv*'))):
            pre = p.split('_run.csv')[0]
            kk = _keys_from_name(pre)
            if kk is None:
                continue
            if spec.get('kind') == 'helper_miss' and kk['hm_mode'] == 'none':
                continue                             # p = 0 comes from the load campaigns
            if spec.get('nbg') is not None and kk['nbg'] not in spec['nbg']:
                continue
            out.append((pre, dict(campaign=name, arm=arm, **kk) | dict(fb_issue='none', fb_val=0.0) | var))
    return out


def load(campaigns, cache_dir, jobs=6, refresh=False):
    """Load every campaign (cached per campaign as parquet). Returns dict(runs, win, dist, errors)."""
    os.makedirs(cache_dir, exist_ok=True)
    res = {t: [] for t in ('runs', 'win', 'dist')}
    errors = []
    for name, spec in campaigns.items():
        if not os.path.isdir(spec['path']):
            print(f'skip {name}: no folder {spec["path"]}')
            continue
        todo = campaign_runs(name, spec)
        f = {t: os.path.join(cache_dir, f'{name}__{t}.parquet') for t in res}
        ok = not refresh and os.path.exists(f['runs']) and len(pd.read_parquet(f['runs'])) == len(todo)
        if not ok:
            with ProcessPoolExecutor(jobs, mp_context=mp.get_context('fork')) as ex:
                rr = list(ex.map(_job, todo, chunksize=4))
            errors += [x['error'] for x in rr if 'error' in x]
            rr = [x for x in rr if 'error' not in x]
            for t in res:
                parts = [x[t] for x in rr if x[t] is not None]
                (pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()).to_parquet(f[t])
            print(f'{name}: {len(rr)} runs read, {len(todo) - len(rr)} failed')
        for t in res:
            res[t].append(pd.read_parquet(f[t]))
    out = {t: pd.concat([x for x in v if len(x)], ignore_index=True) for t, v in res.items()}
    for t in ('runs', 'win', 'dist'):
        out[t]['scg'] = out[t].sc.map(SC_GROUP)
    r = out['runs']
    # untraced runs: kB of the same command in the reference load campaign (same traffic RNG)
    if (r.traced == 0).any():
        ref = r[(r.traced == 1) & (r.campaign.str.startswith('load')) & (r.fb_issue == 'none') & (r.hm_mode == 'none')]
        kb = ref.groupby(['arm', 'sc', 'tr', 'nbg', 'seed']).kB.first()
        idx = pd.MultiIndex.from_frame(r[['arm', 'sc', 'tr', 'nbg', 'seed']])
        r.loc[r.traced == 0, 'kB'] = kb.reindex(idx).values[(r.traced == 0).values]
    r['ss_data_ctrl'] = r[['ss_L', 'ss_H']].sum(axis=1)
    r['ss_kB'] = r.ss_data_ctrl / r.kB
    out['errors'] = errors
    return out


# ------------------------------------------------------------------------------------------------ metrics
def ci95(x):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if len(x) < 2:
        return 0.0
    from scipy import stats
    return float(stats.t.ppf(0.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x)))


def summarize(runs, win, by, p5_from='runs'):
    """Metrics per group `by` (list of columns).
    prr_group  mean over runs of the group PRR                       (+ ci)
    prr_worst  mean over runs of the worst-member PRR                (+ ci)
    prr_p5     5th percentile over runs of the worst-member PRR; p5_from='win': over (run x 2 s window)
    pir_*      mean over runs; ss_kB, ss_<class>_kB mean over runs
    """
    g = runs.groupby(by)
    out = g.agg(n_runs=('seed', 'size'), cbr=('cbr', 'mean'),
                prr_group=('prr_group', 'mean'), prr_worst=('prr_worst', 'mean'),
                pir_mean=('pir_mean', 'mean'), pir_worst=('pir_worst', 'mean'), pir_p95=('pir_p95', 'mean'),
                ss_kB=('ss_kB', 'mean'), ss_tot=('ss_data_ctrl', 'mean'), traced=('traced', 'min'))
    for m, c in (('prr_group',) * 2, ('prr_worst',) * 2, ('pir_mean',) * 2, ('pir_worst',) * 2,
                 ('pir_p95',) * 2, ('ss_kB',) * 2, ('ss_tot', 'ss_data_ctrl')):
        out[m + '_ci'] = g[c].apply(ci95)
    for c in RES_CLASSES + ['L']:
        col = f'ss_{c}'
        if col in runs:
            out[col + '_kB'] = (runs[col] / runs.kB).groupby([runs[b] for b in by]).mean()
    if p5_from == 'win' and win is not None and len(win):
        keyc = ['campaign', 'arm', 'sc', 'tr', 'nbg', 'seed', 'hm_p', 'hm_mode', 'fb_issue', 'fb_val']
        w = win.merge(runs[keyc + [b for b in by if b not in win.columns]].drop_duplicates(keyc), on=keyc)
        out['prr_p5'] = w.groupby(by).worst.quantile(0.05)
    else:
        out['prr_p5'] = g.prr_worst.quantile(0.05)
    untr = out.traced == 0                                  # untraced groups: P5 over runs
    if untr.any():
        out.loc[untr, 'prr_p5'] = g.prr_worst.quantile(0.05)[untr]
    return out.reset_index()


def time_series(win, by):
    """PRR vs time per group `by` + window centre t: group = mean over runs of the window PRR,
    worst = mean over runs of the window worst-member PRR."""
    w = win.assign(prr=win.d / win.n)
    return w.groupby(by + ['t']).agg(prr_group=('prr', 'mean'), prr_worst=('worst', 'mean'),
                                     n_runs=('seed', 'nunique')).reset_index()


def dist_series(dist, by, min_pairs=5):
    """PRR vs distance bin per group `by`: group = pooled pairs; worst = min over runs of the run's bin PRR;
    p5 = 5th percentile over (run x 2 s window) of the bin PRR (windows with >= min_pairs pairs)."""
    run_keys = ['campaign', 'arm', 'sc', 'seed']
    g = dist.groupby(by + ['dbin'])
    out = g.agg(n=('n', 'sum'), d=('d', 'sum'))
    out['prr_group'] = out.d / out.n
    pr = dist.groupby(by + ['dbin'] + [c for c in run_keys if c not in by])[['n', 'd']].sum()
    out['prr_worst'] = (pr.d / pr.n).groupby(by + ['dbin']).min()
    wd = dist[dist.n >= min_pairs]
    out['prr_p5'] = (wd.d / wd.n).groupby([wd[c] for c in by + ['dbin']]).quantile(0.05)
    return out.reset_index()
