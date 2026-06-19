#!/usr/bin/env python3
"""
plot_traffic_class_raapc.py — Batch PNG renderer for the NACK-rate
adaptive-MCS campaign.  Reads the five tidy CSVs produced by extract_kpis.py
(including adapt_log.csv) and writes plots to
results_traffic_class_raapc_eval/plots/.

Mirrors sections 2-6 of evaluation.ipynb plus a new mcs_timeline.png that
shows the controller's MCS trajectory per traffic class.
"""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SCRIPT_DIR     = Path(__file__).resolve().parent
DEFAULT_RESULTS = SCRIPT_DIR / "results_traffic_class_raapc_eval"

CLASS_ORDER = ["periodic1", "periodic2", "periodic3", "aperiodic1", "aperiodic2"]
CLASS_COLORS = {
    "periodic1":  "#2c7bb6",
    "periodic2":  "#abd9e9",
    "periodic3":  "#d7191c",
    "aperiodic1": "#fdae61",
    "aperiodic2": "#7b3294",
}

plt.rcParams.update({
    "figure.dpi": 110,
    "font.size": 10,
    "axes.titlesize": 11,
    "axes.labelsize": 10,
    "axes.grid": True,
    "grid.alpha": 0.3,
})


# ────────────────────────────────────────────────────────────────────────────
def load(results: Path):
    tidy = results / "tidy"
    out = {
        "rx":  pd.read_csv(tidy / "per_rx_kpis.csv"),
        "ts":  pd.read_csv(tidy / "time_series.csv"),
        "ru":  pd.read_csv(tidy / "resource_usage.csv"),
        "hq":  pd.read_csv(tidy / "harq_stats.csv"),
    }
    ad_path = tidy / "adapt_log.csv"
    out["ad"] = pd.read_csv(ad_path) if ad_path.is_file() else pd.DataFrame()
    return out


def order_class(df):
    df = df.copy()
    df["trafficClass"] = pd.Categorical(df["trafficClass"], CLASS_ORDER, ordered=True)
    return df.sort_values("trafficClass")


# ────────────────────────────────────────────────────────────────────────────
# 2. Per-RX distributions
# ────────────────────────────────────────────────────────────────────────────
def plot_per_rx_distributions(data, out_dir):
    df = order_class(data["rx"])
    classes = [c for c in CLASS_ORDER if c in df["trafficClass"].unique()]

    metrics = [
        ("prr",       "Group PRR",            (0, 1.02)),
        ("pir_ms",    "PIR [ms]",             None),
        ("tput_kbps", "Throughput [kbps]",    None),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    for ax, (col, label, ylim) in zip(axes, metrics):
        # box per class
        data_lst = [df[df["trafficClass"] == c][col].dropna().to_numpy()
                    for c in classes]
        bp = ax.boxplot(data_lst, patch_artist=True,
                        tick_labels=classes, widths=0.55, showfliers=False)
        for box, c in zip(bp["boxes"], classes):
            box.set_facecolor(CLASS_COLORS[c]); box.set_alpha(0.7)
        # overlay raw points
        for i, c in enumerate(classes, 1):
            vals = df[df["trafficClass"] == c][col].dropna().to_numpy()
            jitter = (np.random.rand(len(vals)) - 0.5) * 0.25
            ax.scatter(np.full_like(vals, i, dtype=float) + jitter,
                       vals, s=6, color="0.25", alpha=0.45, zorder=3)
        ax.set_ylabel(label); ax.set_title(label + " — per-RX distribution")
        if ylim is not None: ax.set_ylim(ylim)
        for tick in ax.get_xticklabels(): tick.set_rotation(20)
    fig.suptitle("Per-RX KPI distributions across traffic classes", y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "per_rx_distributions.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  per_rx_distributions.png")


# ────────────────────────────────────────────────────────────────────────────
# 3. KPI vs distance
# ────────────────────────────────────────────────────────────────────────────
def plot_vs_distance(data, out_dir):
    df = order_class(data["rx"])
    classes = [c for c in CLASS_ORDER if c in df["trafficClass"].unique()]

    metrics = [("prr", "Group PRR", (0, 1.02)),
               ("pir_ms", "PIR [ms]", None),
               ("tput_kbps", "Throughput [kbps]", None)]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    for ax, (col, label, ylim) in zip(axes, metrics):
        for c in classes:
            sub = df[df["trafficClass"] == c]
            # per-RX mean over seeds
            grp = sub.groupby("rx_node_id").agg(
                mean_d = ("init_dist_m", "mean"),
                mean_v = (col,           "mean"),
                std_v  = (col,           "std"),
            ).sort_values("mean_d")
            ax.errorbar(grp["mean_d"], grp["mean_v"], yerr=grp["std_v"],
                        fmt="o-", color=CLASS_COLORS[c], capsize=3, lw=1.5,
                        label=c, alpha=0.85)
        ax.set_xlabel("TX-RX initial distance [m]"); ax.set_ylabel(label)
        if ylim is not None: ax.set_ylim(ylim)
        ax.set_title(f"{label} vs distance")
        ax.legend(fontsize=8)
    fig.suptitle("Per-RX KPI vs distance from TX (node 0)", y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "kpi_vs_distance.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  kpi_vs_distance.png")


# ────────────────────────────────────────────────────────────────────────────
# 4. Time-series variation
# ────────────────────────────────────────────────────────────────────────────
def plot_time_series(data, out_dir):
    df = order_class(data["ts"])
    classes = [c for c in CLASS_ORDER if c in df["trafficClass"].unique()]
    rx_nodes = sorted(df["rx_node_id"].unique())

    # PRR over time, one subplot per class, line per RX (averaged over seeds)
    fig, axes = plt.subplots(len(classes), 1,
                             figsize=(11, 2.4 * len(classes)),
                             sharex=True, sharey=True)
    if len(classes) == 1:
        axes = [axes]
    cmap = plt.get_cmap("viridis")
    for ax, c in zip(axes, classes):
        sub = df[df["trafficClass"] == c]
        for j, nid in enumerate(rx_nodes):
            ss = sub[sub["rx_node_id"] == nid]
            if ss.empty: continue
            agg = ss.groupby("t_bin_s")["prr_window"].mean()
            ax.plot(agg.index, agg.values,
                    color=cmap(j / max(1, len(rx_nodes) - 1)),
                    lw=1.2, label=f"N{nid}", alpha=0.9)
        ax.set_ylabel(f"{c}\nPRR")
        ax.set_ylim(0, 1.1)
    axes[-1].set_xlabel("time [s]")
    axes[0].legend(fontsize=7, ncol=4, loc="upper right")
    fig.suptitle("Per-RX PRR over time (1-s bins, mean over seeds)", y=1.01)
    fig.tight_layout()
    fig.savefig(out_dir / "time_series_prr.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  time_series_prr.png")

    # Heatmap of PRR (rx × t) per class
    fig, axes = plt.subplots(1, len(classes),
                             figsize=(3.0 * len(classes), 4.5),
                             sharey=True)
    if len(classes) == 1: axes = [axes]
    for ax, c in zip(axes, classes):
        sub = df[df["trafficClass"] == c]
        if sub.empty:
            ax.set_visible(False); continue
        piv = sub.pivot_table(index="rx_node_id", columns="t_bin_s",
                              values="prr_window", aggfunc="mean")
        im = ax.imshow(piv.values, aspect="auto", vmin=0, vmax=1,
                       cmap="RdYlGn", origin="lower",
                       extent=[piv.columns.min(), piv.columns.max(),
                               piv.index.min() - 0.5, piv.index.max() + 0.5])
        ax.set_title(c, fontsize=10)
        ax.set_xlabel("time [s]")
        ax.set_yticks(sorted(df["rx_node_id"].unique()))
    axes[0].set_ylabel("RX node id")
    cbar = fig.colorbar(im, ax=axes, fraction=0.025, pad=0.02)
    cbar.set_label("PRR (1-s window)")
    fig.suptitle("PRR heatmap (RX × time) by traffic class", y=1.02)
    fig.savefig(out_dir / "time_series_heatmap.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  time_series_heatmap.png")


# ────────────────────────────────────────────────────────────────────────────
# 5. Resource usage
# ────────────────────────────────────────────────────────────────────────────
def plot_resource_usage(data, out_dir):
    df = order_class(data["ru"])
    classes = [c for c in CLASS_ORDER if c in df["trafficClass"].unique()]

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    # rbLen histogram per class
    ax = axes[0]
    for c in classes:
        vals = df[df["trafficClass"] == c]["rbLen"].to_numpy()
        if len(vals) == 0: continue
        bins = np.arange(vals.min(), vals.max() + 2) - 0.5
        ax.hist(vals, bins=bins, alpha=0.55, label=c,
                color=CLASS_COLORS[c], edgecolor="black", lw=0.4)
    ax.set_xlabel("rbLen (allocated PRBs per grant)")
    ax.set_ylabel("count"); ax.legend(fontsize=8)
    ax.set_title("PRB allocation distribution")

    # Number of grants (channel busy occurrences) per class — mean across seeds
    ax = axes[1]
    grp = (df.groupby(["trafficClass", "seed"]).size()
             .reset_index(name="n_grants"))
    means = grp.groupby("trafficClass")["n_grants"].agg(["mean", "std"]).reindex(classes)
    ax.bar(range(len(classes)), means["mean"], yerr=means["std"],
           color=[CLASS_COLORS[c] for c in classes], alpha=0.8, capsize=3)
    ax.set_xticks(range(len(classes))); ax.set_xticklabels(classes, rotation=20)
    ax.set_ylabel("PSSCH grants per run")
    ax.set_title("Channel occupancy (grants per run)")

    # Retransmission share (ndi=0 vs ndi=1) per class
    ax = axes[2]
    retx_share = []
    for c in classes:
        sub = df[df["trafficClass"] == c]
        if len(sub) == 0:
            retx_share.append(0); continue
        n_retx = (sub["ndi"] == 0).sum()
        retx_share.append(n_retx / len(sub))
    ax.bar(range(len(classes)), retx_share,
           color=[CLASS_COLORS[c] for c in classes], alpha=0.8)
    ax.set_xticks(range(len(classes))); ax.set_xticklabels(classes, rotation=20)
    ax.set_ylabel("retransmissions / total TX")
    ax.set_title("Retransmission share")
    fig.suptitle("Resource usage by traffic class", y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "resource_usage.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  resource_usage.png")


# ────────────────────────────────────────────────────────────────────────────
# 6. HARQ statistics
# ────────────────────────────────────────────────────────────────────────────
def plot_harq_stats(data, out_dir):
    df = order_class(data["hq"])
    classes = [c for c in CLASS_ORDER if c in df["trafficClass"].unique()]

    metrics = [("nack_rate",   "PSFCH NACK rate"),
               ("retx_ratio",  "MAC retx ratio"),
               ("phy_err_rate","PHY decode-error rate"),
               ("timeout_count","HARQ-process timeouts (count)")]
    fig, axes = plt.subplots(1, 4, figsize=(18, 4.5))
    for ax, (col, label) in zip(axes, metrics):
        agg = df.groupby("trafficClass")[col].agg(["mean", "std"]).reindex(classes)
        ax.bar(range(len(classes)), agg["mean"], yerr=agg["std"],
               color=[CLASS_COLORS[c] for c in classes],
               alpha=0.85, capsize=3)
        ax.set_xticks(range(len(classes))); ax.set_xticklabels(classes, rotation=20)
        ax.set_ylabel(label); ax.set_title(label)
    fig.suptitle("HARQ statistics by traffic class (mean ± std over seeds)", y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "harq_stats.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  harq_stats.png")


# ────────────────────────────────────────────────────────────────────────────
# 6b. Adaptive-MCS behaviour (new — reads adapt_log.csv)
# ────────────────────────────────────────────────────────────────────────────
def plot_mcs_timeline(data, out_dir):
    df = data.get("ad")
    if df is None or df.empty:
        print("  (skip mcs_timeline.png — adapt_log.csv empty)")
        return
    df = order_class(df)
    classes = [c for c in CLASS_ORDER if c in df["trafficClass"].unique()]

    fig, axes = plt.subplots(len(classes), 1,
                             figsize=(11, 2.2 * len(classes)),
                             sharex=True, sharey=True)
    if len(classes) == 1: axes = [axes]
    for ax, c in zip(axes, classes):
        sub = df[df["trafficClass"] == c]
        # one thin line per seed (alpha low)
        for seed, ss in sub.groupby("seed"):
            ax.plot(ss["t_s"], ss["new_mcs"], color="0.6", lw=0.5, alpha=0.4)
        # mean per t_s bin
        mean = sub.groupby(sub["t_s"].round(1))["new_mcs"].mean()
        ax.plot(mean.index, mean.values, color=CLASS_COLORS[c], lw=2.2,
                label="mean across seeds")
        ax.set_ylabel(f"{c}\nMCS")
        ax.set_ylim(-0.5, 28.5)
        ax.axhline(14, color="k", lw=0.8, ls="--", alpha=0.5,
                   label="MCS=14 (fixed baseline)")
        ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("simulation time [s]")
    fig.suptitle("Adaptive-MCS controller trajectory per traffic class", y=1.01)
    fig.tight_layout()
    fig.savefig(out_dir / "mcs_timeline.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  mcs_timeline.png")


def plot_adapt_decisions(data, out_dir):
    df = data.get("ad")
    if df is None or df.empty:
        print("  (skip adapt_decisions.png — adapt_log.csv empty)")
        return
    df = order_class(df)
    classes = [c for c in CLASS_ORDER if c in df["trafficClass"].unique()]

    # Decision distribution (up / down / same) per class
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))

    ax = axes[0]
    dec = (df.groupby(["trafficClass", "decision"]).size()
              .unstack(fill_value=0)
              .reindex(classes))
    for col in ["up", "down", "same"]:
        if col not in dec.columns: dec[col] = 0
    dec = dec[["down", "same", "up"]]
    bottom = np.zeros(len(classes))
    colors = {"down": "#d7191c", "same": "#bbbbbb", "up": "#2ca02c"}
    for col in ["down", "same", "up"]:
        ax.bar(range(len(classes)), dec[col].values, bottom=bottom,
               label=col, color=colors[col], alpha=0.85)
        bottom += dec[col].values
    ax.set_xticks(range(len(classes)))
    ax.set_xticklabels(classes, rotation=20)
    ax.set_ylabel("# evaluations")
    ax.set_title("Controller decisions per traffic class")
    ax.legend(loc="upper right", fontsize=9)

    # NACK-rate distribution per class
    ax = axes[1]
    data_lst = [df[df["trafficClass"] == c]["nack_rate"].dropna().to_numpy()
                for c in classes]
    bp = ax.boxplot(data_lst, patch_artist=True,
                    tick_labels=classes, widths=0.55, showfliers=False)
    for box, c in zip(bp["boxes"], classes):
        box.set_facecolor(CLASS_COLORS[c]); box.set_alpha(0.7)
    ax.axhline(0.15, color="r", lw=0.8, ls="--", alpha=0.7, label="down_thr (0.15)")
    ax.axhline(0.08, color="g", lw=0.8, ls="--", alpha=0.7, label="up_thr (0.08)")
    ax.set_ylabel("NACK rate (per evaluation window)")
    ax.set_title("NACK-rate distribution per class")
    ax.legend(fontsize=8)
    for tick in ax.get_xticklabels(): tick.set_rotation(20)

    fig.suptitle("Adaptive controller diagnostics", y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "adapt_decisions.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  adapt_decisions.png")


# ────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = ap.parse_args()

    out_dir = args.results / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading tidy CSVs from {args.results}/tidy/")
    data = load(args.results)
    ad_n = len(data.get("ad", pd.DataFrame()))
    print(f"  rx={len(data['rx'])}  ts={len(data['ts'])}  "
          f"ru={len(data['ru'])}  hq={len(data['hq'])}  ad={ad_n}")

    print(f"Writing plots → {out_dir}")
    plot_per_rx_distributions(data, out_dir)
    plot_vs_distance(data, out_dir)
    plot_time_series(data, out_dir)
    plot_resource_usage(data, out_dir)
    plot_harq_stats(data, out_dir)
    plot_mcs_timeline(data, out_dir)
    plot_adapt_decisions(data, out_dir)
    print("Done.")


if __name__ == "__main__":
    main()
