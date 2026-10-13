#!/usr/bin/env python3
"""analyze_helper_geom.py -- when does the helper's copy reach a member the leader missed?

Usage: analyze_helper_geom.py <dir> [--member 8] [--helper 4]
  <dir> holds runs <arm-tag>_att.csv / _pkt.csv / _run.csv written with --writeAtt
  (tags h_<arm>_s<seed>, as made by the session's pilot runner).

Per leader TB (one per packet for periodic traffic) and the chosen member:
  geometry at the leader's initial transmission (deterministic, from positions:
  B = a building blocks the segment, V = a vehicle box does, L = line of sight)
    LT  leader -> member,  LH leader -> helper,  HT helper -> member
  outcome: delivered by a leader transmission, recovered by a helper copy (every leader
  transmission of the TB failed at the member, a helper copy decoded), or lost.
Reported: per geometry class, the member's delivery ratio for `none` and for every helper
arm (pooled over seeds; geometry depends on the trace only, so the classes match across
arms), the share recovered by the helper, the helper's own decoding ratio of the
leader's TB, and the class's share of the TBs.
"""
import argparse
import glob
import os
import re

import numpy as np
import pandas as pd

G = {0: "B", 1: "V", 2: "L", -1: "?"}


def per_tb(prefix, member, helper):
    at = pd.read_csv(prefix + "_att.csv")
    run = pd.read_csv(prefix + "_run.csv").iloc[0]
    k0, k1 = float(run.kpi_start), float(run.kpi_end)
    lead = at[at.side == 0]
    first = lead[(lead.tx_in_tb == 1) & (lead.member == member)]
    first = first[(first.t >= k0) & (first.t < k1)].set_index("tb")
    lh_dec = lead[lead.member == helper].groupby("tb").status.apply(lambda s: (s == 1).any())
    m_lead = lead[lead.member == member].groupby("tb").status.apply(lambda s: (s == 1).any())
    hel = at[(at.side == 1) & (at.member == member)]
    m_help = hel.groupby("tb").status.apply(lambda s: (s == 1).any())
    n_help = hel.groupby("tb").size()
    d = pd.DataFrame({
        "t": first.t,
        "LT": first.geo_tx.map(G), "LH": first.geo_leader_helper.map(G),
        "HT": first.geo_helper.map(G), "cond_LT": first.cond, "dist_LT": first.dist_m,
    })
    d["leader_ok"] = m_lead.reindex(d.index).astype("boolean").fillna(False).astype(bool)
    d["helper_ok"] = m_help.reindex(d.index).astype("boolean").fillna(False).astype(bool)
    d["helper_tx"] = n_help.reindex(d.index).fillna(0)
    d["helper_decoded"] = lh_dec.reindex(d.index).astype("boolean").fillna(False).astype(bool)
    d["delivered"] = d.leader_ok | d.helper_ok
    d["recovered"] = (~d.leader_ok) & d.helper_ok
    return d


def candidates(directory, member, pattern):
    """Every member as a candidate helper: for the leader's initial transmissions where a
    building blocks leader -> member, which members have a building-free path on both
    hops (leader -> k and k -> member)? Needs the geo_member_tail column (last member)."""
    rows = []
    for f in sorted(glob.glob(os.path.join(directory, pattern))):
        at = pd.read_csv(f)
        if "geo_member_tail" not in at:
            continue
        run = pd.read_csv(f[: -len("_att.csv")] + "_run.csv").iloc[0]
        L = at[(at.side == 0) & (at.tx_in_tb == 1) & (at.t >= run.kpi_start) & (at.t < run.kpi_end)]
        piv_lk = L.pivot_table(index="tb", columns="member", values="geo_tx")
        piv_kt = L.pivot_table(index="tb", columns="member", values="geo_member_tail")
        blocked = piv_lk[member] == 0
        for tb in piv_lk.index[blocked]:
            ok = [k for k in range(1, member)
                  if piv_lk.loc[tb, k] != 0 and piv_kt.loc[tb, k] != 0]
            rows.append({"run": os.path.basename(f), "tb": tb, "n_ok": len(ok),
                         "ok": ok})
    C = pd.DataFrame(rows)
    if C.empty:
        print("no geo_member_tail data")
        return
    n_tb = len(C)
    print(f"\nCandidate helpers, TBs with leader -> member {member} blocked by a building: {n_tb}")
    print(f"  some member has a building-free path on both hops: {(C.n_ok > 0).mean():.3f}")
    cnt = {k: sum(k in o for o in C.ok) / n_tb for k in range(1, member)}
    print("  share of these TBs where member k is such a helper:",
          {k: round(v, 3) for k, v in cnt.items()})
    print("  number of such members per TB:", C.n_ok.value_counts(normalize=True).sort_index().round(3).to_dict())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--member", type=int, default=8)
    ap.add_argument("--helper", type=int, default=4)
    ap.add_argument("--candidates", default=None,
                    help="glob of att files with geo_member_tail (e.g. 'g_none_*_att.csv'): "
                         "only the candidate-helper analysis")
    a = ap.parse_args()
    if a.candidates:
        candidates(a.dir, a.member, a.candidates)
        return
    rows = []
    for f in sorted(glob.glob(os.path.join(a.dir, "h_*_att.csv"))):
        tag = os.path.basename(f)[: -len("_att.csv")]
        mt = re.match(r"h_(\w+?)_s(\d+)$", tag)
        if not mt:
            continue
        d = per_tb(os.path.join(a.dir, tag), a.member, a.helper)
        d["arm"], d["seed"] = mt.group(1), int(mt.group(2))
        rows.append(d)
    D = pd.concat(rows)
    D["geo"] = "LT=" + D.LT + " LH=" + D.LH + " HT=" + D.HT
    arms = [x for x in ["none", "leaderRes", "ownRes", "split", "additive"] if x in D.arm.unique()]
    print(f"member {a.member}, helper {a.helper}; seeds {sorted(D.seed.unique())}; "
          f"TBs per arm {D.groupby('arm').size().to_dict()}")

    def table(by):
        out = []
        for key, x in D.groupby(by):
            r = {by if isinstance(by, str) else "class": key,
                 "share": len(x[x.arm == "none"]) / max(1, (D.arm == "none").sum())}
            for arm in arms:
                y = x[x.arm == arm]
                r[f"{arm}"] = y.delivered.mean() if len(y) else np.nan
            for arm in arms[1:]:
                y = x[x.arm == arm]
                r[f"rec_{arm}"] = y.recovered.mean() if len(y) else np.nan
            y = x[x.arm == "leaderRes"] if "leaderRes" in arms else x[x.arm != "none"]
            r["helper_decodes"] = y.helper_decoded.mean() if len(y) else np.nan
            r["n_none"] = (x.arm == "none").sum()
            out.append(r)
        T = pd.DataFrame(out).sort_values("share", ascending=False)
        return T

    pd.set_option("display.width", 250)
    print("\nDelivery ratio of the member per arm, by leader->member geometry "
          "(rec_* = share recovered by the helper's copy):")
    print(table("LT").round(3).to_string(index=False))
    print("\n... by the full geometry (LT, LH, HT), classes with >= 1 % of the TBs:")
    T = table("geo")
    print(T[T.share >= 0.01].round(3).to_string(index=False))
    # the hypothesis: helper with LOS to both the leader and the member
    D["helper_LOS_both"] = (D.LH == "L") & (D.HT == "L")
    print("\nLeader->member blocked by a building (LT = B): helper with LOS to both vs not")
    x = D[D.LT == "B"]
    print(table_hyp(x, arms))
    print("\nLeader->member blocked by vehicles only (LT = V):")
    print(table_hyp(D[D.LT == "V"], arms))
    # gain per seed (paired) in LT = B, helper LOS both
    print("\nPer seed, LT = B: delivery none vs leaderRes / additive, by helper_LOS_both")
    for flag in [True, False]:
        y = D[(D.LT == "B") & (D.helper_LOS_both == flag)]
        p = y.groupby(["seed", "arm"]).delivered.mean().unstack()
        cols = [c for c in ["none", "leaderRes", "additive"] if c in p]
        print(f"  helper LOS both = {flag}:")
        print(p[cols].round(2).to_string())


def table_hyp(x, arms):
    out = []
    for flag, y in x.groupby("helper_LOS_both"):
        r = {"helper LOS to leader and member": flag,
             "share of these TBs": (y.arm == "none").sum() / max(1, (x.arm == "none").sum())}
        for arm in arms:
            z = y[y.arm == arm]
            r[arm] = z.delivered.mean() if len(z) else np.nan
        for arm in arms[1:]:
            z = y[y.arm == arm]
            r["gain_" + arm] = (z.delivered.mean() - y[y.arm == "none"].delivered.mean()) if len(z) else np.nan
        z = y[y.arm == "leaderRes"] if "leaderRes" in arms else y[y.arm != "none"]
        r["helper decodes"] = z.helper_decoded.mean() if len(z) else np.nan
        out.append(r)
    return pd.DataFrame(out).round(3).to_string(index=False)


if __name__ == "__main__":
    main()
