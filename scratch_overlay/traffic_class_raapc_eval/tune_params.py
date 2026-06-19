#!/usr/bin/env python3
"""
tune_params.py — One-At-a-Time parameter sensitivity sweep for all three
adaptation modes (nackRate, raApcA, raApcB) across all five NR-SL groupcast
traffic classes.

Design (OAT):
  For each (mode, swept_param, swept_value, class, seed):
    Fix all other params at the mode's physically-motivated baseline.
    Vary the single swept parameter across its grid.
  This isolates each parameter's marginal effect on PRR / mean_mcs / PIR.

Outputs written to tune_results/:
  tune_raw.csv          — every run result (one row per run)
  tune_summary.csv      — mean±std over seeds, per (mode, param, value, class)
  tune_pivot_<mode>.csv — pivot table: rows=swept_param+value, cols=trafficClass, cell=prr_mean

Usage:
  python3 tune_params.py                              # full sweep (3 seeds × 60 s)
  python3 tune_params.py --quick                      # 1 seed × 20 s (quick sanity)
  python3 tune_params.py --mode raApcA                # single mode only
  python3 tune_params.py --classes periodic2 aperiodic2   # subset of classes
  python3 tune_params.py --seeds 5 --simTime 60       # custom depth
  python3 tune_params.py --jobs 6                     # parallelism
  python3 tune_params.py --resume                     # skip already-completed runs
"""

import argparse
import csv
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import product
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR  = Path(__file__).resolve().parent
REPO_ROOT   = SCRIPT_DIR.parent.parent
TRACE       = REPO_ROOT / "experiment_ra_apc" / "traces" / "intersection_nlosv.mob.csv"
NS3_ROOT    = REPO_ROOT / "ns-3-dev"

# Binary search: debug first, then optimized
BINARY = None
for prof in ("debug", "optimized", "default"):
    cand = NS3_ROOT / "build" / "scratch" / "traffic_class_raapc_eval" / \
           f"ns3.42-traffic_class_raapc_eval-{prof}"
    if cand.is_file():
        BINARY = cand
        break

ALL_CLASSES = ["periodic1", "periodic2", "periodic3", "aperiodic1", "aperiodic2"]

# Representative payload bytes per class (mid-range of the distribution).
# Used by the RA-APC rbLen LUT. For nackRate mode this is ignored.
PAYLOAD_BYTES = {
    "periodic1":  200,
    "periodic2":  1000,
    "periodic3":  45000,
    "aperiodic1": 1000,
    "aperiodic2": 20000,
}

# ---------------------------------------------------------------------------
# Parameter grids — OAT design
# ---------------------------------------------------------------------------
# Each entry: baseline dict + sweep dict {param: [values]}.
# The baseline is the physically-motivated starting point derived from:
#   adaptWinMs  → max(20 × RRI_ms, 500ms)  — ensures >20 SINR/NACK samples
#   adaptDownThr→ 0.10 (tighter than default 0.15 to react earlier)
#   blerSlope   → 3.0  (NR-LDPC waterfall ~1.5 dB wide)
#   adaptMcsMin → 7    (QPSK floor; keeps TBS useful for safety payloads)

GRIDS = {
    # ── NACK-rate ──────────────────────────────────────────────────────────
    # Signal: fraction of PSFCH NACKs in a sliding window.
    # Key params: window length, step-down threshold, step-up threshold, hysteresis.
    "nackRate": {
        "baseline": {
            "adaptWinMs":   500,
            "adaptDownThr": 0.10,
            "adaptUpThr":   0.05,
            "adaptHyst":    2,
            "adaptMcsMin":  0,    # no floor; NACK-rate is self-correcting
            "adaptMcsMax":  28,
            "adaptInitMcs": 14,
            "adaptPerFlow": "true",
        },
        "sweep": {
            # How much history to average NACK rate over.
            # Anchored to: need > 20 feedback events → win > 20 × RRI
            "adaptWinMs":   [100, 200, 500, 1000, 2000],
            # NACK rate above which we step MCS down.
            # Too low → over-reacts to transient errors; too high → slow response.
            "adaptDownThr": [0.05, 0.08, 0.10, 0.15, 0.20],
            # NACK rate below which we step MCS up.
            # Should be < downThr; gap = hysteresis band.
            "adaptUpThr":   [0.01, 0.03, 0.05, 0.08],
            # Consecutive up-evals before committing to a higher MCS.
            # Prevents flickering on short NACK-rate dips.
            "adaptHyst":    [1, 2, 4, 8],
        },
    },

    # ── RA-APC Strategy A (worst-case receiver) ────────────────────────────
    # Signal: per-RX P5 SINR from a sliding window.
    # groupDemand = min_i feas[i] → conservative, protects every receiver.
    # Key params: window (sample count), BLER sigmoid steepness, MCS floor.
    "raApcA": {
        "baseline": {
            "adaptWinMs":   2000,  # > 20 × RRI for all classes
            "blerSlope":    3.0,   # NR-LDPC waterfall ≈ 1.5 dB wide
            "adaptMcsMin":  7,     # QPSK floor (keeps TBS ≥ ~1 kB at 10 PRBs)
            "adaptMcsMax":  28,
            "adaptInitMcs": 14,
            "poolPrbs":     100,
        },
        "sweep": {
            # Window for P5 SINR estimate.
            # Must exceed 20 × RRI_ms (code uses worst-sample below N=20).
            "adaptWinMs":   [500, 1000, 2000, 3000, 4000],
            # Sigmoid steepness (1/dB) around 10%-BLER SINR threshold.
            # Larger = sharper waterfall = more decisive MCS selection.
            # NR-LDPC ≈ 3.0; use 1.0 to reproduce the over-soft default.
            "blerSlope":    [0.5, 1.0, 2.0, 3.0, 5.0],
            # Minimum MCS. Prevents catastrophic over-adaptation when one
            # receiver has a momentarily bad P5 SINR (Strategy A is sensitive
            # to outliers because it uses the worst receiver).
            "adaptMcsMin":  [0, 4, 7, 10, 14],
        },
    },

    # ── RA-APC Strategy B (P5 over per-RX feasible MCS) ───────────────────
    # groupDemand = 5th-percentile of {feas[i]} → tolerates 5 % outage.
    # Less aggressive than A on occluded receivers; same param meanings.
    "raApcB": {
        "baseline": {
            "adaptWinMs":   2000,
            "blerSlope":    3.0,
            "adaptMcsMin":  7,
            "adaptMcsMax":  28,
            "adaptInitMcs": 14,
            "poolPrbs":     100,
        },
        "sweep": {
            "adaptWinMs":   [500, 1000, 2000, 3000, 4000],
            "blerSlope":    [0.5, 1.0, 2.0, 3.0, 5.0],
            "adaptMcsMin":  [0, 4, 7, 10, 14],
        },
    },
}

# ---------------------------------------------------------------------------
# Build run list (OAT expansion)
# ---------------------------------------------------------------------------

def build_runs(modes, classes, n_seeds, sim_time):
    """
    Yield dicts describing a single simulation run.
    OAT: for each (mode, swept_param, swept_value) vary that param while
    all others stay at the baseline.  The baseline itself is also included
    as the (swept_param='baseline', value=baseline_value) row.
    """
    run_id = 0
    for mode in modes:
        grid = GRIDS[mode]
        baseline = grid["baseline"]

        # Include the baseline as a named configuration
        for seed in range(1, n_seeds + 1):
            run_id += 1
            for cls in classes:
                yield dict(
                    run_id     = run_id,
                    mode       = mode,
                    swept_param= "baseline",
                    swept_value= "baseline",
                    trafficClass = cls,
                    seed       = seed,
                    sim_time   = sim_time,
                    params     = dict(baseline),
                )

        # OAT sweeps
        for param, values in grid["sweep"].items():
            for val in values:
                # Skip if value matches baseline (already covered above)
                if str(val) == str(baseline.get(param, "N/A")):
                    continue
                sweep_params = dict(baseline)
                sweep_params[param] = val
                for seed in range(1, n_seeds + 1):
                    run_id += 1
                    for cls in classes:
                        yield dict(
                            run_id      = run_id,
                            mode        = mode,
                            swept_param = param,
                            swept_value = val,
                            trafficClass= cls,
                            seed        = seed,
                            sim_time    = sim_time,
                            params      = sweep_params,
                        )


# ---------------------------------------------------------------------------
# Execute one run
# ---------------------------------------------------------------------------

def run_sim(spec, out_root, resume_set):
    """Run one simulation; return a result dict."""
    mode   = spec["mode"]
    cls    = spec["trafficClass"]
    seed   = spec["seed"]
    sp     = spec["swept_param"]
    sv     = spec["swept_value"]
    params = spec["params"]
    sim_time = spec["sim_time"]

    tag = f"{mode}__{sp}__{sv}__{cls}__s{seed}"
    key = tag  # used for resume

    if key in resume_set:
        return None  # already done

    out_dir = out_root / mode / cls
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / f"{tag}.db"
    log_path = out_dir / f"{tag}.log"

    # Build CLI args
    cmd = [
        str(BINARY),
        f"--trafficClass={cls}",
        f"--mobilityTrace={TRACE}",
        f"--seed={seed}",
        f"--simTime={sim_time}",
        f"--warmup=2",
        "--mcs=14",
        "--bwPRBs=10",
        "--harq=true",
        "--psfchPeriod=4",
        "--maxNumTx=2",
        "--baseScenario=V2V_Highway",
        "--shadowing=true",
        "--numUEs=9",
        f"--adaptiveMcs=true",
        f"--adaptMode={mode}",
        f"--outputDir={out_dir}/",
        f"--simTag={tag}",
        # payload / pool (RA-APC only; ignored by nackRate)
        f"--payloadBytes={PAYLOAD_BYTES.get(cls, 1500)}",
        f"--poolPrbs={params.get('poolPrbs', 100)}",
    ]

    # Append all swept/baseline params
    for k, v in params.items():
        if k == "poolPrbs":
            continue  # already added
        flag = {
            "adaptWinMs":   "adaptWinMs",
            "adaptDownThr": "adaptDownThr",
            "adaptUpThr":   "adaptUpThr",
            "adaptHyst":    "adaptHyst",
            "adaptMcsMin":  "adaptMcsMin",
            "adaptMcsMax":  "adaptMcsMax",
            "adaptInitMcs": "adaptInitMcs",
            "adaptPerFlow": "adaptPerFlow",
            "blerSlope":    "blerSlope",
        }.get(k)
        if flag:
            cmd.append(f"--{flag}={v}")

    t0 = time.time()
    with open(log_path, "w") as lf:
        proc = subprocess.run(cmd, stdout=lf, stderr=lf)
    elapsed = time.time() - t0

    # Extract KPIs from DB
    prr = mean_mcs = pir_p95 = tput_p5 = float("nan")
    nack_rate = retx_ratio = float("nan")
    exit_ok = (proc.returncode == 0)

    if exit_ok and db_path.is_file():
        try:
            con = sqlite3.connect(str(db_path))
            cur = con.cursor()
            # PRR: mean over RX nodes
            row = cur.execute(
                "SELECT AVG(CAST(totalPktRxed AS REAL)/CAST(totalPktTxed AS REAL)) "
                "FROM thput WHERE txRx='rx' AND totalPktTxed > 0"
            ).fetchone()
            if row and row[0] is not None:
                prr = round(row[0], 4)

            # Mean MCS from adaptLog
            row = cur.execute("SELECT AVG(newMcs) FROM adaptLog").fetchone()
            if row and row[0] is not None:
                mean_mcs = round(row[0], 2)

            # NACK rate from slHarqFeedback
            row = cur.execute(
                "SELECT SUM(status='NACK')*1.0 / COUNT(*) FROM slHarqFeedback"
            ).fetchone()
            if row and row[0] is not None:
                nack_rate = round(row[0], 4)

            # Retx ratio
            row = cur.execute(
                "SELECT SUM(ndi=0)*1.0 / COUNT(*) FROM psschTxUeMac"
            ).fetchone()
            if row and row[0] is not None:
                retx_ratio = round(row[0], 4)

            con.close()
        except Exception as e:
            exit_ok = False

    return dict(
        run_key      = key,
        mode         = mode,
        swept_param  = sp,
        swept_value  = str(sv),
        trafficClass = cls,
        seed         = seed,
        exit_ok      = int(exit_ok),
        elapsed_s    = round(elapsed, 1),
        prr          = prr,
        mean_mcs     = mean_mcs,
        nack_rate    = nack_rate,
        retx_ratio   = retx_ratio,
        # echo back all params for traceability
        **{f"p_{k}": str(v) for k, v in params.items()},
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick",   action="store_true",
                    help="1 seed, 20 s sim — fast sanity check")
    ap.add_argument("--mode",    nargs="+", choices=list(GRIDS),
                    default=list(GRIDS),
                    help="Adaptation modes to sweep (default: all)")
    ap.add_argument("--classes", nargs="+", choices=ALL_CLASSES,
                    default=ALL_CLASSES,
                    help="Traffic classes to sweep (default: all)")
    ap.add_argument("--seeds",   type=int, default=3,
                    help="Seeds per configuration (default 3)")
    ap.add_argument("--simTime", type=int, default=60,
                    help="Simulation duration in seconds (default 60)")
    ap.add_argument("--jobs",    type=int, default=5,
                    help="Parallel simulation jobs (default 5)")
    ap.add_argument("--out-dir", type=Path,
                    default=SCRIPT_DIR / "tune_results",
                    help="Output directory for DBs and CSVs")
    ap.add_argument("--resume",  action="store_true",
                    help="Skip runs whose run_key already appears in tune_raw.csv")
    args = ap.parse_args()

    if not BINARY:
        sys.exit("ERROR: binary not found — build traffic_class_raapc_eval first:\n"
                 "  cd ns-3-dev && ./ns3 build traffic_class_raapc_eval")
    if not TRACE.is_file():
        sys.exit(f"ERROR: trace not found: {TRACE}")

    n_seeds  = 1          if args.quick else args.seeds
    sim_time = 20         if args.quick else args.simTime
    jobs     = args.jobs
    out_dir  = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_csv  = out_dir / "tune_raw.csv"
    sum_csv  = out_dir / "tune_summary.csv"

    # ── Resume support ──────────────────────────────────────────────────────
    resume_set = set()
    if args.resume and raw_csv.is_file():
        with open(raw_csv) as f:
            for row in csv.DictReader(f):
                if row.get("exit_ok", "0") == "1":
                    resume_set.add(row["run_key"])
        print(f"[resume] {len(resume_set)} completed runs found in {raw_csv}")

    # ── Build run list ──────────────────────────────────────────────────────
    runs = list(build_runs(args.mode, args.classes, n_seeds, sim_time))
    total = len(runs)
    skipped = sum(1 for r in runs if r["trafficClass"] + "__" + r["mode"] in resume_set
                  or f"{r['mode']}__{r['swept_param']}__{r['swept_value']}__{r['trafficClass']}__s{r['seed']}" in resume_set)
    print(f"Planned: {total} runs  |  skip(resume): {skipped}  |  to run: {total-skipped}")
    print(f"Jobs: {jobs}  |  simTime: {sim_time}s  |  seeds: {n_seeds}")
    print(f"Output: {out_dir}")
    print()

    # ── Open CSV writer ─────────────────────────────────────────────────────
    # Build a superset of all possible fieldnames across all modes so that
    # rows from different modes (nackRate vs raApcA/B) can coexist in one CSV.
    ALL_PARAM_KEYS = set()
    for g in GRIDS.values():
        for k in g["baseline"]:
            ALL_PARAM_KEYS.add(f"p_{k}")
    FIXED_COLS = ["run_key","mode","swept_param","swept_value","trafficClass",
                  "seed","exit_ok","elapsed_s","prr","mean_mcs","nack_rate","retx_ratio"]
    # If CSV already exists with a header, reuse that header order so appended
    # rows align with existing columns; otherwise build from scratch.
    existing_fieldnames = None
    if raw_csv.is_file() and raw_csv.stat().st_size > 0:
        with open(raw_csv) as _f:
            existing_fieldnames = csv.DictReader(_f).fieldnames
    if existing_fieldnames:
        # Extend with any new param keys not already in the header
        extra = [k for k in sorted(ALL_PARAM_KEYS) if k not in existing_fieldnames]
        ALL_FIELDNAMES = list(existing_fieldnames) + extra
    else:
        ALL_FIELDNAMES = FIXED_COLS + sorted(ALL_PARAM_KEYS)

    lock = threading.Lock()
    csv_file = open(raw_csv, "a", newline="")
    writer_ref = [None]

    completed = 0
    failed    = 0
    t_start   = time.time()

    def write_row(result):
        with lock:
            if writer_ref[0] is None:
                writer_ref[0] = csv.DictWriter(
                    csv_file, fieldnames=ALL_FIELDNAMES, extrasaction="ignore")
                if raw_csv.stat().st_size == 0:
                    writer_ref[0].writeheader()
            try:
                writer_ref[0].writerow(result)
                csv_file.flush()
            except Exception as exc:
                print(f"  [WARN] CSV write failed for {result.get('run_key','?')}: {exc}",
                      flush=True)

    # ── Execute ─────────────────────────────────────────────────────────────
    sims_dir = out_dir / "sims"
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(run_sim, r, sims_dir, resume_set): r for r in runs}
        for fut in as_completed(futures):
            try:
                result = fut.result()
            except Exception as exc:
                print(f"  [ERROR] future raised: {exc}", flush=True)
                continue
            if result is None:
                skipped += 1
                continue
            completed += 1
            if not result["exit_ok"]:
                failed += 1
            write_row(result)
            elapsed = time.time() - t_start
            rate = completed / elapsed if elapsed > 0 else 0
            eta  = (total - completed - skipped) / rate if rate > 0 else 0
            prr_s = f"{result['prr']:.3f}" if result['prr'] == result['prr'] else "nan"
            mcs_s = f"{result['mean_mcs']:.1f}" if result['mean_mcs'] == result['mean_mcs'] else "nan"
            print(f"  [{completed}/{total-skipped}] {result['mode']:10s} "
                  f"{result['swept_param']:14s}={str(result['swept_value']):6s} "
                  f"{result['trafficClass']:12s} s{result['seed']} "
                  f"PRR={prr_s} mcs={mcs_s} "
                  f"[ETA {eta/60:.1f} min]", flush=True)

    csv_file.close()
    print(f"\nRaw results → {raw_csv}  ({completed} runs, {failed} failed)")

    # ── Aggregate: mean±std over seeds ──────────────────────────────────────
    import math
    rows_by_key = {}
    with open(raw_csv) as f:
        for row in csv.DictReader(f):
            if row.get("exit_ok", "0") != "1":
                continue
            key = (row["mode"], row["swept_param"], row["swept_value"], row["trafficClass"])
            rows_by_key.setdefault(key, []).append(row)

    summary_rows = []
    for (mode, sp, sv, cls), rlist in sorted(rows_by_key.items()):
        def mean_std(field):
            vals = [float(r[field]) for r in rlist
                    if r.get(field, "") not in ("nan", "", "ERR")]
            if not vals:
                return float("nan"), float("nan")
            m = sum(vals) / len(vals)
            s = math.sqrt(sum((v - m)**2 for v in vals) / len(vals)) if len(vals) > 1 else 0.0
            return round(m, 4), round(s, 4)

        prr_m, prr_s     = mean_std("prr")
        mcs_m, mcs_s     = mean_std("mean_mcs")
        nack_m, _        = mean_std("nack_rate")
        retx_m, _        = mean_std("retx_ratio")
        summary_rows.append(dict(
            mode=mode, swept_param=sp, swept_value=sv, trafficClass=cls,
            n_seeds=len(rlist),
            prr_mean=prr_m, prr_std=prr_s,
            mean_mcs_mean=mcs_m, mean_mcs_std=mcs_s,
            nack_rate_mean=nack_m, retx_ratio_mean=retx_m,
        ))

    with open(sum_csv, "w", newline="") as f:
        if summary_rows:
            w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            w.writeheader()
            w.writerows(summary_rows)
    print(f"Summary   → {sum_csv}  ({len(summary_rows)} rows)")

    # ── Pivot tables per mode ───────────────────────────────────────────────
    classes_present = sorted({r["trafficClass"] for r in summary_rows})
    for mode in args.mode:
        mode_rows = [r for r in summary_rows if r["mode"] == mode]
        if not mode_rows:
            continue
        pivot_path = out_dir / f"tune_pivot_{mode}.csv"
        params_seen = [(r["swept_param"], r["swept_value"]) for r in mode_rows]
        seen_set = []
        for p in params_seen:
            if p not in seen_set:
                seen_set.append(p)
        with open(pivot_path, "w", newline="") as f:
            w = csv.writer(f)
            header = ["swept_param", "swept_value"] + classes_present
            w.writerow(header)
            for (sp, sv) in seen_set:
                row_data = [sp, sv]
                for cls in classes_present:
                    match = [r for r in mode_rows
                             if r["swept_param"]==sp and r["swept_value"]==sv
                             and r["trafficClass"]==cls]
                    row_data.append(f"{match[0]['prr_mean']:.3f}" if match else "")
                w.writerow(row_data)
        print(f"Pivot     → {pivot_path}")

    # ── Print console summary ────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("PRR SUMMARY — mean over seeds, grouped by (mode, swept_param, value)")
    print("=" * 80)
    for mode in args.mode:
        print(f"\n{'─'*70}")
        print(f"  MODE: {mode}")
        print(f"{'─'*70}")
        mode_rows = [r for r in summary_rows if r["mode"] == mode]
        # group by swept_param
        params_in_mode = []
        for r in mode_rows:
            if r["swept_param"] not in params_in_mode:
                params_in_mode.append(r["swept_param"])
        for sp in params_in_mode:
            print(f"\n  [{sp}]")
            print(f"  {'value':>10s}  " + "  ".join(f"{c:>12s}" for c in classes_present))
            sp_rows = [r for r in mode_rows if r["swept_param"] == sp]
            vals_seen = []
            for r in sp_rows:
                if r["swept_value"] not in vals_seen:
                    vals_seen.append(r["swept_value"])
            for sv in vals_seen:
                row_str = f"  {sv:>10s}  "
                for cls in classes_present:
                    match = [r for r in sp_rows
                             if r["swept_value"]==sv and r["trafficClass"]==cls]
                    if match:
                        row_str += f"  {match[0]['prr_mean']:>10.3f}  "
                    else:
                        row_str += f"  {'—':>10s}  "
                print(row_str)

    print()
    total_time = time.time() - t_start
    print(f"Total wall time: {total_time/60:.1f} min")


if __name__ == "__main__":
    main()
