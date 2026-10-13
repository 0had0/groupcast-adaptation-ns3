#!/usr/bin/env python3
"""Hidden-node test of the helper on leader-chosen resources (README "Hidden-node test").

Runs the program with the hidden-node instrumentation (--hiddenNode: <tag>_use.csv,
<tag>_sel.csv, hn_* run-CSV columns) in the arms none / leaderRes / ownRes /
leaderResIuc (oracle inter-UE coordination: an UPPER BOUND, not IUC), periodic1 only,
every run with --collCond, at one or two sensing thresholds (--slThresRsrp).

The SB-SPS reference flags (run_rerun_sps.sh NEW_FLAGS) are NOT added here: pass a
wrapper binary (--bin) that appends them, as run_hidden_node.sh does.

Stages (one results root for all, so stage-B runs reuse the stage-A seeds when the
tag already exists):
  stageA  cells rbt IVD30 N45, hwy IVD30 N45, hwy IVD60 N45; helpers 4, 7; seeds 1-3
  sweep1  N {0,15,30,45,60} x {highway, roundabout}, IVD 30, helper 4, seeds 1-10
  sweep2  N 45 x {highway, roundabout} x helpers {2,4,7} x IVD {30,60}, seeds 1-10
          (arms leaderRes / ownRes / leaderResIuc; none once per scenario x IVD x thr)
Cells can be dropped with --drop scenario:ivd:N (cells stage A showed to be flat).

Tags: {hwy|rbt}_ivd{IVD}_periodic1_n{N}_h{helper}_th{-thr}_s{seed}; arm = folder.

  python3 run_hidden_node.py --stage stageA --thresholds -128 -110 --dry-run
  python3 run_hidden_node.py --stage stageA --thresholds -128 -110 --jobs 24 \\
      --results results/hidden_hn --bin results/rerun_hn/ns3_hn
"""
import argparse
import concurrent.futures as cf
import csv
import itertools
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_campaign as rc  # noqa: E402  (traces)
import run_coop as rcp  # noqa: E402  (run_job, design checks, arms)

TRAFFIC = "periodic1"
ARMS = ["none", "leaderRes", "ownRes", "leaderResIuc"]
HELPER_ARMS = ["leaderRes", "ownRes", "leaderResIuc"]
NONE_HELPER = 4  # `none` is run once (cooperation off), with helper 4 as sensing observer

# run-CSV columns that must be 0 (README "Hidden-node test", checks)
HN_MUST0 = ["hn_chk_sel_in_l", "hn_chk_age0_unexplained", "hn_chk_gap_mismatch",
            "hn_chk_link", "hn_chk_unlinked_uses"]

# rough server cost per run (s, 24 parallel jobs, periodic1; SB-SPS coop medians)
COST_S = {("highway", 0): 10, ("highway", 15): 300, ("highway", 30): 700,
          ("highway", 45): 1200, ("highway", 60): 2800,
          ("roundabout", 0): 8, ("roundabout", 15): 150, ("roundabout", 30): 400,
          ("roundabout", 45): 850, ("roundabout", 60): 1600}


def tag_of(sc, ivd, nbg, helper, thr, seed):
    return f"{rc.SHORT[sc]}_ivd{ivd}_{TRAFFIC}_n{nbg}_h{helper}_th{-thr}_s{seed}"


def matrix(stage, thresholds, seeds, drop):
    """[(arm, scenario, ivd, nbg, helper, thr, seed)], without duplicates"""
    runs = []
    if stage == "stageA":
        cells = [("roundabout", 30, 45), ("highway", 30, 45), ("highway", 60, 45)]
        helpers = [4, 7]
    elif stage == "sweep1":
        cells = [(sc, 30, n) for sc in ("highway", "roundabout") for n in (0, 15, 30, 45, 60)]
        helpers = [4]
    elif stage == "sweep2":
        cells = [(sc, ivd, 45) for sc in ("highway", "roundabout") for ivd in (30, 60)]
        helpers = [2, 4, 7]
    else:
        raise ValueError(stage)
    cells = [c for c in cells if c not in drop]
    for (sc, ivd, nbg), thr, seed in itertools.product(cells, thresholds, seeds):
        runs.append(("none", sc, ivd, nbg, NONE_HELPER, thr, seed))
        for arm, h in itertools.product(HELPER_ARMS, helpers):
            runs.append((arm, sc, ivd, nbg, h, thr, seed))
    seen, out = set(), []
    for r in runs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def hn_check(run_csv, arm, thr):
    """hidden-node checks and flags (in addition to run_coop.check)"""
    with open(run_csv) as f:
        r = next(csv.DictReader(f))
    bad = []
    if r.get("hidden_node") != "1":
        bad.append(f"hidden_node={r.get('hidden_node')}")
    if r.get("sl_thres_rsrp") != str(thr):
        bad.append(f"sl_thres_rsrp={r.get('sl_thres_rsrp')}")
    if r.get("coll_cond") != "1":
        bad.append(f"coll_cond={r.get('coll_cond')}")
    must0 = HN_MUST0 + (["hn_chk_iuc_sel_in_h"] if arm == "leaderResIuc" else [])
    for k in must0:
        if k not in r or int(float(r[k])) != 0:
            bad.append(f"{k}={r.get(k)}")
    return bad


def run_one(job, tdir, root, max_time):
    arm, sc, ivd, nbg, helper, thr, seed = job
    tag = tag_of(sc, ivd, nbg, helper, thr, seed)
    extra = [f"--slThresRsrp={thr}", "--hiddenNode=true", "--collCond=true"]
    if max_time > 0:
        extra.append(f"--maxTime={max_time:g}")
    res = rcp.run_job((arm, sc, ivd, TRAFFIC, nbg, seed, tdir, root / arm,
                       {"helper": helper, "tag": tag, "extra": extra}))
    arm, tag, st, dt = res
    if st == "ok":
        bad = hn_check(root / arm / f"{tag}_run.csv", arm, thr)
        if bad:
            st = "FLAG CHECK FAILED " + " ".join(bad)
    return arm, tag, st, dt


def estimate(jobs, n_jobs):
    total = sum(COST_S.get((j[1], j[3]), 1200) for j in jobs)
    longest = max((COST_S.get((j[1], j[3]), 1200) for j in jobs), default=0)
    return total, max(total / max(n_jobs, 1), longest)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", required=True, choices=["stageA", "sweep1", "sweep2"])
    ap.add_argument("--thresholds", type=int, nargs="+", default=[-128, -110])
    ap.add_argument("--seeds", type=int, nargs="+", default=None,
                    help="default: 1-3 (stageA), 1-10 (sweeps)")
    ap.add_argument("--drop", nargs="*", default=[],
                    help="cells to leave out, scenario:ivd:N (e.g. highway:60:45)")
    ap.add_argument("--jobs", type=int, default=7)
    ap.add_argument("--traces", default=str(HERE / "traces"))
    ap.add_argument("--results", default=str(HERE / "results/hidden_hn"))
    ap.add_argument("--bin", default=None, help="program or wrapper (adds the SB-SPS flags)")
    ap.add_argument("--max-time", type=float, default=0.0, help="truncate runs (pilots)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    seeds = a.seeds or (list(range(1, 4)) if a.stage == "stageA" else list(range(1, 11)))
    drop = set()
    for d in a.drop:
        sc, ivd, n = d.split(":")
        drop.add((sc, int(ivd), int(n)))
    jobs = matrix(a.stage, a.thresholds, seeds, drop)
    tdir, root = Path(a.traces).resolve(), Path(a.results).resolve()
    todo = [j for j in jobs
            if not (root / j[0] / f"{tag_of(*j[1:])}_run.csv").exists()]
    tot, wall = estimate(todo, a.jobs)
    print(f"{a.stage}: {len(jobs)} runs ({len(todo)} to do), thresholds {a.thresholds}, "
          f"seeds {seeds[0]}-{seeds[-1]}, dropped {sorted(drop) or '-'}; estimate "
          f"{tot / 3600:.1f} CPU-h, ~{wall / 3600:.1f} h at {a.jobs} jobs (server costs)")
    if a.dry_run:
        by = {}
        for j in todo:
            by[(j[1], j[2], j[3], j[0])] = by.get((j[1], j[2], j[3], j[0]), 0) + 1
        for k in sorted(by):
            print(f"  {k[0]:10s} IVD {k[1]:2d} N {k[2]:2d} {k[3]:13s} {by[k]:3d}")
        return
    if a.bin:
        rcp.BIN = Path(a.bin).resolve()
    else:
        cands = sorted(rc.BIN_DIR.glob("ns3*-platoon_harq_robustness_eval-*"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            sys.exit(f"no binary in {rc.BIN_DIR}; build it first or pass --bin")
        rcp.BIN = cands[0]
    tdir.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    progress = open(root / "progress.log", "a")

    def log(msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        progress.write(line + "\n")
        progress.flush()
        try:
            print(line, flush=True)
        except OSError:
            pass

    log(f"binary {rcp.BIN}; {a.stage}: {len(jobs)} runs, {len(todo)} to do, {a.jobs} parallel")
    tjobs = sorted({(tdir, j[1], j[2], j[3], j[6]) for j in jobs}, key=str)
    with cf.ProcessPoolExecutor(max_workers=a.jobs) as ex:
        for path, st in ex.map(rc.make_trace, tjobs):
            if st != "cached":
                log(f"trace {Path(path).name}: {st}")
            if st.startswith("FAILED"):
                sys.exit(1)
    # replicate by replicate, most expensive first
    todo.sort(key=lambda j: (j[6], -COST_S.get((j[1], j[3]), 1200)))
    done, failed, t0 = 0, 0, time.time()
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = [ex.submit(run_one, j, tdir, root, a.max_time) for j in todo]
        for f in cf.as_completed(futs):
            arm, tag, st, dt = f.result()
            done += 1
            failed += st not in ("ok", "cached")
            log(f"{done}/{len(todo)} {arm}/{tag}: {st} ({dt:.0f}s)  "
                f"elapsed {(time.time() - t0) / 60:.1f} min")
    log(f"{a.stage} finished, {failed} not ok")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
