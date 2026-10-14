"""Histogram of the helper member signalled per TB, per strategy (coop_dyn_sps).
kuo / map: <tag>_hsel.csv, one row per published leader grant with > 1 slot (a helper is
named only when there are retransmission slots to hand over, nr_sl_coop_scheduler.cc
grant.slotAllocations.size() > 1); single-slot TBs have no helper. s4: member 4 on every
helper-assigned TB (which TBs is not logged). Leader TBs: distinct new transmissions in _att.csv.
Usage: plot_dynhelper_choice.py <coop_dyn_sps dir> <out dir>"""
import sys, os, glob, re
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

src, out = sys.argv[1], sys.argv[2]
rx = re.compile(r"rbt_ivd30_(periodic\d)_n(\d+)_s(\d+)_")
rows, cov = [], []
def ntbs(f):
    a = pd.read_csv(f, usecols=["side", "tb", "ndi", "member"])
    return a[(a.side == 0) & (a.ndi == 1) & (a.member == 1)].tb.nunique()
for arm in ["kuoNack", "kuoBlind", "mapNack", "mapBlind"]:
    for f in glob.glob(os.path.join(src, arm, "*_hsel.csv.gz")):
        tr, n, s = rx.search(os.path.basename(f)).groups()
        h = pd.read_csv(f, usecols=["applied"]).applied
        rows += [dict(arm=arm, traffic=tr, nbg=int(n), seed=int(s), member=int(k), tbs=int(v))
                 for k, v in h.value_counts().items()]
        cov.append(dict(arm=arm, traffic=tr, nbg=int(n), seed=int(s), helper_tbs=len(h),
                        leader_tbs=ntbs(f.replace("_hsel", "_att"))))
for arm in ["s4Nack", "s4Blind"]:
    for f in glob.glob(os.path.join(src, arm, "*_att.csv.gz")):
        tr, n, s = rx.search(os.path.basename(f)).groups()
        rows.append(dict(arm=arm, traffic=tr, nbg=int(n), seed=int(s), member=4, tbs=1))
C = pd.DataFrame(cov); C["frac"] = C.helper_tbs / C.leader_tbs
C.to_csv(os.path.join(out, "helper_coverage.csv"), index=False)
D = pd.DataFrame(rows)
runs = pd.read_csv(os.path.join(src, "analysis_dyn/dyn_runs.csv"))
print("relay_by_unchosen total:", runs.relay_by_unchosen.sum())

ARMS = ["s4Nack", "s4Blind", "kuoNack", "kuoBlind", "mapNack", "mapBlind"]
COL = {"s4": "#2a78d6", "kuo": "#eb6834", "map": "#1baf7a"}
T = D.groupby(["traffic", "nbg", "arm", "member"]).tbs.sum().unstack("member", fill_value=0)
T = T.reindex(columns=range(1, 9), fill_value=0)
S = T.div(T.sum(axis=1), axis=0)
T.assign(total=T.sum(axis=1)).drop(index=[i for i in T.index if i[2].startswith('s4')]).to_csv(os.path.join(out, "helper_choice_counts.csv"))

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
fig, ax = plt.subplots(2, 3, figsize=(15, 7.5), sharey=True)
w = 0.13; x = np.arange(1, 9)
for i, tr in enumerate(["periodic1", "periodic2"]):
    for j, n in enumerate([0, 45, 60]):
        a_ = ax[i, j]
        for k, arm in enumerate(ARMS):
            p = arm[:-4] if arm.endswith("Nack") else arm[:-5]
            y = S.loc[(tr, n, arm)].values
            a_.bar(x + (k - 2.5) * w, y, w * 0.9, color=COL[p] if arm.endswith("Nack") else "white",
                   edgecolor=COL[p], hatch=None if arm.endswith("Nack") else "////", lw=1.2, label=arm)
        tot = int(T.loc[(tr, n, "mapNack")].sum())
        a_.set_title(f"{tr}, N = {n}  ({tot} helper TBs)", fontsize=10)
        a_.set_xticks(x); a_.grid(axis="y", color="#e6e6e3"); a_.set_axisbelow(True)
        if j == 0: a_.set_ylabel("share of helper-assigned TBs")
        if i == 1: a_.set_xlabel("member selected as helper (1 = behind leader ... 8 = tail)")
ax[0, 0].legend(ncol=3, fontsize=9, frameon=False, loc="upper right")
fig.suptitle("Helper member signalled in SCI-2A, over the leader's TBs that had retransmission slots to hand over "
             "(solid = NACK, hatched = blind; s4 = member 4 by construction; 'none' has no helper)", fontsize=11)
fig.tight_layout(); fig.savefig(os.path.join(out, "fig_d3_helper_choice.png"), dpi=150)
print(S.round(3).to_string())
K = C.groupby(["traffic", "nbg", "arm"]).agg(helper_tbs=("helper_tbs", "sum"), leader_tbs=("leader_tbs", "sum"),
                                              frac_mean=("frac", "mean"), frac_min=("frac", "min"), frac_max=("frac", "max"))
print(K.round(3).to_string())
fig, ax = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
for j, tr in enumerate(["periodic1", "periodic2"]):
    for arm in ["kuoNack", "kuoBlind", "mapNack", "mapBlind"]:
        p = arm[:3]; k = K.loc[tr].xs(arm, level="arm")
        ax[j].plot(k.index, k.frac_mean, color=COL[p], marker="^" if p == "kuo" else "D", lw=2,
                   ls="-" if arm.endswith("Nack") else "--", label=arm)
    ax[j].set_title(tr); ax[j].set_xticks([0, 45, 60]); ax[j].set_ylim(0, 1.05)
    ax[j].set_xlabel("background vehicles N"); ax[j].grid(color="#e6e6e3")
ax[0].set_ylabel("share of leader TBs with a helper")
ax[0].legend(frameon=False, fontsize=9, loc="lower left")
fig.suptitle("Helper coverage: TBs whose grant had > 1 slot (the rest are single-slot, no helper)", fontsize=11)
fig.tight_layout(); fig.savefig(os.path.join(out, "fig_d3b_helper_coverage.png"), dpi=150)
