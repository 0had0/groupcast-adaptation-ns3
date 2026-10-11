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
explicit ACK (Option 1), and for the helper arms relay_without_nack == 0 and
relay_without_decode == 0. A mismatch is logged as "FLAG CHECK FAILED".

Resumable (a run is skipped when its <tag>_run.csv exists).

  python3 run_coop.py --jobs 7
  python3 run_coop.py --dry-run
  python3 run_coop.py --analyze-only

Outputs: results/coop_rlc0/<arm>/<tag>_*  and  results/coop_rlc0/analysis/:
  coop_runs.csv, coop_members.csv, coop_summary.csv (mean and 95 % CI over seeds),
  figc1_prr.png, figc2_tail_prr_pdb.png, figc3_collision_sensing.png,
  coop_latency_cdf.csv, figc4_latency.png, figc5_reliability_latency.png

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

ARMS = {
    "none": ("Leader only", "none"),
    "leaderRes": ("Helper, leader's resources", "leaderRes"),
    "ownRes": ("Helper, own resources", "ownRes"),
}
CELLS = [("roundabout", 30, 0), ("highway", 30, 45), ("highway", 30, 60)]
TRAFFIC = ["periodic1", "periodic2"]
SEEDS = list(range(1, 11))
BIN = None
HELPER = 4
RLC_MS = 0.0
PDB_MS = {"periodic1": 100.0, "periodic2": 10.0}  # the program's packet delay budgets
LAT_STEP_MS = {"periodic1": 0.5, "periodic2": 0.1}  # grid of the reliability-latency curve


def tag_of(sc, ivd, tr, nbg, seed):
    return f"{rc.SHORT[sc]}_ivd{ivd}_{tr}_n{nbg}_s{seed}"


def check(run_csv, arm):
    with open(run_csv) as f:
        r = next(csv.DictReader(f))
    bad = []
    if r.get("helper_mode") != ARMS[arm][1]:
        bad.append(f"helper_mode={r.get('helper_mode')}")
    if int(float(r.get("helper", -1))) != HELPER:
        bad.append(f"helper={r.get('helper')}")
    if abs(rc.rlc_reordering_of(run_csv) - RLC_MS) > 1e-9:
        bad.append(f"rlc_reordering_ms={r.get('rlc_reordering_ms')}")
    if int(float(r["explicit_ack_rx"])) != 0:
        bad.append(f"explicit_ack_rx={r['explicit_ack_rx']}")
    if arm != "none":
        for k in ("relay_without_nack", "relay_without_decode"):
            if int(float(r[k])) != 0:
                bad.append(f"{k}={r[k]}")
    return bad


def run_job(job):
    arm, sc, ivd, tr, nbg, seed, tdir, adir = job
    tag = tag_of(sc, ivd, tr, nbg, seed)
    try:
        if (adir / f"{tag}_run.csv").exists():
            return arm, tag, "cached", 0.0
        cmd = [str(BIN), f"--scenario={sc}",
               f"--mobility={rc.trace_path(tdir, sc, ivd, nbg, seed)}", f"--traffic={tr}",
               f"--seed={seed}", "--run=1", f"--out={adir / tag}",
               f"--helperMode={ARMS[arm][1]}", f"--helper={HELPER}",
               f"--ns3::LteRlcUm::ReorderingTimer={RLC_MS:g}ms"]
        if sc == "roundabout":
            cmd.append(f"--sumoNet={rc.NET}")
        env = {k: v for k, v in os.environ.items() if not k.startswith("MAGIC_")}
        t0 = time.time()
        with open(adir / f"{tag}.log", "w") as log:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=log, env=env, cwd=adir)
        dt = time.time() - t0
        if r.returncode != 0:
            return arm, tag, f"FAILED rc={r.returncode}", dt
        for suffix in ("_pkt.csv", "_ltx.csv", "_htx.csv", "_coll.csv"):
            f = adir / f"{tag}{suffix}"
            if f.exists():
                with open(f, "rb") as fi, gzip.open(str(f) + ".gz", "wb") as fo:
                    shutil.copyfileobj(fi, fo)
                f.unlink()
        bad = check(adir / f"{tag}_run.csv", arm)
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
           .agg(prr_pdb_mean=("prr_pdb", "mean")).reset_index())
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
    metrics = (["prr_mean", "prr_min", "prr_tail", "prr_pdb_mean", "prr_pdb_tail",
                "collision_loss_rate", "channel_loss_rate", "cbr", "tx_per_pkt",
                "leader_tx_coll_rate", "helper_tx", "helper_tx_coll_rate", "coop_sent",
                "coop_no_nack", "coop_no_resource", "helper_recovered"]
               + [f"{r}coll_{c}_share" for r in ("l", "h") for c in cats] + lat_metrics)
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

    colors = {"none": "#2a78d6", "leaderRes": "#eb6834", "ownRes": "#1baf7a"}
    hatches = {"none": "", "leaderRes": "//", "ownRes": ".."}
    cells = [(sc, nbg) for sc, _, nbg in CELLS]
    cell_lab = {("roundabout", 0): "Roundabout, N = 0\n(blockage)",
                ("highway", 45): "Highway, N = 45\n(collisions)",
                ("highway", 60): "Highway, N = 60\n(collisions)"}

    def bar_panel(ax, metric, tr):
        w = 0.26
        for k, arm in enumerate(ARMS):
            ys, es = [], []
            for sc, nbg in cells:
                d = S[(S.arm == arm) & (S.scenario == sc) & (S.nbg == nbg) & (S.traffic == tr)]
                ys.append(d[metric].iloc[0] if len(d) else np.nan)
                es.append(d[metric + "_ci"].iloc[0] if len(d) else 0)
            x = np.arange(len(cells)) + (k - 1) * w
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
    groups = [(arm, role) for arm in ARMS for role in ("l", "h") if not (arm == "none" and role == "h")]
    glab = {("none", "l"): "LO: leader", ("leaderRes", "l"): "LR: leader",
            ("leaderRes", "h"): "LR: helper", ("ownRes", "l"): "OR: leader",
            ("ownRes", "h"): "OR: helper"}
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
            for arm in ARMS:
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
    print(f"\nwritten to {out}")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arms", nargs="+", default=list(ARMS), choices=list(ARMS))
    ap.add_argument("--seeds", type=int, nargs="+", default=SEEDS)
    ap.add_argument("--traffic", nargs="+", default=TRAFFIC)
    ap.add_argument("--cells", nargs="+", default=None,
                    help="subset of cells as scenario:nbg, e.g. roundabout:0 highway:45")
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
        cells = [c for c in CELLS if (c[0], c[2]) in want]
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

    tdir, root = Path(a.traces), Path(a.results)
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
        jobs.append((arm, sc, ivd, tr, nbg, seed, tdir, adir))
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
