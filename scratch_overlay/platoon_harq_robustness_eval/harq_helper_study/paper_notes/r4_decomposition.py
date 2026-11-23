"""Why does R4 lose to R1 under saturated aperiodic load? Split the gap into
   (a) fewer attempts (handed slots left silent) and (b) worse attempts (helper copies decode less often)."""
import os, numpy as np, pandas as pd
B = os.path.expanduser('~/mnt/harq_helper_study/out/remote_full')
def rd(p): return pd.read_csv(p if os.path.exists(p) else p + '.gz')
def load(arm, sc, tr, bg, s):
    pre = f'{B}/{arm}/{sc}_ivd30_{tr}_{bg}_s{s}'
    r = rd(pre + '_run.csv').iloc[0]; a = rd(pre + '_att.csv')
    tb0 = a[(a.side == 0) & (a.tx_in_tb == 1) & (a.t >= r.kpi_start) & (a.t < r.kpi_end - 0.05)].tb.unique()
    return a[a.tb.isin(tb0)]
rows = []
for sc, tr, bg in [('hwy', 'aperiodic1tb', 'moderate'), ('hwy', 'aperiodic1tb', 'none'), ('rbt', 'periodic1tb', 'none'), ('rbt', 'aperiodic1tb', 'moderate')]:
    for s in range(1, 11):
        a1 = load('R1', sc, tr, bg, s); a4 = load('R4', sc, tr, bg, s)
        for m in range(1, 9):
            x1 = a1[a1.member == m]; x4 = a4[a4.member == m]
            x1 = x1.sort_values('t'); x1 = x1[x1.groupby('tb').status.transform(lambda v: (v == 1).cumsum().shift(fill_value=0)) == 0]  # attempts before first success
            pL1 = (x1[x1.tx_in_tb == 1].status == 1).mean(); pLR = (x1[x1.tx_in_tb > 1].status == 1).mean()
            prr1 = x1.groupby('tb').status.apply(lambda v: (v == 1).any()).mean()
            x4 = x4.sort_values('t')
            l4 = x4[x4.side == 0]
            k = x4[x4.side == 1].groupby('tb').size().reindex(l4.tb.unique(), fill_value=0).clip(upper=2)
            x4c = x4[x4.groupby('tb').status.transform(lambda v: (v == 1).cumsum().shift(fill_value=0)) == 0]
            h4 = x4c[x4c.side == 1]
            pL1b = (l4.status == 1).mean()
            pH = (h4.status == 1).mean() if len(h4) else np.nan
            prr4 = x4.groupby('tb').status.apply(lambda v: (v == 1).any()).reindex(l4.tb.unique(), fill_value=False).mean()
            ph = 0 if np.isnan(pH) else pH
            pred4 = 1 - (1 - pL1b) * np.mean((1 - ph) ** k)
            cfA = 1 - (1 - pL1b) * (1 - ph) ** 2                      # helper never silent (2 copies always)
            cfB = 1 - (1 - pL1b) * np.mean((1 - pLR) ** k)            # same silences, copies as good as R1's retx
            rows.append(dict(cell=f'{sc} {tr[:4]} {bg}', seed=s, m=m, prr1=prr1, prr4=prr4, pred4=pred4, cfA=cfA, cfB=cfB,
                             pL1=pL1, pLR=pLR, pL1_r4=pL1b, pH=pH, copies=k.mean()))
D = pd.DataFrame(rows)
pd.set_option('display.width', 250)
G = D.groupby('cell')[['prr1', 'prr4', 'pred4', 'cfA', 'cfB', 'pL1', 'pLR', 'pL1_r4', 'pH', 'copies']].mean().round(3)
print(G.to_string())
print(D[D.m == 1].groupby('cell')[['prr1', 'prr4', 'pL1', 'pLR', 'pL1_r4', 'pH', 'copies']].mean().round(3).to_string())
D.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'r4_decomposition.csv'), index=False)
