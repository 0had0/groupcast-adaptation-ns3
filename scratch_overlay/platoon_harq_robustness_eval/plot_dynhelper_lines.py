"""Line plots for the per-TB helper selection campaign (coop_dyn_sps):
  fig_d1: per-member PRR (within PDB) vs distance from leader, by traffic x background N
  fig_d2: GPRR and worst-RX PRR vs background N, by traffic
Mean over seeds, 95 % CI (t). Usage: plot_dynhelper_lines.py <coop_dyn_sps dir> <out dir>"""
import sys, os
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
m = pd.read_csv(os.path.join(src, "analysis/coop_members.csv"))
runs = pd.read_csv(os.path.join(src, "analysis_dyn/dyn_runs.csv"))

ARMS = ["none", "s4Nack", "s4Blind", "kuoNack", "kuoBlind", "mapNack", "mapBlind"]
COL = {"none": "#7a7a75", "s4": "#2a78d6", "kuo": "#eb6834", "map": "#1baf7a"}
MRK = {"none": "o", "s4": "s", "kuo": "^", "map": "D"}
def style(a):
    p = "none" if a == "none" else a[:-4] if a.endswith("Nack") else a[:-5]
    return dict(color=COL[p], marker=MRK[p], ls="--" if a.endswith("Blind") else "-",
                lw=2, ms=6, label=a)

def ci(x):
    x = np.asarray(x, float); n = len(x)
    return x.mean(), (stats.t.ppf(0.975, n - 1) * x.std(ddof=1) / np.sqrt(n) if n > 1 else 0)

mm = m[m.member != "all"].copy(); mm["member"] = mm.member.astype(int)
TRAF = ["periodic1", "periodic2"]; NBG = [0, 45, 60]
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": "#e6e6e3", "grid.linewidth": 0.8})

# fig_d1: PRR vs distance
fig, ax = plt.subplots(2, 3, figsize=(15, 8.5), sharex=True)
for i, tr in enumerate(TRAF):
    for j, n in enumerate(NBG):
        a_ = ax[i, j]
        for arm in ARMS:
            d = mm[(mm.arm == arm) & (mm.traffic == tr) & (mm.nbg == n)]
            g = d.groupby("member")
            x = g.mean_dist_m.mean()
            st = g.prr_pdb.apply(ci)
            y = np.array([s[0] for s in st]); e = np.array([s[1] for s in st])
            a_.plot(x.values, y, **style(arm))
            a_.fill_between(x.values, y - e, y + e, color=style(arm)["color"], alpha=0.07, lw=0)
        a_.set_title(f"{tr}, background N = {n}")
        if j == 0: a_.set_ylabel("member PRR within PDB")
        if i == 1: a_.set_xlabel("mean distance from leader (m)  [members 1..8, IVD 30 m]")
ax[0, 0].legend(ncol=2, fontsize=9, frameon=False, loc="lower left")
fig.suptitle("Per-TB helper selection, roundabout, SB-SPS leader: PRR vs distance from leader "
             "(mean of 10 seeds, band = 95 % CI)", fontsize=12)
fig.tight_layout(); fig.savefig(os.path.join(out, "fig_d1_prr_vs_distance.png"), dpi=150)

# fig_d2: GPRR / worst RX vs N
per = mm.groupby(["arm", "traffic", "nbg", "seed"]).prr_pdb.agg(gprr="mean", worst="min").reset_index()
per = per.merge(runs[["arm", "traffic", "nbg", "seed", "gprr"]].rename(columns={"gprr": "gprr_run"}),
                on=["arm", "traffic", "nbg", "seed"])
fig, ax = plt.subplots(2, 2, figsize=(12, 8.5), sharex=True)
rows = [("gprr", "GPRR (mean member PRR within PDB)"), ("worst", "worst-RX PRR within PDB")]
tab = []
for i, (k, lab) in enumerate(rows):
    for j, tr in enumerate(TRAF):
        a_ = ax[i, j]
        for arm in ARMS:
            st = [ci(per[(per.arm == arm) & (per.traffic == tr) & (per.nbg == n)][k]) for n in NBG]
            y = np.array([s[0] for s in st]); e = np.array([s[1] for s in st])
            a_.errorbar(NBG, y, yerr=e, capsize=3, **style(arm))
            tab += [dict(metric=k, traffic=tr, nbg=n, arm=arm, mean=y[q], ci95=e[q]) for q, n in enumerate(NBG)]
        a_.set_title(f"{tr}"); a_.set_xticks(NBG)
        if j == 0: a_.set_ylabel(lab)
        if i == 1: a_.set_xlabel("background vehicles N")
ax[0, 0].legend(ncol=2, fontsize=9, frameon=False)
fig.suptitle("GPRR and worst-RX PRR vs background traffic (mean of 10 seeds, 95 % CI)", fontsize=12)
fig.tight_layout(); fig.savefig(os.path.join(out, "fig_d2_gprr_worst_vs_load.png"), dpi=150)

T = pd.DataFrame(tab); T.to_csv(os.path.join(out, "gprr_worst_vs_load.csv"), index=False)
worst_member = mm.loc[mm.groupby(["arm", "traffic", "nbg", "seed"]).prr_pdb.idxmin()].member.value_counts()
print("worst member counts:\n", worst_member.to_string())
print("max |gprr(members prr_pdb) - gprr(run csv)|:", (per.gprr - per.gprr_run).abs().max())
print(T.pivot_table(index=["metric", "traffic", "arm"], columns="nbg", values="mean").round(3).to_string())
