#!/usr/bin/env python3
"""Unified plotting script for all RA-APC campaign results.

Usage:
    python3 plot_ra_apc.py --results results_intersect_nlosv_multi_flow
    python3 plot_ra_apc.py --results results_intersect_nlosv_multi_flow --plot mcs_evolution
    python3 plot_ra_apc.py --results results_intersect_nlosv_multi_flow --plot all

All plots saved to: results_intersect_nlosv_multi_flow/plots/
"""

import argparse
import sqlite3
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from collections import defaultdict

METHODS = ["ra_apc_A", "ra_apc_B", "nack_rate", "yan_broadcast", "yan_groupcast"]
METHOD_CONFIG = {
    "ra_apc_A": {"window_ms": 500, "eval_ms": 200},
    "ra_apc_B": {"window_ms": 500, "eval_ms": 200},
    "nack_rate": {"window_ms": 300, "eval_ms": 150, "down_thr": 0.15, "up_thr": 0.08},
}

# ── Time binning helpers (from intersect_nlosv) ─────────────────────
EDGES = (0, 60)
BIN_S = 1
N_BINS = int((EDGES[1] - EDGES[0]) / BIN_S)

def _bin(t):
    idx = int((t - EDGES[0]) / BIN_S)
    return max(0, min(idx, N_BINS - 1))


# ── Data collection functions ─────────────────────────────────────────
def per_seed_prr_vs_time(db_path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT (timeMs / 1000.0) as t, avrgPrr FROM avrgPrr WHERE txRx='rx' ORDER BY t")
    rows = cur.fetchall()
    con.close()
    result = defaultdict(list)
    for t, prr in rows:
        result[_bin(t)].append(prr)
    return {k: np.median(v) for k, v in result.items()}


def per_seed_per_rx_prr(db_path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT avrgPrr FROM avrgPrr WHERE txRx='rx'")
    rows = cur.fetchall()
    con.close()
    return [r[0] for r in rows]


def per_seed_mux_vs_time(db_path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT (timeMs / 1000.0) as t, AVG(rbLen) as rb FROM psschTxUeMac GROUP BY ROUND(timeMs/1000.0, 0) ORDER BY t")
    rows = cur.fetchall()
    con.close()
    result = defaultdict(list)
    for t, rb in rows:
        if rb:
            result[_bin(t)].append(rb)
    return {k: np.median(v) for k, v in result.items()}


def per_seed_mcs_vs_time(db_path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT (timeMs / 1000.0) as t, psschMcs, COUNT(*) as cnt FROM psschRxUePhy GROUP BY ROUND(timeMs/1000.0, 0), psschMcs ORDER BY t")
    rows = cur.fetchall()
    con.close()
    result = defaultdict(lambda: defaultdict(int))
    for t, mcs, cnt in rows:
        result[_bin(t)][mcs] += cnt
    return {k: max(v.keys(), key=lambda m: v[m]) for k, v in result.items() if v}


def per_seed_pir_vs_time(db_path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT (timeMs / 1000.0) as t, avrgPirSec * 1000.0 as pir_ms FROM avrgPir WHERE txRx='rx' ORDER BY t")
    rows = cur.fetchall()
    con.close()
    result = defaultdict(list)
    for t, pir in rows:
        result[_bin(t)].append(pir)
    return {k: np.median(v) for k, v in result.items()}


def per_seed_harq_vs_time(db_path):
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT (timeSec) as t, status, COUNT(*) as cnt FROM slHarqFeedback GROUP BY ROUND(timeSec, 0), status ORDER BY t")
    rows = cur.fetchall()
    con.close()
    ack, nack = defaultdict(int), defaultdict(int)
    for t, status, cnt in rows:
        idx = _bin(t)
        if status == "ACK":
            ack[idx] += cnt
        else:
            nack[idx] += cnt
    return {"ack": ack, "nack": nack}


# ── Aggregation helpers ─────────────────────────────────────────────
def collect(scheme, fn, **kwargs):
    import glob
    results = {}
    dbs = glob.glob(str(RESULTS_DIR / scheme / "*.db"))
    for db in dbs:
        seed_key = Path(db).stem
        try:
            data = fn(db, **kwargs)
            results[seed_key] = data
        except Exception as e:
            print(f"  skip {seed_key}: {e}")
    return results


def stack_median(series):
    all_bins = set()
    for s in series.values():
        all_bins.update(s.keys())
    result = {}
    for b in sorted(all_bins):
        vals = [s.get(b, np.nan) for s in series.values()]
        result[b] = np.nanmedian(vals)
    return result


def stack_p5(series):
    all_bins = set()
    for s in series.values():
        all_bins.update(s.keys())
    result = {}
    for b in sorted(all_bins):
        vals = [s.get(b, np.nan) for s in series.values()]
        result[b] = np.nanpercentile(vals, 5)
    return result


# ── Get functions for adaptive plots ─────────────────────────────────────────
def get_adapt_log(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT timeMs, metric, prevMcs, newMcs, decision, ackCount, nackCount, windowStartMs, windowEndMs FROM adaptLog ORDER BY timeMs")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_mcs_changes(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT ROUND(timeMs/1000.0, 0) as t, psschMcs, COUNT(*) FROM psschRxUePhy GROUP BY ROUND(timeMs/1000.0, 0), psschMcs ORDER BY t")
    rows = cur.fetchall()
    conn.close()
    mcs_by_time, count_by_time = {}, {}
    for t, mcs, cnt in rows:
        t = int(t)
        mcs_by_time[t] = mcs_by_time.get(t, 0) + mcs * cnt
        count_by_time[t] = count_by_time.get(t, 0) + cnt
    return {t: mcs_by_time[t] / count_by_time[t] for t in mcs_by_time}


def get_ack_nack(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT ROUND(timeSec, 0) as t, status, COUNT(*) FROM slHarqFeedback GROUP BY ROUND(timeSec, 0), status ORDER BY t")
    rows = cur.fetchall()
    conn.close()
    ack, nack = {}, {}
    for t, status, cnt in rows:
        t = int(t)
        if status == "ACK":
            ack[t] = cnt
        else:
            nack[t] = cnt
    all_times = sorted(set(ack.keys()) | set(nack.keys()))
    return {"ack": [ack.get(t, 0) for t in all_times], "nack": [nack.get(t, 0) for t in all_times], "time": all_times}


# ── Plot functions ─────────────────────────────────────────────────
def plot_prr_vs_time(results_dir, methods, plots_dir):
    data_by_scheme = {}
    for scheme in methods:
        seeds = collect(scheme, per_seed_prr_vs_time)
        if seeds:
            data_by_scheme[scheme] = stack_median(seeds)
    if not data_by_scheme:
        print("  No PRR data")
        return
    fig, ax = plt.subplots(figsize=(12, 5))
    for scheme, data in data_by_scheme.items():
        times = sorted(data.keys())
        ax.plot(times, [data.get(t, np.nan) for t in times], '-o', markersize=4, label=scheme)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("PRR")
    ax.set_title("PRR vs Time")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(plots_dir / "prr_vs_time.png", dpi=150)
    plt.close()
    print(f"Saved {plots_dir / 'prr_vs_time.png'}")


def plot_per_rx_prr(results_dir, methods, plots_dir):
    data_by_scheme = {}
    for scheme in methods:
        seeds = collect(scheme, per_seed_per_rx_prr)
        if seeds:
            data = []
            for s in seeds.values():
                data.extend(s)
            if data:
                data_by_scheme[scheme] = data
    if not data_by_scheme:
        print("  No per-RX PRR data")
        return
    fig, ax = plt.subplots(figsize=(10, 6))
    box_data = [data_by_scheme[s] for s in methods if s in data_by_scheme]
    labels = [s for s in methods if s in data_by_scheme]
    ax.boxplot(box_data, labels=labels, patch_artist=True)
    ax.set_ylabel("PRR")
    ax.set_title("Per-Receiver PRR")
    ax.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()
    plt.savefig(plots_dir / "per_rx_prr_full.png", dpi=150)
    plt.close()
    print(f"Saved {plots_dir / 'per_rx_prr_full.png'}")


def plot_mux_vs_time(results_dir, methods, plots_dir):
    data_by_scheme = {}
    for scheme in methods:
        seeds = collect(scheme, per_seed_mux_vs_time)
        if seeds:
            data_by_scheme[scheme] = stack_median(seeds)
    if not data_by_scheme:
        print("  No mux data")
        return
    fig, ax = plt.subplots(figsize=(12, 5))
    for scheme, data in data_by_scheme.items():
        times = sorted(data.keys())
        ax.plot(times, [data.get(t, np.nan) for t in times], '-o', markersize=3, label=scheme)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Avg RB Len")
    ax.set_title("Multiplexing vs Time")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(plots_dir / "multiplex_vs_time.png", dpi=150)
    plt.close()
    print(f"Saved {plots_dir / 'multiplex_vs_time.png'}")


def plot_etp_vs_time(results_dir, methods, plots_dir):
    data_by_scheme = {}
    for scheme in methods:
        prr_seeds = collect(scheme, per_seed_prr_vs_time)
        mux_seeds = collect(scheme, per_seed_mux_vs_time)
        if prr_seeds and mux_seeds:
            combined = {}
            for seed in prr_seeds:
                if seed in mux_seeds:
                    prr = prr_seeds[seed]
                    mux = mux_seeds[seed]
                    combined[seed] = {k: prr.get(k, np.nan) * mux.get(k, np.nan) for k in set(prr.keys()) | set(mux.keys())}
            if combined:
                data_by_scheme[scheme] = stack_median(combined)
    if not data_by_scheme:
        print("  No ETP data")
        return
    fig, ax = plt.subplots(figsize=(12, 5))
    for scheme, data in data_by_scheme.items():
        times = sorted(data.keys())
        ax.plot(times, [data.get(t, np.nan) for t in times], '-o', markersize=3, label=scheme)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("ETP (PRR × mux)")
    ax.set_title("Expected Throughput per Slot vs Time")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(plots_dir / "etp_vs_time.png", dpi=150)
    plt.close()
    print(f"Saved {plots_dir / 'etp_vs_time.png'}")


def plot_mcs_trace(results_dir, methods, plots_dir):
    data_by_scheme = {}
    for scheme in methods:
        seeds = collect(scheme, per_seed_mcs_vs_time)
        if seeds:
            data_by_scheme[scheme] = stack_median(seeds)
    if not data_by_scheme:
        print("  No MCS data")
        return
    fig, ax = plt.subplots(figsize=(12, 5))
    for scheme, data in data_by_scheme.items():
        times = sorted(data.keys())
        ax.plot(times, [data.get(t, np.nan) for t in times], '-o', markersize=3, label=scheme)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("MCS")
    ax.set_title("MCS Trace")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(plots_dir / "mcs_trace.png", dpi=150)
    plt.close()
    print(f"Saved {plots_dir / 'mcs_trace.png'}")


def plot_pir_vs_time(results_dir, methods, plots_dir):
    data_by_scheme = {}
    for scheme in methods:
        seeds = collect(scheme, per_seed_pir_vs_time)
        if seeds:
            data_by_scheme[scheme] = stack_median(seeds)
    if not data_by_scheme:
        print("  No PIR data")
        return
    fig, ax = plt.subplots(figsize=(12, 5))
    for scheme, data in data_by_scheme.items():
        times = sorted(data.keys())
        ax.plot(times, [data.get(t, np.nan) for t in times], '-o', markersize=3, label=scheme)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("PIR (ms)")
    ax.set_title("Packet Information Rate vs Time")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(plots_dir / "pir_vs_time.png", dpi=150)
    plt.close()
    print(f"Saved {plots_dir / 'pir_vs_time.png'}")


def plot_harq_overview(results_dir, methods, plots_dir):
    data_by_scheme = {}
    for scheme in methods:
        seeds = collect(scheme, per_seed_harq_vs_time)
        if seeds:
            ack_all, nack_all = defaultdict(int), defaultdict(int)
            for s in seeds.values():
                for k, v in s["ack"].items():
                    ack_all[k] += v
                for k, v in s["nack"].items():
                    nack_all[k] += v
            data_by_scheme[scheme] = {"ack": dict(ack_all), "nack": dict(nack_all)}
    if not data_by_scheme:
        print("  No HARQ data")
        return
    fig, axes = plt.subplots(len(data_by_scheme), 1, figsize=(12, 3 * len(data_by_scheme)), sharex=True)
    if len(data_by_scheme) == 1:
        axes = [axes]
    for idx, (scheme, data) in enumerate(data_by_scheme.items()):
        ax = axes[idx]
        times = sorted(set(data["ack"].keys()) | set(data["nack"].keys()))
        ack = [data["ack"].get(t, 0) for t in times]
        nack = [data["nack"].get(t, 0) for t in times]
        ax.bar(times, ack, label="ACK", color="green", alpha=0.7)
        ax.bar(times, nack, bottom=ack, label="NACK", color="red", alpha=0.7)
        ax.set_ylabel("Count")
        ax.set_title(scheme)
        ax.legend()
        ax.grid(True, alpha=0.3)
    axes[-1].set_xlabel("Time (s)")
    plt.tight_layout()
    plt.savefig(plots_dir / "harq_vs_time.png", dpi=150)
    plt.close()
    print(f"Saved {plots_dir / 'harq_vs_time.png'}")


def plot_harq_bar(results_dir, methods, plots_dir):
    data_by_scheme = {}
    for scheme in methods:
        seeds = collect(scheme, per_seed_harq_vs_time)
        if seeds:
            total_ack, total_nack = 0, 0
            for s in seeds.values():
                total_ack += sum(s["ack"].values())
                total_nack += sum(s["nack"].values())
            data_by_scheme[scheme] = {"ack": total_ack, "nack": total_nack}
    if not data_by_scheme:
        print("  No HARQ data for bar plot")
        return
    fig, ax = plt.subplots(figsize=(10, 6))
    labels = list(data_by_scheme.keys())
    ack_vals = [data_by_scheme[s]["ack"] for s in labels]
    nack_vals = [data_by_scheme[s]["nack"] for s in labels]
    x = np.arange(len(labels))
    width = 0.35
    ax.bar(x - width/2, ack_vals, width, label="ACK", color="green", alpha=0.7)
    ax.bar(x + width/2, nack_vals, width, label="NACK", color="red", alpha=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Count")
    ax.set_title("HARQ Feedback Summary")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    plt.savefig(plots_dir / "harq_bar.png", dpi=150)
    plt.close()
    print(f"Saved {plots_dir / 'harq_bar.png'}")


def plot_overview(results_dir, methods, plots_dir):
    """Combined overview plot."""
    data = {}
    for scheme in methods:
        prr = collect(scheme, per_seed_prr_vs_time)
        mux = collect(scheme, per_seed_mux_vs_time)
        mcs = collect(scheme, per_seed_mcs_vs_time)
        harq = collect(scheme, per_seed_harq_vs_time)
        if prr or mux or harq:
            data[scheme] = {"prr": stack_median(prr) if prr else {}, 
                         "mux": stack_median(mux) if mux else {},
                         "mcs": stack_median(mcs) if mcs else {},
                         "harq": harq}
    
    if not data:
        print("  No data for overview")
        return
    
    fig = plt.figure(figsize=(15, 10))
    gs = fig.add_gridspec(2, 2, hspace=0.3, wspace=0.3)
    
    # PRR
    ax = fig.add_subplot(gs[0, 0])
    for scheme, d in data.items():
        if d["prr"]:
            times = sorted(d["prr"].keys())
            ax.plot(times, [d["prr"].get(t, np.nan) for t in times], "-o", markersize=3, label=scheme)
    ax.set_ylabel("PRR")
    ax.set_title("PRR vs Time")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    
    # Multiplexing
    ax = fig.add_subplot(gs[0, 1])
    for scheme, d in data.items():
        if d["mux"]:
            times = sorted(d["mux"].keys())
            ax.plot(times, [d["mux"].get(t, np.nan) for t in times], "-o", markersize=3, label=scheme)
    ax.set_ylabel("Avg RB Len")
    ax.set_title("Multiplexing vs Time")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    
    # MCS
    ax = fig.add_subplot(gs[1, 0])
    for scheme, d in data.items():
        if d["mcs"]:
            times = sorted(d["mcs"].keys())
            ax.plot(times, [d["mcs"].get(t, np.nan) for t in times], "-o", markersize=3, label=scheme)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("MCS")
    ax.set_title("MCS Trace")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    
    # Summary text
    ax = fig.add_subplot(gs[1, 1])
    ax.axis("off")
    summary = "Summary\n" + "=" * 40 + "\n\n"
    for scheme, d in data.items():
        summary += f"{scheme}:\n"
        if d["harq"]:
            ack = sum(d["harq"].values())[0].get("ack", {}).values() if hasattr(sum(d["harq"].values())[0], "get") else sum(sum(s["ack"].values() for s in d["harq"].values()))
            nack = sum(s["nack"].values() for s in d["harq"].values())
            total = ack + nack
            if total > 0:
                summary += f"  ACK: {ack} ({100*ack/total:.1f}%)\n"
                summary += f"  NACK: {nack} ({100*nack/total:.1f}%)\n"
        summary += "\n"
    ax.text(0.1, 0.9, summary, transform=ax.transAxes, fontsize=9, verticalalignment="top", family="monospace")
    
    plt.savefig(plots_dir / "overview.png", dpi=150)
    plt.close()
    print(f"Saved {plots_dir / 'overview.png'}")


# ── Adaptations plots ───────────────────────────────────────────────
def plot_mcs_evolution(results_dir, methods, plots_dir):
    data = {}
    for method in methods:
        db_path = Path(results_dir) / method / f"{method}_seed1.db"
        if db_path.exists():
            data[method] = {"mcs": get_mcs_changes(str(db_path)), "harq": get_ack_nack(str(db_path))}
    if not data:
        print("  No MCS evolution data")
        return
    n = len(data)
    fig, axes = plt.subplots(n, 2, figsize=(14, 3 * n), sharex=True)
    if n == 1:
        axes = [axes]
    for idx, (method, d) in enumerate(data.items()):
        ax_mcs, ax_harq = axes[idx]
        if d["mcs"]:
            times = sorted(d["mcs"].keys())
            ax_mcs.plot(times, [d["mcs"][t] for t in times], 'b-o', markersize=3)
        if d["harq"]:
            t = d["harq"]["time"]
            w = 0.8
            ax_harq.bar([x - w/2 for x in t], d["harq"]["ack"], w, label="ACK", color="green", alpha=0.7)
            ax_harq.bar([x + w/2 for x in t], d["harq"]["nack"], w, label="NACK", color="red", alpha=0.7)
            ax_harq.legend()
        ax_mcs.set_title(f"{method} - MCS")
        ax_harq.set_title(f"{method} - HARQ")
        ax_mcs.grid(True, alpha=0.3)
        ax_harq.grid(True, alpha=0.3)
    axes[-1][0].set_xlabel("Time (s)")
    axes[-1][1].set_xlabel("Time (s)")
    plt.tight_layout()
    plt.savefig(plots_dir / "mcs_evolution.png", dpi=150)
    plt.close()
    print(f"Saved {plots_dir / 'mcs_evolution.png'}")


def plot_adaptation_metric(results_dir, methods, plots_dir):
    fig, axes = plt.subplots(len(methods), 1, figsize=(14, 3 * len(methods)), sharex=True)
    if len(methods) == 1:
        axes = [axes]
    for idx, method in enumerate(methods):
        ax = axes[idx]
        db_path = Path(results_dir) / method / f"{method}_seed1.db"
        if not db_path.exists():
            ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center")
            ax.set_title(method)
            continue
        rows = get_adapt_log(str(db_path))
        if rows:
            times = [r[0] / 1000.0 for r in rows]
            metrics = [r[1] for r in rows]
            new_mcs = [r[3] for r in rows]
            ax.plot(times, metrics, "b-", lw=1.5, label="Metric")
            ax.fill_between(times, 0, metrics, alpha=0.3)
            config = METHOD_CONFIG.get(method, {})
            if method == "nack_rate":
                ax.axhline(config.get("down_thr", 0.15), color="red", ls="--", alpha=0.7)
                ax.axhline(config.get("up_thr", 0.08), color="green", ls="--", alpha=0.7)
            for r in rows:
                if r[3] != r[2]:
                    ax.axvline(r[0]/1000.0, color="red" if r[3] < r[2] else "green", alpha=0.6, ls="--")
            ax2 = ax.twinx()
            ax2.step(times, new_mcs, where="post", color="purple", lw=2)
            ax2.set_ylabel("MCS", color="purple")
        else:
            ax.text(0.5, 0.5, "No adaptLog", transform=ax.transAxes, ha="center")
        ax.set_title(method)
        ax.grid(True, alpha=0.3)
    axes[-1].set_xlabel("Time (s)")
    plt.tight_layout()
    plt.savefig(plots_dir / "adaptation_metric.png", dpi=150)
    plt.close()
    print(f"Saved {plots_dir / 'adaptation_metric.png'}")


# ── Main ───────────────────────────────────────────────────────────
def main():
    global RESULTS_DIR
    
    parser = argparse.ArgumentParser(description="Plot RA-APC results")
    parser.add_argument("--results", required=True, help="Results folder")
    parser.add_argument("--plot", help="Plot type (default: all)")
    parser.add_argument("--methods", nargs="+", default=METHODS, help="Methods to plot")
    args = parser.parse_args()
    
    script_dir = Path(__file__).parent
    results_dir = script_dir / args.results
    RESULTS_DIR = results_dir
    
    # Always use results-specific plots folder
    plots_dir = results_dir / "plots"
    plots_dir.mkdir(exist_ok=True)
    
    plot_all = not args.plot
    plot_type = args.plot
    
    plot_map = {
        "prr_vs_time": plot_prr_vs_time,
        "per_rx_prr": plot_per_rx_prr,
        "mux_vs_time": plot_mux_vs_time,
        "multiplex_vs_time": plot_mux_vs_time,  # alias
        "etp_vs_time": plot_etp_vs_time,
        "mcs_trace": plot_mcs_trace,
        "pir_vs_time": plot_pir_vs_time,
        "harq_overview": plot_harq_overview,
        "harq_vs_time": plot_harq_overview,  # alias
        "harq_bar": plot_harq_bar,
        "overview": plot_overview,
        "mcs_evolution": plot_mcs_evolution,
        "adaptation_metric": plot_adaptation_metric,
    }
    
    if plot_all:
        for name, func in plot_map.items():
            func(results_dir, args.methods, plots_dir)
        print(f"\nAll plots saved to {plots_dir}/")
    else:
        if plot_type in plot_map:
            plot_map[plot_type](results_dir, args.methods, plots_dir)
        else:
            print(f"Unknown plot: {plot_type}")
            print(f"Available: {', '.join(plot_map.keys())}")


if __name__ == "__main__":
    main()
