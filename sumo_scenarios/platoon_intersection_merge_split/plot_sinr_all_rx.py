#!/usr/bin/env python3
"""All 9 receivers on one axis — single-TX (leader v0), two frequencies.

Replaces the original all-tx `sinr_all_rx.png` with the single-TX model
the user asked for.  Each receiver gets two lines:

    solid  (5.9 GHz V2V Urban, 10 MHz ch.)
    dashed (mmWave 28 GHz UMi Street Canyon, 100 MHz ch.)

Geometry (LOS / NLOSv / NLOS) is identical between the two — only the
dB conversion changes.  Self-link is excluded (no SINR for v0).

QPSK 1/2 reference at 5 dB.  Vertical grey bands mark the four
crossing events: joiner entries, J1 / J2 crossings, leaver departures.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parent
SUB6_CSV = HERE / "distances_classified_singletx_5.9ghz.csv"
MMW_CSV  = HERE / "distances_classified_singletx_mmwave.csv"
PNG_OUT  = HERE / "sinr_all_rx.png"

LEADER_ID = 0

# Receiver palette (matches the rest of the figures in this directory).
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


def main():
    sub6 = pd.read_csv(SUB6_CSV)
    mmw  = pd.read_csv(MMW_CSV)
    sub6 = sub6[sub6.tx == LEADER_ID].sort_values(["rx", "time"])
    mmw  = mmw[mmw.tx == LEADER_ID].sort_values(["rx", "time"])
    rx_ids = sorted(r for r in sub6.rx.unique() if r != LEADER_ID)

    fig, ax = plt.subplots(figsize=(15, 7))

    for rx in rx_ids:
        color, label = RX_COLORS[rx]
        s = sub6[sub6.rx == rx]
        m = mmw[mmw.rx == rx]
        ax.plot(s.time, s.sinr_db, color=color, lw=1.4,
                label=f"{label} — 5.9 GHz")
        ax.plot(m.time, m.sinr_db, color=color, lw=1.4, ls="--",
                label=f"{label} — 28 GHz")

    ax.axhline(5, color="grey", lw=0.4, ls=":", label="QPSK 1/2 min SNR ≈ 5 dB")
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel("SINR (dB) — receiving from leader v0")
    ax.set_ylim(-60, 70)
    ax.grid(True, alpha=0.25)
    ax.set_title("SINR per receiver vs time (single-TX, leader v0; "
                 "solid = 5.9 GHz, dashed = mmWave 28 GHz)")
    # Compact legend: 3 columns × 7 rows fits.
    ax.legend(loc="lower right", ncol=3, fontsize=7)

    fig.tight_layout()
    fig.savefig(PNG_OUT, dpi=130)
    print(f"Wrote {PNG_OUT}")


if __name__ == "__main__":
    main()