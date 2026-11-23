"""Who was the helper? Aggregates every *_hsel.csv of the helper arms over the KPI window."""
import glob, os, numpy as np, pandas as pd
B = os.path.expanduser('~/mnt/harq_helper_study/out/remote_full')
OUT = os.path.dirname(os.path.abspath(__file__))
ARMS = ['R4', 'R4.1', 'R4.2', 'R5', 'R6']
rows, seqs = [], []
for arm in ARMS:
    for f in sorted(glob.glob(f'{B}/{arm}/*_hsel.csv')):
        tag = os.path.basename(f)[:-len('_hsel.csv')]
        sc, _, tr, bg, s = tag.split('_')
        r = pd.read_csv(f'{B}/{arm}/{tag}_run.csv').iloc[0]
        h = pd.read_csv(f)
        h = h[(h.t_ndi >= r.kpi_start) & (h.t_ndi < r.kpi_end)].sort_values('t_ndi')
        sc_cols = [c for c in h.columns if c.startswith('score_m')]
        S = h[sc_cols].to_numpy()
        srt = np.sort(S, axis=1)
        margin = srt[:, -1] - srt[:, -2]
        ch = (h.applied.diff().fillna(0) != 0).sum()
        dur = r.kpi_end - r.kpi_start
        d = dict(arm=arm, sc=sc, tr=tr, nbg=0 if bg == 'none' else 60, seed=int(s[1:]), n_tb=len(h),
                 changes_per_s=ch / dur, mean_run_s=dur / max(ch + 1, 1), nsubch_mean=h.n_subch.mean(),
                 plmax_mean=h.pl_max_db.mean(), margin_med=np.median(margin), best_score_mean=srt[:, -1].mean(),
                 cam_age_ms=h.cam_age_ms.mean())
        for m in range(1, 9):
            d[f'share_m{m}'] = (h.applied == m).mean()
        rows.append(d)
        x = h[['t_ndi', 'applied', 'n_subch', 'pl_max_db']].copy()
        x['margin'] = margin
        for k, v in dict(arm=arm, sc=sc, tr=tr, nbg=d['nbg'], seed=d['seed']).items():
            x[k] = v
        seqs.append(x)
R = pd.DataFrame(rows); Q = pd.concat(seqs)
R.to_csv(f'{OUT}/hsel_runs.csv', index=False); Q.to_csv(f'{OUT}/hsel_tb.csv.gz', index=False)
pd.set_option('display.width', 250)
share = [f'share_m{m}' for m in range(1, 9)]
G = R.groupby(['sc', 'tr', 'nbg', 'arm'])[share + ['changes_per_s', 'mean_run_s', 'nsubch_mean', 'plmax_mean', 'margin_med']].mean()
G.round(3).to_csv(f'{OUT}/hsel_summary.csv')
print(G.round(2).to_string())
# agreement between arms: same scenario/seed, nearest TB within 2 ms
agree = []
for (sc, tr, nb, seed), g in Q.groupby(['sc', 'tr', 'nbg', 'seed']):
    ref = g[g.arm == 'R4'].sort_values('t_ndi')
    for arm in ['R4.1', 'R4.2', 'R5', 'R6']:
        o = g[g.arm == arm].sort_values('t_ndi')
        if len(o) == 0 or len(ref) == 0:
            continue
        m = pd.merge_asof(ref[['t_ndi', 'applied', 'n_subch']], o[['t_ndi', 'applied', 'n_subch']], on='t_ndi',
                          direction='nearest', tolerance=0.002, suffixes=('_r4', '_x')).dropna()
        same_n = m.n_subch_r4 == m.n_subch_x
        agree.append(dict(sc=sc, tr=tr, nbg=nb, seed=seed, arm=arm, matched=len(m) / len(ref),
                          same_helper=(m.applied_r4 == m.applied_x).mean(),
                          same_helper_same_nsub=(m.applied_r4 == m.applied_x)[same_n].mean() if same_n.any() else np.nan,
                          same_nsub=same_n.mean()))
A = pd.DataFrame(agree)
A.groupby(['sc', 'tr', 'nbg', 'arm']).mean(numeric_only=True).drop(columns='seed').round(3).to_csv(f'{OUT}/hsel_agreement_vs_R4.csv')
print(A.groupby(['sc', 'tr', 'nbg', 'arm']).mean(numeric_only=True).drop(columns='seed').round(3).to_string())
