#!/usr/bin/env python3
"""
plot_sinr_vs_pathloss.py
Compare simulated SINR (from psschRxUePhy) against the deterministic
path-loss prediction, and decompose the gap into its sources.

Uses: slinky scenario, MCS=14, all 10 seeds (averaged).

Output:
  plots/sinr_vs_pathloss.png    — time-series comparison per node
  plots/sinr_gap_decomp.png     — budget bar chart
"""

from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

HERE     = Path(__file__).parent
RESULTS  = HERE.parent / "scratch_overlay/platoon_baseline_eval/results_mcs_sweep"
PLOTS    = HERE / "plots"
PLOTS.mkdir(exist_ok=True)

# ── channel constants (same as plot_pathloss.py) ──────────────────────────────
FC_GHZ   = 5.89
TX_DBM   = 23.0                       # NR-SL TX power (dBm)
N_PRB    = 10                         # PRBs in BWP
BW_HZ    = N_PRB * 12 * 15e3         # 1.8 MHz  (15 kHz SCS)
NF_DB    = 9.0                        # UE noise figure
N0_DBM   = -174 + 10*np.log10(BW_HZ) + NF_DB   # ≈ -102.4 dBm
PL_CONST = 32.4 + 20*np.log10(FC_GHZ)           # 47.80 dB

def pl_los(d):
    return PL_CONST + 20*np.log10(np.maximum(d, 0.1))

def snr_pred(d):
    """Path-loss-only SNR prediction [dB]. No fading, no shadowing."""
    return TX_DBM - pl_los(d) - N0_DBM

# ── load distance trace ───────────────────────────────────────────────────────
def load_distances(scenario="ccam_platoon_slinky"):
    p = HERE / scenario / f"{scenario}.mob.csv"
    df = pd.read_csv(p, comment="#", header=None,
                     names=["time","node_id","x","y","speed"])
    df["time"]    = df["time"].round(3)
    df["node_id"] = df["node_id"].astype(int)
    pivot = df.pivot(index="time", columns="node_id", values="x")
    dist = {}
    for nid in range(1, 9):
        dist[nid] = (pivot[0] - pivot[nid]).clip(lower=0.1)
    return dist          # dict: node_id → Series(index=time_s, values=dist_m)

# ── load SINR from all seeds ──────────────────────────────────────────────────
def load_sinr(scenario="ccam_platoon_slinky", mcs=14):
    pat = RESULTS / scenario / f"{scenario}_mcs{mcs}_seed*.db"
    dbs = sorted(Path(RESULTS / scenario).glob(f"{scenario}_mcs{mcs}_seed*.db"))
    frames = []
    for db in dbs:
        seed = int(Path(db).stem.split("seed")[-1])
        con  = sqlite3.connect(db)
        df   = pd.read_sql_query("""
            SELECT ROUND(timeMs/1000.0, 1) AS t_s,
                   (rnti - 1)              AS node_id,
                   avrgSinr,
                   minSinr,
                   psschCorrupt
            FROM psschRxUePhy WHERE txRnti=1
        """, con)
        con.close()
        df["seed"]    = seed
        df["sinr_db"] = 10 * np.log10(df["avrgSinr"].clip(lower=1e-9))
        df["min_db"]  = 10 * np.log10(df["minSinr"].clip(lower=1e-9))
        frames.append(df)

    all_df = pd.concat(frames, ignore_index=True)
    # Bin into 2-second windows for a smoother trace
    all_df["t_bin"] = (all_df["t_s"] // 2 * 2).astype(int)
    stats = all_df.groupby(["t_bin","node_id"])["sinr_db"].agg(
        mean="mean",
        p5 =lambda x: np.percentile(x,  5),
        p95=lambda x: np.percentile(x, 95),
        n  ="count"
    ).reset_index()
    return stats


# ── Figure 1: SINR vs predicted SNR time-series ──────────────────────────────
def plot_timeseries():
    scenario = "ccam_platoon_slinky"
    mcs      = 14
    nodes_to_plot = [1, 4, 8]   # near / mid / far

    dist  = load_distances(scenario)
    sinr  = load_sinr(scenario, mcs)

    fig, axes = plt.subplots(3, 1, figsize=(13, 12), sharex=True)
    fig.suptitle(
        "Simulated SINR vs path-loss prediction  —  Slinky/Accordion, MCS=14, 10 seeds\n"
        r"$SNR_{pred} = P_{TX} - PL_{LOS}(d(t)) - N_0$" + "\n"
        rf"Gap = $SNR_{{pred}}$ − mean SINR  =  all components NOT in the deterministic path-loss formula",
        fontsize=10, y=1.00,
    )

    NODE_COL = {1: "#2171b5", 4: "#6baed6", 8: "#08306b"}
    NODE_LABEL = {1: "Node 1  (near, ~25 m)", 4: "Node 4  (mid, ~100 m)", 8: "Node 8  (far, ~200±100 m)"}

    for ax, nid in zip(axes, nodes_to_plot):
        # Distance → predicted SNR (from trace)
        t_dist = dist[nid].index.values
        snr_p  = snr_pred(dist[nid].values)

        # SINR from DB
        sub = sinr[sinr.node_id == nid].sort_values("t_bin")
        t_s = sub["t_bin"].values.astype(float)
        s_mean = sub["mean"].values
        s_p5   = sub["p5"].values
        s_p95  = sub["p95"].values

        # Gap (interpolate snr_p to same time axis as sinr)
        snr_interp = np.interp(t_s, t_dist, snr_p)
        gap = snr_interp - s_mean

        ax.fill_between(t_s, s_p5, s_p95,
                         color=NODE_COL[nid], alpha=0.18, label="p5–p95 SINR range")
        ax.plot(t_s,    s_mean,   color=NODE_COL[nid], lw=1.8, ls="--",
                label=f"Mean SINR  (sim, 10 seeds)")
        ax.plot(t_dist, snr_p,    color="firebrick",   lw=1.8, ls="-",
                label=r"$SNR_{pred}$ from path loss only")

        # Gap fill between predicted and mean
        ax.fill_between(t_s, s_mean, snr_interp,
                         color="orange", alpha=0.25, label="Gap (missing components)")

        # Annotate average gap value at a representative point
        mid_idx = len(t_s)//2
        ax.annotate(
            f"avg gap\n≈ {gap[mid_idx//2:3*mid_idx//2].mean():.1f} dB",
            xy=(t_s[mid_idx], (s_mean[mid_idx] + snr_interp[mid_idx])/2),
            xytext=(3, 0), textcoords="offset points",
            fontsize=8, color="darkorange", fontweight="bold", va="center"
        )

        ax.axhline(0, color="gray", lw=0.7, ls=":", alpha=0.6)
        ax.set_title(f"{NODE_LABEL[nid]}  —  MCS 14 threshold ≈ 20 dB",
                     fontsize=9.5, fontweight="bold", pad=3)
        ax.set_ylabel("SINR / SNR  [dB]", fontsize=9)
        ax.grid(ls=":", alpha=0.4)
        ax.set_xlim(t_dist[0], t_dist[-1])

        # Mark MCS-14 threshold
        ax.axhline(20, color="crimson", lw=1.0, ls="--", alpha=0.7)
        ax.text(t_dist[-1]+0.3, 20, "MCS14\nthresh", fontsize=6.5,
                color="crimson", va="center")

        # Add second y-axis: corresponding distance scale
        ax2 = ax.twinx()
        ax2.set_ylabel("Distance  [m]", fontsize=8, color="saddlebrown")
        ax2.tick_params(axis="y", colors="saddlebrown")
        # Same limits as left axis, but labelled in distance units
        ylo, yhi = ax.get_ylim()
        # PL_pred - (TX_DBM - N0_DBM) = -SNR
        # distance ticks
        d_marks = [20, 50, 100, 150, 200, 300, 400]
        snr_of_d = [snr_pred(d) for d in d_marks]
        valid = [(d, s) for d, s in zip(d_marks, snr_of_d) if ylo <= s <= yhi]
        if valid:
            ax2.set_ylim(ylo, yhi)
            ax2.set_yticks([s for _,s in valid])
            ax2.set_yticklabels([f"{d}m" for d,_ in valid], fontsize=7)

        ax.legend(fontsize=7.5, loc="upper left", ncol=2, framealpha=0.85)

    axes[-1].set_xlabel("Simulation time  [s]", fontsize=9)
    fig.tight_layout(rect=[0, 0, 0.93, 1.0])
    out = PLOTS / "sinr_vs_pathloss.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── Figure 2: Component budget bar chart ─────────────────────────────────────
def plot_budget():
    """
    Decompose the SINR budget for each receiver node:
      TX power
      − Path loss  (deterministic)
      − Shadow fading (mean ≈ 0 in dB; std = 3 dB  ← shown as error bar)
      − Fast fading  (CDL spatial channel, frequency-averaged)
      − Noise floor
      = Measured mean SINR

    The gap = SNR_pred − mean_SINR is attributed to:
      a) Jensen's penalty  (log-mean < mean-log for lognormal SINR)
      b) Shadow fading std  (pulls dB mean negative)
      c) Fast fading average loss  (Ricean/Rayleigh channel matrix H)
    """
    scenario = "ccam_platoon_slinky"
    dist  = load_distances(scenario)
    sinr  = load_sinr(scenario, mcs=14)

    # Average over full simulation per node (for the budget chart)
    nodes = list(range(1, 9))
    rows  = []
    for nid in nodes:
        sub      = sinr[sinr.node_id == nid]
        t_dist   = dist[nid].index.values
        # Mean distance over sim
        d_mean   = dist[nid].mean()
        snr_p    = snr_pred(d_mean)
        sinr_mean= sub["mean"].mean()
        gap      = snr_p - sinr_mean

        # Estimated components of the gap
        # 1. Jensen's penalty: for lognormal SINR with std ~ (p95-p5)/3.3 dB
        sinr_std  = (sub["p95"].mean() - sub["p5"].mean()) / (2 * 1.645)
        jensens   = sinr_std**2 / (20 / np.log(10))   # approx for lognormal
        jensens   = min(jensens, gap * 0.35)           # cap at 35 % of gap

        # 2. Shadow fading: σ=3 dB (3GPP TR 37.885, V2V Highway LOS)
        shadow_std = 3.0    # only the std; mean contribution ≈ 0

        # 3. Fast fading = residual gap
        fast_fade = max(0, gap - jensens)

        rows.append(dict(nid=nid, d_mean=d_mean,
                         snr_pred=snr_p, sinr_mean=sinr_mean,
                         gap=gap, jensens=jensens, fast_fade=fast_fade,
                         shadow_std=shadow_std,
                         sinr_p5=sub["p5"].mean(), sinr_p95=sub["p95"].mean()))

    df = pd.DataFrame(rows)
    print("\n=== SINR budget table ===")
    print(df[["nid","d_mean","snr_pred","sinr_mean","gap","jensens","fast_fade"]].round(1).to_string(index=False))

    # ---- Plot ----
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        "SINR budget vs path-loss prediction  —  Slinky, MCS=14, all nodes, full-sim average\n"
        rf"$SNR_{{pred}} = P_{{TX}}({TX_DBM:.0f}\ \mathrm{{dBm}}) - PL_{{LOS}}(d) - N_0({N0_DBM:.1f}\ \mathrm{{dBm}})$",
        fontsize=11, fontweight="bold"
    )

    xs      = np.arange(len(nodes))
    bar_w   = 0.35
    labels  = [f"N{nid}\n({row.d_mean:.0f}m)" for nid, row in zip(nodes, df.itertuples())]

    # -- Left: absolute SINR levels --
    ax1.bar(xs,         df.snr_pred.values,  bar_w, label=r"$SNR_{pred}$ (path loss only)",
            color="steelblue", alpha=0.85, edgecolor="navy")
    ax1.bar(xs + bar_w, df.sinr_mean.values, bar_w, label="Mean SINR (simulated)",
            color="tomato", alpha=0.85, edgecolor="darkred")
    # p5–p95 error bars on simulated
    ax1.errorbar(xs + bar_w, df.sinr_mean.values,
                 yerr=[df.sinr_mean - df.sinr_p5, df.sinr_p95 - df.sinr_mean],
                 fmt="none", color="black", capsize=3, lw=1.2, alpha=0.6)

    ax1.axhline(20, color="crimson", lw=1.2, ls="--", alpha=0.8, label="MCS-14 threshold (~20 dB)")
    ax1.axhline(N0_DBM - TX_DBM + PL_CONST, color="gray", lw=0.7, ls=":", alpha=0.5)
    ax1.set_xticks(xs + bar_w/2); ax1.set_xticklabels(labels, fontsize=8)
    ax1.set_ylabel("SINR / SNR  [dB]", fontsize=9)
    ax1.set_title("Absolute levels: predicted vs simulated", fontsize=9)
    ax1.legend(fontsize=8, loc="upper right")
    ax1.grid(axis="y", ls=":", alpha=0.5)

    # -- Right: gap decomposition stacked bar --
    c_jens  = "#fdae61"
    c_fast  = "#d73027"
    c_shad  = "#fee090"

    ax2.bar(xs, df.fast_fade, bar_w*2, label="Fast fading (CDL spatial channel)",
            color=c_fast, alpha=0.85, edgecolor="darkred")
    ax2.bar(xs, df.jensens, bar_w*2, bottom=df.fast_fade,
            label="Jensen's penalty  [E[log X] < log E[X]]",
            color=c_jens, alpha=0.85, edgecolor="darkorange")

    # Shadow fading: show as ±σ error bar at the top of each gap bar
    ax2.errorbar(xs, df.gap, yerr=[[0]*len(nodes), [3.0]*len(nodes)],
                 fmt="none", color="forestgreen", capsize=5, lw=2.0,
                 label=r"Shadow fading  $\pm\sigma_{shadow}=3$ dB  (3GPP TR 37.885)")

    for i, row in df.iterrows():
        ax2.text(xs[i], row.gap + 0.3, f"{row.gap:.1f}\ndB",
                 ha="center", va="bottom", fontsize=7.5, fontweight="bold")

    ax2.set_xticks(xs); ax2.set_xticklabels(labels, fontsize=8)
    ax2.set_ylabel("Gap = SNR_pred − mean SINR  [dB]", fontsize=9)
    ax2.set_title("Gap decomposition into missing components", fontsize=9)
    ax2.legend(fontsize=8, loc="upper left")
    ax2.grid(axis="y", ls=":", alpha=0.5)
    ax2.set_ylim(0, df.gap.max() * 1.3)

    # Annotate the 3 components
    ax2.text(0.98, 0.97,
        "Red   = fast fading (CDL channel matrix)\n"
        "Orange = Jensen's penalty (log-mean < mean-log)\n"
        "Green bar = ±3 dB shadow fading (not shown in mean)",
        transform=ax2.transAxes, fontsize=7.5, ha="right", va="top",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8))

    fig.tight_layout()
    out = PLOTS / "sinr_gap_decomp.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out}")


# ── main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Noise floor: {N0_DBM:.2f} dBm  (BW={BW_HZ/1e6:.2f} MHz, NF={NF_DB} dB)")
    print(f"PL constant: {PL_CONST:.3f} dB  (fc={FC_GHZ} GHz)\n")
    print("Generating SINR vs path-loss plots …")
    plot_timeseries()
    plot_budget()
    print(f"\nDone → {PLOTS}/")
