"""Selected helper member over time (coop_dyn_sps), from the per-TB selector log.
x = t_ndi (time of the TB's new transmission, s); y = member signalled in SCI-2A.
Grey band = the leader -> tail link is blocked by a building (deterministic geometry of the
leader's new transmissions, _att.csv geo_tx == 0; same trace in every arm of a seed).
Lower strip: TBs the tail (member 8) did not decode from any transmission (leader or helper);
periodic1 one tick per lost TB, periodic2 (100 TBs/s) the lost share per 1 s bin.
  fig_d4_<traffic>_s<seed>.png : one seed, N = 0 / 45 / 60
  fig_d5_helper_share_time.png  : share of each member per 1 s bin, all 10 seeds pooled
Usage: plot_dynhelper_timeline.py <coop_dyn_sps dir> <out dir> [seed]"""
import sys, os
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

src, out = sys.argv[1], sys.argv[2]
SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 1
COL = {"s4Nack": "#2a78d6", "kuoNack": "#eb6834", "mapNack": "#1baf7a"}
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})

def tag(tr, n, s): return f"rbt_ivd30_{tr}_n{n}_s{s}"

def att(arm, tr, n, s):
    a = pd.read_csv(os.path.join(src, arm, tag(tr, n, s) + "_att.csv.gz"),
                    usecols=["side", "t", "tb", "ndi", "member", "status", "geo_tx"])
    return a[a.member == 8]

def blocked_spans(a):
    f = a[(a.side == 0) & (a.ndi == 1)].sort_values("t")
    b = (f.geo_tx == 0).values; t = f.t.values; spans = []; i = 0
    while i < len(b):
        if b[i]:
            j = i
            while j + 1 < len(b) and b[j + 1]: j += 1
            spans.append((t[i], t[j])); i = j + 1
        else: i += 1
    return spans

def tail_lost(a):
    ok = a.groupby("tb").status.apply(lambda s: (s == 1).any())
    t0 = a[(a.side == 0) & (a.ndi == 1)].set_index("tb").t
    return t0[ok.reindex(t0.index, fill_value=False) == False].values

for tr in ["periodic1", "periodic2"]:
    fig, axes = plt.subplots(6, 1, figsize=(15, 11), sharex=True,
                             gridspec_kw={"height_ratios": [3, 0.8] * 3})
    for k, n in enumerate([0, 45, 60]):
        ax, rug = axes[2 * k], axes[2 * k + 1]
        a_map = att("mapNack", tr, n, SEED)
        for t0, t1 in blocked_spans(a_map):
            for x in (ax, rug): x.axvspan(t0, t1 + 0.01, color="#d9d9d4", lw=0, zorder=0)
        ax.axhline(4, color=COL["s4Nack"], lw=2, ls="--", label="s4Nack (fixed 4)", zorder=2)
        for arm, off in [("kuoNack", -0.12), ("mapNack", 0.12)]:
            h = pd.read_csv(os.path.join(src, arm, tag(tr, n, SEED) + "_hsel.csv.gz"),
                            usecols=["t_ndi", "applied"])
            if tr == "periodic1":
                ax.step(h.t_ndi, h.applied + off, where="post", color=COL[arm], lw=1.6, label=arm, zorder=3)
            else:  # 10 ms TBs, with gaps (single-slot TBs have no helper): dots
                ax.plot(h.t_ndi, h.applied + off, ".", ms=2.5, color=COL[arm], label=arm, zorder=3)
        ax.set_ylim(0.5, 8.5); ax.set_yticks(range(1, 9)); ax.set_ylabel("helper member")
        ax.grid(axis="y", color="#ececea"); ax.set_title(f"{tr}, N = {n}, seed {SEED}", loc="left")
        for r, arm in enumerate(["s4Nack", "kuoNack", "mapNack"]):
            a_ = att(arm, tr, n, SEED) if arm != "mapNack" else a_map
            lost = tail_lost(a_)
            if tr == "periodic1":
                rug.plot(lost, np.full(len(lost), r), "|", color=COL[arm], ms=7, mew=1)
            else:  # 100 TBs/s: ticks merge, show the loss rate per 1 s bin instead
                allt = a_[(a_.side == 0) & (a_.ndi == 1)].t.values
                bins = np.arange(np.floor(allt.min()), np.ceil(allt.max()) + 1)
                tot = np.histogram(allt, bins)[0]; ls = np.histogram(lost, bins)[0]
                rate = np.where(tot > 0, ls / np.maximum(tot, 1), np.nan)
                rug.step(bins[:-1], rate, where="post", color=COL[arm], lw=1.4)
        if tr == "periodic1":
            rug.set_yticks([0, 1, 2]); rug.set_yticklabels(["s4", "kuo", "map"], fontsize=8)
            rug.set_ylim(-0.7, 2.7); rug.set_ylabel("tail lost", fontsize=8)
        else:
            rug.set_ylim(0, 1.05); rug.set_yticks([0, 1]); rug.set_ylabel("tail loss\nrate /1 s", fontsize=8)
        if k == 0: ax.legend(ncol=3, frameon=False, loc="upper right", fontsize=9)
    axes[-1].set_xlabel("time (s)  [t_ndi of each TB]")
    fig.suptitle("Selected helper per TB over time; grey = building blocks leader -> tail; "
                 "lower strips: TBs the tail failed to decode", fontsize=12)
    fig.tight_layout(); fig.savefig(os.path.join(out, f"fig_d4_{tr}_s{SEED}.png"), dpi=150); plt.close(fig)

# pooled share over time (all seeds), N = 0
RAMP = {1: "#cde2fb", 2: "#9ec5f4", 3: "#6da7ec", 4: "#3987e5", 5: "#256abf", 6: "#184f95",
        7: "#104281", 8: "#0d366b"}
fig, axes = plt.subplots(2, 2, figsize=(15, 7.5), sharex=True, sharey=True)
for i, tr in enumerate(["periodic1", "periodic2"]):
    blk = []
    for s in range(1, 11):
        f = att("mapNack", tr, 0, s); f = f[(f.side == 0) & (f.ndi == 1)]
        blk.append(pd.DataFrame({"bin": np.floor(f.t).astype(int), "b": f.geo_tx == 0}))
    B = pd.concat(blk).groupby("bin").b.mean()
    for j, arm in enumerate(["kuoNack", "mapNack"]):
        H = pd.concat([pd.read_csv(os.path.join(src, arm, tag(tr, 0, s) + "_hsel.csv.gz"),
                                   usecols=["t_ndi", "applied"]) for s in range(1, 11)])
        H["bin"] = np.floor(H.t_ndi).astype(int)
        S = H.groupby(["bin", "applied"]).size().unstack(fill_value=0).reindex(columns=range(1, 9), fill_value=0)
        S = S.div(S.sum(axis=1), axis=0)
        ax = axes[i, j]
        ax.stackplot(S.index, S.T.values, colors=[RAMP[m] for m in range(1, 9)],
                     labels=[f"member {m}" for m in range(1, 9)], edgecolor="white", lw=0.3)
        ax.plot(B.index, B.values, color="#202020", lw=1.6, label="P(building blocks L->tail)")
        ax.set_title(f"{arm}, {tr}, N = 0, 10 seeds pooled", loc="left"); ax.set_ylim(0, 1)
        if j == 0: ax.set_ylabel("share of helper TBs in the 1 s bin")
        if i == 1: ax.set_xlabel("time (s)")
axes[0, 1].legend(ncol=1, fontsize=8, frameon=False, loc="center left", bbox_to_anchor=(1.0, 0.5))
fig.suptitle("Which member is helper along the route (1 s bins); black = share of TBs with a building between leader and tail", fontsize=12)
fig.tight_layout(); fig.savefig(os.path.join(out, "fig_d5_helper_share_time.png"), dpi=150)
