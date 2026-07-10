#!/usr/bin/env python3
"""SINR-vs-time plot, one panel per receiver, **per frequency profile**.

Mirrors `plot_sinr_per_rx.py` but produces two separate figures:

  * `sinr_per_rx_5.9ghz.png`  (5.9 GHz V2V Urban, 10 MHz ch.)
  * `sinr_per_rx_mmwave.png`  (28 GHz UMi Street Canyon, 100 MHz ch.)

Each figure is a 2×5 small-multiples grid (one panel per receiver
v0…v9) showing the link from the leader (v0):

  * thick line  = SINR (dB)
  * thin line   = SNR (dB)  — should equal SINR in single-TX mode
  * background  = link class on each 0.1 s step
        LOS   light blue   |   NLOSv light orange
        NLOS  light red    |   inactive grey
  * vertical event lines (joiner entries, J1/J2 crossings, leaver
    departures) shared with the rest of the figures in this directory.

Both inputs are single-TX (only the leader transmits), so the two
curves coincide.  The figures differ only in the dB conversion.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
MOB_CSV = HERE / "platoon_intersection_merge_split.mob.csv"

# (input CSV, output PNG, figure title suffix, line colour)
PROFILES = [
    (HERE / "distances_classified_singletx_5.9ghz.csv",
     HERE / "sinr_per_rx_5.9ghz.png",
     "5.9 GHz  (V2V Urban, 10 MHz ch.)",
     "#1f77b4"),
    (HERE / "distances_classified_singletx_mmwave.csv",
     HERE / "sinr_per_rx_mmwave.png",
     "mmWave 28 GHz  (UMi Street Canyon, 100 MHz ch.)",
     "#d62728"),
]

LEADER_ID = 0

LINK_BG = {
    "LOS":   "#cfe2ff",
    "NLOSv": "#ffe5b4",
    "NLOS":  "#ffd1d1",
    "self":  "#ffffff",
    None:    "#eaeaea",
}

_RX_PALETTE = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00",
    "#a65628", "#f781bf", "#1b9e77", "#d95f02", "#7570b3",
]


def detect_events(mob):
    from plot_relay_timeline import detect_events
    return detect_events(mob)


def shade_link_background(ax, t, link):
    if len(t) == 0:
        return
    t = np.asarray(t, dtype=float)
    link = np.asarray(link, dtype=object)
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


def render(csv_in, png_out, title_suffix, snr_line_color, events):
    df = pd.read_csv(csv_in)
    leader = df[df.tx == LEADER_ID].copy()
    leader = leader.sort_values(["rx", "time"]).reset_index(drop=True)

    all_times = sorted(df.time.unique())
    rx_ids = sorted(leader.rx.unique())
    full = pd.DataFrame([(t, r) for t in all_times for r in rx_ids],
                        columns=["time", "rx"])
    merged = full.merge(leader[["time", "rx", "link", "snr_db", "sinr_db"]],
                        on=["time", "rx"], how="left")
    merged.loc[merged.rx == LEADER_ID, "link"] = "self"
    merged["is_inactive"] = merged.snr_db.isna() & (merged.rx != LEADER_ID)
    merged.loc[merged.is_inactive, "link"] = None

    fig, axes = plt.subplots(5, 2, figsize=(15, 12), sharex=True)
    axes = axes.flatten()

    for idx, rx in enumerate(rx_ids):
        ax = axes[idx]
        sub = merged[merged.rx == rx].sort_values("time")
        # Single-TX mode: SNR and SINR coincide.  Still draw both so
        # the legend stays consistent with the all-tx counterpart.
        ax.plot(sub.time, sub.snr_db,  color=_RX_PALETTE[rx],
                lw=1.0, alpha=0.55, label="SNR = SINR (1 TX)")
        ax.plot(sub.time, sub.sinr_db, color=_RX_PALETTE[rx],
                lw=1.8, alpha=1.0)
        shade_link_background(ax, sub.time, sub.link)
        ax.axhline(5, color="grey", lw=0.4, ls=":",
                   label="QPSK 1/2 min SNR ≈ 5 dB")
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

    for ax in axes[-2:]:
        ax.set_xlabel("Simulation time (s)")

    handles = [mpatches.Patch(facecolor=LINK_BG["LOS"],   label="LOS"),
               mpatches.Patch(facecolor=LINK_BG["NLOSv"], label="NLOSv"),
               mpatches.Patch(facecolor=LINK_BG["NLOS"],  label="NLOS"),
               mpatches.Patch(facecolor=LINK_BG[None],    label="inactive")]
    fig.legend(handles=handles, loc="upper center",
               ncol=4, fontsize=9, frameon=False,
               bbox_to_anchor=(0.5, 1.005))
    fig.suptitle(f"SINR per receiver (link from leader v0) — "
                 f"{title_suffix}  ·  single-TX",
                 fontsize=12, y=1.015)
    fig.tight_layout()
    fig.savefig(png_out, dpi=130, bbox_inches="tight")
    print(f"Wrote {png_out}")


def main():
    mob = pd.read_csv(MOB_CSV)
    mob.columns = [c.strip().lstrip("#").strip() for c in mob.columns]
    mob["node_id"] = mob["node_id"].astype(int)
    events = detect_events(mob)

    for csv_in, png_out, title, color in PROFILES:
        render(csv_in, png_out, title, color, events)


if __name__ == "__main__":
    main()