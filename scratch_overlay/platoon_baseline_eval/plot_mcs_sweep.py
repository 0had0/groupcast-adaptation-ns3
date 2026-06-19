#!/usr/bin/env python3
"""
plot_mcs_sweep.py

Reads results_mcs_sweep/tidy/ CSVs (produced by extract_mcs_sweep.py)
and generates publication-ready PNGs into results_mcs_sweep/plots/:

  01_prr_vs_mcs_per_scenario.png   — mean PRR vs MCS (line), facet per scenario
  02_prr_vs_dist_per_mcs.png       — PRR vs init distance, one line per MCS
  03_worst_rx_prr_vs_mcs.png       — worst-RX PRR per (scenario, MCS) bar chart
  04_harq_nack_rate_vs_mcs.png     — NACK rate vs MCS bar chart
  05_sinr_cdf_per_mcs.png          — SINR CDF, one line per MCS (all scenarios)
  06_prr_heatmap_rx_vs_mcs.png     — heatmap: rx_node_id × MCS → mean PRR

Run after extract_mcs_sweep.py.
"""

from pathlib import Path
import argparse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import seaborn as sns

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TIDY = SCRIPT_DIR / "results_mcs_sweep" / "tidy"
MCS_ORDER = [4, 7, 10, 12, 14, 20]          # canonical sort (4 if present)
MCS_PALETTE = {
    4:  "#1f77b4",
    7:  "#ff7f0e",
    10: "#2ca02c",
    12: "#d62728",
    14: "#9467bd",
    20: "#8c564b",
}
SCENARIO_LABELS = {
    "shadowing_blockage":   "Shadow+Blockage",
    "high_density":         "High Density",
    "variable_packet_size": "Variable Pkt Size",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def scenario_label(s):
    return SCENARIO_LABELS.get(s, s)


def mcs_present(df):
    """Return MCS values found in df, sorted in MCS_ORDER then numerically."""
    vals = sorted(df["mcs"].unique())
    order = [m for m in MCS_ORDER if m in vals] + [m for m in vals if m not in MCS_ORDER]
    return order


def savefig(fig, path):
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {path}")


# ── plots ─────────────────────────────────────────────────────────────────────

def plot_prr_vs_mcs(df_prx, out_dir):
    """Line plot: mean PRR vs MCS, 95 % CI band, facet per scenario."""
    rx = df_prx[df_prx["rx_node_id"] != 0].copy()
    scenarios = sorted(rx["scenario"].unique())
    ncols = len(scenarios)
    fig, axes = plt.subplots(1, ncols, figsize=(5 * ncols, 4), sharey=True)
    if ncols == 1:
        axes = [axes]

    for ax, scen in zip(axes, scenarios):
        sub = rx[rx["scenario"] == scen]
        grp = sub.groupby("mcs")["prr"].agg(["mean", "std", "count"]).reset_index()
        grp["se95"] = 1.96 * grp["std"] / np.sqrt(grp["count"].clip(lower=1))
        mcs_vals = sorted(grp["mcs"])
        color = [MCS_PALETTE.get(m, "grey") for m in mcs_vals]
        for i, (_, row) in enumerate(grp.sort_values("mcs").iterrows()):
            ax.errorbar(row["mcs"], row["mean"], yerr=row["se95"],
                        fmt="o-", color=color[i], capsize=4, linewidth=1.8,
                        label=f"MCS {int(row['mcs'])}")
        ax.set_title(scenario_label(scen), fontsize=11)
        ax.set_xlabel("MCS index")
        ax.yaxis.set_major_formatter(mtick.PercentFormatter(xmax=1))
        ax.grid(True, alpha=0.3)
        ax.set_ylim(0, 1.05)
        if ax is axes[0]:
            ax.set_ylabel("Mean PRR (all RX nodes)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(handles),
               bbox_to_anchor=(0.5, -0.05), frameon=False, fontsize=9)
    fig.suptitle("Mean PRR vs MCS index (periodic2, 60 s, 10 seeds)", y=1.02)
    savefig(fig, out_dir / "01_prr_vs_mcs_per_scenario.png")


def plot_prr_vs_dist(df_prx, out_dir):
    """PRR vs initial TX-RX distance for each MCS, facet per scenario."""
    rx = df_prx[df_prx["rx_node_id"] != 0].copy()
    scenarios = sorted(rx["scenario"].unique())
    ncols = len(scenarios)
    fig, axes = plt.subplots(1, ncols, figsize=(5 * ncols, 4), sharey=True)
    if ncols == 1:
        axes = [axes]

    for ax, scen in zip(axes, scenarios):
        sub = rx[rx["scenario"] == scen]
        mcs_vals = mcs_present(sub)
        for mcs in mcs_vals:
            m = sub[sub["mcs"] == mcs].groupby("init_dist_m")["prr"].mean().reset_index()
            if m.empty:
                continue
            ax.plot(m["init_dist_m"], m["prr"],
                    "o-", color=MCS_PALETTE.get(mcs, "grey"),
                    label=f"MCS {mcs}", linewidth=1.8, markersize=5)
        ax.set_title(scenario_label(scen), fontsize=11)
        ax.set_xlabel("Initial TX–RX distance (m)")
        ax.yaxis.set_major_formatter(mtick.PercentFormatter(xmax=1))
        ax.set_ylim(0, 1.05)
        ax.grid(True, alpha=0.3)
        if ax is axes[0]:
            ax.set_ylabel("Mean PRR")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(handles),
               bbox_to_anchor=(0.5, -0.05), frameon=False, fontsize=9)
    fig.suptitle("PRR vs distance for each MCS", y=1.02)
    savefig(fig, out_dir / "02_prr_vs_dist_per_mcs.png")


def plot_worst_rx(df_prx, out_dir):
    """Bar chart: worst-RX PRR (min over rx_node_id mean) per (scenario, MCS)."""
    rx = df_prx[df_prx["rx_node_id"] != 0].copy()
    worst = (rx.groupby(["scenario", "mcs", "rx_node_id"])["prr"].mean()
               .reset_index()
               .groupby(["scenario", "mcs"])["prr"].min()
               .reset_index()
               .rename(columns={"prr": "worst_rx_prr"}))

    scenarios = sorted(worst["scenario"].unique())
    mcs_vals = mcs_present(worst)
    x = np.arange(len(scenarios))
    width = 0.8 / len(mcs_vals)
    fig, ax = plt.subplots(figsize=(max(6, 3 * len(scenarios)), 4))
    for i, mcs in enumerate(mcs_vals):
        sub = worst[worst["mcs"] == mcs]
        vals = [sub[sub["scenario"] == s]["worst_rx_prr"].values[0]
                if len(sub[sub["scenario"] == s]) else 0
                for s in scenarios]
        ax.bar(x + i * width - (len(mcs_vals) - 1) * width / 2,
               vals, width * 0.9,
               color=MCS_PALETTE.get(mcs, "grey"),
               label=f"MCS {mcs}")
    ax.set_xticks(x)
    ax.set_xticklabels([scenario_label(s) for s in scenarios], rotation=15, ha="right")
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(xmax=1))
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Worst-RX PRR")
    ax.set_title("Worst-RX PRR per (scenario, MCS)", fontsize=11)
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    savefig(fig, out_dir / "03_worst_rx_prr_vs_mcs.png")


def plot_harq_nack(df_harq, out_dir):
    """Bar chart: NACK rate vs MCS, facet per scenario."""
    if df_harq.empty:
        return
    scenarios = sorted(df_harq["scenario"].unique())
    ncols = len(scenarios)
    fig, axes = plt.subplots(1, ncols, figsize=(4.5 * ncols, 4), sharey=True)
    if ncols == 1:
        axes = [axes]

    for ax, scen in zip(axes, scenarios):
        sub = df_harq[df_harq["scenario"] == scen]
        grp = sub.groupby("mcs")["nack_rate"].mean().reset_index().sort_values("mcs")
        colors = [MCS_PALETTE.get(int(m), "grey") for m in grp["mcs"]]
        ax.bar(grp["mcs"].astype(str), grp["nack_rate"], color=colors)
        ax.set_title(scenario_label(scen), fontsize=11)
        ax.set_xlabel("MCS index")
        ax.yaxis.set_major_formatter(mtick.PercentFormatter(xmax=1))
        ax.grid(axis="y", alpha=0.3)
        if ax is axes[0]:
            ax.set_ylabel("Mean NACK rate")
    fig.suptitle("HARQ NACK rate vs MCS (lower = better)", y=1.02)
    savefig(fig, out_dir / "04_harq_nack_rate_vs_mcs.png")


def plot_sinr_cdf(df_sinr, out_dir):
    """SINR CDF (mean_sinr_db), one line per MCS, all scenarios pooled."""
    if df_sinr.empty:
        return
    mcs_vals = mcs_present(df_sinr)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for mcs in mcs_vals:
        sub = df_sinr[df_sinr["mcs"] == mcs]["mean_sinr_db"].dropna()
        if sub.empty:
            continue
        vals = np.sort(sub)
        cdf  = np.arange(1, len(vals) + 1) / len(vals)
        ax.plot(vals, cdf, color=MCS_PALETTE.get(mcs, "grey"), label=f"MCS {mcs}", linewidth=1.8)
    ax.set_xlabel("Mean SINR per RX-UE (dB)")
    ax.set_ylabel("CDF")
    ax.set_title("SINR CDF across all scenarios and seeds")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    savefig(fig, out_dir / "05_sinr_cdf_per_mcs.png")


def plot_prr_heatmap(df_prx, out_dir):
    """Heatmap: rx_node_id (rows) × MCS (cols) → mean PRR, facet per scenario."""
    rx = df_prx[df_prx["rx_node_id"] != 0].copy()
    scenarios = sorted(rx["scenario"].unique())
    mcs_vals = mcs_present(rx)
    ncols = len(scenarios)
    fig, axes = plt.subplots(1, ncols, figsize=(4.5 * ncols, 4))
    if ncols == 1:
        axes = [axes]

    for ax, scen in zip(axes, scenarios):
        sub = rx[rx["scenario"] == scen]
        pivot = sub.groupby(["rx_node_id", "mcs"])["prr"].mean().unstack("mcs")
        # Ensure columns in MCS_ORDER
        cols = [m for m in mcs_vals if m in pivot.columns]
        pivot = pivot[cols]
        sns.heatmap(pivot, ax=ax, vmin=0, vmax=1, cmap="RdYlGn",
                    annot=True, fmt=".2f", linewidths=0.5, linecolor="white",
                    cbar=(ax is axes[-1]))
        ax.set_title(scenario_label(scen), fontsize=11)
        ax.set_xlabel("MCS")
        ax.set_ylabel("RX node ID")
        ax.tick_params(axis="x", rotation=0)
    fig.suptitle("Mean PRR per (RX node, MCS)", y=1.02)
    savefig(fig, out_dir / "06_prr_heatmap_rx_vs_mcs.png")


def plot_delta_prr_vs_baseline(df_prx, out_dir):
    """Δ-PRR vs MCS=14 baseline, per (rx_node_id, scenario) – stacked bar."""
    rx = df_prx[df_prx["rx_node_id"] != 0].copy()
    base14 = (rx[rx["mcs"] == 14]
              .groupby(["scenario", "rx_node_id"])["prr"].mean()
              .rename("prr14"))
    grp = (rx.groupby(["scenario", "mcs", "rx_node_id"])["prr"].mean()
             .reset_index())
    grp = grp.merge(base14.reset_index(), on=["scenario", "rx_node_id"], how="left")
    grp["delta"] = grp["prr"] - grp["prr14"]
    grp = grp[grp["mcs"] != 14]

    scenarios = sorted(grp["scenario"].unique())
    mcs_vals = [m for m in mcs_present(grp) if m != 14]
    ncols = len(scenarios)
    fig, axes = plt.subplots(1, ncols, figsize=(5 * ncols, 4.5), sharey=True)
    if ncols == 1:
        axes = [axes]

    for ax, scen in zip(axes, scenarios):
        sub = grp[grp["scenario"] == scen]
        pivot = sub.groupby(["rx_node_id", "mcs"])["delta"].mean().unstack("mcs")
        cols = [m for m in mcs_vals if m in pivot.columns]
        pivot = pivot[cols]
        pivot.plot(kind="bar", ax=ax,
                   color=[MCS_PALETTE.get(m, "grey") for m in cols],
                   width=0.75)
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_title(scenario_label(scen), fontsize=11)
        ax.set_xlabel("RX node ID")
        ax.yaxis.set_major_formatter(mtick.PercentFormatter(xmax=1))
        ax.grid(axis="y", alpha=0.3)
        ax.legend(title="MCS", fontsize=8, title_fontsize=8)
        ax.tick_params(axis="x", rotation=0)
        if ax is axes[0]:
            ax.set_ylabel("ΔPRR vs MCS=14")
    fig.suptitle("PRR gain (positive) or loss (negative) vs MCS=14 baseline", y=1.02)
    savefig(fig, out_dir / "07_delta_prr_vs_baseline.png")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tidy", type=Path, default=DEFAULT_TIDY)
    ap.add_argument("--out",  type=Path, default=None)
    args = ap.parse_args()

    tidy = args.tidy
    if not tidy.is_dir():
        raise SystemExit(f"tidy dir not found: {tidy}")

    out_dir = args.out or tidy.parent / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading from {tidy}")
    df_prx   = pd.read_csv(tidy / "per_rx_kpis.csv")
    df_sinr  = pd.read_csv(tidy / "sinr_per_rx.csv")  if (tidy / "sinr_per_rx.csv").is_file()  else pd.DataFrame()
    df_harq  = pd.read_csv(tidy / "harq_stats.csv")   if (tidy / "harq_stats.csv").is_file()   else pd.DataFrame()

    print(f"per_rx_kpis: {len(df_prx)} rows  |  sinr: {len(df_sinr)} rows  |  harq: {len(df_harq)} rows")
    print(f"Writing plots → {out_dir}")

    plot_prr_vs_mcs(df_prx, out_dir)
    plot_prr_vs_dist(df_prx, out_dir)
    plot_worst_rx(df_prx, out_dir)
    plot_harq_nack(df_harq, out_dir)
    plot_sinr_cdf(df_sinr, out_dir)
    plot_prr_heatmap(df_prx, out_dir)
    plot_delta_prr_vs_baseline(df_prx, out_dir)

    print("\nDone.")


if __name__ == "__main__":
    main()
