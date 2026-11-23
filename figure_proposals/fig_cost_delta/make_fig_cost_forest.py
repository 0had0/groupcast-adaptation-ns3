#!/usr/bin/env python3
"""Forest-plot version of the cost figure: change against source HARQ Opt. 2, one row per configuration.

  python3 make_fig_cost_delta.py && python3 make_fig_cost_forest.py

Input: fig_cost_delta.csv (written by make_fig_cost_delta.py from the v3 per-seed results; per-seed paired means and
95 % Student-t half-widths over 10 seeds).
"""
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parent
mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Times", "DejaVu Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8, "legend.fontsize": 7, "xtick.labelsize": 7,
    "ytick.labelsize": 7, "pdf.fonttype": 42, "ps.fonttype": 42, "axes.linewidth": 0.6, "grid.linewidth": 0.4,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.02})
SCHEMES = ["Cooperative", "Helper blind + C", "No takeover"]
COL = {"Cooperative": "#0072B2", "Helper blind + C": "#E69F00", "No takeover": "#CC79A7"}
MK = {"Cooperative": "v", "Helper blind + C": "D", "No takeover": "o"}
OFF = {"Cooperative": -0.24, "Helper blind + C": 0.0, "No takeover": 0.24}
ROUTES = ["Highway", "Roundabout W", "Roundabout E"]
ROWS = [(r, t, l) for r in ROUTES for t in ("periodic", "aperiodic") for l in (0, 60)]

r = pd.read_csv(HERE / "fig_cost_delta.csv")
fig, axes = plt.subplots(1, 2, figsize=(7.16, 4.1), sharey=True, gridspec_kw=dict(wspace=0.06))
for ax, (m, lab) in zip(axes, [("dprr", "Change in group PRR vs. Opt. 2 (pp)"),
                               ("dss", "Change in subchannel-slots per packet vs. Opt. 2 (%)")]):
    for i, key in enumerate(ROWS):
        if (i // 4) % 2 == 1:
            ax.axhspan(i - 0.5, i + 0.5, color="0.94", lw=0, zorder=0)
        for s in SCHEMES:
            q = r[(r.route == key[0]) & (r.traffic == key[1]) & (r.load == key[2]) & (r.scheme == s)].iloc[0]
            ax.errorbar(q[m], i + OFF[s], xerr=q[m + "_ci"], fmt=MK[s], ms=4.5 if s == "Cooperative" else 3.6,
                        color=COL[s], mfc=COL[s], elinewidth=0.8, capsize=1.5, zorder=3 if s == "Cooperative" else 2)
    ax.axvline(0, color="k", lw=0.7, zorder=1)
    ax.grid(axis="x", alpha=0.35)
    ax.set_xlabel(lab)
    ax.set_ylim(len(ROWS) - 0.5, -0.5)
    for b in (4, 8):
        ax.axhline(b - 0.5, color="0.4", lw=0.6)
axes[0].set_yticks(range(len(ROWS)))
axes[0].set_yticklabels([f"{t.capitalize()}, {l} veh." for _, t, l in ROWS])
axes[0].tick_params(axis="y", length=0)
for k, rt in enumerate(ROUTES):   # route labels left of the tick labels
    axes[0].text(-0.36, 4 * k + 1.5, rt, transform=axes[0].get_yaxis_transform(), rotation=90, ha="center",
                 va="center", fontsize=8)
axes[0].annotate("worse  ← | →  better", xy=(0.5, 1.01), xycoords="axes fraction", ha="center",
                 va="bottom", fontsize=7, color="0.3")
axes[1].annotate("fewer resources  ← | →  more", xy=(0.5, 1.01), xycoords="axes fraction", ha="center",
                 va="bottom", fontsize=7, color="0.3")
hs = [mpl.lines.Line2D([], [], ls="none", marker=MK[s], color=COL[s], ms=4.5, label=s) for s in SCHEMES]
fig.legend(handles=hs, loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.55, -0.06))
fig.savefig(HERE / "fig_cost_forest.pdf")
fig.savefig(HERE / "fig_cost_forest.png", dpi=220)
