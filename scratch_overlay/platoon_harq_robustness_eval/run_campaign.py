#!/usr/bin/env python3
"""
run_campaign.py -- full sweep of platoon_harq_robustness_eval.

  scenario  : highway, roundabout
  ivd       : 5, 15, 30 m            (bumper-to-bumper gap inside the platoon)
  traffic   : periodic1, periodic2, aperiodic1   (leader -> 8 members, groupcast)
  nbg       : 0, 15, 30, 45, 60      (background vehicles in the area of interest)
  seed      : 1..N

One mobility trace per (scenario, ivd, nbg, seed) is shared by the three
traffic types, so traffic types are compared on identical mobility.

Resumable: a run is skipped when its <tag>_run.csv exists. Jobs are started
largest first (nbg, then highway) to pack the pool well.

  python3 run_campaign.py --seeds 1 2 3 --jobs 7
  python3 run_campaign.py --seeds 1 --jobs 7 --dry-run
"""

import argparse
import concurrent.futures as cf
import gzip
import itertools
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BIN_DIR = REPO / "ns-3-dev/build/scratch/platoon_harq_robustness_eval"
BIN = None   # resolved in main(): --bin, else the build-profile-specific binary in BIN_DIR
NET = HERE / "sumo/roundabout/roundabout.net.xml"

SCENARIOS = ["highway", "roundabout"]
IVDS = [5, 15, 30]
TRAFFIC = ["periodic1", "periodic2", "aperiodic1"]
NBG = [0, 15, 30, 45, 60]
SHORT = {"highway": "hwy", "roundabout": "rbt"}


def trace_path(tdir, sc, ivd, nbg, seed):
    return tdir / f"{SHORT[sc]}_ivd{ivd}_n{nbg}_s{seed}.csv.gz"


def make_trace(args):
    tdir, sc, ivd, nbg, seed = args
    out = trace_path(tdir, sc, ivd, nbg, seed)
    if out.exists() and out.with_suffix("").with_suffix(".json").exists():
        return str(out), "cached"
    env = dict(os.environ)
    if "SUMO_HOME" not in env and (REPO / "sumo/bin/sumo").exists():
        env["SUMO_HOME"] = str(REPO / "sumo")
    r = subprocess.run([sys.executable, str(HERE / "gen_mobility.py"), "--scenario", sc,
                        "--ivd", str(ivd), "--nbg", str(nbg), "--seed", str(seed),
                        "--out", str(out)], env=env, capture_output=True, text=True)
    if r.returncode != 0:
        return str(out), "FAILED: " + r.stdout[-300:] + r.stderr[-600:]
    return str(out), "ok"


def run_job(job):
    try:
        return _run_job(job)
    except Exception as e:  # never let one job take the campaign loop down
        sc, ivd, tr, nbg, seed, _, _ = job
        return f"{SHORT[sc]}_ivd{ivd}_{tr}_n{nbg}_s{seed}", f"FAILED exception {e!r}", 0.0


def _run_job(job):
    sc, ivd, tr, nbg, seed, tdir, rdir = job
    tag = f"{SHORT[sc]}_ivd{ivd}_{tr}_n{nbg}_s{seed}"
    out = rdir / tag
    if (rdir / f"{tag}_run.csv").exists():
        return tag, "cached", 0.0
    cmd = [str(BIN), f"--scenario={sc}", f"--mobility={trace_path(tdir, sc, ivd, nbg, seed)}",
           f"--traffic={tr}", f"--seed={seed}", f"--run=1", f"--out={out}"]
    if sc == "roundabout":
        cmd.append(f"--sumoNet={NET}")
    env = {k: v for k, v in os.environ.items() if not k.startswith("MAGIC_")}
    t0 = time.time()
    with open(rdir / f"{tag}.log", "w") as log:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=log, env=env)
    dt = time.time() - t0
    if r.returncode != 0:
        return tag, f"FAILED rc={r.returncode}", dt
    for suffix in ("_pkt.csv", "_ltx.csv"):
        f = rdir / f"{tag}{suffix}"
        if f.exists():
            with open(f, "rb") as fi, gzip.open(str(f) + ".gz", "wb") as fo:
                shutil.copyfileobj(fi, fo)
            f.unlink()
    return tag, "ok", dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--jobs", type=int, default=7)
    ap.add_argument("--scenarios", nargs="+", default=SCENARIOS)
    ap.add_argument("--ivds", type=int, nargs="+", default=IVDS)
    ap.add_argument("--traffic", nargs="+", default=TRAFFIC)
    ap.add_argument("--nbg", type=int, nargs="+", default=NBG)
    ap.add_argument("--traces", default=str(HERE / "traces"))
    ap.add_argument("--results", default=str(HERE / "results/campaign"))
    ap.add_argument("--bin", default=None, help="ns-3 binary (default: auto-detect in build/)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    global BIN
    if a.bin:
        BIN = Path(a.bin)
    else:
        cands = sorted(BIN_DIR.glob("ns3*-platoon_harq_robustness_eval-*"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            sys.exit(f"no binary in {BIN_DIR}; build it first or pass --bin")
        BIN = cands[0]
    print(f"binary: {BIN}")

    tdir, rdir = Path(a.traces), Path(a.results)
    tdir.mkdir(parents=True, exist_ok=True)
    rdir.mkdir(parents=True, exist_ok=True)
    progress = open(rdir / "progress.log", "a")

    def log(msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        progress.write(line + "\n")
        progress.flush()
        try:
            print(line, flush=True)
        except OSError:
            pass  # terminal gone (closed ssh / pipe): keep logging to the file

    # ---- 1. mobility traces (cheap, SUMO)
    tjobs = [(tdir, sc, ivd, nbg, seed) for sc, ivd, nbg, seed in
             itertools.product(a.scenarios, a.ivds, a.nbg, a.seeds)]
    if not a.dry_run:
        with cf.ProcessPoolExecutor(max_workers=a.jobs) as ex:
            for path, st in ex.map(make_trace, tjobs):
                if st != "cached":
                    log(f"trace {Path(path).name}: {st}")
                if st.startswith("FAILED"):
                    sys.exit(1)

    # ---- 2. ns-3 runs, most expensive first
    jobs = [(sc, ivd, tr, nbg, seed, tdir, rdir) for sc, ivd, tr, nbg, seed in
            itertools.product(a.scenarios, a.ivds, a.traffic, a.nbg, a.seeds)]
    jobs.sort(key=lambda j: (-j[3], j[0] != "highway", j[2] != "periodic2", j[4]))
    todo = [j for j in jobs if not (rdir / f"{SHORT[j[0]]}_ivd{j[1]}_{j[2]}_n{j[3]}_s{j[4]}_run.csv").exists()]
    log(f"{len(jobs)} runs, {len(todo)} to do, {a.jobs} parallel")
    if a.dry_run:
        for j in todo[:10]:
            print(j[:5])
        return
    done = 0
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(run_job, j): j for j in todo}
        for f in cf.as_completed(futs):
            try:
                tag, st, dt = f.result()
            except Exception as e:
                j = futs[f]
                tag, st, dt = f"{SHORT[j[0]]}_ivd{j[1]}_{j[2]}_n{j[3]}_s{j[4]}", f"FAILED {e!r}", 0.0
            done += 1
            el = time.time() - t0
            log(f"{done}/{len(todo)} {tag}: {st} ({dt:.0f}s)  elapsed {el / 60:.1f} min")
    log("campaign finished")


if __name__ == "__main__":
    main()
