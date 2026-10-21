#!/usr/bin/env python3
"""run_matrix.py -- parallel, resumable runner of the test run-sets (TEST_PLAN section 10).

  run_matrix.py --runset SYN_NEAR,SYN_MARGINAL [--profile quick|pilot|reduced|full] [--jobs 4] [--no-resume] [--list] [--only REGEX]

For every run: `/usr/bin/time -l -o <tag>.time env -u MAGIC_HELPER_ID -u NUM_MEMBERS <binary> <args>` (the A05 probe
sets MAGIC_HELPER_ID explicitly), timeout (default 3600 s; the plan's 10 min is too short for the background cells), exit code and peak RSS recorded in <tag>.meta.json, CSV outputs
larger than 1 MB are gzipped, and tests/out/<runset>/manifest.csv is (re)written from the sidecars:
  runset,arm,scenario,ivd,bg,traffic,seed,leader_sps,tag,out_prefix,exit,wall_s,max_rss_mb,cmd
A run counts as complete when <prefix>_run.csv(.gz) exists, its sidecar says exit 0 and its log holds the summary line
('  pkts=').  No run starts while less than 4 GB of memory is available (free + inactive + speculative pages).
"""

import argparse
import concurrent.futures as cf
import csv
import gzip
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runsets as rs  # noqa: E402
import make_synth_traces  # noqa: E402

MIN_FREE_GB = 4.0
TIMEOUT_S = 3600  # real-trace runs with background traffic take 5-20 min (historical wall_s: 456 s hwy n60 k=1.67 for a 20 s run)
MANIFEST_COLS = ["runset", "arm", "scenario", "ivd", "bg", "traffic", "seed", "leader_sps", "tag", "out_prefix",
                 "exit", "wall_s", "max_rss_mb", "cmd"]


def avail_gb():
    """free + inactive + speculative + purgeable pages (macOS vm_stat)."""
    try:
        txt = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return 99.0
    m = re.search(r"page size of (\d+) bytes", txt)
    page = int(m.group(1)) if m else 16384
    tot = 0
    for key in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
        mm = re.search(rf"{key}:\s+(\d+)", txt)
        if mm:
            tot += int(mm.group(1))
    return tot * page / 1e9


def is_complete(run):
    pre = run.prefix
    meta = Path(str(pre) + ".meta.json")
    if not meta.exists():
        return False
    if not (Path(str(pre) + "_run.csv").exists() or Path(str(pre) + "_run.csv.gz").exists()):
        return False
    try:
        m = json.loads(meta.read_text())
    except Exception:
        return False
    if m.get("exit") != 0:
        return False
    # a result of another profile (e.g. the 20 s quick run of the same tag) is not a result of this run: the command line must match
    if m.get("cmd") != " ".join(shlex.quote(c) for c in cmd_of(run)):
        return False
    log = Path(str(pre) + ".log")
    if not log.exists():
        return False
    try:
        return "  pkts=" in log.read_text(errors="replace")
    except Exception:
        return False


def gzip_big(prefix: Path):
    d = prefix.parent
    stem = prefix.name
    for f in d.glob(stem + "_*.csv"):
        try:
            if f.stat().st_size > 1_000_000:
                with open(f, "rb") as fi, gzip.open(str(f) + ".gz", "wb", compresslevel=6) as fo:
                    shutil.copyfileobj(fi, fo)
                f.unlink()
        except Exception as e:  # keep the plain file on failure
            print(f"gzip failed for {f}: {e}", file=sys.stderr)


def cmd_of(run):
    env = ["env", "-u", "NUM_MEMBERS"]
    if "MAGIC_HELPER_ID" not in run.env_set:
        env += ["-u", "MAGIC_HELPER_ID"]
    for k, v in run.env_set.items():
        env.append(f"{k}={v}")
    return env + [str(rs.BIN)] + run.args()


def exec_run(run):
    pre = run.prefix
    run.outdir.mkdir(parents=True, exist_ok=True)
    while avail_gb() < MIN_FREE_GB:
        time.sleep(5)
    cmd = cmd_of(run)
    tfile = Path(str(pre) + ".time")
    full = ["/usr/bin/time", "-l", "-o", str(tfile)] + cmd
    t0 = time.time()
    code = None
    with open(str(pre) + ".log", "w") as lf:
        try:
            p = subprocess.run(full, stdout=lf, stderr=subprocess.STDOUT, timeout=TIMEOUT_S, cwd=str(run.outdir))
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = 124
    wall = time.time() - t0
    rss = 0.0
    try:
        mm = re.search(r"(\d+)\s+maximum resident set size", tfile.read_text())
        if mm:
            rss = int(mm.group(1)) / 1e6  # bytes on macOS -> MB
    except Exception:
        pass
    if code == 0:
        gzip_big(pre)
    meta = {"exit": code, "wall_s": round(wall, 2), "max_rss_mb": round(rss, 1),
            "cmd": " ".join(shlex.quote(c) for c in cmd), "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    Path(str(pre) + ".meta.json").write_text(json.dumps(meta))
    return run, code, wall, rss


def write_manifest(runset, runs):
    outdir = rs.OUT / runset
    outdir.mkdir(parents=True, exist_ok=True)
    rows, failed = [], []
    for r in runs:
        meta_p = Path(str(r.prefix) + ".meta.json")
        if not meta_p.exists():
            failed.append(f"{r.arm}/{r.tag}: not run")
            continue
        m = json.loads(meta_p.read_text())
        ok = is_complete(r)
        if not ok:
            failed.append(f"{r.arm}/{r.tag}: exit {m.get('exit')}")
            continue
        rows.append({"runset": runset, "arm": r.arm, "scenario": r.scenario, "ivd": r.ivd, "bg": r.bg,
                     "traffic": r.traffic, "seed": r.seed, "leader_sps": int(r.leader_sps), "tag": r.tag,
                     "out_prefix": str(r.prefix), "exit": m["exit"], "wall_s": m["wall_s"],
                     "max_rss_mb": m["max_rss_mb"], "cmd": m["cmd"]})
    with open(outdir / "manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLS)
        w.writeheader()
        w.writerows(rows)
    (outdir / "failed.txt").write_text("\n".join(failed) + ("\n" if failed else ""))
    return len(rows), failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runset", required=True, help="comma list: " + ",".join(rs.SYNTH_NAMES + rs.CAMPAIGN_NAMES))
    ap.add_argument("--profile", default="quick", choices=["quick", "pilot", "reduced", "full"])
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", default="", help="regex on '<arm>/<tag>'")
    ap.add_argument("--timeout", type=int, default=TIMEOUT_S, help="per-run timeout (s)")
    args = ap.parse_args()

    globals()["TIMEOUT_S"] = args.timeout
    rs.assert_base_flags()
    make_synth_traces.write_all()
    if not rs.BIN.exists():
        sys.exit(f"binary missing: {rs.BIN}")
    names = [x for x in args.runset.split(",") if x]
    t_all = time.time()
    for name in names:
        runs = rs.build(name, args.profile)
        if args.only:
            runs = [r for r in runs if re.search(args.only, f"{r.arm}/{r.tag}")]
        outdir = rs.OUT / name
        outdir.mkdir(parents=True, exist_ok=True)
        # drop runs whose trace is missing
        missing, keep = [], []
        for r in runs:
            if r.trace.exists():
                keep.append(r)
            else:
                missing.append(f"{r.arm}/{r.tag}: {r.trace}")
        (outdir / "missing.txt").write_text("\n".join(sorted(set(missing))) + ("\n" if missing else ""))
        runs = keep
        todo = [r for r in runs if args.no_resume or not is_complete(r)]
        print(f"[{name}] profile={args.profile} planned={len(runs) + len(missing)} missing_trace={len(missing)} "
              f"to_run={len(todo)} already_done={len(runs) - len(todo)} jobs={args.jobs}", flush=True)
        if args.list:
            for r in todo[:10]:
                print("   ", " ".join(cmd_of(r))[:300])
            continue
        t0 = time.time()
        bad = 0
        done = 0
        peak = 0.0
        with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = [ex.submit(exec_run, r) for r in todo]
            for fu in cf.as_completed(futs):
                r, code, wall, rss = fu.result()
                done += 1
                peak = max(peak, rss)
                if code != 0:
                    bad += 1
                    print(f"[{name} {done}/{len(todo)}] FAILED exit {code}: {r.arm}/{r.tag} (log: {r.prefix}.log)",
                          file=sys.stderr, flush=True)
                elif done % 10 == 0 or done == len(todo):
                    el = time.time() - t0
                    rate = done / max(el, 1e-3)
                    print(f"[{name} {done}/{len(todo)}] {rate:.2f} runs/s, eta {(len(todo) - done) / rate / 60:.1f} min,"
                          f" failed {bad}, peak rss {peak:.0f} MB", flush=True)
        n_ok, failed = write_manifest(name, runs)
        print(f"[{name}] done in {time.time() - t0:.0f} s: {n_ok} complete, {len(failed)} failed/not run; "
              f"manifest {outdir / 'manifest.csv'}", flush=True)
    print(f"total wall {time.time() - t_all:.0f} s")


if __name__ == "__main__":
    main()
