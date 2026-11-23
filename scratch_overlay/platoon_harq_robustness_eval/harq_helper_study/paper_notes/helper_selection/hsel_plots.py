import os, numpy as np, pandas as pd, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
OUT = os.path.dirname(os.path.abspath(__file__))
Q = pd.read_csv(f'{OUT}/hsel_tb.csv.gz'); R = pd.read_csv(f'{OUT}/hsel_runs.csv')
plt.rcParams.update({'font.size': 8, 'axes.titlesize': 8.5, 'legend.fontsize': 7})
SC = {'hwy': 'Highway', 'rbt': 'Roundabout W', 'rbtE': 'Roundabout E'}; TR = {'periodic1tb': 'periodic', 'aperiodic1tb': 'aperiodic'}
ARMS = ['R4', 'R4.1', 'R4.2', 'R5', 'R6']; OFF = dict(zip(ARMS, np.linspace(-.28, .28, 5)))
COL = dict(zip(ARMS, ['#d62728', '#9467bd', '#ff7f0e', '#1f77b4', '#2ca02c']))
# 1) timeline, seed 1, both loads
for nb in (0, 60):
    fig, axes = plt.subplots(3, 2, figsize=(11, 7.8), sharey=True)
    fig.subplots_adjust(hspace=.42, wspace=.08)
    for i, sc in enumerate(SC):
        for j, tr in enumerate(TR):
            ax = axes[i, j]
            g = Q[(Q.sc == sc) & (Q.tr == tr) & (Q.nbg == nb) & (Q.seed == 1)]
            for a in ARMS:
                x = g[g.arm == a].sort_values('t_ndi')
                ax.step(x.t_ndi, x.applied + OFF[a], where='post', color=COL[a], lw=1, label=a)
            x = g[g.arm == 'R6']
            sca = ax.scatter(x.t_ndi, np.full(len(x), 0.45), c=x.n_subch, cmap='viridis', vmin=1, vmax=5, s=3, marker='|')
            ax.set_title(f'{SC[sc]}, {TR[tr]}, {nb} cars, seed 1'); ax.set_ylim(0.2, 5.5); ax.set_yticks(range(1, 6))
            ax.grid(alpha=.3); ax.set_xlabel('time (s), KPI window only') if i == 2 else None
            if j == 0: ax.set_ylabel('helper (member index)')
    h, l = axes[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc='upper center', ncol=5, bbox_to_anchor=(.5, 1.0), title='scheme (lines offset vertically so they do not hide each other)')
    cb = fig.colorbar(sca, ax=axes, shrink=.5, pad=.01); cb.set_label('TB size (subchannels), bottom ticks')
    fig.savefig(f'{OUT}/helper_timeline_{nb}cars.png', dpi=150, bbox_inches='tight'); plt.close(fig)
# 2) who is helper vs TB size
R6 = Q[Q.arm == 'R6']
fig, axes = plt.subplots(1, 3, figsize=(11, 2.6), sharey=True)
cols = ['#8c564b', '#1f77b4', '#ff7f0e', '#2ca02c']
for ax, sc in zip(axes, SC):
    g = R6[R6.sc == sc]
    ct = pd.crosstab(g.n_subch, g.applied, normalize='index').reindex(columns=[1, 2, 3, 4], fill_value=0)
    n = g.n_subch.value_counts(normalize=True).sort_index()
    bottom = np.zeros(len(ct))
    for k, m in enumerate([1, 2, 3, 4]):
        ax.bar(ct.index, ct[m], bottom=bottom, color=cols[k], label=f'member {m}'); bottom += ct[m].values
    for xi, v in n.items(): ax.text(xi, 1.02, f'{100*v:.0f}%', ha='center', fontsize=6.5)
    ax.set_title(SC[sc] + ' (all loads, traffic, seeds)', pad=12); ax.set_xlabel('TB size (subchannels)  [% of TBs above]')
    ax.set_ylim(0, 1.1)
axes[0].set_ylabel('share of TBs'); axes[2].legend(loc='lower right', fontsize=6.5)
fig.savefig(f'{OUT}/helper_vs_tb_size.png', dpi=150, bbox_inches='tight'); plt.close(fig)
print('ok')
