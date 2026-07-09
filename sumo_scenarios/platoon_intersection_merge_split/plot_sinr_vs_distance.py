#!/usr/bin/env python3
"""Scatter plot of (SNR | SINR) vs distance, color-coded by link class.

Reads `distances_classified.csv` (the output of
`build_distance_classification.py`) and produces a two-panel figure:

  left  : SNR  vs distance (signal / noise only — pure link budget)
  right : SINR vs distance (signal / (noise + interference))

Each point is one (time, tx, rx) record; colour encodes the channel
condition (LOS / NLOSv / NLOS).

Output: `distances_sinr_scatter.png` next to the CSV.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")                # headless
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CSV_IN = HERE / "distances_classified.csv"
PNG_OUT = HERE / "distances_sinr_scatter.png"

# Cap distance for readability: ~95 % of records are < 250 m
DIST_CAP = 250.0

# Per-class styling (matches the HTML viewer)
CLASS_STYLE = {
    "LOS":   dict(color="#1f77b4", marker="o", s=4,  alpha=0.35, label="LOS"),
    "NLOSv": dict(color="#ff7f0e", marker="^", s=6,  alpha=0.45, label="NLOSv"),
    "NLOS":  dict(color="#d62728", marker="x", s=8,  alpha=0.65, label="NLOS"),
}

# Reference curves for the V2V_URBAN path loss (LOS / NLOS) at
# 5.9 GHz, 23 dBm TX, -95 dBm noise floor — pure SNR (no interference).
def _snr_curve(d, plos):
    f = 5.9
    pl = (38.77 + 16.7 * np.log10(d) + 18.2 * np.log10(f)) if plos \
         else (36.85 + 30.0 * np.log10(d) + 18.9 * np.log10(f))
    return 23.0 - pl - (-95.0)   # P_tx - PL - noise_floor


def main():
    df = pd.read_csv(CSV_IN)
    real = df[df.tx != df.rx].copy()        # drop self-links
    real = real[real.distance_m <= DIST_CAP]

    fig, axes = plt.subplots(1, 2, figsize=(13, 6), sharey=False)

    for ax, metric, ylabel in (
        (axes[0], "snr_db",  "SNR (dB), no interferers"),
        (axes[1], "sinr_db", "SINR (dB), all-tx concurrent interference"),
    ):
        for cls, style in CLASS_STYLE.items():
            sub = real[real.link == cls]
            ax.scatter(sub.distance_m, sub[metric],
                       **style, edgecolors="none")
        # Theoretical LOS / NLOS SNR curves (no interference)
        d_grid = np.logspace(0, 2.5, 200)
        ax.plot(d_grid, _snr_curve(d_grid, plos=True),
                "k--", lw=1, alpha=0.6, label="theory LOS (5.9 GHz)")
        ax.plot(d_grid, _snr_curve(d_grid, plos=False),
                "k:", lw=1, alpha=0.6, label="theory NLOS (5.9 GHz)")

        ax.set_xscale("log")
        ax.set_xlabel("TX–RX distance (m)")
        ax.set_ylabel(ylabel)
        ax.grid(True, which="both", alpha=0.25)
        ax.axhline(0, color="grey", lw=0.5)
        ax.legend(loc="lower left", fontsize=8)

    fig.suptitle("V2V_URBAN link budget, P$_{tx}$=23 dBm, "
                 "f=5.9 GHz, NF=9 dB (10 MHz ch.)",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(PNG_OUT, dpi=130)
    print(f"Wrote {PNG_OUT}  ({len(real):,} records, "
          f"distance cap {DIST_CAP:g} m)")


if __name__ == "__main__":
    main()