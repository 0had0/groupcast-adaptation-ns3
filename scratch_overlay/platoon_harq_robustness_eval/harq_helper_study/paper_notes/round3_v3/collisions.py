"""Collision metrics per run, from the logs of the full and density campaigns.

Definitions (all inside the KPI window):
  overlap        a platoon transmission (leader or helper copy) shares the slot and
                 >= 1 subchannel with a PSSCH of another vehicle within 5 km of the
                 transmitter. This is the simulator's definition (n_coll > 0).
  overlap_rate   overlapped platoon transmissions / platoon transmissions.
  near_rate_<d>  same, but the nearest overlapping transmitter is within d metres.
  collision_loss / channel_loss
                 lost (packet, member) pairs / all pairs, split by the simulator:
                 "collision" if >= 1 failed attempt of the packet at that member
                 overlapped, "channel" if all failed attempts were on clean resources.
  p_fail_overlap / p_fail_clean
                 per member attempt (a copy received by a member that did not yet
                 hold the TB): share that failed, for overlapped and clean copies.
Attempt-level metrics need _att/_ltx/_htx, which only the full campaign wrote.
"""
import glob, os
import numpy as np, pandas as pd

ARMS = {'remote_full': ['R1', 'R1_bsc', 'R2', 'R3', 'R4', 'R4_bsc', 'R4.1', 'R4.2', 'R5', 'R6'],
        'remote_density': ['R1', 'R1_bsc', 'R3', 'R4', 'R4.2', 'R6']}
NBG = {'none': 0, 'n15': 15, 'n30': 30, 'n45': 45, 'moderate': 60}
NEAR = (100, 300, 1000)


def _rd(p):
    for q in (p, p + '.gz'):
        if os.path.exists(q):
            return pd.read_csv(q)
    return None


def run_metrics(pre):
    r = pd.read_csv(pre + '_run.csv').iloc[0]
    sc, _, tr, bg, s = os.path.basename(pre).split('_')
    k0, k1 = r.kpi_start, r.kpi_end
    ntx = r.leader_tx + r.helper_tx
    out = dict(sc=sc, tr=tr, nbg=NBG[bg], seed=int(s[1:]), prr=r.prr_mean,
               cbr_occupied=r.cbr_occupied, platoon_tx=ntx,
               overlap_rate=(r.leader_tx * r.leader_tx_coll_rate + r.helper_tx * r.helper_tx_coll_rate) / ntx,
               collision_loss=r.collision_loss_rate, channel_loss=r.channel_loss_rate)
    l, h, a = _rd(pre + '_ltx.csv'), _rd(pre + '_htx.csv'), _rd(pre + '_att.csv')
    if l is None or a is None:
        return out, None
    tx = pd.concat([l.assign(side=0), (h if h is not None else l.iloc[:0]).assign(side=1)])
    tx = tx[(tx.t >= k0) & (tx.t < k1)]
    for d in NEAR:
        out[f'near_rate_{d}'] = ((tx.n_coll > 0) & (tx.nearest_collider_m <= d)).mean()
    # platoon self-overlap: leader and helper copy in the same slot on common subchannels
    m = l.merge(h, on='slot_abs', suffixes=('_l', '_h')) if h is not None else l.iloc[:0]
    if len(m):
        m = m[(m.t_l >= k0) & (m.t_l < k1)]
        ov = (m.rb_start_l < m.rb_start_h + m.rb_len_h) & (m.rb_start_h < m.rb_start_l + m.rb_len_l)
        out['same_slot_pairs'], out['same_slot_overlap'] = len(m), int(ov.sum())
    else:
        out['same_slot_pairs'], out['same_slot_overlap'] = 0, 0
    # member attempts: copies received before (and including) the member's first success
    a = a[(a.t >= k0) & (a.t < k1)].sort_values('t')
    first = a[a.status == 1].groupby(['tb', 'member']).t.min().rename('t_ok')
    a = a.join(first, on=['tb', 'member'])
    a = a[a.t_ok.isna() | (a.t <= a.t_ok)]
    a = a.merge(tx[['side', 'slot_abs', 'n_coll', 'nearest_collider_m']], on=['side', 'slot_abs'], how='left')
    ov = a.n_coll > 0
    fail = a.status != 1
    out.update(attempts=len(a), att_overlap_share=ov.mean(),
               p_fail_overlap=fail[ov].mean() if ov.any() else np.nan,
               p_fail_clean=fail[~ov].mean() if (~ov).any() else np.nan)
    near = a.loc[ov, ['nearest_collider_m']].assign(fail=fail[ov].values)
    return out, near


def collect(out_dir, cache):
    """Build (or load) the per-run table and the per-attempt nearest-collider table."""
    runs_csv, near_csv = cache + '_runs.csv', cache + '_near.csv'
    if os.path.exists(runs_csv):
        return pd.read_csv(runs_csv), pd.read_csv(near_csv)
    rows, nears = [], []
    for camp, arms in ARMS.items():
        for arm in arms:
            for f in sorted(glob.glob(f'{out_dir}/{camp}/{arm}/*_run.csv')):
                pre = f[:-len('_run.csv')]
                o, n = run_metrics(pre)
                rows.append(dict(camp=camp, arm=arm, **o))
                if n is not None:
                    nears.append(n.assign(arm=arm, sc=o['sc'], tr=o['tr'], nbg=o['nbg'], seed=o['seed']))
    runs, near = pd.DataFrame(rows), pd.concat(nears)
    runs.to_csv(runs_csv, index=False)
    near.to_csv(near_csv, index=False)
    return runs, near
