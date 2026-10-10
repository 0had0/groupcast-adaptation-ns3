#!/usr/bin/env python3
"""
analyze.py -- aggregate and plot the platoon_harq_robustness_eval campaign.

  python3 analyze.py [--results results/campaign] [--out results/analysis]

Inputs : <tag>_run.csv, <tag>_member.csv, <tag>_pkt.csv.gz of every run
Outputs: runs_all.csv, members_all.csv, summary.csv (mean and 95 % CI over
         seeds per scenario/ivd/traffic/nbg), per_member_summary.csv and the
         figures fig*.png.
"""

import argparse
import glob
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

HERE = Path(__file__).resolve().parent

# reference palette (dataviz skill): categorical slots in fixed order
C_HWY, C_RBT = "#2a78d6", "#eb6834"          # scenario identity
C_CHAN, C_COLL, C_NOTX = "#2a78d6", "#eb6834", "#1baf7a"   # loss causes
C_LOS, C_NLOSV, C_NLOS = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
SCN = {"highway": ("Highway", C_HWY, "-", "o"), "roundabout": ("Roundabout", C_RBT, "--", "s")}
TRAFFIC = ["periodic1", "periodic2", "aperiodic1"]
IVDS = [5, 15, 30]
KEYS = ["scenario", "ivd_m", "traffic", "nbg"]

plt.rcParams.update({
    "font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2,
    "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
    "lines.linewidth": 2, "lines.markersize": 5, "savefig.dpi": 160,
})


def ci95(x):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if len(x) < 2:
        return 0.0
    return stats.t.ppf(0.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x))


def load(rdir):
    runs = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(str(rdir / "*_run.csv")))],
                     ignore_index=True)
    mem = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(str(rdir / "*_member.csv")))],
                    ignore_index=True)
    for d in (runs, mem):
        d["ivd_m"] = d["ivd_m"].astype(float).astype(int)
        d["nbg"] = d["nbg"].astype(int)
    return runs, mem


def summarize(runs):
    metrics = ["prr_mean", "prr_min", "collision_loss_rate", "channel_loss_rate", "notx_rate",
               "other_loss_rate", "cbr", "cbr_leader", "leader_tx_coll_rate", "pkt_any_coll_rate",
               "tx_per_pkt", "retx_ratio", "bg_in_area_mean", "wall_s"]
    rows = []
    for k, g in runs.groupby(KEYS):
        r = dict(zip(KEYS, k))
        r["n_seeds"] = len(g)
        for m in metrics:
            r[m] = g[m].mean()
            r[m + "_ci"] = ci95(g[m])
        # share of NACK decisions among all HARQ decisions
        dec = g["implicit_ack"] + g["nack_decisions"]
        r["nack_decision_share"] = (g["nack_decisions"] / dec.where(dec > 0)).mean()
        rows.append(r)
    return pd.DataFrame(rows)


def facet(fig_title, fname, plot_cell, sharey=True, ylabel="", legend_cell=(0, 0)):
    fig, axes = plt.subplots(3, 3, figsize=(10.5, 8.2), sharex=True, sharey=sharey)
    for i, ivd in enumerate(IVDS):
        for j, tr in enumerate(TRAFFIC):
            ax = axes[i, j]
            plot_cell(ax, ivd, tr)
            if i == 0:
                ax.set_title(tr, color=INK, fontsize=10)
            if j == 0:
                ax.set_ylabel(f"IVD {ivd} m\n{ylabel}")
            if i == 2:
                ax.set_xlabel("background vehicles in the area")
    axes[legend_cell].legend(loc="lower left", fontsize=8)
    fig.suptitle(fig_title, color=INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(fname)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(HERE / "results/campaign"))
    ap.add_argument("--out", default=str(HERE / "results/analysis"))
    a = ap.parse_args()
    rdir, out = Path(a.results), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    runs, mem = load(rdir)
    runs.to_csv(out / "runs_all.csv", index=False)
    mem.to_csv(out / "members_all.csv", index=False)
    S = summarize(runs)
    S.to_csv(out / "summary.csv", index=False)
    memb = mem[mem.member != "all"].copy()
    memb["member"] = memb.member.astype(int)
    MS = (memb.groupby(KEYS + ["member"])
          .agg(prr=("prr", "mean"), prr_ci=("prr", ci95),
               coll=("collision_loss_rate", "mean"), chan=("channel_loss_rate", "mean"),
               sinr=("mean_sinr_db", "mean"), dist=("mean_dist_m", "mean"),
               los=("frac_los", "mean"), nlosv=("frac_nlosv", "mean"), nlos=("frac_nlos", "mean"))
          .reset_index())
    MS.to_csv(out / "per_member_summary.csv", index=False)
    print(f"{len(runs)} runs, seeds per cell: {S.n_seeds.min()}-{S.n_seeds.max()}")

    # ---------------- fig 1: average PRR per vehicle vs density
    def cell_prr(ax, ivd, tr):
        for sc, (lab, col, ls, mk) in SCN.items():
            d = S[(S.scenario == sc) & (S.ivd_m == ivd) & (S.traffic == tr)].sort_values("nbg")
            if d.empty:
                continue
            ax.errorbar(d.nbg, d.prr_mean, yerr=d.prr_mean_ci, color=col, ls=ls, marker=mk,
                        capsize=2, label=lab)
        ax.set_ylim(0.4, 1.01)
    facet("Average PRR per platoon member (mean over 8 members, 95 % CI over seeds)",
          out / "fig1_prr_vs_density.png", cell_prr, ylabel="PRR")

    # ---------------- fig 2: loss decomposition
    fig, axes = plt.subplots(3, 3, figsize=(11, 8.6), sharey=True)
    for i, ivd in enumerate(IVDS):
        for j, tr in enumerate(TRAFFIC):
            ax = axes[i, j]
            xt, xl = [], []
            for k, sc in enumerate(["highway", "roundabout"]):
                d = S[(S.scenario == sc) & (S.ivd_m == ivd) & (S.traffic == tr)].sort_values("nbg")
                x = np.arange(len(d)) + k * (len(d) + 1)
                b1 = d.channel_loss_rate.values
                b2 = d.collision_loss_rate.values
                b3 = (d.notx_rate + d.other_loss_rate).values
                ax.bar(x, b1, 0.8, color=C_CHAN, edgecolor="white", linewidth=1,
                       label="channel" if k == 0 else None)
                ax.bar(x, b2, 0.8, bottom=b1, color=C_COLL, edgecolor="white", linewidth=1,
                       label="collision" if k == 0 else None)
                ax.bar(x, b3, 0.8, bottom=b1 + b2, color=C_NOTX, edgecolor="white", linewidth=1,
                       label="not transmitted / other" if k == 0 else None)
                xt += list(x)
                xl += [str(n) for n in d.nbg]
                if len(x):
                    ax.text(x.mean(), -0.13, SCN[sc][0], ha="center", va="top",
                            transform=ax.get_xaxis_transform(), color=INK2, fontsize=8)
            ax.set_xticks(xt)
            ax.set_xticklabels(xl, fontsize=7)
            if i == 0:
                ax.set_title(tr, color=INK, fontsize=10)
            if j == 0:
                ax.set_ylabel(f"IVD {ivd} m\nloss rate (1 - PRR)")
    axes[0, 0].legend(loc="upper left", fontsize=8)
    fig.suptitle("Loss decomposition per member-packet: channel vs collision "
                 "(x = background vehicles)", color=INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.01, 1, 0.97))
    fig.savefig(out / "fig2_loss_breakdown.png")
    plt.close(fig)

    # ---------------- fig 3: PRR along the platoon, no background (channel robustness)
    def member_fig(nbg, fname, title):
        fig, axes = plt.subplots(3, 3, figsize=(10.5, 8.2), sharex=True, sharey=True)
        for i, ivd in enumerate(IVDS):
            for j, tr in enumerate(TRAFFIC):
                ax = axes[i, j]
                for sc, (lab, col, ls, mk) in SCN.items():
                    d = MS[(MS.scenario == sc) & (MS.ivd_m == ivd) & (MS.traffic == tr) &
                           (MS.nbg == nbg)].sort_values("member")
                    ax.errorbar(d.member, d.prr, yerr=d.prr_ci, color=col, ls=ls, marker=mk,
                                capsize=2, label=lab)
                ax.set_ylim(0.0, 1.02)
                ax.set_xticks(range(1, 9))
                if i == 0:
                    ax.set_title(tr, color=INK, fontsize=10)
                if j == 0:
                    ax.set_ylabel(f"IVD {ivd} m\nPRR")
                if i == 2:
                    ax.set_xlabel("member (1 = right behind the leader)")
        axes[0, 0].legend(loc="lower left", fontsize=8)
        fig.suptitle(title, color=INK, fontsize=11, x=0.01, ha="left")
        fig.tight_layout(rect=(0, 0, 1, 0.97))
        fig.savefig(fname)
        plt.close(fig)

    member_fig(0, out / "fig3_prr_per_member_nobg.png",
               "PRR along the platoon without background traffic (channel only)")
    member_fig(60, out / "fig3b_prr_per_member_n60.png",
               "PRR along the platoon with 60 background vehicles")

    # ---------------- fig 4: link condition along the platoon (nbg = 0, periodic1)
    fig, axes = plt.subplots(2, 3, figsize=(10.5, 5.4), sharey=True)
    for i, sc in enumerate(["highway", "roundabout"]):
        for j, ivd in enumerate(IVDS):
            ax = axes[i, j]
            d = MS[(MS.scenario == sc) & (MS.ivd_m == ivd) & (MS.traffic == "periodic1") &
                   (MS.nbg == 0)].sort_values("member")
            b = np.zeros(len(d))
            for col, c, lab in [("los", C_LOS, "LOS"), ("nlosv", C_NLOSV, "NLOSv (vehicle)"),
                                ("nlos", C_NLOS, "NLOS (building)")]:
                ax.bar(d.member, d[col], 0.8, bottom=b, color=c, edgecolor="white", linewidth=1,
                       label=lab)
                b = b + d[col].values
            ax.set_xticks(range(1, 9))
            ax.set_title(f"{SCN[sc][0]}, IVD {ivd} m", color=INK, fontsize=10)
            if j == 0:
                ax.set_ylabel("share of leader transmissions")
            if i == 1:
                ax.set_xlabel("member")
    axes[1, 0].legend(loc="lower left", fontsize=8)
    fig.suptitle("Leader -> member link condition (3GPP + geometric blockage), no background",
                 color=INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out / "fig4_link_condition.png")
    plt.close(fig)

    # ---------------- fig 5: CBR and resource-collision rate of the leader
    def cell_cbr(ax, ivd, tr):
        for sc, (lab, col, ls, mk) in SCN.items():
            d = S[(S.scenario == sc) & (S.ivd_m == ivd) & (S.traffic == tr)].sort_values("nbg")
            ax.errorbar(d.nbg, d.leader_tx_coll_rate, yerr=d.leader_tx_coll_rate_ci, color=col,
                        ls=ls, marker=mk, capsize=2, label=f"{lab}: leader TBs in collision")
            ax.plot(d.nbg, d.cbr, color=col, ls=":", marker="^", lw=1.2,
                    label=f"{lab}: CBR")
        ax.set_ylim(0, None)
    facet("Channel busy ratio and resource-collision rate of the leader's transmissions",
          out / "fig5_cbr_collision.png", cell_cbr, ylabel="ratio", legend_cell=(0, 0))

    # ---------------- fig 6: HARQ activity
    def cell_harq(ax, ivd, tr):
        for sc, (lab, col, ls, mk) in SCN.items():
            d = S[(S.scenario == sc) & (S.ivd_m == ivd) & (S.traffic == tr)].sort_values("nbg")
            ax.errorbar(d.nbg, d.tx_per_pkt, yerr=d.tx_per_pkt_ci, color=col, ls=ls, marker=mk,
                        capsize=2, label=lab)
        ax.set_ylim(0.9, None)
    facet("HARQ activity: PSSCH transmissions per application packet "
          "(1 = no retransmission needed)", out / "fig6_harq_tx_per_pkt.png", cell_harq,
          ylabel="transmissions / packet")

    # ---------------- fig 7: PRR over time, roundabout vs highway (seed 1, IVD 30, periodic1)
    try:
        fig, axes = plt.subplots(2, 2, figsize=(10.5, 5.6), sharey=True)
        for i, sc in enumerate(["highway", "roundabout"]):
            for j, nbg in enumerate([0, 60]):
                ax = axes[i, j]
                tag = f"{'hwy' if sc == 'highway' else 'rbt'}_ivd30_periodic1_n{nbg}_s1"
                p = pd.read_csv(rdir / f"{tag}_pkt.csv.gz")
                p["tb"] = (p.t_tx // 2.0) * 2.0 + 1.0
                for m, col in [(1, "#2a78d6"), (4, "#eb6834"), (8, "#1baf7a")]:
                    d = p[p.member == m].groupby("tb").rx.mean()
                    ax.plot(d.index, d.values, color=col, label=f"member {m}")
                ax.set_title(f"{SCN[sc][0]}, {nbg} background vehicles", color=INK, fontsize=10)
                ax.set_ylim(0, 1.02)
                if j == 0:
                    ax.set_ylabel("PRR (2 s bins)")
                if i == 1:
                    ax.set_xlabel("time (s)")
        axes[0, 0].legend(loc="lower left", fontsize=8)
        fig.suptitle("PRR over the run (IVD 30 m, periodic1, seed 1)", color=INK, fontsize=11,
                     x=0.01, ha="left")
        fig.tight_layout(rect=(0, 0, 1, 0.95))
        fig.savefig(out / "fig7_prr_timeline.png")
        plt.close(fig)
    except FileNotFoundError as e:
        print("fig7 skipped:", e)

    # ---------------- console table
    pd.set_option("display.width", 200)
    cols = KEYS + ["n_seeds", "prr_mean", "prr_mean_ci", "prr_min", "collision_loss_rate",
                   "channel_loss_rate", "notx_rate", "cbr", "leader_tx_coll_rate", "tx_per_pkt"]
    print(S[cols].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
