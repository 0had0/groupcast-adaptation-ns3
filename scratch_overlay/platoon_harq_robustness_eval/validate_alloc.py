#!/usr/bin/env python3
"""validate_alloc.py -- what every vehicle allocated, from --writeAllTx / --writeGrants.

Usage: validate_alloc.py <out-prefix> [<out-prefix> ...]
  reads <prefix>_alltx.csv, <prefix>_grants.csv, <prefix>_run.csv

Per vehicle class (leader, members, background periodic1 = nodes with SPS grants,
background aperiodic1 = nodes with dynamic grants only), over the KPI window and the
vehicles in the area (within --radius of the leader; parked seats are > 1000 km away):
  * grants: type (SPS / dynamic), RRI, subchannels, bytes, slots per period;
    (re)selections, kept resources and misfit reselections per vehicle and second;
    SPS grant lifetime in periods (creation to next creation of the same vehicle)
  * transmissions: per vehicle and second, subchannels per transmission, share of
    retransmissions (NDI = 0); SPS periodicity: share of new transmissions exactly one
    RRI after the vehicle's previous one, on the same subchannels
  * resource collisions: share of transmissions overlapping another transmission
    (same slot, >= 1 common PRB), by class pair and by distance between the two
  * CBR recomputed from the transmissions vs the run CSV
"""
import argparse
import math
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

SUBCH_PRB = 10
NUM_PLATOON = 9


def load(prefix):
    tx = pd.read_csv(prefix + "_alltx.csv")
    gr = pd.read_csv(prefix + "_grants.csv")
    run = pd.read_csv(prefix + "_run.csv").iloc[0]
    return tx, gr, run


def classes(gr, nodes):
    sps_nodes = set(gr[(gr.event == "c") & (gr.sps == 1)].node)
    cls = {}
    for n in nodes:
        if n == 0:
            cls[n] = "leader"
        elif n < NUM_PLATOON:
            cls[n] = "member"
        else:
            cls[n] = "bg_sps" if n in sps_nodes else "bg_dyn"
    return cls


def report(prefix, radius):
    tx, gr, run = load(prefix)
    k0, k1 = float(run.kpi_start), float(run.kpi_end)
    win = k1 - k0
    tx = tx[(tx.t >= k0) & (tx.t < k1)].copy()
    lead = tx[tx.node == 0][["slot_abs", "x", "y"]]
    # leader position per slot (nearest leader transmission in time)
    lead_xy = lead.sort_values("slot_abs")
    tx = tx.sort_values("slot_abs")
    m = pd.merge_asof(tx, lead_xy.rename(columns={"x": "lx", "y": "ly"}), on="slot_abs",
                      direction="nearest")
    m["d_leader"] = np.hypot(m.x - m.lx, m.y - m.ly)
    tx = m
    cls = classes(gr, tx.node.unique())
    tx["cls"] = tx.node.map(cls)
    inarea = tx[tx.d_leader <= radius].copy()
    tx["sb0"] = tx.rb_start // SUBCH_PRB
    tx["nsb"] = (tx.rb_len + SUBCH_PRB - 1) // SUBCH_PRB
    inarea["sb0"] = inarea.rb_start // SUBCH_PRB
    inarea["nsb"] = (inarea.rb_len + SUBCH_PRB - 1) // SUBCH_PRB

    print(f"\n=== {prefix.split('/')[-1]}: {run.scenario} IVD {run.ivd_m} {run.traffic} N {run.nbg}, "
          f"KPI window {k0:.1f}-{k1:.1f} s, leader grant {run.get('leader_grant', '?')}, "
          f"misfit {run.get('sps_reselect_on_misfit', '?')}, D1 {run.get('sl_dyn_exclusion', '?')}, "
          f"D2 {run.get('sl_project_announced', '?')}, keep {run.get('sl_prob_resource_keep', '?')}")

    # vehicles in the area per class (time-averaged by transmissions is biased; use
    # the share of each vehicle's transmissions that are in the area)
    nveh = inarea.groupby("cls").node.nunique()
    vehsec = (inarea.groupby(["cls", "node"]).t.agg(["min", "max"])
              .assign(dur=lambda d: (d["max"] - d["min"]).clip(lower=0.1))
              .groupby("cls").dur.sum())

    # ---- grants
    g = gr[(gr.t >= k0) & (gr.t < k1)].copy()
    g["cls"] = g.node.map(lambda n: cls.get(n, "bg_sps" if n >= NUM_PLATOON else "member"))
    # only vehicles that were in the area at the event (nearest own transmission)
    pos = tx[["t", "node", "d_leader"]].sort_values("t")
    g = pd.merge_asof(g.sort_values("t"), pos, on="t", by="node", direction="nearest")
    g = g[g.d_leader <= radius]
    print("\n-- grants (vehicles within %d m of the leader)" % radius)
    rows = []
    for c in ["leader", "bg_sps", "bg_dyn"]:
        gc = g[g.cls == c]
        cr = gc[gc.event == "c"]
        if cr.empty and c != "leader":
            continue
        vs = vehsec.get(c, win if c == "leader" else np.nan)
        rows.append({
            "class": c, "vehicles": int(nveh.get(c, 0)),
            "grants": len(cr), "sps share": cr.sps.mean() if len(cr) else np.nan,
            "RRI ms": ",".join(str(v) for v in sorted(cr.rri_ms.unique())),
            "subch/grant": cr.sb_len.mean(), "bytes/grant": cr.bytes.mean(),
            "slots/period": cr.slots_or_counter.mean(),
            "(re)sel per veh-s": len(cr) / vs, "kept per veh-s": (gc.event == "k").sum() / vs,
            "misfit resel per veh-s": ((gc.event == "m") & (gc.reselected == 1)).sum() / vs,
            "counter mean": cr[cr.sps == 1].extra.mean() if (cr.sps == 1).any() else np.nan,
        })
    print(pd.DataFrame(rows).round(3).to_string(index=False))
    mis = g[(g.event == "m")]
    if len(mis):
        print("  misfit events: buffer bytes", dict(Counter(mis.bytes).most_common(4)),
              "| TB capacity", dict(Counter(mis.extra).most_common(3)),
              "| reselected share %.2f" % mis.reselected.mean())
    by = g[(g.event == "c") & (g.sps == 1)].groupby("cls").bytes.apply(lambda s: dict(Counter(s).most_common(4)))
    print("  SPS grant sizes (bytes: count):", by.to_dict())

    # SPS lifetime in periods (creation -> next creation of the same vehicle)
    cr = gr[(gr.event == "c") & (gr.sps == 1)].sort_values("t")
    life = []
    for n, s in cr.groupby("node"):
        t = s.t.values
        life += list(np.diff(t) / (s.rri_ms.iloc[0] / 1000.0))
    if life:
        life = np.array(life)
        print("  SPS lifetime (periods between (re)selections of a vehicle): mean %.1f, "
              "median %.0f, p10 %.0f, p90 %.0f (n %d)" % (life.mean(), np.median(life),
                                                       np.percentile(life, 10),
                                                       np.percentile(life, 90), len(life)))

    # ---- transmissions
    print("\n-- transmissions (in area)")
    rows = []
    for c in ["leader", "member", "bg_sps", "bg_dyn"]:
        tc = inarea[inarea.cls == c]
        if tc.empty:
            continue
        vs = vehsec.get(c, np.nan)
        new = tc[tc.ndi == 1].sort_values(["node", "slot_abs"])
        per = np.nan
        if c in ("leader", "bg_sps"):
            rri = 200 if run.traffic != "periodic2" or c != "leader" else 20
            if c == "bg_sps":
                rri = 200
            d = new.groupby("node").slot_abs.diff()
            same = (new.groupby("node").rb_start.diff() == 0)
            per = ((d == rri) & same).sum() / max(1, d.notna().sum())
        rows.append({"class": c, "tx": len(tc), "tx per veh-s": len(tc) / vs,
                     "subch/tx": tc.nsb.mean(), "retx share": (tc.ndi == 0).mean(),
                     "next new tx = +RRI, same subch": per})
    print(pd.DataFrame(rows).round(3).to_string(index=False))

    # ---- collisions among in-area transmissions
    a = inarea[["slot_abs", "node", "cls", "rb_start", "rb_len", "x", "y"]].reset_index(drop=True)
    a = a.sort_values("slot_abs", kind="stable").reset_index(drop=True)
    slot = a.slot_abs.values
    node = a.node.values
    rb0 = a.rb_start.values
    rb1 = rb0 + a.rb_len.values
    xs, ys = a.x.values, a.y.values
    cl = a.cls.values
    coll = np.zeros(len(a), dtype=bool)
    pairs = []
    starts = np.flatnonzero(np.r_[True, slot[1:] != slot[:-1]])
    ends = np.r_[starts[1:], len(a)]
    for b, e in zip(starts, ends):
        if e - b < 2:
            continue
        for i in range(b, e):
            for j in range(i + 1, e):
                if node[i] != node[j] and max(rb0[i], rb0[j]) < min(rb1[i], rb1[j]):
                    coll[i] = coll[j] = True
                    pairs.append((tuple(sorted((cl[i], cl[j]))),
                                  math.hypot(xs[i] - xs[j], ys[i] - ys[j])))
    a["coll"] = coll
    print("\n-- resource collisions (same slot, >= 1 common PRB), in-area transmissions")
    print("  share of transmissions in collision:",
          a.groupby("cls").coll.mean().round(3).to_dict())
    if pairs:
        P = pd.DataFrame(pairs, columns=["pair", "d"])
        P["d_bin"] = pd.cut(P.d, [0, 100, 300, 600, 1000, 1e9],
                            labels=["<100 m", "100-300", "300-600", "600-1000", ">1000"])
        print("  colliding pairs by class pair x distance between the two transmitters:")
        print(P.groupby(["pair", "d_bin"], observed=False).size().unstack(fill_value=0).to_string())

    # ---- CBR
    sl_frac = 9 / 20 if not int(run.get("dedicated_carrier", 0) or 0) else 1.0
    slots = win / 0.0005 * sl_frac
    nsub = int(run.n_subch)
    busy_all = tx.nsb.sum() / (slots * nsub)
    print("\n-- CBR (offered load): all transmitters %.3f (run CSV cbr %.3f), in-area %.3f"
          % (busy_all, float(run.cbr), inarea.nsb.sum() / (slots * nsub)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix", nargs="+")
    ap.add_argument("--radius", type=float, default=1000.0,
                    help="vehicles within this distance of the leader (m)")
    a = ap.parse_args()
    for p in a.prefix:
        report(p, a.radius)


if __name__ == "__main__":
    main()
