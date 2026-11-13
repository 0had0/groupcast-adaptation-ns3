"""ns-3 campaign on the helper reception ratio (notebook 10 of coop-harq-analytical).

Phase 1, "fixed": the helper is fixed to member k = 1..8 (--helperSelect=static --helper=k) instead of Kuo's choice,
so the helper reception ratio spans its whole range with real, channel-driven misses.
Phase 2, "controlled": Kuo's helper, forced to miss the leader's first copy with probability p (--helperMissProb,
--helperMissMode: data = SCI-2A decoded and TB failed, the helper NACKs; sci = the helper misses the SCI-2A and stays
silent). This is the ns-3 twin of the model's helper-miss sweep.

Both phases: arms R4_bsc (helper blind + C) and R6 (cooperative), periodic traffic, no background, seeds 1..10, on
the highway and both roundabout routes. Every command is the campaign-v3 command of the same arm, cell and seed
(out/remote_v3/<arm>/<cell>_s<seed>.cmd.json) with only those flags changed. p = 0 and Kuo's helper are campaign v3
itself: the local build reproduces those runs bit for bit (checked on R6, highway, seed 1).

  python3 campaign.py [--jobs 4] [--phase fixed|controlled|all] [--dry-run] [--no-analysis]

Resumable: a job whose <out>_run.csv.gz exists is skipped. A finished run's CSV files are gzipped (about 0.15 MB per
run instead of 0.75). Progress: out/local_helperrx/status.json (monitor.py), log: out/local_helperrx/campaign.log,
failures: out/local_helperrx/failures.csv. When every job is done, extract.py and notebook 10 run (unless
--no-analysis).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import gzip
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent
ROOT = STUDY.parents[2]  # groupcast-adaptation-ns3
NS3 = ROOT / "ns-3-dev"
BIN = NS3 / "build/scratch/platoon_harq_robustness_eval/ns3.42-platoon_harq_robustness_eval-default"
V3 = STUDY / "out/remote_v3"
OUT = STUDY / "out/local_helperrx"
ANALYTICAL = Path(os.environ.get("COOPHARQ_ANALYTICAL", "/Users/hadih/Documents/coop-harq-analytical"))
SERVER_ROOT = "/home/ease/Documents/groupcast-adaptation-ns3"

CELLS = ("hwy_ivd30_periodic1tb_none", "rbt_ivd30_periodic1tb_none", "rbtE_ivd30_periodic1tb_none")
SEEDS = range(1, 11)
HELPERS = range(1, 9)
PROBS = (0.1, 0.2, 0.3, 0.5, 0.7)
CONTROLLED_ARMS = (("R4_bsc", "data"), ("R6", "data"), ("R6", "sci"))


def base_cmd(arm: str, cell: str, seed: int) -> list[str]:
    cmd = json.load(open(V3 / arm / f"{cell}_s{seed}.cmd.json"))
    cmd = [c.replace(SERVER_ROOT, str(ROOT)) for c in cmd]
    cmd[0] = str(BIN)
    return cmd


def with_flags(cmd, out: Path, **flags) -> list[str]:
    keep = [c for c in cmd if not c.startswith("--out=") and c.split("=")[0].lstrip("-") not in flags]
    return keep + [f"--{k}={v}" for k, v in flags.items()] + [f"--out={out}"]


def jobs(phase: str) -> list[dict]:
    out = []
    if phase in ("fixed", "all"):
        for seed in SEEDS:
            for cell in CELLS:
                for arm in ("R4_bsc", "R6"):
                    for k in HELPERS:
                        o = OUT / "fixed" / arm / f"{cell}_h{k}_s{seed}"
                        out.append(dict(phase="fixed", arm=arm, cell=cell, seed=seed, helper=k, p=0.0, mode="",
                                        out=str(o), cmd=with_flags(base_cmd(arm, cell, seed), o,
                                                                   helperSelect="static", helper=k)))
    if phase in ("controlled", "all"):
        for seed in SEEDS:
            for cell in CELLS:
                for arm, mode in CONTROLLED_ARMS:
                    for p in PROBS:
                        o = OUT / "controlled" / arm / f"{cell}_p{p:g}_{mode}_s{seed}"
                        out.append(dict(phase="controlled", arm=arm, cell=cell, seed=seed, helper=0, p=p, mode=mode,
                                        out=str(o), cmd=with_flags(base_cmd(arm, cell, seed), o,
                                                                   helperMissProb=p, helperMissMode=mode)))
    return out


def done(job) -> bool:
    return os.path.exists(job["out"] + "_run.csv.gz")


class Status:
    def __init__(self, todo, total_by_phase, done_by_phase, jobs_n):
        self.lock = threading.Lock()
        self.s = dict(started=dt.datetime.now().isoformat(timespec="seconds"), pid=os.getpid(), jobs=jobs_n,
                      phases={ph: dict(total=total_by_phase[ph], done=done_by_phase[ph], failed=0)
                              for ph in total_by_phase},
                      running={}, durations=[], current_phase=None, state="running", todo=todo)
        self.write()

    def write(self):
        tmp = OUT / "status.json.tmp"
        tmp.write_text(json.dumps(self.s, indent=1))
        tmp.replace(OUT / "status.json")

    def update(self, fn):
        with self.lock:
            fn(self.s)
            self.write()


def log(msg: str):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(OUT / "campaign.log", "a") as f:
        f.write(line + "\n")


def gzip_outputs(prefix: str):
    for f in glob.glob(prefix + "_*.csv"):
        with open(f, "rb") as a, gzip.open(f + ".gz", "wb", compresslevel=6) as b:
            shutil.copyfileobj(a, b)
        os.remove(f)


def run_job(job, status: Status) -> bool:
    tag = Path(job["out"]).name
    Path(job["out"]).parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        status.update(lambda s: s["running"].__setitem__(f'{job["arm"]}/{tag}', time.time()))
        t0 = time.time()
        r = subprocess.run(job["cmd"], cwd=NS3, capture_output=True, text=True)
        dur = time.time() - t0
        status.update(lambda s: s["running"].pop(f'{job["arm"]}/{tag}', None))
        if r.returncode == 0 and os.path.exists(job["out"] + "_run.csv"):
            gzip_outputs(job["out"])
            Path(job["out"] + ".cmd.json").write_text(json.dumps(job["cmd"]))

            def ok(s):
                s["phases"][job["phase"]]["done"] += 1
                s["durations"] = (s["durations"] + [round(dur, 1)])[-200:]
            status.update(ok)
            return True
        log(f'FAIL ({attempt}/2) {job["arm"]}/{tag} exit {r.returncode}: {r.stderr.strip()[-300:]!r}')
        with open(OUT / "failures.csv", "a") as f:
            f.write(f'{dt.datetime.now().isoformat(timespec="seconds")},{job["phase"]},{job["arm"]},{tag},'
                    f'{attempt},{r.returncode}\n')
    status.update(lambda s: s["phases"][job["phase"]].__setitem__("failed", s["phases"][job["phase"]]["failed"] + 1))
    return False


def analysis():
    log("analysis: extract.py")
    r = subprocess.run([sys.executable, str(HERE / "extract.py")], capture_output=True, text=True)
    log(f"extract.py exit {r.returncode} {r.stdout.strip()[-300:]} {r.stderr.strip()[-500:]}")
    if r.returncode != 0:
        return False
    log("analysis: notebook 10")
    r = subprocess.run([sys.executable, "build_notebooks.py", "10_helper_rx_ns3"], cwd=ANALYTICAL / "notebooks",
                       capture_output=True, text=True)
    log(f"notebook exit {r.returncode} {r.stdout.strip()[-300:]} {r.stderr.strip()[-800:]}")
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--phase", default="all", choices=("fixed", "controlled", "all"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-analysis", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if not BIN.exists():
        sys.exit(f"binary missing: {BIN}")
    phases = ["fixed", "controlled"] if a.phase == "all" else [a.phase]
    alljobs = {ph: jobs(ph) for ph in phases}
    total = {ph: len(alljobs[ph]) for ph in phases}
    already = {ph: sum(done(j) for j in alljobs[ph]) for ph in phases}
    todo = sum(total.values()) - sum(already.values())
    free_gb = shutil.disk_usage(OUT).free / 1e9
    log(f"start: {total} jobs, {already} already done, {todo} to run, {a.jobs} parallel, {free_gb:.1f} GB free")
    if a.dry_run:
        for ph in phases:
            for j in alljobs[ph][:2]:
                print(" ".join(j["cmd"]))
        return
    if free_gb < 3:
        sys.exit("less than 3 GB free on the disk: stopping")
    status = Status(todo, total, already, a.jobs)
    for ph in phases:  # one phase after the other
        status.update(lambda s: s.__setitem__("current_phase", ph))
        pending = [j for j in alljobs[ph] if not done(j)]
        log(f"phase {ph}: {len(pending)} to run")
        with ThreadPoolExecutor(a.jobs) as ex:
            futs = [ex.submit(run_job, j, status) for j in pending]
            for _ in as_completed(futs):
                pass
        log(f"phase {ph} finished: {status.s['phases'][ph]}")
    failed = sum(p["failed"] for p in status.s["phases"].values())
    status.update(lambda s: s.__setitem__("state", "runs done"))
    if a.no_analysis:
        status.update(lambda s: s.__setitem__("state", "done (no analysis)"))
    elif failed:
        log(f"{failed} failed jobs: re-run campaign.py to retry them; analysis not started")
        status.update(lambda s: s.__setitem__("state", "done with failures"))
    else:
        status.update(lambda s: s.__setitem__("state", "analysis"))
        ok = analysis()
        status.update(lambda s: s.__setitem__("state", "done" if ok else "analysis failed"))
    log(f"state: {status.s['state']}")


if __name__ == "__main__":
    main()
