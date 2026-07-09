#!/usr/bin/env python3
"""Downstream-coverage plot for the chosen relay.

Reads `relay_coverage.csv` and produces a three-panel figure:

  Top    : stacked-area counts of recipients reachable by
           `direct` (LOS/NLOSv to leader) and `via_relay`
           (NLOS to leader, but LOS/NLOSv from the chosen relay).
  Middle : `n_unreachable` over time (NLOS to BOTH leader and relay).
  Bottom : `coverage_pct` (the share of recipients reachable in ≤ 1 hop).

Output: `relay_coverage.png` next to the CSV.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")                 # headless
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
COV_CSV  = HERE / "relay_coverage.csv"
PNG_OUT  = HERE / "relay_coverage.png"


def main():
    df = pd.read_csv(COV_CSV)

    fig, (ax_counts, ax_unr, ax_cov) = plt.subplots(
        3, 1, figsize=(13, 8), sharex=True,
        gridspec_kw=dict(height_ratios=[1.3, 0.7, 0.9]),
    )

    # Top panel — stacked area of direct / via_relay / unreachable.
    ax_counts.fill_between(df.time, 0, df.n_direct,
                           color="#1f77b4", alpha=0.85,
                           label="direct (LOS/NLOSv to leader)")
    ax_counts.fill_between(df.time, df.n_direct,
                           df.n_direct + df.n_via_relay,
                           color="#2ca02c", alpha=0.85,
                           label="via relay (NLOS to leader, "
                                 "LOS/NLOSv to relay)")
    ax_counts.fill_between(df.time, df.n_direct + df.n_via_relay,
                           df.n_direct + df.n_via_relay + df.n_unreachable,
                           color="#d62728", alpha=0.75,
                           label="unreachable (NLOS to both)")
    ax_counts.set_ylabel("Recipients reachable in ≤ 1 hop")
    ax_counts.legend(loc="upper right", fontsize=8)
    ax_counts.grid(True, alpha=0.25)
    ax_counts.set_ylim(bottom=0)

    # Middle panel — n_unreachable as a line/bar plot.
    ax_unr.fill_between(df.time, 0, df.n_unreachable,
                        color="#d62728", alpha=0.5,
                        label="unreachable")
    ax_unr.set_ylabel("Unreachable\nrecipients")
    ax_unr.set_ylim(bottom=0)
    ax_unr.grid(True, alpha=0.25)

    # Bottom panel — coverage_pct line.  When leader is gone,
    # coverage_pct is NaN → leave a gap in the line.
    df_active = df[df.leader_active]
    ax_cov.plot(df_active.time, df_active.coverage_pct,
                color="#2ca02c", lw=1.2, label="coverage (%)")
    ax_cov.axhline(100, color="grey", lw=0.4, ls=":")
    ax_cov.set_ylim(0, 105)
    ax_cov.set_ylabel("Coverage (%)")
    ax_cov.set_xlabel("Simulation time (s)")
    ax_cov.grid(True, alpha=0.25)
    ax_cov.legend(loc="lower left", fontsize=8)

    # Shade the leader-exited region so the gap is visible.
    gap = df[~df.leader_active]
    if not gap.empty:
        for t_start, t_end in zip(gap.time.iloc[:-1], gap.time.iloc[1:]):
            ax_counts.axvspan(t_start, t_end, color="grey", alpha=0.15)
            ax_unr.axvspan(t_start, t_end, color="grey", alpha=0.15)
            ax_cov.axvspan(t_start, t_end, color="grey", alpha=0.15)

    fig.suptitle("Relay downstream coverage — "
                 "platoon_intersection_merge_split",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(PNG_OUT, dpi=130)
    print(f"Wrote {PNG_OUT}")


if __name__ == "__main__":
    main()