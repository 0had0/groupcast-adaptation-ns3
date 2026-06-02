#!/usr/bin/env python3
"""Speed and distance-from-leader plots for platoon_merge_incremental."""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

df = pd.read_csv(SCRIPT_DIR / "platoon_merge_incremental.mob.csv",
                 comment="#", names=["time", "node_id", "x", "y", "speed"])

leader = df[df.node_id == 0].set_index("time")["x"]
all_times = sorted(df["time"].unique())

# Merge events (approximate insertion times from simulation log)
MERGE_EVENTS = {
    "Jct 1\n(+1)": 36.2,
    "Jct 2\n(+2)": 111.2,
    "Jct 3\n(+3)": 204.3,
    "Jct 4\n(+4)": 312.9,
    "Jct 5\n(+5)": 421.5,
}

n_nodes = df["node_id"].nunique()
cmap = plt.cm.get_cmap("tab20", n_nodes)
colors = {nid: cmap(i) for i, nid in enumerate(sorted(df["node_id"].unique()))}

# ── Figure 1: Speed in km/h ──────────────────────────────────────────────────
fig1, ax1 = plt.subplots(figsize=(14, 6))

for nid in sorted(df["node_id"].unique()):
    sub = df[df.node_id == nid]
    label = f"v{nid}" if nid < 8 else f"j{nid-7}"
    ax1.plot(sub["time"], sub["speed"] * 3.6,
             lw=0.8, alpha=0.75, color=colors[nid], label=label)

for label, t in MERGE_EVENTS.items():
    ax1.axvline(t, color="red", lw=1.2, ls="--", alpha=0.7)
    ax1.text(t + 1.5, ax1.get_ylim()[1] if ax1.get_ylim()[1] > 0 else 200,
             label, fontsize=7, color="red", va="top")

ax1.axhline(33.333 * 3.6, color="k", lw=0.8, ls=":", alpha=0.5,
            label="V_highway (120 km/h)")
ax1.set_xlabel("Time (s)")
ax1.set_ylabel("Speed (km/h)")
ax1.set_title("Platoon Merge Incremental — Speed Variation")
ax1.legend(ncol=4, fontsize=7, loc="upper right")
ax1.set_xlim(0, df["time"].max())
ax1.set_ylim(0)
fig1.tight_layout()
fig1.savefig(SCRIPT_DIR / "merge_speed.png", dpi=150)
print(f"Saved merge_speed.png")

# ── Figure 2: Distance from leader ──────────────────────────────────────────
fig2, ax2 = plt.subplots(figsize=(14, 6))

for nid in sorted(df["node_id"].unique()):
    if nid == 0:
        continue
    sub = df[df.node_id == nid].copy()
    sub = sub.merge(
        leader.rename("x_lead").reset_index(),
        on="time", how="left"
    )
    sub["dist"] = sub["x_lead"] - sub["x"]
    label = f"v{nid}" if nid < 8 else f"j{nid-7}"
    ax2.plot(sub["time"], sub["dist"],
             lw=0.8, alpha=0.75, color=colors[nid], label=label)

for label, t in MERGE_EVENTS.items():
    ax2.axvline(t, color="red", lw=1.2, ls="--", alpha=0.7)

ax2.set_xlabel("Time (s)")
ax2.set_ylabel("Distance from leader v0 (m)")
ax2.set_title("Platoon Merge Incremental — Distance from Leader")
ax2.legend(ncol=4, fontsize=7, loc="upper left")
ax2.set_xlim(0, df["time"].max())
ax2.set_ylim(bottom=0)
fig2.tight_layout()
fig2.savefig(SCRIPT_DIR / "merge_distance.png", dpi=150)
print(f"Saved merge_distance.png")

# ── Figure 3: Platoon size over time ─────────────────────────────────────────
counts = df.groupby("time")["node_id"].count()
fig3, ax3 = plt.subplots(figsize=(10, 3))
ax3.step(counts.index, counts.values, where="post", color="steelblue", lw=1.5)
for label, t in MERGE_EVENTS.items():
    ax3.axvline(t, color="red", lw=1.0, ls="--", alpha=0.7)
ax3.set_xlabel("Time (s)")
ax3.set_ylabel("Platoon size")
ax3.set_title("Platoon size growth")
ax3.set_xlim(0, df["time"].max())
ax3.set_yticks([8, 9, 11, 14, 18, 23])
fig3.tight_layout()
fig3.savefig(SCRIPT_DIR / "merge_size.png", dpi=150)
print(f"Saved merge_size.png")
