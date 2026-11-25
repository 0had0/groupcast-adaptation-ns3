"""SL CBR (TS 38.215 5.1.30) rebuilt offline from the _alltx traces of campaign v3_fbs (every pool transmission).

SL RSSI (TS 38.215 5.1.28): total received power in one subchannel (10 PRB), averaged over the PSCCH/PSSCH
symbols of a slot. SL CBR at slot n: share of the pool's subchannels whose SL RSSI exceeds sl-Threshold-RSSI-CBR
(TS 38.331: -112 + 2k dBm, k = 0..45) over the window [n - W, n - 1], W = 100 ms (sl-TimeWindowSizeCBR = ms100;
200 slots at mu = 1). Slots in which the measuring UE transmits are left out (half duplex: it cannot measure).

Received power, as the simulator models it (common/stack/nr_sl_stack.cc, three-gpp-geometric-nlosv, TR 37.885):
  - 23 dBm spread uniformly over the transmitted RBs (NrPhy PowerAllocationType default: used RBs);
  - path loss TR 37.885 at 5.89 GHz, antennas 1.5 m: highway LOS 32.4 + 20 log d + 20 log fc;
    urban LOS 38.77 + 16.7 log d + 18.2 log fc; urban NLOS (building) 36.85 + 30 log d + 18.9 log fc;
  - roundabout: NLOS when the segment crosses one of the 98 building rectangles (sumo/roundabout/buildings.csv);
  - NLOSv when the 5 m x 1.8 m axis-aligned box of another vehicle crosses the segment (as the geometric
    wrapper); extra loss lognormal, mean 9 + max(0, 15 log d - 41) dB, std 4.5 (both antennas below 1.6 m);
  - otherwise LOS with the TR 37.885 LOS probability (highway / urban), else NLOSv;
  - shadowing N(0, 3 dB) LOS / N(0, 4 dB) NLOS and NLOSv, one draw per (transmission, receiver); no fast fading
    (the RSSI averages over 120 subcarriers and 12 symbols);
  - noise -174 dBm/Hz + 10 log10(3.6 MHz) + 5 dB noise figure = -103.4 dBm per subchannel.
Draws are independent per transmission (the simulator correlates them over 10 ms): CBR is an average, so this
changes little, but single-slot values are not those of the run.

  python3 scripts/sl_cbr.py [--folder out/remote_v3_fbs/R4_bsc_fbs] [--jobs 6] [--thr -100,-94,-88]
  -> out/cache_schemes/sl_cbr.parquet (one row per run x measuring UE x threshold)
"""
import argparse
import glob
import multiprocessing as mp
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACES = os.path.join(HERE, '..', 'traces')
BUILDINGS = os.path.join(HERE, '..', 'sumo', 'roundabout', 'buildings.csv')
FC = 5.89
PTX_DBM = 23.0
NOISE_DBM = -174 + 10 * np.log10(10 * 12 * 30e3) + 5.0
SLOT = 0.5e-3
W_SLOTS = 200
NSUB = 5
SUB_RB = 10
VEH_L, VEH_W = 5.0, 1.8
NBG = {'none': 0, 'n15': 15, 'n30': 30, 'n45': 45, 'moderate': 60}
RX_NODES = {'leader': 0, 'tail': 8}


def _seg_hits_boxes(p0, p1, cx, cy, hx, hy):
    """Liang-Barsky: segments p0->p1 (N x 2) against axis-aligned boxes centred (cx, cy) (N x M or M),
    half sizes hx, hy. Returns N x M bool."""
    dx = (p1[:, 0] - p0[:, 0])[:, None]
    dy = (p1[:, 1] - p0[:, 1])[:, None]
    t0 = np.zeros(np.broadcast(dx, cx).shape)
    t1 = np.ones_like(t0)
    ok = np.ones_like(t0, bool)
    for p, q in ((-dx, p0[:, 0][:, None] - (cx - hx)), (dx, (cx + hx) - p0[:, 0][:, None]),
                 (-dy, p0[:, 1][:, None] - (cy - hy)), (dy, (cy + hy) - p0[:, 1][:, None])):
        p = np.broadcast_to(p, t0.shape)
        q = np.broadcast_to(q, t0.shape)
        par = p == 0
        ok &= ~(par & (q < 0))
        with np.errstate(divide='ignore', invalid='ignore'):
            r = np.where(par, 0.0, q / np.where(par, 1, p))
        t0 = np.where(~par & (p < 0), np.maximum(t0, r), t0)
        t1 = np.where(~par & (p > 0), np.minimum(t1, r), t1)
    return ok & (t0 <= t1)


def _buildings():
    b = pd.read_csv(BUILDINGS)
    xs = b[['x1', 'x2', 'x3', 'x4']].values
    ys = b[['y1', 'y2', 'y3', 'y4']].values
    return ((xs.min(1) + xs.max(1)) / 2, (ys.min(1) + ys.max(1)) / 2,
            (xs.max(1) - xs.min(1)) / 2, (ys.max(1) - ys.min(1)) / 2)


def run_cbr(pre, thresholds, seed=1):
    rng = np.random.default_rng(seed)
    cell = os.path.basename(pre)
    sc, ivd, tr, bg, s = cell.split('_')
    r = pd.read_csv(pre + '_run.csv').iloc[0]
    k0, k1 = float(r.kpi_start), float(r.kpi_end)
    a = pd.read_csv(pre + '_alltx.csv' + ('.gz' if os.path.exists(pre + '_alltx.csv.gz') else ''))
    a = a[(a.t >= k0 - W_SLOTS * SLOT) & (a.t < k1)].reset_index(drop=True)
    tracef = os.path.join(TRACES, f'{sc}_ivd30_n{NBG[bg]}_s{s[1:]}.csv.gz')
    tr_df = pd.read_csv(tracef, comment='#')
    nodes = int(tr_df.node_id.max()) + 1
    times = np.round(tr_df.time.values / 0.1).astype(int)
    pos = np.full((times.max() + 1, nodes, 2), -1e7)
    pos[times, tr_df.node_id.values] = tr_df[['x', 'y']].values
    ti = np.clip(np.round(a.t.values / 0.1).astype(int), 0, times.max())
    urban = sc != 'hwy'
    bld = _buildings() if urban else None
    s_lo = int(round(k0 / SLOT))
    s_hi = int(round(k1 / SLOT))
    out = []
    for rx_name, rx in RX_NODES.items():
        m = a.node.values != rx
        own_slots = set(a.slot_abs.values[~m])
        b = a[m].reset_index(drop=True)
        bti = ti[m]
        txp = b[['x', 'y']].values
        rxp = pos[bti, rx]
        d = np.maximum(np.hypot(*(txp - rxp).T), 1.0)
        far = (np.abs(txp).max(1) > 1e5) | (np.abs(rxp).max(1) > 1e5)
        # geometry: buildings (NLOS), other vehicles (NLOSv)
        nlos = np.zeros(len(b), bool)
        if urban:
            cx, cy, hx, hy = bld
            for i in range(0, len(b), 20000):
                sl = slice(i, i + 20000)
                nlos[sl] = _seg_hits_boxes(txp[sl], rxp[sl], cx[None], cy[None], hx[None], hy[None]).any(1)
        vblk = np.zeros(len(b), bool)
        for i in range(0, len(b), 5000):
            sl = slice(i, i + 5000)
            vp = pos[bti[sl]]                                    # N x nodes x 2
            hit = _seg_hits_boxes(txp[sl], rxp[sl], vp[:, :, 0], vp[:, :, 1], VEH_L / 2, VEH_W / 2)
            ar = np.arange(hit.shape[0])
            hit[ar, b.node.values[sl]] = False                   # the transmitter itself
            hit[:, rx] = False                                   # the receiver itself
            vblk[sl] = hit.any(1)
        # LOS probability for links not blocked geometrically
        if urban:
            plos = np.minimum(1.0, 1.05 * np.exp(-0.0114 * d))
        else:
            plos = np.where(d <= 475, np.minimum(1.0, 2.1013e-6 * d ** 2 - 0.002 * d + 1.0193),
                            np.maximum(0.0, 0.54 - 0.001 * (d - 475)))
        state = np.where(nlos, 2, np.where(vblk | (rng.random(len(b)) >= plos), 1, 0))   # 0 LOS 1 NLOSv 2 NLOS
        if urban:
            pl_los = 38.77 + 16.7 * np.log10(d) + 18.2 * np.log10(FC)
        else:
            pl_los = 32.4 + 20 * np.log10(d) + 20 * np.log10(FC)
        pl_nlos = 36.85 + 30 * np.log10(d) + 18.9 * np.log10(FC)
        mu = 9.0 + np.maximum(0.0, 15 * np.log10(d) - 41.0)
        sg = 4.5
        lmu = np.log(mu ** 2 / np.sqrt(sg ** 2 + mu ** 2))
        lsg = np.sqrt(np.log(sg ** 2 / mu ** 2 + 1))
        extra = np.maximum(0.0, rng.lognormal(lmu, lsg))
        pl = np.where(state == 2, pl_nlos, pl_los + np.where(state == 1, extra, 0.0))
        pl = pl + rng.normal(0, np.where(state == 0, 3.0, 4.0))
        prx_sub = PTX_DBM - pl + 10 * np.log10(SUB_RB / b.rb_len.values)   # dBm per subchannel
        prx_sub[far] = -np.inf
        # RSSI matrix over slots x subchannels
        s0 = int(b.slot_abs.min()) if len(b) else s_lo - W_SLOTS
        s0 = min(s0, s_lo - W_SLOTS)
        n_sl = s_hi - s0
        rssi_w = np.zeros((n_sl, NSUB))
        sub0 = (b.rb_start.values // SUB_RB).astype(int)
        nsub = (b.rb_len.values // SUB_RB).astype(int)
        lin = 10 ** ((prx_sub - 30) / 10)
        idx = b.slot_abs.values.astype(int) - s0
        ok = (idx >= 0) & (idx < n_sl)
        for k in range(NSUB):
            cover = ok & (sub0 <= k) & (sub0 + nsub > k)
            np.add.at(rssi_w[:, k], idx[cover], lin[cover])
        rssi = 10 * np.log10(rssi_w + 10 ** ((NOISE_DBM - 30) / 10)) + 30
        valid = np.ones(n_sl, bool)
        own = np.array(sorted(own_slots), int) - s0
        valid[own[(own >= 0) & (own < n_sl)]] = False
        for thr in thresholds:
            busy = (rssi > thr).sum(1) * valid
            cb = np.concatenate([[0], np.cumsum(busy)])
            cv = np.concatenate([[0], np.cumsum(valid)])
            n = np.arange(s_lo - s0, s_hi - s0)
            num = cb[n] - cb[n - W_SLOTS]
            den = NSUB * (cv[n] - cv[n - W_SLOTS])
            cbr = num / np.maximum(den, 1)
            out.append(dict(cell=cell, sc=sc, tr=tr, nbg=NBG[bg], seed=int(s[1:]), rx=rx_name, thr=thr,
                            cbr_mean=float(cbr.mean()), cbr_p5=float(np.percentile(cbr, 5)),
                            cbr_p95=float(np.percentile(cbr, 95)), cbr_max=float(cbr.max()),
                            occ=float(r.cbr_occupied), los=float((state == 0).mean()),
                            nlosv=float((state == 1).mean()), nlos=float((state == 2).mean())))
    return out


def _job(a):
    try:
        return run_cbr(*a)
    except Exception as e:
        return [dict(cell=a[0], error=repr(e))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--folder', default=os.path.join(HERE, 'out', 'remote_v3_fbs', 'R4_bsc_fbs'))
    ap.add_argument('--jobs', type=int, default=6)
    ap.add_argument('--thr', default='-100,-94,-88')
    ap.add_argument('--only', default='')
    ap.add_argument('--out', default=os.path.join(HERE, 'out', 'cache_schemes', 'sl_cbr.parquet'))
    a = ap.parse_args()
    thr = [float(x) for x in a.thr.split(',')]
    pres = sorted(p.split('_alltx.csv')[0] for p in glob.glob(f'{a.folder}/*{a.only}*_alltx.csv*'))
    with ProcessPoolExecutor(a.jobs, mp_context=mp.get_context('fork')) as ex:
        rows = [x for rr in ex.map(_job, [(p, thr) for p in pres]) for x in rr]
    d = pd.DataFrame(rows)
    if 'error' in d:
        print(d[d.error.notna()][['cell', 'error']].to_string())
        d = d[d.error.isna()].drop(columns='error')
    d.to_parquet(a.out)
    print(len(d), 'rows ->', a.out)
    print(d[d.thr == -94].groupby(['rx', 'sc', 'tr', 'nbg'])[['occ', 'cbr_mean', 'cbr_p95']].mean().round(3))


if __name__ == '__main__':
    main()
