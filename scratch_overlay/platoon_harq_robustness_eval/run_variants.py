#!/usr/bin/env python3
"""
run_variants.py -- reduced sweep of platoon_harq_robustness_eval around the
baseline (HANDOFF.md section 11, next steps 1-3), one factor at a time:

  variant    what changes w.r.t. the baseline              program flags
  ---------  --------------------------------------------  ---------------------------------
  mcs14      baseline: MCS 14, shared TDD pool (9 SL slots  (none; reused from --baseline-dir)
             per 20), HARQ Option 1 (NACK-only), 2 retx
  mcs5       MCS 5                                          --mcs=5
  mcs9       MCS 9                                          --mcs=9
  dedicated  dedicated ITS carrier: every slot is SL        --dedicatedCarrier=true
  blind      blind retransmission: always 3 transmissions,  --option1Tx=false --psfchPeriod=0
             no PSFCH in the pool

  cells: scenario {highway, roundabout} x IVD 30 m x traffic {periodic1,
         periodic2, aperiodic1} x N {0, 45, 60} x seeds 1..3

Notes on the flags (checked with pilots, see README "Variants"):
- blind needs psfchPeriod=0. With PSFCH configured, CTTC's resource selection
  still enforces the PSFCH gap between the reserved resources of a TB, so
  --option1Tx=false alone gives 2-3 transmissions per TB (periodic2: never 3).
  Without PSFCH the scheduler takes SelectResourcesForBlindRetransmissions and
  reserves all sl-MaxTxTransNumPSSCH = 3 resources.
- After every run the run CSV is checked against the variant: MCS decoded by
  the members, option1_tx, psfch_period, the SL slot fraction and the RLC
  t-Reordering. A mismatch is logged as "FLAG CHECK FAILED".
- RLC t-Reordering (--rlc-reordering-ms, default 0): NR RLC UM has none, but
  5G-LENA's sidelink reuses LTE RLC UM (100 ms), which holds every packet behind
  a lost one and wrecks prr_pdb / delay; PRR is unaffected. Results go to
  results/variants_rlc<ms>/ with the baseline from results/campaign_rlc<ms>/.
  Baseline cells there with another t-Reordering are not reused (mcs14 runs
  them here instead). The original 100 ms results are results/variants/ and
  results/campaign/:
    python3 run_variants.py --analyze-only --rlc-reordering-ms 100 \
        --results results/variants --baseline-dir results/campaign

The mobility traces are the campaign's (same trace for every variant), so the
variants are compared on identical mobility and background seats.

Resumable: a run is skipped when its <tag>_run.csv exists.

  python3 run_variants.py                       # run everything, then analyze
  python3 run_variants.py --variants blind --seeds 1 --jobs 4
  python3 run_variants.py --dry-run
  python3 run_variants.py --analyze-only

Outputs: results/variants_rlc<ms>/<variant>/<tag>_*  and  .../analysis/
  variants_runs.csv, variants_members.csv, variants_summary.csv (mean and 95 %
  CI over seeds), figv1_prr_vs_density.png, figv2_tail_prr_vs_density.png,
  figv3_tx_per_pkt.png, figv4_offered_load.png
"""

import argparse
import concurrent.futures as cf
import glob
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

SCENARIOS = ["highway", "roundabout"]
IVDS = [30]
TRAFFIC = ["periodic1", "periodic2", "aperiodic1"]
NBG = [0, 45, 60]
SEEDS = [1, 2, 3]

# name -> (legend label, extra program flags, expected run-CSV values)
VARIANTS = {
    "mcs14": ("MCS 14, Option 1 (baseline)", [],
              dict(mcs=14, option1_tx=1, psfch_period=1, sl_slot_frac=0.45)),
    "mcs5": ("MCS 5", ["--mcs=5"],
             dict(mcs=5, option1_tx=1, psfch_period=1, sl_slot_frac=0.45)),
    "mcs9": ("MCS 9", ["--mcs=9"],
             dict(mcs=9, option1_tx=1, psfch_period=1, sl_slot_frac=0.45)),
    "dedicated": ("Dedicated carrier", ["--dedicatedCarrier=true"],
                  dict(mcs=14, option1_tx=1, psfch_period=1, sl_slot_frac=1.0)),
    "blind": ("Blind retx (3 tx)", ["--option1Tx=false", "--psfchPeriod=0"],
              dict(mcs=14, option1_tx=0, psfch_period=0, sl_slot_frac=0.45)),
}
BASELINE = "mcs14"
BIN = None
MAX_TIME = 0.0   # > 0: truncated runs (smoke tests only)
RLC_REORDERING_MS = 0.0   # set in main() from --rlc-reordering-ms


def rlc_matches(run_csv):
    return abs(rc.rlc_reordering_of(run_csv) - RLC_REORDERING_MS) < 1e-9


def baseline_ok(base, tag):
    """A campaign run can stand in for an mcs14 cell only with the same t-Reordering."""
    f = Path(base) / f"{tag}_run.csv"
    return f.exists() and rlc_matches(f)


def tag_of(sc, ivd, tr, nbg, seed):
    return f"{rc.SHORT[sc]}_ivd{ivd}_{tr}_n{nbg}_s{seed}"


def check_flags(run_csv, variant):
    """Compare the run CSV with what the variant's flags must produce."""
    import csv
    with open(run_csv) as f:
        r = next(csv.DictReader(f))
    exp = VARIANTS[variant][2]
    bad = []
    if int(float(r["mcs"])) != exp["mcs"]:
        bad.append(f"mcs={r['mcs']}")
    # columns added with the variants (absent in older campaign CSVs)
    if "mcs_rx_min" in r and int(r["mcs_rx_max"]) >= 0:
        if not int(r["mcs_rx_min"]) == int(r["mcs_rx_max"]) == exp["mcs"]:
            bad.append(f"mcs_rx={r['mcs_rx_min']}-{r['mcs_rx_max']}")
    if "option1_tx" in r and int(r["option1_tx"]) != exp["option1_tx"]:
        bad.append(f"option1_tx={r['option1_tx']}")
    if int(float(r["psfch_period"])) != exp["psfch_period"]:
        bad.append(f"psfch_period={r['psfch_period']}")
    if "sl_slot_frac" in r and abs(float(r["sl_slot_frac"]) - exp["sl_slot_frac"]) > 1e-6:
        bad.append(f"sl_slot_frac={r['sl_slot_frac']}")
    if not rlc_matches(run_csv):
        bad.append(f"rlc_reordering_ms={rc.rlc_reordering_of(run_csv):g}")
    if int(float(r["explicit_ack_rx"])) != 0:   # Option-1 receivers never ACK
        bad.append(f"explicit_ack_rx={r['explicit_ack_rx']}")
    return bad


def run_job(job):
    try:
        return _run_job(job)
    except Exception as e:  # never let one job take the loop down
        variant, sc, ivd, tr, nbg, seed, _, _ = job
        return variant, tag_of(sc, ivd, tr, nbg, seed), f"FAILED exception {e!r}", 0.0


def _run_job(job):
    variant, sc, ivd, tr, nbg, seed, tdir, vdir = job
    tag = tag_of(sc, ivd, tr, nbg, seed)
    if (vdir / f"{tag}_run.csv").exists():
        return variant, tag, "cached", 0.0
    cmd = [str(BIN), f"--scenario={sc}", f"--mobility={rc.trace_path(tdir, sc, ivd, nbg, seed)}",
           f"--traffic={tr}", f"--seed={seed}", "--run=1", f"--out={vdir / tag}"]
    cmd += VARIANTS[variant][1]
    cmd.append(f"--ns3::LteRlcUm::ReorderingTimer={RLC_REORDERING_MS:g}ms")
    if MAX_TIME > 0:
        cmd.append(f"--maxTime={MAX_TIME}")
    if sc == "roundabout":
        cmd.append(f"--sumoNet={rc.NET}")
    env = {k: v for k, v in os.environ.items() if not k.startswith("MAGIC_")}
    t0 = time.time()
    with open(vdir / f"{tag}.log", "w") as log:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=log, env=env, cwd=vdir)
    dt = time.time() - t0
    if r.returncode != 0:
        return variant, tag, f"FAILED rc={r.returncode}", dt
    for suffix in ("_pkt.csv", "_ltx.csv"):
        f = vdir / f"{tag}{suffix}"
        if f.exists():
            with open(f, "rb") as fi, gzip.open(str(f) + ".gz", "wb") as fo:
                shutil.copyfileobj(fi, fo)
            f.unlink()
    bad = check_flags(vdir / f"{tag}_run.csv", variant)
    return variant, tag, ("ok" if not bad else "FLAG CHECK FAILED " + " ".join(bad)), dt


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------
def run_dir(a, variant):
    """Directory holding the variant's runs; the baseline comes from the
    campaign unless a cell is missing there and was run here instead."""
    return Path(a.results) / variant


def collect(a):
    import pandas as pd
    runs, mems = [], []
    for variant in VARIANTS:
        for sc, ivd, tr, nbg, seed in itertools.product(a.scenarios, a.ivds, a.traffic,
                                                        a.nbg, a.seeds):
            tag = tag_of(sc, ivd, tr, nbg, seed)
            d = run_dir(a, variant)
            if variant == BASELINE and not (d / f"{tag}_run.csv").exists():
                d = Path(a.baseline_dir)
            if not (d / f"{tag}_run.csv").exists():
                continue
            if not rlc_matches(d / f"{tag}_run.csv"):
                print(f"skipped {d / tag}: rlc_reordering_ms="
                      f"{rc.rlc_reordering_of(d / f'{tag}_run.csv'):g}, "
                      f"expected {RLC_REORDERING_MS:g}")
                continue
            r = pd.read_csv(d / f"{tag}_run.csv")
            m = pd.read_csv(d / f"{tag}_member.csv")
            for x in (r, m):
                x["variant"] = variant
            runs.append(r)
            mems.append(m)
    if not runs:
        return None, None
    runs = pd.concat(runs, ignore_index=True)
    mems = pd.concat(mems, ignore_index=True)
    for x in (runs, mems):
        x["ivd_m"] = x["ivd_m"].astype(float).astype(int)
        x["nbg"] = x["nbg"].astype(int)
    mems["member"] = mems["member"].astype(str)
    return runs, mems


def analyze(a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import analyze as an  # palette, rcParams and ci95 of the campaign analysis

    out = Path(a.results) / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    runs, mems = collect(a)
    if runs is None:
        print("no results to analyze")
        return
    runs.to_csv(out / "variants_runs.csv", index=False)
    mems.to_csv(out / "variants_members.csv", index=False)

    tail = str(max(int(m) for m in mems.member.unique() if m.isdigit()))
    per = mems[mems.member.str.isdigit()]
    memb_run = (per.groupby(["variant", "scenario", "ivd_m", "traffic", "nbg", "seed"])
                .agg(prr_pdb_mean=("prr_pdb", "mean")).reset_index())
    tail_run = (per[per.member == tail]
                .groupby(["variant", "scenario", "ivd_m", "traffic", "nbg", "seed"])
                .agg(prr_tail=("prr", "mean"), prr_pdb_tail=("prr_pdb", "mean")).reset_index())
    keys = ["variant", "scenario", "ivd_m", "traffic", "nbg"]
    R = runs.merge(memb_run, on=keys + ["seed"], how="left").merge(
        tail_run, on=keys + ["seed"], how="left")
    metrics = ["prr_mean", "prr_pdb_mean", "prr_min", "prr_tail", "prr_pdb_tail",
               "collision_loss_rate", "channel_loss_rate", "other_loss_rate", "cbr",
               "cbr_occupied", "cbr_leader", "leader_tx_coll_rate", "tx_per_pkt", "retx_ratio", "wall_s"]
    rows = []
    for k, g in R.groupby(keys):
        r = dict(zip(keys, k))
        r["n_seeds"] = len(g)
        for m in metrics:
            if m not in g:          # e.g. cbr_occupied in the older baseline CSVs
                r[m], r[m + "_ci"] = float("nan"), float("nan")
                continue
            r[m] = g[m].mean()
            r[m + "_ci"] = an.ci95(g[m])
        rows.append(r)
    S = pd.DataFrame(rows)
    order = {v: i for i, v in enumerate(VARIANTS)}
    S = S.sort_values(["scenario", "traffic", "nbg", "variant"],
                      key=lambda c: c.map(order) if c.name == "variant" else c)
    S.to_csv(out / "variants_summary.csv", index=False)

    # fixed categorical order (dataviz reference palette slots 1-5); marker and
    # line style as secondary encoding (slots 3-5 are below 3:1 on white)
    style = {
        "mcs14": ("#2a78d6", "-", "o"),
        "mcs5": ("#eb6834", "--", "s"),
        "mcs9": ("#1baf7a", "-.", "^"),
        "dedicated": ("#eda100", ":", "D"),
        "blind": ("#e87ba4", (0, (5, 1, 1, 1)), "v"),
    }

    def grid(metric, ylabel, fname, title, ylim=None):
        scs = [s for s in a.scenarios if s in set(S.scenario)]
        trs = [t for t in a.traffic if t in set(S.traffic)]
        fig, axes = plt.subplots(len(trs), len(scs), figsize=(3.6 * len(scs) + 1.2,
                                                              2.6 * len(trs) + 0.6),
                                 sharex=True, sharey=True, squeeze=False)
        for i, tr in enumerate(trs):
            for j, sc in enumerate(scs):
                ax = axes[i, j]
                for v in VARIANTS:
                    d = S[(S.variant == v) & (S.scenario == sc) & (S.traffic == tr)
                          & (S.ivd_m == a.ivds[0])].sort_values("nbg")
                    if d.empty:
                        continue
                    c, ls, mk = style[v]
                    ax.errorbar(d.nbg, d[metric], yerr=d[metric + "_ci"], color=c, ls=ls,
                                marker=mk, capsize=2, elinewidth=1, label=VARIANTS[v][0])
                if i == 0:
                    ax.set_title(sc, color=an.INK, fontsize=10)
                if j == 0:
                    ax.set_ylabel(f"{tr}\n{ylabel}")
                if i == len(trs) - 1:
                    ax.set_xlabel("background vehicles in the area")
                ax.set_xticks(a.nbg)
                if ylim:
                    ax.set_ylim(*ylim)
        h, l = axes[0, 0].get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", ncol=min(5, len(l)), fontsize=8,
                   bbox_to_anchor=(0.5, 0.0))
        fig.suptitle(title, color=an.INK, fontsize=11, x=0.01, ha="left")
        fig.tight_layout(rect=(0, 0.06, 1, 0.97))
        fig.savefig(out / fname)
        plt.close(fig)

    ivd = a.ivds[0]
    grid("prr_mean", "PRR (mean of members)", "figv1_prr_vs_density.png",
         f"Average PRR per member vs background density, IVD {ivd} m (95 % CI over seeds)",
         (0, 1.02))
    grid("prr_tail", f"PRR of member {tail}", "figv2_tail_prr_vs_density.png",
         f"Tail member PRR vs background density, IVD {ivd} m", (0, 1.02))
    grid("tx_per_pkt", "leader PSSCH tx per packet", "figv3_tx_per_pkt.png",
         f"Leader transmissions per packet (TBs x HARQ transmissions), IVD {ivd} m")
    # cbr sums all transmissions (collided ones twice): offered load, may exceed 1.
    # It is the only busy-ratio column the reused baseline has, so it is the one
    # plotted; cbr_occupied (each subchannel-slot once) is in variants_summary.csv.
    grid("cbr", "offered load / pool", "figv4_offered_load.png",
         f"Offered load (subchannel-slots of all PSSCHs / available, overlaps counted twice), "
         f"IVD {ivd} m")

    cols = ["variant", "scenario", "traffic", "nbg", "n_seeds", "prr_mean", "prr_mean_ci",
            "prr_tail", "prr_pdb_mean", "cbr", "tx_per_pkt", "collision_loss_rate",
            "channel_loss_rate"]
    with pd.option_context("display.width", 200, "display.max_rows", 500,
                           "display.float_format", "{:.3f}".format):
        print(S[cols].to_string(index=False))
    print(f"\nwritten to {out}")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS),
                    help="variants to run; mcs14 only runs the cells that --baseline-dir "
                         "lacks (or has with another t-Reordering)")
    ap.add_argument("--seeds", type=int, nargs="+", default=SEEDS)
    ap.add_argument("--jobs", type=int, default=7)
    ap.add_argument("--scenarios", nargs="+", default=SCENARIOS)
    ap.add_argument("--ivds", type=int, nargs="+", default=IVDS)
    ap.add_argument("--traffic", nargs="+", default=TRAFFIC)
    ap.add_argument("--nbg", type=int, nargs="+", default=NBG)
    ap.add_argument("--traces", default=str(HERE / "traces"))
    ap.add_argument("--rlc-reordering-ms", type=float, default=0.0,
                    help="RLC UM t-Reordering (ms); 0 = NR behaviour, 100 = LTE/ns-3 default")
    ap.add_argument("--results", default=None, help="default: results/variants_rlc<ms>")
    ap.add_argument("--baseline-dir", default=None, help="default: results/campaign_rlc<ms>")
    ap.add_argument("--bin", default=None, help="ns-3 binary (default: newest in build/)")
    ap.add_argument("--max-time", type=float, default=0.0,
                    help="truncate every run at this time (s); smoke tests only, use a "
                         "separate --results directory")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--analyze-only", action="store_true")
    a = ap.parse_args()
    global RLC_REORDERING_MS
    RLC_REORDERING_MS = a.rlc_reordering_ms
    ms = f"{a.rlc_reordering_ms:g}"
    if a.results is None:
        a.results = str(HERE / f"results/variants_rlc{ms}")
    if a.baseline_dir is None:
        a.baseline_dir = str(HERE / f"results/campaign_rlc{ms}")
    print(f"RLC t-Reordering: {ms} ms, results: {a.results}, baseline: {a.baseline_dir}")

    if a.analyze_only:
        analyze(a)
        return

    global BIN, MAX_TIME
    MAX_TIME = a.max_time
    if a.bin:
        BIN = Path(a.bin)
    else:
        cands = sorted(rc.BIN_DIR.glob("ns3*-platoon_harq_robustness_eval-*"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            sys.exit(f"no binary in {rc.BIN_DIR}; build it first or pass --bin")
        BIN = cands[0]
    print(f"binary: {BIN}")

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

    # ---- 1. mobility traces (normally all cached from the campaign)
    tjobs = [(tdir, sc, ivd, nbg, seed) for sc, ivd, nbg, seed in
             itertools.product(a.scenarios, a.ivds, a.nbg, a.seeds)]
    if not a.dry_run:
        with cf.ProcessPoolExecutor(max_workers=a.jobs) as ex:
            for path, st in ex.map(rc.make_trace, tjobs):
                if st != "cached":
                    log(f"trace {Path(path).name}: {st}")
                if st.startswith("FAILED"):
                    sys.exit(1)

    # ---- 2. ns-3 runs: seed by seed (a complete replicate first), most expensive first
    base = Path(a.baseline_dir)
    jobs = []
    for variant, sc, ivd, tr, nbg, seed in itertools.product(a.variants, a.scenarios, a.ivds,
                                                             a.traffic, a.nbg, a.seeds):
        tag = tag_of(sc, ivd, tr, nbg, seed)
        if variant == BASELINE and baseline_ok(base, tag):
            continue
        vdir = root / variant
        vdir.mkdir(exist_ok=True)
        jobs.append((variant, sc, ivd, tr, nbg, seed, tdir, vdir))
    jobs.sort(key=lambda j: (j[5], -j[4], j[1] != "highway", j[3] != "periodic2"))
    todo = [j for j in jobs if not (j[7] / f"{tag_of(*j[1:6])}_run.csv").exists()]
    log(f"variants {a.variants}: {len(jobs)} runs, {len(todo)} to do, {a.jobs} parallel")
    if a.dry_run:
        for j in todo[:12]:
            print(j[:6], " ".join(VARIANTS[j[0]][1]))
        return

    done, failed, t0 = 0, 0, time.time()
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = [ex.submit(run_job, j) for j in todo]
        for f in cf.as_completed(futs):
            variant, tag, st, dt = f.result()
            done += 1
            failed += st != "ok" and st != "cached"
            log(f"{done}/{len(todo)} {variant}/{tag}: {st} ({dt:.0f}s)  "
                f"elapsed {(time.time() - t0) / 60:.1f} min")
    log(f"variant sweep finished, {failed} not ok")
    analyze(a)


if __name__ == "__main__":
    main()
