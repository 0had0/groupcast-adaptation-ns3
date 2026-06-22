#!/usr/bin/env python3
"""
plot_relay_storm.py
====================
Publication-quality PDF figures from a relay_storm_eval sweep.

Usage
-----
    python3 plot_relay_storm.py [results_dir] [output_dir]

Arguments
----------
results_dir  Directory containing sweep_aggregate.csv and storm_indicator.csv.
              Default: ../results_relay_storm_10seeds (relative to this script).
output_dir   Directory for *.pdf figures.  Default: ./plots.

Requires: matplotlib, numpy (stdlib-only Python — no pandas, no pytest).
"""

import csv
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

matplotlib.use("Agg")
plt.rcParams.update({
    "font.family": "serif",
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "figure.dpi": 150,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
})

# ---------------------------------------------------------------------------
# Color palette (colorblind-safe Tableau-10 subset)
# ---------------------------------------------------------------------------
CLR = {
    "baseline": "#4C78A8",
    "relay4":   "#F58518",
    "relay6":   "#E45756",
    "storm":    "#54A24B",
    "mcd80":   "#9E9E9E",
    "mcd150":  "#607D8B",
    "mcd350":  "#212121",
}
MRK = {"baseline": "o", "relay4": "s", "relay6": "^"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_float(v: str) -> Optional[float]:
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def percentile(vals: List[float], p: float) -> float:
    """Interpolated p-th percentile of a list of floats."""
    s = sorted(v for v in vals if v is not None)
    if not s:
        return float("nan")
    idx = p * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (idx - lo) * (s[hi] - s[lo])


def speed_order(agg: List[Dict]) -> List[int]:
    return sorted({int(float(r["speed"])) for r in agg})


def load_csv(path: Path) -> List[Dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def series_mean(agg: List[Dict],
                speed: int,
                is_baseline: bool,
                relay_node: Optional[int],
                col: str) -> float:
    rows = [r for r in agg
            if int(float(r["speed"])) == speed
            and r["is_baseline"].lower() == str(is_baseline).lower()
            and (relay_node is None
                 or int(float(r.get("relay_node", 0))) == relay_node)]
    vals = [safe_float(r.get(col, "")) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else float("nan")


# Build a {speed: (mean, p5, p95)} dict for a given filter and column.
# p5/p95 are interpolated percentiles across seeds for each speed bucket.
def build_series(agg: List[Dict],
                 is_baseline: bool,
                 relay_node: Optional[int],
                 col: str,
                 p5_col: Optional[str] = None,
                 p95_col: Optional[str] = None,
                 ) -> Dict[int, Tuple[float, float, float]]:
    out: Dict[int, Tuple[float, float, float]] = {}
    for s in speed_order(agg):
        rows = [r for r in agg
                if int(float(r["speed"])) == s
                and r["is_baseline"].lower() == str(is_baseline).lower()
                and (relay_node is None
                     or int(float(r.get("relay_node", 0))) == relay_node)]
        vals = [safe_float(r.get(col, "")) for r in rows]
        vals = [v for v in vals if v is not None]
        mn = sum(vals) / len(vals) if vals else float("nan")

        if p5_col and p95_col:
            p5s  = [safe_float(r.get(p5_col,  "")) for r in rows]
            p5s  = [v for v in p5s if v is not None]
            p95s = [safe_float(r.get(p95_col, "")) for r in rows]
            p95s = [v for v in p95s if v is not None]
            p5v  = percentile(p5s,  0.05) if p5s  else mn
            p95v = percentile(p95s, 0.95) if p95s else mn
        else:
            p5v = mn
            p95v = mn
        out[s] = (mn, p5v, p95v)
    return out


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------

def grouped_bar(ax, speeds, groups, labels, colors, width=0.22,
                ylabel="", title="", ymin=None, ymax=None,
                legend_loc="best"):
    x = np.arange(len(speeds))
    for i, (label, vals, color) in enumerate(zip(labels, groups, colors)):
        offs = x + (i - len(groups) / 2 + 0.5) * width
        means = [(lambda v: v[0] if isinstance(v, tuple) else v)(vals.get(spd, 0.0))
                 for spd in speeds]
        ax.bar(offs, means,
               width * 0.9, label=label, color=color, alpha=0.88,
               edgecolor="white", linewidth=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels([str(s) for s in speeds])
    ax.set_xlabel("Speed (km/h)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if ymin is not None: ax.set_ylim(bottom=ymin)
    if ymax is not None: ax.set_ylim(top=ymax)
    ax.yaxis.grid(True, alpha=0.35)
    ax.legend(loc=legend_loc)


def line_band(ax, speeds, series, labels, colors, markers,
              ylabel="", title="", ymin=None, ymax=None,
              legend_loc="best"):
    for label, vals, color, marker in zip(labels, series, colors, markers):
        # vals: {speed: float}  or  {speed: (mean, p5, p95)}
        def _unpack(v):
            return (v[0], v[1], v[2]) if isinstance(v, tuple) else (v, v, v)
        means_list = [_unpack(vals.get(s, 0.0))[0] for s in speeds]
        p5s_list   = [_unpack(vals.get(s, 0.0))[1] for s in speeds]
        p95s_list  = [_unpack(vals.get(s, 0.0))[2] for s in speeds]
        ax.plot(speeds, means_list, label=label, color=color,
                marker=marker, markersize=5, linewidth=1.5)
        ax.fill_between(speeds, p5s_list, p95s_list, color=color, alpha=0.14)
    ax.set_xlabel("Speed (km/h)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if ymin is not None: ax.set_ylim(bottom=ymin)
    if ymax is not None: ax.set_ylim(top=ymax)
    ax.yaxis.grid(True, alpha=0.35)
    ax.legend(loc=legend_loc)


# ---------------------------------------------------------------------------
# Figure 1: 6-panel overview
# ---------------------------------------------------------------------------

def fig_overview(agg, storm):
    speeds = speed_order(agg)

    fig = plt.figure(figsize=(12, 7))
    fig.suptitle(
        "relay_storm_eval — ReTX Storm from Same-L2ID Relay in 9-Vehicle NR-V2X Platoon\n"
        "(1050 runs · 10 seeds · MCD ∈ {80,100,150,300,350} m; MCD ≈ platoon length ≈ 200 m)",
        fontsize=10, y=1.01,
    )

    # ── A: Storm ratio ──────────────────────────────────────────────────
    ax = fig.add_subplot(2, 3, 1)
    storm_by_s = {int(float(r["speed"])): safe_float(r["storm_ratio"]) or 0
                  for r in storm}
    ratios = [storm_by_s.get(s, 0) for s in speeds]
    bars = ax.bar([str(s) for s in speeds], ratios,
                  color=CLR["storm"], alpha=0.82,
                  edgecolor="white", linewidth=0.5)
    for bar, val in zip(bars, ratios):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.03,
                f"{val:.2f}×", ha="center", va="bottom", fontsize=7)
    ax.axhline(1.0, color="grey", linewidth=1, linestyle="--", label="no storm")
    ax.set_xlabel("Speed (km/h)")
    ax.set_ylabel("Storm ratio\n(relay / baseline reTX)")
    ax.set_title("A. ReTX Storm Overhead")
    ax.set_ylim(0, ax.get_ylim()[1] * 1.15)
    ax.yaxis.grid(True, alpha=0.35)
    ax.legend(loc="upper right", fontsize=7)

    # ── B: Mean PRR ────────────────────────────────────────────────────
    ax = fig.add_subplot(2, 3, 2)
    b  = build_series(agg, True,  None, "prr_mean_agg_mean",
                      p5_col="prr_mean_agg_p5", p95_col="prr_mean_agg_p95")
    r4 = build_series(agg, False, 4, "prr_mean_agg_mean",
                      p5_col="prr_mean_agg_p5", p95_col="prr_mean_agg_p95")
    r6 = build_series(agg, False, 6, "prr_mean_agg_mean",
                      p5_col="prr_mean_agg_p5", p95_col="prr_mean_agg_p95")
    grouped_bar(ax, speeds,
                [b, r4, r6],
                ["Baseline", "Relay node 4", "Relay node 6"],
                [CLR["baseline"], CLR["relay4"], CLR["relay6"]],
                ylabel="Mean PRR",
                title="B. Packet Reception Rate",
                ymin=0.90, ymax=1.0,
                legend_loc="lower left")

    # ── C: Worst-case PRR (last vehicle) ────────────────────────────────
    ax = fig.add_subplot(2, 3, 3)
    pw_b  = build_series(agg, True,  None, "prr_worst_agg_mean",
                        p5_col="prr_worst_agg_p5", p95_col="prr_worst_agg_p95")
    pw_r4 = build_series(agg, False, 4, "prr_worst_agg_mean",
                        p5_col="prr_worst_agg_p5", p95_col="prr_worst_agg_p95")
    pw_r6 = build_series(agg, False, 6, "prr_worst_agg_mean",
                        p5_col="prr_worst_agg_p5", p95_col="prr_worst_agg_p95")
    line_band(ax, speeds,
              [pw_b, pw_r4, pw_r6],
              ["Baseline", "Relay node 4", "Relay node 6"],
              [CLR["baseline"], CLR["relay4"], CLR["relay6"]],
              [MRK["baseline"], MRK["relay4"], MRK["relay6"]],
              ylabel="Worst-case PRR\n(tail vehicle)",
              title="C. Tail-End Vehicle PRR",
              ymin=0.80, ymax=1.0)

    # ── D: Mean AoI ────────────────────────────────────────────────────
    ax = fig.add_subplot(2, 3, 4)
    aoi_b  = build_series(agg, True,  None, "aoi_mean_ms_agg_mean",
                          p5_col="aoi_mean_ms_agg_p5", p95_col="aoi_mean_ms_agg_p95")
    aoi_r4 = build_series(agg, False, 4, "aoi_mean_ms_agg_mean",
                          p5_col="aoi_mean_ms_agg_p5", p95_col="aoi_mean_ms_agg_p95")
    aoi_r6 = build_series(agg, False, 6, "aoi_mean_ms_agg_mean",
                          p5_col="aoi_mean_ms_agg_p5", p95_col="aoi_mean_ms_agg_p95")
    line_band(ax, speeds,
              [aoi_b, aoi_r4, aoi_r6],
              ["Baseline", "Relay node 4", "Relay node 6"],
              [CLR["baseline"], CLR["relay4"], CLR["relay6"]],
              [MRK["baseline"], MRK["relay4"], MRK["relay6"]],
              ylabel="Mean AoI (ms)",
              title="D. Age of Information",
              ymin=0)

    # ── E: HARQ NACK rate ──────────────────────────────────────────────
    ax = fig.add_subplot(2, 3, 5)
    nk_b  = build_series(agg, True,  None, "harq_nack_rate_agg_mean")
    nk_r4 = build_series(agg, False, 4, "harq_nack_rate_agg_mean")
    nk_r6 = build_series(agg, False, 6, "harq_nack_rate_agg_mean")
    grouped_bar(ax, speeds,
                [nk_b, nk_r4, nk_r6],
                ["Baseline", "Relay node 4", "Relay node 6"],
                [CLR["baseline"], CLR["relay4"], CLR["relay6"]],
                ylabel="HARQ NACK rate",
                title="E. HARQ NACK Rate",
                ymin=0,
                legend_loc="upper left")

    # ── F: Channel occupancy ────────────────────────────────────────────
    ax = fig.add_subplot(2, 3, 6)
    occ_b  = build_series(agg, True,  None, "ch_occupancy_agg_mean")
    occ_r4 = build_series(agg, False, 4, "ch_occupancy_agg_mean")
    occ_r6 = build_series(agg, False, 6, "ch_occupancy_agg_mean")
    line_band(ax, speeds,
              [occ_b, occ_r4, occ_r6],
              ["Baseline", "Relay node 4", "Relay node 6"],
              [CLR["baseline"], CLR["relay4"], CLR["relay6"]],
              [MRK["baseline"], MRK["relay4"], MRK["relay6"]],
              ylabel="PSSCH channel\noccupancy (fraction)",
              title="F. Sidelink Channel Occupancy",
              ymin=0)

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Figure 2: storm detail + MCD independence evidence
# ---------------------------------------------------------------------------

def fig_storm_detail(agg, storm):
    speeds = speed_order(agg)
    mcd_vals = sorted({r["mcd"] for r in storm})
    cmap = plt.cm.Greys(np.linspace(0.35, 0.85, len(mcd_vals)))

    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    fig.suptitle("Relayed Storm — Supporting Evidence", fontsize=10)

    # G: MCD independence — storm ratio flat across all MCD values
    ax = axes[0]
    for mc, color in zip(mcd_vals, cmap):
        rows = [r for r in storm if r["mcd"] == mc]
        by_s = {int(float(r["speed"])): safe_float(r.get("storm_ratio")) or 0
                 for r in rows}
        label = f"MCD={mc.split('.')[0]} m"
        ax.plot(speeds, [by_s.get(s, np.nan) for s in speeds],
                marker="o", markersize=4, linewidth=1.2,
                color=color, label=label)
    ax.set_xlabel("Speed (km/h)")
    ax.set_ylabel("Storm ratio")
    ax.set_title("G. MCD Independence\n(all MCD values yield identical storm ratio)")
    ax.yaxis.grid(True, alpha=0.35)
    ax.legend(title="MCD", fontsize=7, title_fontsize=7)
    ax.set_ylim(0, None)

    # H: raw reTX counts baseline vs relay
    ax = axes[1]
    base_retx = {int(float(r["speed"])): safe_float(r.get("baseline_hop1_retx_mean")) or 0
                  for r in storm}
    relay_retx = {int(float(r["speed"])): safe_float(r.get("relay_hop1_retx_mean")) or 0
                   for r in storm}
    x = np.arange(len(speeds))
    w = 0.35
    ax.bar(x - w/2, [base_retx.get(s, 0)  for s in speeds],
           w, label="Baseline reTX", color=CLR["baseline"], alpha=0.82)
    ax.bar(x + w/2, [relay_retx.get(s, 0) for s in speeds],
           w, label="Relay reTX",    color=CLR["relay4"],    alpha=0.82)
    ax.set_xticks(x)
    ax.set_xticklabels([str(s) for s in speeds])
    ax.set_xlabel("Speed (km/h)")
    ax.set_ylabel("PSSCH reTX count\n(5 s measurement window)")
    ax.set_title("H. Leader PSSCH Retx: Baseline vs Storm")
    ax.yaxis.grid(True, alpha=0.35)
    ax.legend(fontsize=8)

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Summary table to stdout
# ---------------------------------------------------------------------------

def print_summary(agg, storm):
    speeds = speed_order(agg)
    storm_by_s = {int(float(r["speed"])): safe_float(r.get("storm_ratio")) or 0
                  for r in storm}

    print(f"\n{'Speed':>5}  {'storm':>6}  {'PRR_b':>7}  {'PRR_r4':>7}  {'PRR_r6':>7}"
          f"  {'AoI_b':>7}  {'AoI_r4':>7}  {'AoI_r6':>7}"
          f"  {'NACK_b':>7}  {'NACK_r':>7}")
    for s in speeds:
        sr = storm_by_s.get(s, 0)
        pb = series_mean(agg, s, True,  None, "prr_mean_agg_mean")
        pr4 = series_mean(agg, s, False, 4, "prr_mean_agg_mean")
        pr6 = series_mean(agg, s, False, 6, "prr_mean_agg_mean")
        ab = series_mean(agg, s, True,  None, "aoi_mean_ms_agg_mean")
        ar4 = series_mean(agg, s, False, 4, "aoi_mean_ms_agg_mean")
        ar6 = series_mean(agg, s, False, 6, "aoi_mean_ms_agg_mean")
        nb = series_mean(agg, s, True,  None, "harq_nack_rate_agg_mean")
        nr4 = series_mean(agg, s, False, 4, "harq_nack_rate_agg_mean")
        print(f"{s:>5}  {sr:>6.2f}  {pb:>7.4f}  {pr4:>7.4f}  {pr6:>7.4f}"
              f"  {ab:>7.1f}  {ar4:>7.1f}  {ar6:>7.1f}"
              f"  {nb:>7.4f}  {nr4:>7.4f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    script_dir = Path(__file__).parent.resolve()

    # Resolve results directory
    if len(sys.argv) > 1:
        results_dir = Path(sys.argv[1])
    else:
        project_root = script_dir.parent.parent
        results_dir = project_root / "results_relay_storm_10seeds"

    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else script_dir / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)

    agg_path   = results_dir / "sweep_aggregate.csv"
    storm_path = results_dir / "storm_indicator.csv"

    for p in [agg_path, storm_path]:
        if not p.exists():
            print(f"Error: {p} not found", file=sys.stderr)
            sys.exit(1)

    agg   = load_csv(agg_path)
    storm = load_csv(storm_path)

    print(f"Loaded {len(agg)} aggregate rows, {len(storm)} storm rows "
          f"  speeds={speed_order(agg)} km/h")

    fig = fig_overview(agg, storm)
    out = out_dir / "fig1_overview.pdf"
    fig.savefig(out)
    print(f"  → {out}")

    fig2 = fig_storm_detail(agg, storm)
    out2 = out_dir / "fig2_storm_detail.pdf"
    fig2.savefig(out2)
    print(f"  → {out2}")

    print_summary(agg, storm)
    print(f"\nPlots: {out_dir}/")
    print("Done.")


if __name__ == "__main__":
    main()
