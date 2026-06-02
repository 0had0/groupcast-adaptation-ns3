#!/usr/bin/env python3
"""
plot_mcs_sweep.py
MCS sweep (7, 10, 12, 14, 20) effect on KPIs for 3 platoon scenarios.

Metrics shown for every MCS value
──────────────────────────────────
  • PRR             — packet reception ratio
  • PIR             — mean inter-reception time  [ms]
  • Throughput      — goodput per RX             [kbps]
  • Channel occ.    — fraction of BWP RBs used per TX slot
  • Wasted subch.   — subchannel equivalents wasted by padding per TX

Two statistics per metric
─────────────────────────
  • worst RX  = min PRR across 8 nodes (per seed), then mean ± 90 % CI
                (equiv. max PIR / min tput for the corresponding metric)
  • p5 RX     = 5th percentile across all {seed × node} = 80 values
                (≈ robust tail — bottom ~4 of 80 observations)
  • mean RX   = mean across all 8 nodes and 10 seeds (reference line)

Channel occupancy and wasted subchannels are TX-side (same for every RX),
so only mean ± 90 % CI across seeds is shown for those two panels.

Output:  sumo_scenarios/plots/mcs_sweep_kpis.png
         sumo_scenarios/plots/mcs_sweep_channel.png
         sumo_scenarios/plots/mcs_sweep_combined.png
"""

from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import MultipleLocator
import warnings
warnings.filterwarnings("ignore")

# ── paths ─────────────────────────────────────────────────────────────────────
HERE    = Path(__file__).parent
PLOTS   = HERE / "plots"
PLOTS.mkdir(exist_ok=True)
RESULTS = (HERE.parent / "scratch_overlay" / "platoon_baseline_eval"
           / "results_mcs_sweep")

# ── constants ─────────────────────────────────────────────────────────────────
MCS_VALUES  = [7, 10, 12, 14, 20]
N_SEEDS     = 10
RX_NODES    = list(range(1, 9))          # nodes 1–8
BWP_TOTAL   = 100                        # total BWP PRBs
SCH_SIZE    = 10                         # PRBs per subchannel
TX_RATE_HZ  = 100                        # periodic2 → 10 ms RRI

SCENARIOS = {
    "slinky":   dict(label="Sc. A — Slinky",    key="ccam_platoon_slinky",    color="#e41a1c"),
    "gap_open": dict(label="Sc. B — Gap Open",  key="ccam_platoon_gap_open",  color="#377eb8"),
    "merge":    dict(label="Sc. C — Merge",     key="ccam_platoon_merge",     color="#4daf4a"),
}

# line styles for worst / p5 / mean
LS_WORST = "-"
LS_P5    = "--"
LS_MEAN  = ":"
LW       = 1.8


# ── data loader ───────────────────────────────────────────────────────────────
def load_scalar_kpis(scen_key: str) -> dict:
    """
    Returns nested dict:  mcs → seed → nid → {prr, pir_ms, tput_kbps}
    Uses the pre-computed avrgPrr / avrgPir / thput tables for speed.
    """
    data = {}
    for mcs in MCS_VALUES:
        data[mcs] = {}
        for s in range(1, N_SEEDS + 1):
            db = RESULTS / scen_key / f"{scen_key}_mcs{mcs}_seed{s}.db"
            if not db.exists():
                continue
            con = sqlite3.connect(db)
            # PRR: avrgPrr only has a 'tx' row with overall PRR across all RX.
            # We need per-RX PRR → derive from pktTxRx counts.
            pkt = pd.read_sql_query(
                "SELECT txRx, nodeId, pktSeqNum FROM pktTxRx", con)
            n_tx = len(pkt[pkt.txRx == "tx"])
            # PIR from avrgPir (one row per RX node)
            pir_df = pd.read_sql_query(
                "SELECT nodeId, avrgPirSec FROM avrgPir WHERE txRx='rx'", con)
            # Throughput from thput table
            tput_df = pd.read_sql_query(
                "SELECT nodeId, thputKbps FROM thput WHERE txRx='rx'", con)
            con.close()

            seed_dict = {}
            for nid in RX_NODES:
                n_rx = len(pkt[(pkt.txRx == "rx") & (pkt.nodeId == nid)])
                prr  = n_rx / n_tx if n_tx > 0 else np.nan

                pir_row  = pir_df[pir_df.nodeId == nid]
                pir_ms   = float(pir_row.avrgPirSec.values[0]) * 1000 \
                           if len(pir_row) else np.nan

                tput_row = tput_df[tput_df.nodeId == nid]
                tput     = float(tput_row.thputKbps.values[0]) \
                           if len(tput_row) else np.nan

                seed_dict[nid] = dict(prr=prr, pir_ms=pir_ms, tput_kbps=tput)
            data[mcs][s] = seed_dict
    return data


def load_chan_kpis(scen_key: str) -> dict:
    """
    Returns nested dict: mcs → seed → {occ_mean, waste_mean}
    Channel occupancy = mean(rbLen) / BWP_TOTAL
    Wasted subch      = numSubCh × (1 – pktSize/tbSize)
    """
    data = {}
    for mcs in MCS_VALUES:
        data[mcs] = {}
        for s in range(1, N_SEEDS + 1):
            db = RESULTS / scen_key / f"{scen_key}_mcs{mcs}_seed{s}.db"
            if not db.exists():
                continue
            con = sqlite3.connect(db)
            tx_mac = pd.read_sql_query(
                "SELECT rbLen FROM psschTxUeMac", con)
            rx_phy = pd.read_sql_query(
                "SELECT psschRbLen, tbSizeBytes FROM psschRxUePhy "
                "WHERE txRnti=1 AND rnti=2 AND ndi=1", con)
            pkt_sz_row = pd.read_sql_query(
                "SELECT pktSizeBytes FROM pktTxRx WHERE txRx='tx' LIMIT 500", con)
            con.close()

            occ   = tx_mac.rbLen.mean() / BWP_TOTAL if len(tx_mac) > 0 else np.nan
            p_sz  = int(pkt_sz_row.pktSizeBytes.mode()[0]) \
                    if len(pkt_sz_row) > 0 else 800
            if len(rx_phy) > 0:
                fill   = np.minimum(p_sz / rx_phy.tbSizeBytes.values, 1.0)
                nSubCh = rx_phy.psschRbLen.values / SCH_SIZE
                waste  = np.mean(nSubCh * (1.0 - fill))
            else:
                waste = np.nan

            data[mcs][s] = dict(occ=occ, waste=waste)
    return data


# ── aggregation ───────────────────────────────────────────────────────────────
def aggregate_rx_metric(data: dict, field: str,
                        higher_is_worse: bool = False) -> dict:
    """
    Returns dict:  mcs → {worst_mean, worst_lo, worst_hi,
                           p5_mean, p5_lo, p5_hi,
                           mean_mean, mean_lo, mean_hi}
    worst = per-seed min (or max if higher_is_worse), then CI across seeds.
    p5    = 5th percentile across all seed × node values (80 per MCS).
    """
    out = {}
    for mcs in MCS_VALUES:
        mcs_data = data[mcs]
        worst_per_seed, mean_per_seed, all_vals = [], [], []

        for s, seed_dict in mcs_data.items():
            vals = [seed_dict[nid][field]
                    for nid in RX_NODES if not np.isnan(seed_dict[nid][field])]
            if not vals:
                continue
            all_vals.extend(vals)
            if higher_is_worse:
                worst_per_seed.append(max(vals))
            else:
                worst_per_seed.append(min(vals))
            mean_per_seed.append(np.mean(vals))

        def _ci(arr):
            if not arr:
                return np.nan, np.nan, np.nan
            return (np.mean(arr),
                    np.percentile(arr, 5),
                    np.percentile(arr, 95))

        wm, wlo, whi = _ci(worst_per_seed)
        mm, mlo, mhi = _ci(mean_per_seed)
        p5 = np.nanpercentile(all_vals, 5)  if all_vals else np.nan
        p95= np.nanpercentile(all_vals, 95) if all_vals else np.nan

        out[mcs] = dict(
            worst_mean=wm, worst_lo=wlo, worst_hi=whi,
            p5_mean=p5,    p5_lo=p5,    p5_hi=p95,   # use p95 as upper for PIR
            mean_mean=mm,  mean_lo=mlo, mean_hi=mhi,
        )
    return out


def aggregate_chan_metric(data: dict, field: str) -> dict:
    """Single-value per seed → mean ± CI across seeds."""
    out = {}
    for mcs in MCS_VALUES:
        vals = [v[field] for v in data[mcs].values() if not np.isnan(v[field])]
        if not vals:
            out[mcs] = dict(mean=np.nan, lo=np.nan, hi=np.nan)
        else:
            out[mcs] = dict(mean=np.mean(vals),
                            lo=np.percentile(vals, 5),
                            hi=np.percentile(vals, 95))
    return out


# ── plotting helpers ──────────────────────────────────────────────────────────
def _xs():
    return np.array(MCS_VALUES)


def _draw_metric(ax, agg, color, label_prefix="", higher_is_worse=False):
    xs  = _xs()
    wm  = [agg[m]["worst_mean"] for m in MCS_VALUES]
    wlo = [agg[m]["worst_lo"]   for m in MCS_VALUES]
    whi = [agg[m]["worst_hi"]   for m in MCS_VALUES]
    p5  = [agg[m]["p5_mean"]    for m in MCS_VALUES]
    mm  = [agg[m]["mean_mean"]  for m in MCS_VALUES]
    mlo = [agg[m]["mean_lo"]    for m in MCS_VALUES]
    mhi = [agg[m]["mean_hi"]    for m in MCS_VALUES]

    # worst RX
    ax.plot(xs, wm, color=color, lw=LW, ls=LS_WORST, marker="o", ms=5, zorder=4,
            label=f"{label_prefix}Worst RX")
    ax.fill_between(xs, wlo, whi, color=color, alpha=0.15, zorder=2)
    # p5 line (dashed)
    ax.plot(xs, p5, color=color, lw=LW, ls=LS_P5, marker="s", ms=4,
            alpha=0.80, zorder=3,
            label=f"{label_prefix}p5  RX")
    # mean (dotted, thin reference)
    ax.plot(xs, mm, color=color, lw=1.1, ls=LS_MEAN, zorder=2, alpha=0.65,
            label=f"{label_prefix}Mean RX")
    ax.fill_between(xs, mlo, mhi, color=color, alpha=0.08, zorder=1)


def _draw_chan(ax, agg, color, label=""):
    xs  = _xs()
    m   = [agg[v]["mean"] for v in MCS_VALUES]
    lo  = [agg[v]["lo"]   for v in MCS_VALUES]
    hi  = [agg[v]["hi"]   for v in MCS_VALUES]
    ax.plot(xs, m, color=color, lw=LW, ls=LS_WORST, marker="o", ms=5,
            zorder=4, label=label)
    ax.fill_between(xs, lo, hi, color=color, alpha=0.18, zorder=2)


def _mcs_axis(ax):
    ax.set_xticks(MCS_VALUES)
    ax.set_xticklabels([f"MCS\n{m}" for m in MCS_VALUES], fontsize=8)
    ax.set_xlabel("MCS index", fontsize=8.5)
    ax.grid(axis="y", ls=":", alpha=0.45, zorder=0)
    ax.grid(axis="x", ls=":", alpha=0.2,  zorder=0)


# ── Figure 1 : PRR / PIR / Tput per scenario (3 × 3) ─────────────────────────
def plot_rx_kpis():
    fig, axes = plt.subplots(3, 3, figsize=(14, 11), sharey="row")
    fig.suptitle(
        "MCS sweep — per-RX KPIs: worst RX (solid ●), p5 RX (dashed ■), mean RX (dotted)\n"
        f"periodic2 · 100 Hz · {N_SEEDS} seeds · 3 platoon scenarios · V2V-Highway LOS",
        fontsize=10.5, fontweight="bold", y=1.01,
    )

    metrics = [
        ("prr",       False, "PRR",              (0, 1.05),
         lambda ax: ax.yaxis.set_major_formatter(
             plt.FuncFormatter(lambda v, _: f"{v:.0%}"))),
        ("pir_ms",    True,  "Mean PIR  [ms]",   (0, None), None),
        ("tput_kbps", False, "Throughput  [kbps]",(0, None), None),
    ]

    for row, (field, hiw, ylabel, ylim, extra) in enumerate(metrics):
        for col, (key, meta) in enumerate(SCENARIOS.items()):
            ax = axes[row, col]
            scalar = load_scalar_kpis(meta["key"])
            agg    = aggregate_rx_metric(scalar, field, higher_is_worse=hiw)
            _draw_metric(ax, agg, meta["color"])
            _mcs_axis(ax)

            if ylim[0] is not None:
                ax.set_ylim(bottom=ylim[0])
            if ylim[1] is not None:
                ax.set_ylim(top=ylim[1])
            if extra:
                extra(ax)

            if row == 0:
                ax.set_title(meta["label"], fontsize=10, fontweight="bold", pad=4)
            if col == 0:
                ax.set_ylabel(ylabel, fontsize=9)

            # Reference lines
            if field == "prr":
                ax.axhline(1.0, color="gray", lw=0.8, ls="--", alpha=0.5)
            if field == "pir_ms":
                ax.axhline(10.0, color="firebrick", lw=0.9, ls=":", alpha=0.7,
                           label="Nominal period (10 ms)")

    # Legend on first panel
    legend_handles = [
        Line2D([0],[0], color="gray", lw=LW, ls=LS_WORST, marker="o", ms=5,
               label="Worst RX (min across nodes)"),
        Line2D([0],[0], color="gray", lw=LW, ls=LS_P5,    marker="s", ms=4,
               label="p5 RX  (5th-pct across seeds×nodes)"),
        Line2D([0],[0], color="gray", lw=1.1, ls=LS_MEAN,
               label="Mean RX"),
        Line2D([0],[0], color="firebrick", lw=0.9, ls=":",
               label="Nominal TX period"),
    ]
    axes[0, 2].legend(handles=legend_handles, fontsize=7.5,
                      loc="lower right", framealpha=0.88)
    # Scenario colour patches for row 0
    for col, (key, meta) in enumerate(SCENARIOS.items()):
        axes[0, col].set_facecolor(meta["color"] + "08")

    fig.tight_layout()
    out = PLOTS / "mcs_sweep_kpis.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── Figure 2 : Channel occ + wasted subch (2 × 3) ───────────────────────────
def plot_chan_kpis():
    fig, axes = plt.subplots(2, 3, figsize=(14, 7), sharey="row")
    fig.suptitle(
        "MCS sweep — channel resource utilisation  (TX-side, mean ± 90 % CI across seeds)\n"
        "Top: channel occupancy = mean RBs allocated / 100 BWP RBs   |   "
        "Bottom: wasted subchannel equivalents = numSubCh × (1 − TB fill ratio)",
        fontsize=10, y=1.01,
    )

    for col, (key, meta) in enumerate(SCENARIOS.items()):
        chan = load_chan_kpis(meta["key"])
        agg_occ   = aggregate_chan_metric(chan, "occ")
        agg_waste = aggregate_chan_metric(chan, "waste")

        ax0 = axes[0, col]
        _draw_chan(ax0, {m: dict(mean=agg_occ[m]["mean"]*100,
                                lo=agg_occ[m]["lo"]*100,
                                hi=agg_occ[m]["hi"]*100)
                        for m in MCS_VALUES},
                  meta["color"], label="Channel occupancy")
        ax0.set_ylim(0, 105)
        ax0.axhline(100, color="gray", lw=0.8, ls="--", alpha=0.4)
        ax0.set_title(meta["label"], fontsize=10, fontweight="bold", pad=4)
        _mcs_axis(ax0)
        if col == 0:
            ax0.set_ylabel("Channel occupancy  [%]", fontsize=9)

        ax1 = axes[1, col]
        _draw_chan(ax1, agg_waste, meta["color"], label="Wasted subch. equiv.")
        ax1.set_ylim(bottom=0)
        _mcs_axis(ax1)
        if col == 0:
            ax1.set_ylabel("Wasted subchannel equivalents", fontsize=9)

        # Annotate mean value at each MCS
        for mcs in MCS_VALUES:
            v0 = agg_occ[mcs]["mean"] * 100
            v1 = agg_waste[mcs]["mean"]
            if not np.isnan(v0):
                ax0.text(mcs, v0 + 1.5, f"{v0:.0f}%",
                         ha="center", va="bottom", fontsize=6.5, color=meta["color"])
            if not np.isnan(v1):
                ax1.text(mcs, v1 + 0.02, f"{v1:.2f}",
                         ha="center", va="bottom", fontsize=6.5, color=meta["color"])

    fig.tight_layout()
    out = PLOTS / "mcs_sweep_channel.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── Figure 3 : Combined 5 × 3 summary ────────────────────────────────────────
def plot_combined():
    """
    5 rows × 3 cols:
      row 0 — PRR
      row 1 — PIR
      row 2 — Throughput
      row 3 — Channel occupancy
      row 4 — Wasted subchannels
    """
    fig, axes = plt.subplots(5, 3, figsize=(14, 17))
    fig.suptitle(
        "MCS sweep — full KPI summary\n"
        "Per-RX (rows 0–2): worst RX ●—, p5 RX ■--, mean RX …   "
        "Channel (rows 3–4): mean ± 90 % CI  |  10 seeds per point",
        fontsize=10.5, fontweight="bold", y=1.005,
    )

    ylabels = [
        "PRR",
        "Mean PIR  [ms]",
        "Throughput  [kbps]",
        "Channel occupancy  [%]",
        "Wasted subch. equiv.",
    ]

    # Cache loaded data (avoid re-loading per row)
    all_scalar = {key: None for key in SCENARIOS}
    all_chan   = {key: None for key in SCENARIOS}
    for key, meta in SCENARIOS.items():
        print(f"  Loading {meta['key']} …")
        all_scalar[key] = load_scalar_kpis(meta["key"])
        all_chan[key]   = load_chan_kpis(meta["key"])

    metrics = [
        ("prr",       False, (0, 1.05), True,  False),   # field, higher_is_worse, ylim, fmt%, ref10ms
        ("pir_ms",    True,  (0, None), False, True),
        ("tput_kbps", False, (0, None), False, False),
    ]

    for col, (key, meta) in enumerate(SCENARIOS.items()):
        scalar = all_scalar[key]
        chan   = all_chan[key]

        # ── rows 0-2: per-RX metrics ─────────────────────────────────────────
        for row, (field, hiw, ylim, fmt_pct, ref_line) in enumerate(metrics):
            ax  = axes[row, col]
            agg = aggregate_rx_metric(scalar, field, higher_is_worse=hiw)
            _draw_metric(ax, agg, meta["color"])
            _mcs_axis(ax)
            if ylim[0] is not None: ax.set_ylim(bottom=ylim[0])
            if ylim[1] is not None: ax.set_ylim(top=ylim[1])
            if fmt_pct:
                ax.yaxis.set_major_formatter(
                    plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
            if ref_line:
                ax.axhline(10.0, color="firebrick", lw=0.9, ls=":",
                           alpha=0.7, zorder=1)
            ax.set_facecolor(meta["color"] + "06")

        # ── row 3: channel occupancy ──────────────────────────────────────────
        ax  = axes[3, col]
        agg_occ = aggregate_chan_metric(chan, "occ")
        _draw_chan(ax,
                   {m: dict(mean=agg_occ[m]["mean"]*100,
                             lo=agg_occ[m]["lo"]*100,
                             hi=agg_occ[m]["hi"]*100)
                    for m in MCS_VALUES},
                   meta["color"])
        ax.set_ylim(0, 105)
        ax.axhline(100, color="gray", lw=0.7, ls="--", alpha=0.4)
        _mcs_axis(ax)
        for mcs in MCS_VALUES:
            v = agg_occ[mcs]["mean"] * 100
            if not np.isnan(v):
                ax.text(mcs, v + 1.5, f"{v:.0f}%",
                        ha="center", va="bottom", fontsize=6, color=meta["color"])
        ax.set_facecolor(meta["color"] + "06")

        # ── row 4: wasted subchannels ─────────────────────────────────────────
        ax  = axes[4, col]
        agg_waste = aggregate_chan_metric(chan, "waste")
        _draw_chan(ax, agg_waste, meta["color"])
        ax.set_ylim(bottom=0)
        _mcs_axis(ax)
        for mcs in MCS_VALUES:
            v = agg_waste[mcs]["mean"]
            if not np.isnan(v):
                ax.text(mcs, v + 0.03, f"{v:.2f}",
                        ha="center", va="bottom", fontsize=6, color=meta["color"])
        ax.set_facecolor(meta["color"] + "06")

        # Title on row 0
        axes[0, col].set_title(meta["label"], fontsize=10, fontweight="bold", pad=4)

    # Y-labels on leftmost column
    for row, ylabel in enumerate(ylabels):
        axes[row, 0].set_ylabel(ylabel, fontsize=9)

    # Single legend panel
    leg_handles = [
        Line2D([0],[0], color="dimgray", lw=LW, ls=LS_WORST, marker="o", ms=5,
               label="Worst RX  (min across 8 nodes, per seed)"),
        Line2D([0],[0], color="dimgray", lw=LW, ls=LS_P5,    marker="s", ms=4,
               label="p5 RX  (5th-pct across seeds × nodes)"),
        Line2D([0],[0], color="dimgray", lw=1.1, ls=LS_MEAN,
               label="Mean RX  (mean across 8 nodes × seeds)"),
        Line2D([0],[0], color="firebrick", lw=0.9, ls=":",
               label="Nominal TX period  (10 ms)"),
    ] + [
        Line2D([0],[0], color=meta["color"], lw=LW, ls=LS_WORST, marker="o", ms=5,
               label=meta["label"])
        for _, meta in SCENARIOS.items()
    ]
    axes[0, 2].legend(handles=leg_handles, fontsize=7,
                      loc="lower right", ncol=1, framealpha=0.90)

    fig.tight_layout(rect=[0, 0, 1, 1])
    out = PLOTS / "mcs_sweep_combined.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("MCS sweep KPI plots …\n")
    print("── Figure 1: per-RX PRR / PIR / throughput …")
    plot_rx_kpis()
    print("── Figure 2: channel occupancy + wasted subchannels …")
    plot_chan_kpis()
    print("── Figure 3: combined 5×3 summary …")
    plot_combined()
    print(f"\nAll plots saved to {PLOTS}/")
