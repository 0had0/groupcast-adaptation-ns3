#!/usr/bin/env python3
"""run_retx.py — Runner for the groupcast retransmission × helper selection × helper scheduling campaign.

Implements Stages A, A', B, C, and Calib per PLAN.md.
Reuses trace generation from run_campaign.py and execution patterns from run_coop.py.
"""

import argparse
import concurrent.futures as cf
import csv
import gzip
import itertools
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
import run_campaign as rc

# Base configuration constants (Phase 0 pinned)
RRI_LIST = "0,10,20,50,100,200,300,400,500,600,700,800,900,1000"
PROB_KEEP = 0.8
RLC_MS = 0.0

BASE_FLAGS = [
    "--dedicatedCarrier=true",
    "--leaderSps=true",
    "--spsReselectOnMisfit=true",
    "--slDynExclusion=true",
    "--slProjectAnnounced=true",
    f"--slProbResourceKeep={PROB_KEEP}",
    f"--rriList={RRI_LIST}",
    f"--ns3::LteRlcUm::ReorderingTimer={RLC_MS:g}ms",
]

# Background load levels: name -> (N_cars, flows_per_car k)
# If BG_CARS env is set (e.g. 30), cars are halved and k is doubled.
def get_bg_levels():
    bg_cars = int(os.environ.get("BG_CARS", "60"))
    if bg_cars == 30:
        return {
            "none": (0, 1.0),
            "moderate": (30, 3.33),
            "high": (30, 6.67),
        }
    return {
        "none": (0, 1.0),
        "moderate": (60, 1.67),
        "high": (60, 3.33),
    }

# Traffic classes for the campaign (one-TB classes)
TRAFFIC_CLASSES = ["periodic1tb", "periodic2tb", "aperiodic1tb"]

PDB_MS = {
    "periodic1tb": 100.0,
    "periodic2tb": 10.0,
    "aperiodic1tb": 50.0,
}

# ---------------------------------------------------------------------------
# Stage Arm Generators
# ---------------------------------------------------------------------------

def generate_stageA_arms():
    """Stage A: Retransmission strategies (12 arms).
    R1..R3 (leader) + R4..R6 (helper S1 + H3 map), each x {1, 2} retx (maxNumTx 2 or 3).
    """
    arms = {}
    for retx in ["R1", "R2", "R3", "R4", "R5", "R6"]:
        for n_retx in [1, 2]:
            max_tx = n_retx + 1
            arm_name = f"{retx}_tx{max_tx}"
            flags = [f"--retx={retx}", f"--maxNumTx={max_tx}"]
            if retx in ("R4", "R5", "R6"):
                flags += ["--helperSched=S1", "--helperSelect=map", "--helper=4"]
            arms[arm_name] = flags
    return arms

def generate_stageAprime_arms():
    """Stage A': aperiodic RRI sensitivity (2 arms)."""
    arms = {
        "R2_tx3_rri10": ["--retx=R2", "--maxNumTx=3", "--aperiodicRri=10"],
        "R5_S1_map_tx3_rri10": [
            "--retx=R5", "--helperSched=S1", "--helperSelect=map",
            "--helper=4", "--maxNumTx=3", "--aperiodicRri=10"
        ],
    }
    return arms

def generate_stageB_arms(h4_rules=("nearest",), h4_cand_max_fail=0.5, kuo_objectives=("mean",)):
    """Stage B: Helper selection (18 arms).
    R4: H1..H3
    R5: H1..H3
    R6: H1..H3 + H4..H6 x 3 windows (500, 1000, 3000 ms)
    All with S1, 2 retx (maxNumTx=3).
    """
    arms = {}
    # R4
    arms["R4_S1_H1"] = ["--retx=R4", "--helperSched=S1", "--helperSelect=static", "--helper=4", "--maxNumTx=3"]
    arms["R4_S1_H2"] = ["--retx=R4", "--helperSched=S1", "--helperSelect=kuo", "--helper=4", "--maxNumTx=3"]
    arms["R4_S1_H3"] = ["--retx=R4", "--helperSched=S1", "--helperSelect=map", "--helper=4", "--maxNumTx=3"]
    # R5
    arms["R5_S1_H1"] = ["--retx=R5", "--helperSched=S1", "--helperSelect=static", "--helper=4", "--maxNumTx=3"]
    arms["R5_S1_H2"] = ["--retx=R5", "--helperSched=S1", "--helperSelect=kuo", "--helper=4", "--maxNumTx=3"]
    arms["R5_S1_H3"] = ["--retx=R5", "--helperSched=S1", "--helperSelect=map", "--helper=4", "--maxNumTx=3"]
    # R6
    arms["R6_S1_H1"] = ["--retx=R6", "--helperSched=S1", "--helperSelect=static", "--helper=4", "--maxNumTx=3"]
    arms["R6_S1_H2"] = ["--retx=R6", "--helperSched=S1", "--helperSelect=kuo", "--helper=4", "--maxNumTx=3"]
    arms["R6_S1_H3"] = ["--retx=R6", "--helperSched=S1", "--helperSelect=map", "--helper=4", "--maxNumTx=3"]
    for w in [500, 1000, 3000]:
        arms[f"R6_S1_H4_w{w}"] = [
            "--retx=R6", "--helperSched=S1", "--helperSelect=harq",
            f"--hstatWindowMs={w}", "--helper=4", "--maxNumTx=3"
        ]
        arms[f"R6_S1_H5_w{w}"] = [
            "--retx=R6", "--helperSched=S1", "--helperSelect=harqKuo",
            f"--hstatWindowMs={w}", "--helper=4", "--maxNumTx=3"
        ]
        arms[f"R6_S1_H6_w{w}"] = [
            "--retx=R6", "--helperSched=S1", "--helperSelect=harqMap",
            f"--hstatWindowMs={w}", "--helper=4", "--maxNumTx=3"
        ]
    # extra H4 candidate rules (README "H4 rules"): arms R6_S1_H4-<rule>_w<w>; the default
    # rule "nearest" keeps the names above, so completed runs stay recognised
    for rule in h4_rules:
        if rule == "nearest":
            continue
        for w in [500, 1000, 3000]:
            flags = ["--retx=R6", "--helperSched=S1", "--helperSelect=harq",
                     f"--hstatWindowMs={w}", f"--hstatRule={rule}", "--helper=4", "--maxNumTx=3"]
            if rule == "nearestReliable":
                flags.append(f"--hstatCandMaxFail={h4_cand_max_fail:g}")
            arms[f"R6_S1_H4-{rule}_w{w}"] = flags
    # Kuo objective variants (eq. 13 maxmean is the default; "min" = Kuo's maxmin benchmark):
    # every arm whose selector evaluates Kuo's GPRR_i (kuo, map, harqKuo, harqMap)
    for obj in kuo_objectives:
        if obj == "mean":
            continue
        for name, flags in list(arms.items()):
            if any(f in flags for f in ("--helperSelect=kuo", "--helperSelect=map",
                                         "--helperSelect=harqKuo", "--helperSelect=harqMap")):
                arms[f"{name}-max{obj}"] = flags + [f"--kuoObjective={obj}"]
    return arms

def generate_stageC_arms(best_h_map=None):
    """Stage C: Helper scheduling (12 arms).
    S2, S3 x R4, R5, R6 x best H of stage B x {1, 2} retx.
    """
    if best_h_map is None:
        best_h_map = {"R4": "map", "R5": "map", "R6": "harqMap"}
    arms = {}
    for sched in ["S2", "S3"]:
        for retx in ["R4", "R5", "R6"]:
            h = best_h_map.get(retx, "map")
            for n_retx in [1, 2]:
                max_tx = n_retx + 1
                arm_name = f"{retx}_{sched}_{h}_tx{max_tx}"
                arms[arm_name] = [
                    f"--retx={retx}", f"--helperSched={sched}",
                    f"--helperSelect={h}", "--helper=4", f"--maxNumTx={max_tx}"
                ]
    return arms

# ---------------------------------------------------------------------------
# Verification and checking
# ---------------------------------------------------------------------------

def check_run_csv(run_csv, expected_flags):
    """Check invariants of a completed run."""
    with open(run_csv) as f:
        r = next(csv.DictReader(f))
    bad = []
    # Dedicated carrier and one-TB invariants
    if abs(float(r.get("sl_slot_frac", 0.0)) - 1.0) > 1e-4:
        bad.append(f"sl_slot_frac={r.get('sl_slot_frac')}")
    if int(float(r.get("tb_max_b", 0))) != 1333:
        bad.append(f"tb_max_b={r.get('tb_max_b')}")
    if int(float(r.get("leader_pkt_multi_tb_unqueued", -1))) != 0 and int(float(r.get("nbg", 0))) == 0:
        bad.append(f"leader_pkt_multi_tb_unqueued={r.get('leader_pkt_multi_tb_unqueued')}")
    if abs(float(r.get("rlc_reordering_ms", -1.0)) - RLC_MS) > 1e-9:
        bad.append(f"rlc_reordering_ms={r.get('rlc_reordering_ms')}")
    if int(float(r.get("relay_by_unchosen", 0))) != 0:
        bad.append(f"relay_by_unchosen={r.get('relay_by_unchosen')}")
    if r.get("harq_mode") == "opt1":
        if int(float(r.get("leader_retx_without_nack", 0))) != 0:
            bad.append(f"leader_retx_without_nack={r.get('leader_retx_without_nack')}")
    if r.get("harq_mode") == "opt2" and int(float(r.get("helper_ack_rx", 0))) != 0:
        bad.append(f"helper_ack_rx={r.get('helper_ack_rx')}")
    # the selector options the run actually used must be the ones its arm asked for
    want = {"--hstatRule=": ("hstat_rule", "nearest"), "--kuoObjective=": ("kuo_objective", "mean"),
            "--posErrM=": ("pos_err_m", "0")}
    for prefix, (col, default) in want.items():
        val = next((f[len(prefix):] for f in expected_flags if f.startswith(prefix)), default)
        if col in r:
            try:
                same = abs(float(r[col]) - float(val)) < 1e-9
            except ValueError:
                same = r[col] == val
            if not same:
                bad.append(f"{col}={r[col]} (want {val})")
    return bad

# ---------------------------------------------------------------------------
# Runner Execution
# ---------------------------------------------------------------------------

def run_single(cmd, out_prefix, log_file):
    out_dir = Path(out_prefix).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w") as lf:
        proc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, text=True)
    return proc.returncode

def main():
    parser = argparse.ArgumentParser(description="Retransmission campaign runner")
    parser.add_argument("--stage", required=True, choices=["A", "Aprime", "B", "C", "calib", "check"],
                        help="Campaign stage to execute")
    parser.add_argument("--bin", required=True, help="Path to study binary")
    parser.add_argument("--results", required=True, help="Directory to write results")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4, help="Parallel job count")
    parser.add_argument("--seeds", default="1-5", help="Seed range or comma list, e.g. '1-5'")
    parser.add_argument("--maxTime", type=float, default=0.0, help="Override simulation duration")
    parser.add_argument("--h4Rules", default="nearest",
                        help="stage B: H4 candidate rules, comma list of nearest | nearestAhead | "
                             "nearestReliable | reliableAhead (nearest = the base arms)")
    parser.add_argument("--h4CandMaxFail", type=float, default=0.5,
                        help="stage B: nearestReliable max candidate failure rate")
    parser.add_argument("--kuoObjectives", default="mean",
                        help="stage B: Kuo objectives, comma list of mean | min (mean = the base arms)")
    parser.add_argument("--posErrM", type=float, default=1.5,
                        help="CAM position error std dev (m) of the geometric selectors H3 (map) "
                             "and H6 (harqMap); added to every such arm")
    parser.add_argument("--bg", default="", help="restrict background levels, e.g. 'none'")
    parser.add_argument("--scenarios", default="", help="restrict scenarios, e.g. 'highway'")
    parser.add_argument("--ivds", default="", help="restrict IVDs, e.g. '15,30'")
    parser.add_argument("--traffics", default="", help="restrict leader traffic classes")
    parser.add_argument("--armFilter", default="",
                        help="regex: run only the arms whose name matches (all stages)")
    args = parser.parse_args()

    # Parse seeds
    if "-" in args.seeds:
        s0, s1 = args.seeds.split("-", 1)
        seeds = list(range(int(s0), int(s1) + 1))
    else:
        seeds = [int(s) for s in args.seeds.split(",")]

    bg_levels = get_bg_levels()

    # Select arms and cells by stage
    if args.stage == "A":
        arms = generate_stageA_arms()
        scenarios = ["highway", "roundabout"]
        ivds = [5, 15, 20, 30]
        traffics = TRAFFIC_CLASSES
        bg_list = ["none", "moderate", "high"]
    elif args.stage == "Aprime":
        arms = generate_stageAprime_arms()
        scenarios = ["highway", "roundabout"]
        ivds = [30]
        traffics = ["aperiodic1tb"]
        bg_list = ["none", "moderate", "high"]
    elif args.stage == "B":
        arms = generate_stageB_arms(tuple(x for x in args.h4Rules.split(",") if x),
                                    args.h4CandMaxFail,
                                    tuple(x for x in args.kuoObjectives.split(",") if x))
        scenarios = ["highway", "roundabout"]
        ivds = [15, 30]
        traffics = TRAFFIC_CLASSES
        bg_list = ["none", "moderate", "high"]
    elif args.stage == "C":
        arms = generate_stageC_arms()
        scenarios = ["highway", "roundabout"]
        ivds = [15, 30]
        traffics = TRAFFIC_CLASSES
        bg_list = ["none", "moderate", "high"]
    elif args.stage == "calib":
        arms = {"R2_tx3": ["--retx=R2", "--maxNumTx=3"]}
        scenarios = ["highway", "roundabout"]
        ivds = [30]
        traffics = ["periodic1tb"]
        bg_list = ["none", "moderate", "high"]
        seeds = [1]
        if args.maxTime == 0.0:
            args.maxTime = 20.0
    else:
        print("Unknown stage", file=sys.stderr)
        sys.exit(1)

    # optional restriction of the stage's cells (local tests)
    def _restrict(cur, opt, cast=str):
        if not opt:
            return cur
        want = [cast(x) for x in opt.split(",") if x]
        bad = [w for w in want if w not in cur]
        if bad:
            sys.exit(f"not in this stage: {bad} (stage has {cur})")
        return [c for c in cur if c in want]
    bg_list = _restrict(bg_list, args.bg)
    scenarios = _restrict(scenarios, args.scenarios)
    ivds = _restrict(ivds, args.ivds, int)
    traffics = _restrict(traffics, args.traffics)

    # the geometric selectors see positions with the CAM / GNSS error (README, PLAN.md)
    for name, flags in arms.items():
        if any(f in flags for f in ("--helperSelect=map", "--helperSelect=harqMap")) and \
                not any(f.startswith("--posErrM=") for f in flags):
            flags.append(f"--posErrM={args.posErrM:g}")
    if args.armFilter:
        import re
        arms = {k: v for k, v in arms.items() if re.search(args.armFilter, k)}
    print(f"[{args.stage}] {len(arms)} arms x {len(scenarios)} scen x {len(ivds)} ivds x {len(bg_list)} bg x {len(traffics)} traffic x {len(seeds)} seeds")
    total_planned = len(arms) * len(scenarios) * len(ivds) * len(bg_list) * len(traffics) * len(seeds)
    print(f"Total planned runs: {total_planned}")

    tasks = []
    results_root = Path(args.results)

    for arm_name, arm_flags in arms.items():
        arm_dir = results_root / arm_name
        for sc in scenarios:
            for ivd in ivds:
                for bg_name in bg_list:
                    nbg, k = bg_levels[bg_name]
                    for tr in traffics:
                        for seed in seeds:
                            tag = f"{rc.SHORT[sc]}_ivd{ivd}_{tr}_{bg_name}_s{seed}"
                            out_prefix = arm_dir / tag
                            run_csv = arm_dir / f"{tag}_run.csv"
                            log_file = arm_dir / f"{tag}.log"

                            if run_csv.exists():
                                continue # Resumable: skip completed

                            # Trace file
                            tr_path = rc.trace_path(HERE / "traces", sc, ivd, nbg, seed)
                            if not tr_path.exists():
                                print(f"Missing trace {tr_path}, generating...")
                                _, status = rc.make_trace((HERE / "traces", sc, ivd, nbg, seed))
                                if not status == "ok" and not status == "cached":
                                    print(f"Trace generation failed: {status}", file=sys.stderr)
                                    sys.exit(1)

                            cmd = [
                                args.bin,
                                f"--scenario={sc}",
                                f"--mobility={tr_path}",
                                f"--traffic={tr}",
                                f"--seed={seed}",
                                f"--run={seed}",
                                f"--bgFlowsPerCar={k}",
                                f"--out={out_prefix}",
                            ] + BASE_FLAGS + arm_flags

                            # Aperiodic RRI default = 20 ms for aperiodic1tb unless overridden
                            if tr == "aperiodic1tb" and not any(f.startswith("--aperiodicRri=") for f in arm_flags):
                                cmd.append("--aperiodicRri=20")

                            if sc == "roundabout":
                                cmd.append(f"--sumoNet={HERE}/sumo/roundabout/roundabout.net.xml")

                            # Highway cut to 53s per PLAN.md
                            if sc == "highway":
                                sim_time = 53.0 if args.maxTime == 0.0 else args.maxTime
                                cmd.append(f"--maxTime={sim_time}")
                            elif args.maxTime > 0.0:
                                cmd.append(f"--maxTime={args.maxTime}")

                            tasks.append((cmd, str(out_prefix), str(log_file), str(run_csv), arm_name))

    print(f"Tasks to run: {len(tasks)} (already finished: {total_planned - len(tasks)})")
    if not tasks:
        print("All runs finished.")
        return

    failed = 0
    done_count = 0
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = {ex.submit(run_single, cmd, out, log): (rcsv, arm) for cmd, out, log, rcsv, arm in tasks}
        for fut in cf.as_completed(futures):
            rcsv, arm = futures[fut]
            rc_code = fut.result()
            done_count += 1
            elapsed = time.time() - t0
            rate = done_count / max(1e-3, elapsed)
            rem_s = (len(tasks) - done_count) / max(1e-3, rate)
            if rc_code != 0:
                print(f"[{done_count}/{len(tasks)}] FAILED run: {rcsv} (exit {rc_code})", file=sys.stderr, flush=True)
                failed += 1
            elif not Path(rcsv).exists():
                print(f"[{done_count}/{len(tasks)}] FAILED run: {rcsv} (exit 0 but no run CSV)", file=sys.stderr, flush=True)
                failed += 1
            else:
                bad = check_run_csv(rcsv, arms[arm])
                if bad:
                    print(f"[{done_count}/{len(tasks)}] CHECK FAILED {rcsv}: {', '.join(bad)}", file=sys.stderr, flush=True)
                    failed += 1
                elif done_count % 5 == 0 or done_count == len(tasks):
                    print(f"[{done_count}/{len(tasks)}] completed ({rate:.2f} runs/s, est rem: {rem_s/3600:.1f}h, {failed} failed)", flush=True)

    print(f"Batch completed: {len(tasks) - failed} ok, {failed} failed.", flush=True)
    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
