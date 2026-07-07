#!/usr/bin/env python3
"""
analyze_mcs_sweep.py — post-process the mcs_sweep_runner output.

Reads all cell directories under --outputRoot, aggregates the per-PSFCH-opportunity
summary CSV into:
  - per_run.csv: one row per (scenario, channel, traffic, mcs, seed) with mean
    ack_rate / nack_rate / silence_rate / event-count across the 16 HARQ PIDs.
  - per_setting.csv: one row per (scenario, channel, traffic, mcs) with mean and
    std across seeds (n_seeds = number of seeds that ran).
  - best_mcs.csv: per (scenario, channel, traffic), the MCS with the highest mean
    ack_rate, plus runner-up MCS and the delta.
  - heatmap_mcs_<scenario>_<channel>.png: 3×6 panel — rows = traffic class,
    columns = MCS, cell value = mean ack_rate.

Usage:
  python3 analyze_mcs_sweep.py --outputRoot /tmp/mcs_sweep_full \\
                                --outDir   /tmp/mcs_sweep_analysis

Outputs land in --outDir.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Per-cell parser
# ---------------------------------------------------------------------------

def parse_summary(csv_path: Path) -> dict | None:
    """Return {n_ack, n_nack, n_silence, n_events, ack_rate, nack_rate, silence_rate}
    aggregated over all HARQ PIDs for the sole sender."""
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return None
    n_ack = n_nack = n_silence = 0
    rows = 0
    with open(csv_path, newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            try:
                n_ack     += int(row["n_ack"])
                n_nack    += int(row["n_nack"])
                n_silence += int(row["n_silence"])
                rows += 1
            except (KeyError, ValueError):
                continue
    if rows == 0:
        return None
    n = n_ack + n_nack + n_silence
    if n == 0:
        return None
    return {
        "n_ack":         n_ack,
        "n_nack":        n_nack,
        "n_silence":     n_silence,
        "n_events":      n,
        "ack_rate":      n_ack / n,
        "nack_rate":     n_nack / n,
        "silence_rate":  n_silence / n,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate(output_root: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """Read all cells, return (per_run_rows, per_setting_rows, best_mcs_rows)."""
    per_run: list[dict] = []
    # (scenario, channel, traffic, mcs) -> list of (ack_rate, nack_rate, n_events)
    buckets: dict[tuple[str, str, str, int], list[tuple[float, float, int]]] = defaultdict(list)

    failures: list[tuple[Path, str]] = []
    for cell_dir in sorted(output_root.iterdir()):
        if not cell_dir.is_dir():
            continue
        name = cell_dir.name
        # Parse <scenario>__<channel>__<traffic>__mcs<N>__seed<N>
        try:
            sc, ch, tr, mcs_token, seed_token = name.split("__")
            mcs = int(mcs_token[3:])   # strip "mcs"
            seed = int(seed_token[4:])  # strip "seed"
        except ValueError:
            continue

        csv_path = cell_dir / "run_psfch_opps.csv.summary.csv"
        agg = parse_summary(csv_path)
        if agg is None:
            failures.append((cell_dir, "missing/empty summary"))
            continue

        per_run.append({
            "scenario": sc,
            "channel":  ch,
            "traffic":  tr,
            "mcs":      mcs,
            "seed":     seed,
            "ack_rate":     agg["ack_rate"],
            "nack_rate":    agg["nack_rate"],
            "silence_rate": agg["silence_rate"],
            "n_events":     agg["n_events"],
            "n_ack":        agg["n_ack"],
            "n_nack":       agg["n_nack"],
            "n_silence":    agg["n_silence"],
        })
        buckets[(sc, ch, tr, mcs)].append((agg["ack_rate"], agg["nack_rate"], agg["n_events"]))

    per_setting: list[dict] = []
    for (sc, ch, tr, mcs), samples in buckets.items():
        ack_rates = [s[0] for s in samples]
        nack_rates = [s[1] for s in samples]
        per_setting.append({
            "scenario": sc,
            "channel":  ch,
            "traffic":  tr,
            "mcs":      mcs,
            "n_seeds":  len(samples),
            "mean_ack_rate":  sum(ack_rates) / len(ack_rates),
            "std_ack_rate":   _std(ack_rates),
            "mean_nack_rate": sum(nack_rates) / len(nack_rates),
            "min_ack_rate":   min(ack_rates),
            "max_ack_rate":   max(ack_rates),
        })

    # Best-MCS table
    best_mcs: list[dict] = []
    settings_by_combo: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in per_setting:
        settings_by_combo[(row["scenario"], row["channel"], row["traffic"])].append(row)

    for (sc, ch, tr), rows in settings_by_combo.items():
        rows.sort(key=lambda r: -r["mean_ack_rate"])
        best = rows[0]
        runner_up = rows[1] if len(rows) > 1 else None
        best_mcs.append({
            "scenario": sc,
            "channel":  ch,
            "traffic":  tr,
            "best_mcs": best["mcs"],
            "best_mean_ack_rate": best["mean_ack_rate"],
            "best_n_seeds":      best["n_seeds"],
            "runner_up_mcs":     runner_up["mcs"] if runner_up else None,
            "runner_up_mean_ack_rate": runner_up["mean_ack_rate"] if runner_up else None,
            "delta_to_runner_up": (best["mean_ack_rate"] - runner_up["mean_ack_rate"]) if runner_up else None,
        })

    return per_run, per_setting, best_mcs, failures


def _std(xs: list[float]) -> float:
    if len(xs) <= 1:
        return 0.0
    mean = sum(xs) / len(xs)
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))


# ---------------------------------------------------------------------------
# Heatmap plotting (matplotlib optional — skip if not available)
# ---------------------------------------------------------------------------

def plot_heatmaps(per_setting: list[dict], out_dir: Path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[warn] matplotlib not available — skipping heatmap plots", file=sys.stderr)
        return

    settings_by_combo: dict[tuple[str, str, str], dict[int, float]] = defaultdict(dict)
    for row in per_setting:
        settings_by_combo[(row["scenario"], row["channel"])][(row["traffic"], row["mcs"])] = row["mean_ack_rate"]

    traffic_order = ["periodic1", "periodic2", "aperiodic1"]
    mcs_list = sorted({row["mcs"] for row in per_setting})

    for (sc, ch), grid in sorted(settings_by_combo.items()):
        fig, ax = plt.subplots(figsize=(8, 4))
        matrix = []
        for tr in traffic_order:
            row = []
            for m in mcs_list:
                row.append(grid.get((tr, m), float("nan")))
            matrix.append(row)
        im = ax.imshow(matrix, vmin=0.0, vmax=1.0, cmap="RdYlGn", aspect="auto")
        ax.set_xticks(range(len(mcs_list)))
        ax.set_xticklabels([str(m) for m in mcs_list])
        ax.set_yticks(range(len(traffic_order)))
        ax.set_yticklabels(traffic_order)
        ax.set_xlabel("MCS")
        ax.set_ylabel("Traffic class")
        ax.set_title(f"Mean ack_rate — {sc} / {ch}")
        # Annotate cells
        for i in range(len(traffic_order)):
            for j in range(len(mcs_list)):
                v = matrix[i][j]
                if not math.isnan(v):
                    ax.text(j, i, f"{v:.3f}", ha="center", va="center",
                            color="black" if 0.3 < v < 0.85 else "white", fontsize=9)
        fig.colorbar(im, ax=ax, label="mean ack_rate")
        plt.tight_layout()
        out = out_dir / f"heatmap_mcs_{sc}_{ch}.png"
        plt.savefig(out, dpi=120)
        plt.close()
        print(f"  wrote {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outputRoot", required=True, type=Path,
                    help="Root dir produced by mcs_sweep_runner.py")
    ap.add_argument("--outDir", required=True, type=Path,
                    help="Where to write CSVs and PNGs")
    args = ap.parse_args()

    args.outDir.mkdir(parents=True, exist_ok=True)

    per_run, per_setting, best_mcs, failures = aggregate(args.outputRoot)
    print(f"Cells read: {len(per_run)}")
    print(f"Settings (4-tuple buckets): {len(per_setting)}")
    print(f"Failures: {len(failures)}")
    if failures:
        for d, reason in failures[:5]:
            print(f"  {d.name}: {reason}")
        if len(failures) > 5:
            print(f"  ... and {len(failures)-5} more")

    # per_run.csv
    with open(args.outDir / "per_run.csv", "w", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(per_run[0].keys()) if per_run else
                           ["scenario", "channel", "traffic", "mcs", "seed", "ack_rate", "nack_rate", "silence_rate", "n_events"])
        w.writeheader()
        for r in per_run:
            w.writerow(r)
    print(f"  wrote {args.outDir / 'per_run.csv'}")

    # per_setting.csv
    if per_setting:
        with open(args.outDir / "per_setting.csv", "w", newline="") as fp:
            w = csv.DictWriter(fp, fieldnames=list(per_setting[0].keys()))
            w.writeheader()
            for r in per_setting:
                w.writerow(r)
        print(f"  wrote {args.outDir / 'per_setting.csv'}")

    # best_mcs.csv
    if best_mcs:
        with open(args.outDir / "best_mcs.csv", "w", newline="") as fp:
            w = csv.DictWriter(fp, fieldnames=list(best_mcs[0].keys()))
            w.writeheader()
            for r in best_mcs:
                w.writerow(r)
        print(f"  wrote {args.outDir / 'best_mcs.csv'}")

    # heatmaps
    plot_heatmaps(per_setting, args.outDir)

    # JSON summary for quick programmatic access
    summary = {
        "n_cells": len(per_run),
        "n_settings": len(per_setting),
        "n_failures": len(failures),
        "best_mcs": best_mcs,
    }
    with open(args.outDir / "summary.json", "w") as fp:
        json.dump(summary, fp, indent=2)
    print(f"  wrote {args.outDir / 'summary.json'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())