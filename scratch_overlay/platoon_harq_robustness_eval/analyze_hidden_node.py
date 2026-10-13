#!/usr/bin/env python3
"""Analysis of the hidden-node test (README "Hidden-node test"; runs of run_hidden_node.py).

  python3 analyze_hidden_node.py stageA --results results/hidden_hn
  python3 analyze_hidden_node.py full   --results results/hidden_hn

Inputs per run (arm = folder): <tag>_run.csv, <tag>_member.csv, <tag>_use.csv(.gz),
<tag>_sel.csv(.gz), <tag>_pkt.csv(.gz) (full only).

Definitions and the pre-registered rules are in the README; nothing here may be tuned
after the results. Cell = scenario x IVD x N x helper x threshold. Success of a use =
members that still needed the TB and decoded it / members that still needed it
(member level, pooled over uses). CIs: 95 % cluster bootstrap over runs (2000 draws,
seed 1) for pooled use / packet ratios; paired t-intervals over seeds for run-level
differences of tail (member 8) prr_pdb.

stageA writes <out>/stageA_*.csv, stageA_verdict.txt, figA1-figA2;
full writes <out>/obsolescence_*.csv, prr_vs_gap_*.csv, regression.csv, iuc_cells.csv,
verdict.txt, figh1-figh5.
"""
import argparse
import glob
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
TAG_RE = re.compile(r"_h(\d+)_th(\d+)_s(\d+)$")
KEYS = ["scenario", "ivd", "nbg", "helper", "thr", "seed"]
CELL = ["scenario", "ivd", "nbg", "helper", "thr"]
CATS = ["hidden", "stale", "unannounced", "leader_only"]
# fixed before any result (README)
GO_HIDDEN = 0.10
GO_LOSS = 0.10
GO_MIN_USES = 30
IUC_GAIN = 0.03
AGE_BINS = [0.0, 500.0, 1000.0, 2000.0, 5000.0, np.inf]  # ms
AGE_LABELS = ["0-0.5 s", "0.5-1 s", "1-2 s", "2-5 s", "> 5 s"]
GH_BINS = [-np.inf, 0.0, 0.05, 0.10, 0.20, np.inf]  # (lo, hi]; first bin = exactly 0
GH_LABELS = ["0", "0-5 %", "5-10 %", "10-20 %", "> 20 %"]
DIST_BINS = [0.0, 50.0, 100.0, 150.0, 250.0, 400.0, np.inf]
DIST_LABELS = ["0-50", "50-100", "100-150", "150-250", "250-400", "> 400 m"]
BOOT = 2000


def read_csv_any(path):
    p = Path(path)
    if p.exists():
        return pd.read_csv(p)
    if Path(str(p) + ".gz").exists():
        return pd.read_csv(str(p) + ".gz")
    return None


def load(root):
    """runs (one row per run, tail prr_pdb), uses, selections"""
    runs, uses, sels = [], [], []
    for f in sorted(glob.glob(str(Path(root) / "*" / "*_run.csv"))):
        arm = Path(f).parent.name
        tag = Path(f).name[: -len("_run.csv")]
        m = TAG_RE.search(tag)
        if not m:
            continue
        r = pd.read_csv(f).iloc[0].to_dict()
        key = {"arm": arm, "scenario": r["scenario"], "ivd": int(float(r["ivd_m"])),
               "nbg": int(r["nbg"]), "helper": int(m.group(1)), "thr": -int(m.group(2)),
               "seed": int(m.group(3))}
        mem = read_csv_any(f.replace("_run.csv", "_member.csv"))
        mem = mem[pd.to_numeric(mem["member"], errors="coerce").notna()].copy()
        mem["member"] = mem["member"].astype(int)
        tailm = int(mem["member"].max())
        row = dict(key)
        row.update(r)
        row.update({"helper": key["helper"], "seed": key["seed"]})
        row["prr_pdb_tail"] = float(mem.loc[mem["member"] == tailm, "prr_pdb"].iloc[0])
        row["prr_tail"] = float(mem.loc[mem["member"] == tailm, "prr"].iloc[0])
        row["prr_pdb_mean"] = float(mem["prr_pdb"].mean())
        row["run_id"] = f"{arm}/{tag}"
        runs.append(row)
        u = read_csv_any(f.replace("_run.csv", "_use.csv"))
        if u is not None and len(u):
            for k, v in key.items():
                u[k] = v
            u["run_id"] = row["run_id"]
            uses.append(u)
        s = read_csv_any(f.replace("_run.csv", "_sel.csv"))
        if s is not None and len(s):
            for k, v in key.items():
                s[k] = v
            s["run_id"] = row["run_id"]
            sels.append(s)
    if not runs:
        sys.exit(f"no hidden-node runs in {root}")
    R = pd.DataFrame(runs)
    U = pd.concat(uses, ignore_index=True) if uses else pd.DataFrame()
    S = pd.concat(sels, ignore_index=True) if sels else pd.DataFrame()
    if len(U):
        U = U[U["sel_id"] >= 0].copy()  # linked uses (the unlinked are a must-0 check)
        U["hidden"] = (U["category"] == "hidden").astype(int)
    return R, U, S


def boot_ratio(df, num, den, rng, n=BOOT):
    """pooled ratio sum(num)/sum(den) with a 95 % cluster bootstrap over run_id"""
    g = df.groupby("run_id")[[num, den]].sum()
    tot = g[den].sum()
    if tot == 0:
        return np.nan, np.nan, np.nan
    est = g[num].sum() / tot
    if len(g) < 2:
        return est, np.nan, np.nan
    a, b = g[num].to_numpy(float), g[den].to_numpy(float)
    idx = rng.integers(0, len(g), size=(n, len(g)))
    bn, bd = a[idx].sum(1), b[idx].sum(1)
    ok = bd > 0
    lo, hi = np.percentile(bn[ok] / bd[ok], [2.5, 97.5])
    return est, lo, hi


def boot_diff(d1, d2, num, den, rng, n=BOOT):
    """difference of two pooled ratios (d1 - d2), runs resampled jointly (same runs)"""
    g1 = d1.groupby("run_id")[[num, den]].sum()
    g2 = d2.groupby("run_id")[[num, den]].sum()
    g = g1.join(g2, lsuffix="_1", rsuffix="_2", how="outer").fillna(0.0)
    if g[f"{den}_1"].sum() == 0 or g[f"{den}_2"].sum() == 0:
        return np.nan, np.nan, np.nan
    est = g[f"{num}_1"].sum() / g[f"{den}_1"].sum() - g[f"{num}_2"].sum() / g[f"{den}_2"].sum()
    if len(g) < 2:
        return est, np.nan, np.nan
    M = g.to_numpy(float)
    c = {k: i for i, k in enumerate(g.columns)}
    idx = rng.integers(0, len(g), size=(n, len(g)))
    s = M[idx].sum(1)
    ok = (s[:, c[f"{den}_1"]] > 0) & (s[:, c[f"{den}_2"]] > 0)
    v = (s[ok, c[f"{num}_1"]] / s[ok, c[f"{den}_1"]]
         - s[ok, c[f"{num}_2"]] / s[ok, c[f"{den}_2"]])
    lo, hi = np.percentile(v, [2.5, 97.5])
    return est, lo, hi


def paired(R, arm1, arm2, metric="prr_pdb_tail", none_helper=True):
    """per cell: mean of arm1 - arm2 over seeds with a paired t-interval; `none` is
    matched on scenario x IVD x N x thr x seed (it runs once, helper 4)"""
    a = R[R["arm"] == arm1]
    b = R[R["arm"] == arm2]
    on = ["scenario", "ivd", "nbg", "thr", "seed"] + ([] if arm2 == "none" else ["helper"])
    m = a.merge(b[on + [metric]], on=on, suffixes=("_1", "_2"))
    m["d"] = m[f"{metric}_1"] - m[f"{metric}_2"]
    rows = []
    for cell, g in m.groupby(CELL):
        d = g["d"].to_numpy(float)
        n = len(d)
        h = stats.t.ppf(0.975, n - 1) * d.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
        rows.append(dict(zip(CELL, cell), comparison=f"{arm1} - {arm2}", n=n,
                         mean_1=g[f"{metric}_1"].mean(), mean_2=g[f"{metric}_2"].mean(),
                         d=d.mean(), ci_lo=d.mean() - h, ci_hi=d.mean() + h,
                         wins=int((d > 0).sum())))
    return pd.DataFrame(rows)


def use_table(U, rng, by):
    """per group: uses, category shares, veto rate, success hidden / other,
    vetoed / not vetoed (with CIs), overlap and rear-collision rates"""
    rows = []
    for key, g in U.groupby(by):
        key = key if isinstance(key, tuple) else (key,)
        r = dict(zip(by, key))
        r["uses"] = len(g)
        for c in CATS:
            r[f"share_{c}"] = (g["category"] == c).mean()
        r["share_hidden_lo"], r["share_hidden_hi"] = boot_ratio(
            g.assign(one=1), "hidden", "one", rng)[1:]
        r["veto_rate"] = g["veto"].mean()
        r["n_veto"] = int(g["veto"].sum())
        r["n_noveto"] = int((g["veto"] == 0).sum())
        r["succ"], r["succ_lo"], r["succ_hi"] = boot_ratio(g, "n_decoded_needing",
                                                          "n_needing", rng)
        for lab, sub in (("hidden", g[g["hidden"] == 1]), ("other", g[g["hidden"] == 0]),
                         ("veto", g[g["veto"] == 1]), ("noveto", g[g["veto"] == 0])):
            r[f"succ_{lab}"], r[f"succ_{lab}_lo"], r[f"succ_{lab}_hi"] = boot_ratio(
                sub, "n_decoded_needing", "n_needing", rng)
        r["loss_veto"], r["loss_veto_lo"], r["loss_veto_hi"] = boot_diff(
            g[g["veto"] == 0], g[g["veto"] == 1], "n_decoded_needing", "n_needing", rng)
        r["overlap_rate"] = g["overlap"].mean()
        r["coll_rear_rate"] = g["coll_rear"].mean()
        r["gh_mean_at_use"] = g["gh"].mean()
        rows.append(r)
    return pd.DataFrame(rows)


def sel_table(S, by):
    G = S[(S["granted"] == 1) & (S["in_kpi"] == 1)].copy()
    rows = []
    for key, g in G.groupby(by):
        key = key if isinstance(key, tuple) else (key,)
        r = dict(zip(by, key))
        gh = g["gh"].to_numpy(float)
        r.update(selections=len(g), gh_mean=gh.mean(), gh_median=np.median(gh),
                 gh_p90=np.percentile(gh, 90), gl_mean=g["gl"].mean(),
                 div_median=g["divergence"].median(),
                 relax_steps_mean=g["relax_steps"].mean(),
                 relax_extra_share=(g["relax_steps"] > g["relax_steps_own"]).mean())
        cut = pd.cut(g["gh"], GH_BINS, labels=GH_LABELS)
        for lab in GH_LABELS:
            r[f"gh_bin_{lab}"] = (cut == lab).mean()
        rows.append(r)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- stage A
def stage_a(a):
    root = Path(a.results)
    out = Path(a.out or root / "analysis_A")
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    R, U, S = load(root)
    lines = [f"Hidden-node test, stage A ({root}; {len(R)} runs)", ""]
    bad = R[[c for c in R.columns if c.startswith("hn_chk_")]].sum()
    lines.append("checks (must be 0, summed over runs): " +
                 ", ".join(f"{k}={int(v)}" for k, v in bad.items()))
    lines.append("")
    hu = U[U["role"] == 1]
    lu = U[U["role"] == 0]
    T = use_table(hu, rng, ["arm"] + CELL)
    T.to_csv(out / "stageA_helper_uses.csv", index=False)
    TL = use_table(lu, rng, ["arm"] + CELL)
    TL.to_csv(out / "stageA_leader_uses.csv", index=False)
    G = sel_table(S, ["arm"] + CELL)
    G.to_csv(out / "stageA_gh.csv", index=False)
    P = pd.concat([paired(R, x, y) for x, y in
                   (("leaderRes", "none"), ("ownRes", "none"), ("leaderResIuc", "none"),
                    ("leaderRes", "ownRes"), ("leaderResIuc", "leaderRes"))],
                  ignore_index=True)
    P.to_csv(out / "stageA_tail_paired.csv", index=False)
    tail = (R.groupby(["arm"] + CELL)["prr_pdb_tail"].agg(["mean", "count"]).reset_index())
    tail.to_csv(out / "stageA_tail.csv", index=False)

    # go / no-go (pre-registered): leaderRes helper uses, per cell
    go_rows = []
    for _, r in T[T["arm"] == "leaderRes"].iterrows():
        enough = r["n_veto"] >= GO_MIN_USES and r["n_noveto"] >= GO_MIN_USES
        loss = r["succ_noveto"] - r["succ_veto"] if enough else np.nan
        go = bool(enough and r["share_hidden"] >= GO_HIDDEN and loss >= GO_LOSS)
        go_rows.append({**{k: r[k] for k in CELL}, "uses": r["uses"],
                        "share_hidden": r["share_hidden"], "n_veto": r["n_veto"],
                        "n_noveto": r["n_noveto"], "succ_veto": r["succ_veto"],
                        "succ_noveto": r["succ_noveto"], "loss_veto": loss, "go": go})
    GO = pd.DataFrame(go_rows)
    GO.to_csv(out / "stageA_go.csv", index=False)
    any_go = bool(len(GO) and GO["go"].any())
    lines.append("Go rule (leaderRes helper copies, per cell, seeds pooled): hidden >= "
                 f"{GO_HIDDEN:.0%} and success(vetoed) <= success(not vetoed) - "
                 f"{GO_LOSS * 100:.0f} points, with >= {GO_MIN_USES} vetoed and not vetoed uses")
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        lines.append(GO.round(3).to_string(index=False))
    lines.append("")
    lines.append("VERDICT: " + ("GO -- hidden-node effect present in "
                                + ", ".join(f"{r.scenario} IVD {r.ivd} N {r.nbg} h{r.helper} "
                                            f"{r.thr} dBm" for r in GO[GO["go"]].itertuples())
                                if any_go else
                                "NO-GO -- hidden-node effect not reproduced in this setting"))
    lines.append("")
    lines.append("Helper copies (role 1) per arm and cell:")
    cols = ["arm"] + CELL + ["uses", "share_hidden", "share_stale", "share_unannounced",
                             "share_leader_only", "veto_rate", "succ", "succ_hidden",
                             "succ_other", "succ_veto", "succ_noveto", "overlap_rate"]
    with pd.option_context("display.width", 250, "display.max_columns", 40):
        lines.append(T[cols].round(3).to_string(index=False) if len(T) else "(none)")
        lines.append("")
        lines.append("Leader transmissions (role 0, reference):")
        lines.append(TL[cols].round(3).to_string(index=False) if len(TL) else "(none)")
        lines.append("")
        lines.append("G_H of the leader's selections:")
        lines.append(G[["arm"] + CELL + ["selections", "gh_mean", "gh_median", "gh_p90",
                                         "gl_mean", "relax_extra_share"]]
                     .round(3).to_string(index=False) if len(G) else "(none)")
        lines.append("")
        lines.append("Tail prr_pdb, paired (mean over seeds, 95 % paired t-CI):")
        lines.append(P.round(3).to_string(index=False) if len(P) else "(none)")
    (out / "stageA_verdict.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    figures_a(T, G, out)


def figures_a(T, G, out):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    if len(T):
        sub = T[T["arm"].isin(["leaderRes", "leaderResIuc"])].copy()
        sub["lab"] = (sub["arm"].str.replace("leaderRes", "LR") + " " + sub["scenario"].str[:3]
                      + " " + sub["ivd"].astype(str) + "m h" + sub["helper"].astype(str) + " "
                      + sub["thr"].astype(str))
        fig, ax = plt.subplots(1, 2, figsize=(13, 0.35 * len(sub) + 2), sharey=True)
        bottom = np.zeros(len(sub))
        for c, col in zip(CATS, ["#c0392b", "#7f8c8d", "#2980b9", "#f39c12"]):
            ax[0].barh(sub["lab"], sub[f"share_{c}"], left=bottom, color=col, label=c)
            bottom += sub[f"share_{c}"].to_numpy()
        ax[0].axvline(GO_HIDDEN, color="k", ls=":", lw=1)
        ax[0].set_xlabel("share of helper copies")
        ax[0].legend(fontsize=7, loc="lower right")
        y = np.arange(len(sub))
        ax[1].errorbar(sub["succ_veto"], y + 0.15,
                       xerr=[sub["succ_veto"] - sub["succ_veto_lo"],
                             sub["succ_veto_hi"] - sub["succ_veto"]], fmt="o", ms=4,
                       color="#c0392b", label="vetoed (in H \\ L)")
        ax[1].errorbar(sub["succ_noveto"], y - 0.15,
                       xerr=[sub["succ_noveto"] - sub["succ_noveto_lo"],
                             sub["succ_noveto_hi"] - sub["succ_noveto"]], fmt="s", ms=4,
                       color="#2980b9", label="not vetoed")
        ax[1].set_xlabel("helper-copy success (members that needed it)")
        ax[1].legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(out / "figA1_categories_success.png", dpi=130)
        plt.close(fig)
    if len(G):
        fig, ax = plt.subplots(figsize=(8, 0.3 * len(G) + 2))
        lab = (G["arm"] + " " + G["scenario"].str[:3] + " " + G["ivd"].astype(str) + "m h"
               + G["helper"].astype(str) + " " + G["thr"].astype(str))
        ax.barh(lab, G["gh_mean"], color="#16a085")
        ax.set_xlabel("mean G_H = |H \\ L| / |candidates| (leader selections)")
        fig.tight_layout()
        fig.savefig(out / "figA2_gh.png", dpi=130)
        plt.close(fig)


# ----------------------------------------------------------------------------- full
def tail_by_gh(root, R, U, rng):
    """tail prr_pdb per packet by the G_H bin / veto of the selection behind the
    leader's (initial) transmission of that packet (leaderRes runs)"""
    rows = []
    for rid in R.loc[R["arm"] == "leaderRes", "run_id"]:
        pkt = read_csv_any(Path(root) / f"{rid}_pkt.csv")
        if pkt is None:
            continue
        tailm = pkt["member"].max()
        pdb_ms = 100.0
        t = pkt[pkt["member"] == tailm][["seq", "rx", "delay_ms"]].copy()
        t["ok"] = ((t["rx"] == 1) & (t["delay_ms"] <= pdb_ms + 1e-6)).astype(int)
        lu = U[(U["run_id"] == rid) & (U["role"] == 0)].drop_duplicates("seq")
        m = t.merge(lu[["seq", "gh", "veto"]], on="seq", how="inner")
        m["run_id"] = rid
        m["one"] = 1
        rows.append(m)
    if not rows:
        return pd.DataFrame()
    M = pd.concat(rows, ignore_index=True)
    M["gh_bin"] = pd.cut(M["gh"], GH_BINS, labels=GH_LABELS)
    out = []
    for lab in GH_LABELS:
        g = M[M["gh_bin"] == lab]
        e, lo, hi = boot_ratio(g, "ok", "one", rng)
        out.append({"by": "gh_bin", "bin": lab, "packets": len(g), "tail_prr_pdb": e,
                    "ci_lo": lo, "ci_hi": hi})
    for v in (0, 1):
        g = M[M["veto"] == v]
        e, lo, hi = boot_ratio(g, "ok", "one", rng)
        out.append({"by": "veto", "bin": v, "packets": len(g), "tail_prr_pdb": e,
                    "ci_lo": lo, "ci_hi": hi})
    return pd.DataFrame(out)


def regression(R):
    rows = []
    lr = R[R["arm"] == "leaderRes"][CELL + ["seed", "prr_pdb_tail", "hn_gh_mean"]]
    for other, sign, name in (("ownRes", 1, "leaderRes - ownRes"),
                              ("leaderResIuc", -1, "leaderResIuc - leaderRes")):
        o = R[R["arm"] == other][CELL + ["seed", "prr_pdb_tail"]]
        m = lr.merge(o, on=CELL + ["seed"], suffixes=("_lr", "_o"))
        m["d"] = sign * (m["prr_pdb_tail_lr"] - m["prr_pdb_tail_o"])
        for scope, g in [("all", m)] + list(m.groupby("scenario")):
            if len(g) < 3 or g["hn_gh_mean"].nunique() < 2:
                continue
            fit = stats.linregress(g["hn_gh_mean"], g["d"])
            h = stats.t.ppf(0.975, len(g) - 2) * fit.stderr
            rows.append({"comparison": name, "scope": scope, "runs": len(g),
                         "slope": fit.slope, "slope_lo": fit.slope - h,
                         "slope_hi": fit.slope + h, "intercept": fit.intercept,
                         "r": fit.rvalue})
    return pd.DataFrame(rows)


def full(a):
    root = Path(a.results)
    out = Path(a.out or root / "analysis")
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    R, U, S = load(root)
    lines = [f"Hidden-node test, full analysis ({root}; {len(R)} runs)", ""]
    bad = R[[c for c in R.columns if c.startswith("hn_chk_")]].sum()
    lines.append("checks (must be 0): " + ", ".join(f"{k}={int(v)}" for k, v in bad.items()))
    U = U.copy()
    U["age_bin"] = pd.cut(U["age_ms"], AGE_BINS, labels=AGE_LABELS, right=False)
    U["dist_bin"] = pd.cut(U["d_leader_helper_m"], DIST_BINS, labels=DIST_LABELS, right=False)
    U["gh_bin"] = pd.cut(U["gh"], GH_BINS, labels=GH_LABELS)
    arms_h = ["leaderRes", "leaderResIuc"]
    # obsolescence curves: helper copies (leaderRes) and the leader's own uses (reference)
    curves = []
    for dim in ["nbg", "dist_bin", "thr", "scenario", "age_bin", "ivd", "helper"]:
        for role, arms in ((1, arms_h), (0, ["none", "leaderRes"])):
            sub = U[(U["role"] == role) & (U["arm"].isin(arms))]
            if not len(sub):
                continue
            t = use_table(sub, rng, ["arm", dim]).rename(columns={dim: "value"})
            t.insert(0, "dimension", dim)
            t.insert(1, "role", "helper" if role else "leader")
            curves.append(t)
    C = pd.concat(curves, ignore_index=True) if curves else pd.DataFrame()
    C.to_csv(out / "obsolescence_curves.csv", index=False)
    # PRR vs gap
    pv = []
    for arm in arms_h:
        sub = U[(U["role"] == 1) & (U["arm"] == arm)]
        if len(sub):
            t = use_table(sub, rng, ["gh_bin"]).rename(columns={"gh_bin": "value"})
            t.insert(0, "arm", arm)
            pv.append(t)
    PV = pd.concat(pv, ignore_index=True) if pv else pd.DataFrame()
    PV.to_csv(out / "prr_vs_gap_success.csv", index=False)
    TG = tail_by_gh(root, R, U, rng)
    TG.to_csv(out / "prr_vs_gap_tail.csv", index=False)
    REG = regression(R)
    REG.to_csv(out / "regression.csv", index=False)
    # decision rule 1: hidden-node effect reproduced (per cell, leaderRes helper copies)
    T = use_table(U[(U["role"] == 1) & (U["arm"] == "leaderRes")], rng, CELL)
    T.to_csv(out / "cells_helper_uses.csv", index=False)
    rep = T[(T["share_hidden"] >= GO_HIDDEN) & (T["loss_veto"] >= GO_LOSS)
            & (T["loss_veto_lo"] > 0)]
    # decision rule 2: IUC upper bound per cell, paired vs leaderRes
    P = paired(R, "leaderResIuc", "leaderRes")
    cost = (R[R["arm"].isin(["leaderRes", "leaderResIuc"])]
            .groupby(["arm"] + CELL)[["relax_extra_iuc_share", "tx_per_pkt", "cbr_occupied",
                                      "hn_relax_steps_mean"]].mean().reset_index())
    P.to_csv(out / "iuc_cells.csv", index=False)
    cost.to_csv(out / "iuc_cost.csv", index=False)
    realistic = P[P["nbg"] > 0]
    iuc = realistic[(realistic["d"] >= IUC_GAIN) & (realistic["ci_lo"] > 0)]
    lines += ["", f"Rule 1 (hidden >= {GO_HIDDEN:.0%} and vetoed copies lose >= "
              f"{GO_LOSS * 100:.0f} points, 95 % CI of the loss > 0): "
              + (f"REPRODUCED in {len(rep)} of {len(T)} cells" if len(rep) else
                 f"not reproduced ({len(T)} cells)")]
    with pd.option_context("display.width", 250, "display.max_columns", 40):
        if len(rep):
            lines.append(rep[CELL + ["uses", "share_hidden", "loss_veto", "loss_veto_lo",
                                     "loss_veto_hi"]].round(3).to_string(index=False))
        lines += ["", f"Rule 2 (leaderResIuc - leaderRes >= +{IUC_GAIN} tail prr_pdb, paired "
                  "95 % CI excluding 0, a cell with background N > 0): "
                  + (f"IUC WORTH PURSUING in {len(iuc)} of {len(realistic)} cells"
                     if len(iuc) else
                     f"IUC upper bound below threshold in this setting ({len(realistic)} cells)")]
        lines.append(P.round(3).to_string(index=False))
        lines += ["", "Cost of the oracle (means over runs):",
                  cost.round(3).to_string(index=False), "",
                  "Regression of paired tail prr_pdb differences on the leaderRes run's mean "
                  "G_H:", REG.round(4).to_string(index=False) if len(REG) else "(none)", "",
                  "Tail prr_pdb by G_H bin / veto (leaderRes):",
                  TG.round(3).to_string(index=False) if len(TG) else "(no _pkt files)"]
    (out / "verdict.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    figures_full(C, PV, TG, P, out)


def figures_full(C, PV, TG, P, out):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    if len(C):
        dims = ["nbg", "dist_bin", "thr", "age_bin"]
        fig, ax = plt.subplots(2, len(dims), figsize=(4 * len(dims), 7))
        for j, dim in enumerate(dims):
            sub = C[C["dimension"] == dim]
            for (arm, role), g in sub.groupby(["arm", "role"]):
                x = np.arange(len(g))
                ls = "-" if role == "helper" else ":"
                ax[0, j].plot(x, g["share_hidden"], ls, marker="o", label=f"{arm} {role}")
                ax[0, j].plot(x, g["share_stale"], ls, marker="x", alpha=0.5)
                ax[1, j].plot(x, g["overlap_rate"], ls, marker="o", label=f"{arm} {role}")
                ax[1, j].set_xticks(x, [str(v) for v in g["value"]], rotation=30, fontsize=7)
            ax[0, j].set_title(dim)
            ax[0, j].set_ylabel("hidden (o) / stale (x) share")
            ax[1, j].set_ylabel("resource-collision rate of the use")
        ax[0, 0].legend(fontsize=6)
        fig.tight_layout()
        fig.savefig(out / "figh1_obsolescence.png", dpi=130)
        plt.close(fig)
    if len(PV):
        fig, ax = plt.subplots(figsize=(6, 4))
        for arm, g in PV.groupby("arm"):
            x = np.arange(len(g))
            ax.errorbar(x, g["succ"], yerr=[g["succ"] - g["succ_lo"], g["succ_hi"] - g["succ"]],
                        marker="o", capsize=2, label=arm)
            ax.set_xticks(x, [str(v) for v in g["value"]])
        ax.set_xlabel("G_H of the selection")
        ax.set_ylabel("helper-copy success")
        ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(out / "figh2_success_vs_gh.png", dpi=130)
        plt.close(fig)
    if len(TG):
        g = TG[TG["by"] == "gh_bin"]
        fig, ax = plt.subplots(figsize=(6, 4))
        x = np.arange(len(g))
        ax.errorbar(x, g["tail_prr_pdb"], yerr=[g["tail_prr_pdb"] - g["ci_lo"],
                                                g["ci_hi"] - g["tail_prr_pdb"]],
                    marker="o", capsize=2)
        ax.set_xticks(x, g["bin"].astype(str))
        ax.set_xlabel("G_H of the selection behind the packet")
        ax.set_ylabel("tail prr_pdb (leaderRes)")
        fig.tight_layout()
        fig.savefig(out / "figh3_tail_vs_gh.png", dpi=130)
        plt.close(fig)
    if len(P):
        fig, ax = plt.subplots(figsize=(8, 0.3 * len(P) + 2))
        lab = (P["scenario"].str[:3] + " " + P["ivd"].astype(str) + "m N" + P["nbg"].astype(str)
               + " h" + P["helper"].astype(str) + " " + P["thr"].astype(str))
        y = np.arange(len(P))
        ax.errorbar(P["d"], y, xerr=[P["d"] - P["ci_lo"], P["ci_hi"] - P["d"]], fmt="o",
                    capsize=2)
        ax.axvline(0, color="k", lw=0.8)
        ax.axvline(IUC_GAIN, color="#c0392b", ls=":", lw=1)
        ax.set_yticks(y, lab, fontsize=7)
        ax.set_xlabel("leaderResIuc - leaderRes, tail prr_pdb (oracle IUC upper bound)")
        fig.tight_layout()
        fig.savefig(out / "figh4_iuc_upper_bound.png", dpi=130)
        plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["stageA", "full"])
    ap.add_argument("--results", default=str(HERE / "results/hidden_hn"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    stage_a(a) if a.mode == "stageA" else full(a)


if __name__ == "__main__":
    main()
