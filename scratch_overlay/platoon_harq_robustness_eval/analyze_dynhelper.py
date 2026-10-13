#!/usr/bin/env python3
"""analyze_dynhelper.py -- per-TB helper selection campaign (run_dynhelper.sh).

Usage: analyze_dynhelper.py <results/coop_dyn_sps> [--member 8]

Per cell (scenario, N, traffic), over seeds:
  * tail (member) PRR within the PDB and GPRR (mean member PRR, Kuo's metric), tx/pkt,
    CBR (occupied); paired per-seed differences with 95 % CI vs `none` and vs `s4Nack`;
    blind vs NACK for each selection policy (same seeds), with the cost (tx/pkt);
  * selection: helper changes per s, chosen-member shares, CAM age, delay of the first
    helper copy after the leader's initial transmission;
  * tail delivery by geometry class at the leader's initial transmission (building /
    vehicle per hop, chosen helper) from <tag>_att.csv(.gz);
  * the pre-registered verdicts (README "Per-TB helper selection").
Writes <dir>/analysis_dyn/{dyn_runs.csv, dyn_paired.csv, dyn_geometry.csv, dyn_verdict.txt}.
"""
import argparse
import glob
import os
import re

import numpy as np
import pandas as pd
from scipy import stats

ARMS = ["none", "s4Nack", "s4Blind", "kuoNack", "kuoBlind", "mapNack", "mapBlind"]
G = {0: "B", 1: "V", 2: "L", -1: "?"}
TAG = re.compile(r"(?P<sc>hwy|rbt)_ivd(?P<ivd>\d+)_(?P<tr>\w+?)_n(?P<n>\d+)_s(?P<seed>\d+)$")


def read(path):
    for p in (path, path + ".gz"):
        if os.path.exists(p):
            return pd.read_csv(p)
    return None


def runs(root, member):
    rows = []
    for arm in ARMS:
        for f in glob.glob(os.path.join(root, arm, "*_run.csv")):
            tag = os.path.basename(f)[: -len("_run.csv")]
            m = TAG.match(tag)
            if not m:
                continue
            r = pd.read_csv(f).iloc[0]
            mem = pd.read_csv(os.path.join(root, arm, tag + "_member.csv"))
            tail = mem[mem.member.astype(str) == str(member)].iloc[0]
            rows.append({"arm": arm, "scenario": m["sc"], "nbg": int(m["n"]), "traffic": m["tr"],
                         "seed": int(m["seed"]), "tag": tag,
                         "tail_prr_pdb": float(tail.prr_pdb), "tail_prr": float(tail.prr),
                         "gprr": float(r.prr_mean), "tx_per_pkt": float(r.tx_per_pkt),
                         "cbr_occupied": float(r.get("cbr_occupied", np.nan)),
                         "helper_tx": float(r.get("helper_tx", 0)),
                         "changes_per_s": float(r.get("helper_changes_per_s", 0)),
                         "cam_age_ms": float(r.get("hsel_cam_age_ms_mean", 0)),
                         "relay_after_initial_ms": float(r.get("relay_after_initial_ms_mean", 0)),
                         "relay_after_initial_p95": float(r.get("relay_after_initial_ms_p95", 0)),
                         "relay_by_unchosen": float(r.get("relay_by_unchosen", 0)),
                         **{f"share_m{k}": float(r.get(f"helper_share_m{k}", 0)) for k in range(1, 9)}})
    return pd.DataFrame(rows)


def paired(R, a, b, metric):
    """mean of a, mean of b, paired diff a - b with 95 % CI, wins of a, n"""
    x = R[R.arm == a].set_index("seed")[metric]
    y = R[R.arm == b].set_index("seed")[metric]
    d = (x - y).dropna()
    n = len(d)
    if n < 2:
        return dict(a=x.mean(), b=y.mean(), d=np.nan, ci=np.nan, wins=0, n=n)
    ci = stats.t.ppf(0.975, n - 1) * d.std(ddof=1) / np.sqrt(n)
    return dict(a=x.mean(), b=y.mean(), d=d.mean(), ci=ci, wins=int((d > 0).sum()), n=n)


def tag_of(p):
    if np.isnan(p["d"]):
        return "n/a"
    s = f"{p['d']:+.3f} ±{p['ci']:.3f} ({p['wins']}/{p['n']})"
    return ("**better** " if p["d"] - p["ci"] > 0 else "worse " if p["d"] + p["ci"] < 0 else "n.s. ") + s


def geometry(root, member, cells):
    out = []
    for arm in ARMS:
        for f in glob.glob(os.path.join(root, arm, "*_att.csv*")):
            tag = os.path.basename(f).split("_att.csv")[0]
            m = TAG.match(tag)
            if not m or (m["sc"], int(m["n"]), m["tr"]) not in cells:
                continue
            at = read(os.path.join(root, arm, tag + "_att.csv"))
            run = pd.read_csv(os.path.join(root, arm, tag + "_run.csv")).iloc[0]
            L = at[(at.side == 0) & (at.t >= run.kpi_start) & (at.t < run.kpi_end)]
            first = L[(L.tx_in_tb == 1) & (L.member == member)].set_index("tb")
            ok_l = L[L.member == member].groupby("tb").status.apply(lambda s: (s == 1).any())
            H = at[(at.side == 1) & (at.member == member)]
            ok_h = H.groupby("tb").status.apply(lambda s: (s == 1).any())
            d = pd.DataFrame({"LT": first.geo_tx.map(G), "HT": first.geo_helper.map(G),
                              "LH": first.geo_leader_helper.map(G)})
            d["delivered"] = (ok_l.reindex(d.index).astype("boolean").fillna(False)
                              | ok_h.reindex(d.index).astype("boolean").fillna(False)).astype(bool)
            d["arm"], d["cell"] = arm, f"{m['sc']} N {m['n']} {m['tr']}"
            out.append(d)
    if not out:
        return pd.DataFrame()
    D = pd.concat(out)
    D["geo"] = "LT=" + D.LT + " LH=" + D.LH + " HT=" + D.HT
    return D


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--member", type=int, default=8)
    a = ap.parse_args()
    out = os.path.join(a.root, "analysis_dyn")
    os.makedirs(out, exist_ok=True)
    R = runs(a.root, a.member)
    if R.empty:
        raise SystemExit("no runs")
    R.to_csv(os.path.join(out, "dyn_runs.csv"), index=False)
    lines = []
    P = []
    cells = sorted(R.groupby(["scenario", "nbg", "traffic"]).groups)
    pd.set_option("display.width", 220)
    for sc, n, tr in cells:
        C = R[(R.scenario == sc) & (R.nbg == n) & (R.traffic == tr)]
        lines.append(f"\n=== {sc} N {n} {tr}: seeds per arm {C.groupby('arm').size().to_dict()}")
        mean = C.groupby("arm")[["tail_prr_pdb", "gprr", "tx_per_pkt", "cbr_occupied",
                                 "changes_per_s", "cam_age_ms", "relay_after_initial_ms",
                                 "relay_after_initial_p95", "relay_by_unchosen"]].mean()
        lines.append(mean.reindex([x for x in ARMS if x in mean.index]).round(3).to_string())
        sh = C.groupby("arm")[[f"share_m{k}" for k in range(1, 9)]].mean()
        lines.append("chosen-member shares:\n" + sh.reindex([x for x in ARMS if x in sh.index]).round(2).to_string())
        for metric in ("tail_prr_pdb", "gprr"):
            lines.append(f"-- {metric}: paired (arm - reference)")
            for arm in ARMS[1:]:
                for ref in ("none", "s4Nack"):
                    if arm == ref:
                        continue
                    p = paired(C, arm, ref, metric)
                    P.append({"scenario": sc, "nbg": n, "traffic": tr, "metric": metric,
                              "arm": arm, "ref": ref, **p})
                    lines.append(f"  {arm:9s} vs {ref:7s}: {tag_of(p)}")
            for sel in ("s4", "kuo", "map"):
                p = paired(C, sel + "Nack", sel + "Blind", metric)
                c = paired(C, sel + "Nack", sel + "Blind", "tx_per_pkt")
                P.append({"scenario": sc, "nbg": n, "traffic": tr, "metric": metric,
                          "arm": sel + "Nack", "ref": sel + "Blind", **p})
                lines.append(f"  {sel + 'Nack':9s} vs {sel + 'Blind':8s}: {tag_of(p)}   "
                             f"tx/pkt {c['a']:.2f} vs {c['b']:.2f}")
    PD = pd.DataFrame(P)
    PD.to_csv(os.path.join(out, "dyn_paired.csv"), index=False)

    # pre-registered verdicts
    v = ["Pre-registered verdicts (tail prr_pdb, paired CI of the per-seed difference):"]
    def get(sc, n, tr, metric, arm, ref):
        x = PD[(PD.scenario == sc) & (PD.nbg == n) & (PD.traffic == tr) & (PD.metric == metric)
               & (PD.arm == arm) & (PD.ref == ref)]
        return x.iloc[0] if len(x) else None
    for arm in ("mapNack", "kuoNack"):
        p = get("rbt", 0, "periodic1", "tail_prr_pdb", arm, "s4Nack")
        if p is not None:
            ok = p.d - p.ci > 0
            v.append(f"1. dynamic selection pays ({arm} vs s4Nack, rbt N 0 periodic1): "
                     f"{'YES' if ok else 'NO'}  {tag_of(p)}")
    for sel in ("s4", "kuo", "map"):
        for n in (0, 45, 60):
            p = get("rbt", n, "periodic1", "tail_prr_pdb", sel + "Nack", sel + "Blind")
            c = R[(R.scenario == "rbt") & (R.nbg == n) & (R.traffic == "periodic1")]
            if p is None or c.empty:
                continue
            txn = c[c.arm == sel + "Nack"].tx_per_pkt.mean()
            txb = c[c.arm == sel + "Blind"].tx_per_pkt.mean()
            ok = (p.d + p.ci >= 0) and txn < txb
            v.append(f"2. feedback pays over blind ({sel}, rbt N {n} periodic1): {'YES' if ok else 'NO'}"
                     f"  {tag_of(p)}, tx/pkt {txn:.2f} vs {txb:.2f}")
    for arm in ("mapNack", "kuoNack", "s4Nack"):
        g = []
        for n in (0, 45, 60):
            p = get("rbt", n, "periodic1", "tail_prr_pdb", arm, "none")
            if p is not None:
                g.append(f"N {n}: {p.d:+.3f}")
        if g:
            v.append(f"3. gain vs none by load ({arm}, periodic1): " + ", ".join(g))
    lines.append("\n" + "\n".join(v))
    with open(os.path.join(out, "dyn_verdict.txt"), "w") as f:
        f.write("\n".join(v) + "\n")

    # geometry classes (periodic1 cells)
    D = geometry(a.root, a.member, {c for c in cells if c[2] == "periodic1"})
    if not D.empty:
        T = (D.groupby(["cell", "LT", "arm"]).delivered.agg(["mean", "size"]).unstack("arm"))
        lines.append("\nTail delivery by leader->tail geometry (B building, V vehicles), periodic1:")
        lines.append(T["mean"].reindex(columns=[x for x in ARMS if x in T["mean"].columns]).round(3).to_string())
        D.groupby(["cell", "geo", "arm"]).delivered.agg(["mean", "size"]).reset_index().to_csv(
            os.path.join(out, "dyn_geometry.csv"), index=False)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
