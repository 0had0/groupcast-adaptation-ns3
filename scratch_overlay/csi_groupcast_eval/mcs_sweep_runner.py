#!/usr/bin/env python3
"""
mcs_sweep_runner.py — orchestrate the NR-V2X MCS sweep using traffic_class_eval.

Sweep dimensions (720 cells):
  - 2 SUMO scenarios (ivd_sweep, merge_incremental)
  - 2 channel conditions (V2V_Highway, V2V_Urban)
  - 3 traffic classes (periodic1, periodic2, aperiodic1)
  - 6 MCS values (7, 10, 12, 14, 20, 28)
  - 10 seeds

Total = 2 × 2 × 3 × 6 × 10 = 720 runs.

Why traffic_class_eval (not csi_groupcast_eval)?
================================================
csi_groupcast_eval --mobilityTrace stalls the simulator at ~16 s of sim time
regardless of --simTime. traffic_class_eval runs the full trace (335 s for
ivd_sweep, 537 s for merge_incremental) cleanly when the HARQ-buffer-mismatch
bug is sidestepped by overriding --rri for periodic2 (see below).

Per-class configuration:
  - periodic1   → default RRI=50 ms  (works full-trace)
  - periodic2   → --rri=50          (default 10 ms trips an NR-SL UE-MAC
                                     HARQ buffer mismatch at ~35 s; slower
                                     cadence lets the buffers drain)
                  Packet-size behavior is unchanged (Bernoulli LARGE/SMALL
                  is independent of RRI), so the class identity is preserved.
  - aperiodic1  → default exponential inter-arrival (works full-trace)

CLI:
  --ns3-bin    <abs path to ns3.42-traffic_class_eval-default>
  --outputRoot <dir under which each cell gets its own sub-dir>
  [--seeds 10]            number of seeds per cell (1..N)
  [--warmup 2.0]          warmup window excluded by V2xKpi
  [--cores 4]             ProcessPoolExecutor workers (RAM budget ~6 GB/core)
  [--mcs "7,10,12,14,20,28"]
  [--channels "V2V_Highway,V2V_Urban"]
  [--traffic "periodic1,periodic2,aperiodic1"]
  [--scenarios "ivd_sweep,merge_incremental"]

Per-run layout:
  outputRoot/<scenario>__<channel>__<traffic>__mcs<MCS>__seed<SEED>/run.db
  outputRoot/<scenario>__<channel>__<traffic>__mcs<MCS>__seed<SEED>/run.log
  outputRoot/<scenario>__<channel>__<traffic>__mcs<MCS>__seed<SEED>/run_psfch_opps.csv
  outputRoot/<scenario>__<channel>__<traffic>__mcs<MCS>__seed<SEED>/run_psfch_opps.csv.summary.csv

Hard guards:
  - Refuse to start if fewer than 4 GB total RAM (safety against OOM).
  - Per-run timeout: 30 min.
  - Print a banner with the full sweep matrix before launching.

Progress is printed every 30 s; failed cells are listed at the end.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Sweep matrix
# ---------------------------------------------------------------------------

SCENARIOS = {
    "ivd_sweep": {
        "trace":        "sumo_scenarios/platoon_ivd_sweep/platoon_ivd_sweep.mob.csv",
        "numUEs":       6,            # 8 UEs triggers a pre-existing NR PHY issue;
                                      # 6 UEs works for this trace (verified).
        "simTime":      335.0,        # full SUMO trace duration
        "baseScenario": "V2V_Highway",
    },
    "merge_incremental": {
        "trace":        "sumo_scenarios/platoon_merge_incremental/platoon_merge_incremental.mob.csv",
        "numUEs":       6,            # 23 UEs triggers the bug; leader + first 5 followers
                                      # cover the early-merge phase that we care about.
        "simTime":      537.0,        # full SUMO trace duration
        "baseScenario": "V2V_Urban",
    },
}

# Per-class NR-V2X configuration knobs.
#   rri_ms: 0 = use the TrafficClass default RRI (set in DefaultRriForTrafficClass).
#   Note: periodic2's default RRI of 10 ms trips an NR-SL UE-MAC HARQ-buffer-mismatch
#   abort at ~35 s. We override to 50 ms (matches periodic1's cadence) so the
#   buffer drains cleanly. Packet-size behavior is independent of RRI — periodic2's
#   Bernoulli LARGE/SMALL packet mix is preserved.
TRAFFIC_CFG = {
    "periodic1":  {"rri_ms": 0},   # default 50 ms — works full-trace
    "periodic2":  {"rri_ms": 50},  # override: sidesteps HARQ buffer bug
    "aperiodic1": {"rri_ms": 0},   # exponential inter-arrival — works full-trace
}

CHANNELS  = ["V2V_Highway", "V2V_Urban"]
MCS_LIST  = [7, 10, 12, 14, 20, 28]


# ---------------------------------------------------------------------------
# Per-cell invoker
# ---------------------------------------------------------------------------

def run_one(ns3_bin: str, output_root: Path,
            scenario: str, channel: str, traffic: str, mcs: int, seed: int,
            warmup: float, timeout_s: int = 1800) -> tuple[bool, str]:
    """Run one traffic_class_eval invocation. Returns (ok, message)."""
    sc = SCENARIOS[scenario]
    trace = sc["trace"]
    num_ues = sc["numUEs"]
    sim_time = sc["simTime"]
    # Per-cell channel override: user can sweep channels; scenario provides the default.
    base_scenario = channel  # honor --channels parameter

    cell_dir = output_root / f"{scenario}__{channel}__{traffic}__mcs{mcs}__seed{seed}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    log_path = cell_dir / "run.log"

    rri_ms = TRAFFIC_CFG[traffic]["rri_ms"]

    cmd = [
        str(ns3_bin),
        f"--mobilityTrace={trace}",
        f"--numUEs={num_ues}",
        "--mu=1",          # 30 kHz SCS
        "--bwPRBs=50",     # 50 PRB subchannels — sufficient for the default traffic rate
        f"--baseScenario={base_scenario}",
        f"--mcs={mcs}",
        f"--simTime={sim_time}",
        f"--warmup={warmup}",
        "--psfchPeriod=4",
        "--harq=true",
        "--txNodeId=0",
        f"--trafficClass={traffic}",
    ]
    if rri_ms > 0:
        cmd.append(f"--rri={rri_ms}")
    cmd += [
        "--psfchKpi=true",
        "--psfchKpiSummaryOnly=false",
        f"--seed={seed}",
        f"--outputDir={cell_dir}/",
        "--simTag=run",
    ]

    with open(log_path, "w") as fp:
        fp.write("$ " + " ".join(shlex.quote(c) for c in cmd) + "\n")
        fp.flush()
        try:
            r = subprocess.run(cmd, cwd=os.getcwd(),
                               stdout=fp, stderr=subprocess.STDOUT,
                               timeout=timeout_s)
            if r.returncode != 0:
                return False, f"exit {r.returncode} (see {log_path})"
        except subprocess.TimeoutExpired:
            return False, f"timeout after {timeout_s}s"

    # Sanity: DB and PSFCH summary should exist.
    db = cell_dir / "run.db"
    sum_csv = cell_dir / "run_psfch_opps.csv.summary.csv"
    if not db.exists() or db.stat().st_size < 1024:
        return False, f"missing/empty DB ({db})"
    if not sum_csv.exists():
        return False, f"missing PSFCH summary ({sum_csv})"
    return True, "ok"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ns3-bin", required=True, type=Path)
    parser.add_argument("--outputRoot", required=True, type=Path)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--warmup", type=float, default=2.0)
    parser.add_argument("--cores", type=int, default=4)
    parser.add_argument("--mcs", default=",".join(str(m) for m in MCS_LIST))
    parser.add_argument("--channels", default=",".join(CHANNELS))
    parser.add_argument("--traffic", default=",".join(TRAFFIC_CFG.keys()))
    parser.add_argument("--scenarios", default=",".join(SCENARIOS.keys()))
    parser.add_argument("--dryRun", action="store_true")
    args = parser.parse_args()

    rc = _run_sweep(args)
    return rc


def _task(c: tuple[str, str, str, int, int],
          ns3_bin: str, output_root: str, warmup: float,
          timeout_s: int = 1800) -> tuple[tuple[str, str, str, int, int], bool, str]:
    """Module-level wrapper so ProcessPoolExecutor can pickle it."""
    ok, msg = run_one(ns3_bin, Path(output_root),
                      c[0], c[1], c[2], c[3], c[4], warmup, timeout_s=timeout_s)
    return c, ok, msg


def _run_sweep(args: argparse.Namespace) -> int:
    scenarios  = args.scenarios.split(",")
    channels   = args.channels.split(",")
    traffic    = args.traffic.split(",")
    mcs_list   = [int(m) for m in args.mcs.split(",")]
    seeds      = list(range(1, args.seeds + 1))

    # Sanity
    for s in scenarios:
        if s not in SCENARIOS:
            print(f"ERROR: unknown scenario {s!r}; valid: {list(SCENARIOS.keys())}", file=sys.stderr)
            return 2
    for t in traffic:
        if t not in TRAFFIC_CFG:
            print(f"ERROR: unknown traffic {t!r}; valid: {list(TRAFFIC_CFG.keys())}", file=sys.stderr)
            return 2
    for s in scenarios:
        if not Path(SCENARIOS[s]["trace"]).exists():
            print(f"ERROR: trace file missing for {s!r}: {SCENARIOS[s]['trace']}", file=sys.stderr)
            return 2

    # Build the cell list
    cells: list[tuple[str, str, str, int, int]] = []
    for sc in scenarios:
        for ch in channels:
            for tr in traffic:
                for mcs in mcs_list:
                    for seed in seeds:
                        cells.append((sc, ch, tr, mcs, seed))

    total = len(cells)
    print(f"Sweep matrix: {total} cells")
    print(f"  scenarios={scenarios}")
    for s in scenarios:
        sc = SCENARIOS[s]
        print(f"    {s}: trace={sc['trace']} simTime={sc['simTime']} numUEs={sc['numUEs']}")
    print(f"  channels={channels}")
    print(f"  traffic={traffic}")
    for t in traffic:
        cfg = TRAFFIC_CFG[t]
        rri = cfg['rri_ms'] if cfg['rri_ms'] > 0 else 'default'
        print(f"    {t}: rri_ms={rri}")
    print(f"  mcs={mcs_list}")
    print(f"  seeds={seeds[0]}..{seeds[-1]} ({len(seeds)})")
    print(f"  warmup={args.warmup}")
    print(f"  cores={args.cores}, outputRoot={args.outputRoot}")

    if args.dryRun:
        print(f"\n[DRY RUN] would execute {total} ns-3 invocations")
        return 0

    # RAM check
    try:
        sys_pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_mem_bytes = sys_pages * page_size
        avail = shutil.disk_usage("/").free
        print(f"  system memory: {total_mem_bytes / 2**30:.1f} GiB total, "
              f"{avail / 2**30:.1f} GiB free disk")
        if total_mem_bytes < 4 * 2**30:
            print(f"ERROR: <4 GiB total RAM — refusing to start", file=sys.stderr)
            return 3
    except (ValueError, OSError, AttributeError):
        pass

    args.outputRoot.mkdir(parents=True, exist_ok=True)
    (args.outputRoot / "sweep.log").write_text(
        f"# mcs_sweep_runner started at {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# cells: {total}\n"
    )

    # Ensure clean SIGINT propagation: cancel all workers on Ctrl+C.
    def sigint_handler(sig, frame):
        print("\nINTERRUPTED — shutting down workers", file=sys.stderr)
        sys.exit(130)
    signal.signal(signal.SIGINT, sigint_handler)

    # Submit all jobs.
    t0 = time.time()
    done = 0
    failed: list[tuple[str, str, str, int, int, str]] = []

    print(f"\nLaunching {total} runs with {args.cores} workers…\n")
    ns3_bin_s = str(args.ns3_bin)
    output_root_s = str(args.outputRoot)
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.cores) as ex:
        futures = {
            ex.submit(_task, c, ns3_bin_s, output_root_s,
                      args.warmup): c
            for c in cells
        }
        last_print = 0.0
        for fut in concurrent.futures.as_completed(futures):
            c, ok, msg = fut.result()
            done += 1
            if not ok:
                failed.append((*c, msg))
                print(f"  [{done:3d}/{total}] FAIL {c}  {msg}", file=sys.stderr)
            now = time.time()
            if now - last_print >= 30.0 or done == total:
                elapsed = now - t0
                rate = done / elapsed if elapsed > 0 else 0.0
                eta_s = (total - done) / rate if rate > 0 else 0.0
                print(f"  [{done:3d}/{total}]  elapsed={elapsed/60:.1f}min  "
                      f"rate={rate*60:.1f} runs/min  eta={eta_s/60:.1f}min  "
                      f"failures={len(failed)}", file=sys.stderr)
                last_print = now

    elapsed = time.time() - t0
    print(f"\nSweep complete in {elapsed/60:.1f} min. {len(failed)} failures.")

    # Write summary CSV
    summary = args.outputRoot / "sweep_status.csv"
    with open(summary, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["scenario", "channel", "traffic", "mcs", "seed", "ok", "message"])
        for sc, ch, tr, mcs, seed in cells:
            ok = True
            msg = "ok"
            for f_sc, f_ch, f_tr, f_mcs, f_seed, f_msg in failed:
                if (sc, ch, tr, mcs, seed) == (f_sc, f_ch, f_tr, f_mcs, f_seed):
                    ok = False
                    msg = f_msg
                    break
            w.writerow([sc, ch, tr, mcs, seed, int(ok), msg])
    print(f"Status CSV: {summary}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())