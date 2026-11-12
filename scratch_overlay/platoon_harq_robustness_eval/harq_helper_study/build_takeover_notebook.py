#!/usr/bin/env python3
"""Build TAKEOVER_RESULTS_v3.ipynb: reliability vs channel quality, reliability / resource efficiency / takeover
recovery vs helper failure probability, and latency, per scenario x traffic, on campaign v3 (out/remote_v3).
Per-run tables: takeover_data.py (cached in out/cache_takeover_v3).

  python3 build_takeover_notebook.py [--out-dir out/remote_v3] [--output TAKEOVER_RESULTS_v3.ipynb]
  python3 -m jupyter nbconvert --to notebook --execute --inplace TAKEOVER_RESULTS_v3.ipynb
"""
import argparse, os
import nbformat as nbf

HERE = os.path.dirname(os.path.abspath(__file__))
md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell


def cells(out_dir):
    C = []
    C.append(md(r"""# Takeover results (campaign v3)

**Data: `out/remote_v3`**, campaign v3 (commit 32e29dd, 3000 runs, 10 seeds): SCI 1-A on every PSSCH, leader
aperiodic traffic on dynamic grants, every arm at 0 / 15 / 30 / 45 / 60 background cars. One figure grid per
question: rows = traffic (periodic1tb, aperiodic1tb), columns = scenario (highway, roundabout W, roundabout E).
Error bars / bands: 95 % bootstrap CI over seeds (seeds resampled, ratio of pooled counts).

**Unit**: a (TB, member) pair, inside the run's KPI window. Each leader packet is one TB (the `*1tb` classes).
*Delivered* = the member decoded the TB within the PDB (100 ms periodic, 50 ms aperiodic); this equals `_pkt.csv`
`rx` for every pair. *TB delivery probability* = delivered pairs / pairs (= group PRR); *residual BLER* = 1 − that.

| quantity | definition |
|---|---|
| channel quality (Fig. 1) | SINR of the leader's **first** transmission of the TB at that member (`_att.csv`). A member that missed the SCI 1-A of that copy has no SINR: separate point "SCI missed". Pooled over loads. |
| helper failure (Fig. 2–4) | the TB's helper (named in SCI-2A, `_hsel.csv`) did not decode the leader's first transmission, so it cannot send its assigned retransmission. Probability = share of TBs in a **2 s window** of one run; the windows are binned. Leader-only arms (R1, R2, R3) have no helper: they are placed at the helper failure rate R6 measured in the same scenario, load, seed and window (same trace), drawn with hollow markers. |
| resource efficiency (Fig. 3) | delivered pairs / 8 (TB-equivalents delivered to the group) per subchannel-slot the leader **reserved** (`_grants.csv`, SB-SPS prorated per period; the helper relays in the leader's handed slots, so this is the platoon's whole allocation). Fig. 3b: per subchannel-slot **transmitted** (leader + helper). PSFCH resources are not counted here (`paper_data.cost_metrics`). |
| takeover attempt (Fig. 4) | one leader transmission in a slot it had handed to the helper (`_ltx.csv origin`): **helper NACK** (origin 1) or **fallback**: no helper SCI detected in the handed slot (origin 2). R5 (Option 1) and R6 (Option 2) only. |
| recovery | an attempt *recovers* the TB if at least one member that lacked the TB decodes it at that transmission (its first decode, on the leader's copy). $P_\mathrm{recovery}$ = recovering attempts / attempts. Also: member rescue rate = members rescued / members lacking at the attempt; attempts with no member lacking (fallback false alarm) count as attempts. |
| takeover fraction | TBs with at least one takeover attempt / TBs. |
| latency | generation → first decode at the member (`_pkt.csv delay_ms`, histogram 0.25 ms). CDF counts lost pairs as never delivered (curve ends at the delivery probability); p95 is over delivered pairs. |
"""))
    C.append(code(f"""import os, sys
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from IPython.display import display, Markdown
HERE = os.path.abspath('.')
sys.path.insert(0, HERE)
import takeover_data as TD
OUT = os.environ.get('STUDY_OUT', {out_dir!r})
CACHE = os.path.join(HERE, 'out', 'cache_takeover_v3')
FIG = os.path.join(HERE, 'figures', 'takeover_v3'); os.makedirs(FIG, exist_ok=True)
pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)
D = TD.collect(OUT, CACHE, jobs=4)
SIN, WIN, LAT = D['sinr'], D['win'], D['lat']
NMEM = TD.NMEM

# --- selection: change here
LOADS = [0, 15, 30, 45, 60]                              # loads pooled in Fig. 1-4
ARMS_ALL = ['R1', 'R2', 'R3', 'R4', 'R4.1', 'R4.2', 'R5', 'R6']
ARMS_MAIN = ['R1', 'R3', 'R4', 'R4.2', 'R5', 'R6']      # Fig. 1, latency
HELPER_ARMS = ['R4', 'R4.1', 'R4.2', 'R5', 'R6']
LEADER_REF = ['R1', 'R3']                                # leader-only references in Fig. 2 / 3 (x from R6)
TO_ARMS = ['R5', 'R6']
HF_EDGES = [0, .1, .2, .3, .4, .5, .6, .8, 1.0001]
MIN_PAIRS = 200                                          # a bin is drawn only with >= this many pairs (pooled)

SCS = ['hwy', 'rbt', 'rbtE']; TRS = ['periodic1tb', 'aperiodic1tb']
SCN = {{'hwy': 'Highway', 'rbt': 'Roundabout W (curve)', 'rbtE': 'Roundabout E (curve)'}}
TRN = {{'periodic1tb': 'periodic (100 ms)', 'aperiodic1tb': 'aperiodic'}}
LABEL = {{'R1': 'R1 leader blind', 'R2': 'R2 leader Opt1', 'R3': 'R3 leader Opt2',
         'R4': 'R4 Kuo blind', 'R4.1': 'R4.1 Kuo+Opt1', 'R4.2': 'R4.2 Kuo+Opt2',
         'R5': 'R5 Kuo+Opt1+takeover', 'R6': 'R6 Kuo+Opt2+takeover'}}
STYLE = {{'R1': ('black', '-', 1.5), 'R2': ('gray', '--', 1.5), 'R3': ('silver', ':', 1.5),
         'R4': ('tab:blue', '-', 2.0), 'R4.1': ('tab:blue', '--', 1.5), 'R4.2': ('tab:blue', ':', 1.5),
         'R5': ('tab:red', '-', 2.5), 'R6': ('tab:green', '-', 2.5)}}
MARK = {{'R1': 'o', 'R2': 'o', 'R3': 'o', 'R4': 's', 'R4.1': 's', 'R4.2': 's', 'R5': '^', 'R6': '^'}}
RNG = np.random.default_rng(1)
B = 1000


def boot_ratio(g, num, den):
    \"\"\"pooled ratio sum(num)/sum(den) of frame g and its 95 % CI, resampling seeds.\"\"\"
    s = g.groupby('seed')[[num, den]].sum()
    N, Dn = s[num].values.astype(float), s[den].values.astype(float)
    r = N.sum() / Dn.sum() if Dn.sum() > 0 else np.nan
    if len(s) < 2 or Dn.sum() == 0:
        return r, np.nan, np.nan
    idx = RNG.integers(0, len(s), (B, len(s)))
    with np.errstate(invalid='ignore', divide='ignore'):
        bs = N[idx].sum(1) / Dn[idx].sum(1)
    lo, hi = np.nanpercentile(bs, [2.5, 97.5])
    return r, lo, hi


def grid(title, ylabel, xlabel, sharey=True, figsize=(15, 7.5)):
    fig, ax = plt.subplots(2, 3, figsize=figsize, sharey=sharey, squeeze=False)
    for i, tr in enumerate(TRS):
        for j, sc in enumerate(SCS):
            a = ax[i, j]; a.grid(alpha=.3, lw=.5)
            a.set_title(f'{{SCN[sc]}}, {{TRN[tr]}}', fontsize=10)
            if j == 0: a.set_ylabel(ylabel)
            if i == 1: a.set_xlabel(xlabel)
    fig.suptitle(title, fontsize=12)
    return fig, ax


def legend(fig, arms, extra=()):
    h = [Line2D([], [], color=STYLE[a][0], ls=STYLE[a][1], lw=STYLE[a][2], marker=MARK[a], label=LABEL[a])
         for a in arms] + list(extra)
    fig.legend(handles=h, loc='lower center', ncol=min(len(h), 5), fontsize=9, frameon=False)
    fig.tight_layout(rect=(0, .07, 1, .96))


def save(fig, name):
    fig.savefig(os.path.join(FIG, name + '.pdf'), bbox_inches='tight')
    fig.savefig(os.path.join(FIG, name + '.png'), dpi=130, bbox_inches='tight')
"""))
    C.append(md("## Data check\nRuns per arm and cell, and the group PRR of these tables against `paper_data.collect_folder` "
                "(`paper_v2/analysis/cache_v3`, mean of the member PRRs): must agree to rounding."))
    C.append(code("""runs = WIN.drop_duplicates(['arm', 'sc', 'tr', 'nbg', 'seed'])
display(runs.groupby(['arm', 'sc', 'tr']).size().unstack(['sc', 'tr']))
g = WIN.groupby(['arm', 'sc', 'tr', 'nbg', 'seed'])[['n_deliv', 'n_pairs', 'n_notx']].sum()
g['prr'] = g.n_deliv / g.n_pairs
ref = []
for f in sorted(os.listdir(os.path.join(HERE, 'paper_v2', 'analysis', 'cache_v3'))):
    if f.startswith('remote_v3__') and f.endswith('_runs.csv'):
        ref.append(pd.read_csv(os.path.join(HERE, 'paper_v2', 'analysis', 'cache_v3', f)))
ref = pd.concat(ref).set_index(['arm', 'sc', 'tr', 'nbg', 'seed']).prr
j = g.join(ref.rename('prr_ref'), how='inner')
print(f'{len(j)} runs compared, max |prr - prr_ref| = {(j.prr - j.prr_ref).abs().max():.2e}; '
      f'packets never transmitted: {int(g.n_notx.sum())} of {int(WIN.n_pkt.sum())}')
"""))

    # ---------------- Fig 1
    C.append(md("""## Figure 1 — Residual BLER versus channel quality
x: SINR of the leader's first transmission of the TB at the member (2 dB bins, edges clipped at −12 and 40 dB);
leftmost point: the member missed the SCI 1-A of the first transmission (no SINR). y: residual BLER after every
allowed retransmission and relay (pair not delivered within the PDB), log scale; bins with no loss are not drawn.
Pooled over the loads in `LOADS`. The first transmission is the leader's in every arm, so the x-axis is the same
link for every scheme.

The floor of R4 / R4.2 at high SINR (~1e-2) is real: in every arm ~3.5 % of first copies above 25 dB still fail
(`_att.csv` status 2: a failed SCI-2A or interference on part of the band, which the RB-averaged SINR hides; e.g.
highway aperiodic 60 cars, seeds 1-4: R4 107 of 3034, R3 85 of 3281). Leader HARQ and the takeover arms
retransmit; R4 / R4.2 depend on the single helper copy, and when the helper also missed the TB nothing follows
(never decoded: R4 98, R4.2 71, R6 12, R3 3 of those pairs)."""))
    C.append(code("""XMISS = TD.SINR_LO - 5
def fig1(loads=LOADS, arms=ARMS_MAIN, name='fig1_bler_vs_sinr', hfail=None):
    s = SIN[SIN.nbg.isin(loads) & SIN.arm.isin(arms)]
    if hfail is not None:
        s = s[s.hfail == hfail]
    fig, ax = grid(f'Residual BLER vs leader-member SINR of the first transmission (loads {loads})',
                   'residual BLER', 'first-transmission SINR (dB)')
    rows = []
    for i, tr in enumerate(TRS):
        for j, sc in enumerate(SCS):
            a = ax[i, j]
            for arm in arms:
                g = s[(s.sc == sc) & (s.tr == tr) & (s.arm == arm)].assign(n_lost=lambda x: x.n_pairs - x.n_deliv)
                pts = []
                for b, gb in g.groupby('bin'):
                    if gb.n_pairs.sum() < MIN_PAIRS: continue
                    r, lo, hi = boot_ratio(gb, 'n_lost', 'n_pairs')
                    x = XMISS if b == TD.SINR_MISS else b + TD.SINR_STEP / 2
                    pts.append((x, r, lo, hi, gb.n_pairs.sum()))
                    rows.append(dict(sc=sc, tr=tr, arm=arm, bin=b, bler=r, lo=lo, hi=hi, pairs=gb.n_pairs.sum()))
                if not pts: continue
                p = pd.DataFrame(pts, columns=['x', 'r', 'lo', 'hi', 'n']).sort_values('x')
                p = p[p.r > 0]
                col, ls, lw = STYLE[arm]
                m = p.x > XMISS
                a.plot(p.x[m], p.r[m], ls=ls, color=col, lw=lw, marker=MARK[arm], ms=4)
                a.fill_between(p.x[m], p.lo[m].clip(lower=1e-5), p.hi[m], color=col, alpha=.12, lw=0)
                mm = p[~m]
                if len(mm):
                    a.errorbar(mm.x.values, mm.r.values, yerr=[(mm.r - mm.lo).values, (mm.hi - mm.r).values], fmt=MARK[arm], color=col, ms=6,
                               capsize=2, lw=1)
            a.set_yscale('log'); a.set_ylim(1e-4, 1.2)
            a.axvline(XMISS + 2.5, color='0.6', lw=.8)
            a.set_xticks([XMISS] + list(range(-10, 41, 10)))
            a.set_xticklabels(['SCI\\nmissed'] + [str(v) for v in range(-10, 41, 10)])
    legend(fig, arms); save(fig, name); plt.show()
    return pd.DataFrame(rows)

F1 = fig1()
F1.to_csv(os.path.join(FIG, 'fig1_bler_vs_sinr.csv'), index=False)
"""))
    C.append(md("Share of pairs per SINR bin (pooled, R6): how much of the traffic sits where the schemes differ."))
    C.append(code("""s = SIN[(SIN.arm == 'R6') & SIN.nbg.isin(LOADS)]
cut = pd.cut(s.bin.where(s.bin != TD.SINR_MISS, -100), [-101, -99, 0, 10, 20, 100],
             labels=['SCI missed', '< 0 dB', '0-10 dB', '10-20 dB', '>= 20 dB'])
t = s.assign(c=cut).groupby(['sc', 'tr', 'c'], observed=False).n_pairs.sum().unstack('c')
display((t.div(t.sum(1), axis=0) * 100).round(1))
"""))
    C.append(md("Same figure at 0 and at 60 background cars (interference changes how SINR maps to BLER: "
                "at 60 cars a copy with the same first-copy SINR meets more collisions on its retransmissions)."))
    C.append(code("""_ = fig1(loads=[0], name='fig1_bler_vs_sinr_0cars')
_ = fig1(loads=[60], name='fig1_bler_vs_sinr_60cars')
"""))

    # ---------------- Fig 2
    C.append(md("""## Figure 2 — TB delivery probability versus helper failure probability
x: share of TBs (2 s window of one run) whose helper missed the leader's first transmission; windows binned
(`HF_EDGES`), each point at the TB-weighted mean x of its bin. y: TB delivery probability of the pairs in those
windows. Helper arms use their own helper; leader-only references (hollow) the R6 helper of the same window.
The x-range comes from the data: blockage on the curve, interference at high load."""))
    C.append(code("""def hf_frame(arms):
    w = WIN[WIN.nbg.isin(LOADS)].copy()
    ref = w[w.arm == 'R6'].set_index(['sc', 'tr', 'nbg', 'seed', 'w'])[['n_hfail', 'n_helper']]
    out = []
    for arm in arms:
        x = w[w.arm == arm]
        if arm in HELPER_ARMS:
            x = x.assign(hf=x.n_hfail / x.n_helper.replace(0, np.nan))
        else:
            r = ref.reindex(pd.MultiIndex.from_frame(x[['sc', 'tr', 'nbg', 'seed', 'w']]))
            x = x.assign(hf=(r.n_hfail / r.n_helper.replace(0, np.nan)).values)
        out.append(x[x.hf.notna()])
    x = pd.concat(out)
    x['hbin'] = pd.cut(x.hf, HF_EDGES, right=False, labels=False)
    x['hf_w'] = x.hf * x.n_pkt
    return x

def fig_vs_hf(num, den, ylabel, title, name, arms, ref_arms=LEADER_REF, ylim=None):
    x = hf_frame(arms + ref_arms)
    fig, ax = grid(title, ylabel, 'helper failure probability (share of TBs, 2 s windows)', sharey=False)
    rows = []
    for i, tr in enumerate(TRS):
        for j, sc in enumerate(SCS):
            a = ax[i, j]
            for arm in arms + ref_arms:
                g = x[(x.sc == sc) & (x.tr == tr) & (x.arm == arm)]
                pts = []
                for b, gb in g.groupby('hbin'):
                    if gb.n_pairs.sum() < MIN_PAIRS: continue
                    r, lo, hi = num(gb) if callable(num) else boot_ratio(gb, num, den)
                    pts.append((gb.hf_w.sum() / gb.n_pkt.sum(), r, lo, hi))
                    rows.append(dict(sc=sc, tr=tr, arm=arm, hbin=b, hf=pts[-1][0], y=r, lo=lo, hi=hi,
                                     pairs=gb.n_pairs.sum(), windows=len(gb)))
                if not pts: continue
                p = pd.DataFrame(pts, columns=['x', 'r', 'lo', 'hi'])
                col, ls, lw = STYLE[arm]
                hollow = arm in ref_arms
                a.plot(p.x, p.r, ls=ls, color=col, lw=lw, marker=MARK[arm], ms=5,
                       mfc='white' if hollow else col)
                a.fill_between(p.x, p.lo, p.hi, color=col, alpha=.12, lw=0)
            a.set_xlim(0, 1)
            if ylim: a.set_ylim(*ylim)
    legend(fig, arms + ref_arms, [Line2D([], [], color='k', marker='o', mfc='white', ls='',
                                         label='hollow: leader only, x of the R6 run')])
    save(fig, name); plt.show()
    out = pd.DataFrame(rows); out.to_csv(os.path.join(FIG, name + '.csv'), index=False)
    return out

F2 = fig_vs_hf('n_deliv', 'n_pairs', 'TB delivery probability', 'TB delivery probability vs helper failure probability',
               'fig2_delivery_vs_helper_failure', HELPER_ARMS)
"""))
    C.append(md("""**Per-TB view of the same question.** Delivery probability of the pairs of TBs whose helper failed vs TBs
whose helper decoded the first copy (no windows), pooled over `LOADS`, with seed CI. The gap between "helper
failed" and "helper ok" is what the takeover has to close; the leader-only arms have no row."""))
    C.append(code("""def cond_table(loads=LOADS):
    w = WIN[WIN.nbg.isin(loads) & WIN.arm.isin(HELPER_ARMS)]
    rows = []
    for (sc, tr, arm), g in w.groupby(['sc', 'tr', 'arm']):
        f = boot_ratio(g, 'n_deliv_hfail', 'n_pairs_hfail'); o = boot_ratio(g, 'n_deliv_hok', 'n_pairs_hok')
        rows.append(dict(sc=sc, tr=tr, arm=arm, p_hfail=g.n_hfail.sum() / g.n_helper.sum(),
                         deliv_hfail=f[0], ci_hfail=f'[{f[1]:.3f}, {f[2]:.3f}]',
                         deliv_hok=o[0], ci_hok=f'[{o[1]:.3f}, {o[2]:.3f}]'))
    return pd.DataFrame(rows).set_index(['sc', 'tr', 'arm']).round(3)

for L in (LOADS, [0], [60]):
    display(Markdown(f'loads {L}')); T = cond_table(L); display(T)
    T.to_csv(os.path.join(FIG, f'tab_delivery_given_helper_{"all" if L == LOADS else L[0]}.csv'))
"""))

    # ---------------- Fig 3
    C.append(md("""## Figure 3 — Resource efficiency versus helper failure probability
y: TB-equivalents delivered to the group (delivered pairs / 8) per **reserved** subchannel-slot (10 PRB × 1 slot).
Same windows and bins as Fig. 2. A scheme that buys reliability with extra resources moves down here.
Fig. 3b: per **transmitted** subchannel-slot (unused reservations not counted)."""))
    C.append(code("""for (col_den, nm, lab) in (('ss_resv', 'fig3_efficiency_reserved_vs_helper_failure', 'reserved'),
                          ('ss_tx', 'fig3b_efficiency_tx_vs_helper_failure', 'transmitted')):
    WIN['tbeq'] = WIN.n_deliv / NMEM
    F3 = fig_vs_hf('tbeq', col_den, f'delivered TBs / {lab} subch.-slot',
                   f'Resource efficiency ({lab} resources) vs helper failure probability', nm, HELPER_ARMS)
"""))
    C.append(md("Efficiency and delivery per load (whole runs, no windows): delivered TBs per reserved / transmitted "
                "subchannel-slot, mean over the cell, seed CI."))
    C.append(code("""WIN['tbeq'] = WIN.n_deliv / NMEM
rows = []
for (sc, tr, nbg, arm), g in WIN[WIN.arm.isin(ARMS_ALL)].groupby(['sc', 'tr', 'nbg', 'arm']):
    d = boot_ratio(g, 'n_deliv', 'n_pairs'); e = boot_ratio(g, 'tbeq', 'ss_resv'); t = boot_ratio(g, 'tbeq', 'ss_tx')
    rows.append(dict(sc=sc, tr=tr, nbg=nbg, arm=arm, deliv=d[0], eff_resv=e[0], eff_resv_lo=e[1], eff_resv_hi=e[2],
                     eff_tx=t[0], resv_per_tb=g.ss_resv.sum() / g.n_pkt.sum(), tx_per_tb=g.ss_tx.sum() / g.n_pkt.sum()))
EFF = pd.DataFrame(rows); EFF.to_csv(os.path.join(FIG, 'tab_efficiency_per_load.csv'), index=False)
fig, ax = grid('Resource efficiency vs background load', 'delivered TBs / reserved subch.-slot', 'background cars',
               sharey=False)
for i, tr in enumerate(TRS):
    for j, sc in enumerate(SCS):
        for arm in ARMS_MAIN:
            g = EFF[(EFF.sc == sc) & (EFF.tr == tr) & (EFF.arm == arm)].sort_values('nbg')
            col, ls, lw = STYLE[arm]
            ax[i, j].plot(g.nbg, g.eff_resv, ls=ls, color=col, lw=lw, marker=MARK[arm], ms=5)
            ax[i, j].fill_between(g.nbg, g.eff_resv_lo, g.eff_resv_hi, color=col, alpha=.12, lw=0)
        ax[i, j].set_xticks([0, 15, 30, 45, 60])
legend(fig, ARMS_MAIN); save(fig, 'fig3c_efficiency_vs_load'); plt.show()
display(EFF[EFF.nbg.isin([0, 60])].pivot_table(index=['sc', 'tr', 'nbg'], columns='arm', values='eff_resv')[ARMS_ALL].round(3))
"""))

    # ---------------- Fig 4
    C.append(md("""## Figure 4 — Takeover effectiveness
$P_\\mathrm{recovery}$ = takeover attempts that rescued at least one member / takeover attempts, per trigger:
**helper NACK** (solid) and **fallback, no helper transmission detected** (dashed), R5 (red) and R6 (green).
x: helper failure probability (same windows as Fig. 2; left) and background load (right column of the second figure)."""))
    C.append(code("""TRIG = {1: ('helper NACK', '-'), 2: ('no helper tx detected (fallback)', '--')}
x = hf_frame(TO_ARMS)
fig, ax = grid('Takeover recovery probability vs helper failure probability', 'P(recovery) per takeover attempt',
               'helper failure probability (share of TBs, 2 s windows)')
rows = []
for i, tr in enumerate(TRS):
    for j, sc in enumerate(SCS):
        a = ax[i, j]
        for arm in TO_ARMS:
            for k, (tn, ls) in TRIG.items():
                g = x[(x.sc == sc) & (x.tr == tr) & (x.arm == arm)]
                pts = []
                for b, gb in g.groupby('hbin'):
                    if gb[f'to{k}_tx'].sum() < 30: continue
                    r, lo, hi = boot_ratio(gb, f'to{k}_useful_tx', f'to{k}_tx')
                    pts.append((gb.hf_w.sum() / gb.n_pkt.sum(), r, lo, hi, gb[f'to{k}_tx'].sum()))
                    rows.append(dict(sc=sc, tr=tr, arm=arm, trigger=tn, hbin=b, hf=pts[-1][0], p_rec=r, lo=lo, hi=hi,
                                     attempts=pts[-1][4]))
                if not pts: continue
                p = pd.DataFrame(pts, columns=['x', 'r', 'lo', 'hi', 'n'])
                col = STYLE[arm][0]
                a.plot(p.x, p.r, ls=ls, color=col, lw=2, marker=MARK[arm], ms=5, mfc=col if k == 1 else 'white')
                a.fill_between(p.x, p.lo, p.hi, color=col, alpha=.1, lw=0)
        a.set_xlim(0, 1); a.set_ylim(0, 1.02)
legend(fig, TO_ARMS, [Line2D([], [], color='k', ls=ls, label=tn) for tn, ls in TRIG.values()])
save(fig, 'fig4_recovery_vs_helper_failure'); plt.show()
pd.DataFrame(rows).to_csv(os.path.join(FIG, 'fig4_recovery_vs_helper_failure.csv'), index=False)
"""))
    C.append(code("""rows = []
for (sc, tr, nbg, arm), g in WIN[WIN.arm.isin(TO_ARMS)].groupby(['sc', 'tr', 'nbg', 'arm']):
    row = dict(sc=sc, tr=tr, nbg=nbg, arm=arm, tbs=g.n_pkt.sum(),
               to_frac=g.to_any_tb.sum() / g.n_pkt.sum(), p_hfail=g.n_hfail.sum() / g.n_helper.sum())
    for k, (tn, _) in TRIG.items():
        r = boot_ratio(g, f'to{k}_useful_tx', f'to{k}_tx')
        rt = boot_ratio(g, f'to{k}_rec_tb', f'to{k}_tb')
        row |= {f't{k}_attempts': g[f'to{k}_tx'].sum(), f't{k}_tb_frac': g[f'to{k}_tb'].sum() / g.n_pkt.sum(),
                f't{k}_p_rec': r[0], f't{k}_p_rec_lo': r[1], f't{k}_p_rec_hi': r[2], f't{k}_p_rec_tb': rt[0],
                f't{k}_member_rescue': g[f'to{k}_rescued'].sum() / max(g[f'to{k}_lacking'].sum(), 1),
                f't{k}_no_lack': g[f'to{k}_nolack_tx'].sum() / max(g[f'to{k}_tx'].sum(), 1)}
    rows.append(row)
TO = pd.DataFrame(rows); TO.to_csv(os.path.join(FIG, 'tab_takeover_per_load.csv'), index=False)

fig, ax = grid('Takeover recovery probability and takeover fraction vs background load', 'probability',
               'background cars', figsize=(15, 7.5))
for i, tr in enumerate(TRS):
    for j, sc in enumerate(SCS):
        a = ax[i, j]
        for arm in TO_ARMS:
            g = TO[(TO.sc == sc) & (TO.tr == tr) & (TO.arm == arm)].sort_values('nbg')
            col = STYLE[arm][0]
            for k, (tn, ls) in TRIG.items():
                a.plot(g.nbg, g[f't{k}_p_rec'], ls=ls, color=col, lw=2, marker=MARK[arm], ms=5,
                       mfc=col if k == 1 else 'white')
                a.fill_between(g.nbg, g[f't{k}_p_rec_lo'], g[f't{k}_p_rec_hi'], color=col, alpha=.1, lw=0)
            a.plot(g.nbg, g.to_frac, ls='-.', color=col, lw=1, marker='x', ms=5)
        a.set_ylim(0, 1.02); a.set_xticks([0, 15, 30, 45, 60])
legend(fig, TO_ARMS, [Line2D([], [], color='k', ls=ls, label='P(recovery), ' + tn) for tn, ls in TRIG.values()]
       + [Line2D([], [], color='k', ls='-.', marker='x', label='fraction of TBs with a takeover')])
save(fig, 'fig4b_recovery_vs_load'); plt.show()
"""))
    C.append(md("""**Recovery table.** Per cell and load: TBs; fraction of TBs with any takeover; per trigger: attempts,
fraction of TBs that trigger it, $P_\\mathrm{recovery}$ per attempt (and per TB), member rescue rate (members
rescued / members lacking at the attempt) and the share of attempts with nobody lacking (false alarms)."""))
    C.append(code("""cols = ['tbs', 'to_frac', 'p_hfail', 't1_attempts', 't1_tb_frac', 't1_p_rec', 't1_p_rec_tb', 't1_member_rescue',
        't1_no_lack', 't2_attempts', 't2_tb_frac', 't2_p_rec', 't2_p_rec_tb', 't2_member_rescue', 't2_no_lack']
for arm in TO_ARMS:
    display(Markdown(f'**{LABEL[arm]}** (t1 = helper NACK, t2 = fallback)'))
    display(TO[TO.arm == arm].set_index(['sc', 'tr', 'nbg'])[cols].round(3))
"""))

    # ---------------- Latency
    C.append(md("""## Figure 5 — TB delivery latency
CDF of the generation → decode latency over all pairs, a lost pair counted as never delivered (the curve ends at
the delivery probability). Every delivered pair arrives within ~18 ms, far below the PDB (100 ms periodic, 50 ms
aperiodic), so the x-axis stops at the largest observed latency; nothing is delivered between there and the PDB.
One grid at 0 and one at 60 background cars."""))
    C.append(code("""PDBMS = {tr: TD.PDB[tr] * 1e3 for tr in TRS}
def lat_cdf(loads, name):
    fig, ax = grid(f'TB delivery latency CDF ({loads} background cars), lost = never', 'P(delivered within t)',
                   'latency t (ms; PDB 100 / 50 ms not reached)', sharey=True)
    xmax = (LAT[LAT.bin >= 0].bin.max() + 2) * TD.LAT_STEP_MS
    for i, tr in enumerate(TRS):
        for j, sc in enumerate(SCS):
            a = ax[i, j]
            for arm in ARMS_MAIN:
                g = LAT[(LAT.sc == sc) & (LAT.tr == tr) & (LAT.arm == arm) & LAT.nbg.isin(loads)]
                h = g.groupby('bin').n.sum()
                tot = h.sum(); h = h[h.index >= 0].sort_index()
                t = (h.index.values + 1) * TD.LAT_STEP_MS
                col, ls, lw = STYLE[arm]
                a.step(np.r_[0, t], np.r_[0, h.cumsum().values / tot], where='post', ls=ls, color=col, lw=lw)
            a.set_xlim(0, xmax); a.set_ylim(0, 1.01)
    legend(fig, ARMS_MAIN); save(fig, name); plt.show()

lat_cdf([0], 'fig5_latency_cdf_0cars'); lat_cdf([60], 'fig5_latency_cdf_60cars')
"""))
    C.append(md("""**95th-percentile latency** of the delivered pairs vs load (seed CI), and the probability of delivery
within the PDB beside it in the table. p95 over delivered pairs ignores lost ones, so read it with the delivery
probability; `p95_all` treats lost pairs as infinite (NaN when more than 5 % are lost)."""))
    C.append(code("""def p95(h, tot=None, q=.95):
    h = h.sort_index(); c = h.cumsum().values; n = c[-1] if tot is None else tot
    k = np.searchsorted(c, q * n)
    return (h.index.values[k] + 1) * TD.LAT_STEP_MS if k < len(c) else np.nan

rows = []
for (sc, tr, nbg, arm), g in LAT[LAT.arm.isin(ARMS_ALL)].groupby(['sc', 'tr', 'nbg', 'arm']):
    H = g.pivot_table(index='seed', columns='bin', values='n', aggfunc='sum', fill_value=0)
    lost = H.pop(-1) if -1 in H.columns else pd.Series(0, index=H.index)
    tot = H.sum(1) + lost
    hp = H.sum(0)
    bs = []
    for _ in range(300):
        idx = RNG.integers(0, len(H), len(H))
        bs.append(p95(pd.Series(H.values[idx].sum(0), index=H.columns)))
    rows.append(dict(sc=sc, tr=tr, nbg=nbg, arm=arm, p95=p95(hp), p95_lo=np.percentile(bs, 2.5),
                     p95_hi=np.percentile(bs, 97.5), p95_all=p95(hp, tot.sum()), p50=p95(hp, q=.5),
                     deliv=hp.sum() / tot.sum()))
P95 = pd.DataFrame(rows); P95.to_csv(os.path.join(FIG, 'tab_latency_p95.csv'), index=False)
fig, ax = grid('95th-percentile latency of delivered TBs vs background load (PDB 100 / 50 ms)', 'p95 latency (ms)', 'background cars',
               sharey=False)
for i, tr in enumerate(TRS):
    for j, sc in enumerate(SCS):
        for arm in ARMS_MAIN:
            g = P95[(P95.sc == sc) & (P95.tr == tr) & (P95.arm == arm)].sort_values('nbg')
            col, ls, lw = STYLE[arm]
            ax[i, j].plot(g.nbg, g.p95, ls=ls, color=col, lw=lw, marker=MARK[arm], ms=5)
            ax[i, j].fill_between(g.nbg, g.p95_lo, g.p95_hi, color=col, alpha=.12, lw=0)
        ax[i, j].set_xticks([0, 15, 30, 45, 60])
legend(fig, ARMS_MAIN); save(fig, 'fig5b_latency_p95_vs_load'); plt.show()
display(P95[P95.nbg.isin([0, 60])].pivot_table(index=['sc', 'tr', 'nbg'], columns='arm', values='p95')[ARMS_ALL])
"""))

    # ---------------- summary
    C.append(md("""## Summary table — the four metrics
Per cell, at 30 cars (CBR ~0.5, the realistic operating point) and 60 cars: TB delivery probability, residual
BLER, delivered TBs per reserved subchannel-slot, p95 latency of delivered TBs, and for R5 / R6 the fraction of
TBs with a takeover and $P_\\mathrm{recovery}$ per trigger. Written to `figures/takeover_v3/tab_summary.csv`."""))
    C.append(code("""S = EFF.merge(P95[['sc', 'tr', 'nbg', 'arm', 'p95']], on=['sc', 'tr', 'nbg', 'arm'])
S = S.merge(TO[['sc', 'tr', 'nbg', 'arm', 'to_frac', 't1_p_rec', 't2_p_rec']], on=['sc', 'tr', 'nbg', 'arm'], how='left')
S['bler'] = 1 - S.deliv
S = S[S.nbg.isin([30, 60])][['sc', 'tr', 'nbg', 'arm', 'deliv', 'bler', 'eff_resv', 'p95', 'to_frac', 't1_p_rec',
                              't2_p_rec']]
S.to_csv(os.path.join(FIG, 'tab_summary.csv'), index=False)
display(S.set_index(['sc', 'tr', 'nbg', 'arm']).round(3))
"""))
    return C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out-dir', default='out/remote_v3')
    ap.add_argument('--output', default='TAKEOVER_RESULTS_v3.ipynb')
    a = ap.parse_args()
    nb = nbf.v4.new_notebook()
    nb.cells = cells(os.path.abspath(os.path.join(HERE, a.out_dir)))
    nb.metadata['kernelspec'] = {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}
    nbf.write(nb, os.path.join(HERE, a.output))
    print('wrote', a.output, len(nb.cells), 'cells')


if __name__ == '__main__':
    main()
