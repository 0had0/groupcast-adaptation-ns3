#!/usr/bin/env python3
"""Build PSFCH_ERRORS.ipynb: how sensitive is the cooperative protocol (R6) to HARQ feedback that is lost or
misread? Data: out/psfch_err/summary.csv (psfch_err_sweep.py, ease-rtx, commit 5ca2305 build) and the error-free
campaign-v3 runs out/remote_v3 (same commands). Figures: figures/psfch_err/.

  python3 scripts/build_psfch_notebook.py
  python3 -m jupyter nbconvert --to notebook --execute --inplace notebooks/PSFCH_ERRORS.ipynb
"""
import nbformat as nbf
import nb_layout

md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell


def cells():
    C = []
    C.append(md(r"""# PSFCH reception errors: sensitivity of the cooperative protocol

**The question.** Campaign v3 delivers every PSFCH (ACK/NACK) without error. Every decision of the cooperative
protocol is driven by that feedback: the helper relays only while a member still NACKs, the source takes over on a
helper NACK, falls back when the helper stays silent, and stops when all members ACK. What happens when feedback
is lost or misread?

**How a PSFCH error acts in the model** (`nack_only_harq.cc`): the source declares success only if *every* member
ACKed; a missing feedback (DTX) counts as NACK. A **lost** feedback therefore costs an extra transmission (a lost
ACK) or a later recovery (a lost helper NACK is caught by the fallback check), but cannot end a TB early. A
**NACK detected as ACK** can: if it completes the set of ACKs, the source stops and that member loses the TB.

**3GPP PSFCH vs 5G-LENA** (TS 38.211 V17.11.0 §8.3.4, TS 38.213 V17.14.0 §16.2.3, §16.3):

| | 3GPP Rel-17 | 5G-LENA (stock) |
|---|---|---|
| waveform | format 0: 12-subcarrier sequence, ACK/NACK by cyclic shift (NACK $m_{cs}$=0, ACK 6) | a message on the signal |
| bandwidth | 1 PRB per feedback (`sl-PSFCH-RB-Set`) | every PRB of the pool (50) |
| symbols | second-to-last SL symbol, repeated in the symbol before (AGC), then a guard | 1 symbol |
| multiplexing | orthogonal: resource $(P_{ID}+M_{ID}) \bmod R$ over PRBs × cyclic-shift pairs | all PSFCH overlap and interfere |
| power (no `dl-P0-PSFCH`) | $P_{CMAX} - 10\log_{10} N_{Tx,PSFCH}$ on that PRB | 23 dBm spread over 50 PRBs (−17 dB per PRB) |

**What is swept** (`NrSpectrumPhy` attributes, `patches/nr/local.patch`; each run is the v3 command of the same
arm, cell and seed plus these flags; every UE draws on its own stream, so the leader and the helper overhearing the
members both see the errors):

| setting | attributes | meaning | arms |
|---|---|---|---|
| `miss<p>` | mode 1, `SlPsfchMissProb` = 1, 5, 10, 20, 30 % | each received ACK/NACK lost (read as DTX) | R6 (R3 at 10 %) |
| `n2a<q>` | mode 1, `SlPsfchNackToAckProb` = 0.1, 1, 5 % | a received NACK detected as ACK | R6 (R3 at 1 %) |
| `snr_p<P>` | mode 2, `SlPsfchTxPowerDbm` = 23, 10, 0 dBm | the PSFCH at the 3GPP power on one PRB, decoded like a PSCCH of the same link (NrEesmIrT1, MCS 0) over noise; other PSFCH orthogonal; a failed PSFCH is lost | R6 (R3 at 23 dBm) |

23 dBm is $P_{CMAX}$, the out-of-coverage PSFCH power; 10 and 0 dBm stand for in-coverage open-loop power control.
Cells: 3 routes × 2 traffic types × 0 / 60 background vehicles, seeds 1–10. Reference: campaign v3 (error-free),
which the new build reproduces bit for bit with the flags off (`psfch_err_sweep.py check`, laptop and ease-rtx).

**Limits.** Format-0 detection is a sequence correlation, here replaced by the PSCCH decoding model on one PRB.
The AGC repeat adds no energy. DTX detected as ACK (a silent member read as ACK) is not modelled. Mode 1 errors
are independent per feedback (no correlation with the link)."""))
    C.append(code(r"""import glob, os, numpy as np, pandas as pd, matplotlib.pyplot as plt
from scipy import stats
FIG = 'figures/psfch_err'; os.makedirs(FIG, exist_ok=True)
plt.rcParams.update({'figure.dpi': 110, 'font.size': 9, 'font.family': 'serif', 'axes.grid': True, 'grid.alpha': 0.3})
# paper palette (draw.io protocol figure): source = blue, cooperative = sage, ACK = green, NACK = red
ARM = {'R3': '#3E7CA6', 'R6': '#4F7D69'}
SCN = {'hwy': 'Highway', 'rbt': 'Roundabout W', 'rbtE': 'Roundabout E'}
MK = {'hwy': 'o', 'rbt': 's', 'rbtE': '^'}
d = pd.read_csv('out/psfch_err/summary.csv')
d['seed'] = d.cell.str.extract(r'_s(\d+)$').astype(int)[0]
d['cell'] = d.cell.str.replace(r'_s\d+$', '', regex=True)
v = []
for arm in ('R3', 'R6'):
    for (cell, seed) in d[['cell', 'seed']].drop_duplicates().itertuples(index=False):
        f = glob.glob(f'out/remote_v3/{arm}/{cell}_s{seed}_run.csv*')
        if f:
            v.append(pd.read_csv(f[0]).iloc[0].to_dict() | {'arm': arm, 'cell': cell, 'seed': seed, 'setting': 'v3'})
v = pd.DataFrame(v)
for x in (d, v):
    x['bg'] = np.where(x.cell.str.contains('moderate'), 60, 0)
    x['sc'] = x.cell.str.split('_').str[0]
    x['tr'] = np.where(x.cell.str.contains('aperiodic'), 'aperiodic', 'periodic')
print(len(d), 'sweep runs,', len(v), 'error-free v3 runs')
d.groupby(['arm', 'setting']).size().unstack(0)"""))
    C.append(md(r"""## 1. Realised PSFCH error rates

Fraction of the platoon's feedback received at the leader (and at the members, who overhear it) that was lost,
and NACKs detected as ACK per received feedback. For the forced settings it must match the setting."""))
    C.append(code(r"""g = d.groupby(['arm', 'setting', 'bg'])
R = pd.DataFrame({'lost at leader (%)': 100 * g.psfch_lost_leader.sum() / g.psfch_fb_leader.sum(),
                  'lost at members (%)': 100 * g.psfch_lost_members.sum() / g.psfch_fb_members.sum(),
                  'NACK->ACK at leader (per 1000 fb)': 1000 * g.psfch_n2a_leader.sum() / g.psfch_fb_leader.sum()})
R.round(3).unstack('bg')"""))
    C.append(md(r"""### SNR mode: loss rate by route, load and power

The loss depends on the link through the received power (distance, buildings, fading). The leader's mean
PSFCH error rate per route:"""))
    C.append(code(r"""s = d[d.setting.str.startswith('snr')]
T = s.groupby(['setting', 'sc', 'bg']).apply(lambda x: 100 * x.psfch_lost_leader.sum() / x.psfch_fb_leader.sum())
T = T.unstack(['sc', 'bg']).round(2)
T.columns = [f'{SCN[a]}, {b} veh.' for a, b in T.columns]
T"""))
    C.append(md(r"""## 2. Cost against the error-free protocol (paired by seed)

Change against campaign v3 of the same arm, cell and seed: mean member PRR (pp), worst member PRR (pp) and
transmissions per packet. Mean over the runs ± 95 % Student-t half-width."""))
    C.append(code(r"""K = {'prr_mean': 100, 'prr_min': 100, 'tx_per_pkt': 1}
J = d.merge(v, on=['arm', 'cell', 'seed'], suffixes=('', '_v3'))
def ci(x):
    x = x.dropna()
    return x.mean(), stats.t.ppf(.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x)) if len(x) > 1 else np.nan
rows = []
for (arm, s, bg), q in J.groupby(['arm', 'setting', 'bg']):
    r = dict(arm=arm, setting=s, bg=bg, runs=len(q))
    for k, f in K.items():
        m, h = ci(f * (q[k] - q[k + '_v3']))
        r[k] = f'{m:+.2f} ± {h:.2f}'
    rows.append(r)
D = pd.DataFrame(rows).set_index(['arm', 'setting', 'bg'])
D"""))
    C.append(code(r"""def curve(prefix, xs, label, fname):
    fig, axs = plt.subplots(2, 2, figsize=(9, 5.4), sharex=True)
    for j, bg in enumerate((0, 60)):
        for i, (k, f, yl) in enumerate([('prr_mean', 100, 'Δ mean member PRR (pp)'),
                                        ('tx_per_pkt', 1, 'Δ transmissions per packet')]):
            ax = axs[i, j]
            for sc in SCN:
                m, h = [], []
                for x in xs:
                    q = J[(J.arm == 'R6') & (J.setting == f'{prefix}{x}') & (J.bg == bg) & (J.sc == sc)]
                    a, b = ci(f * (q[k] - q[k + '_v3'])) if len(q) else (np.nan, np.nan)
                    m.append(a); h.append(b)
                ax.errorbar(100 * np.array(xs, float), m, yerr=h, marker=MK[sc], ms=4, capsize=2, lw=1,
                            color=ARM['R6'], alpha=.9, label=SCN[sc], mfc='white' if sc == 'rbt' else ARM['R6'])
            ax.axhline(0, color='k', lw=.6)
            ax.set_ylabel(yl)
            if i == 0: ax.set_title(f'R6, {bg} background vehicles')
            if i == 1: ax.set_xlabel(label)
    axs[0, 0].legend(fontsize=7)
    fig.tight_layout(); fig.savefig(f'{FIG}/{fname}.pdf'); plt.show()
curve('miss', [0.01, 0.05, 0.1, 0.2, 0.3], 'PSFCH loss probability (%)', 'r6_vs_loss')"""))
    C.append(code(r"""curve('n2a', [0.001, 0.01, 0.05], 'NACK detected as ACK (%)', 'r6_vs_nack_to_ack')"""))
    C.append(code(r"""fig, axs = plt.subplots(1, 2, figsize=(9, 3.0), sharey=True)
P = ['snr_p23', 'snr_p10', 'snr_p0']
for j, bg in enumerate((0, 60)):
    ax = axs[j]
    for i, sc in enumerate(SCN):
        m, h = zip(*[ci(100 * (q.prr_mean - q.prr_mean_v3)) for p in P
                      for q in [J[(J.arm == 'R6') & (J.setting == p) & (J.bg == bg) & (J.sc == sc)]]])
        ax.bar(np.arange(3) + (i - 1) * .27, m, .25, yerr=h, capsize=2, color=ARM['R6'], alpha=.35 + .3 * i,
               edgecolor='k', lw=.4, label=SCN[sc])
    ax.set_xticks(range(3)); ax.set_xticklabels(['23 dBm', '10 dBm', '0 dBm']); ax.axhline(0, color='k', lw=.6)
    ax.set_title(f'R6, SNR-based PSFCH errors, {bg} veh.'); ax.set_xlabel('PSFCH transmit power')
axs[0].set_ylabel('Δ mean member PRR (pp)'); axs[0].legend(fontsize=7)
fig.tight_layout(); fig.savefig(f'{FIG}/r6_vs_power.pdf'); plt.show()"""))
    C.append(md(r"""### Per route, traffic and load

The table above pools the six route × traffic cells of a load. Here R6 per cell (10 paired seeds each): Δ mean
member PRR (pp), Δ worst member PRR (pp) and Δ transmissions per packet against v3; `*` = the 95 % interval
excludes 0. Last column: transmissions per packet without errors."""))
    C.append(code(r"""SETS = ['snr_p23', 'snr_p10', 'snr_p0', 'miss0.1', 'miss0.3', 'n2a0.05']
def cell_ci(x, f):
    m, h = ci(f * x)
    return f'{m:+.2f} ± {h:.2f}' + ('*' if abs(m) > h else ' ')
rows = []
for s in SETS:
    for (sc, tr, bg), q in J[(J.arm == 'R6') & (J.setting == s)].groupby(['sc', 'tr', 'bg']):
        rows.append({'setting': s, 'route': SCN[sc], 'traffic': tr, 'bg': bg,
                     'Δ prr_mean (pp)': cell_ci(q.prr_mean - q.prr_mean_v3, 100),
                     'Δ prr_min (pp)': cell_ci(q.prr_min - q.prr_min_v3, 100),
                     'Δ tx/pkt': cell_ci(q.tx_per_pkt - q.tx_per_pkt_v3, 1),
                     'tx/pkt v3': round(q.tx_per_pkt_v3.mean(), 2)})
PC = pd.DataFrame(rows).set_index(['setting', 'route', 'traffic', 'bg'])
sig = {k: PC[k].str.endswith('*').groupby(level='setting').sum() for k in ('Δ prr_mean (pp)', 'Δ prr_min (pp)', 'Δ tx/pkt')}
display(pd.DataFrame(sig).loc[SETS].rename(columns=lambda c: f'{c}: cells with *').assign(cells=12))
with pd.option_context('display.max_rows', 200):
    display(PC)"""))
    C.append(md(r"""### Where the extra transmissions come from

`tx_per_pkt` = (leader + helper transmissions) / packets. Split per packet for R6, mean over the 12 cells: the
leader's new TBs and retransmissions (fallback takeovers and takeovers after a helper NACK are among them), the
helper's relays, and the relays sent after every member had already ACKed (`relay_after_allack`: the helper
missed at least one ACK and read it as NACK). The helper overhears the members' PSFCH and draws its own errors, so
it relays on a lost ACK just as the leader would retransmit."""))
    C.append(code(r"""M = {'leader new TB': 'leader_new_tb', 'leader retx': 'leader_retx', '  of which fallback': 'fallback_tx',
     '  of which takeover (helper NACK)': 'helper_reclaim', 'helper relays': 'helper_tx',
     '  of which after all ACKed': 'relay_after_allack'}
def per_pkt(q, sfx=''):
    return {k: (q[c + sfx] / q['pkts' + sfx]).mean() for k, c in M.items()}
r6 = J[J.arm == 'R6']
S = {'v3 (error-free)': per_pkt(r6[r6.setting == 'snr_p23'], '_v3')}
for s in ['snr_p23', 'snr_p10', 'snr_p0', 'miss0.01', 'miss0.05', 'miss0.1', 'miss0.2', 'miss0.3', 'n2a0.05']:
    S[s] = per_pkt(r6[r6.setting == s])
display(pd.DataFrame(S).T.round(3))
rows = []
for s in ['snr_p23', 'miss0.1', 'n2a0.01']:
    for bg in (0, 60):
        r = {'setting': s, 'bg': bg}
        for arm in ('R3', 'R6'):
            q = J[(J.arm == arm) & (J.setting == s) & (J.bg == bg)]
            r[f'{arm} Δ tx/pkt'] = cell_ci(q.tx_per_pkt - q.tx_per_pkt_v3, 1)
        rows.append(r)
print('Extra transmissions per packet under the same errors, R6 against source Option 2 (R3):')
pd.DataFrame(rows).set_index(['setting', 'bg'])"""))
    C.append(md(r"""## 3. Does the cooperative scheme suffer more than source Option 2?

Both arms use Option 2 feedback, so both are exposed. Cooperative gain R6 − R3 (mean member PRR, pp, paired by
cell and seed) without errors (v3) and under the same errors; a smaller gain means R6 is the more sensitive one."""))
    C.append(code(r"""rows = []
for s in ['v3', 'snr_p23', 'miss0.1', 'n2a0.01']:
    src = v if s == 'v3' else d[d.setting == s]
    p = src.pivot_table(index=['bg', 'cell', 'seed'], columns='arm', values=['prr_mean', 'prr_min'])
    for bg, q in p.groupby(level=0):
        if ('prr_mean', 'R3') not in q or ('prr_mean', 'R6') not in q:
            continue
        r = dict(setting=s, bg=bg, runs=int(q[('prr_mean', 'R6')].notna().sum()))
        for k in ('prr_mean', 'prr_min'):
            m, h = ci(100 * (q[(k, 'R6')] - q[(k, 'R3')]))
            r[f'R6 − R3 {k} (pp)'] = f'{m:+.2f} ± {h:.2f}'
        rows.append(r)
pd.DataFrame(rows).set_index(['setting', 'bg'])"""))
    C.append(md(r"""## 3b. Against the blind helper (R4)

R4 (Kuo's blind helper: the leader and the helper send fixed blind copies) uses no HARQ feedback, so PSFCH errors
cannot change it: its campaign-v3 run of the same cell and seed is its result under every error setting. R6 − R4
paired by cell and seed: mean and worst member PRR (pp) and transmissions per packet, pooled over the routes per
load, then per route (`*` = the 95 % interval excludes 0). A gain that shrinks under errors means the errors eat
the advantage of feedback over blind relaying."""))
    C.append(code(r"""r4 = []
for (cell, seed) in d[['cell', 'seed']].drop_duplicates().itertuples(index=False):
    f = glob.glob(f'out/remote_v3/R4/{cell}_s{seed}_run.csv*')
    if f:
        r4.append(pd.read_csv(f[0]).iloc[0][['prr_mean', 'prr_min', 'tx_per_pkt']].to_dict() | {'cell': cell, 'seed': seed})
r4 = pd.DataFrame(r4)
print(len(r4), 'R4 v3 runs (blind, no PSFCH: identical under every error setting)')
SETS4 = ['v3', 'snr_p23', 'snr_p10', 'snr_p0', 'miss0.1', 'miss0.3', 'n2a0.05']
def vs_r4(s):
    src = v if s == 'v3' else d[d.setting == s]
    return src[src.arm == 'R6'].merge(r4, on=['cell', 'seed'], suffixes=('', '_r4'))
rows, rows_sc = [], []
for s in SETS4:
    q6 = vs_r4(s)
    for key, grp, out in ((['bg'], ['bg'], rows), (['sc', 'bg'], ['sc', 'bg'], rows_sc)):
        for g, q in q6.groupby(grp):
            g = g if isinstance(g, tuple) else (g,)
            r = {'setting': s} | dict(zip(key, g))
            if 'sc' in r: r['sc'] = SCN[r['sc']]
            r['runs'] = len(q)
            r['R6 − R4 prr_mean (pp)'] = cell_ci(q.prr_mean - q.prr_mean_r4, 100)
            r['R6 − R4 prr_min (pp)'] = cell_ci(q.prr_min - q.prr_min_r4, 100)
            r['R6 − R4 tx/pkt'] = cell_ci(q.tx_per_pkt - q.tx_per_pkt_r4, 1)
            out.append(r)
display(pd.DataFrame(rows).set_index(['setting', 'bg']))
with pd.option_context('display.max_rows', 200):
    display(pd.DataFrame(rows_sc).set_index(['setting', 'sc', 'bg']))"""))
    C.append(md(r"""## 4. Conclusions

All 1680 runs completed (ease-rtx, commit 5ca2305, 2026-11-20/22); 10 seeds per point, 95 % Student-t intervals.

- **The errors are realised as set.** Forced loss lands at 1.0 / 5.0 / 10.0 / 19.9 / 29.9 % at the leader and the
  members; NACK→ACK flips 0.1 / 1.5 / 7.5–9.0 per 1000 feedbacks (per NACK the set rate).
- **At the 3GPP power the PSFCH is almost never lost.** With 23 dBm on one PRB (out of coverage, $P_{CMAX}$) the
  leader loses 0.1–0.6 % of the feedback, and nothing measurable changes (Δ mean PRR −0.10 ± 0.34 and −0.07 ± 0.43
  pp, Δ transmissions ≤ 0.01). At 10 dBm the loss is 2 % on the highway and 7–10 % on the roundabouts, at 0 dBm
  11–12 % and 21–28 %: the loss follows the link (blocked roundabout links lose most).
- **Lost feedback costs transmissions, not reliability.** A lost ACK is read as NACK, so copies are sent that
  were not needed: +0.36 transmissions per packet at 10 % loss and +0.60 at 30 % with no background traffic (+0.23
  and +0.37 with 60 vehicles). The cost is largest on the highway (+1.13 per packet at 30 %, periodic, no load:
  1.75 → 2.88), where almost every member ACKs and a single lost ACK triggers a copy. Pooled over the routes, mean
  member PRR stays within its interval up to 30 % loss with no background traffic; with 60 vehicles it drops by
  0.6 ± 0.5 pp at 20 % and 0.8 ± 0.6 pp at 30 % (the extra copies add load to a saturated channel).
- **Per cell, no drop in reliability larger than 0.15 pp is significant up to 10 % loss.** In mean member PRR the
  significant changes are −0.08 ± 0.07 pp (0 dBm) and −0.14 ± 0.10 pp (5 % NACK→ACK), both on the highway, periodic,
  no load. At 30 % loss one cell drops clearly: Roundabout E, aperiodic, 60 vehicles, −1.9 ± 1.7 pp mean and
  −3.0 ± 2.6 pp worst member. The other roundabout cells lean negative (−0.2 to −1.4 pp) inside their intervals.
  Worst member: −2.6 ± 1.5 pp at 10 % loss (Roundabout E, periodic, 60 vehicles), −1.0 ± 0.9 pp at 30 % (highway,
  periodic, 60).
- **The extra copies are the helper's.** The leader's transmissions per packet stay at ~1.19 at every loss rate;
  helper relays grow from 1.20 per packet error-free to 1.48 at 10 % loss and 1.69 at 30 %, and the relays sent after
  every member had ACKed from ~0 to 0.29 and 0.54 per packet. The helper overhears the members' PSFCH, misses an ACK,
  and relays. Fallback takeovers also rise (0.07 → 0.17 per packet at 10 %), takeovers after a helper NACK fall
  slightly (0.13 → 0.09 at 30 %). So R6 pays more than R3 for the same loss: +0.36 against +0.23 transmissions per
  packet at 10 % with no background traffic, +0.23 against +0.16 with 60 vehicles, since two nodes act on the
  feedback. A helper that skips the relay when it heard no feedback from the members it serves would remove most of
  this (not tested).
- **NACK read as ACK is rare in its effect.** Even at 5 % per NACK the PRR change is −0.04 ± 0.32 pp (0 vehicles) and
  −0.16 ± 0.48 pp (60): a flip ends a TB early only when it completes the full set of ACKs, i.e. when that member was
  the only one still missing the TB (the per-TB effect was not traced here).
- **The cooperative scheme is not more fragile than source Option 2.** The gain R6 − R3 is unchanged under the same
  errors: +11.9 ± 2.1 pp mean / +22.7 ± 3.7 pp worst member error-free, +11.9 / +23.6 (SNR, 23 dBm), +11.9 / +24.3
  (10 % loss), +12.1 / +23.7 (1 % NACK→ACK) with no background traffic; +9.3, +9.2, +9.0, +9.4 pp mean with 60.
- **Against the blind helper (R4), the reliability gain holds but the cost advantage disappears under heavy loss.**
  R4 uses no feedback, so it is unaffected. R6 − R4 in mean member PRR: +3.8 ± 0.6 pp (0 vehicles) / +10.3 ± 1.4 pp
  (60) error-free, +3.7 / +10.3 at 23 dBm, +3.8 / +10.0 at 10 % loss, +3.4 / +9.6 at 30 % loss; worst member +7.1 /
  +10.5 error-free and +7.0 / +9.1 at 30 % loss; significant in every route and load under every setting. In
  transmissions per packet R6 is cheaper than R4 without errors (−0.52 ± 0.10 with no background traffic, −0.99 on
  the highway) and still at 23 and 10 dBm, but the extra helper relays erode it: −0.17 / +0.15 at 10 % loss and
  +0.08 / +0.29 at 30 % (R6 then costs more than R4 in every cell). Without background traffic the break-even lies
  between 10 and 30 % loss; with 60 vehicles the two already cost the same without errors (−0.07 ± 0.10) and R6
  costs more from 10 % loss on.

**For the paper.** The error-free PSFCH assumption does not inflate the result: at the 3GPP PSFCH power feedback
is lost in under 1 % of cases with no measurable effect, and even 30 % loss or 5 % NACK→ACK keeps the cooperative
gain over R3 and over R4; the price of lost feedback is redundant helper relays (resources), because the protocol
reads silence as NACK, and at 30 % loss that price makes R6 more expensive than the blind helper.

**Limits.** See the introduction: PSCCH decoding model on one PRB instead of format-0 sequence detection, no AGC
energy, no DTX→ACK, independent forced errors.
"""))
    return C


def main():
    nb = nbf.v4.new_notebook()
    nb['cells'] = cells()
    nb['metadata']['kernelspec'] = {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}
    nbf.write(nb_layout.add_preamble(nb), nb_layout.notebook_path('PSFCH_ERRORS.ipynb'))
    print('wrote PSFCH_ERRORS.ipynb')


if __name__ == '__main__':
    main()
