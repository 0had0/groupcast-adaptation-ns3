#!/usr/bin/env python3
"""
analyse_mcs_timeseries.py

For each scenario, computes worst-RX PRR, max-PIR, and throughput in sliding
5-second windows for every MCS value (7, 10, 12, 14, 20), averages over seeds,
then derives:

  • Which MCS maximises worst-RX PRR at each moment in time
  • Gain (PRR, PIR, tput) of the optimal MCS vs fixed MCS=14 per window
  • Cross-scenario summary comparison table

Supported scenarios
-------------------
  shadowing_blockage       — original NLOSv truck scenario
  high_density             — 75 background NLOSv vehicles
  variable_packet_size     — clean platoon control
  ccam_platoon_overtake    — truck overtake (NLOSv-based)
  ccam_platoon_slinky      — Scenario A: sinusoidal gap oscillation (3 cycles)
  ccam_platoon_gap_open    — Scenario B: single gap-opening event
  ccam_platoon_merge       — Scenario C: joining vehicles (monotone improvement)

Outputs
-------
  results_mcs_sweep/tidy/timeseries_worst_rx.csv
  results_mcs_sweep/tidy/scenario_gain_summary.csv
  results_mcs_sweep/plots/ts_<scenario>_optimal_mcs.png
  results_mcs_sweep/plots/gain_comparison_all.png
"""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import matplotlib.patches as mpatches

SCRIPT_DIR   = Path(__file__).resolve().parent
SWEEP_DIR    = SCRIPT_DIR / "results_mcs_sweep"
BASELINE_DIR = SCRIPT_DIR / "results_platoon_baseline"
TIDY_DIR     = SWEEP_DIR / "tidy"
PLOT_DIR     = SWEEP_DIR / "plots"

WINDOW_SEC = 5.0
TX_NODE_ID = 0
WARMUP_SEC = 2.0

ALL_MCS    = [7, 10, 12, 14, 20]
MCS_COLORS = {7:'#ff7f0e', 10:'#2ca02c', 12:'#d62728', 14:'#9467bd', 20:'#8c564b'}
MCS_LS     = {7:'-', 10:'--', 12:'-.', 14:':', 20:(0,(3,1,1,1))}

SCENARIOS = [
    "shadowing_blockage",
    "high_density",
    "variable_packet_size",
    "ccam_platoon_overtake",
    "ccam_platoon_slinky",
    "ccam_platoon_gap_open",
    "ccam_platoon_merge",
]

SCENARIO_LABELS = {
    "shadowing_blockage":    "Shadow + Blockage",
    "high_density":          "High Density",
    "variable_packet_size":  "Variable Pkt Size",
    "ccam_platoon_overtake": "Truck Overtake (NLOSv)",
    "ccam_platoon_slinky":   "Scenario A — Slinky (3 cycles)",
    "ccam_platoon_gap_open": "Scenario B — Single Gap-Open Event",
    "ccam_platoon_merge":    "Scenario C — Platoon Merge",
}

# Vertical phase markers per scenario
SCENARIO_PHASE_LINES = {
    "ccam_platoon_overtake": [
        (25.0, "Ph2\n(blockage)"),
        (63.3, "Ph3\n(clear)"),
    ],
    # Slinky: mark 3 gap-minima (MCS=14 windows) and 3 gap-maxima (MCS=7 windows)
    "ccam_platoon_slinky": [
        (7.5,  "max\n296m"),
        (22.5, "min\n104m"),
        (37.5, "max\n296m"),
        (52.5, "min\n104m"),
        (67.5, "max\n296m"),
        (82.5, "min\n104m"),
    ],
    # Gap-open: follower-1 events + follower-8 events
    "ccam_platoon_gap_open": [
        (22.0,  "F1 opens"),
        (50.0,  "F8 full\n400m"),
        (72.0,  "F1 closes"),
        (100.0, "F8 closed\n160m"),
    ],
    # Merge: milestones for worst-RX distance
    "ccam_platoon_merge": [
        (30.0, "~295m"),
        (55.0, "~208m"),
        (70.0, "merged\n165m"),
    ],
}


# ── helpers ────────────────────────────────────────────────────────────────────

def window_metrics(db_path: Path,
                   window_sec: float = WINDOW_SEC,
                   tx_node: int = TX_NODE_ID) -> pd.DataFrame:
    """
    Returns a DataFrame with one row per (window, rx_node_id):
      win_start, win_end, rx_node_id,
      n_tx, n_rx, prr,
      max_pir_ms,    ← max inter-packet-arrival in window (ms); NaN if <2 pkts
      mean_pir_ms,   ← mean IAT (ms)
      tput_kbps      ← received bytes × 8 / window_sec / 1000
    """
    conn = sqlite3.connect(str(db_path))
    df = pd.read_sql_query(
        "SELECT timeSec, txRx, nodeId, pktSeqNum, pktSizeBytes FROM pktTxRx", conn)
    conn.close()

    t_min = WARMUP_SEC
    t_max = df["timeSec"].max() if not df.empty else np.nan
    if df.empty or np.isnan(t_max) or t_max <= t_min:
        return pd.DataFrame()

    wins    = np.arange(t_min, t_max, window_sec)
    tx_df   = df[df["txRx"] == "tx"]
    rx_df   = df[df["txRx"] == "rx"]
    rx_nodes = sorted(rx_df["nodeId"].unique())

    rows = []
    for ws in wins:
        we = ws + window_sec
        tx_seqs = set(tx_df.loc[(tx_df["timeSec"] >= ws) & (tx_df["timeSec"] < we), "pktSeqNum"])
        n_tx = len(tx_seqs)
        if n_tx == 0:
            continue

        rx_win = rx_df[rx_df["pktSeqNum"].isin(tx_seqs)]

        for nid in rx_nodes:
            nid_rx = rx_win[rx_win["nodeId"] == nid]
            n_rx   = len(nid_rx)
            prr    = min(n_rx / n_tx, 1.0)

            # PIR: inter-arrival times
            times_sorted = nid_rx["timeSec"].sort_values().values
            if len(times_sorted) >= 2:
                iats        = np.diff(times_sorted) * 1000.0   # → ms
                max_pir_ms  = float(iats.max())
                mean_pir_ms = float(iats.mean())
            else:
                max_pir_ms  = window_sec * 1000.0   # pathological: no IAT measurable
                mean_pir_ms = window_sec * 1000.0

            # Throughput (application bytes delivered)
            total_bytes = int(nid_rx["pktSizeBytes"].sum())
            tput_kbps   = total_bytes * 8.0 / window_sec / 1000.0

            rows.append(dict(
                win_start   = ws,
                win_end     = we,
                rx_node_id  = int(nid),
                n_tx        = n_tx,
                n_rx        = n_rx,
                prr         = prr,
                max_pir_ms  = max_pir_ms,
                mean_pir_ms = mean_pir_ms,
                tput_kbps   = tput_kbps,
            ))
    return pd.DataFrame(rows)


# keep backward-compatible alias
window_prr = window_metrics


def collect_scenario(scenario: str) -> pd.DataFrame:
    """Load all DBs for a scenario (sweep + optional baseline for MCS=14)."""
    all_rows = []

    scen_dir = SWEEP_DIR / scenario
    if scen_dir.is_dir():
        for db in sorted(scen_dir.glob(f"{scenario}_mcs*_seed*.db")):
            stem = db.stem
            try:
                _, rest = stem.split("_mcs", 1)
                mcs_str, seed_str = rest.split("_seed", 1)
                mcs, seed = int(mcs_str), int(seed_str)
            except ValueError:
                continue
            df = window_metrics(db)
            if df.empty:
                continue
            df["mcs"]  = mcs
            df["seed"] = seed
            all_rows.append(df)

    mcs14_from_sweep = any(14 in r["mcs"].values for r in all_rows if not r.empty)
    if not mcs14_from_sweep:
        base_dir = BASELINE_DIR / scenario
        if base_dir.is_dir():
            for db in sorted(base_dir.glob(f"{scenario}_seed*.db")):
                stem = db.stem
                try:
                    seed = int(stem.rsplit("_seed", 1)[1])
                except (ValueError, IndexError):
                    continue
                df = window_metrics(db)
                if df.empty:
                    continue
                df["mcs"]  = 14
                df["seed"] = seed
                all_rows.append(df)

    if not all_rows:
        return pd.DataFrame()
    return pd.concat(all_rows, ignore_index=True)


def worst_rx_timeseries(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per (mcs, win_start): worst-RX metrics.
    worst PRR  = min PRR over rx_node_id (bottleneck reliability).
    worst PIR  = max max_pir_ms over rx_node_id (worst latency pocket).
    mean tput  = mean tput_kbps over rx_node_id (average delivered throughput).
    """
    def _worst_node(sub_df):
        return int(sub_df.loc[sub_df["prr"].idxmin(), "rx_node_id"])

    # Per (mcs, seed, win_start): worst-RX aggregates
    grp = df.groupby(["mcs", "seed", "win_start"])
    worst_prr  = grp["prr"].min().rename("worst_prr")
    worst_pir  = grp["max_pir_ms"].max().rename("worst_pir_ms")
    mean_tput  = grp["tput_kbps"].mean().rename("mean_tput_kbps")
    worst_node = grp.apply(_worst_node).rename("worst_node")

    worst = pd.concat([worst_prr, worst_pir, mean_tput, worst_node], axis=1).reset_index()

    agg = (worst.groupby(["mcs", "win_start"])
               .agg(
                   mean_worst_prr   = ("worst_prr",     "mean"),
                   std_worst_prr    = ("worst_prr",     "std"),
                   mean_worst_pir   = ("worst_pir_ms",  "mean"),
                   std_worst_pir    = ("worst_pir_ms",  "std"),
                   mean_tput_kbps   = ("mean_tput_kbps","mean"),
                   std_tput_kbps    = ("mean_tput_kbps","std"),
                   n_seeds          = ("worst_prr",     "count"),
                   bottleneck_node  = ("worst_node",    lambda s: int(s.mode().iloc[0])),
               )
               .reset_index())
    agg["ci95_prr"] = 1.96 * agg["std_worst_prr"] / np.sqrt(agg["n_seeds"].clip(lower=1))
    agg["ci95_pir"] = 1.96 * agg["std_worst_pir"] / np.sqrt(agg["n_seeds"].clip(lower=1))
    agg["t_mid"]    = agg["win_start"] + WINDOW_SEC / 2.0
    return agg


def optimal_mcs_series(ts: pd.DataFrame) -> pd.DataFrame:
    """
    Per window: optimal MCS (best PRR) and gain vs MCS=14 for PRR, PIR, tput.
    """
    rows = []
    for win, sub in ts.groupby("win_start"):
        best_row  = sub.loc[sub["mean_worst_prr"].idxmax()]
        row14     = sub[sub["mcs"] == 14]
        prr14     = row14["mean_worst_prr"].values[0] if len(row14) else np.nan
        pir14     = row14["mean_worst_pir"].values[0] if len(row14) else np.nan
        tput14    = row14["mean_tput_kbps"].values[0] if len(row14) else np.nan

        rows.append(dict(
            win_start       = win,
            t_mid           = best_row["t_mid"],
            optimal_mcs     = int(best_row["mcs"]),
            best_prr        = float(best_row["mean_worst_prr"]),
            best_pir_ms     = float(best_row["mean_worst_pir"]),
            best_tput_kbps  = float(best_row["mean_tput_kbps"]),
            prr_mcs14       = float(prr14),
            pir_mcs14       = float(pir14),
            tput_mcs14      = float(tput14),
            # gains (positive = improvement over fixed MCS=14)
            delta_prr_pp    = (float(best_row["mean_worst_prr"]) - float(prr14)) * 100,
            delta_pir_ms    = float(pir14) - float(best_row["mean_worst_pir"]),  # -→ shorter wait
            delta_tput_kbps = float(best_row["mean_tput_kbps"]) - float(tput14),
            node_bottleneck = int(best_row["bottleneck_node"]),
        ))
    return pd.DataFrame(rows).sort_values("win_start").reset_index(drop=True)


# ── per-scenario plot ──────────────────────────────────────────────────────────

def plot_scenario(scenario: str, ts: pd.DataFrame, opt: pd.DataFrame, out_dir: Path):
    mcs_vals = sorted(ts["mcs"].unique())
    t_vals   = sorted(ts["win_start"].unique())

    fig, axes = plt.subplots(4, 1, figsize=(13, 14),
                              gridspec_kw={"height_ratios": [3, 1.2, 1.2, 1]})
    ax_prr, ax_mcs, ax_gain, ax_node = axes

    # ── Panel 1: worst-RX PRR ────────────────────────────────────────────────
    for mcs in mcs_vals:
        sub = ts[ts["mcs"] == mcs].sort_values("win_start")
        lw  = 2.5 if mcs == 14 else 1.8
        ax_prr.plot(sub["t_mid"], sub["mean_worst_prr"],
                    color=MCS_COLORS[mcs], ls=MCS_LS.get(mcs, "-"),
                    linewidth=lw, label=f"MCS {mcs}", zorder=3 if mcs==14 else 2)
        ax_prr.fill_between(sub["t_mid"],
                             sub["mean_worst_prr"] - sub["ci95_prr"],
                             sub["mean_worst_prr"] + sub["ci95_prr"],
                             color=MCS_COLORS[mcs], alpha=0.12)

    ax_prr.plot(opt["t_mid"], opt["best_prr"],
                color="black", ls="-", linewidth=3.5, alpha=0.22, zorder=1, label="_nolegend_")
    ax_prr.set_ylabel("Worst-RX PRR", fontsize=11)
    ax_prr.yaxis.set_major_formatter(mtick.PercentFormatter(xmax=1))
    ax_prr.set_ylim(0, 1.05)
    ax_prr.legend(loc="lower left", fontsize=9, ncol=len(mcs_vals))
    ax_prr.grid(True, alpha=0.3)
    ax_prr.set_title(f"{SCENARIO_LABELS[scenario]} — time-domain worst-RX metrics",
                     fontsize=12, fontweight="bold")

    # Phase lines
    for t_mark, lbl in SCENARIO_PHASE_LINES.get(scenario, []):
        for ax_ph in [ax_prr, ax_mcs, ax_gain, ax_node]:
            ax_ph.axvline(t_mark, color="steelblue", ls=":", lw=1.3, alpha=0.75)
        ax_prr.text(t_mark + 0.3, 1.01, lbl,
                    color="steelblue", fontsize=6.5, va="bottom")

    # ── Panel 2: optimal MCS + ΔPRR ─────────────────────────────────────────
    for _, row in opt.iterrows():
        ax_mcs.fill_between([row["win_start"], row["win_start"] + WINDOW_SEC],
                             [0, 0], [1, 1],
                             color=MCS_COLORS.get(row["optimal_mcs"], "grey"), alpha=0.28)
    ax_mcs.step(opt["t_mid"], opt["optimal_mcs"],
                where="mid", color="black", linewidth=2.0)
    ax_mcs.set_yticks(mcs_vals)
    ax_mcs.set_yticklabels([f"MCS {m}" for m in mcs_vals], fontsize=8)
    ax_mcs.set_ylabel("Optimal MCS", fontsize=10)
    ax_mcs.set_ylim(min(mcs_vals) - 1, max(mcs_vals) + 1)
    ax_mcs.grid(axis="x", alpha=0.3)

    # Annotate MCS switches
    prev_mcs = 14
    for _, row in opt.iterrows():
        cur = row["optimal_mcs"]
        if cur != prev_mcs and prev_mcs == 14:
            ax_mcs.axvline(row["win_start"], color="red", ls="--", lw=1.1, alpha=0.65)
            ax_mcs.text(row["win_start"] + 0.3, max(mcs_vals) * 0.97,
                        f"↓{cur}", color="red", fontsize=7, va="top")
        elif cur != prev_mcs and cur == 14:
            ax_mcs.axvline(row["win_start"], color="green", ls="--", lw=1.1, alpha=0.65)
            ax_mcs.text(row["win_start"] + 0.3, max(mcs_vals) * 0.97,
                        "↑14", color="green", fontsize=7, va="top")
        prev_mcs = cur

    ax_mcs2 = ax_mcs.twinx()
    ax_mcs2.plot(opt["t_mid"], opt["delta_prr_pp"],
                 color="navy", ls=":", linewidth=1.5, alpha=0.7)
    ax_mcs2.set_ylabel("ΔPRR vs\nMCS=14 (pp)", fontsize=8, color="navy")
    ax_mcs2.tick_params(axis="y", labelcolor="navy", labelsize=8)
    ax_mcs2.axhline(0, color="navy", lw=0.6, alpha=0.35)

    # ── Panel 3: gain in PIR and throughput ──────────────────────────────────
    ax_pir  = ax_gain
    ax_tp   = ax_gain.twinx()

    ax_pir.fill_between(opt["t_mid"], 0, opt["delta_pir_ms"],
                        where=opt["delta_pir_ms"] >= 0,
                        color="teal", alpha=0.25, label="ΔPIR<0 (shorter wait)")
    ax_pir.fill_between(opt["t_mid"], 0, opt["delta_pir_ms"],
                        where=opt["delta_pir_ms"] < 0,
                        color="salmon", alpha=0.25, label="ΔPIR>0 (longer wait)")
    ax_pir.plot(opt["t_mid"], opt["delta_pir_ms"],
                color="teal", lw=1.5, alpha=0.8)
    ax_pir.axhline(0, color="grey", lw=0.7, alpha=0.5)
    ax_pir.set_ylabel("ΔPIR (ms)\noptimal−MCS14", fontsize=8, color="teal")
    ax_pir.tick_params(axis="y", labelcolor="teal", labelsize=8)

    ax_tp.plot(opt["t_mid"], opt["delta_tput_kbps"],
               color="darkorange", lw=1.5, ls="--", alpha=0.85, label="Δtput (kbps)")
    ax_tp.axhline(0, color="grey", lw=0.6, alpha=0.4)
    ax_tp.set_ylabel("Δtput (kbps)\noptimal−MCS14", fontsize=8, color="darkorange")
    ax_tp.tick_params(axis="y", labelcolor="darkorange", labelsize=8)
    ax_gain.grid(axis="x", alpha=0.3)
    ax_gain.set_title("Gain of optimal MCS over fixed MCS=14", fontsize=9)

    # ── Panel 4: bottleneck node ─────────────────────────────────────────────
    ax_node.step(opt["t_mid"], opt["node_bottleneck"],
                 where="mid", color="darkorange", linewidth=2.0)
    ax_node.set_yticks(sorted(opt["node_bottleneck"].unique()))
    ax_node.set_ylabel("Bottleneck\nRX node", fontsize=10)
    ax_node.set_xlabel("Simulation time (s)", fontsize=11)
    ax_node.grid(True, alpha=0.3)

    node_colors = plt.cm.Set2(np.linspace(0, 1, 9))
    prev_node = int(opt.iloc[0]["node_bottleneck"])
    prev_t    = opt.iloc[0]["win_start"]
    for _, row in opt.iterrows():
        node = int(row["node_bottleneck"])
        if node != prev_node:
            ax_node.axvspan(prev_t, row["win_start"],
                            alpha=0.18, color=node_colors[(prev_node - 1) % 9])
            prev_node = node
            prev_t    = row["win_start"]

    t_max_plot = max(t_vals) + WINDOW_SEC
    for ax in axes:
        ax.set_xlim(WARMUP_SEC, t_max_plot)

    plt.tight_layout()
    out_path = out_dir / f"ts_{scenario}_optimal_mcs.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {out_path}")


# ── cross-scenario comparison plot ────────────────────────────────────────────

def plot_gain_comparison(summary_df: pd.DataFrame, out_dir: Path):
    """
    Bar chart comparing mean gain (PRR, PIR, tput) of optimal-MCS vs MCS=14
    across all scenarios, broken down into positive-gain (adaptation helps)
    windows only and all windows.
    """
    if summary_df.empty:
        return

    scens  = summary_df["scenario"].tolist()
    labels = [SCENARIO_LABELS.get(s, s) for s in scens]

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    # PRR gain
    ax = axes[0]
    bars = ax.bar(range(len(scens)), summary_df["mean_delta_prr_pp"],
                  color="steelblue", alpha=0.75, edgecolor="black", linewidth=0.6)
    ax.bar_label(bars, fmt="%.1f pp", fontsize=8, padding=2)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("Δ Worst-RX PRR\n(optimal MCS − MCS=14)", fontsize=10, fontweight="bold")
    ax.set_ylabel("Mean gain (percentage points)", fontsize=9)
    ax.set_xticks(range(len(scens)))
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    # PIR gain (negative = shorter wait = improvement)
    ax = axes[1]
    bars = ax.bar(range(len(scens)), summary_df["mean_delta_pir_ms"],
                  color="teal", alpha=0.75, edgecolor="black", linewidth=0.6)
    ax.bar_label(bars, fmt="%.0f ms", fontsize=8, padding=2)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("Δ Worst-RX PIR\n(MCS=14 − optimal MCS; positive = shorter wait)",
                 fontsize=10, fontweight="bold")
    ax.set_ylabel("Mean gain (ms)", fontsize=9)
    ax.set_xticks(range(len(scens)))
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    # Throughput gain
    ax = axes[2]
    bars = ax.bar(range(len(scens)), summary_df["mean_delta_tput_kbps"],
                  color="darkorange", alpha=0.75, edgecolor="black", linewidth=0.6)
    ax.bar_label(bars, fmt="%.1f", fontsize=8, padding=2)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("Δ Mean RX Throughput\n(optimal MCS − MCS=14)", fontsize=10, fontweight="bold")
    ax.set_ylabel("Mean gain (kbps)", fontsize=9)
    ax.set_xticks(range(len(scens)))
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    plt.suptitle("Gain of adaptive (optimal) MCS vs fixed MCS=14 across scenarios",
                 fontsize=12, fontweight="bold", y=1.01)
    plt.tight_layout()
    out_path = out_dir / "gain_comparison_all.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\n  → {out_path}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    TIDY_DIR.mkdir(parents=True, exist_ok=True)
    PLOT_DIR.mkdir(parents=True, exist_ok=True)

    all_ts_rows  = []
    summary_rows = []

    for scenario in SCENARIOS:
        print(f"\n{'='*62}")
        print(f"  {scenario}")
        print(f"{'='*62}")

        df_raw = collect_scenario(scenario)
        if df_raw.empty:
            print("  [skip] no data found")
            continue

        print(f"  {len(df_raw):,} window-node rows  "
              f"({df_raw['seed'].nunique()} seeds × {df_raw['mcs'].nunique()} MCS)")

        ts  = worst_rx_timeseries(df_raw)
        opt = optimal_mcs_series(ts)

        ts["scenario"] = scenario
        all_ts_rows.append(ts)

        # Console table
        print(f"\n  {'t_start':>8}  {'opt_MCS':>7}  {'PRR':>6}  {'MCS14_PRR':>9}  "
              f"{'ΔPRR(pp)':>8}  {'ΔPIR(ms)':>8}  {'Δtput(kb)':>9}  node")
        print("  " + "-"*78)
        for _, row in opt.iterrows():
            print(f"  {row['win_start']:8.1f}  "
                  f"{'MCS '+str(int(row['optimal_mcs'])):>7}  "
                  f"{row['best_prr']:6.3f}  "
                  f"{row['prr_mcs14']:9.3f}  "
                  f"{row['delta_prr_pp']:+8.1f}  "
                  f"{row['delta_pir_ms']:+8.1f}  "
                  f"{row['delta_tput_kbps']:+9.2f}  "
                  f"{int(row['node_bottleneck'])}")

        # Aggregate gain summary
        mean_dprr  = float(opt["delta_prr_pp"].mean())
        mean_dpir  = float(opt["delta_pir_ms"].mean())
        mean_dtput = float(opt["delta_tput_kbps"].mean())
        frac_better = float((opt["delta_prr_pp"] > 0).mean())

        print(f"\n  Summary:")
        print(f"    Mean ΔPRR   = {mean_dprr:+.2f} pp  (fraction of windows where opt>MCS14: {frac_better:.0%})")
        print(f"    Mean ΔPIR   = {mean_dpir:+.1f} ms  (negative = shorter wait)")
        print(f"    Mean Δtput  = {mean_dtput:+.2f} kbps")

        summary_rows.append(dict(
            scenario           = scenario,
            n_windows          = len(opt),
            n_seeds            = int(df_raw["seed"].nunique()),
            mean_delta_prr_pp  = mean_dprr,
            mean_delta_pir_ms  = mean_dpir,
            mean_delta_tput_kbps = mean_dtput,
            frac_windows_opt_beats_mcs14 = frac_better,
            mean_best_prr      = float(opt["best_prr"].mean()),
            mean_prr_mcs14     = float(opt["prr_mcs14"].mean()),
        ))

        print(f"\n  Plotting ...")
        plot_scenario(scenario, ts, opt, PLOT_DIR)

    # Save timeseries CSV
    if all_ts_rows:
        out_csv = TIDY_DIR / "timeseries_worst_rx.csv"
        pd.concat(all_ts_rows, ignore_index=True).to_csv(out_csv, index=False)
        print(f"\n→ {out_csv}")

    # Save and plot summary
    if summary_rows:
        summary_df = pd.DataFrame(summary_rows)
        out_sum = TIDY_DIR / "scenario_gain_summary.csv"
        summary_df.to_csv(out_sum, index=False)
        print(f"→ {out_sum}")

        print("\n\n" + "="*70)
        print("  CROSS-SCENARIO GAIN SUMMARY")
        print("="*70)
        col_fmt = "{:<30}  {:>8}  {:>8}  {:>8}  {:>8}  {:>8}"
        print(col_fmt.format("Scenario", "ΔPRR(pp)", "ΔPIR(ms)",
                              "Δtput(kb)", "wins>14%", "seeds"))
        print("-"*70)
        for _, r in summary_df.iterrows():
            print(col_fmt.format(
                SCENARIO_LABELS.get(r["scenario"], r["scenario"])[:30],
                f"{r['mean_delta_prr_pp']:+.2f}",
                f"{r['mean_delta_pir_ms']:+.1f}",
                f"{r['mean_delta_tput_kbps']:+.2f}",
                f"{r['frac_windows_opt_beats_mcs14']:.0%}",
                str(int(r["n_seeds"])),
            ))

        plot_gain_comparison(summary_df, PLOT_DIR)

    print("\nDone.")


if __name__ == "__main__":
    main()
