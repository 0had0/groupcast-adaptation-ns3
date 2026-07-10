#!/usr/bin/env python3
"""SINR-vs-time plot, one panel per receiver.

Reads `distances_classified.csv` and produces a grid of small-multiples:

  * 10 panels (one per receiver, v0 … v9) laid out in a 2 × 5 grid.
  * Top panel: SINR (dB) when the receiver is listening to the leader
    (v0).  This is the SINR that the leader's groupcast actually
    sees at the receiver.
  * Bottom panel: SNR (dB) — same link budget, no interference —
    so the gap between SINR and SNR reveals how much interference
    costs at that receiver.
  * Background colour band encodes the link class on each 0.1 s step:
        LOS   light blue
        NLOSv light orange
        NLOS  light red
        self  white
        inactive grey
  * Vertical dashed lines mark the scenario events (J1 / J2
    crossings, leaver departures, joiner entries).

Output: `sinr_per_rx.png` next to the CSV.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CSV_IN  = HERE / "distances_classified.csv"
MOB_CSV = HERE / "platoon_intersection_merge_split.mob.csv"
PNG_OUT = HERE / "sinr_per_rx.png"

# Link-class → light background colour (matches the other plots).
LINK_BG = {
    "LOS":   "#cfe2ff",   # light blue
    "NLOSv": "#ffe5b4",   # light orange
    "NLOS":  "#ffd1d1",   # light red
    "self":  "#ffffff",
    None:    "#eaeaea",   # inactive (rx not in scenario)
}

# Per-rx line colour — same palette as the relay timeline.
_RX_PALETTE = [
    "#e41a1c",  # 0  leader
    "#377eb8",  # 1
    "#4daf4a",  # 2
    "#984ea3",  # 3
    "#ff7f00",  # 4
    "#a65628",  # 5
    "#f781bf",  # 6
    "#1b9e77",  # 7
    "#d95f02",  # 8  j0
    "#7570b3",  # 9  j1
]
LEADER_ID = 0


def detect_events(mob):
    """Same event list as plot_relay_timeline.py — share via a local
    import rather than copy-pasting the logic.
    """
    from plot_relay_timeline import detect_events
    return detect_events(mob)


def shade_link_background(ax, t, link):
    """Fill vertical bands matching the link-class sequence."""
    if len(t) == 0:
        return
    t = np.asarray(t, dtype=float)
    link = np.asarray(link, dtype=object)
    # Compute run-length encoded segments.
    cur = link[0]
    start = t[0]
    for i in range(1, len(link)):
        if link[i] != cur:
            ax.axvspan(start, t[i],
                       facecolor=LINK_BG.get(cur, "#ffffff"),
                       alpha=0.6, linewidth=0, zorder=0)
            cur = link[i]
            start = t[i]
    ax.axvspan(start, t[-1] + 0.1,
               facecolor=LINK_BG.get(cur, "#ffffff"),
               alpha=0.6, linewidth=0, zorder=0)


def main():
    df = pd.read_csv(CSV_IN)
    mob = pd.read_csv(MOB_CSV)
    mob.columns = [c.strip().lstrip("#").strip() for c in mob.columns]
    mob["node_id"] = mob["node_id"].astype(int)
    events = detect_events(mob)

    # Pull every (time, rx) pair from the leader's outgoing rows so we
    # have a complete timeline of the link state.
    leader = df[df.tx == LEADER_ID].copy()
    leader = leader.sort_values(["rx", "time"]).reset_index(drop=True)

    # We need rx present at every time so the shading lines up.  Build
    # a (time, rx) grid and fill missing combinations with NaN / None.
    all_times = sorted(df.time.unique())
    rx_ids = sorted(leader.rx.unique())
    full = pd.MultiIndex.from_product([all_times, rx_ids],
                                      names=["time", "rx"]).to_frame_index(
                                          index=False) if False else \
        pd.DataFrame([(t, r) for t in all_times for r in rx_ids],
                     columns=["time", "rx"])
    merged = full.merge(leader[["time", "rx", "link", "snr_db", "sinr_db"]],
                        on=["time", "rx"], how="left")

    # Mark "self" and inactive cells.
    merged.loc[merged.rx == LEADER_ID, "link"] = "self"
    merged["is_inactive"] = merged.snr_db.isna() & (merged.rx != LEADER_ID)
    merged.loc[merged.is_inactive, "link"] = None

    fig, axes = plt.subplots(5, 2, figsize=(15, 12), sharex=True)
    axes = axes.flatten()

    for idx, rx in enumerate(rx_ids):
        ax = axes[idx]
        sub = merged[merged.rx == rx].sort_values("time")
        # Top panel: SINR (only the link to leader is meaningful).
        sinr = sub.snr_db.where(sub.link != "self")   # reuse snr_db col?
        # We have both SNR and SINR columns; use them.
        snr  = sub.snr_db.copy()
        sinr = sub.snr_db.copy()                     # placeholder
        # Plot SNR as a thin line, SINR as a thick line on top.
        ax.plot(sub.time, sub.snr_db, color=_RX_PALETTE[rx],
                lw=1.0, alpha=0.55, label="SNR (no interferers)")
        ax.plot(sub.time, sub.sinr_db, color=_RX_PALETTE[rx],
                lw=1.8, alpha=1.0, label="SINR")
        # Shade background by link class.
        shade_link_background(ax, sub.time, sub.link)
        # Reference line at 802.11p QPSK 1/2 min SNR (~5 dB).
        ax.axhline(5, color="grey", lw=0.4, ls=":")
        # Event lines.
        for t0, _, color, ls in events:
            ax.axvline(t0, color=color, ls=ls, lw=0.4, alpha=0.6)
        ax.set_title(f"v{rx}" + (" (leader)" if rx == LEADER_ID
                                  else " (joiner)" if rx in (8, 9)
                                  else " (leaver)" if rx in (1, 3, 6)
                                  else ""), fontsize=10)
        ax.set_ylabel("dB")
        ax.grid(True, alpha=0.25)
        ax.set_xlim(all_times[0], all_times[-1])
        ax.set_ylim(-60, 70)
        if idx == 0:
            ax.legend(loc="upper right", fontsize=7)

    axes[-2].set_xlabel("Simulation time (s)")
    axes[-1].set_xlabel("Simulation time (s)")

    # Shared legend for the link-class shading at the very top.
    handles = [mpatches.Patch(facecolor=LINK_BG["LOS"],   label="LOS"),
               mpatches.Patch(facecolor=LINK_BG["NLOSv"], label="NLOSv"),
               mpatches.Patch(facecolor=LINK_BG["NLOS"],  label="NLOS"),
               mpatches.Patch(facecolor=LINK_BG[None],    label="inactive")]
    fig.legend(handles=handles, loc="upper center",
               ncol=4, fontsize=9, frameon=False,
               bbox_to_anchor=(0.5, 1.005))

    fig.suptitle("SINR / SNR per receiver (link from leader, v0) — "
                 "platoon_intersection_merge_split",
                 fontsize=12, y=1.015)
    fig.tight_layout()
    fig.savefig(PNG_OUT, dpi=130, bbox_inches="tight")
    print(f"Wrote {PNG_OUT}")


if __name__ == "__main__":
    main()