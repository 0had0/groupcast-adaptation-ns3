#!/usr/bin/env python3
"""All receivers' SINR-over-time on one plot.

Reads `distances_classified.csv`, extracts the leader's outgoing rows,
and plots SINR (dB) versus time for every receiver (v0..v9) on a
single axis.  Each receiver gets its own colour.  Self-link is
excluded.  When a receiver is inactive, the line is broken (NaN).

Output: `sinr_all_rx.png` next to the CSV.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parent
CSV_IN  = HERE / "distances_classified.csv"
PNG_OUT = HERE / "sinr_all_rx.png"

LEADER_ID = 0
_PALETTE = [
    "#e41a1c",  # 0 leader (unused, self-link)
    "#377eb8",  # 1
    "#4daf4a",  # 2
    "#984ea3",  # 3
    "#ff7f00",  # 4
    "#a65628",  # 5
    "#f781bf",  # 6
    "#1b9e77",  # 7
    "#d95f02",  # 8 j0
    "#7570b3",  # 9 j1
]


def main():
    df = pd.read_csv(CSV_IN)
    leader = df[df.tx == LEADER_ID].copy()

    fig, ax = plt.subplots(figsize=(15, 7))
    for rx in sorted(leader.rx.unique()):
        if rx == LEADER_ID:
            continue
        sub = leader[leader.rx == rx].sort_values("time")
        ax.plot(sub.time, sub.sinr_db,
                color=_PALETTE[rx], lw=1.2,
                label=f"v{rx}" + (" (joiner)" if rx in (8, 9)
                                   else " (leaver)" if rx in (1, 3, 6)
                                   else ""))

    ax.axhline(5, color="grey", lw=0.5, ls=":",
               label="QPSK 1/2 min SNR ≈ 5 dB")
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel("SINR (dB)  — receiving from leader v0")
    ax.set_title("SINR per receiver vs time (all rx, one plot)")
    ax.grid(True, alpha=0.25)
    ax.legend(ncol=6, loc="lower left", fontsize=9, framealpha=0.9)
    ax.set_xlim(leader.time.min(), leader.time.max())

    fig.tight_layout()
    fig.savefig(PNG_OUT, dpi=130)
    print(f"Wrote {PNG_OUT}  ({len(leader.rx.unique()) - 1} receivers, "
          f"{len(leader.time.unique())} frames)")


if __name__ == "__main__":
    main()