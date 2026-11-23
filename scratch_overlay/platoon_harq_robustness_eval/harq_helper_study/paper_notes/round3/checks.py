"""Consistency checks on every run of the full and density campaigns.

For each run, compares the counters in _run.csv with each other and with the
per-row logs (_member, _pkt, _ltx, _htx, _att). Writes one row per (run, check)
that fails to checks_fail.csv and a per-check summary to checks_summary.csv.
Usage: python3 -I checks.py <out_dir> <dest_dir>
"""
import glob, os, sys
import numpy as np, pandas as pd

OUT, DEST = sys.argv[1], sys.argv[2]
CAMPS = [('remote_full', ['R1', 'R1_bsc', 'R2', 'R3', 'R4', 'R4_bsc', 'R4.1', 'R4.2', 'R5', 'R6']),
         ('remote_density', ['R1', 'R1_bsc', 'R3', 'R4', 'R4.2', 'R6'])]
SUBCH_PRB = 10
HELPER_ARMS = {'R4', 'R4_bsc', 'R4.1', 'R4.2', 'R5', 'R6'}
TAKEOVER_ARMS = {'R5', 'R6'}


def rd(p):
    for q in (p, p + '.gz'):
        if os.path.exists(q):
            return pd.read_csv(q)
    return None


fails, counts, runinfo = [], {}, []


def chk(name, ok, run, detail=''):
    c = counts.setdefault(name, [0, 0])
    c[0] += 1
    if not ok:
        c[1] += 1
        fails.append(dict(check=name, **run, detail=detail))


for camp, arms in CAMPS:
    for arm in arms:
        for f in sorted(glob.glob(f'{OUT}/{camp}/{arm}/*_run.csv')):
            pre = f[:-len('_run.csv')]
            tag = os.path.basename(pre)
            run = dict(camp=camp, arm=arm, tag=tag)
            r = pd.read_csv(f).iloc[0]
            k0, k1 = r.kpi_start, r.kpi_end
            mem, pkt = rd(pre + '_member.csv'), rd(pre + '_pkt.csv')
            ltx, htx, att = rd(pre + '_ltx.csv'), rd(pre + '_htx.csv'), rd(pre + '_att.csv')
            runinfo.append(dict(**run, pkts=r.pkts, bg_seats=r.bg_seats, bg_in_area=r.bg_in_area_mean,
                                leader_new_tb=r.leader_new_tb))
            # --- transmission counters
            chk('leader_tx = new_tb + retx', r.leader_tx == r.leader_new_tb + r.leader_retx, run)
            chk('new_tb = pkts (one TB per packet)', r.leader_new_tb == r.pkts - r.pkts_not_tx, run,
                f'{r.leader_new_tb} vs {r.pkts}')
            chk('tx_per_pkt = (leader_tx + helper_tx)/pkts',
                abs(r.tx_per_pkt * r.pkts - (r.leader_tx + r.helper_tx)) < 1, run)
            chk('leader tx per TB <= 3', r.leader_tx_per_tb_max <= 3, run)
            if ltx is not None:
                lw = ltx[(ltx.t >= k0) & (ltx.t < k1)]
                chk('leader_tx = rows of _ltx in window', len(lw) == r.leader_tx, run, f'{len(lw)} vs {r.leader_tx}')
                chk('res_used_leader = sum ltx rb/10', abs(lw.rb_len.sum() / SUBCH_PRB - r.res_used_leader) < 1e-6, run,
                    f'{lw.rb_len.sum() / SUBCH_PRB} vs {r.res_used_leader}')
                chk('leader_tx_coll_rate = share of ltx with n_coll>0',
                    abs((lw.n_coll > 0).mean() - r.leader_tx_coll_rate) < 1e-4 if len(lw) else True, run)
            if htx is not None:
                hw = htx[(htx.t >= k0) & (htx.t < k1)]
                chk('helper_tx = rows of _htx in window', len(hw) == r.helper_tx, run, f'{len(hw)} vs {r.helper_tx}')
                chk('res_used_helper = sum htx rb/10', abs(hw.rb_len.sum() / SUBCH_PRB - r.res_used_helper) < 1e-6, run,
                    f'{hw.rb_len.sum() / SUBCH_PRB} vs {r.res_used_helper}')
            # --- channel occupancy
            chk('cbr_occupied <= cbr', r.cbr_occupied <= r.cbr + 1e-9, run)
            chk('cbr_occupied <= 1', r.cbr_occupied <= 1 + 1e-9, run)
            slots = (k1 - k0) / 0.5e-3 * r.sl_slot_frac
            chk('cbr_leader * slots * nsub = res_used_leader',
                abs(r.cbr_leader * slots * r.n_subch - r.res_used_leader) < 0.01 * max(1, r.res_used_leader), run)
            # --- feedback
            chk('nack_rx <= nack_sent', r.nack_rx <= r.nack_sent, run, f'{r.nack_rx} vs {r.nack_sent}')
            if r.harq_mode == 'blind':
                chk('blind: no NACK/ACK received', r.nack_rx == 0 and r.explicit_ack_rx == 0, run)
                chk('blind: leader_tx = 3 x new_tb (leader alone)' if arm in ('R1', 'R1_bsc') else 'blind helper: leader sends 1 per TB',
                    r.leader_tx == (3 if arm in ('R1', 'R1_bsc') else 1) * r.leader_new_tb, run,
                    f'{r.leader_tx} vs {r.leader_new_tb}')
            if r.harq_mode == 'opt1':
                chk('opt1: no explicit ACK', r.explicit_ack_rx == 0, run)
            if r.harq_mode in ('opt1', 'opt2'):
                chk('feedback: no leader retx without trigger', r.leader_retx_without_nack == 0, run, str(r.leader_retx_without_nack))
            # --- helper counters
            if arm not in HELPER_ARMS:
                chk('no helper: helper_tx = 0', r.helper_tx == 0 and r.res_used_helper == 0, run)
            else:
                chk('helper arm: helper_tx > 0', r.helper_tx > 0, run)
                chk('helper_att = after_fail + first', r.helper_att == r.helper_att_after_fail + r.helper_att_first, run)
                chk('helper_recovered <= helper_att_ok <= helper_att',
                    r.helper_recovered <= r.helper_att_ok <= r.helper_att, run)
            if arm in TAKEOVER_ARMS:
                chk('helper_named_tx = decoded + corrupt + sci_missed',
                    r.helper_named_tx == r.helper_decoded_tx + r.helper_corrupt_tx + r.helper_sci_missed, run,
                    f'{r.helper_named_tx} vs {r.helper_decoded_tx}+{r.helper_corrupt_tx}+{r.helper_sci_missed}')
                chk('helper_corrupt_tx = nack_sent + nack_not_sent',
                    r.helper_corrupt_tx == r.helper_nack_sent + r.helper_nack_not_sent, run)
                chk('helper_nack_not_sent = corrupt_sci2', r.helper_nack_not_sent == r.helper_corrupt_sci2, run)
                chk('helper_nack = reclaim + nack_no_slot', r.helper_nack == r.helper_reclaim + r.helper_nack_no_slot, run)
                chk('fallback_check = heard + tx + no_slot',
                    r.fallback_check == r.fallback_heard + r.fallback_tx + r.fallback_no_slot, run)
            # --- implementation sanity flags (must be zero)
            for z in ['phy_unmatched', 'slot_misaligned', 'sfn_offset_mismatch', 'relay_unmatched',
                      'relay_into_reused_harq', 'relay_without_decode', 'relay_before_leader_retx',
                      'relay_after_allack', 'stale_decisions', 'leader_pkt_multi_tb', 'pkts_not_tx']:
                chk(f'zero: {z}', r[z] == 0, run, str(r[z]))
            # --- members
            m = mem[mem.member.astype(str) != 'all'].copy()
            m['member'] = m.member.astype(int)
            chk('8 members', len(m) == 8, run)
            chk('member sent = pkts', (m.sent == r.pkts).all(), run)
            chk('sent = rx + lost_coll + lost_chan + lost_notx + lost_other',
                (m.sent == m.rx + m.lost_collision + m.lost_channel + m.lost_notx + m.lost_other).all(), run)
            chk('attempts = ok + corrupt + missed', (m.attempts == m.att_ok + m.att_corrupt + m.att_missed).all(), run)
            chk('prr_mean = mean member prr', abs(m.prr.mean() - r.prr_mean) < 1e-4, run)
            chk('prr_min = min member prr', abs(m.prr.min() - r.prr_min) < 1e-4, run)
            chk('collision_loss_rate = sum lost_coll / sum sent',
                abs(m.lost_collision.sum() / m.sent.sum() - r.collision_loss_rate) < 1e-4, run)
            # --- per packet log
            if pkt is not None:
                chk('pkt rows = pkts x 8', len(pkt) == r.pkts * 8, run, f'{len(pkt)} vs {r.pkts * 8}')
                rx = pkt.groupby('member').rx.sum()
                chk('pkt rx per member = member.rx', (rx.reindex(m.member).values == m.rx.values).all(), run)
                chk('n_att_coll <= n_att and n_att_fail <= n_att',
                    ((pkt.n_att_coll <= pkt.n_att) & (pkt.n_att_fail <= pkt.n_att)).all(), run)
                chk('received => >= 1 attempt', (pkt[pkt.rx == 1].n_att >= 1).all(), run)
                chk('lost and transmitted => all attempts failed',
                    (pkt[(pkt.rx == 0)].n_att == pkt[(pkt.rx == 0)].n_att_fail).all(), run)
                chk('pkt n_att <= 3', (pkt.n_att <= 3).all(), run, str(pkt.n_att.max()))
                chk('delay <= 100 ms when received', (pkt[pkt.rx == 1].delay_ms <= 100).all(), run,
                    str(pkt[pkt.rx == 1].delay_ms.max()))
            # --- budget per TB: distinct transmissions (leader + helper) <= 3
            if att is not None:
                n = att.drop_duplicates(['tb', 'side', 'slot_abs']).groupby('tb').size()
                chk('copies per TB (leader + helper) <= 3', n.max() <= 3, run, str(n.max()))
                chk('no member decodes the same TB twice',
                    att[att.status == 1].duplicated(['tb', 'member']).sum() == 0, run,
                    str(att[att.status == 1].duplicated(['tb', 'member']).sum()))
        print(camp, arm, flush=True)

# --- pairing: same tag must see the same traffic and the same background in every arm
ri = pd.DataFrame(runinfo)
for (camp, tag), g in ri.groupby(['camp', 'tag']):
    run = dict(camp=camp, arm='*', tag=tag)
    chk('pairing: same pkts in every arm', g.pkts.nunique() == 1, run, str(sorted(g.pkts.unique())))
    chk('pairing: same bg_seats in every arm', g.bg_seats.nunique() == 1, run)
    chk('pairing: same bg_in_area in every arm', g.bg_in_area.round(3).nunique() == 1, run,
        str(sorted(g.bg_in_area.unique())))

pd.DataFrame(fails).to_csv(f'{DEST}/checks_fail.csv', index=False)
s = pd.DataFrame([dict(check=k, runs=v[0], failed=v[1]) for k, v in counts.items()])
s.to_csv(f'{DEST}/checks_summary.csv', index=False)
print(s.to_string())
