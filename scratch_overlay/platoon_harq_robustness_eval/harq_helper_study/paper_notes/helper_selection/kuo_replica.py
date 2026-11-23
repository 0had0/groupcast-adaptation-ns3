"""Offline replica of helper_select.cc (policy kuo) + Kuo et al. 2021 literal variants.
Checks (1) the replica reproduces the logged choices, (2) what Kuo's full rule would pick:
   K1 = eq.12-13 'maxmean once' with the leader as a candidate (Kuo: 'either itself or other UE')
   K2 = eq.17-18 'maxmean twice' (two retransmission UEs i,j incl. the leader), Kuo's proposal for 2 retx."""
import gzip, math, os, sys, itertools, numpy as np, pandas as pd
E = os.path.expanduser('~/mnt/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval')
B = os.path.expanduser('~/mnt/harq_helper_study/out/remote_full')
FC, TXP, NF, SINR, SUBPRB = 5.89, 23.0, 9.0, 8.0, 10
CLAMP = lambda p: min(max(p, 1e-9), 1 - 1e-9)
Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
def plmax(ns):
    nprb = max(1.0, ns * SUBPRB)
    return TXP - 10 * math.log10(nprb) - (-174 + 10 * math.log10(12 * 30e3) + NF) - SINR
def model(hwy):
    def pllos(d):
        d = max(d, 1.0)
        return 32.4 + 20 * math.log10(d) + 20 * math.log10(FC) if hwy else 38.77 + 16.7 * math.log10(d) + 18.2 * math.log10(FC)
    def plos(d):
        if hwy:
            return CLAMP(2.1013e-6 * d * d - 0.002 * d + 1.0193) if d <= 475 else CLAMP(0.54 - 0.001 * (d - 475))
        return CLAMP(1.05 * math.exp(-0.0114 * d))
    def ps(d, pm):
        slos = Phi((pm - pllos(d)) / 3.0)
        mu = 9.0 + max(0.0, 15 * math.log10(max(d, 1)) - 41); sig = 4.5
        s = Phi(-mu / sig) * slos; hi = max(20.0, max(0, mu) + 8 * sig); n = 80; w = hi / n
        for k in range(n):
            b = (k + .5) * w
            s += math.exp(-.5 * ((b - mu) / sig) ** 2) / (sig * math.sqrt(2 * math.pi)) * w * Phi((pm - pllos(d) - b) / 3.0)
        return CLAMP(plos(d) * slos + (1 - plos(d)) * CLAMP(s))
    return ps
def choose(P, pm, ps):
    M = 8
    D = lambda a, b: math.hypot(P[a][0] - P[b][0], P[a][1] - P[b][1])
    S = {(a, b): (1.0 if a == b else ps(D(a, b), pm)) for a in range(9) for b in range(9)}   # success prob
    F = {k: 1 - v for k, v in S.items()}                                                      # failure prob, F[a,a]=0
    pL = {r: S[0, r] for r in range(1, 9)}
    # (0) simulator: members only, r==i -> pL[i]
    best, ch = -1, None
    for i in range(1, 9):
        g = np.mean([pL[r] if r == i else CLAMP(pL[r] + (1 - pL[r]) * pL[i] * S[i, r]) for r in range(1, 9)])
        if g > best + 1e-12 or (abs(g - best) <= 1e-12 and pL[i] > pL[ch]): best, ch = g, i
    # K1: Kuo eq.12 literal over candidates {leader 0, members}
    g1 = {i: np.mean([1 - F[0, r] * (F[0, i] + (1 - F[0, i]) * F[i, r]) for r in range(1, 9)]) for i in range(0, 9)}
    k1 = max(g1, key=g1.get)
    # K2: Kuo eq.17 over pairs (i, j)
    best2, k2 = -1, None
    for i, j in itertools.product(range(9), range(9)):
        Fs_tj = F[0, j] * (F[0, i] + (1 - F[0, i]) * F[i, j])
        g = np.mean([1 - F[0, r] * (F[0, i] + (1 - F[0, i]) * F[i, r]) * (Fs_tj + (1 - Fs_tj) * F[j, r]) for r in range(1, 9)])
        if g > best2 + 1e-12: best2, k2 = g, (i, j)
    return ch, best, k1, g1[k1], g1[0], k2, best2
def trace(tag):
    rows = []
    with gzip.open(f'{E}/traces/{tag}.csv.gz', 'rt') as f:
        for line in f:
            if line[:1].isdigit():
                t, n, x, y = line.split(',')[:4]
                if int(n) <= 8: rows.append((round(float(t), 1), int(n), float(x), float(y)))
    d = pd.DataFrame(rows, columns=['t', 'n', 'x', 'y'])
    return {t: {r.n: (r.x, r.y) for r in g.itertuples()} for t, g in d.groupby('t')}
out = []
for sc, tr, bg in [(s, t, b) for s in ['hwy', 'rbt', 'rbtE'] for t in ['periodic1tb', 'aperiodic1tb'] for b in ['none', 'moderate']]:
    seed = 1; nbg = 0 if bg == 'none' else 60
    tg = f'{sc}_ivd30_n{nbg}_s{seed}'
    if not os.path.exists(f'{E}/traces/{tg}.csv.gz'): continue
    T = trace(tg); ps = model(sc == 'hwy')
    h = pd.read_csv(f'{B}/R4/{sc}_ivd30_{tr}_{bg}_s{seed}_hsel.csv')
    r = pd.read_csv(f'{B}/R4/{sc}_ivd30_{tr}_{bg}_s{seed}_run.csv').iloc[0]
    h = h[(h.t_ndi >= r.kpi_start) & (h.t_ndi < r.kpi_end)]
    h = h.iloc[::max(1, len(h) // 150)]       # subsample ~150 TBs per run
    cache = {}
    for x in h.itertuples():
        tc = round(math.floor((x.t_decide - x.cam_age_ms / 1000) * 10 + 1e-6) / 10, 1)
        P = T.get(tc)
        if P is None or len(P) < 9: continue
        key = (tc, x.n_subch)
        if key not in cache: cache[key] = choose(P, plmax(x.n_subch), ps)
        ch, g0, k1, gk1, gleader, k2, g2 = cache[key]
        out.append(dict(sc=sc, tr=tr, nbg=nbg, logged=x.applied, replica=ch, k1=k1, k2=f'{k2[0]},{k2[1]}',
                        k2_has_leader=0 in k2, k2_two_diff=k2[0] != k2[1], g_sim=g0, g_k1_best=gk1, g_k1_leader=gleader, g_k2=g2))
    print(sc, tr, bg, 'done', file=sys.stderr, flush=True)
O = pd.DataFrame(out); O.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'kuo_replica.csv'), index=False)
pd.set_option('display.width', 250)
G = O.groupby(['sc', 'tr', 'nbg']).agg(n=('logged', 'size'), replica_match=('replica', lambda s: (s == O.loc[s.index, 'logged']).mean()),
        k1_leader=('k1', lambda s: (s == 0).mean()), k1_same_as_sim=('k1', lambda s: (s == O.loc[s.index, 'logged']).mean()),
        k2_has_leader=('k2_has_leader', 'mean'), k2_two_diff=('k2_two_diff', 'mean'), g_sim=('g_sim', 'mean'), g_k1_leader=('g_k1_leader', 'mean'), g_k2=('g_k2', 'mean'))
print(G.round(4).to_string())
print(O.groupby(['sc', 'tr'])['k2'].agg(lambda s: s.value_counts(normalize=True).round(2).head(4).to_dict()).to_string())
print(O.groupby(['sc', 'tr'])['k1'].agg(lambda s: s.value_counts(normalize=True).round(2).head(4).to_dict()).to_string())
