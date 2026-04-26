#!/usr/bin/env python3
"""
Run adaptive PHY evaluation sweep.

Orchestrates NS3 simulations sweeping (μ, W, MCS) across scenarios.
"""

import argparse
import gzip
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _gzip_inplace(path: Path) -> None:
    if not path.exists():
        return
    out = path.with_suffix(path.suffix + ".gz")
    with open(path, "rb") as fin, gzip.open(out, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout, length=1 << 20)
    path.unlink()

# Configuration
NS3_DIR = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/ns-3-dev")
NS3_BINARY = NS3_DIR / "build" / "scratch" / "groupcast_eval" / "ns3.42-groupcast_eval-default"
ADAPTIVE_PHY_EVAL_DIR = NS3_DIR / "scratch" / "adaptive-phy-eval"
SUMO_DIR = ADAPTIVE_PHY_EVAL_DIR / "sumo"
RESULTS_DIR = ADAPTIVE_PHY_EVAL_DIR / "results"

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


def build_ns3_command(scenario: str, condition: str, mu: int, w: int, mcs: int,
                    seed: int, output_dir: Path) -> List[str]:
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
    
    return cmd


def run_single(args: Tuple) -> Tuple[bool, str]:
    """Run a single NS3 simulation."""
    scenario, condition, mu, w, mcs, seed, output_dir = args
    
    cmd = build_ns3_command(scenario, condition, mu, w, mcs, seed, output_dir)
    if not cmd:
        return False, "No command"
    
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
            run_id = f"{scenario}_{condition}_mu{mu}_w{w}_mcs{mcs}_seed{seed}"
            _gzip_inplace(output_dir / f"{run_id}.db")
            _gzip_inplace(output_dir / f"{run_id}_packets.csv")
        return ok, result.stderr[-500:] if result.stderr else ""
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


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

    args = parser.parse_args()
    output_dir = Path(args.outDir) if args.outDir else RESULTS_DIR / "raw"
    
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
            cmd = build_ns3_command(scen, cond, mu, w, mcs, seed, output_dir)
            if cmd:
                print(" ".join(cmd[:5]))  # First 5 args
        print(f" ... ({len(all_runs)} total)")
        return
    
    # Create output directory
    (output_dir).mkdir(parents=True, exist_ok=True)
    
    # Run simulations
    if args.parallel > 1:
        print(f"Running {len(all_runs)} simulations with {args.parallel} workers...")
        run_args = [r + (output_dir,) for r in all_runs]
        with mp.Pool(args.parallel) as pool:
            results = pool.map(run_single, run_args)
    else:
        print(f"Running {len(all_runs)} simulations sequentially...")
        results = []
        for run in all_runs:
            result = run_single(run + (output_dir,))
            results.append(result)
    
    # Report results
    success = sum(1 for ok, _ in results if ok)
    print(f"\nCompleted: {success}/{len(all_runs)} successful")
    
    if success < len(all_runs):
        print("\nFailures:")
        for (ok, err), run in zip(results, all_runs):
            if not ok:
                print(f"  {run}: {err[:100]}")


if __name__ == "__main__":
    main()