#!/usr/bin/env python3
"""Distance-from-leader and speed traces for every vehicle in the
platoon_intersection_merge_split scenario.

Reads the FCD trace (`platoon_intersection_merge_split.mob.csv`) and
produces two figures:

  * `distance_from_leader.png` — Euclidean distance from leader v0
    for each non-leader vehicle, vs time.  One line per receiver,
    color-coded as in the other figures in this directory.
  * `speed_trace.png` — speed (m/s) of every vehicle (including
    leader), vs time.  Same colour coding.

Self-link excluded from the distance plot (v0 == v0, always 0).

Output: `distance_from_leader.png` and `speed_trace.png` next to the
FCD trace.
"""
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parent
MOB_CSV = HERE / "platoon_intersection_merge_split.mob.csv"
DIST_PNG = HERE / "distance_from_leader.png"
SPEED_PNG = HERE / "speed_trace.png"

LEADER_ID = 0
RX_COLORS = {
    0: ("#000000", "v0 (leader)"),
    1: ("#1f77b4", "v1 (leaver)"),
    2: ("#2ca02c", "v2"),
    3: ("#9467bd", "v3 (leaver)"),
    4: ("#ff7f0e", "v4"),
    5: ("#8c564b", "v5"),
    6: ("#e377c2", "v6 (leaver)"),
    7: ("#17becf", "v7"),
    8: ("#d62728", "v8 (joiner)"),
    9: ("#7f7f7f", "v9 (joiner)"),
}


def load_mob():
    df = pd.read_csv(MOB_CSV, comment="#", header=None,
                      names=["time", "node_id", "x", "y", "speed"])
    df["node_id"] = df["node_id"].astype(int)
    return df


def add_events(ax, mob):
    """Share event detection with the other plots in this directory."""
    from plot_relay_timeline import detect_events
    events = detect_events(mob)
    for t0, label, color, ls in events:
        ax.axvline(t0, color=color, ls=ls, lw=0.4, alpha=0.6)


def plot_distance(mob):
    leader = mob[mob.node_id == LEADER_ID][["time", "x", "y"]].rename(
        columns={"x": "lx", "y": "ly"})
    merged = mob.merge(leader, on="time", how="left")
    merged["dist"] = ((merged.x - merged.lx) ** 2
                      + (merged.y - merged.ly) ** 2) ** 0.5

    fig, ax = plt.subplots(figsize=(15, 7))
    for nid in sorted(mob.node_id.unique()):
        if nid == LEADER_ID:
            continue
        color, label = RX_COLORS[nid]
        s = merged[merged.node_id == nid].sort_values("time")
        ax.plot(s.time, s.dist, color=color, lw=1.6, label=label)

    add_events(ax, mob)
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel("Distance from leader v0 (m)")
    ax.set_title("Per-receiver distance from leader v0  ·  "
                 "platoon_intersection_merge_split")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper right", ncol=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(DIST_PNG, dpi=130)
    print(f"Wrote {DIST_PNG}")


def plot_speed(mob):
    fig, ax = plt.subplots(figsize=(15, 7))
    for nid in sorted(mob.node_id.unique()):
        color, label = RX_COLORS[nid]
        s = mob[mob.node_id == nid].sort_values("time")
        lw = 2.0 if nid == LEADER_ID else 1.4
        ax.plot(s.time, s.speed, color=color, lw=lw, label=label)

    add_events(ax, mob)
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel("Speed (m/s)")
    ax.set_title("Per-vehicle speed  ·  "
                 "platoon_intersection_merge_split")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper right", ncol=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(SPEED_PNG, dpi=130)
    print(f"Wrote {SPEED_PNG}")


def main():
    mob = load_mob()
    plot_distance(mob)
    plot_speed(mob)


if __name__ == "__main__":
    main()