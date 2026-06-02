#!/usr/bin/env python3
"""
plot_scenario_kpis.py
Per-RX KPI analysis for the 3 platoon scenarios (slinky, gap_open, merge)
using MCS=14, 10 seeds each from platoon_baseline_eval.

Metrics
-------
  1. PRR per RX — 5 s sliding window, mean ± 90 % CI across seeds
  2. PIR per RX — mean inter-packet gap (ms), same windowing
  3. Throughput per RX — kbps goodput, same windowing
  4. Channel occupancy — fraction of BWP RBs used per TX slot (time series)
  5. TB fill ratio — payload / TB_capacity per TX slot (time series)
     + equivalent wasted subchannel fraction per TX

All figures saved to  sumo_scenarios/plots/
"""

from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MultipleLocator
import warnings
warnings.filterwarnings("ignore")

# ── paths ────────────────────────────────────────────────────────────────────
HERE    = Path(__file__).parent
PLOTS   = HERE / "plots"
PLOTS.mkdir(exist_ok=True)
RESULTS = (HERE.parent / "scratch_overlay" / "platoon_baseline_eval"
           / "results_mcs_sweep")

SCENARIOS = {
    "slinky": dict(
        label   = "Scenario A — Slinky/Accordion",
        key     = "ccam_platoon_slinky",
        joining = [],
        vlines  = [(15,"peak gap",":"),(30,"min gap",":"),(45,"peak gap",":"),
                   (60,"min gap",":"),(75,"peak gap",":")],
    ),
    "gap_open": dict(
        label   = "Scenario B — CACC Gap Opening",
        key     = "ccam_platoon_gap_open",
        joining = [],
        vlines  = [(22,"leader\nopens","--"),(36,"foll.8\nopens","--"),
                   (72,"leader\ncloses",":"),(86,"foll.8\ncloses",":")],
    ),
    "merge": dict(
        label   = "Scenario C — Platoon Merge",
        key     = "ccam_platoon_merge",
        joining = [5, 6, 7, 8],
        vlines  = [(70, "merge\ncomplete", "--")],
    ),
}

MCS        = 14
N_SEEDS    = 10
BWP_TOTAL  = 100          # total BWP RBs (10 subchannels × 10 RBs)
SCH_SIZE   = 10           # RBs per subchannel
WINDOW_S   = 5.0          # sliding-window width (seconds)
STEP_S     = 1.0          # step between windows (seconds)
TX_PACKET_PERIOD_S = 0.1  # 10 Hz CAM → expected PIR ≈ 100 ms

RX_NODES   = list(range(1, 9))   # nodes 1–8

FOLLOWER_CMAP = cm.Blues
JOINING_CMAP  = cm.Greens


# ── colour helpers ────────────────────────────────────────────────────────────
def node_colors(joining):
    tight  = [n for n in RX_NODES if n not in joining]
    jveh   = [n for n in RX_NODES if n in joining]
    colors = {}
    for i, nid in enumerate(tight):
        colors[nid] = FOLLOWER_CMAP(0.30 + 0.70 * i / max(len(tight)-1, 1))
    for i, nid in enumerate(jveh):
        colors[nid] = JOINING_CMAP(0.35 + 0.65 * i / max(len(jveh)-1, 1))
    return colors


# ── data loaders ─────────────────────────────────────────────────────────────
def load_pkt(scen_key: str, mcs: int) -> pd.DataFrame:
    """Return concatenated pktTxRx for all seeds."""
    dfs = []
    for s in range(1, N_SEEDS + 1):
        db = RESULTS / scen_key / f"{scen_key}_mcs{mcs}_seed{s}.db"
        if not db.exists():
            continue
        con = sqlite3.connect(db)
        df  = pd.read_sql_query(
            "SELECT timeSec, txRx, nodeId, pktSizeBytes, pktSeqNum FROM pktTxRx",
            con)
        con.close()
        df["seed"] = s
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)


def load_mac(scen_key: str, mcs: int) -> pd.DataFrame:
    """Return concatenated psschTxUeMac + psschRxUePhy for channel metrics."""
    tx_dfs, rx_dfs = [], []
    for s in range(1, N_SEEDS + 1):
        db = RESULTS / scen_key / f"{scen_key}_mcs{mcs}_seed{s}.db"
        if not db.exists():
            continue
        con = sqlite3.connect(db)
        # TX side: timeMs, rbLen, sbChSize, ndi
        tx = pd.read_sql_query(
            "SELECT timeMs, rbLen, sbChSize, ndi, rv FROM psschTxUeMac", con)
        tx["seed"] = s
        # RX side (any one RX node): timeMs, psschRbLen, tbSizeBytes, ndi (initial TX only)
        rx = pd.read_sql_query(
            """SELECT timeMs, psschRbLen, tbSizeBytes, psschMcs, ndi, rv
               FROM psschRxUePhy WHERE txRnti=1 AND rnti=2""", con)
        rx["seed"] = s
        con.close()
        tx_dfs.append(tx)
        rx_dfs.append(rx)
    return pd.concat(tx_dfs, ignore_index=True), pd.concat(rx_dfs, ignore_index=True)


# ── sliding-window helper ─────────────────────────────────────────────────────
def make_windows(t_min: float, t_max: float) -> np.ndarray:
    return np.arange(t_min + WINDOW_S/2, t_max - WINDOW_S/2 + STEP_S, STEP_S)


def compute_prr(pkt: pd.DataFrame) -> tuple:
    """
    Returns (times, mean[8×T], lo[8×T], hi[8×T]) for PRR per RX node.
    All in [0,1].
    """
    tx_all = pkt[pkt.txRx == "tx"]
    t_min  = tx_all.timeSec.min()
    t_max  = tx_all.timeSec.max()
    times  = make_windows(t_min, t_max)

    per_seed = {nid: [] for nid in RX_NODES}

    for seed_id, seed_grp in pkt.groupby("seed"):
        tx   = seed_grp[seed_grp.txRx == "tx"].set_index("pktSeqNum")["timeSec"]
        rx_g = seed_grp[seed_grp.txRx == "rx"].groupby("nodeId")

        for nid in RX_NODES:
            if nid not in rx_g.groups:
                per_seed[nid].append(np.full(len(times), np.nan))
                continue
            rx = rx_g.get_group(nid).set_index("pktSeqNum")["timeSec"]
            vals = []
            for t in times:
                lo, hi = t - WINDOW_S/2, t + WINDOW_S/2
                tx_w = tx[(tx >= lo) & (tx < hi)]
                if len(tx_w) == 0:
                    vals.append(np.nan)
                    continue
                # RX packets that were in the window (allow slight lag)
                rx_w = rx[(rx >= lo) & (rx < hi + 0.2)]
                n_rx = rx_w.index.isin(tx_w.index).sum()
                vals.append(n_rx / len(tx_w))
            per_seed[nid].append(np.array(vals))

    means, los, his = {}, {}, {}
    for nid in RX_NODES:
        mat = np.vstack(per_seed[nid])          # (seeds × times)
        means[nid] = np.nanmean(mat, axis=0)
        los[nid]   = np.nanpercentile(mat, 5,  axis=0)
        his[nid]   = np.nanpercentile(mat, 95, axis=0)
    return times, means, los, his


def compute_pir(pkt: pd.DataFrame) -> tuple:
    """
    Returns (times, mean_ms, lo_ms, hi_ms) dicts per RX node.
    PIR = inter-reception time in ms, windowed.
    """
    tx_all = pkt[pkt.txRx == "tx"]
    t_min  = tx_all.timeSec.min()
    t_max  = tx_all.timeSec.max()
    times  = make_windows(t_min, t_max)

    per_seed = {nid: [] for nid in RX_NODES}

    for seed_id, seed_grp in pkt.groupby("seed"):
        rx_g = seed_grp[seed_grp.txRx == "rx"].sort_values("timeSec").groupby("nodeId")
        for nid in RX_NODES:
            if nid not in rx_g.groups:
                per_seed[nid].append(np.full(len(times), np.nan))
                continue
            rx_t = rx_g.get_group(nid)["timeSec"].values
            if len(rx_t) < 2:
                per_seed[nid].append(np.full(len(times), np.nan))
                continue
            # mid-points and gaps
            mid_t  = 0.5 * (rx_t[:-1] + rx_t[1:])
            gaps_ms = np.diff(rx_t) * 1000.0
            vals = []
            for t in times:
                lo, hi = t - WINDOW_S/2, t + WINDOW_S/2
                mask = (mid_t >= lo) & (mid_t < hi)
                vals.append(np.nanmean(gaps_ms[mask]) if mask.sum() > 0 else np.nan)
            per_seed[nid].append(np.array(vals))

    means, los, his = {}, {}, {}
    for nid in RX_NODES:
        mat = np.vstack(per_seed[nid])
        means[nid] = np.nanmean(mat, axis=0)
        los[nid]   = np.nanpercentile(mat, 5,  axis=0)
        his[nid]   = np.nanpercentile(mat, 95, axis=0)
    return times, means, los, his


def compute_tput(pkt: pd.DataFrame) -> tuple:
    """
    Returns (times, mean_kbps, lo_kbps, hi_kbps) per RX node.
    Throughput = bits received / window duration in kbps.
    """
    tx_all = pkt[pkt.txRx == "tx"]
    t_min  = tx_all.timeSec.min()
    t_max  = tx_all.timeSec.max()
    times  = make_windows(t_min, t_max)

    per_seed = {nid: [] for nid in RX_NODES}

    for seed_id, seed_grp in pkt.groupby("seed"):
        rx_g = seed_grp[seed_grp.txRx == "rx"].groupby("nodeId")
        for nid in RX_NODES:
            if nid not in rx_g.groups:
                per_seed[nid].append(np.full(len(times), np.nan))
                continue
            rx = rx_g.get_group(nid)
            vals = []
            for t in times:
                lo, hi = t - WINDOW_S/2, t + WINDOW_S/2
                rx_w = rx[(rx.timeSec >= lo) & (rx.timeSec < hi)]
                tput = rx_w.pktSizeBytes.sum() * 8 / (WINDOW_S * 1000.0)   # kbps
                vals.append(tput)
            per_seed[nid].append(np.array(vals))

    means, los, his = {}, {}, {}
    for nid in RX_NODES:
        mat = np.vstack(per_seed[nid])
        means[nid] = np.nanmean(mat, axis=0)
        los[nid]   = np.nanpercentile(mat, 5,  axis=0)
        his[nid]   = np.nanpercentile(mat, 95, axis=0)
    return times, means, los, his


def compute_chan_metrics(tx_mac: pd.DataFrame, rx_phy: pd.DataFrame,
                         pkt: pd.DataFrame) -> dict:
    """
    Returns dict with keys 'times', 'occ_mean','occ_lo','occ_hi',
    'fill_mean','fill_lo','fill_hi', 'waste_subch_mean','waste_subch_lo','waste_subch_hi'.
    Channel occupancy = rbLen / BWP_TOTAL.
    TB fill ratio    = pkt_size / tbSizeBytes  (initial TX only).
    Wasted subch     = numSubCh × (1 − fill).
    """
    # Determine pkt size per seed (may be 800 or 1200 depending on segment)
    # Use the most-common TX packet size within each seed
    pkt_sz = {}
    for s, g in pkt[pkt.txRx == "tx"].groupby("seed"):
        pkt_sz[s] = int(g.pktSizeBytes.mode()[0])

    # Derive time axis from tx_mac
    all_times_ms = tx_mac.timeMs.values
    t_min_s = all_times_ms.min() / 1000.0
    t_max_s = all_times_ms.max() / 1000.0
    times   = make_windows(t_min_s, t_max_s)

    occ_per_seed   = []
    fill_per_seed  = []
    waste_per_seed = []

    for seed_id in range(1, N_SEEDS + 1):
        tm = tx_mac[tx_mac.seed == seed_id]
        rp = rx_phy[(rx_phy.seed == seed_id) & (rx_phy.ndi == 1)]
        if tm.empty or rp.empty:
            occ_per_seed.append(np.full(len(times), np.nan))
            fill_per_seed.append(np.full(len(times), np.nan))
            waste_per_seed.append(np.full(len(times), np.nan))
            continue
        p_sz = pkt_sz.get(seed_id, 800)

        tm_t = tm.timeMs.values / 1000.0
        tm_rblen = tm.rbLen.values
        rp_t   = rp.timeMs.values / 1000.0
        rp_tb  = rp.tbSizeBytes.values
        rp_rb  = rp.psschRbLen.values

        occ_vals   = []
        fill_vals  = []
        waste_vals = []
        for t in times:
            lo, hi = t - WINDOW_S/2, t + WINDOW_S/2

            # Channel occupancy from TX MAC (all slots incl. retransmissions)
            mask_tx = (tm_t >= lo) & (tm_t < hi)
            if mask_tx.sum() > 0:
                occ = np.mean(tm_rblen[mask_tx]) / BWP_TOTAL
            else:
                occ = np.nan

            # TB fill ratio from RX PHY (initial TX only)
            mask_rx = (rp_t >= lo) & (rp_t < hi)
            if mask_rx.sum() > 0:
                tb    = rp_tb[mask_rx]
                rb    = rp_rb[mask_rx]
                fill  = np.minimum(p_sz / tb, 1.0)         # clamp to 1
                nSubCh = rb / SCH_SIZE
                waste = nSubCh * (1.0 - fill)               # wasted subchannel fraction
                fill_vals.append(np.nanmean(fill))
                waste_vals.append(np.nanmean(waste))
            else:
                fill_vals.append(np.nan)
                waste_vals.append(np.nan)
            occ_vals.append(occ)

        occ_per_seed.append(np.array(occ_vals))
        fill_per_seed.append(np.array(fill_vals))
        waste_per_seed.append(np.array(waste_vals))

    def _agg(mat_list):
        mat = np.vstack(mat_list)
        return (np.nanmean(mat, 0),
                np.nanpercentile(mat, 5,  0),
                np.nanpercentile(mat, 95, 0))

    occ_m,   occ_lo,   occ_hi   = _agg(occ_per_seed)
    fill_m,  fill_lo,  fill_hi  = _agg(fill_per_seed)
    waste_m, waste_lo, waste_hi = _agg(waste_per_seed)

    return dict(times=times,
                occ_mean=occ_m,   occ_lo=occ_lo,   occ_hi=occ_hi,
                fill_mean=fill_m, fill_lo=fill_lo, fill_hi=fill_hi,
                waste_mean=waste_m, waste_lo=waste_lo, waste_hi=waste_hi)


# ── generic panel helpers ─────────────────────────────────────────────────────
def _draw_vlines(ax, vlines, y_top, y_range):
    for xv, lbl, vls in vlines:
        ax.axvline(xv, color="gray", lw=0.9, ls=vls, alpha=0.65, zorder=1)
        ax.text(xv + 0.4, y_top - 0.02 * y_range, lbl,
                fontsize=5.5, color="dimgray", va="top")


def _draw_per_rx(ax, times, means, los, his, colors, joining, vlines, ylabel,
                 ymin=None, ymax=None, hline=None):
    for nid in RX_NODES:
        c  = colors[nid]
        ls = "--" if nid in joining else "-"
        ax.plot(times, means[nid], color=c, lw=1.4, ls=ls, alpha=0.9, zorder=3)
        ax.fill_between(times, los[nid], his[nid],
                        color=c, alpha=0.12, zorder=2)
    if hline is not None:
        ax.axhline(hline, color="firebrick", lw=0.9, ls=":", alpha=0.7, zorder=1)
    yl = ax.get_ylim()
    y_lo = ymin if ymin is not None else yl[0]
    y_hi = ymax if ymax is not None else yl[1]
    ax.set_ylim(y_lo, y_hi)
    _draw_vlines(ax, vlines, y_hi, y_hi - y_lo)
    ax.set_ylabel(ylabel, fontsize=8.5)
    ax.set_xlabel("Simulation time  [s]", fontsize=8)
    ax.grid(axis="y", ls=":", alpha=0.45, zorder=0)
    ax.grid(axis="x", ls=":", alpha=0.25, zorder=0)


def _legend(ax, colors, joining):
    handles = []
    for nid in RX_NODES:
        ls = "--" if nid in joining else "-"
        lbl = f"Node {nid}" + ("★" if nid in joining else "")
        handles.append(Line2D([0],[0], color=colors[nid], lw=1.4, ls=ls, label=lbl))
    ax.legend(handles=handles, fontsize=6.5, ncol=4, loc="lower right",
              framealpha=0.85)


# ── Figure 1 : PRR ───────────────────────────────────────────────────────────
def plot_prr():
    fig, axes = plt.subplots(3, 1, figsize=(13, 11), sharey=False)
    fig.suptitle(
        f"Packet Reception Ratio per RX node — 5 s sliding window, {N_SEEDS} seeds  "
        f"(MCS={MCS}, periodic2, V2V-Highway LOS)\n"
        "Shaded band = 5th–95th percentile across seeds",
        fontsize=10, y=1.01,
    )
    for ax, (key, meta) in zip(axes, SCENARIOS.items()):
        pkt    = load_pkt(meta["key"], MCS)
        times, means, los, his = compute_prr(pkt)
        colors = node_colors(meta["joining"])
        t_min  = pkt[pkt.txRx == "tx"].timeSec.min()
        t_max  = pkt[pkt.txRx == "tx"].timeSec.max()
        ax.set_xlim(t_min, t_max)
        _draw_per_rx(ax, times, means, los, his, colors, meta["joining"],
                     meta["vlines"], "PRR", ymin=0, ymax=1.05)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
        ax.set_title(meta["label"], fontsize=10, fontweight="bold", pad=3)
        _legend(ax, colors, meta["joining"])
        print(f"  [PRR] {key}: loaded {len(pkt)} rows")
    fig.tight_layout()
    out = PLOTS / "scenario_prr.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── Figure 2 : PIR ───────────────────────────────────────────────────────────
def plot_pir():
    fig, axes = plt.subplots(3, 1, figsize=(13, 11), sharey=False)
    fig.suptitle(
        f"Packet Inter-Reception time per RX node — 5 s sliding window, {N_SEEDS} seeds  "
        f"(MCS={MCS})\n"
        "Shaded band = 5th–95th percentile across seeds.  "
        "Red dashed line = nominal TX period (100 ms)",
        fontsize=10, y=1.01,
    )
    for ax, (key, meta) in zip(axes, SCENARIOS.items()):
        pkt   = load_pkt(meta["key"], MCS)
        times, means, los, his = compute_pir(pkt)
        colors = node_colors(meta["joining"])
        t_min  = pkt[pkt.txRx == "tx"].timeSec.min()
        t_max  = pkt[pkt.txRx == "tx"].timeSec.max()
        ax.set_xlim(t_min, t_max)
        all_vals = np.concatenate([v[~np.isnan(v)] for v in means.values()])
        y_hi = max(np.nanpercentile(all_vals, 99) * 1.2, 200)
        _draw_per_rx(ax, times, means, los, his, colors, meta["joining"],
                     meta["vlines"], "PIR  [ms]",
                     ymin=0, ymax=y_hi, hline=100.0)
        ax.set_title(meta["label"], fontsize=10, fontweight="bold", pad=3)
        _legend(ax, colors, meta["joining"])
    fig.tight_layout()
    out = PLOTS / "scenario_pir.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── Figure 3 : Throughput ────────────────────────────────────────────────────
def plot_tput():
    fig, axes = plt.subplots(3, 1, figsize=(13, 11), sharey=False)
    # Offered load: 800 bytes × 10 Hz × 8 = 64 kbps if all RX = 100% PRR
    offered_kbps = 800 * 8 * 10 / 1000.0   # ≈ 64 kbps
    fig.suptitle(
        f"Received throughput per RX node — 5 s sliding window, {N_SEEDS} seeds  "
        f"(MCS={MCS})\n"
        f"Red dashed = offered load per RX (≈ {offered_kbps:.0f} kbps at 100 % PRR, 800 B / 10 Hz)",
        fontsize=10, y=1.01,
    )
    for ax, (key, meta) in zip(axes, SCENARIOS.items()):
        pkt   = load_pkt(meta["key"], MCS)
        times, means, los, his = compute_tput(pkt)
        colors = node_colors(meta["joining"])
        t_min  = pkt[pkt.txRx == "tx"].timeSec.min()
        t_max  = pkt[pkt.txRx == "tx"].timeSec.max()
        ax.set_xlim(t_min, t_max)
        _draw_per_rx(ax, times, means, los, his, colors, meta["joining"],
                     meta["vlines"], "Throughput  [kbps]",
                     ymin=0, ymax=None, hline=offered_kbps)
        ax.set_title(meta["label"], fontsize=10, fontweight="bold", pad=3)
        _legend(ax, colors, meta["joining"])
    fig.tight_layout()
    out = PLOTS / "scenario_tput.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── Figure 4 : Channel occupancy + TB fill / wasted subchannels ──────────────
def plot_channel():
    fig, axes = plt.subplots(2, 3, figsize=(16, 8), sharey="row")
    fig.suptitle(
        f"Channel resource utilisation — {N_SEEDS} seeds, MCS={MCS},  5 s window\n"
        "Top: channel occupancy (fraction of BWP RBs allocated per TX slot)  |  "
        "Bottom: TB fill ratio & wasted subchannel equivalents",
        fontsize=10, y=1.01,
    )

    scen_color = {"slinky": "#e41a1c", "gap_open": "#377eb8", "merge": "#4daf4a"}

    for col, (key, meta) in enumerate(SCENARIOS.items()):
        pkt           = load_pkt(meta["key"], MCS)
        tx_mac, rx_phy = load_mac(meta["key"], MCS)
        ch = compute_chan_metrics(tx_mac, rx_phy, pkt)
        times  = ch["times"]
        color  = scen_color[key]
        t_min  = pkt[pkt.txRx == "tx"].timeSec.min()
        t_max  = pkt[pkt.txRx == "tx"].timeSec.max()

        # ── Row 0: channel occupancy ─────────────────────────────────────────
        ax0 = axes[0, col]
        ax0.plot(times, ch["occ_mean"] * 100, color=color, lw=1.8, zorder=3)
        ax0.fill_between(times, ch["occ_lo"]*100, ch["occ_hi"]*100,
                         color=color, alpha=0.20, zorder=2)
        ax0.axhline(50, color="gray", lw=0.9, ls="--", alpha=0.6, label="50 % reference")
        ax0.set_xlim(t_min, t_max)
        ax0.set_ylim(0, 105)
        ax0.set_ylabel("Channel occupancy  [%]", fontsize=8.5)
        ax0.set_xlabel("Simulation time  [s]", fontsize=8)
        ax0.set_title(meta["label"], fontsize=9.5, fontweight="bold", pad=4)
        ax0.grid(ls=":", alpha=0.4)
        ax0.yaxis.set_minor_locator(MultipleLocator(5))
        for xv, lbl, vls in meta["vlines"]:
            ax0.axvline(xv, color="gray", lw=0.8, ls=vls, alpha=0.5)
            ax0.text(xv + 0.4, 100, lbl, fontsize=5.5, color="dimgray", va="top")
        if col == 0:
            ax0.legend(fontsize=7.5, loc="lower right")

        # ── Row 1: TB fill ratio (left y) + wasted subchannels (right y) ────
        ax1 = axes[1, col]
        ax1.plot(times, ch["fill_mean"] * 100, color=color, lw=1.8,
                 zorder=3, label="TB fill ratio")
        ax1.fill_between(times, ch["fill_lo"]*100, ch["fill_hi"]*100,
                         color=color, alpha=0.20, zorder=2)
        ax1.set_xlim(t_min, t_max)
        ax1.set_ylim(0, 105)
        ax1.set_ylabel("TB fill ratio  [%]", fontsize=8.5)
        ax1.set_xlabel("Simulation time  [s]", fontsize=8)
        ax1.grid(ls=":", alpha=0.4)
        for xv, lbl, vls in meta["vlines"]:
            ax1.axvline(xv, color="gray", lw=0.8, ls=vls, alpha=0.5)

        # Right y-axis: wasted subchannel equivalents
        ax1r = ax1.twinx()
        ax1r.plot(times, ch["waste_mean"], color="darkorange", lw=1.4,
                  ls="--", zorder=3, alpha=0.85, label="Wasted subch")
        ax1r.fill_between(times, ch["waste_lo"], ch["waste_hi"],
                          color="darkorange", alpha=0.10, zorder=2)
        ax1r.set_ylim(0, max(ch["waste_mean"].max() * 1.5, 0.5))
        ax1r.set_ylabel("Wasted subchannel equivalents", fontsize=8, color="darkorange")
        ax1r.tick_params(axis="y", colors="darkorange")
        ax1r.yaxis.set_minor_locator(MultipleLocator(0.1))

        # Legend
        if col == 0:
            lines = [
                Line2D([0],[0], color=color,       lw=1.8, label="TB fill ratio  (left)"),
                Line2D([0],[0], color="darkorange", lw=1.4, ls="--",
                       label="Wasted subch. equiv.  (right)"),
            ]
            ax1.legend(handles=lines, fontsize=7.5, loc="lower right")

    fig.tight_layout()
    out = PLOTS / "scenario_channel.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── Figure 5 : Combined summary (4 × 3 grid) ─────────────────────────────────
def plot_combined():
    """
    Single figure: rows = PRR, PIR, tput, channel; cols = 3 scenarios.
    Compact at-a-glance view for the paper.
    """
    fig, axes = plt.subplots(4, 3, figsize=(16, 14), sharey="row")
    fig.suptitle(
        f"Per-RX KPIs + channel utilisation — MCS={MCS}, {N_SEEDS} seeds, "
        "5 s sliding window  (periodic2, V2V-Highway LOS)",
        fontsize=11, fontweight="bold", y=1.005,
    )
    row_ylabels = [
        "PRR",
        "Mean PIR  [ms]",
        "Throughput  [kbps]",
        "Channel occupancy  [%]",
    ]

    all_data = {}
    for key, meta in SCENARIOS.items():
        pkt = load_pkt(meta["key"], MCS)
        tx_mac, rx_phy = load_mac(meta["key"], MCS)
        all_data[key] = dict(
            pkt    = pkt,
            prr    = compute_prr(pkt),
            pir    = compute_pir(pkt),
            tput   = compute_tput(pkt),
            chan   = compute_chan_metrics(tx_mac, rx_phy, pkt),
            meta   = meta,
        )
        print(f"  [combined] {key}: computed")

    scen_keys  = list(SCENARIOS.keys())
    scen_color = {"slinky": "#e41a1c", "gap_open": "#377eb8", "merge": "#4daf4a"}

    for col, key in enumerate(scen_keys):
        d      = all_data[key]
        meta   = d["meta"]
        colors = node_colors(meta["joining"])
        pkt    = d["pkt"]
        t_min  = pkt[pkt.txRx == "tx"].timeSec.min()
        t_max  = pkt[pkt.txRx == "tx"].timeSec.max()

        # ── Row 0: PRR ───────────────────────────────────────────────────────
        ax = axes[0, col]
        times, means, los, his = d["prr"]
        ax.set_xlim(t_min, t_max)
        for nid in RX_NODES:
            ls = "--" if nid in meta["joining"] else "-"
            ax.plot(times, means[nid], color=colors[nid], lw=1.2, ls=ls, alpha=0.9)
            ax.fill_between(times, los[nid], his[nid],
                            color=colors[nid], alpha=0.10)
        ax.set_ylim(0, 1.05)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
        ax.set_title(meta["label"], fontsize=9.5, fontweight="bold", pad=3)
        for xv, lbl, vls in meta["vlines"]:
            ax.axvline(xv, color="gray", lw=0.8, ls=vls, alpha=0.5)
        ax.grid(ls=":", alpha=0.35)

        # ── Row 1: PIR ───────────────────────────────────────────────────────
        ax = axes[1, col]
        times, means, los, his = d["pir"]
        ax.set_xlim(t_min, t_max)
        all_vals = np.concatenate([v[~np.isnan(v)] for v in means.values()])
        y_hi = max(np.nanpercentile(all_vals, 99) * 1.3, 250) if len(all_vals) > 0 else 500
        for nid in RX_NODES:
            ls = "--" if nid in meta["joining"] else "-"
            ax.plot(times, means[nid], color=colors[nid], lw=1.2, ls=ls, alpha=0.9)
            ax.fill_between(times, los[nid], his[nid],
                            color=colors[nid], alpha=0.10)
        ax.axhline(100, color="firebrick", lw=0.9, ls=":", alpha=0.7)
        ax.set_ylim(0, y_hi)
        for xv, lbl, vls in meta["vlines"]:
            ax.axvline(xv, color="gray", lw=0.8, ls=vls, alpha=0.5)
        ax.grid(ls=":", alpha=0.35)

        # ── Row 2: Throughput ────────────────────────────────────────────────
        ax = axes[2, col]
        times, means, los, his = d["tput"]
        ax.set_xlim(t_min, t_max)
        offered_kbps = 800 * 8 * 10 / 1000.0
        for nid in RX_NODES:
            ls = "--" if nid in meta["joining"] else "-"
            ax.plot(times, means[nid], color=colors[nid], lw=1.2, ls=ls, alpha=0.9)
            ax.fill_between(times, los[nid], his[nid],
                            color=colors[nid], alpha=0.10)
        ax.axhline(offered_kbps, color="firebrick", lw=0.9, ls=":", alpha=0.7)
        ax.set_ylim(0, None)
        for xv, lbl, vls in meta["vlines"]:
            ax.axvline(xv, color="gray", lw=0.8, ls=vls, alpha=0.5)
        ax.grid(ls=":", alpha=0.35)

        # ── Row 3: Channel occupancy + waste ────────────────────────────────
        ax = axes[3, col]
        ch     = d["chan"]
        times  = ch["times"]
        color  = scen_color[key]
        ax.set_xlim(t_min, t_max)
        ax.plot(times, ch["occ_mean"]*100,  color=color, lw=1.8, label="Occ.")
        ax.fill_between(times, ch["occ_lo"]*100, ch["occ_hi"]*100,
                        color=color, alpha=0.20)
        ax.set_ylim(0, 105)
        for xv, lbl, vls in meta["vlines"]:
            ax.axvline(xv, color="gray", lw=0.8, ls=vls, alpha=0.5)
        ax.grid(ls=":", alpha=0.35)

        # TB fill annotation
        mean_fill = np.nanmean(ch["fill_mean"]) * 100
        mean_waste = np.nanmean(ch["waste_mean"])
        ax.text(0.02, 0.97,
                f"Mean TB fill: {mean_fill:.1f}%\nMean waste: {mean_waste:.2f} subch",
                transform=ax.transAxes, fontsize=6.5, va="top",
                color="dimgray",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.75, ec="none"))
        ax.set_xlabel("Simulation time  [s]", fontsize=7.5)

        # Y-labels on leftmost column only
        if col == 0:
            for row, ylabel in enumerate(row_ylabels):
                axes[row, 0].set_ylabel(ylabel, fontsize=8.5)

    # ── Per-row legend patches ───────────────────────────────────────────────
    # Node colour legend: show once in top-right of figure
    leg_ax = axes[0, 2]
    handles = []
    for nid in RX_NODES:
        c  = FOLLOWER_CMAP(0.30 + 0.70*(nid-1)/7)
        ls = "-"
        handles.append(Line2D([0],[0], color=c, lw=1.4, ls=ls, label=f"N{nid}"))
    handles += [
        Line2D([0],[0], color=JOINING_CMAP(0.65), lw=1.4, ls="--",
               label="★ Joining veh."),
        Line2D([0],[0], color="firebrick", lw=0.9, ls=":", label="Nominal / offered"),
    ]
    leg_ax.legend(handles=handles, fontsize=6.5, ncol=3,
                  loc="lower right", framealpha=0.85)

    fig.tight_layout(rect=[0, 0, 1, 1])
    out = PLOTS / "scenario_kpis_combined.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Generating per-RX KPI plots …\n")
    print("── Figure 1: PRR …")
    plot_prr()
    print("── Figure 2: PIR …")
    plot_pir()
    print("── Figure 3: Throughput …")
    plot_tput()
    print("── Figure 4: Channel occupancy + TB fill …")
    plot_channel()
    print("── Figure 5: Combined summary …")
    plot_combined()
    print(f"\nAll plots saved to {PLOTS}/")
