#!/usr/bin/env python3
"""
plot_fixed_vs_adaptive.py — Standalone PNG generator for the
fixed-MCS vs NACK-rate adaptive-MCS comparison.

Mirrors sections 2–5 + 7 of comparison.ipynb so the same figures can be
produced headlessly for slides/reports without opening a notebook.
"""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FIXED    = SCRIPT_DIR.parent / "traffic_class_eval" / "results_traffic_class_eval" / "tidy"
DEFAULT_ADAPTIVE = SCRIPT_DIR / "results_traffic_class_adaptive_eval" / "tidy"

CLASS_ORDER   = ["periodic1", "periodic2", "periodic3", "aperiodic1", "aperiodic2"]
SCHEME_COLORS = {"fixed": "#1f77b4", "adaptive": "#d62728"}

sns.set_style("whitegrid")
plt.rcParams.update({"figure.dpi": 110, "font.size": 10})


def load(dir_, scheme):
    out = {}
    for name in ["per_rx_kpis", "time_series", "resource_usage", "harq_stats"]:
        p = dir_ / f"{name}.csv"
        df = pd.read_csv(p) if p.is_file() else pd.DataFrame()
        df["scheme"] = scheme
        out[name] = df
    p = dir_ / "adapt_log.csv"
    out["adapt_log"] = pd.read_csv(p) if p.is_file() else pd.DataFrame()
    return out


def plot_per_class_violins(rx, out_dir, classes):
    metrics = [("prr", "Group PRR", (0, 1.02)),
               ("pir_ms", "PIR [ms]", None),
               ("tput_kbps", "Throughput [kbps]", None)]
    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    for ax, (col, label, ylim) in zip(axes, metrics):
        sns.violinplot(data=rx, x="trafficClass", y=col, hue="scheme",
                       palette=SCHEME_COLORS, split=True, inner="quartile",
                       cut=0, ax=ax, order=classes)
        sns.stripplot(data=rx, x="trafficClass", y=col, hue="scheme",
                      dodge=True, color="0.15", size=2, alpha=0.4,
                      ax=ax, order=classes, legend=False)
        ax.set_title(label); ax.set_xlabel("")
        if ylim: ax.set_ylim(ylim)
        for c_idx, c in enumerate(classes):
            mf = rx[(rx["trafficClass"]==c)&(rx["scheme"]=="fixed")][col].mean()
            ma = rx[(rx["trafficClass"]==c)&(rx["scheme"]=="adaptive")][col].mean()
            if np.isnan(mf) or np.isnan(ma): continue
            d = ma - mf
            txt = f"Δ={d:+.2f}" if abs(d) < 100 else f"Δ={d:+.0f}"
            ax.annotate(txt, xy=(c_idx, ax.get_ylim()[1]*0.97),
                        ha="center", va="top", fontsize=9, fontweight="bold",
                        color=("g" if (d>0 and col!="pir_ms") or (d<0 and col=="pir_ms") else "r"))
        for t in ax.get_xticklabels(): t.set_rotation(20)
        if ax is not axes[0] and ax.get_legend() is not None:
            ax.get_legend().remove()
    fig.suptitle("Fixed vs Adaptive — per-class KPI distributions", y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "cmp_per_class_violins.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  cmp_per_class_violins.png")


def plot_per_rx_heatmap(rx, out_dir, classes):
    fig, axes = plt.subplots(2, len(classes), figsize=(3.2*len(classes), 7),
                             gridspec_kw={"height_ratios": [2.2, 1]})
    if len(classes) == 1: axes = axes.reshape(2, 1)
    for j, c in enumerate(classes):
        sub = rx[rx["trafficClass"] == c]
        piv = (sub.groupby(["rx_node_id", "scheme"])["prr"].mean()
                  .unstack().reindex(columns=["fixed", "adaptive"]))
        ax = axes[0, j]
        im = ax.imshow(piv.values, vmin=0, vmax=1, cmap="RdYlGn", aspect="auto")
        ax.set_title(c, fontsize=10)
        ax.set_xticks([0, 1]); ax.set_xticklabels(["fixed", "adaptive"], fontsize=8)
        ax.set_yticks(range(len(piv))); ax.set_yticklabels(piv.index, fontsize=8)
        if j == 0: ax.set_ylabel("RX node id")
        for ri, rid in enumerate(piv.index):
            for ci, sch in enumerate(["fixed", "adaptive"]):
                v = piv.loc[rid, sch] if sch in piv.columns else np.nan
                if pd.notna(v):
                    ax.text(ci, ri, f"{v:.2f}", ha="center", va="center",
                            color="white" if v < 0.5 else "black", fontsize=7)
        ax = axes[1, j]
        if "fixed" in piv.columns and "adaptive" in piv.columns:
            delta = piv["adaptive"] - piv["fixed"]
            colors = ["g" if d > 0 else "r" for d in delta.values]
            ax.barh(range(len(delta)), delta.values, color=colors, alpha=0.75)
            ax.axvline(0, color="k", lw=0.6)
            ax.set_yticks(range(len(delta))); ax.set_yticklabels(delta.index, fontsize=8)
            ax.set_xlim(-1, 1)
            if j == 0: ax.set_ylabel("RX node id")
            ax.set_xlabel("Δ PRR\n(adaptive − fixed)", fontsize=8)
    fig.suptitle("Per-RX PRR — Fixed (left) vs Adaptive (right), Δ below", y=1.01)
    fig.tight_layout()
    fig.savefig(out_dir / "cmp_per_rx_heatmap.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  cmp_per_rx_heatmap.png")


def plot_harq(hq, out_dir, classes):
    metrics = [("nack_rate", "PSFCH NACK rate"),
               ("retx_ratio", "MAC retx ratio"),
               ("phy_err_rate", "PHY decode-error rate"),
               ("timeout_count", "HARQ timeouts")]
    fig, axes = plt.subplots(1, 4, figsize=(18, 4.5))
    for ax, (col, label) in zip(axes, metrics):
        sns.barplot(data=hq, x="trafficClass", y=col, hue="scheme",
                    palette=SCHEME_COLORS, ax=ax, order=classes,
                    errorbar=("ci", 95))
        ax.set_title(label); ax.set_xlabel("")
        for t in ax.get_xticklabels(): t.set_rotation(20)
        if ax is not axes[0] and ax.get_legend() is not None:
            ax.get_legend().remove()
    fig.suptitle("HARQ stats: Fixed vs Adaptive (95% CI)", y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "cmp_harq.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  cmp_harq.png")


def plot_resources(ru, out_dir, classes):
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.5))
    ax = axes[0]
    sns.barplot(data=ru, x="trafficClass", y="rbLen", hue="scheme",
                palette=SCHEME_COLORS, ax=ax, order=classes, errorbar=("ci",95))
    ax.set_title("Mean PRBs per grant"); ax.set_xlabel("")
    for t in ax.get_xticklabels(): t.set_rotation(20)

    ax = axes[1]
    grants = (ru.groupby(["trafficClass", "scheme", "seed"]).size()
                .reset_index(name="n_grants"))
    sns.barplot(data=grants, x="trafficClass", y="n_grants", hue="scheme",
                palette=SCHEME_COLORS, ax=ax, order=classes, errorbar=("ci",95))
    ax.set_title("Grants per run"); ax.set_xlabel("")
    for t in ax.get_xticklabels(): t.set_rotation(20)
    if ax.get_legend() is not None: ax.get_legend().remove()
    fig.suptitle("Resource usage: Fixed vs Adaptive", y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "cmp_resources.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  cmp_resources.png")


def write_summary_csv(rx, hq, ad, out_dir, classes):
    def p5(x):  return np.nanpercentile(x, 5)
    def p95(x): return np.nanpercentile(x, 95)
    base = (rx.groupby(["trafficClass", "scheme"], observed=True)
              .agg(prr_mean=("prr","mean"),
                   prr_p5=("prr", p5),
                   pir_p95_ms=("pir_ms", p95),
                   tput_p5_kbps=("tput_kbps", p5)))
    hq_agg = (hq.groupby(["trafficClass", "scheme"], observed=True)
                ["retx_ratio"].mean().rename("retx_ratio_mean"))
    summary = base.join(hq_agg)
    summary["mean_mcs"] = np.nan
    if not ad.empty:
        mcs_mean = ad.groupby("trafficClass", observed=True)["new_mcs"].mean()
        for c, v in mcs_mean.items():
            if (c, "adaptive") in summary.index:
                summary.loc[(c, "adaptive"), "mean_mcs"] = v
    summary = summary.round(3).reindex(
        [(c, s) for c in classes for s in ["fixed", "adaptive"]])
    out = out_dir / "cmp_summary.csv"
    summary.to_csv(out)
    print(f"  cmp_summary.csv ({len(summary)} rows)")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixed",    type=Path, default=DEFAULT_FIXED)
    ap.add_argument("--adaptive", type=Path, default=DEFAULT_ADAPTIVE)
    ap.add_argument("--out-dir",  type=Path, default=None,
                    help="default: <adaptive>/../plots/comparison/")
    args = ap.parse_args()

    out_dir = args.out_dir or args.adaptive.parent / "plots" / "comparison"
    out_dir.mkdir(parents=True, exist_ok=True)

    F = load(args.fixed,    "fixed")
    A = load(args.adaptive, "adaptive")
    rx = pd.concat([F["per_rx_kpis"],    A["per_rx_kpis"]], ignore_index=True)
    ru = pd.concat([F["resource_usage"], A["resource_usage"]], ignore_index=True)
    hq = pd.concat([F["harq_stats"],     A["harq_stats"]], ignore_index=True)
    ad = A["adapt_log"]

    for df in (rx, ru, hq):
        df["trafficClass"] = pd.Categorical(df["trafficClass"], CLASS_ORDER, ordered=True)
        df["scheme"]       = pd.Categorical(df["scheme"], ["fixed", "adaptive"], ordered=True)
    classes = [c for c in CLASS_ORDER if c in rx["trafficClass"].unique()]

    print(f"Writing PNGs → {out_dir}")
    plot_per_class_violins(rx, out_dir, classes)
    plot_per_rx_heatmap(rx, out_dir, classes)
    plot_harq(hq, out_dir, classes)
    plot_resources(ru, out_dir, classes)
    summary = write_summary_csv(rx, hq, ad, out_dir, classes)
    print()
    print(summary.to_string())


if __name__ == "__main__":
    main()
