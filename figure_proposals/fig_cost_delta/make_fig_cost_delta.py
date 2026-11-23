#!/usr/bin/env python3
"""Proposed replacement for fig_cost: paired change against source HARQ Opt. 2, per configuration.

  python3 make_fig_cost_delta.py [per_seed_csv]

Input: reproducibility_bundle/results/main_v3_per_seed.csv (campaign v3, 10 seeds per configuration; group_prr_pdb
and pd_ss_pkt are the values behind the current fig_cost). For each (route, traffic, load) and scheme, the change is
computed per seed against R3 of the same seed, then averaged; bars are 95 % Student-t intervals over the 10 seeds.
x: change in subchannel-slots sent per packet (%); y: change in group PRR (percentage points).
"""
import sys
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import t

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    HERE.parents[1] / "reproducibility_bundle/results/main_v3_per_seed.csv"

mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Times", "DejaVu Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8, "legend.fontsize": 7, "xtick.labelsize": 7,
    "ytick.labelsize": 7, "pdf.fonttype": 42, "ps.fonttype": 42, "axes.linewidth": 0.6, "grid.linewidth": 0.4,
    "savefig.bbox": "tight", "savefig.pad_inches": 0.02})
ARMS = {"R6": "Cooperative", "R4_bsc": "Helper blind + C", "R4.2": "No takeover"}
COL = {"Cooperative": "#0072B2", "Helper blind + C": "#E69F00", "No takeover": "#CC79A7"}
ROUTES = {"Highway": "o", "Roundabout W": "s", "Roundabout E": "^"}
W2 = 7.16

d = pd.read_csv(SRC)
K = ["route", "traffic", "load", "seed"]
ref = d[d.arm == "R3"].set_index(K)[["group_prr_pdb", "pd_ss_pkt"]]
rows = []
for arm, name in ARMS.items():
    s = d[d.arm == arm].set_index(K)[["group_prr_pdb", "pd_ss_pkt"]].join(ref, rsuffix="_ref", how="inner")
    s["dprr"] = 100 * (s.group_prr_pdb - s.group_prr_pdb_ref)
    s["dss"] = 100 * (s.pd_ss_pkt / s.pd_ss_pkt_ref - 1)
    for key, g in s.groupby(level=["route", "traffic", "load"]):
        n = len(g)
        h = t.ppf(0.975, n - 1) / np.sqrt(n)
        rows.append(dict(zip(["route", "traffic", "load"], key), scheme=name, n=n,
                         dprr=g.dprr.mean(), dprr_ci=h * g.dprr.std(ddof=1),
                         dss=g.dss.mean(), dss_ci=h * g.dss.std(ddof=1),
                         seeds_cheaper=int((g.dss < 0).sum()), seeds_better=int((g.dprr > 0).sum())))
r = pd.DataFrame(rows)
r.to_csv(HERE / "fig_cost_delta.csv", index=False, float_format="%.4f")
assert (r.n == 10).all()

fig, axes = plt.subplots(1, 2, figsize=(W2, 3.0), sharex=True, sharey=True)
xlo, xhi = r.dss.sub(r.dss_ci).min() - 3, r.dss.add(r.dss_ci).max() + 3
ylo, yhi = r.dprr.sub(r.dprr_ci).min() - 1.5, r.dprr.add(r.dprr_ci).max() + 1.5
for ax, tr in zip(axes, ("periodic", "aperiodic")):
    ax.axhspan(0, yhi, xmin=0, xmax=(0 - xlo) / (xhi - xlo), color="#0072B2", alpha=0.07, lw=0, zorder=0)
    ax.text(xlo + 1, 7, "better PRR,\nfewer resources", fontsize=6.5, va="center", color="#0b3d66")
    ax.axhline(0, color="k", lw=0.6, zorder=1)
    ax.axvline(0, color="k", lw=0.6, zorder=1)
    for name in ("Helper blind + C", "No takeover", "Cooperative"):   # Cooperative on top
        for _, q in r[(r.traffic == tr) & (r.scheme == name)].iterrows():
            ax.errorbar(q.dss, q.dprr, xerr=q.dss_ci, yerr=q.dprr_ci, fmt=ROUTES[q.route],
                        ms=5.5 if name == "Cooperative" else 4.5, color=COL[name],
                        mfc=COL[name] if q.load == 0 else "white", mew=0.9, elinewidth=0.5, capsize=1.2,
                        alpha=1 if name == "Cooperative" else 0.85, zorder=4 if name == "Cooperative" else 3)
    ax.set_title(f"{tr.capitalize()} traffic")
    ax.set_xlabel("Change in subchannel-slots per packet vs. Opt. 2 (%)")
    ax.grid(alpha=0.3)
    ax.set_xlim(xlo, xhi)
    ax.set_ylim(ylo, yhi)
axes[0].set_ylabel("Change in group PRR vs. Opt. 2 (pp)")
L = mpl.lines.Line2D
hs = [L([], [], ls="none", marker="o", color=COL[n], ms=5, label=n) for n in ("Cooperative", "Helper blind + C", "No takeover")]
hs += [L([], [], ls="none", marker=m, color="0.3", ms=4.5, label=k) for k, m in ROUTES.items()]
hs += [L([], [], ls="none", marker="o", color="0.3", ms=4.5, label="no background"),
       L([], [], ls="none", marker="o", color="0.3", mfc="white", ms=4.5, label="60 background vehicles")]
fig.legend(handles=hs, loc="lower center", ncol=8, frameon=False, bbox_to_anchor=(0.5, -0.07), columnspacing=1.0,
           handletextpad=0.3)
fig.tight_layout(w_pad=0.6)
fig.savefig(HERE / "fig_cost_delta.pdf")
fig.savefig(HERE / "fig_cost_delta.png", dpi=220)
print(r[r.scheme == "Cooperative"].round(2).to_string(index=False))
