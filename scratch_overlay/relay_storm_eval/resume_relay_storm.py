#!/usr/bin/env python3
"""
resume_relay_storm.py
=====================
Resume an interrupted relay_storm_eval sweep.

Reads the existing run directories in the output folder and re-runs
ONLY the missing (incomplete) configurations.

Usage:
    python3 resume_relay_storm.py <output_dir> <sim_time> <nparallel>

Example:
    python3 resume_relay_storm.py ./results_relay_storm_eval 30.0 10
"""

import os, sys, subprocess, time, pathlib
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()
NS3_DIR    = SCRIPT_DIR.parent.parent / "ns-3-dev"
BINARY     = NS3_DIR / "cmake-cache-opt/scratch/relay_storm_eval/ns3.42-relay_storm_eval-default"

SPEED_IVD_PAIRS = [
    (80, 25), (90, 27), (100, 29),
    (110, 32), (120, 34), (130, 37), (140, 39),
]
MCD_VALUES  = [80, 100, 150, 300, 350]
RELAY_NODES = [0, 4, 6]   # 0 = baseline (relayEnabled=false)


def build_full_set(seeds):
    """Return set of all expected run tags."""
    runs = set()
    for spd, ivd in SPEED_IVD_PAIRS:
        for mcd in MCD_VALUES:
            for relay in RELAY_NODES:
                for seed in seeds:
                    if relay == 0:
                        tag = f"baseline_s{spd}_ivd{ivd}_mcd{mcd}_seed{seed}"
                        enabled = "false"
                    else:
                        tag = f"relay{relay}_s{spd}_ivd{ivd}_mcd{mcd}_seed{seed}"
                        enabled = "true"
                    runs.add(tag)
    return runs


def existing_runs(output_dir):
    """Return set of run tags that already have a .db file."""
    existing = set()
    for d in os.listdir(output_dir):
        full = os.path.join(output_dir, d)
        if os.path.isdir(full) and not d.startswith("."):
            db_files = [f for f in os.listdir(full) if f.endswith(".db")]
            if db_files:
                existing.add(d)
    return existing


def run_single(tag: str, speed: int, ivd: int, mcd: int,
               relay: int, enabled: str, seed: int,
               output_dir: str, sim_time: float) -> str:
    """Run one simulation, write to output_dir/tag/.  Returns tag on success."""
    run_dir = os.path.join(output_dir, tag)
    os.makedirs(run_dir, exist_ok=True)

    relay_arg = str(relay) if relay > 0 else "0"

    cmd = [
        str(BINARY),
        f"--runId={tag}",
        f"--seed={seed}",
        f"--speed={speed}",
        f"--ivd={ivd}",
        f"--mcd={mcd}",
        f"--relayNode={relay_arg}",
        f"--relayEnabled={enabled}",
        f"--simTime={sim_time}",
        f"--outputDir={run_dir}",
        f"--simTag={tag}",
    ]
    try:
        subprocess.run(cmd, check=True,
                      stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL,
                      timeout=600)
        return tag
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def main():
    if len(sys.argv) < 4:
        print("Usage: resume_relay_storm.py <output_dir> <sim_time> <nparallel>")
        sys.exit(1)

    output_dir = os.path.abspath(sys.argv[1])
    sim_time   = float(sys.argv[2])
    nparallel  = int(sys.argv[3])

    # Determine seeds from existing runs
    existing = existing_runs(output_dir)
    if existing:
        # Infer seeds from existing run tag pattern
        import re
        seeds_found = set()
        for tag in existing:
            m = re.search(r"_seed(\d+)$", tag)
            if m:
                seeds_found.add(int(m.group(1)))
        seeds = sorted(seeds_found) if seeds_found else list(range(1, 31))
    else:
        seeds = list(range(1, 31))

    all_runs  = build_full_set(seeds)
    done      = existing
    todo      = sorted(all_runs - done)
    total     = len(all_runs)
    remaining = len(todo)

    print(f"Output:     {output_dir}")
    print(f"Binary:     {BINARY}")
    print(f"Sim time:   {sim_time}s")
    print(f"Total runs: {total}  |  Done: {len(done)}  |  Remaining: {remaining}")
    print(f"Parallel:   {nparallel}")
    print()

    if not todo:
        print("All runs complete!")
        return

    # Build task list
    import re
    tasks = []
    for tag in todo:
        m = re.match(
            r"(baseline|relay(\d+))_s(\d+)_ivd(\d+)_mcd(\d+)_seed(\d+)",
            tag
        )
        if not m:
            continue
        mode, relay_str, spd, ivd, mcd, seed = m.groups()
        relay    = int(relay_str) if relay_str else 0
        enabled  = "false" if relay == 0 else "true"
        tasks.append({
            "tag":     tag,
            "speed":   int(spd),
            "ivd":     int(ivd),
            "mcd":     int(mcd),
            "relay":   relay,
            "enabled": enabled,
            "seed":    int(seed),
        })

    print(f"Launching {len(tasks)} missing runs ...")
    start = time.time()
    done_count = len(done)
    done_tags  = set(done)
    lock = __import__("threading").Lock()

    def worker(task):
        result = run_single(
            output_dir=output_dir, sim_time=sim_time,
            **task
        )
        if result:
            with lock:
                done_tags.add(result)
                n = len(done_tags)
                pct = n / total * 100
                elapsed = time.time() - start
                rate = (n - done_count) / elapsed * 3600 if elapsed > 1 else 0
                eta = (total - n) / (rate / 3600) if rate > 0 else 0
                print(f"\r  [{n:>4}/{total}  {pct:5.1f}%  "
                      f"{rate:.0f}/hr  ETA {eta:.0f}s] {result}", end="", flush=True)
        return result

    with ThreadPoolExecutor(max_workers=nparallel) as ex:
        futures = [ex.submit(worker, t) for t in tasks]
        for f in as_completed(futures):
            pass  # all collected

    elapsed = time.time() - start
    print(f"\n\nDone. {len(done_tags)}/{total} runs completed "
          f"in {elapsed:.0f}s (~{elapsed/3600:.1f}h)")


if __name__ == "__main__":
    main()
