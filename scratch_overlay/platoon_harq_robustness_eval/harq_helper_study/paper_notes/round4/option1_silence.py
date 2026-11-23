"""Why leader Option 1 leaves reserved slots dead, from the traced runs (out/remote_slots_trace, --writeHarqTrace).

For every TB of the window with a dead slot (paper_data.slot_outcomes: an empty reserved slot while a member still
lacked the TB), look at the members that still lacked it on the TB's last copy:
  sci1  no decode attempt (status 0 in _att.csv): the member missed the SCI-1A
  sci2  status 2 and an SCI-2A failure event (event 6) in _harq.csv: the member could not tell the TB was for it
  data  status 2 without SCI-2A failure: data failed, the member knew the TB (should have sent a NACK)
  nack  the member sent a NACK (< 3 ms after the copy) but the leader did not use the slot
A dead slot counts under 'all_sci1' / 'all_sci2' / 'mixed_sci' when every lacking member was silent for that
reason, under 'not_silent' otherwise. Output: per-cell means, fractions of dead slots.
Usage: python3 -I option1_silence.py <out_dir> [arm ...]   (default arm R2; out_dir holds remote_full_v2 and
remote_slots_trace)
"""
import sys, os, glob, collections
import numpy as np, pandas as pd

NMEM = 8


def rd(p):
    for q in (p, p + '.gz'):
        if os.path.exists(q):
            return pd.read_csv(q)


def one(pre, tpre):
    r = rd(pre + '_run.csv').iloc[0]
    a = rd(pre + '_att.csv')
    k0, k1 = r.kpi_start, r.kpi_end
    first = a[(a.side == 0) & (a.tx_in_tb == 1) & (a.t >= k0) & (a.t < k1 - 0.05)]
    tbs = first.tb.unique()
    gr = rd(pre + '_grants.csv')
    h = rd(tpre + '_harq.csv')
    e6 = h[h.event == 6]
    sci2 = set(zip(e6.member.astype(int), np.floor(e6.t_ms / 0.5).astype(int)))
    nk = rd(pre + '_nack.csv')
    nack_ts = {m: np.sort(g.t.values) for m, g in nk.groupby('member')} if nk is not None and len(nk) else {}
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../paper_v2/analysis'))
    import paper_data as P
    nres = P.reserved_slots(pre, first)
    c = collections.Counter()
    for tb, g in a[a.tb.isin(tbs)].sort_values('t').groupby('tb'):
        have = set(g[g.status == 1].member)
        nslots = g.slot_abs.nunique()
        unused = max(0, nres.get(tb, 3) - nslots)
        if len(have) == NMEM or unused == 0:
            continue
        last = g[g.slot_abs == g.slot_abs.max()]
        lx = last[last.member.isin(set(range(1, NMEM + 1)) - have)]
        why = []
        for m, s, t, sl in zip(lx.member, lx.status, lx.t, lx.slot_abs):
            v = nack_ts.get(m)
            if v is not None and ((v >= t) & (v < t + 0.003)).any():
                why.append('nack')
            elif s == 0:
                why.append('sci1')
            elif (m, sl) in sci2:
                why.append('sci2')
            else:
                why.append('data')
        w = set(why)
        key = ('all_sci1' if w == {'sci1'} else 'all_sci2' if w == {'sci2'} else
               'mixed_sci' if w <= {'sci1', 'sci2'} and w else 'not_silent')
        c['dead'] += unused
        c[key] += unused
        for x in why:
            c['m_' + x] += 1
    n = len(tbs)
    return {k: v / n for k, v in c.items()}


def main():
    out = sys.argv[1]
    arms = sys.argv[2:] or ['R2']
    rows = []
    for arm in arms:
        for f in sorted(glob.glob(f'{out}/remote_slots_trace/{arm}/*_run.csv')):
            tag = os.path.basename(f)[:-len('_run.csv')]
            cell, seed = tag.rsplit('_s', 1)
            rows.append(dict(arm=arm, cell=cell, seed=int(seed)) |
                        one(f'{out}/remote_full_v2/{arm}/{tag}', f'{out}/remote_slots_trace/{arm}/{tag}'))
    d = pd.DataFrame(rows).fillna(0)
    c = d.groupby(['arm', 'cell']).mean(numeric_only=True).drop(columns='seed')
    for k in ('all_sci1', 'all_sci2', 'mixed_sci', 'not_silent'):
        c['f_' + k] = c[k] / c.dead
    pd.set_option('display.width', 250)
    print(c.round(3).to_string())


if __name__ == '__main__':
    main()
