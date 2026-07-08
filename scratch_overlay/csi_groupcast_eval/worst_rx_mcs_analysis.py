#!/usr/bin/env python3
"""
worst_rx_mcs_analysis.py — find the best MCS that serves the worst RX.

For each (channel, traffic, mcs) setting in the ivd_sweep, identifies the
"worst RX" (lowest mean SINR across the simulation) and reports:
  - Worst RX's BLER
  - Worst RX's mean SINR (dB)
  - How many subchannels the worst RX's packets needed

Output: worst_rx_mcs.csv (one row per setting) + worst_rx_mcs_summary.txt
"""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
from pathlib import Path


def per_rx_stats(db_path: Path) -> list[dict] | None:
    """Return one row per rx_node with mean SINR (dB), BLER, n_rx events,
    max subchannel count used."""
    if not db_path.exists():
        return None
    con = sqlite3.connect(db_path)
    rows = []
    for r in con.execute("""
        SELECT
          rnti - 1                          AS rx_node,
          COUNT(*)                          AS n_rx,
          AVG(avrgSinr)                     AS mean_sinr_lin,
          MIN(avrgSinr)                     AS min_sinr_lin,
          AVG(psschCorrupt) * 100.0         AS bler_pct,
          MAX(psschRbLen / 50)              AS max_subchannels
        FROM psschRxUePhy
        GROUP BY rnti
    """):
        rx_node, n_rx, mean_sinr_lin, min_sinr_lin, bler_pct, max_sbch = r
        rows.append({
            "rx_node":      rx_node,
            "n_rx":         n_rx,
            "mean_sinr_dB": 10*math.log10(mean_sinr_lin) if mean_sinr_lin > 0 else float("-inf"),
            "min_sinr_dB":  10*math.log10(min_sinr_lin)  if min_sinr_lin  > 0 else float("-inf"),
            "bler_pct":     bler_pct,
            "max_subch":    max_sbch,
        })
    return rows


def find_worst_rx(rx_stats: list[dict]) -> dict:
    """Worst RX = the one with the lowest mean SINR."""
    return min(rx_stats, key=lambda r: r["mean_sinr_dB"])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--outputRoot", required=True, type=Path)
    p.add_argument("--outDir",      required=True, type=Path)
    args = p.parse_args()
    args.outDir.mkdir(parents=True, exist_ok=True)

    # Find all ivd_sweep cells (we may add merge later)
    rows = []
    for cell_dir in sorted(args.outputRoot.iterdir()):
        name = cell_dir.name
        if not name.startswith(("ivd_sweep", "merge_incremental")):
            continue
        parts = name.split("__")
        if len(parts) != 5:
            continue
        scenario, channel, traffic, mcs_part, seed_part = parts
        mcs  = int(mcs_part.replace("mcs", ""))
        seed = int(seed_part.replace("seed", ""))
        rx_stats = per_rx_stats(cell_dir / "run.db")
        if not rx_stats:
            continue
        worst = find_worst_rx(rx_stats)
        # Aggregate worst stats across seeds
        rows.append({
            "scenario":  scenario,
            "channel":   channel,
            "traffic":   traffic,
            "mcs":       mcs,
            "seed":      seed,
            "worst_rx":         worst["rx_node"],
            "worst_rx_n":       worst["n_rx"],
            "worst_mean_sinr_dB": worst["mean_sinr_dB"],
            "worst_min_sinr_dB":  worst["min_sinr_dB"],
            "worst_bler_pct":     worst["bler_pct"],
            "worst_max_subch":    worst["max_subch"],
        })

    # Write per-seed CSV
    csv_path = args.outDir / "worst_rx_mcs.csv"
    with csv_path.open("w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {csv_path} ({len(rows)} rows)")

    # Aggregate across seeds: per (channel, traffic, mcs), mean worst-RX BLER
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in rows:
        key = (r["scenario"], r["channel"], r["traffic"], r["mcs"])
        buckets[key].append(r)

    agg = []
    for key, samples in buckets.items():
        sc, ch, tr, mcs = key
        agg.append({
            "scenario":  sc,
            "channel":   ch,
            "traffic":   tr,
            "mcs":       mcs,
            "n_seeds":   len(samples),
            "mean_worst_rx":      sum(s["worst_rx"]         for s in samples) / len(samples),
            "mean_worst_sinr_dB": sum(s["worst_mean_sinr_dB"] for s in samples) / len(samples),
            "mean_worst_bler":    sum(s["worst_bler_pct"]     for s in samples) / len(samples),
            "std_worst_bler":     (sum((s["worst_bler_pct"] - sum(x["worst_bler_pct"] for x in samples)/len(samples))**2 for s in samples) / len(samples)) ** 0.5,
        })

    # Write summary CSV
    agg_csv = args.outDir / "worst_rx_mcs_summary.csv"
    with agg_csv.open("w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(agg[0].keys()))
        w.writeheader()
        w.writerows(agg)
    print(f"wrote {agg_csv} ({len(agg)} rows)")

    # Best MCS per (channel, traffic) by lowest worst-RX BLER
    print()
    print("=== Best MCS for worst RX (lowest mean worst-RX BLER) ===")
    print(f"{'scenario':<18}{'channel':<14}{'traffic':<12}{'best_mcs':>10}{'best_bler%':>13}{'runner_up_mcs':>15}{'runner_up_bler%':>17}{'delta':>8}")
    print("-" * 110)
    by_setting = defaultdict(list)
    for r in agg:
        by_setting[(r["scenario"], r["channel"], r["traffic"])].append(r)
    for key, samples in sorted(by_setting.items()):
        sc, ch, tr = key
        samples.sort(key=lambda r: r["mean_worst_bler"])
        best = samples[0]
        runner = samples[1] if len(samples) > 1 else None
        if runner:
            print(f"{sc:<18}{ch:<14}{tr:<12}{best['mcs']:>10d}{best['mean_worst_bler']:>13.2f}{runner['mcs']:>15d}{runner['mean_worst_bler']:>17.2f}{best['mean_worst_bler'] - runner['mean_worst_bler']:>8.2f}")
        else:
            print(f"{sc:<18}{ch:<14}{tr:<12}{best['mcs']:>10d}{best['mean_worst_bler']:>13.2f}{'(none)':>15}{'(none)':>17}{'-':>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
