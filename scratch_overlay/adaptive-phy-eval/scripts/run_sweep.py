#!/usr/bin/env python3
"""
Run adaptive PHY evaluation sweep.

Orchestrates NS3 simulations sweeping (μ, W, MCS) across scenarios.
"""

import argparse
import csv
import gzip
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Local helpers for inline KPI extraction (--inline-kpi mode).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kpi_compute as _kpi
import per_rx_prr as _prx  # for load_distances() and num_ues_from_cond()


def _gzip_inplace(path: Path) -> None:
    if not path.exists():
        return
    out = path.with_suffix(path.suffix + ".gz")
    with open(path, "rb") as fin, gzip.open(out, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout, length=1 << 20)
    path.unlink()

# Configuration — derived from this file's location so it works on any machine.
# Layout: <repo>/scratch_overlay/adaptive-phy-eval/scripts/run_sweep.py
#         <repo>/ns-3-dev/  (submodule)
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT   = _SCRIPTS_DIR.parents[2]  # scratch_overlay/adaptive-phy-eval/scripts → repo root
NS3_DIR      = _REPO_ROOT / "ns-3-dev"

# Binary name differs by platform; find the first matching executable.
def _find_binary(ns3_dir: Path) -> Path:
    pattern = "ns3.*-groupcast_eval-default"
    import fnmatch, os
    build_scratch = ns3_dir / "build" / "scratch" / "groupcast_eval"
    if build_scratch.is_dir():
        for name in sorted(os.listdir(build_scratch)):
            if fnmatch.fnmatch(name, "ns3*groupcast_eval-optimized"):
                return build_scratch / name
    # fallback: let ns3 wrapper handle it
    return ns3_dir / "ns3"

NS3_BINARY           = _find_binary(NS3_DIR)
ADAPTIVE_PHY_EVAL_DIR = NS3_DIR / "scratch" / "adaptive-phy-eval"
SUMO_DIR             = _SCRIPTS_DIR.parents[1] / "sumo"   # scratch_overlay/adaptive-phy-eval/sumo
RESULTS_DIR          = _SCRIPTS_DIR.parents[1] / "results"

# PHY configuration sweep space
NUMEROLOGIES = [0, 1, 2]
BANDWIDTHS = [25, 50, 75, 100]
MCS_VALUES = [0, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27]

# Scenario definitions
SCENARIOS = {
    "s1": {
        "conditions": ["nrx02", "nrx04", "nrx08", "nrx12", "nrx16", "nrx20"],
        "params": {"numUEs": lambda c: int(c[3:])},  # Parse N from condition
    },
    "s2": {
        "conditions": ["ivd010m", "ivd020m", "ivd050m", "ivd100m", "ivd150m"],
        "params": {"distanceSpread": lambda c: {"ivd010m": 0, "ivd020m": 1, "ivd050m": 2, "ivd100m": 3, "ivd150m": 4}[c]},
    },
    "s3": {
        "conditions": ["spd030kmh", "spd060kmh", "spd100kmh", "spd140kmh", "spd200kmh"],
        "params": {"vMeanMps": lambda c: float(c[3:-4]) / 3.6},  # km/h to m/s
    },
    "s4": {
        "conditions": ["highway_los", "urban_nlos", "mixed"],
        "params": {},
    },
}


def get_sumo_config(scenario: str, condition: str) -> Optional[Path]:
    """Get path to SUMO config for scenario/condition."""
    if scenario == "s1":
        subdir = f"s1_{condition}"
        return SUMO_DIR / "s1_group_size" / subdir / "highway.sumocfg"
    elif scenario == "s2":
        subdir = f"s2_{condition}"
        return SUMO_DIR / "s2_channel_heterogeneity" / subdir / "highway.sumocfg"
    elif scenario == "s3":
        subdir = f"s3_{condition}"
        return SUMO_DIR / "s3_speed_doppler" / subdir / "highway.sumocfg"
    elif scenario == "s4":
        subdir = f"s4_{condition}"
        return SUMO_DIR / "s4_propagation" / subdir / "highway.sumocfg"
    return None


def format_flows_spec(flows) -> Optional[str]:
    """Format a flow list/string into the --flows= CLI value, or None if empty."""
    if not flows:
        return None
    if isinstance(flows, str):
        return flows
    return ",".join(";".join(f"{k}={v}" for k, v in f.items()) for f in flows)


def build_ns3_command(scenario: str, condition: str, mu: int, w: int, mcs: int,
                    seed: int, output_dir: Path,
                    flows=None, max_num_tx: Optional[int] = None,
                    psfch_period: Optional[int] = None) -> List[str]:
    """Build NS3 command line for a single run."""
    
    # Determine numUEs based on scenario
    # Map condition to numUEs: nrx02->2, nrx04->4, etc.
    num_ues = 8  # default
    try:
        # Extract number from condition like "nrx08" -> 8
        num_ues = int(condition[3:])  # nrx08 -> 8
    except:
        pass
    
    # Build command using existing groupcast_eval binary
    # Use correct parameter names matching the existing CLI
    run_id = f"{scenario}_{condition}_mu{mu}_w{w}_mcs{mcs}_seed{seed}"
    cmd = [
        str(NS3_BINARY),
        f"--runId={run_id}",
        f"--seed={seed}",
        f"--numUEs={num_ues}",
        f"--mu={mu}",
        f"--bwPRBs={w}",
        f"--mcs={mcs}",
        f"--simTime=24",
        f"--warmup=3",
        f"--outputDir={output_dir}/",
        f"--outPacketsCsv={output_dir}/{run_id}_packets.csv",
        f"--outRunCsv={output_dir}/{run_id}_run.csv",
    ]
    
    sumo_cfg = get_sumo_config(scenario, condition)
    if sumo_cfg and sumo_cfg.exists():
        cmd.append(f"--sumoCfg={sumo_cfg}")

    flows_spec = format_flows_spec(flows)
    if flows_spec:
        cmd.append(f"--flows={flows_spec}")

    if max_num_tx is not None:
        cmd.append(f"--maxNumTx={max_num_tx}")

    if psfch_period is not None:
        cmd.append(f"--psfchPeriod={psfch_period}")

    return cmd


_DIST_CACHE: Dict[Tuple[str, str], Optional[List[float]]] = {}


def _write_csv(path: Path, rows: List[Dict], fields: List[str]) -> None:
    """Write a list of dicts to a CSV with a stable column order."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def _inline_kpi_then_cleanup(output_dir: Path, run_id: str, scen: str, cond: str,
                             mu: int, w: int, mcs: int, seed: int,
                             sim_time: float, warmup: float,
                             keep_packets: bool) -> Optional[str]:
    """Compute KPIs from the raw .db, write per-run CSVs, delete the .db.

    Returns None on success, or an error string on failure.
    """
    db_path = output_dir / f"{run_id}.db"
    pkt_csv = output_dir / f"{run_id}_packets.csv"
    if not db_path.exists():
        return f"db missing: {db_path.name}"
    n_ues = _prx.num_ues_from_cond(scen, cond)
    key = (scen, cond)
    if key not in _DIST_CACHE:
        sumo_root = Path(__file__).resolve().parent.parent / "sumo"
        try:
            _DIST_CACHE[key] = _prx.load_distances(sumo_root, scen, cond)
        except Exception:
            _DIST_CACHE[key] = None
    distances = _DIST_CACHE[key]
    meta = {"scen": scen, "cond": cond, "mu": mu, "w": w, "mcs": mcs, "seed": seed}
    try:
        run_rows, rx_rows = _kpi.compute_kpis(
            db_path, n_ues, distances, warmup_s=warmup, sim_time_s=sim_time,
            stem=run_id, meta=meta,
        )
        _write_csv(output_dir / f"{run_id}_kpi.csv", run_rows, _kpi.RUN_FIELDS)
        _write_csv(output_dir / f"{run_id}_per_rx_kpi.csv", rx_rows, _kpi.RX_FIELDS)
    except Exception as e:
        return f"kpi failed: {type(e).__name__}: {e}"
    finally:
        try:
            db_path.unlink()
        except FileNotFoundError:
            pass
        if not keep_packets:
            try:
                pkt_csv.unlink()
            except FileNotFoundError:
                pass
    return None


def run_single(args: Tuple) -> Tuple[bool, str, str, float]:
    """Run a single NS3 simulation. Returns (ok, err, run_id, wall_seconds)."""
    (scenario, condition, mu, w, mcs, seed, output_dir, flows, max_num_tx,
     psfch_period, inline_kpi, keep_packets, sim_time, warmup) = args

    run_id = f"{scenario}_{condition}_mu{mu}_w{w}_mcs{mcs}_seed{seed}"
    t0 = time.monotonic()
    cmd = build_ns3_command(scenario, condition, mu, w, mcs, seed, output_dir,
                            flows=flows, max_num_tx=max_num_tx, psfch_period=psfch_period)
    if not cmd:
        return False, "No command", run_id, 0.0

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout (n=20, simTime=24 takes ~250s)
            cwd=str(NS3_DIR),
        )
        ok = result.returncode == 0
        if ok:
            if inline_kpi:
                err = _inline_kpi_then_cleanup(
                    output_dir, run_id, scenario, condition, mu, w, mcs, seed,
                    sim_time=sim_time, warmup=warmup, keep_packets=keep_packets,
                )
                if err:
                    return False, err, run_id, time.monotonic() - t0
            else:
                _gzip_inplace(output_dir / f"{run_id}.db")
                _gzip_inplace(output_dir / f"{run_id}_packets.csv")
        return ok, result.stderr[-500:] if result.stderr else "", run_id, time.monotonic() - t0
    except subprocess.TimeoutExpired:
        return False, "Timeout", run_id, time.monotonic() - t0
    except Exception as e:
        return False, str(e), run_id, time.monotonic() - t0


def _fmt_eta(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:  # NaN guard
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _print_progress(done: int, total: int, ok_count: int, fail_count: int,
                    last_run_id: str, last_ok: bool, last_dt: float,
                    last_err: str,
                    recent: "deque[float]", t_start: float, stream=sys.stdout) -> None:
    """One-line progress update, intended to be called once per completion."""
    pct = 100.0 * done / total if total else 100.0
    elapsed = time.monotonic() - t_start
    # Rate over the last N completions (rolling), falling back to overall rate.
    if len(recent) >= 2:
        rate = len(recent) / max(sum(recent), 1e-9)  # runs per second
    else:
        rate = done / elapsed if elapsed > 0 else 0.0
    remaining = (total - done) / rate if rate > 0 else float("inf")
    status = "OK " if last_ok else "FAIL"
    line = (f"[{done:>5}/{total}] {pct:5.1f}%  ok={ok_count} fail={fail_count}  "
            f"rate={rate:5.2f}/s  ETA={_fmt_eta(remaining)}  "
            f"last({last_dt:5.1f}s {status}): {last_run_id}")
    if not last_ok and last_err:
        # Tack on a single-line summary of the failure for live diagnosis.
        snippet = " ".join(last_err.strip().splitlines())[-180:]
        line += f"  err={snippet}"
    print(line, file=stream, flush=True)


def _concat_kpi_csvs(output_dir: Path) -> None:
    """Concatenate per-run *_kpi.csv and *_per_rx_kpi.csv into master CSVs."""
    for glob_pat, master_name, fields in (
        ("*_kpi.csv",        "_kpi_summary.csv",        _kpi.RUN_FIELDS),
        ("*_per_rx_kpi.csv", "_per_rx_kpi_summary.csv", _kpi.RX_FIELDS),
    ):
        # Skip the master file itself if it matches the glob.
        files = sorted(p for p in output_dir.glob(glob_pat) if p.name != master_name)
        if not files:
            continue
        master = output_dir / master_name
        with open(master, "w", newline="") as fout:
            w = csv.DictWriter(fout, fieldnames=fields)
            w.writeheader()
            for fp in files:
                with open(fp, newline="") as fin:
                    r = csv.DictReader(fin)
                    for row in r:
                        w.writerow({k: row.get(k) for k in fields})
        print(f"  wrote {master} from {len(files)} per-run files")


def expand_sweep(scenario: str, conditions: List[str], 
               mu_values: List[int], w_values: List[int], 
               mcs_values: List[int], seeds: List[int]) -> List[Tuple]:
    """Expand sweep into individual runs."""
    runs = []
    for cond in conditions:
        for mu in mu_values:
            for w in w_values:
                for mcs in mcs_values:
                    for seed in seeds:
                        runs.append((scenario, cond, mu, w, mcs, seed))
    return runs


def main():
    parser = argparse.ArgumentParser(description="Run adaptive PHY sweep")
    parser.add_argument("--scenarios", default="all", help="Comma-separated or 'all'")
    parser.add_argument("--mu", default="all", help="Comma-separated numerologies or 'all'")
    parser.add_argument("--w", default="all", help="Comma-separated bandwidths or 'all'")
    parser.add_argument("--mcs", default="all", help="Comma-separated MCS or 'all'")
    parser.add_argument("--seeds", default="1,2,3,4,5", help="Comma-separated seeds")
    parser.add_argument("--parallel", type=int, default=1, help="Parallel jobs")
    parser.add_argument("--dry-run", action="store_true", help="Print commands only")
    parser.add_argument("--outDir", default=None, help="Output directory (default: adaptive-phy-eval/results/raw)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip runs whose _run.csv already exists in outDir")
    parser.add_argument("--flows", default=None,
                        help="Pre-formatted --flows spec passed verbatim to groupcast_eval; "
                             "empty/unset uses the binary's 3-flow default")
    parser.add_argument("--maxNumTx", type=int, default=None,
                        help="slMaxTxTransNumPssch (1..32). Unset => binary default (5)")
    parser.add_argument("--psfchPeriod", type=int, default=None,
                        help="PSFCH period in slots (0=off, 1/2/4=on). Unset => binary default (0)")
    parser.add_argument("--inline-kpi", action="store_true",
                        help="After each run, extract PRR/PIR/throughput per flow and per receiver "
                             "from the raw .db, then DELETE the .db (and packets.csv unless "
                             "--keep-packets is set). Default: keep gzipped .db (current behaviour).")
    parser.add_argument("--keep-packets", action="store_true",
                        help="With --inline-kpi, retain the gzipped per-run packets.csv. "
                             "Default with --inline-kpi: delete to save disk.")
    parser.add_argument("--sim-time", type=float, default=24.0,
                        help="Per-run simTime (seconds, post-warmup). Used by inline-kpi for "
                             "throughput denominator. Default: 24 (matches build_ns3_command).")
    parser.add_argument("--warmup", type=float, default=3.0,
                        help="Warmup window (seconds) excluded from KPI extraction. Default: 3.")
    parser.add_argument("--no-progress", action="store_true",
                        help="Disable per-run progress lines (for log-friendly output).")
    parser.add_argument("--progress-every", type=int, default=1,
                        help="Print a progress line every N completions (default: 1).")

    args = parser.parse_args()
    # Resolve to absolute: subprocess workers run with cwd=NS3_DIR, so any
    # relative --outDir would otherwise be interpreted against ns-3-dev/ and
    # the simulator's sqlite3_open would fail with "Failed to open DB".
    output_dir = (Path(args.outDir) if args.outDir else RESULTS_DIR / "raw").resolve()
    
    # Expand parameters
    if args.mu == "all":
        mu_values = NUMEROLOGIES
    else:
        mu_values = [int(x) for x in args.mu.split(",")]
    
    if args.w == "all":
        w_values = BANDWIDTHS
    else:
        w_values = [int(x) for x in args.w.split(",")]
    
    if args.mcs == "all":
        mcs_values = MCS_VALUES
    else:
        mcs_values = [int(x) for x in args.mcs.split(",")]
    
    seeds = [int(x) for x in args.seeds.split(",")]
    
    # Determine scenarios
    if args.scenarios == "all":
        scenario_list = list(SCENARIOS.keys())
    else:
        scenario_list = [x.strip() for x in args.scenarios.split(",")]
    
    # Build all runs
    all_runs = []
    for scen in scenario_list:
        if scen not in SCENARIOS:
            print(f"Warning: Unknown scenario {scen}")
            continue
        conditions = SCENARIOS[scen]["conditions"]
        runs = expand_sweep(scen, conditions, mu_values, w_values, mcs_values, seeds)
        all_runs.extend(runs)
    
    print(f"Total runs: {len(all_runs)}")

    if args.skip_existing:
        before = len(all_runs)
        def _done(r):
            scen, cond, mu, w, mcs, seed = r
            run_id = f"{scen}_{cond}_mu{mu}_w{w}_mcs{mcs}_seed{seed}"
            return (output_dir / f"{run_id}_run.csv").exists() if args.outDir or True else False
        all_runs = [r for r in all_runs if not _done(r)]
        print(f"Skipping {before - len(all_runs)} runs already complete; {len(all_runs)} remaining")
    
    if args.dry_run:
        for run in all_runs[:10]:  # Show first 10
            scen, cond, mu, w, mcs, seed = run
            cmd = build_ns3_command(scen, cond, mu, w, mcs, seed, output_dir,
                                    flows=args.flows, max_num_tx=args.maxNumTx,
                                    psfch_period=args.psfchPeriod)
            if cmd:
                print(" ".join(cmd[:5]))  # First 5 args
        print(f" ... ({len(all_runs)} total)")
        return

    # Create output directory
    (output_dir).mkdir(parents=True, exist_ok=True)

    # Common per-run trailing args (output_dir, flows, maxNumTx, psfchPeriod,
    # inline_kpi, keep_packets, sim_time, warmup).
    common = (output_dir, args.flows, args.maxNumTx, args.psfchPeriod,
              args.inline_kpi, args.keep_packets, args.sim_time, args.warmup)

    if args.inline_kpi:
        print("--inline-kpi: per-run KPI extraction enabled "
              f"(packets.csv {'kept' if args.keep_packets else 'deleted'}, .db deleted)")

    # Run simulations with live progress reporting.
    total = len(all_runs)
    results: List[Tuple[bool, str, str, float]] = []
    recent: "deque[float]" = deque(maxlen=20)  # rolling per-run wall-times for rate estimate
    ok_count = 0
    fail_count = 0
    show_progress = (not args.no_progress) and total > 0
    t_start = time.monotonic()
    fail_log = output_dir / "_failures.log"

    def _on_done(res):
        nonlocal ok_count, fail_count
        ok, err, run_id, dt = res
        if ok:
            ok_count += 1
        else:
            fail_count += 1
            # Append full stderr to a per-sweep failure log so we can diagnose live.
            try:
                with open(fail_log, "a") as fp:
                    fp.write(f"=== {run_id} (wall={dt:.2f}s) ===\n")
                    fp.write((err or "<no stderr>") + "\n\n")
            except Exception:
                pass
        recent.append(dt)
        results.append(res)
        done = len(results)
        if show_progress and (done % args.progress_every == 0 or done == total):
            _print_progress(done, total, ok_count, fail_count,
                            run_id, ok, dt, err, recent, t_start)

    if args.parallel > 1:
        print(f"Running {total} simulations with {args.parallel} workers...")
        run_args = [r + common for r in all_runs]
        with mp.Pool(args.parallel) as pool:
            for res in pool.imap_unordered(run_single, run_args):
                _on_done(res)
    else:
        print(f"Running {total} simulations sequentially...")
        for run in all_runs:
            _on_done(run_single(run + common))

    # Report results
    print(f"\nCompleted: {ok_count}/{total} successful in "
          f"{_fmt_eta(time.monotonic() - t_start)}")

    if fail_count:
        print("\nFailures:")
        for ok, err, run_id, _dt in results:
            if not ok:
                print(f"  {run_id}: {err[:100]}")

    # Concatenate per-run KPI CSVs into master files when inline-kpi was used.
    if args.inline_kpi:
        print("\nConcatenating per-run KPI CSVs ...")
        _concat_kpi_csvs(output_dir)


if __name__ == "__main__":
    main()