#!/usr/bin/env python3
"""
aggregate_baseline.py — per-cell finalize-and-prune for
groupcast_mcr_baseline_eval.

For every unique (N, harqOption, traffic, speed, ivd, mcr) cell, this
script:

  1. Discovers all seed runs under <output_dir>/<cellTag>/ .
  2. Runs kpi_postprocess.py on each seed to produce a per-UE KpiRow CSV.
  3. Concatenates the per-seed CSVs into cells/<cellTag>/per_seed.csv.
  4. Computes the cell-level aggregate (mean / p5 / p95 / worst / best)
     over the union of (seed × UE) samples for each KPI:
       - PRR  (higher = better; worst = min)
       - PIR  (lower  = better; worst = max)
       - AoI  (lower  = better; worst = max)
       - collision: hd_count, rc_count, ce_count, total_corrupt (sums)
  5. Writes cells/<cellTag>/cell_aggregate.csv.
  6. PRUNES the raw .db files: keeps seed1.db, deletes the rest, to bound
     disk usage.
  7. Writes cells/<cellKey>/FINALIZED as the resume marker.

Atomicity: aggregates and the FINALIZED marker are written via temp-file
+ rename so a crash mid-finalize either completes fully or leaves the
previous FINALIZED state untouched (idempotent rerun).

Usage:
  python3 aggregate_baseline.py <output_dir> [seeds_str] [sim_time]
  seeds_str : e.g. "1 2 3" (optional; defaults to extracting from dirs)
  sim_time  : float (optional; defaults to 5.0 — used for kpi_postprocess
              warmup/window/step defaults that match the relay_storm
              convention).

The script is also safe to re-run: cells already FINALIZED are skipped.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev

SCRIPT_DIR = Path(__file__).resolve().parent
KPI_SCRIPT = (SCRIPT_DIR.parent /
              "common" / "kpi" / "kpi_postprocess.py").resolve()

# Tag format produced by run_baseline_eval.sh:
#   N{N}_s{spd}_ivd{ivd}_mcr{mcr_str}_{label}_h{harq}_{tc}_seed{seed}
# Examples:
#   N8_s80_ivd25_mcr24p5_admit_1_h2_periodic2_seed1
#   N16_s140_ivd39_mcr738p5_admit_all_h1_aperiodic1_seed3
TAG_RE = re.compile(
    r"^N(?P<N>\d+)"
    r"_s(?P<spd>\d+)"
    r"_ivd(?P<ivd>\d+)"
    r"_mcr(?P<mcr>[0-9p]+)"
    r"_(?P<label>[A-Za-z0-9_]+)"
    r"_h(?P<harq>\d)"
    r"_(?P<traffic>[A-Za-z0-9]+)"
    r"_seed(?P<seed>\d+)$"
)


def parse_tag(tag: str):
    m = TAG_RE.match(tag)
    if not m:
        return None
    d = m.groupdict()
    # mcr is encoded with 'p' replacing '.' (e.g. "24p5" -> 24.5)
    mcr = float(d["mcr"].replace("p", "."))
    return {
        "tag": tag,
        "N": int(d["N"]),
        "speed": int(d["spd"]),
        "ivd": int(d["ivd"]),
        "mcr": mcr,
        "label": d["label"],
        "harq": int(d["harq"]),
        "traffic": d["traffic"],
        "seed": int(d["seed"]),
    }


def cell_key(info: dict) -> tuple:
    """Unique key identifying a (cell, no-seed) tuple."""
    return (info["N"], info["speed"], info["ivd"],
            info["mcr"], info["harq"], info["traffic"])


def parse_summary_csv(path: Path):
    """Read the per-run summary CSV to extract run-level metadata."""
    out = {}
    if not path.exists():
        return out
    try:
        with open(path) as f:
            reader = csv.DictReader(f)
            row = next(reader, None)
            if row:
                for k, v in row.items():
                    out[k] = v
    except Exception:
        pass
    return out


def run_kpi_postprocess(db_path: Path, out_dir: Path,
                        warmup: float, range_m: float,
                        seed_n: int, tag: str,
                        bin_spec: str = "20:500/20") -> bool:
    """Invoke the registry-based KPI postprocessor on one DB.

    Outputs per-KPI CSVs at <out_dir>/<tag>_<kpi_id>.csv. The downstream
    ``join_per_run.py`` merges them with the per-cell summary CSV.
    """
    cmd = [
        "python3", str(KPI_SCRIPT),
        "--db", str(db_path),
        "--out-dir", str(out_dir),
        "--warmup", str(warmup),
        "--range", str(range_m),
        "--bin-spec", bin_spec,
        "--seed", "1",
        "--run", str(seed_n),
        "--tag", tag,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as ex:
        print(f"    KPI postprocess failed for {db_path}: {ex}")
        return False


def _safe_stat(vals):
    def _to_float(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            f = float(v)
            # Filter NaN/Inf — they're not valid statistics.
            if f != f or f in (float("inf"), float("-inf")):
                return None
            return f
        try:
            f = float(v)
            if f != f or f in (float("inf"), float("-inf")):
                return None
            return f
        except (TypeError, ValueError):
            return None
    s = sorted(v for v in map(_to_float, vals) if v is not None)
    n = len(s)
    if n == 0:
        return (None, None, None, None, None)
    return (
        mean(s),                              # mean
        median(s),                            # median
        s[max(0, int(round(n * 0.05)) - 1)],  # p5
        s[min(n - 1, int(round(n * 0.95)))],  # p95
        stdev(s) if n > 1 else 0.0,           # stddev
    )


def aggregate_cell(per_seed_rows):
    """Compute the cell aggregate from per-(seed, UE) rows.

    Input rows are produced by ``join_per_run.py`` which merges the per-KPI
    files emitted by the registry-based ``kpi_postprocess`` with the
    per-cell ``_summary.csv``. Column names are prefixed by KPI id, e.g.
    ``prr_type2_prr_mean``, ``aoi_aoi_mean_ms``,
    ``throughput_thput_kbps``, ``harq_harq_nack_rate``,
    ``ch_occupancy_ch_occupancy``.
    """
    def _f(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    def _vals(rows, key):
        return [_f(r.get(key)) for r in rows if _f(r.get(key)) is not None]
    # Per-link KPIs.
    prr = _vals(per_seed_rows, "prr_type2_prr_mean")
    aoi = _vals(per_seed_rows, "aoi_aoi_mean_ms")
    aoi_p95_list = _vals(per_seed_rows, "aoi_aoi_p95_ms")
    thput = _vals(per_seed_rows, "throughput_thput_kbps")
    # Per-rx KPIs (harq).
    harq_ack = _vals(per_seed_rows, "harq_harq_ack")
    harq_nack = _vals(per_seed_rows, "harq_harq_nack")
    harq_nack_rate = _vals(per_seed_rows, "harq_harq_nack_rate")
    # Per-tx KPIs (ch_occupancy).
    ch_occ = _vals(per_seed_rows, "ch_occupancy_ch_occupancy")
    subch_slots_used = _vals(per_seed_rows, "ch_occupancy_subch_slots_used")
    subch_slots_per_pkt = _vals(per_seed_rows, "ch_occupancy_subch_slots_per_pkt")

    prr_worst_per_seed = defaultdict(list)
    aoi_worst_per_seed = defaultdict(list)
    for r in per_seed_rows:
        # Use prr_mean for worst-case (per-link prr_worst is the min over
        # per-packet PRR, which is 0 for any link that ever had a single
        # dropped packet — too pessimistic for cell-level worst-case).
        v_prr_worst = _f(r.get("prr_type2_prr_mean"))
        if v_prr_worst is not None and v_prr_worst == v_prr_worst:  # filter NaN
            prr_worst_per_seed[r.get("seed")].append(v_prr_worst)
        v_aoi_max = _f(r.get("aoi_aoi_max_ms"))
        if v_aoi_max is not None and v_aoi_max == v_aoi_max:  # filter NaN
            aoi_worst_per_seed[r.get("seed")].append(v_aoi_max)

    prr_mean_m,  prr_med,  prr_p5,  prr_p95,  prr_std  = _safe_stat(prr)
    aoi_mean_m,  aoi_med,  aoi_p5,  aoi_p95,  aoi_std  = _safe_stat(aoi)
    thput_mean, thput_med, thput_p5, thput_p95, thput_std = _safe_stat(thput)
    aoi_p95_mean_m, _, _, _, _ = _safe_stat(aoi_p95_list)
    harq_nack_rate_mean, _, _, _, _ = _safe_stat(harq_nack_rate)

    prr_worst_per_seed_vals = [min(v) for v in prr_worst_per_seed.values() if v]
    aoi_worst_per_seed_vals = [max(v) for v in aoi_worst_per_seed.values() if v]

    def _safe_mean(vals):
        s = [v for v in (_f(v) for v in vals) if v is not None and v == v]
        return mean(s) if s else None

    prr_worst_mean = _safe_mean(prr_worst_per_seed_vals)
    aoi_worst_mean = _safe_mean(aoi_worst_per_seed_vals)
    def _sum_finite(xs):
        s = 0
        any_added = False
        for v in xs:
            f = _f(v)
            if f is not None and f == f:  # filter NaN
                s += f
                any_added = True
        return s if any_added else 0
    harq_ack_total = int(_sum_finite(harq_ack))
    harq_nack_total = int(_sum_finite(harq_nack))

    return {
        "prr_mean":  prr_mean_m,
        "prr_median": prr_med,
        "prr_p5":    prr_p5,
        "prr_p95":   prr_p95,
        "prr_std":   prr_std,
        "prr_worst_mean": prr_worst_mean,
        "aoi_mean_ms": aoi_mean_m,
        "aoi_median_ms": aoi_med,
        "aoi_p5_ms": aoi_p5,
        "aoi_p95_ms": aoi_p95,
        "aoi_p95_mean_ms": aoi_p95_mean_m,
        "aoi_worst_ms_mean": aoi_worst_mean,
        "thput_mean_kbps": thput_mean,
        "thput_p5_kbps": thput_p5,
        "thput_p95_kbps": thput_p95,
        "harq_ack_total": harq_ack_total,
        "harq_nack_total": harq_nack_total,
        "harq_nack_rate_mean": harq_nack_rate_mean,
        "ch_occupancy_mean": _safe_mean(ch_occ),
        "subch_slots_used_total": int(_sum_finite(subch_slots_used)),
        "subch_slots_per_pkt_mean": _safe_mean(subch_slots_per_pkt),
    }


def discover_cells(output_dir: Path):
    """Walk the output dir and group seed runs by cell key."""
    cells = defaultdict(list)  # cell_key -> list[run_dir]
    for entry in sorted(output_dir.iterdir()):
        if not entry.is_dir():
            continue
        info = parse_tag(entry.name)
        if info is None:
            continue
        cells[cell_key(info)].append((entry, info))
    return cells


def finalize_cell(output_dir: Path, key: tuple, runs, warmup, window, step):
    """Run KPI postprocess on every seed, write aggregates, prune .db files,
    write FINALIZED marker. Returns True if finalized (or already done),
    False if the cell is incomplete (not all seeds present)."""
    cells_root = output_dir / "cells"
    cells_root.mkdir(exist_ok=True)
    # Cell tag (sans seed) for directory naming.
    info0 = runs[0][1]
    cell_tag = (
        f"N{info0['N']}_s{info0['speed']}_ivd{info0['ivd']}"
        f"_mcr{('%.4f' % info0['mcr']).replace('.', 'p')}"
        f"_{info0['label']}_h{info0['harq']}_{info0['traffic']}"
    )
    cell_dir = cells_root / cell_tag
    cell_dir.mkdir(parents=True, exist_ok=True)

    finalized_marker = cell_dir / "FINALIZED"
    if finalized_marker.exists():
        return True, "already-finalized"

    # Expected seed count — must match across the cell.
    expected_seeds = sorted({info["seed"] for _, info in runs})
    # A run may have only a `_summary.csv` (failed run) — those don't
    # count as a successful seed for KPI extraction.
    successful_runs = []
    for run_dir, info in runs:
        if info["seed"] not in expected_seeds:
            continue
        db = run_dir / f"{info['tag']}.db"
        if db.exists() and db.stat().st_size > 0:
            successful_runs.append((run_dir, info))

    if not successful_runs:
        return False, "no-successful-runs"

    # 1. KPI postprocess per seed, writing per-KPI CSVs at <tag>_<kpi>.csv.
    #    Then aggregate into a per-UE wide CSV using join_per_run.
    per_seed_rows = []
    for run_dir, info in successful_runs:
        db = run_dir / f"{info['tag']}.db"
        tag = info["tag"]
        # Check if any KPI file is already present for this run.
        existing_kpi = list(run_dir.glob(f"{tag}_*.csv"))
        existing_kpi = [p for p in existing_kpi
                        if not p.name.endswith("_summary.csv")
                        and not p.name.endswith("_kpi.csv")]
        if not existing_kpi:
            ok = run_kpi_postprocess(db, run_dir, warmup, 320.0,
                                     info["seed"], tag)
            if not ok:
                print(f"    WARN: KPI failed for {db.name}")
                continue
        # Read the per-UE rows from the joined CSV (via join_per_run).
        try:
            sys.path.insert(0, str(KPI_SCRIPT.parent))
            from join_per_run import combine_run
            df = combine_run(run_dir)
            for _, row in df.iterrows():
                rdict = row.to_dict()
                rdict["seed"] = info["seed"]
                rdict["tag"] = info["tag"]
                rdict["N"] = info["N"]
                rdict["speed"] = info["speed"]
                rdict["ivd"] = info["ivd"]
                rdict["mcr"] = info["mcr"]
                rdict["mcr_label"] = info["label"]
                rdict["harq"] = info["harq"]
                rdict["traffic"] = info["traffic"]
                per_seed_rows.append(rdict)
        except Exception as ex:
            print(f"    WARN: read failed for {run_dir}: {ex}")
            continue

    if not per_seed_rows:
        return False, "no-kpi-rows"

    # 2. Persist per_seed.csv (per-UE × per-seed).
    per_seed_csv = cell_dir / "per_seed.csv"
    with open(per_seed_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(per_seed_rows[0].keys()))
        w.writeheader()
        w.writerows(per_seed_rows)

    # 3. Cell-level aggregate.
    agg = aggregate_cell(per_seed_rows)
    agg_row = {
        "N": info0["N"],
        "speed": info0["speed"],
        "ivd": info0["ivd"],
        "mcr_m": info0["mcr"],
        "mcr_label": info0["label"],
        "harq": info0["harq"],
        "traffic": info0["traffic"],
        "n_seeds": len({r["seed"] for r in per_seed_rows}),
        "n_ue_rows": len(per_seed_rows),
        **agg,
    }
    cell_agg_csv = cell_dir / "cell_aggregate.csv"
    # Atomic write: write to temp then rename.
    tmp = cell_dir / f".cell_aggregate.{os.getpid()}.tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(agg_row.keys()))
        w.writeheader()
        w.writerow(agg_row)
    tmp.replace(cell_agg_csv)

    # 4. Prune .db files. Keep seed1.db (or the smallest seed if seed1 absent),
    # delete the others to bound disk usage.
    keep_seed = expected_seeds[0]
    for run_dir, info in successful_runs:
        if info["seed"] == keep_seed:
            continue
        db = run_dir / f"{info['tag']}.db"
        if db.exists():
            try:
                db.unlink()
            except OSError as ex:
                print(f"    WARN: could not delete {db}: {ex}")

    # 5. Write the FINALIZED marker (atomic via temp+rename).
    tmp = cell_dir / f".FINALIZED.{os.getpid()}.tmp"
    tmp.write_text(json.dumps({
        "finalized_at": time.time(),
        "cell_key": list(key),
        "n_seeds": len({r["seed"] for r in per_seed_rows}),
        "keep_seed": keep_seed,
    }, indent=2))
    tmp.replace(finalized_marker)

    return True, "finalized"


def main():
    if len(sys.argv) < 2:
        print("Usage: aggregate_baseline.py <output_dir> [seeds_str] [sim_time]")
        sys.exit(1)
    output_dir = Path(sys.argv[1]).resolve()
    seeds_str = sys.argv[2] if len(sys.argv) > 2 else ""
    sim_time = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0

    if not output_dir.exists():
        print(f"ERROR: output_dir does not exist: {output_dir}")
        sys.exit(1)

    # KPI postprocess defaults (mirroring run_relay_storm.sh):
    warmup, window, step = 1.0, 1.0, 0.5

    print(f"=== aggregate_baseline ===")
    print(f"Output dir:  {output_dir}")
    print(f"KPI script:  {KPI_SCRIPT}")
    print(f"Warmup:      {warmup} s   Window: {window} s   Step: {step} s")
    print()

    cells = discover_cells(output_dir)
    if not cells:
        print("No cells found (no per-run directories matched TAG_RE).")
        return

    n_cells = len(cells)
    n_finalized = n_skipped = n_completed = 0
    print(f"Discovered {n_cells} unique cells.")
    for key, runs in sorted(cells.items()):
        info0 = runs[0][1]
        seeds_in_cell = sorted({info["seed"] for _, info in runs})
        # Require every expected seed to be present.
        if seeds_str:
            expected = [int(s) for s in seeds_str.split()]
        else:
            expected = seeds_in_cell
        missing = [s for s in expected if s not in seeds_in_cell]
        if missing:
            print(f"  [{key}] SKIP (missing seeds: {missing})")
            n_skipped += 1
            continue
        ok, why = finalize_cell(output_dir, key, runs, warmup, window, step)
        if ok:
            if why == "already-finalized":
                n_finalized += 1
            else:
                n_completed += 1
        else:
            print(f"  [{key}] SKIP ({why})")
            n_skipped += 1

    print()
    print(f"Cells completed:  {n_completed}")
    print(f"Cells already-finalized: {n_finalized}")
    print(f"Cells skipped:    {n_skipped}")
    print(f"Total unique cells: {n_cells}")

    # Roll-up: stitch every cell_aggregate.csv into a master CSV for the
    # notebook. This is the headline output of the campaign.
    cells_root = output_dir / "cells"
    master_csv = output_dir / "all_cells.csv"
    rows = []
    for cell_dir in sorted(cells_root.iterdir()):
        ca = cell_dir / "cell_aggregate.csv"
        if ca.exists():
            with open(ca) as f:
                rdr = csv.DictReader(f)
                for row in rdr:
                    rows.append(row)
    if rows:
        with open(master_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"Master aggregate: {master_csv} ({len(rows)} rows)")
    else:
        print("No finalized cells yet; master aggregate not written.")


if __name__ == "__main__":
    main()
