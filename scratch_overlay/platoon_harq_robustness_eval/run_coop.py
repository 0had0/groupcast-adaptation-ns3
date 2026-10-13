#!/usr/bin/env python3
"""
run_coop.py -- cooperative HARQ comparison (branch coop-harq-nr): leader-only vs
helper on the leader's resources vs helper on its own resources.

  arm        program flags                               what retransmits
  ---------  ------------------------------------------  ----------------------------------
  none       --helperMode=none                            the leader (Option 1, 2 retx)
  leaderRes  --helperMode=leaderRes --helper=H            the helper, in the leader's
                                                          reserved retx slots
  ownRes     --helperMode=ownRes --helper=H               the helper, on resources it
                                                          selects itself (Mode 2)

  cells: roundabout IVD 30 m N = 0 (blockage), highway IVD 30 m N = 45 / 60
         (collisions) x traffic {periodic1, periodic2} x seeds 1..10
  helper H = member 4 by default (--helper); the leader-only arm also gets --helper
  so the sensing attribution reports what the helper would have sensed.

Every run uses RLC t-Reordering 0 (NR RLC UM behaviour, see README) and is
checked afterwards: helper mode / member / t-Reordering in the run CSV, no
explicit ACK (Option 1), and for the helper arms relay_without_nack == 0 (no
relay unless a member sent a NACK on the previous transmission of the TB; runs
made before 2026-10-12 judged it on the NACKs the leader received, which flags
the relays after a NACK the leader missed) and relay_without_decode == 0. A
mismatch is logged as "FLAG CHECK FAILED".

Resumable (a run is skipped when its <tag>_run.csv exists).

  python3 run_coop.py --jobs 7
  python3 run_coop.py --dry-run
  python3 run_coop.py --analyze-only

Outputs: results/coop_rlc0/<arm>/<tag>_*  and  results/coop_rlc0/analysis/:
  coop_runs.csv, coop_members.csv, coop_summary.csv (mean and 95 % CI over seeds),
  figc1_prr.png, figc2_tail_prr_pdb.png, figc3_collision_sensing.png,
  coop_latency_cdf.csv, figc4_latency.png, figc5_reliability_latency.png, and for runs
  made since 2026-10-12 coop_why_summary.csv, figc6_why_both_sensed.png, figc6b_why_all.png
  (why collided attempts collided although the UEs sense; see the program's _coll.csv)

  python3 run_coop.py --cells roundabout:45 roundabout:60 --results results/coop_rbt_rlc0

Latency = application end-to-end delay of a packet at a member: generation at the
leader -> first delivery to the member's application (queueing for the resource,
HARQ retransmissions and relayed copies included), read from <tag>_pkt.csv.gz (the
program writes it with 2 significant digits: 0.1 ms below 10 ms, 1 ms above).
Percentiles are over delivered packets only; the reliability-latency curve
P(delivered within t) counts every packet (a lost packet never arrives), so it
ends at the PRR.
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
sys.path.insert(0, str(HERE))
import run_campaign as rc  # noqa: E402  (trace generation, binary location)

# arm -> (label, program --helperMode, extra program flags)
ARMS = {
    "none": ("Leader only", "none", []),
    # none4 = leader-only with sl-MaxTxTransNumPSSCH = 4: not feasible in this pool
    # (T2 = 33 slots, 9 SL slots / 20, PSFCH + processing gaps: the scheduler never
    # finds a 4th resource, pilot 2026-10-12: 138 x 3, 41 x 2, 0 x 4); kept for
    # completeness, not in the follow-up campaign
    "none4": ("Leader only, 4 tx", "none", ["--maxNumTx=4"]),
    "leaderRes": ("Helper, leader's resources", "leaderRes", []),
    "ownRes": ("Helper, own resources", "ownRes", []),
    "split": ("Split: leader 2 tx + helper", "split", []),
    "additive": ("Additive: leader + helper copy", "additive", ["--relayBudget=1"]),
    # oracle inter-UE coordination on top of leaderRes: an UPPER BOUND, not IUC (the
    # helper's sensing exclusions reach the leader's selection with zero delay, no loss
    # and no signalling); README "Hidden-node test"
    "leaderResIuc": ("Helper, leader's resources + oracle IUC (upper bound)", "leaderResIuc",
                     []),
    # per-TB helper selection (README "Per-TB helper selection"): the leader hands its
    # reserved retransmission slots to one member per TB; static member 4 = Kuo's "middle
    # Tx"; kuo = max-mean GPRR with distance-only LOS probability; map = the same with the
    # building map; Nack = Option-1 NACK-triggered relaying, Blind = every handed slot
    "s4Nack": ("Static helper 4, NACK", "leaderRes", []),
    "s4Blind": ("Static helper 4, blind", "leaderRes", ["--helperBlind=true"]),
    "kuoNack": ("Kuo selection, NACK", "leaderRes", ["--helperSelect=kuo"]),
    "kuoBlind": ("Kuo selection, blind", "leaderRes",
                 ["--helperSelect=kuo", "--helperBlind=true"]),
    "mapNack": ("Map-aware selection, NACK", "leaderRes", ["--helperSelect=map"]),
    "mapBlind": ("Map-aware selection, blind", "leaderRes",
                 ["--helperSelect=map", "--helperBlind=true"]),
}
DYN_ARMS = ["none", "s4Nack", "s4Blind", "kuoNack", "kuoBlind", "mapNack", "mapBlind"]
DEFAULT_ARMS = ["none", "leaderRes", "ownRes"]  # the 2026-10-11 comparison
SHORT_ARM = {"none": "LO", "none4": "LO4", "leaderRes": "LR", "ownRes": "OR", "split": "SP",
             "additive": "AD", "leaderResIuc": "LRI", "s4Nack": "S4N", "s4Blind": "S4B",
             "kuoNack": "KN", "kuoBlind": "KB", "mapNack": "MN", "mapBlind": "MB"}
CELLS = [("roundabout", 30, 0), ("highway", 30, 45), ("highway", 30, 60)]  # default design
# further cells selectable with --cells (roundabout with background: is the helper's
# sensing different when buildings, not distance, separate it from the leader?)
ALL_CELLS = CELLS + [("roundabout", 30, 45), ("roundabout", 30, 60)]
TRAFFIC = ["periodic1", "periodic2"]
SEEDS = list(range(1, 11))
BIN = None
HELPER = 4
RLC_MS = 0.0
PDB_MS = {"periodic1": 100.0, "periodic2": 10.0}  # the program's packet delay budgets
LAT_STEP_MS = {"periodic1": 0.5, "periodic2": 0.1}  # grid of the reliability-latency curve


def tag_of(sc, ivd, tr, nbg, seed):
    return f"{rc.SHORT[sc]}_ivd{ivd}_{tr}_n{nbg}_s{seed}"


def check(run_csv, arm, helper=None):
    helper = HELPER if helper is None else helper
    with open(run_csv) as f:
        r = next(csv.DictReader(f))
    bad = []
    if r.get("helper_mode") != ARMS[arm][1]:
        bad.append(f"helper_mode={r.get('helper_mode')}")
    if int(float(r.get("helper", -1))) != helper:
        bad.append(f"helper={r.get('helper')}")
    if abs(rc.rlc_reordering_of(run_csv) - RLC_MS) > 1e-9:
        bad.append(f"rlc_reordering_ms={r.get('rlc_reordering_ms')}")
    if int(float(r["explicit_ack_rx"])) != 0:
        bad.append(f"explicit_ack_rx={r['explicit_ack_rx']}")
    want_tx = 4 if arm == "none4" else 3
    if int(float(r.get("max_num_tx", want_tx))) != want_tx:
        bad.append(f"max_num_tx={r.get('max_num_tx')}")
    must0 = ["leader_retx_without_nack"]  # Option 1: a leader retx only after a NACK
    if ARMS[arm][1] not in ("none", "off"):
        must0 += ["relay_without_decode", "relay_into_reused_harq"]
    if arm == "split":
        must0 += ["relay_without_nack", "relay_before_leader_retx"]
    elif arm == "additive":
        # the copy follows the NACK on the leader's initial transmission; when the
        # leader's own retransmission comes first and reaches everyone, the copy is
        # redundant (relay_without_nack counts those, by design not 0)
        must0 += ["relay_without_initial_nack"]
    elif ARMS[arm][1] not in ("none", "off") and "--helperBlind=true" not in ARMS[arm][2]:
        must0 += ["relay_without_nack"]  # blind arms relay without a NACK by design
    if any(f.startswith("--helperSelect=") for f in ARMS[arm][2]):
        must0 += ["relay_by_unchosen"]  # a copy only by the TB's chosen helper
        want_sel = [f.split("=", 1)[1] for f in ARMS[arm][2] if f.startswith("--helperSelect=")][0]
        if r.get("helper_select") != want_sel:
            bad.append(f"helper_select={r.get('helper_select')}")
    if "helper_blind" in r and int(float(r["helper_blind"])) != int("--helperBlind=true" in ARMS[arm][2]):
        bad.append(f"helper_blind={r['helper_blind']}")
    for k in must0:
        if k in r and int(float(r[k])) != 0:
            bad.append(f"{k}={r[k]}")
    max_leader = {"split": 2, "leaderRes": 1, "ownRes": 1, "leaderResIuc": 1}.get(
        ARMS[arm][1] if arm not in ("none", "none4", "additive") else arm, want_tx)
    if "leader_tx_per_tb_max" in r and int(float(r["leader_tx_per_tb_max"])) > max_leader:
        bad.append(f"leader_tx_per_tb_max={r['leader_tx_per_tb_max']}")
    return bad


def run_job(job):
    """job = (arm, scenario, ivd, traffic, nbg, seed, trace dir, arm dir[, opts]);
    opts (dict, optional): helper, tag (output tag), extra (program flags)."""
    arm, sc, ivd, tr, nbg, seed, tdir, adir = job[:8]
    opts = job[8] if len(job) > 8 else {}
    helper = opts.get("helper", HELPER)
    tag = opts.get("tag", tag_of(sc, ivd, tr, nbg, seed))
    tdir, adir = Path(tdir).resolve(), Path(adir).resolve()
    try:
        adir.mkdir(parents=True, exist_ok=True)
        if (adir / f"{tag}_run.csv").exists():
            return arm, tag, "cached", 0.0
        cmd = [str(BIN), f"--scenario={sc}",
               f"--mobility={rc.trace_path(tdir, sc, ivd, nbg, seed)}", f"--traffic={tr}",
               f"--seed={seed}", "--run=1", f"--out={adir / tag}",
               f"--helperMode={ARMS[arm][1]}", f"--helper={helper}",
               f"--ns3::LteRlcUm::ReorderingTimer={RLC_MS:g}ms"]
        cmd += list(ARMS[arm][2]) + list(opts.get("extra", []))
        if sc == "roundabout":
            cmd.append(f"--sumoNet={rc.NET}")
        env = {k: v for k, v in os.environ.items() if not k.startswith("MAGIC_")}
        t0 = time.time()
        with open(adir / f"{tag}.log", "w") as log:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=log, env=env, cwd=adir)
        dt = time.time() - t0
        if r.returncode != 0:
            return arm, tag, f"FAILED rc={r.returncode}", dt
        for suffix in ("_pkt.csv", "_ltx.csv", "_htx.csv", "_coll.csv", "_div.csv", "_nack.csv",
                       "_use.csv", "_sel.csv", "_att.csv", "_hsel.csv", "_alltx.csv",
                       "_grants.csv"):
            f = adir / f"{tag}{suffix}"
            if f.exists():
                with open(f, "rb") as fi, gzip.open(str(f) + ".gz", "wb") as fo:
                    shutil.copyfileobj(fi, fo)
                f.unlink()
        bad = check(adir / f"{tag}_run.csv", arm, helper)
        return arm, tag, ("ok" if not bad else "FLAG CHECK FAILED " + " ".join(bad)), dt
    except Exception as e:  # never let one job take the loop down
        return arm, tag, f"FAILED exception {e!r}", 0.0


# ---------------------------------------------------------------------------
def latency_of_run(pkt_gz, traffic, tail):
    """Latency statistics of one run (all members and the tail member) and its
    reliability-latency curves P(delivered within t) on the LAT_STEP_MS grid."""
    import numpy as np
    import pandas as pd

    d = pd.read_csv(pkt_gz, usecols=["member", "rx", "delay_ms"])
    grid = np.arange(0.0, 1.2 * PDB_MS[traffic] + 1e-9, LAT_STEP_MS[traffic])
    stats, curves = {}, {}
    for who, sel in (("", d), ("_tail", d[d.member == tail])):
        lat = np.sort(sel.delay_ms[sel.rx == 1].to_numpy(dtype=float))
        n = len(sel)
        ok = len(lat) > 0
        stats[f"lat{who}_mean_ms"] = lat.mean() if ok else np.nan
        for q in (50, 95, 99):
            stats[f"lat{who}_p{q}_ms"] = np.percentile(lat, q) if ok else np.nan
        # tiny tolerance: delays are rounded when written
        curves[who] = (np.searchsorted(lat, grid + 1e-6, side="right") / n if n
                       else np.full(len(grid), np.nan))
    return stats, grid, curves


WHY = ["fresh_dynamic", "fresh_sps", "fresh_platoon", "announced_after", "before_window",
       "retx_not_projected", "below_threshold", "relaxed_threshold", "half_duplex",
       "sci_not_received", "not_excluded", "no_selection"]
WHY_GROUPS = [  # (label, classes, colour)
    ("never announced: new dynamic grant", ["fresh_dynamic"], "#e34948"),
    ("never announced: new SPS reservation", ["fresh_sps"], "#f2a29e"),
    ("never announced: platoon", ["fresh_platoon"], "#bdbbb4"),
    ("announced after our selection", ["announced_after"], "#eda100"),
    ("sensed above threshold, not excluded\n(5G-LENA: no step 6 for dynamic grants)",
     ["not_excluded"], "#4a3aa7"),
    ("announced retx gap, not projected (5G-LENA)", ["retx_not_projected"], "#8f7fe0"),
    ("relaxed threshold", ["relaxed_threshold"], "#1baf7a"),
    ("below threshold", ["below_threshold"], "#7fd6b4"),
    ("half-duplex (selector was transmitting)", ["half_duplex"], "#2a78d6"),
    ("announcing SCI not received", ["sci_not_received"], "#8cbcf0"),
    ("other (before window / no selection)", ["before_window", "no_selection"], "#52514e"),
]


def why_breakdown(R, keys, out, cells, cell_lab, colors):
    """Why collided attempts collided although the UEs sense: shares of the
    (attempt, interferer) pairs of collided attempts that failed at >= 1 member,
    pooled over seeds, for all pairs and for the pairs whose interferer had been
    sensed (any SCI-1A decoded in the window) by both leader and helper.
    Needs runs made since 2026-10-12 (cc_* columns)."""
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import analyze as an

    if f"cc_{WHY[0]}_l" not in R:
        return
    rows = []
    for k, g in R.groupby(keys):
        for subset, pre in (("all", "cc"), ("both_sensed", "ccboth")):
            for role, rl in (("leader", "l"), ("helper", "h")):
                cnt = {w: g[f"{pre}_{w}_{rl}"].sum() for w in WHY}
                tot = sum(cnt.values())
                if tot == 0:
                    continue
                row = dict(zip(keys, k), subset=subset, role=role, pairs=int(tot),
                           n_seeds=len(g))
                for w in WHY:
                    row[w] = cnt[w] / tot
                rows.append(row)
    W = pd.DataFrame(rows)
    W.to_csv(out / "coop_why_summary.csv", index=False)
    if W.empty:
        return

    ccells = [c for c in cells if c[1] > 0]
    if not ccells:
        return
    trs = [t for t in TRAFFIC if t in set(W.traffic)]
    groups = [(arm, role) for arm in ARMS if arm in set(W.arm) for role in ("leader", "helper")
              if not (ARMS[arm][1] == "none" and role == "helper")]
    glab = {(arm, role): f"{SHORT_ARM[arm]}: {role}" for arm in ARMS
            for role in ("leader", "helper")}
    for subset, fname, what in (("both_sensed", "figc6_why_both_sensed.png",
                                 "interferer sensed by both leader and helper"),
                                ("all", "figc6b_why_all.png", "all collided attempts")):
        fig, axes = plt.subplots(len(trs), len(ccells), squeeze=False, sharey=True,
                                 figsize=(max(11.0, 4.8 * len(ccells)), 3.4 * len(trs) + 1.2))
        for i, tr in enumerate(trs):
            for j, (sc, nbg) in enumerate(ccells):
                ax = axes[i, j]
                d = W[(W.subset == subset) & (W.scenario == sc) & (W.nbg == nbg)
                      & (W.traffic == tr)]
                bottom = np.zeros(len(groups))
                npairs = []
                for lab, cls, col in WHY_GROUPS:
                    vals = []
                    for arm, role in groups:
                        x = d[(d.arm == arm) & (d.role == role)]
                        vals.append(x[cls].sum(axis=1).iloc[0] if len(x) else 0.0)
                    vals = np.array(vals)
                    ax.bar(range(len(groups)), vals, 0.7, bottom=bottom, color=col,
                           edgecolor="white", linewidth=0.8, label=lab)
                    bottom += vals
                for arm, role in groups:
                    x = d[(d.arm == arm) & (d.role == role)]
                    npairs.append(int(x.pairs.iloc[0]) if len(x) else 0)
                for q, n in enumerate(npairs):
                    ax.text(q, 1.01, str(n), ha="center", va="bottom", fontsize=6,
                            color=an.INK2)
                ax.set_xticks(range(len(groups)))
                ax.set_xticklabels([glab[g] for g in groups], fontsize=7, rotation=20)
                ax.set_ylim(0, 1.08)
                if i == 0:
                    ax.set_title(cell_lab[(sc, nbg)].replace("\n", " "), color=an.INK,
                                 fontsize=10)
                if j == 0:
                    ax.set_ylabel(f"{tr}\nshare of (attempt, interferer)")
        h, l = axes[0, 0].get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", ncol=3, fontsize=7, bbox_to_anchor=(0.5, 0.0))
        fig.suptitle(f"Why collided attempts collided: {what}\n"
                     "(share of (attempt, interferer) pairs pooled over seeds; count above bars)",
                     color=an.INK, fontsize=11, x=0.01, ha="left")
        fig.tight_layout(rect=(0, 0.13, 1, 0.94))
        fig.savefig(out / fname)
        plt.close(fig)



def cost_and_verdict(S, out, cells, cell_lab, colors, arms):
    """Reliability vs cost (tail PRR within the PDB vs transmissions per packet,
    95 % CI over seeds) and the verdict table: does an arm beat leader-only
    Option 1 (none, and none4 = one more allowed transmission) on tail prr_pdb
    with disjoint 95 % CIs, and at what cost (tx_per_pkt, cbr_occupied)."""
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import analyze as an

    trs = [t for t in TRAFFIC if t in set(S.traffic)]
    fig, axes = plt.subplots(len(trs), len(cells), squeeze=False,
                             figsize=(4.4 * len(cells), 3.4 * len(trs) + 0.8))
    for i, tr in enumerate(trs):
        for j, (sc, nbg) in enumerate(cells):
            ax = axes[i, j]
            for arm in arms:
                d = S[(S.arm == arm) & (S.scenario == sc) & (S.nbg == nbg) & (S.traffic == tr)]
                if not len(d):
                    continue
                r = d.iloc[0]
                ax.errorbar(r.tx_per_pkt, r.prr_pdb_tail, xerr=r.tx_per_pkt_ci,
                            yerr=r.prr_pdb_tail_ci, fmt="o", ms=5, capsize=2,
                            color=colors[arm], label=ARMS[arm][0])
                ax.annotate(SHORT_ARM[arm], (r.tx_per_pkt, r.prr_pdb_tail), fontsize=7,
                            xytext=(4, 3), textcoords="offset points", color=an.INK2)
            ax.grid(color=an.GRID, lw=0.5)
            if i == 0:
                ax.set_title(cell_lab[(sc, nbg)].replace("\n", " "), color=an.INK, fontsize=10)
            if j == 0:
                ax.set_ylabel(f"{tr}\ntail PRR within PDB")
            if i == len(trs) - 1:
                ax.set_xlabel("PSSCH transmissions per packet (leader + helper)")
    h, l = [], []
    for ax in axes.flat:
        for hh, ll in zip(*ax.get_legend_handles_labels()):
            if ll not in l:
                h.append(hh)
                l.append(ll)
    fig.legend(h, l, loc="lower center", ncol=3, fontsize=8, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle("Reliability vs cost per arm (95 % CI over seeds)", color=an.INK, fontsize=11,
                 x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.08, 1, 0.95))
    fig.savefig(out / "figc7_reliability_vs_cost.png")
    plt.close(fig)

    rows = []
    for (sc, nbg, tr), g in S.groupby(["scenario", "nbg", "traffic"]):
        ref = {b: g[g.arm == b].iloc[0] for b in ("none", "none4") if (g.arm == b).any()}
        for _, r in g.iterrows():
            row = {"scenario": sc, "nbg": nbg, "traffic": tr, "arm": r.arm,
                   "n_seeds": r.n_seeds, "prr_pdb_tail": r.prr_pdb_tail,
                   "prr_pdb_tail_ci": r.prr_pdb_tail_ci, "tx_per_pkt": r.tx_per_pkt,
                   "cbr_occupied": r.cbr_occupied}
            for b, x in ref.items():
                if b == r.arm:
                    continue
                lo, hi = r.prr_pdb_tail - r.prr_pdb_tail_ci, r.prr_pdb_tail + r.prr_pdb_tail_ci
                blo, bhi = x.prr_pdb_tail - x.prr_pdb_tail_ci, x.prr_pdb_tail + x.prr_pdb_tail_ci
                row[f"d_tail_vs_{b}"] = r.prr_pdb_tail - x.prr_pdb_tail
                row[f"verdict_vs_{b}"] = ("better" if lo > bhi else "worse" if hi < blo
                                          else "overlap")
                row[f"d_tx_per_pkt_vs_{b}"] = r.tx_per_pkt - x.tx_per_pkt
                row[f"d_cbr_occupied_vs_{b}"] = r.cbr_occupied - x.cbr_occupied
            rows.append(row)
    V = pd.DataFrame(rows)
    V.to_csv(out / "coop_verdict.csv", index=False)
    cols = [c for c in ["scenario", "nbg", "traffic", "arm", "n_seeds", "prr_pdb_tail",
                        "prr_pdb_tail_ci", "d_tail_vs_none", "verdict_vs_none",
                        "d_tail_vs_none4", "verdict_vs_none4", "d_tx_per_pkt_vs_none",
                        "d_cbr_occupied_vs_none"] if c in V]
    with pd.option_context("display.width", 220, "display.max_rows", 200,
                           "display.float_format", "{:.3f}".format):
        print("\nverdict (tail prr_pdb, disjoint 95 % CIs):")
        print(V[cols].to_string(index=False))

def analyze(a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import analyze as an

    root = Path(a.results)
    out = root / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    runs, mems, pkts = [], [], []
    for arm in ARMS:
        for f in sorted((root / arm).glob("*_run.csv")):
            r = pd.read_csv(f)
            m = pd.read_csv(str(f).replace("_run.csv", "_member.csv"))
            for x in (r, m):
                x["arm"] = arm
            runs.append(r)
            mems.append(m)
            pkts.append(Path(str(f).replace("_run.csv", "_pkt.csv.gz")))
    if not runs:
        print("no results")
        return
    runs = pd.concat(runs, ignore_index=True)
    mems = pd.concat(mems, ignore_index=True)
    for x in (runs, mems):
        x["ivd_m"] = x["ivd_m"].astype(float).astype(int)
        x["nbg"] = x["nbg"].astype(int)
    mems["member"] = mems["member"].astype(str)
    runs.to_csv(out / "coop_runs.csv", index=False)
    mems.to_csv(out / "coop_members.csv", index=False)

    keys = ["arm", "scenario", "ivd_m", "traffic", "nbg"]
    per = mems[mems.member.str.isdigit()].copy()
    tail = str(per.member.astype(int).max())
    agg = (per.groupby(keys + ["seed"])
           .agg(prr_pdb_mean=("prr_pdb", "mean"), prr_pdb_min=("prr_pdb", "min")).reset_index())
    tl = (per[per.member == tail].groupby(keys + ["seed"])
          .agg(prr_tail=("prr", "mean"), prr_pdb_tail=("prr_pdb", "mean")).reset_index())
    R = runs.merge(agg, on=keys + ["seed"], how="left").merge(tl, on=keys + ["seed"], how="left")

    # latency per run, from the per-packet files
    lat_rows, cdf_rows = [], []
    for i, pf in enumerate(pkts):
        row = {k: runs.loc[i, k] for k in keys + ["seed"]}
        if not pf.exists():
            print(f"latency: {pf.name} missing, run skipped")
            lat_rows.append(row)
            continue
        st, grid, curves = latency_of_run(pf, row["traffic"], int(tail))
        lat_rows.append({**row, **st})
        for who, c in curves.items():
            cdf_rows.append(pd.DataFrame({**row, "who": "tail" if who else "all",
                                          "t_ms": grid, "p_within": c}))
    L = pd.DataFrame(lat_rows)
    lat_metrics = [c for c in L.columns if c.startswith("lat")]
    R = R.merge(L, on=keys + ["seed"], how="left")
    # reliability-latency curves: mean over seeds
    C = (pd.concat(cdf_rows, ignore_index=True)
         .groupby(keys + ["who", "t_ms"]).p_within.agg(["mean", "count"]).reset_index()
         .rename(columns={"mean": "p_within", "count": "n_seeds"}))
    C.to_csv(out / "coop_latency_cdf.csv", index=False)

    cats = ["neither", "helper_only", "leader_only", "both"]
    for role in ("l", "h"):
        tot = sum(R[f"{role}coll_{c}"] for c in cats)
        for c in cats:
            R[f"{role}coll_{c}_share"] = R[f"{role}coll_{c}"] / tot.where(tot > 0)
    metrics = (["prr_mean", "prr_min", "prr_tail", "prr_pdb_mean", "prr_pdb_tail", "prr_pdb_min",
                "cbr_occupied",
                "collision_loss_rate", "channel_loss_rate", "cbr", "tx_per_pkt",
                "leader_tx_coll_rate", "helper_tx", "helper_tx_coll_rate", "coop_sent",
                "coop_no_nack", "coop_no_resource", "helper_recovered"]
               + [f"{r}coll_{c}_share" for r in ("l", "h") for c in cats] + lat_metrics)
    # runs made since 2026-10-12: NACKs the leader missed, and whether sensing
    # (step 6) removed any candidate of the leader's selections
    if "leader_nack_missed" in R:
        R["leader_nack_missed_rate"] = R.leader_nack_missed / R.leader_tx_nacked.where(
            R.leader_tx_nacked > 0)
        metrics += [m for m in ("leader_nack_missed", "leader_nack_missed_rate",
                                "relay_without_rx_nack", "sel_leader_sensing_excluded",
                                "sel_leader_relaxed", "sel_helper_sensing_excluded") if m in R]
    rows = []
    for k, g in R.groupby(keys):
        row = dict(zip(keys, k))
        row["n_seeds"] = len(g)
        for m in metrics:
            row[m] = g[m].mean()
            row[m + "_ci"] = an.ci95(g[m])
        rows.append(row)
    S = pd.DataFrame(rows)
    order = {v: i for i, v in enumerate(ARMS)}
    S = S.sort_values(["scenario", "nbg", "traffic", "arm"],
                      key=lambda c: c.map(order) if c.name == "arm" else c)
    S.to_csv(out / "coop_summary.csv", index=False)

    colors = {"none": "#2a78d6", "none4": "#8cbcf0", "leaderRes": "#eb6834", "ownRes": "#1baf7a",
              "split": "#4a3aa7", "additive": "#eda100"}
    hatches = {"none": "", "none4": "xx", "leaderRes": "//", "ownRes": "..", "split": "\\\\",
               "additive": "oo"}
    # hidden-node and per-TB helper-selection arms (Nack = solid, Blind = lighter)
    colors.update({"leaderResIuc": "#c0392b", "s4Nack": "#eb6834", "s4Blind": "#f5b48f",
                   "kuoNack": "#1baf7a", "kuoBlind": "#8fdcc0", "mapNack": "#4a3aa7",
                   "mapBlind": "#a99fe0"})
    hatches.update({"leaderResIuc": "xx", "s4Nack": "//", "s4Blind": "//", "kuoNack": "..",
                    "kuoBlind": "..", "mapNack": "\\\\", "mapBlind": "\\\\"})
    arms = [arm for arm in ARMS if arm in set(S.arm)]
    present = set(zip(S.scenario, S.nbg))
    cells = [(sc, nbg) for sc, _, nbg in ALL_CELLS if (sc, nbg) in present]
    cell_lab = {("roundabout", 0): "Roundabout, N = 0\n(blockage)",
                ("roundabout", 45): "Roundabout, N = 45\n(blockage + collisions)",
                ("roundabout", 60): "Roundabout, N = 60\n(blockage + collisions)",
                ("highway", 45): "Highway, N = 45\n(collisions)",
                ("highway", 60): "Highway, N = 60\n(collisions)"}

    def bar_panel(ax, metric, tr):
        w = 0.8 / max(1, len(arms))
        for k, arm in enumerate(arms):
            ys, es = [], []
            for sc, nbg in cells:
                d = S[(S.arm == arm) & (S.scenario == sc) & (S.nbg == nbg) & (S.traffic == tr)]
                ys.append(d[metric].iloc[0] if len(d) else np.nan)
                es.append(d[metric + "_ci"].iloc[0] if len(d) else 0)
            x = np.arange(len(cells)) + (k - (len(arms) - 1) / 2) * w
            ax.bar(x, ys, w * 0.92, yerr=es, color=colors[arm], hatch=hatches[arm],
                   edgecolor="white", linewidth=0.5, capsize=2, label=ARMS[arm][0])
        ax.set_xticks(np.arange(len(cells)))
        ax.set_xticklabels([cell_lab[c] for c in cells], fontsize=8)

    def bars(metric, ylabel, fname, title):
        trs = [t for t in TRAFFIC if t in set(S.traffic)]
        fig, axes = plt.subplots(1, len(trs), figsize=(5.2 * len(trs), 3.8), sharey=True,
                                 squeeze=False)
        for j, tr in enumerate(trs):
            ax = axes[0, j]
            bar_panel(ax, metric, tr)
            ax.set_title(tr, color=an.INK, fontsize=10)
            ax.set_ylim(0, 1.02)
            if j == 0:
                ax.set_ylabel(ylabel)
        axes[0, 0].legend(loc="lower left", fontsize=8)
        fig.suptitle(title, color=an.INK, fontsize=11, x=0.01, ha="left")
        fig.tight_layout(rect=(0, 0, 1, 0.94))
        fig.savefig(out / fname)
        plt.close(fig)

    bars("prr_mean", "PRR (mean of the 8 members)", "figc1_prr.png",
         f"Average PRR per member, IVD 30 m, helper = member {HELPER} (95 % CI over seeds)")
    bars("prr_tail", f"PRR of member {tail}", "figc1b_tail_prr.png",
         f"Tail member PRR, IVD 30 m, helper = member {HELPER}")
    bars("prr_pdb_tail", f"PRR within PDB, member {tail}", "figc2_tail_prr_pdb.png",
         f"Tail member PRR within the PDB (t-Reordering 0), IVD 30 m")

    # sensing attribution of failed, collided attempts (collision cells)
    ccells = [c for c in cells if c[1] > 0]
    trs = [t for t in TRAFFIC if t in set(S.traffic)]
    cat_col = {"neither": "#e34948", "helper_only": "#eda100", "leader_only": "#4a3aa7",
               "both": "#1baf7a"}
    cat_lab = {"neither": "sensed by neither", "helper_only": "helper only",
               "leader_only": "leader only", "both": "both"}
    groups = [(arm, role) for arm in arms for role in ("l", "h")
              if not (ARMS[arm][1] == "none" and role == "h")]
    glab = {(arm, role): f"{SHORT_ARM[arm]}: {'leader' if role == 'l' else 'helper'}"
            for arm in ARMS for role in ("l", "h")}
    if ccells:  # no collision cells (N = 0 only): nothing to attribute
        fig, axes = plt.subplots(len(trs), len(ccells), figsize=(5.0 * len(ccells), 3.2 * len(trs)),
                                 sharey=True, squeeze=False)
        for i, tr in enumerate(trs):
            for j, (sc, nbg) in enumerate(ccells):
                ax = axes[i, j]
                bottom = np.zeros(len(groups))
                for c in cats:
                    vals = []
                    for arm, role in groups:
                        d = S[(S.arm == arm) & (S.scenario == sc) & (S.nbg == nbg) & (S.traffic == tr)]
                        vals.append(d[f"{role}coll_{c}_share"].iloc[0] if len(d) else np.nan)
                    vals = np.nan_to_num(np.array(vals))
                    ax.bar(range(len(groups)), vals, 0.7, bottom=bottom, color=cat_col[c],
                           edgecolor="white", linewidth=1.0, label=cat_lab[c])
                    bottom += vals
                ax.set_xticks(range(len(groups)))
                ax.set_xticklabels([glab[g] for g in groups], fontsize=7, rotation=20)
                ax.set_ylim(0, 1.0)
                if i == 0:
                    ax.set_title(cell_lab[(sc, nbg)].replace("\n", " "), color=an.INK, fontsize=10)
                if j == 0:
                    ax.set_ylabel(f"{tr}\nshare of collided failed attempts")
        h, l = axes[0, 0].get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", ncol=4, fontsize=8, bbox_to_anchor=(0.5, 0.0))
        fig.suptitle("Who had sensed an interferer of a failed, collided attempt "
                     "(SCI-1A decoded in the 100 ms sensing window + PDB before it)",
                     color=an.INK, fontsize=11, x=0.01, ha="left")
        fig.tight_layout(rect=(0, 0.07, 1, 0.94))
        fig.savefig(out / "figc3_collision_sensing.png")
        plt.close(fig)

    # latency of delivered packets: mean (all members) and 95th percentile (tail
    # member), one column per traffic type (own y scale: the PDBs differ 10x)
    rows_l = [("lat_mean_ms", "mean latency (ms),\nall members"),
              ("lat_tail_p95_ms", f"95th pct latency (ms),\nmember {tail}")]
    fig, axes = plt.subplots(len(rows_l), len(trs), figsize=(5.2 * len(trs), 3.4 * len(rows_l)),
                             squeeze=False)
    for i, (metric, ylabel) in enumerate(rows_l):
        for j, tr in enumerate(trs):
            ax = axes[i, j]
            bar_panel(ax, metric, tr)
            top = max(1.0, 1.25 * np.nanmax(S[S.traffic == tr][metric]))
            if tr == "periodic2":
                top = max(top, 1.15 * PDB_MS[tr])
            ax.set_ylim(0, top)
            if PDB_MS[tr] <= top:
                ax.axhline(PDB_MS[tr], color=an.INK2, ls="--", lw=0.8)
                ax.text(ax.get_xlim()[1], PDB_MS[tr], "PDB ", va="bottom", ha="right",
                        fontsize=7, color=an.INK2)
            if i == 0:
                ax.set_title(tr, color=an.INK, fontsize=10)
            if j == 0:
                ax.set_ylabel(ylabel)
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=3, fontsize=8, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle("Latency of delivered packets (generation at the leader -> delivery), "
                 "IVD 30 m (95 % CI over seeds)", color=an.INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.05, 1, 0.95))
    fig.savefig(out / "figc4_latency.png")
    plt.close(fig)

    # reliability-latency: P(packet delivered within t), tail member (solid) and
    # mean over members (dashed); a lost packet never arrives, so the curve
    # ends at the PRR
    fig, axes = plt.subplots(len(trs), len(cells), figsize=(4.6 * len(cells), 3.4 * len(trs)),
                             sharey=True, squeeze=False)
    for i, tr in enumerate(trs):
        ct = C[C.traffic == tr]
        # x range: up to where every curve has (nearly) reached its plateau
        xmax = PDB_MS[tr] * 1.2
        plateau = ct.groupby(keys + ["who"]).p_within.transform("max")
        reached = ct[ct.p_within >= plateau - 1e-3].groupby(keys + ["who"]).t_ms.min()
        if len(reached):
            xmax = min(xmax, max(PDB_MS[tr] * 0.3, 1.15 * reached.max()))
        for j, (sc, nbg) in enumerate(cells):
            ax = axes[i, j]
            for arm in arms:
                for who, ls, lw in (("tail", "-", 1.6), ("all", "--", 1.0)):
                    d = ct[(ct.arm == arm) & (ct.scenario == sc) & (ct.nbg == nbg)
                           & (ct.who == who)]
                    if len(d):
                        ax.plot(d.t_ms, d.p_within, ls=ls, lw=lw, color=colors[arm],
                                label=f"{ARMS[arm][0]}, {'member ' + tail if who == 'tail' else 'all members'}")
            if PDB_MS[tr] <= xmax:
                ax.axvline(PDB_MS[tr], color=an.INK2, ls=":", lw=0.8)
            ax.set_xlim(0, xmax)
            ax.set_ylim(0, 1.0)
            ax.grid(color=an.GRID, lw=0.5)
            if i == 0:
                ax.set_title(cell_lab[(sc, nbg)].replace("\n", " "), color=an.INK, fontsize=10)
            if j == 0:
                ax.set_ylabel(f"{tr}\nP(delivered within t)")
            if i == len(trs) - 1:
                ax.set_xlabel("latency t (ms)")
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=3, fontsize=8, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle("Reliability vs latency (mean over seeds; dotted line = PDB)",
                 color=an.INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.09, 1, 0.95))
    fig.savefig(out / "figc5_reliability_latency.png")
    plt.close(fig)

    cols = keys[:1] + ["scenario", "traffic", "nbg", "n_seeds", "prr_mean", "prr_mean_ci",
                       "prr_tail", "prr_pdb_tail", "tx_per_pkt", "helper_tx", "coop_sent",
                       "coop_no_resource", "helper_recovered", "lat_mean_ms", "lat_p95_ms",
                       "lat_tail_mean_ms", "lat_tail_p95_ms"]
    with pd.option_context("display.width", 220, "display.max_rows", 200,
                           "display.float_format", "{:.3f}".format):
        print(S[cols].to_string(index=False))
    cost_and_verdict(S, out, cells, cell_lab, colors, arms)
    why_breakdown(R, keys, out, cells, cell_lab, colors)
    print(f"\nwritten to {out}")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arms", nargs="+", default=DEFAULT_ARMS, choices=list(ARMS))
    ap.add_argument("--max-time", type=float, default=0.0,
                    help="truncate every run at this time (s): smoke tests only")
    ap.add_argument("--coll-cond", action="store_true",
                    help="log the link condition leader / helper -> interferer of collided "
                         "attempts (--collCond; extra channel-condition queries: use for fresh "
                         "campaigns only, results are not comparable seed by seed with runs "
                         "made without it)")
    ap.add_argument("--write-att", action="store_true",
                    help="--writeAtt=true: per-attempt geometry log (_att.csv)")
    ap.add_argument("--extra", action="append", default=[],
                    help="a further program flag for every run, repeatable, written "
                         "--extra=--helperDecisionMs=10")
    ap.add_argument("--seeds", type=int, nargs="+", default=SEEDS)
    ap.add_argument("--traffic", nargs="+", default=TRAFFIC)
    ap.add_argument("--cells", nargs="+", default=None,
                    help="cells as scenario:nbg, e.g. roundabout:0 highway:45 (default: the "
                         "3 of CELLS; also roundabout:45 roundabout:60)")
    ap.add_argument("--helper", type=int, default=4)
    ap.add_argument("--jobs", type=int, default=7)
    ap.add_argument("--traces", default=str(HERE / "traces"))
    ap.add_argument("--results", default=str(HERE / "results/coop_rlc0"))
    ap.add_argument("--bin", default=None, help="ns-3 binary (default: newest in build/)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--analyze-only", action="store_true")
    a = ap.parse_args()

    global BIN, HELPER
    HELPER = a.helper
    cells = CELLS
    if a.cells:
        want = {(c.split(":")[0], int(c.split(":")[1])) for c in a.cells}
        cells = [c for c in ALL_CELLS if (c[0], c[2]) in want]
        if len(cells) != len(want):
            sys.exit(f"unknown cell in {a.cells}; known: {ALL_CELLS}")
    if a.analyze_only:
        analyze(a)
        return
    if a.bin:
        BIN = Path(a.bin)
    else:
        cands = sorted(rc.BIN_DIR.glob("ns3*-platoon_harq_robustness_eval-*"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            sys.exit(f"no binary in {rc.BIN_DIR}; build it first or pass --bin")
        BIN = cands[0]
    print(f"binary: {BIN}\nresults: {a.results}  helper: member {HELPER}")

    # absolute: every run executes with its arm folder as working directory
    tdir, root = Path(a.traces).resolve(), Path(a.results).resolve()
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

    tjobs = [(tdir, sc, ivd, nbg, seed) for (sc, ivd, nbg), seed in
             itertools.product(cells, a.seeds)]
    if not a.dry_run:
        with cf.ProcessPoolExecutor(max_workers=a.jobs) as ex:
            for path, st in ex.map(rc.make_trace, tjobs):
                if st != "cached":
                    log(f"trace {Path(path).name}: {st}")
                if st.startswith("FAILED"):
                    sys.exit(1)

    jobs = []
    for arm, (sc, ivd, nbg), tr, seed in itertools.product(a.arms, cells, a.traffic, a.seeds):
        adir = root / arm
        adir.mkdir(exist_ok=True)
        jobs.append((arm, sc, ivd, tr, nbg, seed, tdir, adir,
                     {"helper": HELPER,
                      "extra": (["--collCond=true"] if a.coll_cond else [])
                      + (["--writeAtt=true"] if a.write_att else [])
                      + list(a.extra)
                      + ([f"--maxTime={a.max_time:g}"] if a.max_time > 0 else [])}))
    # replicate by replicate, most expensive first
    jobs.sort(key=lambda j: (j[5], -j[4], j[1] != "highway", j[3] != "periodic2"))
    todo = [j for j in jobs if not (j[7] / f"{tag_of(*j[1:6])}_run.csv").exists()]
    log(f"arms {a.arms}: {len(jobs)} runs, {len(todo)} to do, {a.jobs} parallel")
    if a.dry_run:
        for j in todo[:8]:
            print(j[:6])
        return
    done, failed, t0 = 0, 0, time.time()
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = [ex.submit(run_job, j) for j in todo]
        for f in cf.as_completed(futs):
            arm, tag, st, dt = f.result()
            done += 1
            failed += st not in ("ok", "cached")
            log(f"{done}/{len(todo)} {arm}/{tag}: {st} ({dt:.0f}s)  "
                f"elapsed {(time.time() - t0) / 60:.1f} min")
    log(f"coop campaign finished, {failed} not ok")
    analyze(a)


if __name__ == "__main__":
    main()
