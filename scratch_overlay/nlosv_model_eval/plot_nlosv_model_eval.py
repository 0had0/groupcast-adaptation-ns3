#!/usr/bin/env python3
"""plot_nlosv_model_eval.py

Visualises the gap between probabilistic (density-based) and geometric
(position-exact) NLOSv determination. Reads results_nlosv_model_eval/all_runs.csv
and writes PNGs into results_nlosv_model_eval/plots/:

  1. confusion_matrix.png   geometric truth vs probabilistic draw
  2. timeline.png           LOS/NLOSv/NLOS fraction over time, both models
  3. spatial_agreement.png  where the two models agree / disagree
  4. pnlosv_vs_distance.png  smooth probabilistic curve vs geometric step
  5. calibration.png        reliability diagram of P(NLOSv)
  6. pathloss_error.png     pathloss-dB error CDF and scatter
"""

import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

CLASSES = ["LOS", "NLOSv", "NLOS"]
EMP = {"LOS": "p_los_emp", "NLOSv": "p_nlosv_emp", "NLOS": "p_nlos_emp"}


def soft_confusion(df):
    mat = np.zeros((3, 3))
    for i, truth in enumerate(CLASSES):
        sub = df[df["geo_class"] == truth]
        if len(sub) == 0:
            continue
        for j, pred in enumerate(CLASSES):
            mat[i, j] = sub[EMP[pred]].sum()
    row = mat.sum(axis=1, keepdims=True)
    row[row == 0] = np.nan
    return mat / row


def plot_confusion(df, path):
    cm = soft_confusion(df)
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    im = ax.imshow(np.nan_to_num(cm), cmap="magma_r", vmin=0, vmax=1)
    ax.set_xticks(range(3), CLASSES)
    ax.set_yticks(range(3), CLASSES)
    ax.set_xlabel("probabilistic draw outcome")
    ax.set_ylabel("geometric truth")
    ax.set_title("NLOSv condition: geometric truth vs probabilistic model\n"
                 "(row-normalised)")
    for i in range(3):
        for j in range(3):
            v = cm[i, j]
            if np.isnan(v):
                continue
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                    color="white" if v > 0.5 else "black", fontsize=11)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_timeline(df, path, scenario, density):
    sub = df[(df["base_scenario"] == scenario) & (df["density"] == density)]
    if len(sub) == 0:
        print(f"  timeline: no rows for {scenario}/{density}, skipped")
        return
    seed = sub["rng_run"].min()
    sub = sub[sub["rng_run"] == seed]
    times = np.sort(sub["time"].unique())

    geo = {c: [] for c in CLASSES}
    prob = {c: [] for c in CLASSES}
    for t in times:
        st = sub[sub["time"] == t]
        n = len(st)
        for c in CLASSES:
            geo[c].append((st["geo_class"] == c).sum() / n)
            prob[c].append(st[EMP[c]].mean())

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    colors = {"LOS": "#2c7bb6", "NLOSv": "#d7191c", "NLOS": "#fdae61"}
    for ax, data, title in [(axes[0], geo, "Geometric (position-exact)"),
                            (axes[1], prob, "Probabilistic (density-based)")]:
        ax.stackplot(times, [data[c] for c in CLASSES], labels=CLASSES,
                     colors=[colors[c] for c in CLASSES], alpha=0.9)
        ax.set_title(title)
        ax.set_xlabel("time [s]")
        ax.set_xlim(times.min(), times.max())
        ax.set_ylim(0, 1)
    axes[0].set_ylabel("fraction of TX-RX links")
    axes[1].legend(loc="upper right", fontsize=8)
    fig.suptitle(f"Link-condition mix over time  ({scenario}, density={density}, "
                 f"seed={seed})")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_spatial(df, path, scenario, density):
    sub = df[(df["base_scenario"] == scenario) & (df["density"] == density)]
    if len(sub) == 0:
        print(f"  spatial: no rows for {scenario}/{density}, skipped")
        return
    seed = sub["rng_run"].min()
    sub = sub[sub["rng_run"] == seed]

    geo_nlosv = sub["geo_nlosv"].astype(bool)
    prob_nlosv = (sub["prob_mode_class"] == "NLOSv")
    # classify each link-sample by midpoint
    mx = 0.5 * (sub["tx_x"] + sub["rx_x"])
    my = 0.5 * (sub["tx_y"] + sub["rx_y"])

    agree = geo_nlosv == prob_nlosv
    false_alarm = (~geo_nlosv) & prob_nlosv
    miss = geo_nlosv & (~prob_nlosv)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(sub["tx_x"], sub["tx_y"], ".", ms=2, color="0.8", zorder=1)
    ax.plot(sub["rx_x"], sub["rx_y"], ".", ms=2, color="0.8", zorder=1,
            label="vehicle positions")
    ax.scatter(mx[agree], my[agree], s=10, c="#2c7bb6", alpha=0.5,
               label="models agree", zorder=2)
    ax.scatter(mx[miss], my[miss], s=22, c="#d7191c", marker="v",
               label="probabilistic MISS (geo NLOSv, prob not)", zorder=3)
    ax.scatter(mx[false_alarm], my[false_alarm], s=22, c="#fdae61", marker="^",
               label="probabilistic FALSE ALARM (geo clear, prob NLOSv)",
               zorder=3)
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.set_title(f"Where the models disagree (link midpoints)\n"
                 f"{scenario}, density={density}, seed={seed}")
    ax.legend(loc="best", fontsize=7)
    ax.set_aspect("equal", adjustable="datalim")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_pnlosv_vs_distance(df, path, scenario, density):
    sub = df[(df["base_scenario"] == scenario) & (df["density"] == density)]
    if len(sub) == 0:
        print(f"  pnlosv-vs-distance: no rows for {scenario}/{density}, skipped")
        return
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.scatter(sub["dist2d"], sub["p_nlosv_emp"], s=8, alpha=0.25,
               color="#2c7bb6", label="probabilistic P(NLOSv), empirical")

    # closed-form curve
    cf = sub[["dist2d", "p_nlosv_cf"]].drop_duplicates().sort_values("dist2d")
    ax.plot(cf["dist2d"], cf["p_nlosv_cf"], color="#08519c", lw=2,
            label="probabilistic P(NLOSv), closed-form")

    # geometric NLOSv frequency, binned
    edges = np.arange(0, sub["dist2d"].max() + 25, 25)
    centers, freq = [], []
    for i in range(len(edges) - 1):
        m = (sub["dist2d"] >= edges[i]) & (sub["dist2d"] < edges[i + 1])
        if m.sum() == 0:
            continue
        centers.append(0.5 * (edges[i] + edges[i + 1]))
        freq.append(sub.loc[m, "geo_nlosv"].mean())
    ax.step(centers, freq, where="mid", color="#d7191c", lw=2.2,
            label="geometric NLOSv frequency (truth)")

    ax.set_xlabel("2-D TX-RX distance [m]")
    ax.set_ylabel("P(NLOSv)  /  NLOSv frequency")
    ax.set_ylim(-0.03, 1.03)
    ax.set_title(f"Smooth density-based probability vs hard geometric truth\n"
                 f"{scenario}, density={density}")
    ax.legend(loc="best", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_calibration(df, path, n_bins=10):
    edges = np.linspace(0, 1, n_bins + 1)
    mean_pred, obs_freq, counts = [], [], []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        if i == n_bins - 1:
            m = (df["p_nlosv_emp"] >= lo) & (df["p_nlosv_emp"] <= hi)
        else:
            m = (df["p_nlosv_emp"] >= lo) & (df["p_nlosv_emp"] < hi)
        sub = df[m]
        if len(sub) == 0:
            continue
        mean_pred.append(sub["p_nlosv_emp"].mean())
        obs_freq.append(sub["geo_nlosv"].mean())
        counts.append(len(sub))
    mean_pred, obs_freq = np.array(mean_pred), np.array(obs_freq)
    n = len(df)
    ece = sum(c / n * abs(p - o) for p, o, c in zip(mean_pred, obs_freq, counts))

    fig, ax = plt.subplots(figsize=(5.4, 5.2))
    ax.plot([0, 1], [0, 1], "--", color="0.5", label="perfect calibration")
    sizes = 30 + 300 * np.array(counts) / max(counts)
    ax.scatter(mean_pred, obs_freq, s=sizes, c="#d7191c", alpha=0.75, zorder=3)
    ax.plot(mean_pred, obs_freq, "-", color="#d7191c", lw=1.2, zorder=2,
            label="probabilistic model")
    ax.set_xlabel("predicted P(NLOSv)  (probabilistic model)")
    ax.set_ylabel("observed NLOSv frequency  (geometric truth)")
    ax.set_title(f"Calibration of density-based P(NLOSv)\nECE = {ece:.3f}")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_pathloss_error(df, path):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))

    err = np.sort(df["loss_err_db"].to_numpy())
    cdf = np.arange(1, len(err) + 1) / len(err)
    axes[0].plot(err, cdf, color="#08519c", lw=2)
    axes[0].axvline(0, color="0.5", ls="--")
    axes[0].set_xlabel("pathloss error  (probabilistic - geometric) [dB]")
    axes[0].set_ylabel("CDF")
    axes[0].set_title(f"Pathloss-dB error CDF\n"
                      f"mean |err| = {df['loss_err_db'].abs().mean():.1f} dB")
    axes[0].grid(alpha=0.3)

    axes[1].scatter(df["geo_loss_db"], df["prob_exp_loss_db"], s=6, alpha=0.2,
                    color="#d7191c")
    lo = min(df["geo_loss_db"].min(), df["prob_exp_loss_db"].min())
    hi = max(df["geo_loss_db"].max(), df["prob_exp_loss_db"].max())
    axes[1].plot([lo, hi], [lo, hi], "--", color="0.5", label="equal loss")
    axes[1].set_xlabel("geometric pathloss [dB]  (truth)")
    axes[1].set_ylabel("probabilistic expected pathloss [dB]")
    axes[1].set_title("Per-link pathloss: probabilistic vs geometric")
    axes[1].legend(loc="best", fontsize=8)
    axes[1].grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.join(
        here, "results_nlosv_model_eval", "all_runs.csv"))
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--scenario", default=None,
                    help="scenario for timeline/spatial/distance plots")
    ap.add_argument("--density", default=None,
                    help="density tier for timeline/spatial/distance plots")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"input not found: {args.input}\nrun run_nlosv_model_eval.sh first.")
    df = pd.read_csv(args.input)
    out_dir = args.out_dir or os.path.join(os.path.dirname(args.input), "plots")
    os.makedirs(out_dir, exist_ok=True)

    scenario = args.scenario or sorted(df["base_scenario"].unique())[0]
    density = args.density or ("MEDIUM" if "MEDIUM" in set(df["density"])
                               else sorted(df["density"].unique())[0])
    print(f"plotting from {args.input} ({len(df)} rows)")
    print(f"  scenario/density for timeline/spatial/distance: {scenario}/{density}")

    plot_confusion(df, os.path.join(out_dir, "confusion_matrix.png"))
    plot_timeline(df, os.path.join(out_dir, "timeline.png"), scenario, density)
    plot_spatial(df, os.path.join(out_dir, "spatial_agreement.png"),
                 scenario, density)
    plot_pnlosv_vs_distance(df, os.path.join(out_dir, "pnlosv_vs_distance.png"),
                            scenario, density)
    plot_calibration(df, os.path.join(out_dir, "calibration.png"))
    plot_pathloss_error(df, os.path.join(out_dir, "pathloss_error.png"))

    print(f"plots written to {out_dir}/")


if __name__ == "__main__":
    main()
