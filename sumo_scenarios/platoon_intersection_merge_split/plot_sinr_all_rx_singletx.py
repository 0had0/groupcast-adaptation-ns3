#!/usr/bin/env python3
"""All 9 receivers on one axis — **per frequency profile**, single-TX.

Replaces the combined `sinr_all_rx.png` with two separate figures, one
per frequency, mirroring the per-rx split:

  * `sinr_all_rx_5.9ghz.png`  (5.9 GHz V2V Urban, 10 MHz ch.)
  * `sinr_all_rx_mmwave.png`  (mmWave 28 GHz UMi Street Canyon, 100 MHz ch.)

Each figure shows every active receiver's SINR over time on a single
axis, with the leader's outgoing link as the only signal (single-TX).
Self-link excluded.  The line breaks (NaN) where a receiver is
inactive (joiner pre-entry, leaver post-departure, leader gone).

QPSK 1/2 reference line at 5 dB.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parent
PROFILES = [
    (HERE / "distances_classified_singletx_5.9ghz.csv",
     HERE / "sinr_all_rx_5.9ghz.png",
     "5.9 GHz  (V2V Urban, 10 MHz ch.)"),
    (HERE / "distances_classified_singletx_mmwave.csv",
     HERE / "sinr_all_rx_mmwave.png",
     "mmWave 28 GHz  (UMi Street Canyon, 100 MHz ch.)"),
]

LEADER_ID = 0
RX_COLORS = {
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


def render(csv_in, png_out, title):
    df = pd.read_csv(csv_in)
    df = df[df.tx == LEADER_ID].sort_values(["rx", "time"])
    rx_ids = sorted(r for r in df.rx.unique() if r != LEADER_ID)

    fig, ax = plt.subplots(figsize=(15, 7))
    for rx in rx_ids:
        color, label = RX_COLORS[rx]
        s = df[df.rx == rx]
        ax.plot(s.time, s.sinr_db, color=color, lw=1.6, label=label)
    ax.axhline(5, color="grey", lw=0.4, ls=":",
               label="QPSK 1/2 min SNR ≈ 5 dB")
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel("SINR (dB) — receiving from leader v0")
    ax.set_ylim(-60, 70)
    ax.grid(True, alpha=0.25)
    ax.set_title(f"SINR per receiver vs time — {title}  ·  single-TX")
    ax.legend(loc="lower right", ncol=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(png_out, dpi=130)
    print(f"Wrote {png_out}")


def main():
    for csv_in, png_out, title in PROFILES:
        render(csv_in, png_out, title)


if __name__ == "__main__":
    main()