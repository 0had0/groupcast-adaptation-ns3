import glob, os, sys, numpy as np, pandas as pd
B = os.path.expanduser('~/mnt/harq_helper_study/out')
OUT = os.path.expanduser('~/agg')
NBG = {'none': 0, 'n15': 15, 'n30': 30, 'n45': 45, 'moderate': 60}
EDGES = np.arange(17.5, 315, 35.0); GAP = 35.0; NMEM = 8
def rd(p):
    p = p if os.path.exists(p) else p + '.gz'
    return pd.read_csv(p) if os.path.exists(p) else None
rows, pk, mem, tko = [], [], [], []
for camp in ('remote_full', 'remote_density'):
    for armdir in sorted(glob.glob(f'{B}/{camp}/*/')):
        arm = os.path.basename(armdir.rstrip('/'))
        for f in sorted(glob.glob(armdir + '*_run.csv*')):
            pre = f[:f.index('_run.csv')]
            tag = os.path.basename(pre)
            sc, _, tr, bg, s = tag.split('_'); seed = int(s[1:])
            r = rd(pre + '_run.csv').iloc[0]
            k = dict(camp=camp, arm=arm, sc=sc, tr=tr, nbg=NBG[bg], seed=seed)
            rows.append(dict(**k, prr=r.prr_mean, worst=r.prr_min, pkts=r.pkts,
                res=(r.res_used_leader + r.res_used_helper) / r.pkts,
                res_resv=r.res_per_pkt_reserved, cbr=r.cbr,
                coll_loss=r.collision_loss_rate, chan_loss=r.channel_loss_rate,
                leader_tx_coll=r.leader_tx_coll_rate, helper_tx_coll=r.helper_tx_coll_rate,
                helper_tx=r.helper_tx / r.pkts, leader_tx=r.leader_tx / r.pkts,
                reclaim=r.helper_reclaim / r.pkts, fallback=r.fallback_tx / r.pkts,
                fb_check=r.fallback_check / r.pkts, fb_heard=r.fallback_heard / r.pkts,
                helper_nack=r.helper_nack / r.pkts,
                sci_miss=r.helper_sci_missed_rate, kpi_len=r.kpi_end - r.kpi_start))
            m = rd(pre + '_member.csv'); m = m[m.member != 'all']
            for _, x in m.iterrows():
                mem.append(dict(**k, member=int(x.member), prr=x.prr, d_mean=x.mean_delay_ms,
                                d_p95=x.delay_p95_ms, dist=x.mean_dist_m, lost_coll=x.lost_collision, lost_chan=x.lost_channel, sent=x.sent))
            p = rd(pre + '_pkt.csv'); p = p[p.dist_m >= 0]
            pc = p if sc == 'hwy' else p[p.dist_m < p.member * GAP - .5]
            g = pc.groupby(pd.cut(pc.dist_m, EDGES), observed=True).rx.agg(['mean', 'size'])
            for b, x in g.iterrows():
                pk.append(dict(**k, d=b.mid, prr=x['mean'], n=x['size']))
            if camp == 'remote_full' and arm in ('R5', 'R6'):
                ltx = rd(pre + '_ltx.csv'); att = rd(pre + '_att.csv')
                k0, k1 = r.kpi_start, r.kpi_end
                org = dict(zip(ltx.slot_abs, ltx.origin))
                a = att[(att.t >= k0) & (att.t < k1)].sort_values('t')
                first = a[a.status == 1].groupby(['tb', 'member']).first().reset_index()
                lo = first[first.side == 0].slot_abs.map(lambda s: org.get(s, 0))
                tko.append(dict(**k, denom=r.pkts * NMEM, leader_regular=int((lo == 0).sum()),
                    leader_nack_takeover=int((lo == 1).sum()), leader_fallback=int((lo == 2).sum()),
                    helper=int((first.side != 0).sum())))
pd.DataFrame(rows).to_csv(f'{OUT}/runs.csv', index=False)
pd.DataFrame(pk).to_csv(f'{OUT}/dist.csv', index=False)
pd.DataFrame(mem).to_csv(f'{OUT}/members.csv', index=False)
pd.DataFrame(tko).to_csv(f'{OUT}/takeover.csv', index=False)
print('done', len(rows))
