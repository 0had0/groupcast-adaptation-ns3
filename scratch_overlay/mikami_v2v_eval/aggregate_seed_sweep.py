#!/usr/bin/env python3
"""aggregate_seed_sweep.py — summarize KPI CSVs across N seed runs.

For each KPI type, reads `<root>/seed{NN}/kpis/{tag}_<kpi>.csv` for every seed
under <root>, then groups by (tx_node_id, rx_node_id) and reports
mean / std / min / max across seeds for every numeric column.

Output:
  <out>/summary_<kpi>.csv      per-link per-KPI cross-seed stats
  <out>/summary_per_seed.csv   flat table: seed × (tx,rx) × KPI value (one row per metric per link per seed)
  <out>/summary.md             markdown table for the headline KPIs
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple


# Headline KPIs that get extra presentation (PRR + BLER + SINR + OTT latency)
HEADLINE_KPIS = [
    "mikami_bler",
    "mikami_sinr",
    "mikami_prr_before_harq",
    "mikami_prr_after_harq",
]


def _safe_mean(xs):
    return statistics.fmean(xs) if xs else float("nan")


def _safe_std(xs):
    if len(xs) < 2:
        return float("nan")
    return statistics.stdev(xs)


def _read_csv(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _per_seed_link_values(rows: List[dict], value_col: str) -> Dict[Tuple[str, str], float]:
    """For per-link CSV where each (tx,rx) appears once per file, return value per (tx,rx)."""
    out = {}
    for r in rows:
        try:
            out[(r["tx_node_id"], r["rx_node_id"])] = float(r[value_col])
        except (KeyError, ValueError):
            continue
    return out


def _ott_p50(rows: List[dict]) -> float:
    if not rows:
        return float("nan")
    vals = sorted(float(r["latency_ms"]) for r in rows)
    n = len(vals)
    return vals[n // 2]


def _summarize_numeric_columns(rows_per_seed: List[List[dict]],
                               numeric_cols: List[str]) -> Dict[str, Dict[str, float]]:
    """For each numeric col, compute mean/std/min/max across seeds at each (tx,rx).

    rows_per_seed: list of per-seed CSV row-lists; each row-list is for one seed.
    numeric_cols: column names to summarize.

    Returns: {col_name: {(tx,rx): {"mean":..., "std":..., "min":..., "max":..., "n":...}}}
    """
    by_link: Dict[str, Dict[Tuple[str, str], List[float]]] = {
        c: defaultdict(list) for c in numeric_cols
    }
    for rows in rows_per_seed:
        for r in rows:
            tx, rx = r.get("tx_node_id", ""), r.get("rx_node_id", "")
            for c in numeric_cols:
                try:
                    by_link[c][(tx, rx)].append(float(r[c]))
                except (KeyError, ValueError):
                    pass

    out: Dict[str, Dict[Tuple[str, str], Dict[str, float]]] = {}
    for c in numeric_cols:
        out[c] = {}
        for link, vals in by_link[c].items():
            out[c][link] = {
                "mean": _safe_mean(vals),
                "std":  _safe_std(vals),
                "min":  min(vals) if vals else float("nan"),
                "max":  max(vals) if vals else float("nan"),
                "n":    len(vals),
            }
    return out


def _write_summary_csv(path: Path, kpi: str, stats: Dict[str, Dict[Tuple[str, str], Dict[str, float]]]):
    cols = list(stats.keys())
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kpi", "tx_node_id", "rx_node_id", "stat"] + cols)
        # all links
        links = set()
        for c in cols:
            links.update(stats[c].keys())
        for link in sorted(links):
            for stat_name in ("mean", "std", "min", "max", "n"):
                row = [kpi, link[0], link[1], stat_name]
                for c in cols:
                    v = stats[c].get(link, {}).get(stat_name, float("nan"))
                    if stat_name == "n":
                        row.append(int(v) if not math.isnan(v) else 0)
                    else:
                        row.append(f"{v:.6g}")
                w.writerow(row)


def _write_per_seed_csv(path: Path, kpi_files: Dict[str, List[Tuple[int, Path]]]):
    """One row per (seed, tx, rx, kpi_metric)."""
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kpi", "seed", "tx_node_id", "rx_node_id", "value"])
        for kpi, files in kpi_files.items():
            for seed, p in files:
                rows = _read_csv(p)
                # For OTT latency we report p50 per (tx,rx); for others the single value column
                if kpi == "mikami_ott_latency":
                    per_link = defaultdict(list)
                    for r in rows:
                        per_link[(r.get("tx_node_id", ""), r.get("rx_node_id", ""))].append(float(r["latency_ms"]))
                    for (tx, rx), vals in per_link.items():
                        vals.sort()
                        p50 = vals[len(vals) // 2]
                        w.writerow([f"{kpi}_p50", seed, tx, rx, f"{p50:.6g}"])
                else:
                    for r in rows:
                        tx, rx = r.get("tx_node_id", ""), r.get("rx_node_id", "")
                        # pick the headline numeric column
                        for col in ("bler_initial", "sinr_mean_db",
                                    "prr_before_harq", "prr_after_harq"):
                            if col in r:
                                try:
                                    w.writerow([f"{kpi}_{col}", seed, tx, rx,
                                                f"{float(r[col]):.6g}"])
                                except ValueError:
                                    pass
                                break


def _markdown_table(kpi: str, stats: Dict) -> str:
    cols = list(stats.keys())
    lines = [f"### {kpi}", "",
             "| link | " + " | ".join(cols) + " |",
             "|---" + "|---" * len(cols) + "|"]
    links = set()
    for c in cols:
        links.update(stats[c].keys())
    for link in sorted(links):
        cells = [f"TX {link[0]} → RX {link[1]}"]
        for c in cols:
            s = stats[c].get(link, {})
            mu = s.get("mean", float("nan"))
            sd = s.get("std", float("nan"))
            n = s.get("n", 0)
            if math.isnan(mu):
                cells.append("n/a")
            elif c in ("prr_before_harq", "prr_after_harq", "bler_initial"):
                cells.append(f"{mu*100:.2f}% ± {sd*100:.2f}% (n={int(n)})")
            else:
                cells.append(f"{mu:.2f} ± {sd:.2f} (n={int(n)})")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Directory containing seed01/, seed02/, ...")
    ap.add_argument("--out", required=True, help="Output directory for summary CSVs")
    args = ap.parse_args()

    root = Path(args.root)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    seed_dirs = sorted(d for d in root.iterdir() if d.is_dir() and d.name.startswith("seed"))
    if not seed_dirs:
        sys.exit(f"No seed directories under {root}")

    # Discover KPI files from the first seed dir
    kpis_dir = seed_dirs[0] / "kpis"
    if not kpis_dir.is_dir():
        sys.exit(f"No kpis/ under {seed_dirs[0]}")

    kpi_types = sorted(p.stem.replace(f"{seed_dirs[0].name}_", "", 1)
                       for p in kpis_dir.glob("*.csv"))

    # Build per-seed, per-KPI file list
    kpi_files: Dict[str, List[Tuple[int, Path]]] = {k: [] for k in kpi_types}
    for sd in seed_dirs:
        try:
            seed_n = int(sd.name.replace("seed", ""))
        except ValueError:
            continue
        for kpi in kpi_types:
            p = sd / "kpis" / f"{sd.name}_{kpi}.csv"
            if p.exists():
                kpi_files[kpi].append((seed_n, p))

    n_seeds = len({seed for files in kpi_files.values() for seed, _ in files})
    print(f"[agg] Found {len(seed_dirs)} seed dirs, {n_seeds} with KPI CSVs")
    print(f"[agg] KPI types: {', '.join(kpi_types)}")

    # Per-KPI summary CSVs
    summary_md: List[str] = [
        f"# Mikami V2V seed-sweep summary",
        "",
        f"**Seeds:** {n_seeds}  ",
        f"**Topology:** 3 UEs, IVD=47m/94m, 80 km/h, μ=2/60kHz, 20 MHz, 32 B / 10 ms, maxNumTx=3, Mode-2",
        "",
    ]

    # numeric columns to summarize per KPI
    kpi_numeric_cols = {
        "mikami_bler":            ["bler_initial", "bler_all", "n_packets", "n_failed"],
        "mikami_sinr":            ["sinr_mean_db", "sinr_min_db", "sinr_max_db",
                                   "sinr_p10_db", "sinr_p90_db", "n_samples"],
        "mikami_prr_before_harq": ["prr_before_harq", "first_rx_success",
                                   "first_rx_attempts", "tx_total"],
        "mikami_prr_after_harq":  ["prr_after_harq", "rx_received", "tx_total"],
        "mikami_harq_efficiency": ["harq_retx_tx", "harq_retx_rx",
                                   "harq_retx_efficiency",
                                   "first_tx_tx", "first_tx_rx",
                                   "nack_count", "ack_count", "harq_timeout"],
        "mikami_ott_latency":     ["latency_ms"],
        "mikami_pathloss":        ["distance_m", "expected_pl_db"],
    }

    for kpi, files in kpi_files.items():
        if not files:
            continue
        rows_per_seed = [_read_csv(p) for _, p in files]
        cols = kpi_numeric_cols.get(kpi, [])
        # OTT latency is per-packet, not per-link; we summarize per-link p50
        if kpi == "mikami_ott_latency":
            # build per-(tx,rx) per-seed p50
            by_link = defaultdict(list)
            for seed, rows in zip([s for s, _ in files], rows_per_seed):
                per_link_p50 = defaultdict(list)
                for r in rows:
                    try:
                        per_link_p50[(r["tx_node_id"], r["rx_node_id"])].append(float(r["latency_ms"]))
                    except (KeyError, ValueError):
                        continue
                for link, vals in per_link_p50.items():
                    vals.sort()
                    by_link[link].append(vals[len(vals) // 2])
            # write
            with (out_dir / f"summary_{kpi}.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["kpi", "tx_node_id", "rx_node_id", "stat", "latency_ms"])
                for link in sorted(by_link):
                    vals = by_link[link]
                    w.writerow([kpi, link[0], link[1], "mean_p50", f"{_safe_mean(vals):.6g}"])
                    w.writerow([kpi, link[0], link[1], "std_p50",  f"{_safe_std(vals):.6g}"])
                    w.writerow([kpi, link[0], link[1], "min_p50",  f"{min(vals):.6g}"])
                    w.writerow([kpi, link[0], link[1], "max_p50",  f"{max(vals):.6g}"])
                    w.writerow([kpi, link[0], link[1], "n",        len(vals)])
            continue
        stats = _summarize_numeric_columns(rows_per_seed, cols)
        _write_summary_csv(out_dir / f"summary_{kpi}.csv", kpi, stats)
        if kpi in HEADLINE_KPIS:
            summary_md.append(_markdown_table(kpi, stats))

    # Per-seed flat CSV
    _write_per_seed_csv(out_dir / "summary_per_seed.csv", kpi_files)

    # Markdown
    with (out_dir / "summary.md").open("w") as f:
        f.write("\n".join(summary_md) + "\n")

    print(f"[agg] Wrote {out_dir}/summary_*.csv, summary_per_seed.csv, summary.md")


if __name__ == "__main__":
    main()