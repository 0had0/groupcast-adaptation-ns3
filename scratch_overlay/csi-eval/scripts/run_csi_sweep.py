#!/usr/bin/env python3
"""
run_csi_sweep.py
----------------
Parallel sweep driver for csi_groupcast_eval. Each worker calls the binary,
collects the per-run _kpi.csv, and the main process concatenates everything
into _kpi_summary.parquet at sweep end.

No .db files are written — aggregate-only storage strategy.

Usage:
  python3 run_csi_sweep.py \
      --schemes S1,S2,S3,S4 \
      --scenarios highway \
      --numUEs 5,10,20 \
      --ivd 10,20,50,100 \
      --mcr 150 \
      --feedbackK 2,4,8 \
      --mu 0,1 --bwPRBs 25,50 \
      --seeds 1,2,3,4,5 \
      --simTime 20 --warmup 2 \
      --jobs 8 \
      --outDir tmp/csi_sweep
"""

import argparse
import csv
import io
import itertools
import os
import subprocess
import sys
import time
from collections import deque
from multiprocessing.pool import Pool
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---- locate ns3 binary -------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
OVERLAY_DIR = SCRIPT_DIR.parent.parent          # scratch_overlay/
NS3_DIR = OVERLAY_DIR.parent / "ns-3-dev"       # ../ns-3-dev relative to scratch_overlay/
BINARY_CANDIDATES = [
    NS3_DIR / "build/scratch/csi_groupcast_eval"
              / "ns3.42-csi_groupcast_eval-optimized",
    NS3_DIR / "build/scratch/csi_groupcast_eval"
              / "ns3.42-csi_groupcast_eval-debug",
]

def find_binary() -> Path:
    for c in BINARY_CANDIDATES:
        if c.exists():
            return c
    raise FileNotFoundError(
        "csi_groupcast_eval binary not found. Run ./ns3 build first.\n"
        f"Tried: {[str(c) for c in BINARY_CANDIDATES]}"
    )

# ---- scheme -> reporter/adapter names (mirrors scheme.cc) --------------------
SCHEME_DEFAULTS = {
    "S1": {"reporter": "all",    "adapter": "fixed"},
    "S2": {"reporter": "all",    "adapter": "expected_gprr"},
    "S3": {"reporter": "random", "adapter": "min"},
    "S4": {"reporter": "edge",   "adapter": "expected_gprr"},
}

# ---- run a single simulation -------------------------------------------------
def run_single(args_tuple) -> Tuple[bool, str, str, float]:
    (binary, outDir, run_id, sim_args) = args_tuple
    outDir.mkdir(parents=True, exist_ok=True)
    cmd = [str(binary), f"--runId={run_id}", f"--outDir={outDir}"] + sim_args
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        wall = time.monotonic() - t0
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "")[:300].replace("\n", " ")
            return False, err, run_id, wall
        return True, "", run_id, wall
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT", run_id, time.monotonic() - t0
    except Exception as e:
        return False, str(e)[:200], run_id, time.monotonic() - t0

# ---- ETA formatting ----------------------------------------------------------
def _fmt_eta(done: int, total: int, elapsed: float) -> str:
    if done == 0:
        return "ETA=?"
    rate = done / elapsed
    remaining = (total - done) / rate
    h, rem = divmod(int(remaining), 3600)
    m, s = divmod(rem, 60)
    return f"ETA={h:02d}h{m:02d}m{s:02d}s  {rate:.1f}/s"

# ---- aggregate per-run CSVs into one Parquet ---------------------------------
def concat_parquet(outDir: Path, meta_rows: List[Dict], run_ids: List[str],
                   parquet_path: Path) -> None:
    try:
        import pandas as pd
    except ImportError:
        print("[warn] pandas not available — skipping Parquet aggregation.", file=sys.stderr)
        return

    all_per_rx: List[Dict] = []
    all_run: List[Dict] = []
    meta_by_id = {r["stem"]: r for r in meta_rows}

    for run_id in run_ids:
        csv_path = outDir / f"{run_id}_kpi.csv"
        if not csv_path.exists():
            continue
        meta = meta_by_id.get(run_id, {})
        try:
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row.update(meta)
                    if row.get("section") == "per_rx":
                        all_per_rx.append(row)
                    elif row.get("section") == "run":
                        all_run.append(row)
        except Exception as e:
            print(f"[warn] failed to read {csv_path}: {e}", file=sys.stderr)

    if all_per_rx:
        df_rx = pd.DataFrame(all_per_rx)
        path_rx = parquet_path.with_suffix("").with_name(
            parquet_path.stem + "_per_rx.parquet")
        df_rx.to_parquet(path_rx, index=False)
        print(f"  per-rx Parquet: {path_rx}  ({len(df_rx)} rows)")

    if all_run:
        df_run = pd.DataFrame(all_run)
        df_run.to_parquet(parquet_path, index=False)
        print(f"  run Parquet:    {parquet_path}  ({len(df_run)} rows)")


# ---- main -------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schemes",   default="S1,S2,S3,S4")
    ap.add_argument("--scenarios", default="highway")
    ap.add_argument("--numUEs",    default="10")
    ap.add_argument("--ivd",       default="20",   help="inter-vehicle dist (m), comma-sep")
    ap.add_argument("--mcr",       default="150",  help="MCR (m), comma-sep")
    ap.add_argument("--speedMps",  default="27.78",help="speed (m/s), comma-sep")
    ap.add_argument("--feedbackK", default="4",    help="K reporters, comma-sep")
    ap.add_argument("--mu",        default="1",    help="numerology, comma-sep")
    ap.add_argument("--bwPRBs",    default="50",   help="subchannel PRBs, comma-sep")
    ap.add_argument("--seeds",     default="1,2,3,4,5")
    ap.add_argument("--simTime",   default="20.0", type=float)
    ap.add_argument("--warmup",    default="2.0",  type=float)
    ap.add_argument("--maxNumTx",  default="4",    type=int)
    ap.add_argument("--psfchPeriod", default="4",  type=int)
    ap.add_argument("--targetPrr", default="0.99", type=float)
    ap.add_argument("--blerTarget",default="0.10", type=float)
    ap.add_argument("--outDir",    default="tmp/csi_sweep")
    ap.add_argument("--jobs",      default=max(1, os.cpu_count() - 1), type=int)
    ap.add_argument("--progressEvery", default=10, type=int)
    ap.add_argument("--noProgress", action="store_true")
    args = ap.parse_args()

    binary  = find_binary()
    outDir  = Path(args.outDir).resolve()
    outDir.mkdir(parents=True, exist_ok=True)
    failures_log = outDir / "_failures.log"

    def csv_list(s): return [x.strip() for x in s.split(",") if x.strip()]

    schemes   = csv_list(args.schemes)
    scenarios = csv_list(args.scenarios)
    n_ues_list= [int(x) for x in csv_list(args.numUEs)]
    ivds      = [float(x) for x in csv_list(args.ivd)]
    mcrs      = [float(x) for x in csv_list(args.mcr)]
    speeds    = [float(x) for x in csv_list(args.speedMps)]
    ks        = [int(x)   for x in csv_list(args.feedbackK)]
    mus       = [int(x)   for x in csv_list(args.mu)]
    bws       = [int(x)   for x in csv_list(args.bwPRBs)]
    seeds     = [int(x)   for x in csv_list(args.seeds)]

    # Build run list.
    combos = list(itertools.product(
        schemes, scenarios, n_ues_list, ivds, mcrs, speeds, ks, mus, bws, seeds
    ))
    total = len(combos)
    print(f"Total runs: {total}  |  jobs={args.jobs}  |  outDir={outDir}")

    tasks: List[tuple] = []
    meta_rows: List[Dict] = []
    run_ids: List[str] = []

    for scheme, scen, n_ues, ivd, mcr, speed, k, mu, bw, seed in combos:
        defaults = SCHEME_DEFAULTS.get(scheme, {})
        reporter = defaults.get("reporter", "edge")
        adapter  = defaults.get("adapter", "expected_gprr")
        run_id = (f"{scheme}_{scen}_n{n_ues}_ivd{ivd:.0f}_mcr{mcr:.0f}"
                  f"_spd{speed:.1f}_k{k}_mu{mu}_bw{bw}_s{seed}")
        sim_args = [
            f"--scheme={scheme}",
            f"--scenario={scen}",
            f"--numUEs={n_ues}",
            f"--ivd={ivd}",
            f"--mcr={mcr}",
            f"--speedMps={speed}",
            f"--feedbackK={k}",
            f"--mu={mu}",
            f"--bwPRBs={bw}",
            f"--seed={seed}",
            f"--simTime={args.simTime}",
            f"--warmup={args.warmup}",
            f"--maxNumTx={args.maxNumTx}",
            f"--psfchPeriod={args.psfchPeriod}",
            f"--targetPrr={args.targetPrr}",
            f"--blerTarget={args.blerTarget}",
        ]
        tasks.append((binary, outDir, run_id, sim_args))
        run_ids.append(run_id)
        meta_rows.append({
            "stem": run_id, "scheme": scheme, "reporter": reporter,
            "adapter": adapter, "scenario": scen,
            "n_ues": n_ues, "speed_mps": speed, "ivd_m": ivd,
            "mcr_m": mcr, "feedback_k": k, "mu": mu, "bw_prbs": bw, "seed": seed,
        })

    done = 0
    failures = 0
    t_start = time.monotonic()
    recent_walls: deque = deque(maxlen=50)

    def on_done(result):
        nonlocal done, failures
        ok, err, run_id, wall = result
        done += 1
        recent_walls.append(wall)
        if not ok:
            failures += 1
            msg = f"FAIL {run_id}: {err}"
            with open(failures_log, "a") as f:
                f.write(msg + "\n")
            if not args.noProgress:
                print(f"\r[{done}/{total}] FAIL {run_id}: {err[:80]}", flush=True)
        elif not args.noProgress and (done % args.progressEvery == 0 or done == total):
            elapsed = time.monotonic() - t_start
            eta = _fmt_eta(done, total, elapsed)
            avg_w = sum(recent_walls) / len(recent_walls) if recent_walls else 0
            print(f"\r[{done}/{total}] ok={done-failures} fail={failures}"
                  f"  avg={avg_w:.1f}s  {eta}", flush=True)

    with Pool(processes=args.jobs) as pool:
        for result in pool.imap_unordered(run_single, tasks):
            on_done(result)

    print(f"\nDone: {done} runs  ok={done-failures}  fail={failures}"
          f"  elapsed={time.monotonic()-t_start:.0f}s")
    if failures > 0:
        print(f"  Failure log: {failures_log}")

    print("\nAggregating CSVs into Parquet...")
    parquet_path = outDir / "_kpi_summary.parquet"
    concat_parquet(outDir, meta_rows, run_ids, parquet_path)


if __name__ == "__main__":
    main()
