#!/usr/bin/env python3
# analyse_relay_storm.py — aggregate relay_storm_eval sweep results
#
# Reads per-run directories containing per-KPI CSVs (the registry-based
# ``kpi_postprocess`` output) and produces:
#   1. sweep_aggregate.csv  — per-(speed,ivd,mcd,relay) aggregate KPIs
#   2. storm_indicator.csv  — retx overhead = relay-run retx / baseline retx
#   3. time_series.csv      — per-window KPI rows (if window_t present)
#
# Usage:
#   python3 analyse_relay_storm.py <results_dir> [output_dir]

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

# Allow importing join_per_run from the common.kpi package.
_KPI_PKG = Path(__file__).resolve().parent.parent / "common" / "kpi"
sys.path.insert(0, str(_KPI_PKG))
from join_per_run import combine_run  # noqa: E402

# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------
if len(sys.argv) < 2:
    print("Usage: analyse_relay_storm.py <results_dir> [output_dir]")
    sys.exit(1)

results_dir = Path(sys.argv[1])
output_dir  = Path(sys.argv[2]) if len(sys.argv) > 2 else results_dir
output_dir.mkdir(parents=True, exist_ok=True)

# -------------------------------------------------------------------------
# Collect per-run directories (each containing per-KPI CSVs + _summary.csv)
# -------------------------------------------------------------------------
run_dirs = sorted([
    p for p in results_dir.rglob("*")
    if p.is_dir() and any(p.glob("*_summary.csv")) and any(p.glob("*_*.csv"))
])
if not run_dirs:
    print(f"No per-run KPI directories found under {results_dir}", file=sys.stderr)
    sys.exit(1)

print(f"Found {len(run_dirs)} per-run directories")

# -------------------------------------------------------------------------
# Load all rows; parse run metadata from the parent directory name.
# Directory naming convention (set by run_relay_storm.sh):
#   baseline_s<speed>_ivd<ivd>_mcd<mcd>_seed<seed>/  → relayEnabled=false
#   relay<N>_s<speed>_ivd<ivd>_mcd<mcd>_seed<seed>/  → relayEnabled=true
# -------------------------------------------------------------------------

def parse_run_meta(runid: str) -> Dict:
    """Extract (speed, ivd, mcd, relay, seed, baseline) from run directory name."""
    meta = {
        "speed": None, "ivd": None, "mcd": None,
        "relay_node": None, "seed": None, "baseline": False
    }
    parts = runid.split("_")
    for p in parts:
        if p.startswith("s") and p[1:].isdigit():
            meta["speed"] = int(p[1:])
        elif p.startswith("ivd"):
            meta["ivd"] = float(p[3:])
        elif p.startswith("mcd"):
            meta["mcd"] = float(p[3:])
        elif p.startswith("seed"):
            meta["seed"] = int(p[4:])
        elif p.startswith("relay") and p[5:].isdigit():
            meta["relay_node"] = int(p[5:])
        elif p == "baseline":
            meta["baseline"] = True
    if meta["relay_node"] is None:
        meta["relay_node"] = 0
    return meta

all_rows: List[Dict] = []

for run_dir in run_dirs:
    # Run metadata is encoded in the directory name.
    run_id = run_dir.name
    meta = parse_run_meta(run_id)
    try:
        df = combine_run(run_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"  skip {run_id}: {e}")
        continue
    for _, src in df.iterrows():
        row = src.to_dict()
        row.update({
            "run_dir":    run_id,
            "speed":      meta["speed"],
            "ivd":        meta["ivd"],
            "mcd":        meta["mcd"],
            "relay_node": meta["relay_node"],
            "seed":       meta["seed"],
            "is_baseline":meta["baseline"],
        })
        all_rows.append(row)

print(f"Loaded {len(all_rows)} rows")

# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------

def safe_float(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None

def safe_mean(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None

def safe_min(vals):
    v = [x for x in vals if x is not None]
    return min(v) if v else None

def safe_max(vals):
    v = [x for x in vals if x is not None]
    return max(v) if v else None

def percentile(vals, p):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    idx = p * (len(v) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(v) - 1)
    return v[lo] + (idx - lo) * (v[hi] - v[lo])

# -------------------------------------------------------------------------
# 1. Aggregate per (speed, ivd, mcd, relay_node) across seeds
# -------------------------------------------------------------------------
GroupKey = tuple  # (speed, ivd, mcd, relay_node, is_baseline)

groups: Dict[GroupKey, List[Dict]] = defaultdict(list)
for row in all_rows:
    # Skip time-series rows (window_t not None).
    if safe_float(row.get("window_t")) is not None:
        continue
    key = (row["speed"], row["ivd"], row["mcd"],
           row["relay_node"], row["is_baseline"])
    groups[key].append(row)

AGG_METRICS = [
    # (column_in_combined_csv, output_short_name)
    # Columns are prefixed by KPI id (e.g. prr_type2_prr_mean) since they come
    # from the registry-based per-KPI outputs joined by join_per_run.combine_run.
    ("prr_type2_prr_mean",     "prr"),
    ("prr_type2_prr_worst",    "prr"),
    ("prr_type2_prr_best",     "prr"),
    ("aoi_aoi_mean_ms",        "aoi_ms"),
    ("aoi_aoi_p95_ms",         "aoi_ms"),
    ("aoi_aoi_max_ms",         "aoi_ms"),
    ("throughput_thput_kbps",  "thput_kbps"),
    ("harq_harq_nack_rate",    "rate"),
    ("ch_occupancy_ch_occupancy", "frac"),
]

agg_rows = []
for key, rows_in_group in sorted(groups.items()):
    speed, ivd, mcd, relay_node, is_baseline = key
    agg = {
        "speed": speed, "ivd": ivd, "mcd": mcd,
        "relay_node": relay_node, "is_baseline": is_baseline,
        "n_rows": len(rows_in_group),
    }
    for col, _ in AGG_METRICS:
        vals = [safe_float(r.get(col)) for r in rows_in_group]
        agg[col + "_agg_mean"] = safe_mean(vals)
        agg[col + "_agg_min"]  = safe_min(vals)
        agg[col + "_agg_max"]  = safe_max(vals)
        agg[col + "_agg_p5"]   = percentile(vals, 0.05)
        agg[col + "_agg_p95"]  = percentile(vals, 0.95)
    agg_rows.append(agg)

agg_path = output_dir / "sweep_aggregate.csv"
if agg_rows:
    fieldnames = list(agg_rows[0].keys())
    with open(agg_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(agg_rows)
    print(f"Wrote {len(agg_rows)} rows → {agg_path}")

# -------------------------------------------------------------------------
# 2. Storm indicator: relay retx / baseline retx per (speed, ivd, mcd)
# -------------------------------------------------------------------------
baseline_retx: Dict[tuple, Optional[float]] = {}
relay_retx:    Dict[tuple, Optional[float]] = {}

for key, rows_in_group in groups.items():
    speed, ivd, mcd, relay_node, is_baseline = key
    subkey = (speed, ivd, mcd)
    vals = [safe_float(r.get("hop1_retx")) for r in rows_in_group]
    mean_val = safe_mean(vals)
    if is_baseline:
        baseline_retx[subkey] = mean_val
    else:
        prev = relay_retx.get(subkey)
        relay_retx[subkey] = (prev or 0) + (mean_val or 0)

storm_rows = []
for subkey in sorted(set(list(baseline_retx.keys()) + list(relay_retx.keys()))):
    speed, ivd, mcd = subkey
    b = baseline_retx.get(subkey)
    r = relay_retx.get(subkey)
    ratio = (r / b) if (r is not None and b is not None and b > 0) else None
    storm_rows.append({
        "speed": speed, "ivd": ivd, "mcd": mcd,
        "baseline_hop1_retx_mean": b,
        "relay_hop1_retx_mean": r,
        "storm_ratio": ratio,  # >1 = storm amplification
    })

storm_path = output_dir / "storm_indicator.csv"
if storm_rows:
    with open(storm_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(storm_rows[0].keys()))
        writer.writeheader()
        writer.writerows(storm_rows)
    print(f"Wrote {len(storm_rows)} rows → {storm_path}")

# -------------------------------------------------------------------------
# 3. Time-series export (rows where window_t is set)
# -------------------------------------------------------------------------
ts_rows = [r for r in all_rows if safe_float(r.get("window_t")) is not None]
ts_path = output_dir / "time_series.csv"
if ts_rows:
    fieldnames = list(ts_rows[0].keys())
    with open(ts_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ts_rows)
    print(f"Wrote {len(ts_rows)} time-series rows → {ts_path}")
else:
    print("No time-series rows found (window_t=None for all rows — normal for aggregate-only runs)")

print("\nDone.")
