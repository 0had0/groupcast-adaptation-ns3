"""Audited analysis for TAKEOVER_RESULTS_v3_audited.ipynb. Reads only the per-run tables cached by takeover_data.py
(out/cache_takeover_v3/<arm>_{win,sinr,lat}.csv); imports no project module and runs no simulation.

Terms (used in every label):
  pair                 a (TB, member) pair inside the run's KPI window; every leader packet is one TB.
  pair delivery prob.  delivered pairs / eligible pairs; delivered = the member decoded the TB by the PDB.
  residual pair failure probability (by the PDB) = 1 - pair delivery probability. Includes SCI misses, decoding
                       failures and deadline failures. It is NOT the PHY BLER of an attempt and NOT the probability
                       that a TB reached every member (all-member delivery, reported separately).
  group TB-equivalents delivered pairs / 8: a fractional count; 4 members of a TB count as half a TB.
  efficiency           group TB-equivalents per subchannel-slot (10 PRB x 1 slot), PSFCH excluded, per RESERVED
                       (leader grants; helper relays reuse handed slots) or per TRANSMITTED (leader + helper PSSCH).
  takeover attempt     one leader transmission in a slot handed to the helper (trigger: helper NACK, or fallback =
                       no helper SCI detected in the handed slot).
  attempt success      the attempt was the first decode of the TB for >= 1 member that lacked it (attributed rescue;
                       not a counterfactual gain: that member might have decoded a later copy anyway).
  redundant attempt    nobody lacked the TB at the attempt (the transmission added nothing; this does not prove the
                       helper-silence detection was wrong).
Gains of a proposed arm P over a baseline B (pair delivery probabilities p, b):
  absolute  100 (p - b) percentage points;  relative 100 (p / b - 1) %;  loss reduction 100 (p - b) / (1 - b) %.
Statistics: seeds are the independent unit (10 per cell). CIs: percentile bootstrap over seeds, ratio of pooled counts
recomputed in each replicate; paired comparisons resample the SAME seed indices in both arms. With 10 clusters the
percentile bootstrap tends to be narrow; per-seed win counts are reported beside it.
Seed-support rule (binned figures): a point is drawn only if >= MIN_SEEDS distinct seeds contribute and the pooled
count reaches the per-figure minimum; unsupported points are exported with supported=False and not drawn.
"""
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'out', 'cache_takeover_v3')
NMEM = 8
PDB_MS = {'periodic1tb': 100.0, 'aperiodic1tb': 50.0}
SINR_MISS, SINR_STEP = -999, 2           # as written by takeover_data.py
LAT_STEP_MS = 0.25
MIN_SEEDS = 5
MIN_PAIRS = 200
MIN_ATTEMPTS = 30
B_PAIRED = 20000
B_DESC = 2000
RNG_SEED = 20261112
HF_EDGES = [0, .1, .2, .3, .4, .5, .6, .8, 1.0001]
KEY = ['sc', 'tr', 'nbg', 'seed']
PAIRS = [('R6', 'R4.2'), ('R5', 'R4.1')]     # (proposed, baseline): takeover on vs off, same HARQ option

SCS = ['hwy', 'rbt', 'rbtE']
TRS = ['periodic1tb', 'aperiodic1tb']
SCN = {'hwy': 'Highway', 'rbt': 'Roundabout W (curve)', 'rbtE': 'Roundabout E (curve)'}
TRN = {'periodic1tb': 'periodic (100 ms)', 'aperiodic1tb': 'aperiodic'}
LABEL = {'R1': 'R1 leader blind', 'R2': 'R2 leader Opt1', 'R3': 'R3 leader Opt2', 'R4': 'R4 Kuo blind',
         'R4.1': 'R4.1 Kuo+Opt1', 'R4.2': 'R4.2 Kuo+Opt2', 'R5': 'R5 Kuo+Opt1+takeover',
         'R6': 'R6 Kuo+Opt2+takeover'}
STYLE = {'R1': ('black', '-', 1.5), 'R2': ('gray', '--', 1.5), 'R3': ('silver', ':', 1.5),
         'R4': ('tab:blue', '-', 2.0), 'R4.1': ('tab:blue', '--', 1.5), 'R4.2': ('tab:blue', ':', 1.5),
         'R5': ('tab:red', '-', 2.5), 'R6': ('tab:green', '-', 2.5)}
MARK = {'R1': 'o', 'R2': 'o', 'R3': 'o', 'R4': 's', 'R4.1': 's', 'R4.2': 's', 'R5': '^', 'R6': '^'}
PAIR_COL = {'R6': 'tab:green', 'R5': 'tab:red'}


# ------------------------------------------------------------------------------------------------- loading
def load(arms, cache=CACHE):
    out = {}
    for t in ('win', 'sinr', 'lat'):
        fr = [pd.read_csv(os.path.join(cache, f'{a}_{t}.csv')) for a in arms]
        out[t] = pd.concat(fr, ignore_index=True)
    w = out['win']
    w['tbeq'] = w.n_deliv / NMEM
    return out


# ------------------------------------------------------------------------------------------------- bootstrap
def _rng():
    return np.random.default_rng(RNG_SEED)


def boot_ratio(g, num, den, B=B_DESC, rng=None, info=False):
    """pooled sum(num)/sum(den) over the rows of g, percentile CI resampling seeds; n_seeds.
    Every sampled seed stays in the resampling frame, also seeds with a zero denominator. info=True also returns
    the number of seeds with a positive denominator and the share of replicates whose ratio is undefined (all
    resampled seeds have a zero denominator); those replicates are dropped from the percentiles."""
    s = g.groupby('seed')[[num, den]].sum()
    N, D = s[num].to_numpy(float), s[den].to_numpy(float)
    n = len(s)
    n_pos = int((D > 0).sum())
    r = N.sum() / D.sum() if D.sum() > 0 else np.nan
    if n < MIN_SEEDS or D.sum() == 0:
        return (r, np.nan, np.nan, n, n_pos, np.nan) if info else (r, np.nan, np.nan, n)
    rng = rng or _rng()
    idx = rng.integers(0, n, (B, n))
    with np.errstate(invalid='ignore', divide='ignore'):
        bs = N[idx].sum(1) / D[idx].sum(1)
    undef = float(np.isnan(bs).mean())
    lo, hi = np.nanpercentile(bs, [2.5, 97.5])
    return (r, lo, hi, n, n_pos, undef) if info else (r, lo, hi, n)


def paired_boot(gp, gb, num, den, B=B_PAIRED, rng=None):
    """Paired difference of pooled ratios, proposed (gp) minus baseline (gb). Asserts the same seed set and, for the
    denominator, identical counts per seed when den is a pair count. Returns dict of point estimates, percentile CIs
    of absolute / relative / loss-reduction gains (same replicates) and per-seed win counts."""
    sp = gp.groupby('seed')[[num, den]].sum().sort_index()
    sb = gb.groupby('seed')[[num, den]].sum().sort_index()
    assert list(sp.index) == list(sb.index), ('seed sets differ', list(sp.index), list(sb.index))
    n = len(sp)
    Np, Dp, Nb, Db = (x.to_numpy(float) for x in (sp[num], sp[den], sb[num], sb[den]))
    p, b = Np.sum() / Dp.sum(), Nb.sum() / Db.sum()
    rng = rng or _rng()
    idx = rng.integers(0, n, (B, n))
    P, Bb = Np[idx].sum(1) / Dp[idx].sum(1), Nb[idx].sum(1) / Db[idx].sum(1)
    ci = lambda v: tuple(np.nanpercentile(v, [2.5, 97.5]))
    with np.errstate(invalid='ignore', divide='ignore'):
        rel_bs, lr_bs = 100 * (P / Bb - 1), 100 * (P - Bb) / (1 - Bb)
        lr = 100 * (p - b) / (1 - b) if b < 1 else np.nan
    per_seed = Np / Dp - Nb / Db
    a_lo, a_hi = ci(100 * (P - Bb))
    r_lo, r_hi = ci(rel_bs)
    l_lo, l_hi = ci(lr_bs) if np.isfinite(lr) else (np.nan, np.nan)
    return dict(n_seeds=n, p_val=p, b_val=b,
                abs_pp=100 * (p - b), abs_lo=a_lo, abs_hi=a_hi,
                rel_pct=100 * (p / b - 1), rel_lo=r_lo, rel_hi=r_hi,
                loss_red_pct=lr, lr_lo=l_lo, lr_hi=l_hi,
                seeds_better=int((per_seed > 0).sum()), seeds_worse=int((per_seed < 0).sum()))


# ------------------------------------------------------------------------------------------------- checks
LOADS = [0, 15, 30, 45, 60]
SEEDS = list(range(1, 11))


def expected_runs():
    """The complete run set of the campaign: 3 scenarios x 2 traffic classes x 5 loads x 10 seeds."""
    return pd.MultiIndex.from_product([SCS, TRS, LOADS, SEEDS], names=KEY)


def assert_complete(W, arms):
    """Every arm has exactly the expected runs, and no (arm, run, window) row appears twice."""
    dup = W.duplicated(['arm'] + KEY + ['w'])
    assert not dup.any(), f'{int(dup.sum())} duplicate (arm, sc, tr, nbg, seed, w) rows'
    exp = expected_runs()
    for a in arms:
        got = pd.MultiIndex.from_frame(W[W.arm == a][KEY].drop_duplicates()).sort_values()
        miss, extra = exp.difference(got), got.difference(exp)
        assert len(miss) == 0 and len(extra) == 0, f'{a}: {len(miss)} runs missing, {len(extra)} unexpected'
    return len(exp)


def coverage_check(W):
    """Assert, for every comparison pair: both arms hold the complete expected run set, no duplicate window rows,
    one-to-one run and window matching, identical pair counts per run and per window."""
    assert_complete(W, [a for pair in PAIRS for a in pair])
    rows = []
    for p, b in PAIRS:
        rp = W[W.arm == p].groupby(KEY)[['n_pairs', 'n_pkt']].sum()
        rb = W[W.arm == b].groupby(KEY)[['n_pairs', 'n_pkt']].sum()
        assert rp.index.equals(rb.index) or set(rp.index) == set(rb.index), f'{p}/{b}: run sets differ'
        j = rp.join(rb, lsuffix='_p', rsuffix='_b', how='outer', validate='one_to_one')
        assert not j.isna().any().any(), f'{p}/{b}: unmatched runs'
        assert (j.n_pairs_p == j.n_pairs_b).all(), f'{p}/{b}: pair counts differ'
        wp = W[W.arm == p].set_index(KEY + ['w']).index
        wb = W[W.arm == b].set_index(KEY + ['w']).index
        assert wp.is_unique and wb.is_unique, f'{p}/{b}: duplicate window keys'
        assert set(wp) == set(wb), f'{p}/{b}: window sets differ'
        jw = (W[W.arm == p].set_index(KEY + ['w'])[['n_pairs']]
              .join(W[W.arm == b].set_index(KEY + ['w'])[['n_pairs']], lsuffix='_p', rsuffix='_b', how='outer',
                    validate='one_to_one'))
        assert not jw.isna().any().any(), f'{p}/{b}: unmatched windows'
        assert (jw.n_pairs_p == jw.n_pairs_b).all(), f'{p}/{b}: pair counts differ in some window'
        spc = j.reset_index().groupby(['sc', 'tr', 'nbg']).seed.nunique()
        rows.append(dict(proposed=p, baseline=b, runs=len(j), cells=len(spc), seeds_min=spc.min(), seeds_max=spc.max(),
                         windows=len(wp), pairs=int(j.n_pairs_p.sum())))
    return pd.DataFrame(rows)


def bin_mismatch(W):
    """Share of matched windows (same scenario, traffic, load, seed, window) that the per-arm helper-failure rule puts
    in different bins for the proposed and the baseline arm."""
    rows = []
    for p, b in PAIRS:
        j = _matched_windows(W, p, b)
        bp = pd.cut(j.hf_p, HF_EDGES, right=False, labels=False)
        bb = pd.cut(j.hf_b, HF_EDGES, right=False, labels=False)
        rows.append(dict(proposed=p, baseline=b, matched_windows=len(j), different_bin=(bp != bb).mean(),
                         bin_differs_by_2plus=((bp - bb).abs() >= 2).mean(),
                         hf_proposed=j.n_hfail_p.sum() / j.n_helper_p.sum(),
                         hf_baseline=j.n_hfail_b.sum() / j.n_helper_b.sum(),
                         corr_window_hf=np.corrcoef(j.hf_p, j.hf_b)[0, 1]))
    return pd.DataFrame(rows)


def _matched_windows(W, p, b):
    k = KEY + ['w']
    cols = ['n_hfail', 'n_helper', 'n_pairs', 'n_deliv', 'n_pkt']
    x = W[W.arm == p].set_index(k)[cols]
    y = W[W.arm == b].set_index(k)[cols]
    j = x.join(y, lsuffix='_p', rsuffix='_b', how='inner')
    j = j[(j.n_helper_p > 0) & (j.n_helper_b > 0)].copy()
    j['hf_p'] = j.n_hfail_p / j.n_helper_p
    j['hf_b'] = j.n_hfail_b / j.n_helper_b
    return j.reset_index()


# ------------------------------------------------------------------------------------------------- paired tables
def paired_by_load(W, num='n_deliv', den='n_pairs', pooled_loads=True):
    """Paired gain per (pair, sc, tr, load); plus an 'all' load row resampling seeds jointly across loads."""
    rng = _rng()
    rows = []
    for p, b in PAIRS:
        for sc in SCS:
            for tr in TRS:
                cell = W[(W.sc == sc) & (W.tr == tr)]
                loads = sorted(cell.nbg.unique())
                for nbg in loads + (['all'] if pooled_loads else []):
                    c = cell if nbg == 'all' else cell[cell.nbg == nbg]
                    r = paired_boot(c[c.arm == p], c[c.arm == b], num, den, rng=rng)
                    rows.append(dict(proposed=p, baseline=b, sc=sc, tr=tr, nbg=nbg) | r)
    return pd.DataFrame(rows)


def paired_ratio_diff(W, num, den):
    """Paired difference (proposed - baseline) of a pooled ratio such as efficiency; absolute and relative."""
    rng = _rng()
    rows = []
    for p, b in PAIRS:
        for (sc, tr, nbg), c in W.groupby(['sc', 'tr', 'nbg']):
            r = paired_boot(c[c.arm == p], c[c.arm == b], num, den, rng=rng)
            rows.append(dict(proposed=p, baseline=b, sc=sc, tr=tr, nbg=nbg, n_seeds=r['n_seeds'],
                             proposed_val=r['p_val'], baseline_val=r['b_val'],
                             diff=r['abs_pp'] / 100, diff_lo=r['abs_lo'] / 100, diff_hi=r['abs_hi'] / 100,
                             rel_pct=r['rel_pct'], rel_lo=r['rel_lo'], rel_hi=r['rel_hi'],
                             seeds_better=r['seeds_better'], seeds_worse=r['seeds_worse']))
    return pd.DataFrame(rows)


def paired_by_ref_bin(W):
    """Secondary, descriptive: paired gain within bins of a COMMON reference helper-failure rate = mean of the two
    arms' realized rates in the matched window. Same windows in both arms; seeds resampled jointly."""
    rng = _rng()
    rows = []
    for p, b in PAIRS:
        j = _matched_windows(W, p, b)
        j['hf_ref'] = (j.hf_p + j.hf_b) / 2
        j['hbin'] = pd.cut(j.hf_ref, HF_EDGES, right=False, labels=False)
        for (sc, tr, hb), g in j.groupby(['sc', 'tr', 'hbin']):
            ns = g.seed.nunique()
            base = dict(proposed=p, baseline=b, sc=sc, tr=tr, hbin=int(hb),
                        hf_ref=(g.hf_ref * g.n_pkt_p).sum() / g.n_pkt_p.sum(), n_seeds=ns, n_windows=len(g),
                        n_pairs=int(g.n_pairs_p.sum()))
            ok = ns >= MIN_SEEDS and g.n_pairs_p.sum() >= MIN_PAIRS
            if ok:
                gp = g.rename(columns={'n_deliv_p': 'd', 'n_pairs_p': 'n'})[['seed', 'd', 'n']]
                gb = g.rename(columns={'n_deliv_b': 'd', 'n_pairs_b': 'n'})[['seed', 'd', 'n']]
                r = paired_boot(gp, gb, 'd', 'n', rng=rng)
                base |= {k: r[k] for k in ('p_val', 'b_val', 'abs_pp', 'abs_lo', 'abs_hi', 'seeds_better')}
            rows.append(base | dict(supported=ok))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------- descriptive bins
def hf_bins(W, arms, ref_arm_for_leader='R6'):
    """Descriptive: each helper arm binned by ITS OWN realized helper-failure rate; leader-only arms at the rate of
    ref_arm_for_leader in the same window. Returns the windows with hf, hbin."""
    ref = W[W.arm == ref_arm_for_leader].set_index(KEY + ['w'])[['n_hfail', 'n_helper']]
    out = []
    for a in arms:
        x = W[W.arm == a]
        if x.n_helper.sum() > 0:
            hf = x.n_hfail / x.n_helper.replace(0, np.nan)
        else:
            r = ref.reindex(pd.MultiIndex.from_frame(x[KEY + ['w']]))
            hf = pd.Series((r.n_hfail / r.n_helper.replace(0, np.nan)).to_numpy(), index=x.index)
        out.append(x.assign(hf=hf).dropna(subset=['hf']))
    x = pd.concat(out)
    x['hbin'] = pd.cut(x.hf, HF_EDGES, right=False, labels=False)
    return x


def binned_points(x, by, num, den, min_den, count_cols=('n_pairs',)):
    """Pooled ratio per group (by + hbin) with seed support. Every point is returned; supported marks drawable ones."""
    rng = _rng()
    rows = []
    for k, g in x.groupby(by + ['hbin']):
        r, lo, hi, ns = boot_ratio(g, num, den, rng=rng)
        d = dict(zip(by + ['hbin'], k)) | dict(x=(g.hf * g.n_pkt).sum() / g.n_pkt.sum(), y=r, lo=lo, hi=hi,
                                                n_seeds=ns, n_windows=len(g), den_total=float(g[den].sum()))
        for c in count_cols:
            d[c] = float(g[c].sum())
        d['supported'] = bool(ns >= MIN_SEEDS and g[den].sum() >= min_den)
        if not d['supported']:
            d['lo'] = d['hi'] = np.nan
        rows.append(d)
    return pd.DataFrame(rows)


def sinr_points(S, arms):
    """Residual pair failure per first-copy SINR bin, pooled over the loads in S; zero-loss bins kept (y = 0)."""
    rng = _rng()
    S = S.assign(n_lost=S.n_pairs - S.n_deliv)
    rows = []
    for (sc, tr, arm, b), g in S[S.arm.isin(arms)].groupby(['sc', 'tr', 'arm', 'bin']):
        r, lo, hi, ns = boot_ratio(g, 'n_lost', 'n_pairs', rng=rng)
        sup = bool(ns >= MIN_SEEDS and g.n_pairs.sum() >= MIN_PAIRS)
        rows.append(dict(sc=sc, tr=tr, arm=arm, bin=int(b), n_pairs=int(g.n_pairs.sum()),
                         n_lost=int(g.n_lost.sum()), n_seeds=ns, y=r,
                         lo=lo if sup and r > 0 else np.nan, hi=hi if sup and r > 0 else np.nan, supported=sup))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------- plotting
def grid(title, ylabel, xlabel, sharey=True, figsize=(15, 7.5)):
    fig, ax = plt.subplots(2, 3, figsize=figsize, sharey=sharey, squeeze=False)
    for i, tr in enumerate(TRS):
        for j, sc in enumerate(SCS):
            a = ax[i, j]
            a.grid(alpha=.3, lw=.5)
            a.set_title(f'{SCN[sc]}, {TRN[tr]}', fontsize=10)
            if j == 0:
                a.set_ylabel(ylabel)
            if i == 1:
                a.set_xlabel(xlabel)
    fig.suptitle(title, fontsize=12)
    return fig, ax


def arm_handles(arms):
    return [Line2D([], [], color=STYLE[a][0], ls=STYLE[a][1], lw=STYLE[a][2], marker=MARK[a], label=LABEL[a])
            for a in arms]


def finish(fig, handles, path=None, ncol=5):
    fig.legend(handles=handles, loc='lower center', ncol=min(len(handles), ncol), fontsize=9, frameon=False)
    fig.tight_layout(rect=(0, .08, 1, .96))
    if path:
        fig.savefig(path + '.pdf', bbox_inches='tight')
        fig.savefig(path + '.png', dpi=130, bbox_inches='tight')


def band(ax, x, lo, hi, valid, color, alpha=.12, floor=None):
    """Shaded interval drawn on the same contiguous runs of valid points as the line: a band never bridges an
    omitted, unsupported or zero point. x, lo, hi, valid: arrays over the FULL x sequence (missing bins included)."""
    x, lo, hi = (np.asarray(v, float) for v in (x, lo, hi))
    ok = np.asarray(valid, bool) & np.isfinite(x) & np.isfinite(lo) & np.isfinite(hi)
    i = 0
    while i < len(ok):
        if not ok[i]:
            i += 1
            continue
        k = i
        while k + 1 < len(ok) and ok[k + 1]:
            k += 1
        sl = slice(i, k + 1)
        l = np.clip(lo[sl], floor, None) if floor is not None else lo[sl]
        ax.fill_between(x[sl], l, hi[sl], color=color, alpha=alpha, lw=0)
        i = k + 1


def full_bins(p, col, values):
    """Reindex the rows of one curve to the full bin sequence, so an absent bin also breaks the line and band."""
    return p.set_index(col).reindex(values).reset_index()


def segments(xs, ys):
    """Split a curve where a value is missing, so lines never bridge omitted or zero points."""
    seg, cur = [], []
    for x, y in zip(xs, ys):
        if np.isfinite(y):
            cur.append((x, y))
        elif cur:
            seg.append(cur)
            cur = []
    if cur:
        seg.append(cur)
    return seg
