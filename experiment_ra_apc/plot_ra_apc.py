#!/usr/bin/env python3
"""
plot_ra_apc.py — RA-APC-Groupcast comparison plots.

Reads results_ra_apc/{scheme}/*.db across all seeds and produces:
  campaign_results/ra_apc_prr_vs_time.png         group PRR vs sim time, per scheme
  campaign_results/ra_apc_per_rx_prr.png          bar chart, scheme groups × per-RX bars
  campaign_results/ra_apc_multiplex_vs_time.png   δ(t) = TOTAL_PRBs / rbLen
  campaign_results/ra_apc_etp_vs_time.png         empirical Group-ETP = δ · PRR
  campaign_results/ra_apc_mcs_trace.png           chosen TX MCS over time (RA-APC only)
  campaign_results/ra_apc_overview.png            combined 4-panel figure
"""

import glob
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

SCRIPT_DIR    = Path(__file__).parent
OUT_DIR       = SCRIPT_DIR / "campaign_results"
SCHEMES       = ["yan_broadcast", "yan_groupcast", "ra_apc_A", "ra_apc_B", "nack_rate"]
SCHEME_COLOR  = {
    "yan_broadcast": "#9E9E9E",
    "yan_groupcast": "#1976D2",
    "ra_apc_A":      "#F57C00",
    "ra_apc_B":      "#2E7D32",
    "nack_rate":     "#FF57FF",
}
SCHEME_LABEL = {
    "yan_broadcast": "Yan broadcast (MCS=14, no HARQ)",
    "yan_groupcast": "Yan groupcast (MCS=14, HARQ+PSFCH)",
    "ra_apc_A":      "RA-APC Strategy A (worst-case)",
    "ra_apc_B":      "RA-APC Strategy B (P5, proposed)",
    "nack_rate":     "Nack Rate (adaptive MCS)",
}

# Time-bin config
WARMUP_S    = 2.0
SIM_END_S   = 60.0
BIN_S       = 1.0
EDGES       = np.arange(WARMUP_S, SIM_END_S + BIN_S, BIN_S)
CENTERS     = EDGES[:-1] + BIN_S / 2
N_BINS      = len(CENTERS)

# Multiplexing constants matching the campaign config.
BW_PRBS     = 10
TOTAL_PRBS  = 100   # default --raApcPoolPrbs (numUEs * bwPRBs = 9 * 10 ≈ 100)
INTERSECT_T = 30.0  # second at which the platoon enters the intersection


# ── DB helpers ───────────────────────────────────────────────────────────────

def _bin(t):
    idx = int((t - EDGES[0]) / BIN_S)
    return max(0, min(idx, N_BINS - 1))


def per_seed_prr_vs_time(db_path):
    """PRR per receiver, per 1-second bin, averaged across receivers."""
    con = sqlite3.connect(db_path)
    tx = con.execute(
        "SELECT timeSec, pktSeqNum FROM pktTxRx WHERE txRx='tx' "
        "AND timeSec >= ? AND timeSec < ?", (WARMUP_S, SIM_END_S)).fetchall()
    rx = con.execute(
        "SELECT timeSec, nodeId, pktSeqNum FROM pktTxRx WHERE txRx='rx' "
        "AND timeSec >= ? AND timeSec < ?", (WARMUP_S, SIM_END_S)).fetchall()
    con.close()
    if not tx:
        return np.full(N_BINS, np.nan)
    # TX count per bin
    tx_count   = np.zeros(N_BINS)
    tx_seqs_in = defaultdict(set)
    for t, sq in tx:
        b = _bin(t); tx_count[b] += 1; tx_seqs_in[b].add(sq)
    # RX count per bin per node
    rx_count = defaultdict(lambda: np.zeros(N_BINS))
    for t, nid, sq in rx:
        b = _bin(t)
        if sq in tx_seqs_in[b]:
            rx_count[int(nid)][b] += 1
    if not rx_count:
        return np.full(N_BINS, np.nan)
    per_rx_prr = np.vstack([np.where(tx_count > 0, v / tx_count, np.nan)
                            for v in rx_count.values()])
    return np.nanmean(per_rx_prr, axis=0)


def per_seed_per_rx_prr(db_path):
    """Per-RX PRR computed from pktTxRx (one value per receiver node)."""
    con = sqlite3.connect(db_path)
    tx = con.execute(
        "SELECT pktSeqNum FROM pktTxRx WHERE txRx='tx' "
        "AND timeSec >= ? AND timeSec < ?", (WARMUP_S, SIM_END_S)).fetchall()
    rx = con.execute(
        "SELECT nodeId, pktSeqNum FROM pktTxRx WHERE txRx='rx' "
        "AND timeSec >= ? AND timeSec < ?", (WARMUP_S, SIM_END_S)).fetchall()
    con.close()
    tx_set = {r[0] for r in tx}
    if not tx_set:
        return {}
    rx_by_node = defaultdict(set)
    for nid, sq in rx:
        if sq in tx_set:
            rx_by_node[int(nid)].add(sq)
    return {nid: len(seqs) / len(tx_set) for nid, seqs in rx_by_node.items()}


def per_seed_mux_vs_time(db_path):
    """Multiplexing capacity δ(t) = TOTAL_PRBs / rbLen averaged per 1-s bin."""
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT timeMs, rbLen FROM psschTxUeMac WHERE timeMs/1000.0 >= ? "
        "AND timeMs/1000.0 < ?", (WARMUP_S, SIM_END_S)).fetchall()
    con.close()
    if not rows:
        return np.full(N_BINS, np.nan)
    deltas = np.full(N_BINS, np.nan)
    by_bin = defaultdict(list)
    for tms, rb in rows:
        if rb > 0:
            by_bin[_bin(tms / 1000.0)].append(TOTAL_PRBS / rb)
    for b, vals in by_bin.items():
        deltas[b] = float(np.mean(vals))
    return deltas


def per_seed_harq_vs_time(db_path):
    """Per-bin NACK rate, retx ratio, and PHY error rate."""
    con = sqlite3.connect(db_path)

    # NACK rate per bin: #NACK / (#ACK + #NACK)
    fb = con.execute(
        "SELECT timeSec, status FROM slHarqFeedback "
        "WHERE timeSec >= ? AND timeSec < ?", (WARMUP_S, SIM_END_S)
    ).fetchall()
    ack_bins  = np.zeros(N_BINS)
    nack_bins = np.zeros(N_BINS)
    for t, st in fb:
        b = _bin(t)
        if st == "ACK":
            ack_bins[b] += 1
        else:
            nack_bins[b] += 1
    total_fb  = ack_bins + nack_bins
    nack_rate = np.where(total_fb > 0, nack_bins / total_fb, np.nan)

    # Retransmission ratio per bin: #ndi=0 / (#ndi=0 + #ndi=1)
    tx_rows = con.execute(
        "SELECT timeMs, ndi FROM psschTxUeMac "
        "WHERE timeMs/1000.0 >= ? AND timeMs/1000.0 < ?", (WARMUP_S, SIM_END_S)
    ).fetchall()
    init_bins = np.zeros(N_BINS)
    retx_bins = np.zeros(N_BINS)
    for tms, ndi in tx_rows:
        b = _bin(tms / 1000.0)
        if ndi == 1:
            init_bins[b] += 1
        else:
            retx_bins[b] += 1
    total_tx   = init_bins + retx_bins
    retx_ratio = np.where(total_tx > 0, retx_bins / total_tx, np.nan)

    # PHY corruption rate per bin
    phy_rows = con.execute(
        "SELECT timeMs, psschCorrupt FROM psschRxUePhy "
        "WHERE timeMs/1000.0 >= ? AND timeMs/1000.0 < ?", (WARMUP_S, SIM_END_S)
    ).fetchall()
    ok_bins  = np.zeros(N_BINS)
    err_bins = np.zeros(N_BINS)
    for tms, corrupt in phy_rows:
        b = _bin(tms / 1000.0)
        if corrupt:
            err_bins[b] += 1
        else:
            ok_bins[b] += 1
    total_phy = ok_bins + err_bins
    phy_err   = np.where(total_phy > 0, err_bins / total_phy, np.nan)

    con.close()
    return {"nack_rate": nack_rate, "retx_ratio": retx_ratio, "phy_err": phy_err}


def per_seed_mcs_vs_time(db_path):
    """Median TX MCS per 1-s bin (RA-APC schemes vary; baselines are constant)."""
    con = sqlite3.connect(db_path)
    # psschRxUePhy stores m_mcs of received TBs; use as a proxy of TX MCS.
    rows = con.execute(
        "SELECT timeMs, psschMcs FROM psschRxUePhy WHERE timeMs/1000.0 >= ? "
        "AND timeMs/1000.0 < ?", (WARMUP_S, SIM_END_S)).fetchall()
    con.close()
    by_bin = defaultdict(list)
    for tms, mcs in rows:
        by_bin[_bin(tms / 1000.0)].append(mcs)
    out = np.full(N_BINS, np.nan)
    for b, vals in by_bin.items():
        out[b] = float(np.median(vals))
    return out


def per_seed_pir_vs_time(db_path):
    """Per-bin PIR statistics (ms) computed from consecutive RX timestamps per node.

    For each 1-s bin b:
      - Collect all RX timestamps in [b_start, b_end) per receiver node.
      - Compute inter-arrival gaps within the bin (plus the gap bridging the
        previous bin boundary using the last RX time before bin start).
      - Return median and P95 across all gaps and all nodes in that bin.
    """
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT timeSec, nodeId FROM pktTxRx WHERE txRx='rx' "
        "AND timeSec >= ? AND timeSec < ? ORDER BY nodeId, timeSec",
        (WARMUP_S, SIM_END_S)
    ).fetchall()
    con.close()

    # Build per-node RX time list
    by_node = defaultdict(list)
    for t, nid in rows:
        by_node[int(nid)].append(t)

    # Compute all inter-arrival gaps with their midpoint time
    gap_by_bin_median = defaultdict(list)   # bin → list of gaps (ms)
    gap_by_bin_p95    = defaultdict(list)

    for nid, times in by_node.items():
        if len(times) < 2:
            continue
        for i in range(1, len(times)):
            gap_ms  = (times[i] - times[i - 1]) * 1000.0
            mid_t   = (times[i] + times[i - 1]) / 2.0
            b       = _bin(mid_t)
            gap_by_bin_median[b].append(gap_ms)
            gap_by_bin_p95[b].append(gap_ms)

    pir_median = np.full(N_BINS, np.nan)
    pir_p95    = np.full(N_BINS, np.nan)
    for b, gaps in gap_by_bin_median.items():
        pir_median[b] = float(np.median(gaps))
        pir_p95[b]    = float(np.percentile(gaps, 95))

    return {"median": pir_median, "p95": pir_p95}


# ── aggregation ──────────────────────────────────────────────────────────────

def collect(scheme, fn):
    dbs = sorted(glob.glob(str(SCRIPT_DIR / "results_ra_apc" / scheme / "*.db")))
    series = []
    for db in dbs:
        arr = fn(db)
        if isinstance(arr, dict):
            series.append(arr)
        else:
            series.append(arr)
    return series


def stack_median(series):
    if not series:
        return np.full(N_BINS, np.nan)
    return np.nanmedian(np.vstack(series), axis=0)


def stack_p5(series):
    if not series:
        return np.full(N_BINS, np.nan)
    return np.nanpercentile(np.vstack(series), 5, axis=0)


# ── plots ────────────────────────────────────────────────────────────────────

def plot_prr_vs_time(prr_by_scheme, out_path):
    fig, ax = plt.subplots(figsize=(12, 4.5))
    for s in SCHEMES:
        med = stack_median(prr_by_scheme[s])
        p5  = stack_p5(prr_by_scheme[s])
        ax.plot(CENTERS, med, color=SCHEME_COLOR[s], lw=2, label=SCHEME_LABEL[s])
        ax.fill_between(CENTERS, p5, med, color=SCHEME_COLOR[s], alpha=0.12)
    ax.axvline(INTERSECT_T, color="black", ls="--", lw=1.2, alpha=0.7,
               label=f"intersection entry (t={INTERSECT_T:.0f} s)")
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel("Group PRR (median across seeds, shaded = P5–median)")
    ax.set_title("Group PRR vs time — RA-APC-Groupcast comparison")
    ax.legend(fontsize=8, loc="lower left")
    ax.set_ylim(0.5, 1.005)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_per_rx_prr(per_rx_by_scheme, out_path):
    fig, ax = plt.subplots(figsize=(12, 5))
    width = 0.2
    rx_ids = sorted({nid for s in SCHEMES for d in per_rx_by_scheme[s] for nid in d})
    rx_ids = [r for r in rx_ids if r != 0]  # skip TX node 0
    x = np.arange(len(rx_ids))

    smallest_value = 0.5  # for better y-axis scaling when some RXs have 0 PRR
    for i, s in enumerate(SCHEMES):
        means = []
        for nid in rx_ids:
            vals = [d.get(nid, float("nan")) for d in per_rx_by_scheme[s]]
            vals = [v for v in vals if not np.isnan(v)]
            means.append(np.mean(vals) if vals else float("nan"))
        smallest_value = max(smallest_value, min(means) - 0.1)
        ax.bar(x + (i - 1.5) * width, means, width,
               color=SCHEME_COLOR[s], label=SCHEME_LABEL[s])
    ax.set_xticks(x); ax.set_xticklabels([f"RX {r}" for r in rx_ids])
    ax.set_ylabel("Group PRR (mean across seeds)")
    ax.set_title("Per-receiver PRR — full simulation")
    ax.legend(fontsize=8, loc="lower right")
    ax.set_ylim(smallest_value, 1.005); ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_mux_vs_time(mux_by_scheme, out_path):
    fig, ax = plt.subplots(figsize=(12, 4))
    for s in SCHEMES:
        med = stack_median(mux_by_scheme[s])
        ax.plot(CENTERS, med, color=SCHEME_COLOR[s], lw=2, label=SCHEME_LABEL[s])
    ax.axvline(INTERSECT_T, color="black", ls="--", lw=1.2, alpha=0.7)
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel(f"Multiplexing level δ(t) = {TOTAL_PRBS} PRBs / rbLen")
    ax.set_title("Multiplexing capacity vs time")
    ax.legend(fontsize=8, loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_etp_vs_time(prr_by_scheme, mux_by_scheme, out_path):
    fig, ax = plt.subplots(figsize=(12, 4))
    for s in SCHEMES:
        prr = stack_median(prr_by_scheme[s])
        mux = stack_median(mux_by_scheme[s])
        etp = prr * mux
        ax.plot(CENTERS, etp, color=SCHEME_COLOR[s], lw=2, label=SCHEME_LABEL[s])
    ax.axvline(INTERSECT_T, color="black", ls="--", lw=1.2, alpha=0.7)
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel("Empirical Group-ETP  =  δ(t) · group PRR(t)")
    ax.set_title("Empirical Group Effective Throughput")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_mcs_trace(mcs_by_scheme, out_path):
    fig, ax = plt.subplots(figsize=(12, 4))
    for s in SCHEMES:
        med = stack_median(mcs_by_scheme[s])
        ax.plot(CENTERS, med, color=SCHEME_COLOR[s], lw=2, label=SCHEME_LABEL[s])
    ax.axvline(INTERSECT_T, color="black", ls="--", lw=1.2, alpha=0.7)
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel("TX MCS (median across seeds)")
    ax.set_title("Chosen TX MCS over time")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_pir_vs_time(pir_by_scheme, out_path):
    """Two-panel PIR plot: median PIR and P95 PIR per scheme vs time."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 4.5), sharey=False)

    for s in SCHEMES:
        series_med = [d["median"] for d in pir_by_scheme[s] if isinstance(d, dict)]
        series_p95 = [d["p95"]    for d in pir_by_scheme[s] if isinstance(d, dict)]
        if not series_med:
            continue
        mat_med = np.vstack(series_med)
        mat_p95 = np.vstack(series_p95)
        med_med = np.nanmedian(mat_med, axis=0)
        p5_med  = np.nanpercentile(mat_med, 5,  axis=0)
        p95_med = np.nanpercentile(mat_med, 95, axis=0)
        med_p95 = np.nanmedian(mat_p95, axis=0)

        ax1.plot(CENTERS, med_med, color=SCHEME_COLOR[s], lw=2, label=SCHEME_LABEL[s])
        ax1.fill_between(CENTERS, p5_med, p95_med,
                         color=SCHEME_COLOR[s], alpha=0.12)
        ax2.plot(CENTERS, med_p95, color=SCHEME_COLOR[s], lw=2, label=SCHEME_LABEL[s])

    for ax in (ax1, ax2):
        ax.axvline(INTERSECT_T, color="black", ls="--", lw=1.2, alpha=0.7,
                   label=f"intersection entry (t={INTERSECT_T:.0f} s)")
        ax.axhline(20, color="gray", ls=":", lw=1, alpha=0.6, label="RRI = 20 ms")
        ax.set_xlabel("Simulation time (s)")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=7, loc="upper left")

    ax1.set_ylabel("PIR median (ms)  [shaded = seed P5–P95]")
    ax1.set_title("Median PIR vs time")
    ax2.set_ylabel("PIR P95 (ms)  [worst-case latency]")
    ax2.set_title("P95 PIR vs time  (tail latency)")

    fig.suptitle("Packet Inter-Reception time — RA-APC-Groupcast comparison",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_harq_overview(harq_by_scheme, out_path):
    """Three-panel HARQ statistics: NACK rate / ReTx ratio / PHY error vs time."""
    metrics = [
        ("nack_rate", "NACK rate  (#NACK / #FB)", (0.0, 0.35)),
        ("retx_ratio", "Retx ratio  (#retx / #TX)", (0.0, 0.35)),
        ("phy_err",   "PHY corruption rate", (0.0, 0.25)),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(18, 4.5), sharey=False)
    for ax, (key, ylabel, ylim) in zip(axes, metrics):
        for s in SCHEMES:
            series = [d[key] for d in harq_by_scheme[s] if isinstance(d, dict)]
            if not series:
                continue
            med = np.nanmedian(np.vstack(series), axis=0)
            p5  = np.nanpercentile(np.vstack(series), 5,  axis=0)
            p95 = np.nanpercentile(np.vstack(series), 95, axis=0)
            ax.plot(CENTERS, med, color=SCHEME_COLOR[s], lw=2, label=SCHEME_LABEL[s])
            ax.fill_between(CENTERS, p5, p95, color=SCHEME_COLOR[s], alpha=0.12)
        ax.axvline(INTERSECT_T, color="black", ls="--", lw=1.2, alpha=0.7)
        ax.set_xlabel("Simulation time (s)")
        ax.set_ylabel(ylabel)
        ax.set_ylim(*ylim)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=7, loc="upper left")
    axes[0].set_title("NACK rate vs time")
    axes[1].set_title("Retransmission ratio vs time")
    axes[2].set_title("PHY corruption rate vs time")
    fig.suptitle("HARQ statistics — RA-APC-Groupcast comparison", fontsize=13)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_harq_bar(harq_by_scheme, out_path):
    """Bar chart of aggregate NACK rate, ReTx ratio, PHY error per scheme."""
    metrics = [
        ("nack_rate",  "NACK rate"),
        ("retx_ratio", "ReTx ratio"),
        ("phy_err",    "PHY corruption"),
    ]
    x     = np.arange(len(SCHEMES))
    width = 0.25
    fig, ax = plt.subplots(figsize=(12, 5))
    for i, (key, label) in enumerate(metrics):
        means = []
        errs  = []
        for s in SCHEMES:
            vals = []
            for d in harq_by_scheme[s]:
                if isinstance(d, dict):
                    arr = d[key]
                    vals.append(float(np.nanmean(arr)))
            if vals:
                a = np.array(vals)
                means.append(float(np.mean(a)))
                errs.append(1.96 * float(a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 else 0.0)
            else:
                means.append(float("nan"))
                errs.append(0.0)
        ax.bar(x + (i - 1) * width, means, width, yerr=errs,
               label=label, capsize=4, alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels([SCHEME_LABEL[s] for s in SCHEMES], rotation=15, ha="right", fontsize=9)
    ax.set_ylabel("Rate (fraction)")
    ax.set_title("Aggregate HARQ statistics per scheme (mean ± 95% CI across seeds)")
    ax.legend(fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_overview(prr, perrx, mux, mcs, etp, out_path):
    fig = plt.figure(figsize=(18, 12))
    gs  = GridSpec(3, 2, figure=fig, hspace=0.45, wspace=0.25)

    # 1. PRR vs time
    ax = fig.add_subplot(gs[0, 0])
    for s in SCHEMES:
        ax.plot(CENTERS, stack_median(prr[s]),
                color=SCHEME_COLOR[s], lw=1.8, label=SCHEME_LABEL[s])
    ax.axvline(INTERSECT_T, color="black", ls="--", lw=1, alpha=0.7)
    ax.set_title("Group PRR vs time"); ax.set_ylim(0.5, 1.005)
    ax.set_xlabel("time (s)"); ax.set_ylabel("PRR")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=7, loc="lower left")

    # 2. MUX vs time
    ax = fig.add_subplot(gs[0, 1])
    for s in SCHEMES:
        ax.plot(CENTERS, stack_median(mux[s]),
                color=SCHEME_COLOR[s], lw=1.8, label=SCHEME_LABEL[s])
    ax.axvline(INTERSECT_T, color="black", ls="--", lw=1, alpha=0.7)
    ax.set_title(f"Multiplexing capacity δ = {TOTAL_PRBS}/rbLen")
    ax.set_xlabel("time (s)"); ax.set_ylabel("δ")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=7)

    # 3. ETP vs time
    ax = fig.add_subplot(gs[1, 0])
    for s in SCHEMES:
        ax.plot(CENTERS, stack_median(etp[s]),
                color=SCHEME_COLOR[s], lw=1.8, label=SCHEME_LABEL[s])
    ax.axvline(INTERSECT_T, color="black", ls="--", lw=1, alpha=0.7)
    ax.set_title("Empirical Group-ETP = δ · PRR")
    ax.set_xlabel("time (s)"); ax.set_ylabel("Group-ETP")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=7)

    # 4. MCS over time
    ax = fig.add_subplot(gs[1, 1])
    for s in SCHEMES:
        ax.plot(CENTERS, stack_median(mcs[s]),
                color=SCHEME_COLOR[s], lw=1.8, label=SCHEME_LABEL[s])
    ax.axvline(INTERSECT_T, color="black", ls="--", lw=1, alpha=0.7)
    ax.set_title("TX MCS over time"); ax.set_xlabel("time (s)")
    ax.set_ylabel("MCS"); ax.grid(True, alpha=0.3); ax.legend(fontsize=7)

    # 5. Per-RX PRR bars
    ax = fig.add_subplot(gs[2, :])
    width = 0.2
    rx_ids = sorted({nid for s in SCHEMES for d in perrx[s] for nid in d})
    rx_ids = [r for r in rx_ids if r != 0]
    x = np.arange(len(rx_ids))
    for i, s in enumerate(SCHEMES):
        means = []
        for nid in rx_ids:
            vals = [d.get(nid, float("nan")) for d in perrx[s]]
            vals = [v for v in vals if not np.isnan(v)]
            means.append(np.mean(vals) if vals else float("nan"))
        ax.bar(x + (i - 1.5) * width, means, width,
               color=SCHEME_COLOR[s], label=SCHEME_LABEL[s])
    ax.set_xticks(x); ax.set_xticklabels([f"RX {r}" for r in rx_ids])
    ax.set_ylim(0.5, 1.005); ax.set_ylabel("PRR")
    ax.set_title("Per-receiver PRR")
    ax.grid(True, axis="y", alpha=0.3); ax.legend(fontsize=8)

    fig.suptitle("RA-APC-Groupcast: highway → intersection scenario",
                 fontsize=14, y=1.001)
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    OUT_DIR.mkdir(exist_ok=True)
    print("Loading per-seed data ...")
    prr   = {s: collect(s, per_seed_prr_vs_time)   for s in SCHEMES}
    perrx = {s: collect(s, per_seed_per_rx_prr)    for s in SCHEMES}
    mux   = {s: collect(s, per_seed_mux_vs_time)   for s in SCHEMES}
    mcs   = {s: collect(s, per_seed_mcs_vs_time)   for s in SCHEMES}
    harq  = {s: collect(s, per_seed_harq_vs_time)  for s in SCHEMES}
    pir   = {s: collect(s, per_seed_pir_vs_time)   for s in SCHEMES}

    # Derive ETP per seed = PRR(t) · δ(t).
    etp = {s: [p * m for p, m in zip(prr[s], mux[s])]
           for s in SCHEMES}

    print("\nRendering plots ...")
    plot_prr_vs_time(prr, OUT_DIR / "ra_apc_prr_vs_time.png")
    plot_per_rx_prr(perrx, OUT_DIR / "ra_apc_per_rx_prr.png")
    plot_mux_vs_time(mux, OUT_DIR / "ra_apc_multiplex_vs_time.png")
    plot_etp_vs_time(prr, mux, OUT_DIR / "ra_apc_etp_vs_time.png")
    plot_mcs_trace(mcs, OUT_DIR / "ra_apc_mcs_trace.png")
    plot_pir_vs_time(pir, OUT_DIR / "ra_apc_pir_vs_time.png")
    plot_harq_overview(harq, OUT_DIR / "ra_apc_harq_vs_time.png")
    plot_harq_bar(harq, OUT_DIR / "ra_apc_harq_bar.png")
    plot_overview(prr, perrx, mux, mcs, etp,
                  OUT_DIR / "ra_apc_overview.png")
    print(f"\nDone — outputs in {OUT_DIR}")


if __name__ == "__main__":
    main()
