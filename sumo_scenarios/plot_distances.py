#!/usr/bin/env python3
"""
plot_distances.py
Distance of each platoon member from the leader — time series + distribution.

Produces two PNG files:
  plots/distance_timeseries.png   — individual traces per follower (3 scenarios)
  plots/distance_heatmap.png      — 2-D density (time × distance) + stats ribbon
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.cm as cm
from matplotlib.lines import Line2D

HERE = Path(__file__).parent
PLOTS = HERE / "plots"
PLOTS.mkdir(exist_ok=True)

# ── scenario metadata ────────────────────────────────────────────────────────
SCENARIOS = {
    "slinky": dict(
        path    = HERE / "ccam_platoon_slinky/ccam_platoon_slinky.mob.csv",
        label   = "Scenario A — Slinky/Accordion",
        joining = [],
        # (x_time, label, ls)
        vlines  = [(15,"t=15 s\n(peak gap)",':'),
                   (30,"t=30 s\n(min gap)",':'),
                   (45,"t=45 s\n(peak gap)",':'),
                   (60,"t=60 s\n(min gap)",':'),
                   (75,"t=75 s\n(peak gap)",':')],
        ylim    = (0, 320),
    ),
    "gap_open": dict(
        path    = HERE / "ccam_platoon_gap_open/ccam_platoon_gap_open.mob.csv",
        label   = "Scenario B — CACC Gap Opening",
        joining = [],
        vlines  = [(22, "leader opens",   "--"),
                   (36, "follower 8\nopens", "--"),
                   (72, "leader closes",  ":"),
                   (86, "follower 8\ncloses", ":")],
        ylim    = (0, 430),
    ),
    "merge": dict(
        path    = HERE / "ccam_platoon_merge/ccam_platoon_merge.mob.csv",
        label   = "Scenario C — Platoon Merge",
        joining = [5, 6, 7, 8],
        vlines  = [(70, "merge\ncomplete", "--")],
        ylim    = (0, 400),
    ),
}

FOLLOWER_CMAP   = cm.Blues   # tight followers: light→dark
JOINING_CMAP    = cm.Greens  # joining vehicles: light→dark


# ── helpers ──────────────────────────────────────────────────────────────────
def load_distances(path: Path) -> pd.DataFrame:
    """Return DataFrame[index=time, columns=1..8] of distance from leader (m)."""
    df = pd.read_csv(path, comment="#", header=None,
                     names=["time", "node_id", "x", "y", "speed"])
    df["time"]    = df["time"].round(3)
    df["node_id"] = df["node_id"].astype(int)
    pivot = df.pivot(index="time", columns="node_id", values="x")
    dist  = pd.DataFrame(index=pivot.index)
    for nid in sorted(c for c in pivot.columns if c > 0):
        dist[nid] = (pivot[0] - pivot[nid]).clip(lower=0)
    return dist


def follower_colors(node_ids: list, joining: list) -> dict:
    """Map node_id → RGBA colour."""
    tight   = [n for n in node_ids if n not in joining]
    jvehicles = [n for n in node_ids if n in joining]
    colors  = {}
    n_t = len(tight)
    for i, nid in enumerate(tight):
        frac = 0.35 + 0.65 * (i / max(n_t - 1, 1))
        colors[nid] = FOLLOWER_CMAP(frac)
    n_j = len(jvehicles)
    for i, nid in enumerate(jvehicles):
        frac = 0.35 + 0.65 * (i / max(n_j - 1, 1))
        colors[nid] = JOINING_CMAP(frac)
    return colors


# ── Figure 1: time-series of individual distances ────────────────────────────
def plot_timeseries():
    fig, axes = plt.subplots(3, 1, figsize=(13, 12), sharey=False)
    fig.suptitle("Distance from leader — individual follower traces",
                 fontsize=13, fontweight="bold", y=0.995)

    for ax, (key, meta) in zip(axes, SCENARIOS.items()):
        dist    = load_distances(meta["path"])
        joining = meta["joining"]
        node_ids = list(dist.columns)
        colors  = follower_colors(node_ids, joining)
        t       = dist.index.values

        for nid in node_ids:
            lw  = 1.4 if nid not in joining else 1.8
            ls  = "-" if nid not in joining else "--"
            ax.plot(t, dist[nid].values,
                    color=colors[nid], lw=lw, ls=ls, alpha=0.9,
                    label=f"node {nid}" + (" (joining)" if nid in joining else ""))

        # Phase / event markers
        for xv, lbl, vls in meta.get("vlines", []):
            ax.axvline(xv, color="gray", lw=0.9, ls=vls, alpha=0.7)
            ax.text(xv + 0.5, meta["ylim"][1] * 0.96, lbl,
                    fontsize=6.5, color="gray", va="top")

        ax.set_title(meta["label"], fontsize=10, fontweight="bold", pad=4)
        ax.set_xlabel("Simulation time  [s]", fontsize=9)
        ax.set_ylabel("Distance from leader  [m]", fontsize=9)
        ax.set_xlim(t[0], t[-1])
        ax.set_ylim(*meta["ylim"])
        ax.grid(axis="y", ls=":", alpha=0.5)
        ax.grid(axis="x", ls=":", alpha=0.3)

        # legend: one entry per "group" to avoid clutter
        if joining:
            handles = [
                Line2D([0],[0], color=FOLLOWER_CMAP(0.7), lw=1.4,
                       label="Tight followers (1–4)"),
                Line2D([0],[0], color=JOINING_CMAP(0.7), lw=1.8, ls="--",
                       label="Joining vehicles (5–8)"),
            ]
        else:
            handles = [
                Line2D([0],[0], color=FOLLOWER_CMAP(0.35 + 0.65*i/7), lw=1.4,
                       label=f"Node {nid}")
                for i, nid in enumerate(node_ids)
            ]
        ax.legend(handles=handles, fontsize=7.5, ncol=4 if not joining else 2,
                  loc="upper left", framealpha=0.8)

    fig.tight_layout(rect=[0, 0, 1, 0.995])
    out = PLOTS / "distance_timeseries.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── Figure 2: 2-D heatmap + stats ribbon ────────────────────────────────────
def plot_heatmap():
    fig, axes = plt.subplots(3, 2, figsize=(15, 12),
                             gridspec_kw={"width_ratios": [2.5, 1]})
    fig.suptitle("Distance-from-leader distribution over time",
                 fontsize=13, fontweight="bold", y=0.998)

    for row, (key, meta) in enumerate(SCENARIOS.items()):
        ax_heat = axes[row, 0]
        ax_box  = axes[row, 1]
        dist    = load_distances(meta["path"])
        joining = meta["joining"]
        t       = dist.index.values
        node_ids = list(dist.columns)
        colors   = follower_colors(node_ids, joining)

        # ---- left: 2-D density heatmap ----
        # Stack all followers into one (time, distance) pair array
        all_t   = np.tile(t, len(node_ids))
        all_d   = np.concatenate([dist[nid].values for nid in node_ids])

        d_max = meta["ylim"][1]
        h, xedges, yedges = np.histogram2d(
            all_t, all_d,
            bins=[min(200, len(t)//2), 80],
            range=[[t[0], t[-1]], [0, d_max]],
        )
        # Normalise each time-column to show proportion (not raw count)
        col_sum = h.sum(axis=1, keepdims=True)
        col_sum[col_sum == 0] = 1
        h_norm = h / col_sum

        im = ax_heat.pcolormesh(
            xedges, yedges, h_norm.T,
            cmap="YlOrRd", shading="auto",
            vmin=0, vmax=h_norm.max() * 0.9,
        )
        fig.colorbar(im, ax=ax_heat, label="Fraction of followers", pad=0.02)

        # Overlay min/mean/max ribbons
        d_min  = dist.min(axis=1)
        d_mean = dist.mean(axis=1)
        d_max_  = dist.max(axis=1)
        d_p25  = dist.quantile(0.25, axis=1)
        d_p75  = dist.quantile(0.75, axis=1)

        ax_heat.fill_between(t, d_p25, d_p75,
                              color="steelblue", alpha=0.30, label="IQR (25–75 %)")
        ax_heat.plot(t, d_mean,  color="steelblue", lw=1.5, label="Mean distance")
        ax_heat.plot(t, d_max_,  color="navy",      lw=1.0, ls="--", label="Max (node 8)")
        ax_heat.plot(t, d_min,   color="cornflowerblue", lw=0.9, ls=":", label="Min (node 1)")

        # Phase lines
        for xv, lbl, vls in meta.get("vlines", []):
            ax_heat.axvline(xv, color="white", lw=1.0, ls=vls, alpha=0.8)

        ax_heat.set_title(meta["label"], fontsize=10, fontweight="bold", pad=4)
        ax_heat.set_xlabel("Simulation time  [s]", fontsize=9)
        ax_heat.set_ylabel("Distance from leader  [m]", fontsize=9)
        ax_heat.set_xlim(t[0], t[-1])
        ax_heat.set_ylim(0, meta["ylim"][1])
        ax_heat.legend(fontsize=7.5, loc="upper right", framealpha=0.85)

        # ---- right: violin / box per follower (full-sim distribution) ----
        data   = [dist[nid].values for nid in node_ids]
        clrs   = [colors[nid] for nid in node_ids]
        labels = [f"N{nid}" + ("★" if nid in joining else "") for nid in node_ids]

        parts = ax_box.violinplot(data, positions=range(len(node_ids)),
                                  showmedians=True, showextrema=True,
                                  widths=0.7)
        for i, (pc, c) in enumerate(zip(parts["bodies"], clrs)):
            pc.set_facecolor(c)
            pc.set_alpha(0.75)
        parts["cmedians"].set_color("black")
        parts["cmedians"].set_linewidth(1.5)
        for part in ("cbars", "cmaxes", "cmins"):
            if part in parts:
                parts[part].set_color("dimgray")
                parts[part].set_linewidth(0.8)

        ax_box.set_xticks(range(len(node_ids)))
        ax_box.set_xticklabels(labels, fontsize=7)
        ax_box.set_ylabel("Distance from leader  [m]", fontsize=8)
        ax_box.set_ylim(0, meta["ylim"][1])
        ax_box.set_title("Distribution per node\n(full sim)", fontsize=8.5)
        ax_box.grid(axis="y", ls=":", alpha=0.5)
        if joining:
            ax_box.text(0.98, 0.97, "★ = joining vehicle",
                        transform=ax_box.transAxes, fontsize=6.5,
                        ha="right", va="top", color="forestgreen")

    fig.tight_layout(rect=[0, 0, 1, 0.996])
    out = PLOTS / "distance_heatmap.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── Figure 3: platoon spread (max-distance) — 3 scenarios overlaid ──────────
def plot_spread_comparison():
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("Platoon spread over time — scenario comparison",
                 fontsize=12, fontweight="bold")

    SCENARIO_COLORS = {
        "slinky":   "#e41a1c",
        "gap_open": "#377eb8",
        "merge":    "#4daf4a",
    }
    SHORT = {
        "slinky":   "A: Slinky",
        "gap_open": "B: Gap open",
        "merge":    "C: Merge",
    }

    ax_max   = axes[0]   # max distance (node 8 / worst-RX)
    ax_spread = axes[1]  # total spread = max_dist - min_dist

    for key, meta in SCENARIOS.items():
        dist  = load_distances(meta["path"])
        t     = dist.index.values
        c     = SCENARIO_COLORS[key]
        lbl   = SHORT[key]

        d_max   = dist.max(axis=1)
        d_min   = dist.min(axis=1)
        spread  = d_max - d_min

        ax_max.plot(t, d_max.values,   color=c, lw=2.0, label=lbl)
        ax_spread.plot(t, spread.values, color=c, lw=2.0, label=lbl)

        # Phase lines
        for xv, lbl_v, vls in meta.get("vlines", []):
            ax_max.axvline(xv, color=c, lw=0.8, ls=vls, alpha=0.4)
            ax_spread.axvline(xv, color=c, lw=0.8, ls=vls, alpha=0.4)

    for ax, title, ylabel in [
        (ax_max,    "Max distance from leader  (= worst-RX range)",
                    "Distance  [m]"),
        (ax_spread, "Total platoon spread  (max − min distance)",
                    "Spread  [m]"),
    ]:
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("Simulation time  [s]", fontsize=9)
        ax.set_ylabel(ylabel, fontsize=9)
        ax.set_ylim(bottom=0)
        ax.grid(ls=":", alpha=0.5)
        ax.legend(fontsize=9)

    fig.tight_layout()
    out = PLOTS / "distance_spread_comparison.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── Figure 4: snapshot violin plots at key time points ───────────────────────
def plot_time_snapshots():
    """
    For each scenario: violin plot of the distribution of follower distances
    at N evenly-spaced time snapshots. Shows how the 'shape' of the distribution
    evolves.
    """
    SNAP_N = 8  # number of time snapshots

    fig, axes = plt.subplots(1, 3, figsize=(16, 6))
    fig.suptitle("Distance-from-leader distribution at time snapshots",
                 fontsize=12, fontweight="bold")

    for ax, (key, meta) in zip(axes, SCENARIOS.items()):
        dist     = load_distances(meta["path"])
        joining  = meta["joining"]
        node_ids = list(dist.columns)
        t        = dist.index.values

        # Pick SNAP_N evenly-spaced timestamps
        snap_idx = np.linspace(0, len(t) - 1, SNAP_N, dtype=int)
        snap_t   = t[snap_idx]

        positions = np.arange(SNAP_N)
        norm      = mcolors.Normalize(vmin=snap_t[0], vmax=snap_t[-1])
        cmap      = cm.plasma

        for pos, st in zip(positions, snap_t):
            d_vals = dist.loc[st].values   # distances of all 8 followers
            c      = cmap(norm(st))

            vp = ax.violinplot([d_vals], positions=[pos],
                               widths=0.75, showmedians=True, showextrema=True)
            for pc in vp["bodies"]:
                pc.set_facecolor(c)
                pc.set_alpha(0.78)
            vp["cmedians"].set_color("white")
            vp["cmedians"].set_linewidth(2.0)
            for part in ("cbars", "cmaxes", "cmins"):
                if part in vp:
                    vp[part].set_color("dimgray")
                    vp[part].set_linewidth(0.8)

        ax.set_xticks(positions)
        ax.set_xticklabels([f"t={st:.0f}s" for st in snap_t],
                           rotation=35, ha="right", fontsize=7)
        ax.set_title(meta["label"], fontsize=9, fontweight="bold")
        ax.set_xlabel("Time snapshot", fontsize=8)
        ax.set_ylabel("Distance from leader  [m]", fontsize=8)
        ax.set_ylim(0, meta["ylim"][1])
        ax.grid(axis="y", ls=":", alpha=0.5)

        # Colourbar for time
        sm = cm.ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        cbar = fig.colorbar(sm, ax=ax, orientation="vertical", pad=0.02, shrink=0.85)
        cbar.set_label("Sim time  [s]", fontsize=7.5)

    fig.tight_layout()
    out = PLOTS / "distance_snapshots.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Generating distance distribution plots …")
    plot_timeseries()
    plot_heatmap()
    plot_spread_comparison()
    plot_time_snapshots()
    print(f"\nAll plots saved to {PLOTS}/")
