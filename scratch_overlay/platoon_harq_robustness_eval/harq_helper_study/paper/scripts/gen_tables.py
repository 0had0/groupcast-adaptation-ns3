"""Table III (group PRR, all arms) + S_tx.csv (transmissions per packet, from the raw runs)."""
import glob, os, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.join(HERE, '..', 'data')
S = pd.read_csv(f'{D}/S_cells.csv')
ARMS = ['R1', 'R2', 'R3', 'R4', 'R4.1', 'R4.2', 'R5', 'R6']
SC = {'hwy': 'Hwy', 'rbt': 'Rbt W', 'rbtE': 'Rbt E'}; TR = {'periodic1tb': 'per.', 'aperiodic1tb': 'aper.'}
lines = [r'\begin{tabular}{@{}l@{\hspace{3pt}}c@{\hspace{4pt}}' + 'c' * 8 + '@{}}', r'\toprule',
         'Scenario & cars & ' + ' & '.join(ARMS) + r' \\', r'\midrule']
for sc in SC:
    for tr in TR:
        for nb in (0, 60):
            x = S[(S.sc == sc) & (S.tr == tr) & (S.nbg == nb)].set_index('arm')
            v = [x.loc[a, 'prr'] for a in ARMS]; best = max(v)
            cells = [(r'\textbf{%.2f}' if abs(p - best) < 5e-3 else '%.2f') % p for p in v]
            lines.append(f'{SC[sc]} {TR[tr]} & {nb} & ' + ' & '.join(cells) + r' \\')
        if not (sc == 'rbtE' and tr == 'aperiodic1tb'):
            lines.append(r'\addlinespace[1pt]')
lines += [r'\bottomrule', r'\end{tabular}']
open(os.path.join(HERE, '..', 'tables', 'tab_prr.tex'), 'w').write('\n'.join(lines) + '\n')
B = os.path.expanduser('~/mnt/harq_helper_study/out/remote_full')
rows = []
for arm in ['R1', 'R3', 'R4', 'R4.2', 'R6']:
    for f in glob.glob(f'{B}/{arm}/*_run.csv'):
        r = pd.read_csv(f).iloc[0]; sc, _, tr, bg, s = os.path.basename(f).split('_')[:5]
        rows.append(dict(arm=arm, sc=sc, tr=tr, nbg=0 if bg == 'none' else 60, leader_tx=r.leader_tx / r.pkts, helper_tx=r.helper_tx / r.pkts))
pd.DataFrame(rows).groupby(['arm', 'sc', 'tr', 'nbg']).mean().round(3).reset_index().to_csv(f'{D}/S_tx.csv', index=False)
print(open(os.path.join(HERE, '..', 'tables', 'tab_prr.tex')).read())
