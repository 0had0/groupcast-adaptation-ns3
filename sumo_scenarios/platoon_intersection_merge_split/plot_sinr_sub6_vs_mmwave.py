#!/usr/bin/env python3
"""SINR-per-rx overlay: 5.9 GHz vs mmWave 28 GHz (single-TX, leader only).

For each receiver, draws two curves on the same axis:
  * 5.9 GHz SINR (solid, V2V Urban path loss, 10 MHz ch.)
  * mmWave 28 GHz SINR (dashed, UMi Street Canyon, 100 MHz ch.)

Geometry (LOS / NLOSv / NLOS) is identical between the two — only the
dB conversion changes.  Layout is a 5×2 small-multiples grid, one
panel per receiver.

Output: `sinr_per_rx_sub6_vs_mmwave.png` next to the CSVs.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parent
SUB6_CSV  = HERE / "distances_classified_singletx_5.9ghz.csv"
MMW_CSV   = HERE / "distances_classified_singletx_mmwave.csv"
PNG_OUT   = HERE / "sinr_per_rx_sub6_vs_mmwave.png"

LEADER_ID = 0


def main():
    sub6 = pd.read_csv(SUB6_CSV)
    mmw  = pd.read_csv(MMW_CSV)
    # Restrict to leader's outgoing rows.
    sub6 = sub6[sub6.tx == LEADER_ID].sort_values(["rx", "time"])
    mmw  = mmw[mmw.tx == LEADER_ID].sort_values(["rx", "time"])
    rx_ids = sorted(sub6.rx.unique())

    fig, axes = plt.subplots(5, 2, figsize=(15, 12), sharex=True)
    axes = axes.flatten()

    for idx, rx in enumerate(rx_ids):
        ax = axes[idx]
        s = sub6[sub6.rx == rx]
        m = mmw[mmw.rx == rx]
        ax.plot(s.time, s.sinr_db, color="#1f77b4", lw=1.6,
                label="5.9 GHz  (V2V Urban, 10 MHz)")
        ax.plot(m.time, m.sinr_db, color="#d62728", lw=1.6, ls="--",
                label="mmWave 28 GHz  (UMi, 100 MHz)")
        ax.axhline(5, color="grey", lw=0.4, ls=":",
                   label="QPSK 1/2 min SNR ≈ 5 dB")
        ax.set_ylim(-60, 70)
        ax.grid(True, alpha=0.25)
        ax.set_title(f"v{rx}" + (" (leader)" if rx == LEADER_ID
                                  else " (joiner)" if rx in (8, 9)
                                  else " (leaver)" if rx in (1, 3, 6)
                                  else ""), fontsize=10)
        if idx in (0, 1):
            ax.legend(loc="upper right", fontsize=7)
        if idx % 2 == 0:
            ax.set_ylabel("SINR (dB)")

    for ax in axes[-2:]:
        ax.set_xlabel("Simulation time (s)")

    fig.suptitle("SINR per receiver — 5.9 GHz vs mmWave 28 GHz "
                 "(single-TX, leader only)",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(PNG_OUT, dpi=130)
    print(f"Wrote {PNG_OUT}")


if __name__ == "__main__":
    main()