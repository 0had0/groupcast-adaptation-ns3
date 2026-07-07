#!/usr/bin/env python3
"""
mcs_sweep_runner.py — orchestrate the csi_groupcast_eval MCS sweep.

Sweep dimensions (720 cells):
  - 2 SUMO scenarios (ivd_sweep, merge_incremental)
  - 2 channel conditions (V2V_Highway, V2V_Urban)
  - 3 CustomTrafficApplication modes (periodic1, periodic2, aperiodic1)
  - 6 MCS values (7, 10, 12, 14, 20, 28)
  - 10 seeds

Total = 2 × 2 × 3 × 6 × 10 = 720 runs.

CLI:
  --ns3-bin <abs path to ns3.42-csi_groupcast_eval-default>
  --outputRoot <dir under which each cell gets its own sub-dir>
  [--seeds 10]           number of seeds per cell
  [--simTime 15.0]       capped to avoid the pre-existing 16-s early-stop
                         observed in csi_groupcast_eval --mobilityTrace mode
                         (sims run to simStop but app-generated events stop
                         at ~16 s regardless of --simTime)
  [--warmup 2.0]
  [--cores 4]            ProcessPoolExecutor workers (RAM budget ~6 GB/core)
  [--mcs "7,10,12,14,20,28"]
  [--channels "V2V_Highway,V2V_Urban"]
  [--traffic "periodic1,periodic2,aperiodic1"]
  [--scenarios "ivd_sweep,merge_incremental"]

Per-run layout:
  outputRoot/<scenario>__<channel>__<traffic>__mcs<MCS>__seed<SEED>/run.db
  outputRoot/<scenario>__<channel>__<traffic>__mcs<MCS>__seed<SEED>/run.log
  outputRoot/<scenario>__<channel>__<traffic>__mcs<MCS>__seed<SEED>/run_psfch_opps.csv*

Hard guards:
  - Refuse to start if fewer than 4 GB free RAM (safety against OOM).
  - Per-run timeout: 30 min.
  - Print a banner with the full sweep matrix before launching.

Progress is printed every 30 s; the script aborts on first non-zero exit
and prints the failing (scenario, channel, traffic, mcs, seed) cell.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import os
import resource
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
        "trace": "sumo_scenarios/platoon_ivd_sweep/platoon_ivd_sweep.mob.csv",
        "numUEs": 6,            # 8 UEs triggers a pre-existing NR PHY issue;
                                # 6 UEs works for this trace (verified).
    },
    "merge_incremental": {
        "trace": "sumo_scenarios/platoon_merge_incremental/platoon_merge_incremental.mob.csv",
        "numUEs": 6,            # Same constraint — 23 UEs triggers the bug; we use the
                                # leader + first 5 platoon members (most interesting for
                                # the early-merge phase).
    },
}

CHANNELS  = ["V2V_Highway", "V2V_Urban"]
TRAFFIC   = ["periodic1", "periodic2", "aperiodic1"]
MCS_LIST  = [7, 10, 12, 14, 20, 28]

# Per-traffic --flows template. CustomTrafficApplication drives the RRI/size
# internally; we just name the mode via --flows="traffic=<mode>;rate_kbps=200;rri_ms=20".
# node 0 is the TX UE; all other UEs are RX. dstL2Id=254 matches the rest of the
# campaigns. txNodes=0, rxNodes=1..N-1.
# groupcast_flows.cc parses `rxNodes` with ':' as top-level separator and '-' for ranges.
def make_flows(numUEs: int, traffic: str) -> str:
    return (f"label={traffic};traffic={traffic};dstL2Id=254;port=8001;"
            f"tx_nodes=0;rxNodes=1-{numUEs - 1};rri_ms=20;rate_kbps=200;harq=1")


# ---------------------------------------------------------------------------
# Per-cell invoker
# ---------------------------------------------------------------------------

def run_one(ns3_bin: str, output_root: Path, sim_time: float, warmup: float,
            scenario: str, channel: str, traffic: str, mcs: int, seed: int,
            timeout_s: int = 1800) -> tuple[bool, str]:
    """Run one csi_groupcast_eval invocation. Returns (ok, message)."""
    sc = SCENARIOS[scenario]
    trace = sc["trace"]
    num_ues = sc["numUEs"]

    cell_dir = output_root / f"{scenario}__{channel}__{traffic}__mcs{mcs}__seed{seed}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    log_path = cell_dir / "run.log"

    cmd = [
        str(ns3_bin),
        f"--mobilityTrace={trace}",
        f"--numUEs={num_ues}",
        "--mu=1",          # 30 kHz SCS, matches traffic_class_eval's defaults
        "--bwPRBs=50",     # 50 PRB subchannels — sufficient for the default traffic rate
        f"--baseScenario={channel}",
        f"--mcs={mcs}",
        f"--simTime={sim_time}",
        f"--warmup={warmup}",
        "--psfchPeriod=4",
        "--psfchKpi=true",
        "--psfchKpiSummaryOnly=false",
        f"--seed={seed}",
        f"--outputDir={cell_dir}/",
        "--simTag=run",
        f"--flows={make_flows(num_ues, traffic)}",
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
    parser.add_argument("--simTime", type=float, default=15.0)
    parser.add_argument("--warmup", type=float, default=2.0)
    parser.add_argument("--cores", type=int, default=4)
    parser.add_argument("--mcs", default=",".join(str(m) for m in MCS_LIST))
    parser.add_argument("--channels", default=",".join(CHANNELS))
    parser.add_argument("--traffic", default=",".join(TRAFFIC))
    parser.add_argument("--scenarios", default=",".join(SCENARIOS.keys()))
    parser.add_argument("--dryRun", action="store_true")
    args = parser.parse_args()

    rc = _run_sweep(args)
    return rc


def _task(c: tuple[str, str, str, int, int],
          ns3_bin: str, output_root: str, sim_time: float, warmup: float,
          timeout_s: int = 1800) -> tuple[tuple[str, str, str, int, int], bool, str]:
    """Module-level wrapper so ProcessPoolExecutor can pickle it."""
    ok, msg = run_one(Path(ns3_bin), Path(output_root), sim_time, warmup,
                      c[0], c[1], c[2], c[3], c[4], timeout_s=timeout_s)
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
    print(f"  channels={channels}")
    print(f"  traffic={traffic}")
    print(f"  mcs={mcs_list}")
    print(f"  seeds={seeds[0]}..{seeds[-1]} ({len(seeds)})")
    print(f"  simTime={args.simTime}, warmup={args.warmup}")
    print(f"  cores={args.cores}, outputRoot={args.outputRoot}")

    if args.dryRun:
        print(f"\n[DRY RUN] would execute {total} ns-3 invocations")
        return 0

    # RAM check (Linux/macOS)
    try:
        # ru_maxrss is KB on Linux, bytes on macOS
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
                      args.simTime, args.warmup): c
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