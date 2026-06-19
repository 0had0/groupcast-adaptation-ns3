#!/usr/bin/env python3
"""
analyse_ra_apc.py — cross-scheme RA-APC-Groupcast campaign aggregator.

Usage:
    python3 analyse_ra_apc.py --results results_ra_apc
    python3 analyse_ra_apc.py --results results_intersect_nlosv
    python3 analyse_ra_apc.py --results results_intersect_nlosv_multi_flow
"""

import argparse
import csv
import glob
import sqlite3
from pathlib import Path

import numpy as np

SCHEMES = ["yan_broadcast", "yan_groupcast", "ra_apc_A", "ra_apc_B", "nack_rate"]
POOL_PRBS = 90
N_SLOTS = 120_000


def seed_of(stem):
    for part in stem.split("_"):
        if part.startswith("seed"):
            try:
                return int(part[4:])
            except ValueError:
                pass
    return None


def load_one(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    prr_rx = [r["avrgPrr"] for r in
              con.execute("SELECT avrgPrr FROM avrgPrr WHERE txRx='tx'")]
    thput_rx = [r["thputKbps"] for r in
                con.execute("SELECT thputKbps FROM thput WHERE txRx='rx'")]
    pir_rx = [r["avrgPirSec"] * 1000.0 for r in
             con.execute("SELECT avrgPirSec FROM avrgPir WHERE txRx='rx'")]
    avg_rb = con.execute("SELECT AVG(rbLen) FROM psschTxUeMac").fetchone()[0] or 0.0
    rb_distrib = dict(con.execute(
        "SELECT rbLen, COUNT(*) FROM psschTxUeMac GROUP BY rbLen").fetchall())
    con.close()
    return {
        "group_prr": prr_rx[0] if prr_rx else float("nan"),
        "tput_mean": float(np.mean(thput_rx)) if thput_rx else float("nan"),
        "tput_p5": float(np.percentile(thput_rx, 5)) if thput_rx else float("nan"),
        "pir_mean": float(np.mean(pir_rx)) if pir_rx else float("nan"),
        "pir_p95": float(np.percentile(pir_rx, 95)) if pir_rx else float("nan"),
        "avg_rb": avg_rb,
        "rb_distrib": rb_distrib,
    }


def load_harq_stats(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    fb = con.execute(
        "SELECT status, COUNT(*) AS cnt FROM slHarqFeedback GROUP BY status"
    ).fetchall()
    fb_dict = {row["status"]: row["cnt"] for row in fb}
    n_ack = fb_dict.get("ACK", 0)
    n_nack = fb_dict.get("NACK", 0)
    total_fb = n_ack + n_nack
    nack_rate = n_nack / total_fb if total_fb > 0 else float("nan")
    n_timeout = con.execute("SELECT COUNT(*) FROM slHarqTimeout").fetchone()[0]
    n_initial = con.execute(
        "SELECT COUNT(*) FROM psschTxUeMac WHERE ndi=1"
    ).fetchone()[0]
    n_retx = con.execute(
        "SELECT COUNT(*) FROM psschTxUeMac WHERE ndi=0"
    ).fetchone()[0]
    retx_ratio = n_retx / n_initial if n_initial > 0 else float("nan")
    all_rbs = [row[0] for row in con.execute("SELECT rbLen FROM psschTxUeMac").fetchall()]
    avg_rb = float(np.mean(all_rbs)) if all_rbs else float("nan")
    total_rb_used = int(sum(all_rbs))
    channel_load = total_rb_used / (N_SLOTS * POOL_PRBS) if all_rbs else float("nan")
    n_corrupt = con.execute(
        "SELECT COUNT(*) FROM psschRxUePhy WHERE psschCorrupt=1"
    ).fetchone()[0]
    n_rx_total = con.execute("SELECT COUNT(*) FROM psschRxUePhy").fetchone()[0]
    phy_err_rate = n_corrupt / n_rx_total if n_rx_total > 0 else float("nan")
    con.close()
    return {
        "n_ack": n_ack, "n_nack": n_nack, "nack_rate": nack_rate,
        "n_timeout": n_timeout, "n_retx": n_retx, "retx_ratio": retx_ratio,
        "avg_rb": avg_rb, "channel_load": channel_load, "phy_err_rate": phy_err_rate,
    }


def ci95(arr):
    a = np.asarray([x for x in arr if not np.isnan(x)])
    if a.size == 0:
        return float("nan"), float("nan")
    if a.size == 1:
        return float(a[0]), 0.0
    se = a.std(ddof=1) / np.sqrt(a.size)
    return float(a.mean()), 1.96 * float(se)


def main():
    parser = argparse.ArgumentParser(description="Aggregate RA-APC campaign results")
    parser.add_argument("--results", required=True,
                       help="Results folder name (e.g., results_ra_apc)")
    parser.add_argument("--schemes", nargs="+", default=SCHEMES,
                       help="Schemes to analyze")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    results_dir = script_dir / args.results
    out_dir = results_dir / "campaigns"
    out_dir.mkdir(exist_ok=True)

    summary_rows = []
    per_scheme = {s: {"prr": [], "tput_mean": [], "tput_p5": [],
                      "pir_mean": [], "pir_p95": [], "avg_rb": []}
                 for s in args.schemes}
    harq_scheme = {s: {"nack_rate": [], "retx_ratio": [], "channel_load": [],
                      "phy_err_rate": [], "n_timeout": []}
                 for s in args.schemes}

    print(f"Loading runs from {results_dir} ...")
    for scheme in args.schemes:
        dbs = sorted(glob.glob(str(results_dir / scheme / "*.db")))
        print(f"  {scheme:>14}: {len(dbs)} seeds")
        for db in dbs:
            seed = seed_of(Path(db).stem)
            k = load_one(db)
            h = load_harq_stats(db)
            summary_rows.append([
                scheme, seed,
                k["group_prr"], k["tput_mean"], k["tput_p5"],
                k["pir_mean"], k["pir_p95"], k["avg_rb"],
                ",".join(f"{kk}:{vv}" for kk, vv in sorted(k["rb_distrib"].items())),
                h["nack_rate"], h["retx_ratio"], h["n_timeout"],
                h["channel_load"], h["phy_err_rate"],
            ])
            per_scheme[scheme]["prr"].append(k["group_prr"])
            per_scheme[scheme]["tput_mean"].append(k["tput_mean"])
            per_scheme[scheme]["tput_p5"].append(k["tput_p5"])
            per_scheme[scheme]["pir_mean"].append(k["pir_mean"])
            per_scheme[scheme]["pir_p95"].append(k["pir_p95"])
            per_scheme[scheme]["avg_rb"].append(k["avg_rb"])
            harq_scheme[scheme]["nack_rate"].append(h["nack_rate"])
            harq_scheme[scheme]["retx_ratio"].append(h["retx_ratio"])
            harq_scheme[scheme]["channel_load"].append(h["channel_load"])
            harq_scheme[scheme]["phy_err_rate"].append(h["phy_err_rate"])
            harq_scheme[scheme]["n_timeout"].append(h["n_timeout"])

    with open(out_dir / "ra_apc_summary.csv", "w") as f:
        w = csv.writer(f)
        w.writerow(["scheme", "seed", "group_prr", "tput_mean_kbps", "tput_p5_kbps",
                    "pir_mean_ms", "pir_p95_ms", "avg_rb_prbs", "rb_distrib",
                    "nack_rate", "retx_ratio", "n_harq_timeout",
                    "channel_load_fraction", "phy_err_rate"])
        w.writerows(summary_rows)

    print("\nAggregate (mean ± 95% CI across seeds):")
    print(f"  {'scheme':>14}  {'PRR':>14}  {'Tput mean (kbps)':>20}  "
          f"{'Tput P5 (kbps)':>17}  {'PIR mean (ms)':>15}  {'avg rbLen':>12}")
    agg_rows = []
    for s in args.schemes:
        prr_m, prr_ci = ci95(per_scheme[s]["prr"])
        tm_m, tm_ci = ci95(per_scheme[s]["tput_mean"])
        t5_m, t5_ci = ci95(per_scheme[s]["tput_p5"])
        pm_m, pm_ci = ci95(per_scheme[s]["pir_mean"])
        rb_m, rb_ci = ci95(per_scheme[s]["avg_rb"])
        agg_rows.append([s, prr_m, prr_ci, tm_m, tm_ci, t5_m, t5_ci,
                     pm_m, pm_ci, rb_m, rb_ci])
        print(f"  {s:>14}  {prr_m:6.3f}±{prr_ci:6.3f}  "
              f"{tm_m:9.2f}±{tm_ci:9.2f}  {t5_m:7.2f}±{t5_ci:7.2f}  "
              f"{pm_m:6.2f}±{pm_ci:6.2f}  {rb_m:5.2f}±{rb_ci:5.2f}")

    with open(out_dir / "ra_apc_aggregate.csv", "w") as f:
        w = csv.writer(f)
        w.writerow(["scheme", "prr_mean", "prr_ci95", "tput_mean_kbps", "tput_mean_ci95",
                    "tput_p5_kbps", "tput_p5_ci95", "pir_mean_ms", "pir_mean_ci95",
                    "avg_rb_prbs", "avg_rb_ci95"])
        w.writerows(agg_rows)

    print("\nHARQ / resource statistics (mean ± 95% CI):")
    print(f"  {'scheme':>14}  {'NACK rate':>12}  {'ReTx ratio':>12}  "
          f"{'Ch. load':>12}  {'PHY err':>10}  {'Timeouts':>10}")
    harq_agg_rows = []
    for s in args.schemes:
        nr_m, nr_ci = ci95(harq_scheme[s]["nack_rate"])
        rx_m, rx_ci = ci95(harq_scheme[s]["retx_ratio"])
        cl_m, cl_ci = ci95(harq_scheme[s]["channel_load"])
        pe_m, pe_ci = ci95(harq_scheme[s]["phy_err_rate"])
        to_m, to_ci = ci95(harq_scheme[s]["n_timeout"])
        harq_agg_rows.append([s, nr_m, nr_ci, rx_m, rx_ci, cl_m, cl_ci, pe_m, pe_ci, to_m, to_ci])
        print(f"  {s:>14}  {nr_m:.4f}±{nr_ci:.4f}  {rx_m:.4f}±{rx_ci:.4f}  "
              f"{cl_m:.5f}±{cl_ci:.5f}  {pe_m:.4f}±{pe_ci:.4f}  {to_m:6.1f}±{to_ci:5.1f}")

    with open(out_dir / "ra_apc_harq_aggregate.csv", "w") as f:
        w = csv.writer(f)
        w.writerow(["scheme", "nack_rate_mean", "nack_rate_ci95",
                    "retx_ratio_mean", "retx_ratio_ci95",
                    "channel_load_mean", "channel_load_ci95",
                    "phy_err_rate_mean", "phy_err_rate_ci95",
                    "n_timeout_mean", "n_timeout_ci95"])
        w.writerows(harq_agg_rows)

    print(f"\nResults written to {out_dir}/")


if __name__ == "__main__":
    main()
