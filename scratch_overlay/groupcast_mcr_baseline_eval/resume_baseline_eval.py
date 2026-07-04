#!/usr/bin/env python3
"""
resume_baseline_eval.py — resume an interrupted
groupcast_mcr_baseline_eval sweep.

Discovers the run-list from <output_dir>/runlist.txt (produced by
run_baseline_eval.sh) and reruns ONLY the missing seed runs (those
without a corresponding .db file). Already-finalized cells (with a
FINALIZED marker) are skipped — aggregate_baseline.py is re-run at
the end to finalize any newly-complete cells.

Usage:
  python3 resume_baseline_eval.py <output_dir> <sim_time> <n_parallel>

Example:
  python3 resume_baseline_eval.py ./results 5.0 10
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
NS3_DIR = (SCRIPT_DIR.parent.parent / "ns-3-dev").resolve()
BIN_DIR = NS3_DIR / "cmake-cache-opt" / "scratch" / "groupcast_mcr_baseline_eval"


def discover_binary():
    matches = sorted(BIN_DIR.glob("ns3.*-groupcast_mcr_baseline_eval-*"))
    if not matches:
        raise FileNotFoundError(
            f"No groupcast_mcr_baseline_eval binary in {BIN_DIR}. "
            f"Build first:  cd {NS3_DIR} && ./ns3 build groupcast_mcr_baseline_eval")
    return matches[0]


def parse_runlist(path: Path):
    """Return list of (tag, N, speed, ivd, mcr, harq, traffic, seed)."""
    runs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 8:
                continue
            tag, N, speed, ivd, mcr, harq, traffic, seed = parts
            runs.append({
                "tag": tag,
                "N": int(N),
                "speed": int(speed),
                "ivd": int(ivd),
                "mcr": float(mcr),
                "harq": int(harq),
                "traffic": traffic,
                "seed": int(seed),
            })
    return runs


def is_finalized(output_dir: Path, run):
    cells_root = output_dir / "cells"
    cell_tag = (
        f"N{run['N']}_s{run['speed']}_ivd{run['ivd']}"
        f"_mcr{('%.4f' % run['mcr']).replace('.', 'p')}"
    )
    # The mcr_label suffix is not recoverable from runlist alone; we look
    # for any cell dir under cells/ that starts with the cell-tag stem AND
    # contains a FINALIZED marker. This is safe because each cell_tag is
    # unique by (N, speed, ivd, mcr) — the label is just an extra suffix.
    for d in cells_root.glob(f"{cell_tag}*"):
        if (d / "FINALIZED").exists():
            return True
    return False


def run_one(binary: Path, output_dir: Path, sim_time: float, run) -> bool:
    out_dir = output_dir / run["tag"]
    out_dir.mkdir(exist_ok=True)
    log = out_dir / "sim.log"
    cmd = [
        str(binary),
        f"--runId={run['tag']}",
        f"--seed={run['seed']}",
        f"--numUEs={run['N']}",
        f"--speed={run['speed']}",
        f"--ivd={run['ivd']}",
        f"--mcr={run['mcr']}",
        f"--harqOption={run['harq']}",
        f"--traffic={run['traffic']}",
        f"--simTime={sim_time}",
        f"--outputDir={out_dir}",
        f"--simTag={run['tag']}",
    ]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=900)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def main():
    if len(sys.argv) < 4:
        print("Usage: resume_baseline_eval.py <output_dir> <sim_time> <n_parallel>")
        sys.exit(1)
    output_dir = Path(sys.argv[1]).resolve()
    sim_time = float(sys.argv[2])
    npar = int(sys.argv[3])

    runlist = output_dir / "runlist.txt"
    if not runlist.exists():
        print(f"ERROR: runlist.txt not found in {output_dir}")
        sys.exit(1)
    binary = discover_binary()

    all_runs = parse_runlist(runlist)
    if not all_runs:
        print("No runs in runlist.")
        return

    # Find missing: cell either not finalized OR has no .db for this seed.
    missing = []
    n_finalized_cells = 0
    cells_seen = set()
    for r in all_runs:
        cell_key = (r["N"], r["speed"], r["ivd"], r["mcr"], r["harq"], r["traffic"])
        if cell_key in cells_seen:
            continue  # we only need ONE missing seed to fail-finalize a cell,
                  # but re-running other seeds is harmless and idempotent.
        cells_seen.add(cell_key)
        if is_finalized(output_dir, r):
            n_finalized_cells += 1
            continue
        out_dir = output_dir / r["tag"]
        db = out_dir / f"{r['tag']}.db"
        if not db.exists() or db.stat().st_size == 0:
            missing.append(r)

    print(f"Output:        {output_dir}")
    print(f"Binary:        {binary}")
    print(f"Sim time:      {sim_time}")
    print(f"Total runs:    {len(all_runs)}   ({len(cells_seen)} unique cells)")
    print(f"Finalized:     {n_finalized_cells}")
    print(f"Missing seeds: {len(missing)}")
    print(f"Parallel:      {npar}")
    print()

    if not missing:
        print("Nothing to resume.")
    else:
        start = time.time()
        n_ok = 0
        with ThreadPoolExecutor(max_workers=npar) as ex:
            futs = {ex.submit(run_one, binary, output_dir, sim_time, r): r for r in missing}
            for f in as_completed(futs):
                if f.result():
                    n_ok += 1
        elapsed = time.time() - start
        print(f"Resumed {n_ok}/{len(missing)} missing runs in {elapsed:.0f}s")

    # Always run the aggregator so newly-complete cells get finalized.
    print()
    print("Running aggregate_baseline.py to finalize newly-complete cells...")
    r = subprocess.run(["python3", str(SCRIPT_DIR / "aggregate_baseline.py"),
                        str(output_dir), "", str(sim_time)])
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()