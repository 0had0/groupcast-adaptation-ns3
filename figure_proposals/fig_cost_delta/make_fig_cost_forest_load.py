#!/usr/bin/env python3
"""Forest plot of the cost figure with both loads in one row: change against source HARQ Opt. 2.

  python3 make_fig_cost_delta.py && python3 make_fig_cost_forest_load.py

One row per route x traffic. Each scheme has two markers on its line, filled = no background (0 background
vehicles), open = congested (60 background vehicles, CBR about 0.99), joined by a thin segment. Input:
fig_cost_delta.csv (per-seed paired means and 95 % Student-t half-widths over 10 seeds).
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
OFF = {"Cooperative": -0.27, "Helper blind + C": 0.0, "No takeover": 0.27}
LOFF = {0: -0.05, 60: 0.05}   # small split so the two intervals of a scheme do not sit on each other
ROUTES = ["Highway", "Roundabout W", "Roundabout E"]
ROWS = [(r, t) for r in ROUTES for t in ("periodic", "aperiodic")]

r = pd.read_csv(HERE / "fig_cost_delta.csv")
fig, axes = plt.subplots(1, 2, figsize=(7.16, 3.0), sharey=True, gridspec_kw=dict(wspace=0.06))
for ax, (m, lab) in zip(axes, [("dprr", "Change in group PRR vs. Opt. 2 (pp)"),
                               ("dss", "Change in subchannel-slots per packet vs. Opt. 2 (%)")]):
    for i, (rt, tr) in enumerate(ROWS):
        if (i // 2) % 2 == 1:
            ax.axhspan(i - 0.5, i + 0.5, color="0.94", lw=0, zorder=0)
        for s in SCHEMES:
            g = r[(r.route == rt) & (r.traffic == tr) & (r.scheme == s)].set_index("load")
            y = i + OFF[s]
            ax.plot([g.loc[0, m], g.loc[60, m]], [y + LOFF[0], y + LOFF[60]], color=COL[s], lw=0.8, alpha=0.6,
                    zorder=2)
            for load in (0, 60):
                q = g.loc[load]
                ax.errorbar(q[m], y + LOFF[load], xerr=q[m + "_ci"], fmt=MK[s],
                            ms=4.6 if s == "Cooperative" else 3.8, color=COL[s],
                            mfc=COL[s] if load == 0 else "white", mew=0.9, elinewidth=0.7, capsize=1.3,
                            zorder=4 if s == "Cooperative" else 3)
    ax.axvline(0, color="k", lw=0.7, zorder=1)
    ax.grid(axis="x", alpha=0.35)
    ax.set_xlabel(lab)
    ax.set_ylim(len(ROWS) - 0.5, -0.5)
    for b in (2, 4):
        ax.axhline(b - 0.5, color="0.4", lw=0.6)
axes[0].set_yticks(range(len(ROWS)))
axes[0].set_yticklabels([t.capitalize() for _, t in ROWS])
axes[0].tick_params(axis="y", length=0)
for k, rt in enumerate(ROUTES):
    axes[0].text(-0.19, 2 * k + 0.5, rt, transform=axes[0].get_yaxis_transform(), rotation=90, ha="center",
                 va="center", fontsize=8)
axes[0].annotate("worse  ← | →  better", xy=(0.5, 1.01), xycoords="axes fraction", ha="center",
                 va="bottom", fontsize=7, color="0.3")
axes[1].annotate("fewer resources  ← | →  more", xy=(0.5, 1.01), xycoords="axes fraction", ha="center",
                 va="bottom", fontsize=7, color="0.3")
L = mpl.lines.Line2D
hs = [L([], [], ls="none", marker=MK[s], color=COL[s], ms=4.5, label=s) for s in SCHEMES]
hs += [L([], [], ls="none", marker="s", color="0.3", ms=4.5, label="No background"),
       L([], [], ls="none", marker="s", color="0.3", mfc="white", ms=4.5, label="Congested")]
fig.legend(handles=hs, loc="lower center", ncol=5, frameon=False, bbox_to_anchor=(0.55, -0.09))
fig.savefig(HERE / "fig_cost_forest_load.pdf")
fig.savefig(HERE / "fig_cost_forest_load.png", dpi=220)
