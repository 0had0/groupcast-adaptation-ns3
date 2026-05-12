#!/usr/bin/env python3
"""
plot_intersect_nlosv.py — plots for the intersection_nlosv campaign.

Intersection geometry (from trace analysis):
  t=2.7 s  : TX (node 0) enters corner
  t=17.2 s : last follower (node 8) enters corner
  t=24.4 s : last follower clears corner → LOS restored for all
  t=24–60 s: post-intersection straight

Annotated vertical lines:
  ENTER_T = 2.7   (first NLOSv event)
  CLEAR_T = 24.4  (LOS fully restored)
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

SCRIPT_DIR  = Path(__file__).parent
RESULTS     = SCRIPT_DIR / "results_intersect_nlosv"
OUT_DIR     = RESULTS / "campaign"
SCHEMES     = ["yan_broadcast", "yan_groupcast", "ra_apc_A", "ra_apc_B", "nack_rate"]
SCHEME_COLOR = {
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
    "nack_rate":     "NACK Rate (adaptive MCS)",
}

WARMUP_S  = 2.0
SIM_END_S = 60.0
BIN_S     = 1.0
EDGES     = np.arange(WARMUP_S, SIM_END_S + BIN_S, BIN_S)
CENTERS   = EDGES[:-1] + BIN_S / 2
N_BINS    = len(CENTERS)

BW_PRBS    = 10
TOTAL_PRBS = 100     # numUEs * bwPRBs = 9 * 10 ≈ 100

# Intersection timeline annotations
ENTER_T = 2.7    # TX (node 0) enters corner — first NLOSv
CLEAR_T = 24.4   # last follower (node 8) clears corner — LOS restored


# ── helpers ──────────────────────────────────────────────────────────────────

def _bin(t):
    idx = int((t - EDGES[0]) / BIN_S)
    return max(0, min(idx, N_BINS - 1))


def per_seed_prr_vs_time(db_path):
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
    tx_count, tx_seqs_in = np.zeros(N_BINS), defaultdict(set)
    for t, sq in tx:
        b = _bin(t); tx_count[b] += 1; tx_seqs_in[b].add(sq)
    rx_count = defaultdict(lambda: np.zeros(N_BINS))
    for t, nid, sq in rx:
        b = _bin(t)
        if sq in tx_seqs_in[b]:
            rx_count[int(nid)][b] += 1
    if not rx_count:
        return np.full(N_BINS, np.nan)
    per_rx = np.vstack([np.where(tx_count > 0, v / tx_count, np.nan)
                        for v in rx_count.values()])
    return np.nanmean(per_rx, axis=0)


def per_seed_per_rx_prr(db_path):
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


def per_seed_per_rx_prr_phase(db_path, t_start, t_end):
    """Per-RX PRR restricted to a time window (for intersection vs post phase)."""
    con = sqlite3.connect(db_path)
    tx = con.execute(
        "SELECT pktSeqNum FROM pktTxRx WHERE txRx='tx' "
        "AND timeSec >= ? AND timeSec < ?", (t_start, t_end)).fetchall()
    rx = con.execute(
        "SELECT nodeId, pktSeqNum FROM pktTxRx WHERE txRx='rx' "
        "AND timeSec >= ? AND timeSec < ?", (t_start, t_end)).fetchall()
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
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT timeMs, rbLen FROM psschTxUeMac WHERE timeMs/1000.0 >= ? "
        "AND timeMs/1000.0 < ?", (WARMUP_S, SIM_END_S)).fetchall()
    con.close()
    if not rows:
        return np.full(N_BINS, np.nan)
    by_bin = defaultdict(list)
    for tms, rb in rows:
        if rb > 0:
            by_bin[_bin(tms / 1000.0)].append(TOTAL_PRBS / rb)
    out = np.full(N_BINS, np.nan)
    for b, vals in by_bin.items():
        out[b] = float(np.mean(vals))
    return out


def per_seed_mcs_vs_time(db_path):
    con = sqlite3.connect(db_path)
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
    """Per-bin PIR statistics (ms) from consecutive RX timestamps per node."""
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT timeSec, nodeId FROM pktTxRx WHERE txRx='rx' "
        "AND timeSec >= ? AND timeSec < ? ORDER BY nodeId, timeSec",
        (WARMUP_S, SIM_END_S)
    ).fetchall()
    con.close()

    by_node = defaultdict(list)
    for t, nid in rows:
        by_node[int(nid)].append(t)

    gap_by_bin = defaultdict(list)
    for nid, times in by_node.items():
        if len(times) < 2:
            continue
        for i in range(1, len(times)):
            gap_ms = (times[i] - times[i - 1]) * 1000.0
            mid_t  = (times[i] + times[i - 1]) / 2.0
            gap_by_bin[_bin(mid_t)].append(gap_ms)

    pir_median = np.full(N_BINS, np.nan)
    pir_p95    = np.full(N_BINS, np.nan)
    for b, gaps in gap_by_bin.items():
        pir_median[b] = float(np.median(gaps))
        pir_p95[b]    = float(np.percentile(gaps, 95))

    return {"median": pir_median, "p95": pir_p95}


def per_seed_harq_vs_time(db_path):
    con = sqlite3.connect(db_path)
    fb = con.execute(
        "SELECT timeSec, status FROM slHarqFeedback "
        "WHERE timeSec >= ? AND timeSec < ?", (WARMUP_S, SIM_END_S)).fetchall()
    ack_bins = np.zeros(N_BINS); nack_bins = np.zeros(N_BINS)
    for t, st in fb:
        b = _bin(t)
        if st == "ACK":
            ack_bins[b] += 1
        else:
            nack_bins[b] += 1
    total_fb  = ack_bins + nack_bins
    nack_rate = np.where(total_fb > 0, nack_bins / total_fb, np.nan)

    tx_rows = con.execute(
        "SELECT timeMs, ndi FROM psschTxUeMac "
        "WHERE timeMs/1000.0 >= ? AND timeMs/1000.0 < ?", (WARMUP_S, SIM_END_S)).fetchall()
    init_bins = np.zeros(N_BINS); retx_bins = np.zeros(N_BINS)
    for tms, ndi in tx_rows:
        b = _bin(tms / 1000.0)
        if ndi == 1:
            init_bins[b] += 1
        else:
            retx_bins[b] += 1
    total_tx   = init_bins + retx_bins
    retx_ratio = np.where(total_tx > 0, retx_bins / total_tx, np.nan)

    phy_rows = con.execute(
        "SELECT timeMs, psschCorrupt FROM psschRxUePhy "
        "WHERE timeMs/1000.0 >= ? AND timeMs/1000.0 < ?", (WARMUP_S, SIM_END_S)).fetchall()
    ok_bins = np.zeros(N_BINS); err_bins = np.zeros(N_BINS)
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


# ── aggregation ──────────────────────────────────────────────────────────────

def collect(scheme, fn, **kwargs):
    dbs = sorted(glob.glob(str(RESULTS / scheme / "*.db")))
    return [fn(db, **kwargs) if kwargs else fn(db) for db in dbs]


def stack_median(series):
    if not series:
        return np.full(N_BINS, np.nan)
    arrays = [s for s in series if isinstance(s, np.ndarray)]
    if not arrays:
        return np.full(N_BINS, np.nan)
    return np.nanmedian(np.vstack(arrays), axis=0)


def stack_p5(series):
    if not series:
        return np.full(N_BINS, np.nan)
    arrays = [s for s in series if isinstance(s, np.ndarray)]
    if not arrays:
        return np.full(N_BINS, np.nan)
    return np.nanpercentile(np.vstack(arrays), 5, axis=0)


def _annotate_intersection(ax):
    """Add shaded NLOSv zone and vertical markers."""
    ax.axvspan(ENTER_T, CLEAR_T, alpha=0.06, color="red",
               label="NLOSv window (2.7–24.4 s)")
    ax.axvline(ENTER_T, color="red",    ls="--", lw=1.2, alpha=0.7,
               label=f"TX enters corner (t={ENTER_T:.1f} s)")
    ax.axvline(CLEAR_T, color="purple", ls=":",  lw=1.2, alpha=0.7,
               label=f"All past corner (t={CLEAR_T:.1f} s)")


# ── individual plots ──────────────────────────────────────────────────────────

def plot_prr_vs_time(prr_by_scheme, out_path):
    fig, ax = plt.subplots(figsize=(13, 4.5))
    _annotate_intersection(ax)
    for s in SCHEMES:
        med = stack_median(prr_by_scheme[s])
        p5  = stack_p5(prr_by_scheme[s])
        ax.plot(CENTERS, med, color=SCHEME_COLOR[s], lw=2, label=SCHEME_LABEL[s])
        ax.fill_between(CENTERS, p5, med, color=SCHEME_COLOR[s], alpha=0.12)
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel("Group PRR (median, shaded = P5–median)")
    ax.set_title("Group PRR vs time — intersection_nlosv scenario")
    ax.legend(fontsize=8, loc="lower right", ncol=2)
    ax.set_ylim(0.4, 1.02); ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_per_rx_prr(per_rx_by_scheme, out_path, title_suffix="", t_start=None, t_end=None):
    fig, ax = plt.subplots(figsize=(13, 5))
    width = 0.2
    rx_ids = sorted({nid for s in SCHEMES for d in per_rx_by_scheme[s] for nid in d})
    rx_ids = [r for r in rx_ids if r != 0]
    x = np.arange(len(rx_ids))
    floor = 1.0
    for i, s in enumerate(SCHEMES):
        means = []
        for nid in rx_ids:
            vals = [d.get(nid, float("nan")) for d in per_rx_by_scheme[s]]
            vals = [v for v in vals if not np.isnan(v)]
            means.append(np.mean(vals) if vals else float("nan"))
        valid = [m for m in means if not np.isnan(m)]
        if valid:
            floor = min(floor, min(valid) - 0.05)
        ax.bar(x + (i - 2) * width, means, width,
               color=SCHEME_COLOR[s], label=SCHEME_LABEL[s])
    ax.set_xticks(x); ax.set_xticklabels([f"RX {r}" for r in rx_ids])
    ax.set_ylabel("PRR (mean across seeds)")
    ax.set_title(f"Per-receiver PRR{title_suffix}")
    ax.legend(fontsize=8, loc="lower right")
    ax.set_ylim(max(0.0, floor), 1.02)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_mux_vs_time(mux_by_scheme, out_path):
    fig, ax = plt.subplots(figsize=(13, 4))
    _annotate_intersection(ax)
    for s in SCHEMES:
        ax.plot(CENTERS, stack_median(mux_by_scheme[s]),
                color=SCHEME_COLOR[s], lw=2, label=SCHEME_LABEL[s])
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel(f"δ(t) = {TOTAL_PRBS} PRBs / rbLen")
    ax.set_title("Multiplexing capacity vs time")
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_etp_vs_time(prr_by_scheme, mux_by_scheme, out_path):
    fig, ax = plt.subplots(figsize=(13, 4))
    _annotate_intersection(ax)
    for s in SCHEMES:
        prr = stack_median(prr_by_scheme[s])
        mux = stack_median(mux_by_scheme[s])
        ax.plot(CENTERS, prr * mux, color=SCHEME_COLOR[s], lw=2, label=SCHEME_LABEL[s])
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel("Empirical Group-ETP  =  δ(t) · PRR(t)")
    ax.set_title("Empirical Group Effective Throughput")
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_mcs_trace(mcs_by_scheme, out_path):
    fig, ax = plt.subplots(figsize=(13, 4))
    _annotate_intersection(ax)
    for s in SCHEMES:
        ax.plot(CENTERS, stack_median(mcs_by_scheme[s]),
                color=SCHEME_COLOR[s], lw=2, label=SCHEME_LABEL[s])
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel("TX MCS (median across seeds)")
    ax.set_title("Chosen TX MCS over time — intersection_nlosv")
    ax.legend(fontsize=8); ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_pir_vs_time(pir_by_scheme, out_path):
    """Two-panel: median PIR and P95 PIR vs time with NLOSv zone annotated."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 4.5))

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
        _annotate_intersection(ax)
        ax.axhline(20, color="gray", ls=":", lw=1.2, alpha=0.7, label="RRI = 20 ms")
        ax.set_xlabel("Simulation time (s)")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=7, loc="upper right")

    ax1.set_ylabel("PIR median (ms)  [shaded = seed P5–P95]")
    ax1.set_title("Median PIR vs time")
    ax2.set_ylabel("PIR P95 (ms)  [worst-case latency]")
    ax2.set_title("P95 PIR vs time  (tail latency)")

    fig.suptitle("Packet Inter-Reception time — intersection_nlosv scenario",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_harq_overview(harq_by_scheme, out_path):
    metrics = [
        ("nack_rate",  "NACK rate (#NACK / #FB)",    (0.0, 0.45)),
        ("retx_ratio", "Retx ratio (#retx / #TX)",   (0.0, 0.45)),
        ("phy_err",    "PHY corruption rate",         (0.0, 0.30)),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(18, 4.5))
    for ax, (key, ylabel, ylim) in zip(axes, metrics):
        for s in SCHEMES:
            series = [d[key] for d in harq_by_scheme[s] if isinstance(d, dict)]
            if not series:
                continue
            mat = np.vstack(series)
            med = np.nanmedian(mat, axis=0)
            p5  = np.nanpercentile(mat, 5,  axis=0)
            p95 = np.nanpercentile(mat, 95, axis=0)
            ax.plot(CENTERS, med, color=SCHEME_COLOR[s], lw=2, label=SCHEME_LABEL[s])
            ax.fill_between(CENTERS, p5, p95, color=SCHEME_COLOR[s], alpha=0.12)
        _annotate_intersection(ax)
        ax.set_xlabel("Simulation time (s)")
        ax.set_ylabel(ylabel)
        ax.set_ylim(*ylim)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=7, loc="upper right")
    axes[0].set_title("NACK rate vs time")
    axes[1].set_title("Retransmission ratio vs time")
    axes[2].set_title("PHY corruption rate vs time")
    fig.suptitle("HARQ statistics — intersection_nlosv scenario", fontsize=13)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_harq_bar(harq_by_scheme, out_path):
    metrics = [("nack_rate", "NACK rate"),
               ("retx_ratio", "ReTx ratio"),
               ("phy_err", "PHY corruption")]
    x = np.arange(len(SCHEMES)); width = 0.25
    fig, ax = plt.subplots(figsize=(13, 5))
    for i, (key, label) in enumerate(metrics):
        means, errs = [], []
        for s in SCHEMES:
            vals = [float(np.nanmean(d[key])) for d in harq_by_scheme[s]
                    if isinstance(d, dict)]
            if vals:
                a = np.array(vals)
                means.append(float(np.mean(a)))
                errs.append(1.96 * float(a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 else 0.0)
            else:
                means.append(float("nan")); errs.append(0.0)
        ax.bar(x + (i - 1) * width, means, width, yerr=errs,
               label=label, capsize=4, alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels([SCHEME_LABEL[s] for s in SCHEMES], rotation=15, ha="right", fontsize=9)
    ax.set_ylabel("Rate (fraction)")
    ax.set_title("Aggregate HARQ statistics — intersection_nlosv (mean ± 95% CI)")
    ax.legend(fontsize=9); ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


def plot_overview(prr, perrx, perrx_nlosv, perrx_post, mux, mcs, harq, out_path):
    fig = plt.figure(figsize=(20, 16))
    gs  = GridSpec(4, 2, figure=fig, hspace=0.5, wspace=0.28)

    def annotate(ax):
        ax.axvspan(ENTER_T, CLEAR_T, alpha=0.06, color="red")
        ax.axvline(ENTER_T, color="red",    ls="--", lw=1, alpha=0.6)
        ax.axvline(CLEAR_T, color="purple", ls=":",  lw=1, alpha=0.6)

    # 1. PRR vs time
    ax = fig.add_subplot(gs[0, 0])
    annotate(ax)
    for s in SCHEMES:
        ax.plot(CENTERS, stack_median(prr[s]), color=SCHEME_COLOR[s],
                lw=1.8, label=SCHEME_LABEL[s])
    ax.set_title("Group PRR vs time"); ax.set_ylim(0.4, 1.02)
    ax.set_xlabel("time (s)"); ax.set_ylabel("PRR")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=7, ncol=1, loc="lower right")

    # 2. MCS over time
    ax = fig.add_subplot(gs[0, 1])
    annotate(ax)
    for s in SCHEMES:
        ax.plot(CENTERS, stack_median(mcs[s]), color=SCHEME_COLOR[s],
                lw=1.8, label=SCHEME_LABEL[s])
    ax.set_title("TX MCS over time")
    ax.set_xlabel("time (s)"); ax.set_ylabel("MCS")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=7)

    # 3. Multiplexing vs time
    ax = fig.add_subplot(gs[1, 0])
    annotate(ax)
    for s in SCHEMES:
        ax.plot(CENTERS, stack_median(mux[s]), color=SCHEME_COLOR[s],
                lw=1.8, label=SCHEME_LABEL[s])
    ax.set_title(f"Multiplexing δ = {TOTAL_PRBS}/rbLen")
    ax.set_xlabel("time (s)"); ax.set_ylabel("δ")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=7)

    # 4. ETP vs time
    ax = fig.add_subplot(gs[1, 1])
    annotate(ax)
    for s in SCHEMES:
        ax.plot(CENTERS, stack_median(prr[s]) * stack_median(mux[s]),
                color=SCHEME_COLOR[s], lw=1.8, label=SCHEME_LABEL[s])
    ax.set_title("Group-ETP = δ · PRR")
    ax.set_xlabel("time (s)"); ax.set_ylabel("Group-ETP")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=7)

    # 5. Per-RX PRR during NLOSv window
    ax = fig.add_subplot(gs[2, :])
    width = 0.2
    rx_ids = sorted({nid for s in SCHEMES for d in perrx_nlosv[s] for nid in d})
    rx_ids = [r for r in rx_ids if r != 0]
    x = np.arange(len(rx_ids))
    for i, s in enumerate(SCHEMES):
        means = [np.mean([d.get(nid, float("nan")) for d in perrx_nlosv[s]
                          if not np.isnan(d.get(nid, float("nan")))] or [float("nan")])
                 for nid in rx_ids]
        ax.bar(x + (i - 2) * width, means, width,
               color=SCHEME_COLOR[s], label=SCHEME_LABEL[s])
    ax.set_xticks(x); ax.set_xticklabels([f"RX {r}" for r in rx_ids])
    ax.set_ylim(0.3, 1.02); ax.set_ylabel("PRR")
    ax.set_title(f"Per-RX PRR during NLOSv window (t={ENTER_T:.1f}–{CLEAR_T:.1f} s)")
    ax.grid(True, axis="y", alpha=0.3); ax.legend(fontsize=8)

    # 6. HARQ NACK rate vs time
    ax = fig.add_subplot(gs[3, :])
    annotate(ax)
    for s in SCHEMES:
        series = [d["nack_rate"] for d in harq[s] if isinstance(d, dict)]
        if series:
            med = np.nanmedian(np.vstack(series), axis=0)
            ax.plot(CENTERS, med, color=SCHEME_COLOR[s], lw=1.8, label=SCHEME_LABEL[s])
    ax.set_xlabel("time (s)"); ax.set_ylabel("NACK rate")
    ax.set_title("HARQ NACK rate vs time")
    ax.grid(True, alpha=0.3); ax.legend(fontsize=7)

    fig.suptitle("RA-APC-Groupcast — intersection_nlosv scenario",
                 fontsize=14, y=1.002)
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {out_path}")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    OUT_DIR.mkdir(exist_ok=True)
    print("Loading per-seed data ...")

    prr         = {s: collect(s, per_seed_prr_vs_time)  for s in SCHEMES}
    perrx       = {s: collect(s, per_seed_per_rx_prr)   for s in SCHEMES}
    # Phase-split per-RX PRR
    perrx_nlosv = {s: [per_seed_per_rx_prr_phase(db, ENTER_T, CLEAR_T)
                        for db in sorted(glob.glob(str(RESULTS / s / "*.db")))]
                   for s in SCHEMES}
    perrx_post  = {s: [per_seed_per_rx_prr_phase(db, CLEAR_T, SIM_END_S)
                        for db in sorted(glob.glob(str(RESULTS / s / "*.db")))]
                   for s in SCHEMES}
    mux         = {s: collect(s, per_seed_mux_vs_time)  for s in SCHEMES}
    mcs         = {s: collect(s, per_seed_mcs_vs_time)  for s in SCHEMES}
    harq        = {s: collect(s, per_seed_harq_vs_time) for s in SCHEMES}
    pir         = {s: collect(s, per_seed_pir_vs_time)  for s in SCHEMES}

    print("\nRendering plots ...")
    plot_prr_vs_time(prr,    OUT_DIR / "prr_vs_time.png")
    plot_per_rx_prr(perrx,   OUT_DIR / "per_rx_prr_full.png",
                    title_suffix=" — full simulation")
    plot_per_rx_prr(perrx_nlosv, OUT_DIR / "per_rx_prr_nlosv.png",
                    title_suffix=f" — NLOSv window (t={ENTER_T:.1f}–{CLEAR_T:.1f} s)")
    plot_per_rx_prr(perrx_post,  OUT_DIR / "per_rx_prr_post.png",
                    title_suffix=f" — post-intersection (t={CLEAR_T:.1f}–60 s)")
    plot_mux_vs_time(mux,    OUT_DIR / "multiplex_vs_time.png")
    plot_etp_vs_time(prr, mux, OUT_DIR / "etp_vs_time.png")
    plot_mcs_trace(mcs,      OUT_DIR / "mcs_trace.png")
    plot_pir_vs_time(pir,    OUT_DIR / "pir_vs_time.png")
    plot_harq_overview(harq, OUT_DIR / "harq_vs_time.png")
    plot_harq_bar(harq,      OUT_DIR / "harq_bar.png")
    plot_overview(prr, perrx, perrx_nlosv, perrx_post, mux, mcs, harq,
                  OUT_DIR / "overview.png")

    print(f"\nDone — outputs in {OUT_DIR}")


if __name__ == "__main__":
    main()
