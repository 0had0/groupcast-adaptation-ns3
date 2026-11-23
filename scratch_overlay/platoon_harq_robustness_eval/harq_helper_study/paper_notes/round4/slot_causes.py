"""Why each reserved slot ends up useful / redundant / saved / dead, per scheme (v2 data, full campaign cells).

Per TB of the KPI window (as paper_data.slot_outcomes, grant-size reserved slots, a slot with two copies counts
once), every reserved slot gets one class and one cause:
  useful     a copy sent while a member still lacked the TB
               useful_gain   at least one lacking member decoded the TB on this copy
               useful_sci    no lacking member decoded it, and every lacking member missed the SCI (status 0;
                             failed SCI-2A counts here only in traced runs, otherwise under useful_data)
               useful_data   no lacking member decoded it, at least one tried and failed (status 2)
  redundant  a copy sent after all 8 members had the TB
               red_blind     scheme without feedback for that copy (R1, R1_bsc, R4, R4_bsc)
               red_helper    a helper copy in a feedback scheme
               red_leader    a leader copy in a feedback scheme (feedback of the completing copy not yet
                             usable, or a fallback / takeover copy)
  saved      reserved, not used, all 8 members had the TB (feedback released it, or the helper had no TB to
             send because nobody needed it... counted only when every member had the TB)
  dead       reserved, not used, a member still lacked the TB
               dead_hm_sci   a helper was named and missed the SCI of copy 1 (never knew it was the helper)
               dead_hm_data  a helper was named, decoded the SCI of copy 1, failed the data
               dead_silent   no helper problem; every lacking member was silent on the last copy (Option 1)
               dead_other    anything else
For the useful copies that brought nothing, the lacking members' link geometry on that copy (geo_tx: 0 building,
1 vehicle box, 2 LOS) splits building blockage from interference: useful_nogain_bldg = every lacking member
behind a building.
Usage: python3 -I slot_causes.py <out_dir> <csv_out>
"""
import sys, os, glob
from concurrent.futures import ProcessPoolExecutor
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '../../paper_v2/analysis'))
import paper_data as P

NMEM = 8
BLIND = {'R1', 'R1_bsc', 'R4', 'R4_bsc'}
KEYS = ['useful_gain', 'useful_sci', 'useful_data', 'useful_nogain_bldg', 'red_blind', 'red_helper', 'red_leader',
        'saved', 'dead_hm_sci', 'dead_hm_data', 'dead_silent', 'dead_other', 'reserved',
        'hm_tbs', 'hm_sci_tbs', 'hm_geo_bldg', 'hm_geo_veh', 'hm_geo_los']


def one(args):
    arm, pre, tpre = args
    r = P.rd(pre + '_run.csv').iloc[0]
    a = P.rd(pre + '_att.csv')
    if a is None:
        return None
    k0, k1 = r.kpi_start, r.kpi_end
    first = a[(a.side == 0) & (a.tx_in_tb == 1) & (a.t >= k0) & (a.t < k1 - 0.05)]
    tbs = first.tb.unique()
    nres = P.reserved_slots(pre, first)
    sci2 = P.sci2_failures(tpre) if tpre else None
    a = a[a.tb.isin(tbs)].sort_values('t')
    if sci2:
        miss = [(m, s) in sci2 for m, s in zip(a.member, a.slot_abs)]
        a = a.assign(status=np.where(miss, 0, a.status))
    helper = {}
    h = P.rd(pre + '_hsel.csv')
    if h is not None and len(h):
        h = h.assign(key=h.t_ndi.round(4))
        t1 = first.drop_duplicates('tb').assign(key=lambda x: x.t.round(4)).set_index('key').tb
        helper = {t1[k]: int(v) for k, v in zip(h.key, h.applied) if k in t1.index}
    nk = P.rd(pre + '_nack.csv')
    nack_ts = {m: np.sort(g.t.values) for m, g in nk.groupby('member')} if nk is not None and len(nk) else None
    c = dict.fromkeys(KEYS, 0)
    for tb, g in a.groupby('tb'):
        have = set()
        nslots = 0
        last = None
        for sl, x in g.groupby('slot_abs', sort=True):
            nslots += 1
            lacking = set(range(1, NMEM + 1)) - have
            if lacking:
                lx = x[x.member.isin(lacking)]
                # with two copies in the slot, a member's best outcome counts
                best = lx.groupby('member').status.agg(lambda s: 1 if (s == 1).any() else (2 if (s == 2).any() else 0))
                if (best == 1).any():
                    c['useful_gain'] += 1
                elif (best == 2).any():
                    c['useful_data'] += 1
                else:
                    c['useful_sci'] += 1
                if not (best == 1).any() and (lx.groupby('member').geo_tx.max() == 0).all():
                    c['useful_nogain_bldg'] += 1
            else:
                if arm in BLIND:
                    c['red_blind'] += 1
                elif (x.side == 1).any() and not (x.side == 0).any():
                    c['red_helper'] += 1
                else:
                    c['red_leader'] += 1
            have |= set(x[x.status == 1].member)
            last = x
        nr = nres.get(tb, 3)
        c['reserved'] += nr
        unused = max(0, nr - nslots)
        hm = helper.get(tb)
        x1 = g[(g.side == 0) & (g.tx_in_tb == 1)]
        hm_missed = hm is not None and not (x1[x1.member == hm].status == 1).any()
        if hm_missed:
            hs = x1[x1.member == hm]
            c['hm_tbs'] += 1
            c['hm_sci_tbs'] += int(len(hs) > 0 and hs.status.iloc[0] == 0)
            geo = hs.geo_tx.iloc[0] if len(hs) else -1
            c['hm_geo_bldg' if geo == 0 else 'hm_geo_veh' if geo == 1 else 'hm_geo_los'] += 1
        if len(have) == NMEM:
            c['saved'] += unused
            continue
        if unused == 0:
            continue
        if hm_missed:
            hs = x1[x1.member == hm]
            c['dead_hm_sci' if len(hs) and hs.status.iloc[0] == 0 else 'dead_hm_data'] += unused
            continue
        lacking = set(range(1, NMEM + 1)) - have
        lx = last[last.member.isin(lacking)]
        if nack_ts is not None:
            def nacked(m, t):
                v = nack_ts.get(m)
                return v is not None and ((v >= t) & (v < t + 0.003)).any()
            silent = all(s0 == 0 or (s0 == 2 and not nacked(m, t)) for m, s0, t in zip(lx.member, lx.status, lx.t))
        else:
            silent = len(lx) > 0 and (lx.status == 0).all()
        c['dead_silent' if len(lx) and silent else 'dead_other'] += unused
    n = len(tbs)
    out = {k: v / n for k, v in c.items()}
    tag = os.path.basename(pre)
    cell, seed = tag.rsplit('_s', 1)
    sc, _, tr, bg = cell.split('_')
    run = dict(leader_tx=r.leader_tx / r.pkts, helper_tx=r.helper_tx / r.pkts,
               reclaim=r.helper_reclaim / r.pkts, fallback=r.fallback_tx / r.pkts,
               helper_sci_missed_rate=r.helper_sci_missed_rate, fb_late=r.fb_late / r.pkts,
               relay_after_allack=r.relay_after_allack / r.pkts)
    return dict(arm=arm, sc=sc, tr=tr, nbg=P.NBG[bg], seed=int(seed), tbs=n, traced=int(bool(sci2))) | out | run


def main():
    out, csv = sys.argv[1], sys.argv[2]
    jobs = []
    for arm in P.CAMPS['remote_full']:
        for f in sorted(glob.glob(f'{out}/remote_full_v2/{arm}/*_run.csv')):
            pre = f[:-len('_run.csv')]
            tp = f'{out}/remote_slots_trace/{arm}/{os.path.basename(pre)}'
            jobs.append((arm, pre, tp if P.rd(tp + '_harq.csv') is not None else None))
    with ProcessPoolExecutor(8) as ex:
        rows = [x for x in ex.map(one, jobs, chunksize=4) if x]
    pd.DataFrame(rows).to_csv(csv, index=False)
    print(len(rows), 'runs ->', csv)


if __name__ == '__main__':
    main()
