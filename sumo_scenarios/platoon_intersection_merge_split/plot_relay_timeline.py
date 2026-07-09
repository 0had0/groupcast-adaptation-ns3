#!/usr/bin/env python3
"""Timeline plot of the chosen relay.

Reads `relay_selection.csv` and produces a two-panel figure:

  Top    : relay id (categorical 0–9) over time, coloured by link
           class.  Gaps (no eligible relay) are drawn in grey.
  Bottom : leader-to-relay SNR (dB) over time.

A vertical dashed line marks each "scenario event" detected from the
FCD trace (`platoon_intersection_merge_split.mob.csv`):

  * joiner entered the scenario  (j0 / j1 first appear)
  * leaver crossed x = 320 east  (departs the groupcast)
  * leader crossed J1            (y = 200 going north)
  * leader crossed J2            (x = 300 going east)

Output: `relay_selection_timeline.png` next to the CSV.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")                 # headless
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RELAY_CSV  = HERE / "relay_selection.csv"
MOB_CSV    = HERE / "platoon_intersection_merge_split.mob.csv"
PNG_OUT    = HERE / "relay_selection_timeline.png"

# Map link class → colour.  Matches the HTML viewer / scatter plot.
LINK_COLOR = {
    "LOS":   "#1f77b4",     # blue
    "NLOSv": "#ff7f0e",     # orange
    None:    "#bfbfbf",     # grey for gaps
}

# Distinct colour per relay id (categorical), so the eye can follow a
# single vehicle's tenure.  Pastel palette + black for gaps.
_RELAY_PALETTE = [
    "#e41a1c",  # 0  leader — only used if the leader itself is picked
    "#377eb8",  # 1
    "#4daf4a",  # 2  (dominant steady-state)
    "#984ea3",  # 3
    "#ff7f00",  # 4
    "#a65628",  # 5
    "#f781bf",  # 6
    "#1b9e77",  # 7
    "#d95f02",  # 8  j0
    "#7570b3",  # 9  j1
]
RELAY_COLOR = {i: c for i, c in enumerate(_RELAY_PALETTE)}


def detect_events(mob):
    """Detect four kinds of events from the FCD trace."""
    events = []     # list of (time, label, color, linestyle)

    # Joiners first appearance.
    for nid in (8, 9):
        first = mob[mob.node_id == nid]
        if not first.empty:
            t0 = float(first.time.min())
            events.append((t0, f"joiner v{nid} enters",
                           "#2ca02c", ":"))    # green dotted
    # Leavers departing: first time the leaver's x exceeds the departed
    # threshold (same constant as relay_selection.py).  We don't need a
    # y check because the leaver's lane (y≈198.4) coincides with the
    # leader's east-bound approach; x > 310 uniquely identifies "past
    # the J2 corner".
    from relay_selection import LEAVER_DEPARTED_X
    for nid in (1, 3, 6):
        rows = mob[mob.node_id == nid].sort_values("time")
        departed = rows[rows.x > LEAVER_DEPARTED_X]
        if not departed.empty:
            t0 = float(departed.time.iloc[0])
            events.append((t0, f"leaver v{nid} departs",
                           "#d62728", ":"))    # red dotted
    # Leader crossing J1 (y = 200 going north): first time after t=0
    # that v0's y ≥ 200.
    leader = mob[mob.node_id == 0].sort_values("time")
    cross_j1 = leader[(leader.y >= 200.0) & (leader.time > 0.0)]
    if not cross_j1.empty:
        events.append((float(cross_j1.time.iloc[0]), "leader crosses J1",
                       "#000000", "--"))       # black dashed
    # Leader crossing J2 (x = 300 going east): first time after J1
    # crossing that v0's x ≥ 300 AND y < 210 (still near J2).
    after_j1 = leader[leader.time >= (
        float(cross_j1.time.iloc[0]) if not cross_j1.empty else 0.0)]
    cross_j2 = after_j1[after_j1.x >= 300.0]
    if not cross_j2.empty:
        events.append((float(cross_j2.time.iloc[0]), "leader crosses J2",
                       "#000000", "--"))       # black dashed

    events.sort(key=lambda e: e[0])
    return events


def main():
    df = pd.read_csv(RELAY_CSV)
    mob = pd.read_csv(MOB_CSV)
    mob.columns = [c.strip().lstrip("#").strip() for c in mob.columns]
    mob["node_id"] = mob["node_id"].astype(int)

    events = detect_events(mob)

    fig, (ax_relay, ax_snr) = plt.subplots(
        2, 1, figsize=(13, 6), sharex=True,
        gridspec_kw=dict(height_ratios=[1.4, 1.0]),
    )

    # Build (start, end, rid, link) runs so each bar is one contiguous
    # segment of identical (rid, link) — same as the text timeline.
    def _key(r):
        rid = None if pd.isna(r.relay_id) else int(r.relay_id)
        link = None if pd.isna(r.relay_link) else str(r.relay_link)
        return (rid, link)

    runs = []
    run_start = None
    run_key = None
    last_t = None
    for _, r in df.iterrows():
        k = _key(r)
        if k != run_key:
            if run_key is not None:
                runs.append((run_start, last_t,
                             run_key[0], run_key[1]))
            run_start = float(r.time)
            run_key = k
        last_t = float(r.time)
    if run_key is not None:
        runs.append((run_start, last_t, run_key[0], run_key[1]))

    # Draw one coloured rectangle per run.
    # The relay_id axis is categorical; we'll use a numeric y from 0..9.
    for t0, t1, rid, link in runs:
        dur = (t1 - t0) + 0.1
        if rid is None:
            color = LINK_COLOR[None]
            ax_relay.broken_barh([(t0, dur)], (-0.5, 10.0),
                                 facecolor=color, edgecolor="none",
                                 alpha=0.45)
            continue
        edge = LINK_COLOR.get(link, "#444444")
        face = RELAY_COLOR.get(rid, "#888888")
        ax_relay.broken_barh([(t0, dur)], (rid - 0.45, 0.9),
                             facecolor=face, edgecolor=edge,
                             linewidth=1.2)

    # Overlay the leader-to-relay SNR as a line plot, restricted to
    # frames where a relay was chosen.
    chosen = df[df.relay_id.notna() & df.relay_snr_db.notna()]
    ax_snr.plot(chosen.time, chosen.relay_snr_db,
                color="#1f77b4", lw=1.0, label="SNR (leader → relay)")
    # Mark the theoretical SNR for 802.11p QPSK 1/2 (~5 dB) for context.
    ax_snr.axhline(5, color="grey", lw=0.5, ls=":")
    ax_snr.text(df.time.max(), 5, " QPSK 1/2 min SNR ≈ 5 dB",
                color="grey", fontsize=8, va="bottom", ha="right")

    # Event annotations on both panels.  With 7+ events often clustered
    # in a few seconds, we stack the labels at progressively lower y
    # positions: each label is placed on its own "row" above the relay
    # bars, in temporal order.  Closely-spaced events therefore sit
    # one above the other instead of overprinting.
    n_events = len(events)
    # Use up to 8 stacked rows between y = 10.95 and y = 9.65.
    label_rows = list(np.linspace(10.95, 9.65,
                                   num=min(8, max(1, n_events))))
    for i, (t0, label, color, ls) in enumerate(events):
        ax_relay.axvline(t0, color=color, ls=ls, lw=0.7, alpha=0.7)
        ax_snr.axvline(t0, color=color, ls=ls, lw=0.7, alpha=0.7)
        y_text = label_rows[i % len(label_rows)]
        # Place the label slightly to the right of the event line so
        # it doesn't overlap the dashed line itself.
        x_text = t0 + 0.1
        ax_relay.text(x_text, y_text, label, color=color, fontsize=7,
                      rotation=90, va="top", ha="left",
                      bbox=dict(facecolor="white", edgecolor="none",
                                alpha=0.75, pad=0.4))
    # Reserve space above the bars for the stacked labels.
    ax_relay.set_ylim(-0.7, 12.2)

    # ── Cosmetics ────────────────────────────────────────────────────────
    ax_relay.set_yticks(list(range(10)))
    ax_relay.set_yticklabels([f"v{i}" for i in range(10)])
    ax_relay.set_ylim(-0.7, 10.5)
    ax_relay.set_ylabel("Chosen relay")
    ax_relay.set_xlim(df.time.min(), df.time.max())
    ax_relay.grid(True, axis="y", alpha=0.25)

    ax_snr.set_xlabel("Simulation time (s)")
    ax_snr.set_ylabel("SNR to leader (dB)")
    ax_snr.grid(True, alpha=0.25)
    ax_snr.legend(loc="upper right", fontsize=8)

    # Legend patches for the link classes (right side).
    handles = [mpatches.Patch(facecolor=LINK_COLOR["LOS"],   label="LOS"),
               mpatches.Patch(facecolor=LINK_COLOR["NLOSv"], label="NLOSv"),
               mpatches.Patch(facecolor=LINK_COLOR[None],    label="gap (no relay)")]
    ax_relay.legend(handles=handles, loc="upper right",
                    fontsize=8, framealpha=0.9)

    fig.suptitle("Relay selection timeline — platoon_intersection_merge_split",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(PNG_OUT, dpi=130)
    print(f"Wrote {PNG_OUT}")


if __name__ == "__main__":
    main()