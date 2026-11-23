#!/usr/bin/env python3
"""Build NOBG_DECOMPOSITION_v3.ipynb: PRR decomposition by delivering copy and helper-reception analysis at 0
background cars (campaign v3). Tables: nobg_decomposition.py (cached in out/cache_nobg_v3); statistics and plotting
helpers: takeover_audit.py. Figures / tables: figures/nobg_v3/.

  python3 scripts/build_nobg_notebook.py
  python3 -m jupyter nbconvert --to notebook --execute --inplace notebooks/NOBG_DECOMPOSITION_v3.ipynb
"""
import os
import nbformat as nbf
import nb_layout

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # harq_helper_study/ (this file: scripts/)
md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell


def cells():
    C = []
    C.append(md(r"""# PRR decomposition without background traffic (campaign v3, 0 cars)

**Data:** `out/remote_v3`, 0 background cars, 3 scenarios × 2 traffic classes × 10 seeds per arm. Without
background vehicles a lost (TB, member) pair is due to the channel (path loss, building and vehicle blockage,
fading) and decoding only.

**Arms:** R1 blind leader (3 copies, decoded independently); R1_bsc the same with blind soft combining; R3 leader
HARQ Option 2; **R4_bsc = "Blind + C"**: Kuo helper relaying blindly in the handed slots, receivers combining every
copy; R4.2 Kuo helper with Option 2 and **no** takeover; **R6** Kuo helper + Option 2 + takeover. R6 − R4.2 is
the takeover **package**, not one rule: besides the takeover and fallback, R6's helper suppresses its own ACK and
listens, R4.2's helper ACKs like any member and cannot hear the others in that feedback occasion. The listening
difference changes the relay cost (R4.2 sends redundant helper copies), not the delivery (section 5b: TBs whose
helper received the first copy are delivered alike).

**Attribution.** Each delivered pair is attributed to the copy that **completed** its decode: `L1` the leader's
first transmission, `Lrep` a later leader copy in its own slot (HARQ retransmission in R3, blind repetition in R1),
`H` a helper copy, `T1` a leader takeover after a helper NACK, `T2` a leader takeover by the fallback (no helper SCI
in the handed slot), `LH` leader and helper in the same slot both decoded (rare). `lost` = not delivered by the PDB.
The shares add up to the PRR (plus `lost` = 1), and the differences between two arms add up to their PRR difference
exactly. **Not counterfactual:** a copy may complete a decode by combining with earlier failed copies, and a
member decoded by one copy might have been reached by a later one; and a takeover can also act **indirectly** (the
helper decodes the leader's takeover copy and then relays it, which is attributed to `H`). The causal effect of an
arm is the paired PRR difference (same traces, seeds and packets; section 2).

**Pairing.** Inside each family (leader only: R1, R1_bsc, R3; helper: R4_bsc, R4.2, R6) every run has identical
packets, and periodic traffic is identical across all arms. Under **aperiodic** traffic the packet arrivals differ
between the two families (known, `paper_notes/round3/CHECKS.md`): R6 vs R3 / R1 / R1_bsc is then paired by seed and
mobility trace but not packet by packet, and each arm's shares use its own pair count. R6 vs R4.2 and R6 vs R4_bsc
are paired packet by packet everywhere.

**Statistics:** seeds are the resampling unit (10 per cell); 95 % percentile bootstrap, paired comparisons resample
the same seed indices in both arms (20 000 replicates, fixed RNG). Binned points need ≥ 5 seeds and ≥ 200 pairs.
"""))
    C.append(code("""import os, sys
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from IPython.display import display, Markdown
sys.path.insert(0, os.path.abspath('.'))
import nobg_decomposition as N        # collector (reads out/remote_v3, caches out/cache_nobg_v3)
import takeover_audit as A            # bootstrap, grid and style helpers
pd.set_option('display.width', 230); pd.set_option('display.max_columns', 40)
FIG = os.path.join(os.path.abspath('.'), 'figures', 'nobg_v3'); os.makedirs(FIG, exist_ok=True)
F = lambda n: os.path.join(FIG, n)
ARMS = ['R1', 'R1_bsc', 'R3', 'R4_bsc', 'R4.2', 'R6']
D = N.collect(ARMS); CAT, WIN, RUN = D['cat'], D['win'], D['run']
A.LABEL |= {'R1_bsc': 'R1_bsc leader blind + C', 'R4_bsc': 'R4_bsc Kuo blind + C (Blind+C)'}
A.STYLE |= {'R1_bsc': ('dimgray', '-.', 1.5), 'R4_bsc': ('tab:purple', '-', 2.0)}
A.MARK |= {'R1_bsc': 'o', 'R4_bsc': 's'}
SRC = ['L1', 'Lrep', 'H', 'T1', 'T2', 'LH', 'lost']
SRC_NAME = {'L1': 'leader 1st copy', 'Lrep': 'leader retx / repetition', 'H': 'helper copy',
            'T1': 'takeover: helper NACK', 'T2': 'takeover: fallback', 'LH': 'leader+helper same slot', 'lost': 'lost'}
SRC_COL = {'L1': '#9e9e9e', 'Lrep': '#4d4d4d', 'H': '#6a3d9a', 'T1': '#1b9e77', 'T2': '#a6dba0', 'LH': '#e6ab02',
           'lost': '#ffffff'}
CELLS = [(sc, tr) for tr in A.TRS for sc in A.SCS]
# completeness: every arm 3 x 2 x 10 runs, one cat/win/run entry per run, identical pairs per run across arms
exp = pd.MultiIndex.from_product([ARMS, A.SCS, A.TRS, range(1, 11)], names=['arm', 'sc', 'tr', 'seed'])
got = pd.MultiIndex.from_frame(RUN[['arm', 'sc', 'tr', 'seed']])
assert got.is_unique and len(exp.difference(got)) == 0 and len(got.difference(exp)) == 0, 'run set incomplete'
pp = RUN.pivot_table(index=['sc', 'tr', 'seed'], columns='arm', values='n_pairs')
FAM = {'leader': ['R1', 'R1_bsc', 'R3'], 'helper': ['R4_bsc', 'R4.2', 'R6']}
for f, arms in FAM.items():   # within a family the packets are identical in every run
    assert (pp[arms].nunique(axis=1) == 1).all(), f'pair counts differ inside the {f} family'
per = pp.xs('periodic1tb', level='tr')
assert (per.nunique(axis=1) == 1).all(), 'periodic: pair counts differ across families'
ap = pp.xs('aperiodic1tb', level='tr')
n_ap = int((ap.nunique(axis=1) > 1).sum())
_c = CAT.groupby(['arm', 'sc', 'tr', 'seed']).n.sum(); _r = RUN.set_index(['arm', 'sc', 'tr', 'seed']).n_pairs
assert len(_c) == len(_r) and (_c - _r.reindex(_c.index)).abs().max() == 0, 'category counts do not add up to the pairs'
print(f'{len(RUN)} runs = {len(ARMS)} arms x 60. Packets identical inside each family in every run and across '
      f'families for periodic traffic; aperiodic: {n_ap} of {len(ap)} runs differ between the leader-only and the '
      f'helper family (known: aperiodic arrivals are not paired across families, paper_notes/round3/CHECKS.md). '
      f'Late decodes (after the PDB): {int(RUN.n_late.sum())}')
"""))

    # ---------------- 1. PRR and decomposition
    C.append(md("""## 1. PRR and its decomposition by delivering copy
Bars: share of all (TB, member) pairs by the copy that completed the decode; the top of the coloured part is the
PRR (white = lost). Table: PRR with seed CI."""))
    C.append(code("""def shares(arms=ARMS):
    t = CAT[CAT.arm.isin(arms)].groupby(['sc', 'tr', 'arm', 'src']).n.sum().unstack('src').reindex(columns=SRC, fill_value=0).fillna(0)
    return t.div(t.sum(1), axis=0)
SH = shares(); SH.to_csv(F('tab_decomposition_shares.csv'))
rows = []
for (sc, tr, arm), g in WIN.groupby(['sc', 'tr', 'arm']):
    r, lo, hi, n = A.boot_ratio(g, 'n_deliv', 'n_pairs')
    rows.append(dict(sc=sc, tr=tr, arm=arm, prr=r, lo=lo, hi=hi, n_seeds=n))
PRR = pd.DataFrame(rows); PRR.to_csv(F('tab_prr.csv'), index=False)
display(PRR.pivot_table(index=['sc', 'tr'], columns='arm', values='prr')[ARMS].round(4))

fig, ax = plt.subplots(2, 3, figsize=(15, 8), sharey=True)
for i, tr in enumerate(A.TRS):
    for j, sc in enumerate(A.SCS):
        a = ax[i, j]; bottom = np.zeros(len(ARMS))
        for s in SRC:
            v = SH.loc[(sc, tr)].reindex(ARMS)[s].values
            a.bar(range(len(ARMS)), v, bottom=bottom, color=SRC_COL[s], edgecolor='k' if s == 'lost' else 'white',
                  lw=.6 if s == 'lost' else 1.5, width=.75)
            bottom += v
        prr = 1 - SH.loc[(sc, tr)].reindex(ARMS).lost.values
        for x, p in enumerate(prr):
            a.text(x, p + .01, f'{p:.3f}', ha='center', va='bottom', fontsize=8)
        a.set_xticks(range(len(ARMS))); a.set_xticklabels(ARMS, fontsize=9)
        a.set_title(f'{A.SCN[sc]}, {A.TRN[tr]}', fontsize=10); a.set_ylim(0, 1.08)
        if j == 0: a.set_ylabel('share of (TB, member) pairs')
fig.suptitle('Where the PRR comes from at 0 background cars: copy that completed each decode (number = PRR)', fontsize=12)
fig.legend(handles=[Patch(facecolor=SRC_COL[s], edgecolor='k' if s == 'lost' else SRC_COL[s], label=SRC_NAME[s]) for s in SRC],
           loc='lower center', ncol=7, fontsize=9, frameon=False)
fig.tight_layout(rect=(0, .05, 1, .96)); fig.savefig(F('fig1_decomposition.pdf'), bbox_inches='tight')
fig.savefig(F('fig1_decomposition.png'), dpi=130, bbox_inches='tight'); plt.show()
display((SH * 100).round(2))
"""))

    # ---------------- 2. paired gain decomposition
    C.append(md("""## 2. Paired gain of R6 and its decomposition
For each comparison R6 − X the PRR gain (same seeds, packets and traces) is split into the change of each
delivering-copy share; the parts add up to the gain exactly (`lost` change = − gain). Intervals: paired bootstrap of
each part with the same seed indices. Reading it:
- `L1` ≈ 0 everywhere: the first copy is the same in every arm (a check of the pairing, not a gain);
- vs **R3**: the leader's retransmissions (`Lrep`, negative) are replaced by helper copies and takeovers;
- vs **R4_bsc (Blind + C)**: same helper selection, blind relaying with combining vs feedback-driven relaying with
  takeover;
- vs **R4.2**: the takeover package (in R6 the named helper also never ACKs and listens to the other members' feedback, so it can cancel redundant relays; in R4.2 it ACKs like any member and, being half duplex, cannot hear the others in that feedback occasion; `platoon_harq_robustness_eval.cc`, ACK suppression installed only for Option 2 with `helperTakeover`). The delivery difference comes from the takeover (section 5b). Its **direct** part is `T1 + T2` (leader takeover copies that
  completed a decode); the change of `H` is its **indirect** part (the helper decodes the leader's takeover copy and
  relays, or relays to members the takeover did not reach), plus chance;
- vs **R1**: everything beyond the blind leader.
Gains are given as absolute (pp), relative (%) and loss reduction (%)."""))
    C.append(code("""rng = np.random.default_rng(A.RNG_SEED)
def seed_counts(arm, sc, tr):
    c = CAT[(CAT.arm == arm) & (CAT.sc == sc) & (CAT.tr == tr)].pivot_table(index='seed', columns='src', values='n',
                                                                            aggfunc='sum', fill_value=0)
    return c.reindex(columns=SRC, fill_value=0).reindex(range(1, 11), fill_value=0).sort_index()
def paired_parts(p, b, sc, tr, B=A.B_PAIRED):
    cp, cb = seed_counts(p, sc, tr), seed_counts(b, sc, tr)
    assert (cp.index == cb.index).all()
    same_pkts = bool((cp.sum(1) == cb.sum(1)).all())
    npv, nbv = cp.sum(1).to_numpy(float), cb.sum(1).to_numpy(float)    # each arm's own pair count per seed
    P, Bm = cp.to_numpy(float), cb.to_numpy(float)
    idx = rng.integers(0, len(npv), (B, len(npv)))                    # same seed indices for both arms
    diff = (P[idx].sum(1) / npv[idx].sum(1)[:, None] - Bm[idx].sum(1) / nbv[idx].sum(1)[:, None]) * 100
    point = (P.sum(0) / npv.sum() - Bm.sum(0) / nbv.sum()) * 100
    lo, hi = np.percentile(diff, [2.5, 97.5], axis=0)
    out = pd.DataFrame(dict(src=SRC, pp=point, lo=lo, hi=hi))
    pb, pbase = 1 - P[:, -1].sum() / npv.sum(), 1 - Bm[:, -1].sum() / nbv.sum()
    gain_bs = -diff[:, -1]
    seeds_better = int(((1 - P[:, -1] / npv) - (1 - Bm[:, -1] / nbv) > 0).sum())
    tot = dict(prr_p=pb, prr_b=pbase, abs_pp=100 * (pb - pbase), abs_lo=np.percentile(gain_bs, 2.5),
               abs_hi=np.percentile(gain_bs, 97.5), rel_pct=100 * (pb / pbase - 1),
               loss_red_pct=100 * (pb - pbase) / (1 - pbase), seeds_better=seeds_better, n_seeds=len(npv),
               packet_paired=same_pkts)
    return out, tot

COMP = ['R3', 'R4_bsc', 'R4.2', 'R1']
parts, tots = [], []
for b in COMP:
    for sc, tr in CELLS:
        o, t = paired_parts('R6', b, sc, tr)
        parts.append(o.assign(baseline=b, sc=sc, tr=tr)); tots.append(dict(baseline=b, sc=sc, tr=tr) | t)
PARTS, TOTS = pd.concat(parts), pd.DataFrame(tots)
PARTS.to_csv(F('tab_gain_parts.csv'), index=False); TOTS.to_csv(F('tab_gain_total.csv'), index=False)
display(Markdown('**Paired PRR gain of R6** (abs in pp with 95 % CI; rel %; loss reduction %; seeds where R6 is better)'))
display(TOTS.set_index(['baseline', 'sc', 'tr'])[['n_seeds', 'packet_paired', 'prr_p', 'prr_b', 'abs_pp', 'abs_lo', 'abs_hi', 'rel_pct',
                                                  'loss_red_pct', 'seeds_better']].round(3))

fig, ax = plt.subplots(2, 3, figsize=(15, 8), sharey=False)
show = ['L1', 'Lrep', 'H', 'T1', 'T2', 'LH']
for i, tr in enumerate(A.TRS):
    for j, sc in enumerate(A.SCS):
        a = ax[i, j]
        for x, b in enumerate(COMP):
            g = PARTS[(PARTS.baseline == b) & (PARTS.sc == sc) & (PARTS.tr == tr)].set_index('src')
            pos, neg = 0.0, 0.0
            for s in show:
                v = g.pp[s]
                if v >= 0: a.bar(x, v, bottom=pos, color=SRC_COL[s], edgecolor='white', width=.6); pos += v
                else: a.bar(x, v, bottom=neg, color=SRC_COL[s], edgecolor='white', width=.6, hatch='//'); neg += v
            t = TOTS[(TOTS.baseline == b) & (TOTS.sc == sc) & (TOTS.tr == tr)].iloc[0]
            a.errorbar(x + .36, t.abs_pp, yerr=[[t.abs_pp - t.abs_lo], [t.abs_hi - t.abs_pp]], fmt='D', color='k', ms=5, capsize=3)
        a.axhline(0, color='k', lw=.8); a.set_xticks(range(len(COMP))); a.set_xticklabels([f'R6−{b}' for b in COMP], fontsize=9)
        a.set_title(f'{A.SCN[sc]}, {A.TRN[tr]}', fontsize=10); a.grid(axis='y', alpha=.3)
        if j == 0: a.set_ylabel('PRR change by delivering copy (pp)')
fig.suptitle('Paired PRR gain of R6 split by the copy that completed the decode (0 cars; ◆ = net gain, 95 % CI; hatched = copy kind delivers a smaller share in R6 than in the baseline)', fontsize=12)
fig.legend(handles=[Patch(facecolor=SRC_COL[s], label=SRC_NAME[s]) for s in show] +
           [Line2D([], [], color='k', marker='D', ls='', label='net PRR gain')], loc='lower center', ncol=7, fontsize=9, frameon=False)
fig.tight_layout(rect=(0, .05, 1, .96)); fig.savefig(F('fig2_gain_parts.pdf'), bbox_inches='tight')
fig.savefig(F('fig2_gain_parts.png'), dpi=130, bbox_inches='tight'); plt.show()
display(PARTS.pivot_table(index=['baseline', 'sc', 'tr'], columns='src', values='pp')[SRC].round(2))
"""))
    C.append(md("""**Direct vs indirect takeover (R6 − R4.2).** `direct` = T1 + T2 (leader takeover copies that completed a
decode), `indirect` = change of the helper share, `rest` = L1 + LH (≈ 0). They add up to the net gain."""))
    C.append(code("""g = PARTS[PARTS.baseline == 'R4.2'].pivot_table(index=['sc', 'tr'], columns='src', values='pp')
TK = pd.DataFrame({'direct_T1': g.T1, 'direct_T2': g.T2, 'indirect_H': g.H, 'rest_L1_LH': g.L1 + g.LH,
                   'net_gain': -g.lost})
TK['indirect_share_pct'] = 100 * TK.indirect_H / TK.net_gain
TK.to_csv(F('tab_takeover_direct_indirect.csv')); display(TK.round(2))
"""))

    # ---------------- 3. recovery after a failed first copy
    C.append(md("""## 3. What each scheme recovers after a failed first copy
Pairs whose leader first copy failed, split by how: the member **missed the SCI** (no decode attempt) or **decoded
and failed**. Share of those pairs delivered in the end (seed CI). This is where the arms differ: the first copy is
the same for all of them."""))
    C.append(code("""rows = []
for (sc, tr, arm, st1), g in CAT[CAT.st1.isin([0, 2])].groupby(['sc', 'tr', 'arm', 'st1']):
    g = g.assign(d=np.where(g.src != 'lost', g.n, 0))
    r, lo, hi, n = A.boot_ratio(g, 'd', 'n')
    rows.append(dict(sc=sc, tr=tr, arm=arm, first='SCI missed' if st1 == 0 else 'decode failed', rec=r, lo=lo, hi=hi,
                     pairs=int(g.n.sum()), n_seeds=n))
REC = pd.DataFrame(rows); REC.to_csv(F('tab_recovery_after_first_fail.csv'), index=False)
fs = CAT[(CAT.st1 >= 0)].groupby(['sc', 'tr', 'st1']).n.sum().unstack('st1')
display(Markdown('First-copy outcome, share of pairs (%), all arms pooled (0 = SCI missed, 1 = decoded, 2 = decode failed)'))
display((fs.div(fs.sum(1), axis=0) * 100).round(1))
fig, ax = plt.subplots(2, 3, figsize=(15, 7.5), sharey=True)
w = .13
for i, tr in enumerate(A.TRS):
    for j, sc in enumerate(A.SCS):
        a = ax[i, j]
        for k, arm in enumerate(ARMS):
            g = REC[(REC.sc == sc) & (REC.tr == tr) & (REC.arm == arm)].set_index('first').reindex(['SCI missed', 'decode failed'])
            x = np.arange(2) + (k - (len(ARMS) - 1) / 2) * w
            a.bar(x, g.rec, w, yerr=[g.rec - g.lo, g.hi - g.rec], color=A.STYLE[arm][0], capsize=1.5, error_kw=dict(lw=.7),
                  edgecolor='white', hatch={'-': '', '--': '///', ':': '...', '-.': 'xx'}[A.STYLE[arm][1]])
        a.set_xticks([0, 1]); a.set_xticklabels(['first copy: SCI missed', 'first copy: decode failed'])
        a.set_title(f'{A.SCN[sc]}, {A.TRN[tr]}', fontsize=10); a.set_ylim(0, 1.02); a.grid(axis='y', alpha=.3)
        if j == 0: a.set_ylabel('share recovered by the PDB')
fig.suptitle('Recovery of pairs whose leader first copy failed (0 cars)', fontsize=12)
A.finish(fig, [Patch(facecolor=A.STYLE[a][0], hatch={'-': '', '--': '///', ':': '...', '-.': 'xx'}[A.STYLE[a][1]],
                     edgecolor='white', label=A.LABEL[a]) for a in ARMS], F('fig3_recovery_after_first_fail'), ncol=6)
plt.show()
display(REC.pivot_table(index=['sc', 'tr', 'first'], columns='arm', values='rec')[ARMS].round(3))
"""))

    # ---------------- 4. per member
    C.append(md("""## 4. PRR per member position
Member 1 follows the leader, member 8 is the tail. Shows where in the platoon each scheme gains."""))
    C.append(code("""PM = CAT.assign(d=np.where(CAT.src != 'lost', CAT.n, 0)).groupby(['sc', 'tr', 'arm', 'member'])[['d', 'n']].sum()
PM['prr'] = PM.d / PM.n; PM = PM.reset_index(); PM.to_csv(F('tab_prr_per_member.csv'), index=False)
fig, ax = A.grid('PRR per member position at 0 background cars (pooled over seeds)', 'PRR', 'member (1 = behind leader, 8 = tail)')
for i, tr in enumerate(A.TRS):
    for j, sc in enumerate(A.SCS):
        for arm in ARMS:
            g = PM[(PM.sc == sc) & (PM.tr == tr) & (PM.arm == arm)]
            col, ls, lw = A.STYLE[arm]; ax[i, j].plot(g.member, g.prr, ls=ls, color=col, lw=lw, marker=A.MARK[arm], ms=4)
        ax[i, j].set_xticks(range(1, 9))
A.finish(fig, A.arm_handles(ARMS), F('fig4_prr_per_member')); plt.show()
display(PM.pivot_table(index=['sc', 'tr', 'member'], columns='arm', values='prr')[ARMS].round(3))
"""))

    # ---------------- 5. helper reception
    C.append(md("""## 5. Helper reception of the first copy and its influence on PRR
**Helper reception probability** = share of TBs whose selected helper (Kuo, chosen per TB) decoded the leader's first
transmission, i.e. could relay in its first handed slot. Leader-only arms have no helper."""))
    C.append(code("""rows = []
for (sc, tr, arm), g in WIN[WIN.arm.isin(['R4_bsc', 'R4.2', 'R6'])].groupby(['sc', 'tr', 'arm']):
    r, lo, hi, n = A.boot_ratio(g, 'n_helper_ok', 'n_helper')
    rows.append(dict(sc=sc, tr=tr, arm=arm, helper_reception=r, lo=lo, hi=hi, tbs=int(g.n_helper.sum()), n_seeds=n))
HR = pd.DataFrame(rows); HR.to_csv(F('tab_helper_reception.csv'), index=False)
display(HR.pivot_table(index=['sc', 'tr'], columns='arm', values=['helper_reception', 'lo', 'hi']).round(3))
"""))
    C.append(md("""**Per TB (descriptive):** PRR of the TBs whose helper received the first copy vs those whose helper did not,
over the **other seven members** (the helper's own reception is the condition, so it is left out) and over all
eight. The two groups of TBs differ in more than the helper (a TB whose helper failed often has a bad channel for
its neighbours too), so the gap is an association, not the effect of the helper alone."""))
    C.append(code("""rows = []
for (sc, tr, arm), g in CAT[(CAT.helper_ok >= 0) & CAT.arm.isin(['R4_bsc', 'R4.2', 'R6'])].groupby(['sc', 'tr', 'arm']):
    g = g.assign(d=np.where(g.src != 'lost', g.n, 0))
    row = dict(sc=sc, tr=tr, arm=arm)
    for ok, nm in ((1, 'helper_rx'), (0, 'helper_missed')):
        for scope, gg in (('others', g[(g.helper_ok == ok) & (g.is_helper == 0)]), ('all8', g[g.helper_ok == ok])):
            r, lo, hi, n = A.boot_ratio(gg, 'd', 'n')
            row |= {f'{nm}_{scope}': r, f'{nm}_{scope}_lo': lo, f'{nm}_{scope}_hi': hi}
        row[f'pairs_{nm}'] = int(g[g.helper_ok == ok].n.sum())
    rows.append(row)
CH = pd.DataFrame(rows); CH.to_csv(F('tab_prr_given_helper_reception.csv'), index=False)
display(CH.set_index(['sc', 'tr', 'arm'])[['pairs_helper_rx', 'helper_rx_others', 'pairs_helper_missed', 'helper_missed_others',
                                          'helper_missed_others_lo', 'helper_missed_others_hi', 'helper_rx_all8',
                                          'helper_missed_all8']].round(3))
"""))
    C.append(md("""**PRR versus helper reception probability (descriptive association).** Each run is cut into 2 s windows
(by packet generation time); x = share of TBs in the window whose helper received the first copy, windows binned,
each point at the TB-weighted mean x of its bin; y = PRR of the pairs in those windows. Cooperative arms use their
own helper; the leader-only references (hollow) are placed at R6's helper reception in the same window (same trace
and seed), showing what the leader alone achieves in the same conditions. Top figure: all 8 members (the PRR you
measure); bottom figure: the 7 non-helper members only (removes the mechanical part: the helper's own reception is
x). The helper reception varies along the trace with the geometry, so x also tracks how hard the channel is for
everyone: this is not the effect of the helper reception alone. Points need ≥ 5 seeds and ≥ 200 pairs; counts in the
CSV."""))
    C.append(code("""HR_EDGES = [0, .5, .7, .8, .9, .95, 1.0001]
ref = WIN[WIN.arm == 'R6'].set_index(['sc', 'tr', 'seed', 'w'])[['n_helper_ok', 'n_helper']]
xs = []
for arm in ['R1', 'R3', 'R4_bsc', 'R4.2', 'R6']:
    x = WIN[WIN.arm == arm].copy()
    if arm in ('R4_bsc', 'R4.2', 'R6'):
        x['hf'] = x.n_helper_ok / x.n_helper.replace(0, np.nan)
    else:
        r = ref.reindex(pd.MultiIndex.from_frame(x[['sc', 'tr', 'seed', 'w']]))
        x['hf'] = (r.n_helper_ok / r.n_helper.replace(0, np.nan)).values
    xs.append(x.dropna(subset=['hf']))
XW = pd.concat(xs); XW['hbin'] = pd.cut(XW.hf, HR_EDGES, right=False, labels=False)
HB = list(range(len(HR_EDGES) - 1))
def helper_curve(num, den, name, title):
    P = A.binned_points(XW, ['sc', 'tr', 'arm'], num, den, A.MIN_PAIRS, count_cols=('n_pairs', 'n_helper'))
    P.to_csv(F(name + '_points.csv'), index=False)
    fig, ax = A.grid(title, 'PRR of the window', 'helper reception probability of the first copy (2 s windows)', sharey=False)
    for i, tr in enumerate(A.TRS):
        for j, sc in enumerate(A.SCS):
            a = ax[i, j]
            for arm in ['R1', 'R3', 'R4_bsc', 'R4.2', 'R6']:
                p = A.full_bins(P[(P.sc == sc) & (P.tr == tr) & (P.arm == arm)], 'hbin', HB)
                col, ls, lw = A.STYLE[arm]
                sup = p.supported.fillna(False).astype(bool).values
                y = np.where(sup, p.y, np.nan)
                for seg in A.segments(p.x.values, y):
                    xx, yy = zip(*seg); a.plot(xx, yy, ls=ls, color=col, lw=lw, marker=A.MARK[arm], ms=5,
                                               mfc='white' if arm in ('R1', 'R3') else col)
                A.band(a, p.x.values, p.lo.values, p.hi.values, sup, col)
            a.set_xlim(.3, 1.0)
    A.finish(fig, A.arm_handles(['R1', 'R3', 'R4_bsc', 'R4.2', 'R6']) +
             [Line2D([], [], color='k', marker='o', mfc='white', ls='', label="hollow: leader only, at R6's helper reception")],
             F(name))
    plt.show()
    un = P[~P.supported]
    display(Markdown(f'{len(P)} points, {len(un)} unsupported (not drawn).'))
    return P
P5a = helper_curve('n_deliv', 'n_pairs', 'fig5a_prr_vs_helper_reception_all8',
                   'PRR (all 8 members) vs helper reception of the first copy, 0 cars (descriptive)')
P5b = helper_curve('n_deliv_nh', 'n_pairs_nh', 'fig5b_prr_vs_helper_reception_others',
                   'PRR of the 7 non-helper members vs helper reception of the first copy, 0 cars (descriptive)')
"""))
    C.append(md("""**Distribution of the helper reception** over the windows (R6): how much of the time each bin covers, so the
curves are read with their weight."""))
    C.append(code("""x = XW[XW.arm == 'R6']
t = x.groupby(['sc', 'tr', 'hbin']).n_pkt.sum().unstack('hbin').fillna(0)
t.columns = [f'[{HR_EDGES[int(c)]:.2f}, {min(HR_EDGES[int(c) + 1], 1):.2f})' for c in t.columns]
display((t.div(t.sum(1), axis=0) * 100).round(1))
"""))

    # ---------------- 5b. helper received vs missed
    C.append(md("""## 5b. Where the takeover acts: TBs whose helper received vs missed the first copy
Every pair of the cooperative arms is put in one of two groups by **its own arm's** helper outcome for that TB:
*received* (the TB's helper decoded the leader's first copy) or *missed*. The groups are not the same TBs in every
arm (the helper of a TB can succeed in one arm and fail in another by chance), but they are of similar size (5–15 %
of pairs missed), and every quantity below adds up exactly to the arm totals of sections 1–2.

**(a) Delivering copy by group**, in % of **all** pairs of the arm. In the received group the three arms should and
do behave the same (R6 never takes over there); everything that separates them sits in the missed group."""))
    C.append(code("""COOP = ['R4_bsc', 'R4.2', 'R6']
CC = CAT[CAT.arm.isin(COOP) & (CAT.helper_ok >= 0)]
TOT = CC.groupby(['sc', 'tr', 'arm']).n.sum()
G = CC.assign(group=np.where(CC.helper_ok == 1, 'received', 'missed'))
SG = G.groupby(['sc', 'tr', 'group', 'arm', 'src']).n.sum().unstack('src').reindex(columns=['L1', 'H', 'T1', 'T2', 'LH', 'lost'],
                                                                                  fill_value=0).fillna(0)
SG = SG.div(TOT.reindex(SG.index.droplevel('group')).values, axis=0) * 100
SG.to_csv(F('tab_5b_shares_by_helper_outcome.csv'))
for grp in ('received', 'missed'):
    display(Markdown(f'**Helper {grp}**: % of all pairs')); display(SG.xs(grp, level='group').round(2))

fig, ax = plt.subplots(2, 3, figsize=(15, 8), sharey=False)
show = ['L1', 'H', 'T1', 'T2', 'LH', 'lost']
for i, tr in enumerate(A.TRS):
    for j, sc in enumerate(A.SCS):
        a = ax[i, j]; xt, xl = [], []
        for gi, grp in enumerate(('received', 'missed')):
            for k, arm in enumerate(COOP):
                x = gi * (len(COOP) + 1) + k; bottom = 0.0
                v = SG.loc[(sc, tr, grp, arm)]
                for s in show:
                    a.bar(x, v[s], bottom=bottom, color=SRC_COL[s], edgecolor='k' if s == 'lost' else 'white',
                          lw=.6 if s == 'lost' else 1.2, width=.8)
                    bottom += v[s]
                xt.append(x); xl.append(arm)
        a.set_xticks(xt); a.set_xticklabels(xl, fontsize=8, rotation=0)
        a.set_title(f'{A.SCN[sc]}, {A.TRN[tr]}\\nleft: helper received   right: helper missed', fontsize=9)
        a.grid(axis='y', alpha=.3)
        if j == 0: a.set_ylabel('% of all pairs of the arm')
fig.suptitle('Delivering copy in TBs whose helper received / missed the first copy (0 cars; bar height = group size)', fontsize=12)
fig.legend(handles=[Patch(facecolor=SRC_COL[s], edgecolor='k' if s == 'lost' else SRC_COL[s], label=SRC_NAME[s]) for s in show],
           loc='lower center', ncol=6, fontsize=9, frameon=False)
fig.tight_layout(rect=(0, .05, 1, .95)); fig.savefig(F('fig5b_shares_by_helper_outcome.pdf'), bbox_inches='tight')
fig.savefig(F('fig5b_shares_by_helper_outcome.png'), dpi=130, bbox_inches='tight'); plt.show()
"""))
    C.append(md("""**(b) Paired gain split by group.** Net PRR gain of R6 over R4.2 (takeover package) and over R4_bsc (Blind + C) =
part from the received group + part from the missed group, each = (delivered pairs of the group in R6 − in the
baseline) / all pairs, in pp. Same seed indices for both arms (paired bootstrap, 20 000 replicates); packets are
identical within the helper family, so the denominators are equal."""))
    C.append(code("""def group_split(p, b, sc, tr, B=A.B_PAIRED):
    def per_seed(arm):
        g = G[(G.arm == arm) & (G.sc == sc) & (G.tr == tr)]
        d = g[g.src != 'lost'].pivot_table(index='seed', columns='group', values='n', aggfunc='sum', fill_value=0)
        d = d.reindex(columns=['received', 'missed'], fill_value=0).reindex(range(1, 11), fill_value=0)
        n = g.groupby('seed').n.sum().reindex(range(1, 11), fill_value=0)
        return d.to_numpy(float), n.to_numpy(float)
    Dp, Np = per_seed(p); Db, Nb = per_seed(b)
    assert (Np == Nb).all(), 'pair counts differ inside the helper family'
    idx = rng.integers(0, len(Np), (B, len(Np)))
    bs = (Dp[idx].sum(1) - Db[idx].sum(1)) / Np[idx].sum(1)[:, None] * 100
    pt = (Dp.sum(0) - Db.sum(0)) / Np.sum() * 100
    lo, hi = np.percentile(bs, [2.5, 97.5], axis=0)
    net = bs.sum(1)
    return dict(from_received=pt[0], rec_lo=lo[0], rec_hi=hi[0], from_missed=pt[1], mis_lo=lo[1], mis_hi=hi[1],
                net=pt.sum(), net_lo=np.percentile(net, 2.5), net_hi=np.percentile(net, 97.5))
rows = [dict(comparison=f'R6 − {b}', sc=sc, tr=tr) | group_split('R6', b, sc, tr) for b in ('R4.2', 'R4_bsc') for sc, tr in CELLS]
GS = pd.DataFrame(rows); GS.to_csv(F('tab_5b_gain_by_helper_outcome.csv'), index=False)
display(GS.set_index(['comparison', 'sc', 'tr']).round(2))
"""))
    C.append(md("""**(c) How much of the recoverable loss the takeover gets.** Within the missed group: the PRR of each arm, the
received group's PRR as a reference (what a working helper achieves for comparable members), the loss the
baseline leaves in the missed group (`ceiling_pp`, % of all pairs: the most any fix inside that group could add),
the part R6 recovers over R4.2 (`recovered_pp`) and its share of the ceiling."""))
    C.append(code("""PG_ = G.assign(d=np.where(G.src != 'lost', G.n, 0)).groupby(['sc', 'tr', 'arm', 'group'])[['d', 'n']].sum()
PG_['prr'] = PG_.d / PG_.n
W5 = PG_.prr.unstack(['arm', 'group'])
CE = pd.DataFrame({'missed_pair_share_R6': PG_.xs(('R6', 'missed'), level=('arm', 'group')).n / TOT.xs('R6', level='arm'),
                   'prr_missed_R4_bsc': W5[('R4_bsc', 'missed')], 'prr_missed_R4.2': W5[('R4.2', 'missed')],
                   'prr_missed_R6': W5[('R6', 'missed')], 'prr_received_R6': W5[('R6', 'received')]})
lost_b = G[(G.arm == 'R4.2') & (G.group == 'missed') & (G.src == 'lost')].groupby(['sc', 'tr']).n.sum()
CE['ceiling_pp'] = 100 * lost_b / TOT.xs('R4.2', level='arm')
CE['recovered_pp'] = GS[GS.comparison == 'R6 − R4.2'].set_index(['sc', 'tr']).from_missed
CE['captured_pct'] = 100 * CE.recovered_pp / CE.ceiling_pp
CE.to_csv(F('tab_5b_takeover_ceiling.csv')); display(CE.round(3))
"""))
    C.append(md("""**(d) Where R6 still loses.** Share of R6's lost pairs in TBs whose helper received / missed the first copy.
Losses in the received group cannot be addressed by a takeover (it never triggers there): those members are
reached neither by the leader nor by the chosen helper."""))
    C.append(code("""L6 = G[(G.arm == 'R6') & (G.src == 'lost')].groupby(['sc', 'tr', 'group']).n.sum().unstack('group')
L6 = L6.reindex(columns=['received', 'missed'], fill_value=0)
out = (L6.div(L6.sum(1), axis=0) * 100).rename(columns=lambda c: f'% of R6 losses, helper {c}')
out['R6 residual pair failure (%)'] = 100 * L6.sum(1) / TOT.xs('R6', level='arm')
out.to_csv(F('tab_5b_r6_losses_by_helper_outcome.csv')); display(out.round(1))
"""))

    # ---------------- 6. cost
    C.append(md("""## 6. Cost at 0 cars
Transmissions per TB by kind, transmitted subchannel-slots per TB (PSSCH; PSFCH excluded) and the share of copies
sent after all 8 members already had the TB (redundant). Efficiency = delivered pairs / 8 per transmitted
subchannel-slot."""))
    C.append(code("""g = RUN.groupby(['sc', 'tr', 'arm']).sum(numeric_only=True)
K = ['L1', 'Lrep', 'H', 'T1', 'T2']
COST = pd.DataFrame({f'tx_{k}_per_tb': g[f'tx_{k}'] / g.n_pkt for k in K})
COST['tx_per_tb'] = sum(g[f'tx_{k}'] for k in K) / g.n_pkt
COST['ss_tx_per_tb'] = g.ss_tx / g.n_pkt
COST['redundant_pct'] = 100 * sum(g[f'red_{k}'] for k in K) / sum(g[f'tx_{k}'] for k in K)
dl = CAT.assign(d=np.where(CAT.src != 'lost', CAT.n, 0)).groupby(['sc', 'tr', 'arm']).d.sum()
COST['eff_tx'] = dl / 8 / g.ss_tx
COST = COST.reset_index(); COST = COST[COST.arm.isin(ARMS)]; COST.to_csv(F('tab_cost.csv'), index=False)
display(COST.set_index(['sc', 'tr', 'arm']).round(3))
"""))
    return C


def main():
    nb = nbf.v4.new_notebook()
    nb.cells = cells()
    nb.metadata['kernelspec'] = {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}
    nbf.write(nb_layout.add_preamble(nb), nb_layout.notebook_path('NOBG_DECOMPOSITION_v3.ipynb'))
    print('wrote NOBG_DECOMPOSITION_v3.ipynb', len(nb.cells), 'cells')


if __name__ == '__main__':
    main()
