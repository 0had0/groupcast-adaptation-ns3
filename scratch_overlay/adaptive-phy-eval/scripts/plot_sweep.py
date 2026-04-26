#!/usr/bin/env python3
"""
Visualise s1 sweep results from _summary.csv.

Usage:
    python3 plot_sweep.py [--csv PATH]   (default: <repo>/tmp/_summary.csv)
    python3 plot_sweep.py --csv /tmp/_summary.csv

Figures produced
----------------
Fig 1  — 3-D (phy_idx × n_ues → coll_rate / prr_group) + 2-D projections
Fig 2  — 3-D (phy_idx × n_ues → prr_cam / prr_vid / prr_bg) + 2-D projections
Fig 3  — KPI vs each individual parameter (mu, w, mcs)  [line plots]
"""

import argparse
import math
import os
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.cm as cm
import numpy as np
import pandas as pd

# ── colour palette ────────────────────────────────────────────────────────────
C_COLL   = "#e05c5c"
C_GROUP  = "#4c72b0"
C_CAM    = "#2ca02c"
C_VID    = "#ff7f0e"
C_BG     = "#9467bd"

FLOW_COLORS = {"prr_cam": C_CAM, "prr_vid": C_VID, "prr_bg": C_BG}
FLOW_LABELS = {"prr_cam": "CAM (port 8001)", "prr_vid": "Video (port 8002)", "prr_bg": "BG (port 8003)"}

# ── helpers ───────────────────────────────────────────────────────────────────

def load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df[df["scen"] == "s1"].copy()
    if df.empty:
        raise SystemExit("No s1 rows found in the CSV.")
    # aggregate over seeds
    grp_cols = ["n_ues", "mu", "w", "mcs"]
    kpi_cols = ["coll_rate", "prr_group", "prr_cam", "prr_vid", "prr_bg"]
    df = df.groupby(grp_cols)[kpi_cols].mean().reset_index()
    return df


def make_phy_index(df: pd.DataFrame):
    """Assign integer index to each unique (mu, w, mcs) triple, sorted."""
    combos = df[["mu", "w", "mcs"]].drop_duplicates().sort_values(["mu", "w", "mcs"])
    combos = combos.reset_index(drop=True)
    combos["phy_idx"] = combos.index
    combos["phy_label"] = combos.apply(
        lambda r: f"µ{int(r.mu)} W{int(r.w)} M{int(r.mcs)}", axis=1
    )
    df = df.merge(combos, on=["mu", "w", "mcs"])
    return df, combos


# ── Figure 1: 3-D + 2-D  coll_rate & prr_group ────────────────────────────────

def fig1(df: pd.DataFrame, combos: pd.DataFrame):
    fig = plt.figure(figsize=(20, 14))
    fig.suptitle("s1 — Collision Rate & Group PRR", fontsize=14, fontweight="bold")

    n_ues_vals = sorted(df["n_ues"].unique())
    phy_max    = int(combos["phy_idx"].max())

    kpis = [("coll_rate", "Collision Rate", C_COLL),
            ("prr_group", "Group PRR",      C_GROUP)]

    # row 0: two 3-D axes
    for col, (kpi, title, colour) in enumerate(kpis):
        ax = fig.add_subplot(3, 2, col + 1, projection="3d")
        xs = df["phy_idx"].values
        ys = df["n_ues"].values
        zs = df[kpi].values
        sc = ax.scatter(xs, ys, zs, c=zs, cmap="RdYlGn_r" if kpi == "coll_rate" else "RdYlGn",
                        s=18, alpha=0.75, depthshade=True)
        ax.set_xlabel("PHY combo index", fontsize=8, labelpad=6)
        ax.set_ylabel("N UEs", fontsize=8)
        ax.set_zlabel(title, fontsize=8)
        ax.set_title(f"3-D: {title}", fontsize=10)
        fig.colorbar(sc, ax=ax, shrink=0.5, label=title)

    # row 1: 2-D vs PHY combo (averaged over n_ues)
    by_phy = df.groupby("phy_idx")[["coll_rate", "prr_group"]].mean().reset_index()
    for col, (kpi, title, colour) in enumerate(kpis):
        ax = fig.add_subplot(3, 2, col + 3)
        ax.scatter(by_phy["phy_idx"], by_phy[kpi], color=colour, s=12, alpha=0.8)
        ax.set_xlabel("PHY combo index (µ × W × MCS)", fontsize=8)
        ax.set_ylabel(title, fontsize=8)
        ax.set_title(f"2-D: {title} vs PHY combo (avg over N UEs)", fontsize=9)
        ax.grid(True, alpha=0.3)

    # row 2: 2-D vs n_ues (averaged over phy combos)
    by_n = df.groupby("n_ues")[["coll_rate", "prr_group"]].mean().reset_index()
    for col, (kpi, title, colour) in enumerate(kpis):
        ax = fig.add_subplot(3, 2, col + 5)
        ax.plot(by_n["n_ues"], by_n[kpi], "o-", color=colour, linewidth=1.8, markersize=6)
        ax.set_xlabel("N UEs", fontsize=8)
        ax.set_ylabel(title, fontsize=8)
        ax.set_title(f"2-D: {title} vs N UEs (avg over PHY combos)", fontsize=9)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    return fig


# ── Figure 2: 3-D + 2-D  per-flow PRR ────────────────────────────────────────

def fig2(df: pd.DataFrame):
    fig = plt.figure(figsize=(20, 14))
    fig.suptitle("s1 — Per-Flow PRR (CAM / Video / BG)", fontsize=14, fontweight="bold")

    flows = list(FLOW_COLORS.keys())

    # row 0: 3-D scatter with 3 colours
    ax3d = fig.add_subplot(3, 1, 1, projection="3d")
    for flow in flows:
        sub = df.dropna(subset=[flow])
        ax3d.scatter(sub["phy_idx"], sub["n_ues"], sub[flow],
                     color=FLOW_COLORS[flow], label=FLOW_LABELS[flow],
                     s=14, alpha=0.7, depthshade=True)
    ax3d.set_xlabel("PHY combo index", fontsize=8, labelpad=6)
    ax3d.set_ylabel("N UEs", fontsize=8)
    ax3d.set_zlabel("PRR", fontsize=8)
    ax3d.set_title("3-D: Per-Flow PRR vs PHY combo × N UEs", fontsize=10)
    ax3d.legend(fontsize=8)

    # row 1: 2-D vs PHY combo
    by_phy = df.groupby("phy_idx")[flows].mean().reset_index()
    ax2a = fig.add_subplot(3, 1, 2)
    for flow in flows:
        ax2a.scatter(by_phy["phy_idx"], by_phy[flow],
                     color=FLOW_COLORS[flow], label=FLOW_LABELS[flow],
                     s=12, alpha=0.8)
    ax2a.set_xlabel("PHY combo index (µ × W × MCS)", fontsize=8)
    ax2a.set_ylabel("PRR", fontsize=8)
    ax2a.set_title("2-D: Per-Flow PRR vs PHY combo (avg over N UEs)", fontsize=9)
    ax2a.legend(fontsize=8)
    ax2a.grid(True, alpha=0.3)

    # row 2: 2-D vs n_ues
    by_n = df.groupby("n_ues")[flows].mean().reset_index()
    ax2b = fig.add_subplot(3, 1, 3)
    for flow in flows:
        ax2b.plot(by_n["n_ues"], by_n[flow], "o-",
                  color=FLOW_COLORS[flow], label=FLOW_LABELS[flow],
                  linewidth=1.8, markersize=6)
    ax2b.set_xlabel("N UEs", fontsize=8)
    ax2b.set_ylabel("PRR", fontsize=8)
    ax2b.set_title("2-D: Per-Flow PRR vs N UEs (avg over PHY combos)", fontsize=9)
    ax2b.legend(fontsize=8)
    ax2b.grid(True, alpha=0.3)

    plt.tight_layout()
    return fig


# ── Figure 3: KPI variation with individual parameters ────────────────────────

def fig3(df: pd.DataFrame):
    params    = [("mu", "Numerology µ"), ("w", "Bandwidth W (PRBs)"), ("mcs", "MCS index")]
    kpi_solo  = [("coll_rate", "Collision Rate", C_COLL),
                 ("prr_group", "Group PRR",      C_GROUP)]
    flows     = list(FLOW_COLORS.keys())

    n_rows = len(params)   # 3 rows: one per parameter
    n_cols = 2             # col 0: coll_rate + prr_group  |  col 1: per-flow PRR

    fig, axes = plt.subplots(n_rows, n_cols, figsize=(16, 14))
    fig.suptitle("s1 — KPI vs Individual PHY Parameters", fontsize=14, fontweight="bold")

    for row, (param, param_label) in enumerate(params):
        by_p = df.groupby(param)[["coll_rate", "prr_group"] + flows].mean().reset_index()

        # col 0: coll_rate and prr_group on twin axes
        ax = axes[row, 0]
        ax2 = ax.twinx()
        l1, = ax.plot(by_p[param], by_p["coll_rate"], "o-", color=C_COLL,
                      linewidth=1.8, markersize=6, label="Coll. Rate")
        l2, = ax2.plot(by_p[param], by_p["prr_group"], "s--", color=C_GROUP,
                       linewidth=1.8, markersize=6, label="Group PRR")
        ax.set_xlabel(param_label, fontsize=8)
        ax.set_ylabel("Collision Rate", color=C_COLL, fontsize=8)
        ax2.set_ylabel("Group PRR", color=C_GROUP, fontsize=8)
        ax.set_title(f"Coll. Rate & Group PRR vs {param_label}", fontsize=9)
        ax.tick_params(axis="y", labelcolor=C_COLL)
        ax2.tick_params(axis="y", labelcolor=C_GROUP)
        ax.legend(handles=[l1, l2], fontsize=8, loc="upper left")
        ax.grid(True, alpha=0.3)
        ax.set_xticks(sorted(df[param].unique()))

        # col 1: per-flow PRR
        ax = axes[row, 1]
        for flow in flows:
            ax.plot(by_p[param], by_p[flow], "o-",
                    color=FLOW_COLORS[flow], label=FLOW_LABELS[flow],
                    linewidth=1.8, markersize=6)
        ax.set_xlabel(param_label, fontsize=8)
        ax.set_ylabel("PRR", fontsize=8)
        ax.set_title(f"Per-Flow PRR vs {param_label}", fontsize=9)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_xticks(sorted(df[param].unique()))

    plt.tight_layout()
    return fig


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    repo_root = Path(__file__).resolve().parents[3]  # scratch_overlay/adaptive-phy-eval/scripts/ → root

    ap = argparse.ArgumentParser(description="Plot s1 sweep results")
    ap.add_argument("--csv", default=str(repo_root / "tmp" / "_summary.csv"),
                    help="Path to _summary.csv (default: <repo>/tmp/_summary.csv)")
    ap.add_argument("--out", default=None,
                    help="Directory to save PNGs (default: show interactively)")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}\nRun analyze_sweep.py first.")

    print(f"Loading {csv_path} ...")
    df, combos = make_phy_index(load(csv_path))
    n_phy = len(combos)
    n_n   = df["n_ues"].nunique()
    print(f"  s1 data: {n_phy} PHY combos × {n_n} group sizes × (aggregated over seeds)")

    f1 = fig1(df, combos)
    f2 = fig2(df)
    f3 = fig3(df)

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        f1.savefig(out / "fig1_coll_group_prr.png", dpi=150, bbox_inches="tight")
        f2.savefig(out / "fig2_per_flow_prr.png",   dpi=150, bbox_inches="tight")
        f3.savefig(out / "fig3_kpi_vs_params.png",  dpi=150, bbox_inches="tight")
        print(f"Saved PNGs to {out}/")
    else:
        plt.show()


if __name__ == "__main__":
    main()
