#!/usr/bin/env python3
"""
plot_pathloss.py
Path-loss component due to distance variation — three platoon scenarios.

Model  : 3GPP TR 37.885, Table 6.2.1-1  (V2V Highway, LOS)
Formula: PL = 32.4 + 20·log₁₀(d₃D [m]) + 20·log₁₀(fᶜ [GHz])  [dB]
fᶜ     : 5.89 GHz  (as set in nr_sl_stack.cc, centralFreqHz = 5.89e9)
Condition: pure LOS (numBlockers = 0 in all three scenarios)

All vehicles travel along the same lane (y = const, z = 0)
→  d₃D = d₂D = |x_leader − x_follower|

Produces:
  plots/pathloss_timeseries.png  — one line per follower, 3 scenarios
  plots/pathloss_delta.png       — ΔPLK (peak-to-peak) per follower, 3 scenarios
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from matplotlib.lines import Line2D
from matplotlib.ticker import MultipleLocator

HERE  = Path(__file__).parent
PLOTS = HERE / "plots"
PLOTS.mkdir(exist_ok=True)

# ── channel constants ─────────────────────────────────────────────────────────
FC_GHZ       = 5.89                              # carrier frequency [GHz]
PL_CONST     = 32.4 + 20 * np.log10(FC_GHZ)     # = 47.80 dB  (constant part)

def pl_los(d_m: np.ndarray) -> np.ndarray:
    """3GPP TR 37.885 V2V Highway LOS path loss [dB].  d_m in metres."""
    return PL_CONST + 20.0 * np.log10(np.maximum(d_m, 0.1))


# ── scenario metadata ─────────────────────────────────────────────────────────
SCENARIOS = {
    "slinky": dict(
        path    = HERE / "ccam_platoon_slinky/ccam_platoon_slinky.mob.csv",
        label   = "Scenario A — Slinky/Accordion",
        joining = [],
        vlines  = [(15,  "peak gap",  ":"),
                   (30,  "min gap",   ":"),
                   (45,  "peak gap",  ":"),
                   (60,  "min gap",   ":"),
                   (75,  "peak gap",  ":")],
    ),
    "gap_open": dict(
        path    = HERE / "ccam_platoon_gap_open/ccam_platoon_gap_open.mob.csv",
        label   = "Scenario B — CACC Gap Opening",
        joining = [],
        vlines  = [(22,  "leader opens",    "--"),
                   (36,  "follower 8\nopens", "--"),
                   (72,  "leader closes",   ":"),
                   (86,  "follower 8\ncloses", ":")],
    ),
    "merge": dict(
        path    = HERE / "ccam_platoon_merge/ccam_platoon_merge.mob.csv",
        label   = "Scenario C — Platoon Merge",
        joining = [5, 6, 7, 8],
        vlines  = [(70, "merge\ncomplete", "--")],
    ),
}

FOLLOWER_CMAP = cm.Blues
JOINING_CMAP  = cm.Greens


# ── helpers ───────────────────────────────────────────────────────────────────
def load_distances(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, comment="#", header=None,
                     names=["time", "node_id", "x", "y", "speed"])
    df["time"]    = df["time"].round(3)
    df["node_id"] = df["node_id"].astype(int)
    pivot = df.pivot(index="time", columns="node_id", values="x")
    dist  = pd.DataFrame(index=pivot.index)
    for nid in sorted(c for c in pivot.columns if c > 0):
        dist[nid] = (pivot[0] - pivot[nid]).clip(lower=0.1)
    return dist


def node_colors(node_ids: list, joining: list) -> dict:
    tight = [n for n in node_ids if n not in joining]
    jveh  = [n for n in node_ids if n in joining]
    colors = {}
    for i, nid in enumerate(tight):
        colors[nid] = FOLLOWER_CMAP(0.30 + 0.70 * (i / max(len(tight) - 1, 1)))
    for i, nid in enumerate(jveh):
        colors[nid] = JOINING_CMAP(0.35 + 0.65 * (i / max(len(jveh) - 1, 1)))
    return colors


# ── Figure 1: PL time-series ──────────────────────────────────────────────────
def plot_timeseries():
    fig, axes = plt.subplots(3, 1, figsize=(13, 13), sharey=False)
    fig.suptitle(
        "Path-loss component due to distance variation\n"
        r"$PL = 32.4 + 20\cdot\log_{10}(d_{3D}\ [\mathrm{m}])"
        r"+ 20\cdot\log_{10}(f_c\ [\mathrm{GHz}])$   "
        rf"[$f_c = {FC_GHZ}$ GHz,  const = {PL_CONST:.2f} dB]"
        "\nModel: 3GPP TR 37.885 Table 6.2.1-1   (V2V Highway LOS, numBlockers=0)",
        fontsize=10, y=1.00,
    )

    for ax, (key, meta) in zip(axes, SCENARIOS.items()):
        dist     = load_distances(meta["path"])
        joining  = meta["joining"]
        node_ids = list(dist.columns)
        colors   = node_colors(node_ids, joining)
        t        = dist.index.values

        # Convert distances → path loss
        pl = pd.DataFrame({nid: pl_los(dist[nid].values) for nid in node_ids},
                          index=dist.index)

        pl_min_all = pl.min().min()
        pl_max_all = pl.max().max()
        y_lo = np.floor(pl_min_all / 2) * 2 - 1
        y_hi = np.ceil(pl_max_all  / 2) * 2 + 1

        for nid in node_ids:
            lw  = 1.5 if nid not in joining else 1.9
            ls  = "-"  if nid not in joining else "--"
            ax.plot(t, pl[nid].values,
                    color=colors[nid], lw=lw, ls=ls, alpha=0.9,
                    zorder=3)

            # Annotate the Δ-PL for each node at the right margin
            delta = pl[nid].max() - pl[nid].min()
            if delta > 0.3:                        # skip near-constant lines
                ax.annotate(
                    f"Δ {delta:.1f} dB",
                    xy=(t[-1], pl[nid].iloc[-1]),
                    xytext=(3, 0), textcoords="offset points",
                    fontsize=6.5, color=colors[nid], va="center",
                    annotation_clip=False,
                )

        # Phase / event markers
        for xv, lbl, vls in meta.get("vlines", []):
            ax.axvline(xv, color="gray", lw=0.9, ls=vls, alpha=0.65, zorder=1)
            ax.text(xv + 0.4, y_hi - 0.3, lbl,
                    fontsize=6, color="dimgray", va="top")

        # Secondary y-axis: corresponding distance
        ax2 = ax.twinx()
        # We want the secondary axis to show the SAME physical scale
        # PL = PL_CONST + 20·log10(d)  →  d = 10^((PL - PL_CONST)/20)
        y_ticks_pl = ax.get_yticks()
        # Mirror the primary axis limits on the right to show distance
        ax2.set_ylim(y_lo, y_hi)

        def pl_to_d(pl_val):
            return 10 ** ((pl_val - PL_CONST) / 20.0)

        # Choose round distance values to mark
        d_marks_m = [10, 20, 50, 100, 150, 200, 250, 300, 350, 400]
        pl_marks  = [PL_CONST + 20*np.log10(d) for d in d_marks_m
                     if y_lo <= PL_CONST + 20*np.log10(d) <= y_hi]
        d_labels  = [f"{int(10**(( v - PL_CONST)/20))} m" for v in pl_marks]
        ax2.set_yticks(pl_marks)
        ax2.set_yticklabels(d_labels, fontsize=7.5, color="saddlebrown")
        ax2.set_ylabel("Distance from leader  [m]", fontsize=8.5, color="saddlebrown")
        ax2.tick_params(axis="y", colors="saddlebrown")

        ax.set_title(meta["label"], fontsize=10, fontweight="bold", pad=4)
        ax.set_xlabel("Simulation time  [s]", fontsize=9)
        ax.set_ylabel("Path loss  [dB]", fontsize=9)
        ax.set_xlim(t[0], t[-1])
        ax.set_ylim(y_lo, y_hi)
        ax.yaxis.set_minor_locator(MultipleLocator(1))
        ax.grid(axis="y", which="major", ls=":", alpha=0.5, zorder=0)
        ax.grid(axis="x", ls=":", alpha=0.3, zorder=0)
        ax.invert_yaxis()          # convention: larger PL = worse link = bottom

        # Reset after invert
        ax.set_ylim(y_hi, y_lo)
        ax2.set_ylim(y_hi, y_lo)

        # Legend
        if joining:
            handles = [
                Line2D([0],[0], color=FOLLOWER_CMAP(0.72), lw=1.5,
                       label="Tight followers (1–4)"),
                Line2D([0],[0], color=JOINING_CMAP(0.72),  lw=1.9, ls="--",
                       label="Joining vehicles (5–8)"),
            ]
        else:
            handles = [
                Line2D([0],[0],
                       color=FOLLOWER_CMAP(0.30 + 0.70*i/7), lw=1.5,
                       label=f"Node {nid}")
                for i, nid in enumerate(node_ids)
            ]
        ax.legend(handles=handles, fontsize=7.5, ncol=4 if not joining else 2,
                  loc="lower right", framealpha=0.85)

    fig.tight_layout(rect=[0, 0, 0.93, 1.0])
    out = PLOTS / "pathloss_timeseries.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── Figure 2: Δ-PL per node (peak-to-peak) — bar chart ───────────────────────
def plot_delta_pl():
    fig, axes = plt.subplots(1, 3, figsize=(14, 5), sharey=False)
    fig.suptitle(
        r"Peak-to-peak path-loss variation  $\Delta PL = PL_{max} - PL_{min}$  per follower node"
        "\n(deterministic component only — no shadowing)",
        fontsize=11, fontweight="bold",
    )

    SCENARIO_COLOR = {"slinky": "#e41a1c", "gap_open": "#377eb8", "merge": "#4daf4a"}

    all_pl_ranges: dict[str, dict[int, tuple]] = {}   # key → nid → (pl_min, pl_max, delta)

    for ax, (key, meta) in zip(axes, SCENARIOS.items()):
        dist     = load_distances(meta["path"])
        joining  = meta["joining"]
        node_ids = list(dist.columns)
        colors   = node_colors(node_ids, joining)

        pl = pd.DataFrame({nid: pl_los(dist[nid].values) for nid in node_ids},
                          index=dist.index)
        deltas = (pl.max() - pl.min()).values
        pl_min = pl.min().values
        pl_max = pl.max().values

        all_pl_ranges[key] = {nid: (pl.min()[nid], pl.max()[nid], pl.max()[nid]-pl.min()[nid])
                              for nid in node_ids}

        bar_colors = [colors[nid] for nid in node_ids]
        xs = np.arange(len(node_ids))
        bars = ax.bar(xs, deltas, color=bar_colors, edgecolor="dimgray", linewidth=0.6, zorder=3)

        # Annotate each bar: PL_min → PL_max
        for i, (nid, bar) in enumerate(zip(node_ids, bars)):
            h = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.05,
                    f"{h:.1f}\ndB", ha="center", va="bottom",
                    fontsize=6.5, color="black", fontweight="bold")
            ax.text(bar.get_x() + bar.get_width()/2, -0.4,
                    f"{pl_min[i]:.0f}",
                    ha="center", va="top", fontsize=5.5, color="steelblue")
            ax.text(bar.get_x() + bar.get_width()/2,
                    h + 0.05 + 1.5,
                    f"{pl_max[i]:.0f}",
                    ha="center", va="bottom", fontsize=5.5, color="firebrick")

        ax.set_xticks(xs)
        xlabels = [f"N{nid}" + ("★" if nid in joining else "") for nid in node_ids]
        ax.set_xticklabels(xlabels, fontsize=8)
        ax.set_title(meta["label"], fontsize=9, fontweight="bold")
        ax.set_ylabel("ΔPL  [dB]", fontsize=9)
        ax.set_ylim(0, max(deltas) * 1.35)
        ax.grid(axis="y", ls=":", alpha=0.5, zorder=0)
        if joining:
            ax.text(0.98, 0.97, "★ = joining vehicle",
                    transform=ax.transAxes, fontsize=6.5,
                    ha="right", va="top", color="forestgreen")

        # Annotate the formula once in the first panel
        if key == "slinky":
            ax.text(0.02, 0.97,
                    rf"$PL_{{min}}$ (blue) ↑ bar ↓ $PL_{{max}}$ (red)",
                    transform=ax.transAxes, fontsize=6, ha="left", va="top",
                    color="dimgray")

    fig.tight_layout()
    out = PLOTS / "pathloss_delta.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")

    # Print summary table
    print(f"\n{'Node':>5}  {'Slinky':^22}  {'Gap open':^22}  {'Merge':^22}")
    print(f"{'':>5}  {'PL_min':>6} {'PL_max':>6} {'ΔPL':>6}  "
          f"{'PL_min':>6} {'PL_max':>6} {'ΔPL':>6}  "
          f"{'PL_min':>6} {'PL_max':>6} {'ΔPL':>6}")
    node_ids_common = sorted(all_pl_ranges["slinky"].keys())
    for nid in node_ids_common:
        row = f"{nid:>5}  "
        for key in ["slinky", "gap_open", "merge"]:
            mn, mx, dlt = all_pl_ranges[key][nid]
            row += f"  {mn:6.1f} {mx:6.1f} {dlt:6.1f}  "
        print(row)


# ── main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\nPath-loss constant (freq term): {PL_CONST:.3f} dB  (fc = {FC_GHZ} GHz)")
    print("Generating plots …")
    plot_timeseries()
    plot_delta_pl()
    print(f"\nAll plots saved to {PLOTS}/")
