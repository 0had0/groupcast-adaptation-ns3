#!/usr/bin/env python3
"""Build RSSI_FALLBACK.ipynb: should the leader's fallback be gated by an SL-RSSI threshold, fixed or dynamic?
Analysis code: rssi_threshold.py. Data: out/rssi10 (R6, 60 background vehicles, seeds 1-10, six cells, ease-rtx,
commit 1ab508d; rssi_campaign.py). The first, one-seed version used out/rssi_v3. Figures: figures/rssi_fallback/.

  python3 build_rssi_notebook.py
  python3 -m jupyter nbconvert --to notebook --execute --inplace RSSI_FALLBACK.ipynb
"""
import os

import nbformat as nbf

HERE = os.path.dirname(os.path.abspath(__file__))
md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell


def cells():
    C = []
    C.append(md(r"""# RSSI-gated fallback: which threshold, and should it be dynamic?

**The problem.** In the cooperative scheme (R6) the leader checks the helper's handed slot. If it decodes no SCI
from the helper there, it takes the next handed slot back (fallback). When the helper did transmit but its SCI
failed at the leader, both then send the TB in the same slot on the same subchannels: a *double transmission*.

**The candidate fix.** Fall back only if no helper SCI was decoded **and** the SL-RSSI on the handed subchannels
is below a threshold. A threshold can be **fixed** (dBm per RB) or **dynamic**: relative to the helper's
PSSCH-RSRP, which the leader measures whenever it does decode the helper, or chosen per context (route, traffic).

**What decides it.** A threshold avoids double transmissions but also suppresses some true fallbacks, when other
vehicles happen to transmit on the handed subchannels. So we need the value of each:
- what a double transmission costs the members, compared with the helper's copy sent alone;
- what a true fallback recovers.

**Data.** `out/rssi10`: R6, 60 background vehicles, seeds 1-10, three routes x two traffic types, 60 runs
(ease-rtx, `rssi_campaign.py`, build `1ab508d`: the campaign-v3 commands with the logging added; the protocol is
unchanged). Rates that are pooled "per context" are pooled over the ten seeds of a route and traffic type. One row per fallback check
(`<cell>_fbchk.csv`), joined with the per-transmission logs (`_ltx`, `_htx`) and the per-member reception log
(`_att`). The split of the received power by transmitter is simulation ground truth. A UE measures only the total.

| term | meaning |
|---|---|
| check | the leader listens in the helper's handed slot h after a not-all-ACK decision |
| A | helper transmitted in h, the leader decoded its SCI: no fallback |
| B | helper transmitted in h, SCI not decoded: false fallback (double transmission if the helper relays again) |
| C | helper silent in h: true fallback |
| s3 | the TB's next copy after h, the slot the fallback takes; who sent it: L, H, or LH (double) |
| member in need | a member that had not decoded the TB up to h |
| recovered | a member in need that decodes the TB at s3 |
| RSSI | per RB on the handed subchannels: helper + others + noise (noise -113.4 dBm per RB) |"""))
    C.append(code(r"""import numpy as np, pandas as pd, matplotlib.pyplot as plt, os
import rssi_threshold as R
DATA = 'out/rssi10'
FIG = 'figures/rssi_fallback'; os.makedirs(FIG, exist_ok=True)
plt.rcParams.update({'figure.dpi': 110, 'font.size': 9, 'axes.grid': True, 'grid.alpha': 0.3})
COL = {'A': '#4C78A8', 'B': '#E45756', 'C': '#54A24B'}
LBL = {'A': 'A: helper sent, SCI decoded', 'B': 'B: helper sent, SCI missed', 'C': 'C: helper silent'}
d = R.load(DATA)
d['helper_ref'] = R.helper_reference(d)
ra = R.r_alone_by_ctx(d)
print(len(d), 'checks in', d.cell.nunique(), 'runs;', d.drop_duplicates('cell').tbs.sum(), 'TBs in the KPI windows')
d.pivot_table(index=['route', 'traffic'], columns='cls', values='t', aggfunc='size', fill_value=0)"""))
    C.append(md(r"""## 1. What the leader measures

The B checks are the problem: 22 % of the fallbacks, at about the same rate for both traffic types (aperiodic
traffic has more fallbacks, so 83 % of the B checks). In every B check the helper's PSCCH reached the leader and
failed under interference. The RSSI is high because the helper's own power is in it. The C checks are often not
quiet: another vehicle transmits on the handed subchannels in about three quarters of them, so their RSSI has a long
upper tail that overlaps B."""))
    C.append(code(r"""fig, ax = plt.subplots(1, 2, figsize=(10, 3.4))
for k in 'ABC':
    x = np.sort(d[d.cls == k].rssi.values)
    ax[0].plot(x, np.arange(1, len(x) + 1) / len(x), color=COL[k], label=f'{LBL[k]} (n={len(x)})')
ax[0].axvline(R.NOISE_DBM_PER_RB, color='grey', ls=':', lw=1); ax[0].text(R.NOISE_DBM_PER_RB + 0.5, 0.05, 'noise', color='grey')
ax[0].set_xlabel('RSSI per RB on the handed subchannels (dBm)'); ax[0].set_ylabel('ECDF'); ax[0].legend(fontsize=7, loc='lower right')
ax[0].set_title('RSSI at the check, by class')
bc = d[d.cls.isin(['B', 'C'])]
ax[1].scatter(bc[bc.cls == 'B'].p_helper_rb, bc[bc.cls == 'B'].p_other_rb.fillna(-125), s=10, color=COL['B'], label='B: helper power vs others')
ax[1].scatter(np.full((bc.cls == 'C').sum(), -125.0) + np.random.default_rng(0).uniform(-2, 2, (bc.cls == 'C').sum()),
              bc[bc.cls == 'C'].p_other_rb.fillna(-125), s=8, color=COL['C'], alpha=0.6, label='C: others only (x = no helper)')
ax[1].plot([-125, -50], [-125, -50], color='grey', lw=0.8, ls='--')
ax[1].set_xlabel('helper PSSCH power per RB (dBm)'); ax[1].set_ylabel('other transmitters per RB (dBm, -125 = none)')
ax[1].set_title('Who is on the handed subchannels'); ax[1].legend(fontsize=7)
fig.tight_layout(); fig.savefig(f'{FIG}/rssi_by_class.pdf'); plt.show()
q = [5, 25, 50, 75, 95]
pd.DataFrame({k: np.percentile(d[d.cls == k].rssi, q) for k in 'ABC'}, index=[f'p{x}' for x in q]).round(1).T.assign(
    others_present=[(d[d.cls == k].p_other > -998).mean().round(2) for k in 'ABC'])"""))
    C.append(md(r"""## 2. What the slot after the check delivers

This is the key input. The table gives, for the members in need, the share that decode the TB at s3:
- with a double transmission (B, s3 = LH);
- with the helper's copy alone (A, s3 = H): the counterfactual for B if the leader withholds;
- with the leader's fallback alone (C, s3 = L): what is lost when a true fallback is suppressed.

The two copies of a double carry the same TB on the same subchannels. Each is interference to the other, but a
member can still decode either one, and both enter the same HARQ buffer. A double therefore costs much less than a
plain collision. A suppressed true fallback, by contrast, leaves the last slot empty."""))
    C.append(code(r"""def rate(sel):
    g = sel.groupby('ctx')[['n_rec3', 'n_need']].sum()
    return g.n_rec3 / g.n_need, g.n_need
dbl, nb = rate(d[(d.cls == 'B') & (d.s3_who == 'LH')])
fb, nc = rate(d[(d.cls == 'C') & (d.s3_who == 'L')])
t = pd.DataFrame({'double (B, LH)': dbl, 'members in need (B)': nb, 'helper alone (A, H)': pd.Series(ra),
                  'fallback alone (C, L)': fb, 'members in need (C)': nc})
tot = lambda m: (lambda g: g.n_rec3.sum() / g.n_need.sum())(d[m])
print(f"pooled: double {tot((d.cls=='B')&(d.s3_who=='LH')):.3f}, helper alone "
      f"{tot((d.cls=='A')&(d.s3_who=='H')&(d.n_need>0)):.3f}, fallback alone {tot((d.cls=='C')&(d.s3_who=='L')):.3f}")
print('checks by class and sender at s3:'); print(d.groupby(['cls', 's3_who']).size().unstack(fill_value=0))
t.round(3)"""))
    C.append(md(r"""## 3. A fixed threshold

For a threshold x, the rule withholds the fallback when RSSI ≥ x. The effect is counted per (TB, member) pair:
- **gained:** in a suppressed double, the helper's copy goes alone. Its recoveries are estimated with the helper-alone
  rate of the same cell, minus what the double delivered.
- **lost:** in a suppressed true fallback, or a B check where the helper had already stopped, the slot stays empty and
  its recoveries are lost.
- **net** in percentage points of group PRR: (gained − lost) / (TBs × 8 members), over all runs.
- **saved:** fallback transmissions withheld, which also means less interference for other vehicles.

The helper-alone counterfactual comes from A checks, which saw less interference than B checks. If anything it
overstates the gain of avoiding a double."""))
    C.append(code(r"""grid = np.arange(-112, -59, 1.0)
F = pd.DataFrame([R.evaluate(d, R.rule_suppress(d, 'fixed', x), ra) | {'x': x} for x in grid])
fig, ax = plt.subplots(1, 3, figsize=(12, 3.3))
ax[0].plot(F.true_fb_lost / F.true_fb, F.doubles_avoided / F.doubles, '-o', ms=2, color='k')
for x in (-105, -95, -85, -75):
    r = F[F.x == x].iloc[0]; ax[0].annotate(f'{x:.0f}', (r.true_fb_lost / r.true_fb, r.doubles_avoided / r.doubles), fontsize=7, xytext=(4, -8), textcoords='offset points')
ax[0].set_xlabel('true fallbacks suppressed (share of C)'); ax[0].set_ylabel('doubles avoided (share)'); ax[0].set_title('Trade-off (labels: threshold, dBm/RB)')
ax[1].plot(F.x, F.pairs_gained, color=COL['B'], label='pairs gained (doubles avoided)')
ax[1].plot(F.x, -F.pairs_lost, color=COL['C'], label='pairs lost (fallbacks suppressed)')
ax[1].plot(F.x, F.net_pairs, color='k', lw=1.6, label='net'); ax[1].axhline(0, color='grey', lw=0.8)
ax[1].set_xlabel('threshold (dBm per RB)'); ax[1].set_ylabel(f'(TB, member) pairs, {d.cell.nunique()} runs'); ax[1].legend(fontsize=7); ax[1].set_title('Delivered pairs')
ax[2].plot(F.tx_saved, F.net_pp, '-o', ms=2, color='k')
for x in (-105, -95, -85, -75):
    r = F[F.x == x].iloc[0]; ax[2].annotate(f'{x:.0f}', (r.tx_saved, r.net_pp), fontsize=7, xytext=(4, 2), textcoords='offset points')
ax[2].set_xlabel(f'fallback transmissions withheld ({d.cell.nunique()} runs)'); ax[2].set_ylabel('group PRR change (pp)'); ax[2].set_title('Resources vs reliability')
fig.tight_layout(); fig.savefig(f'{FIG}/fixed_threshold.pdf'); plt.show()
F[F.x.isin([-110, -105, -100, -95, -90, -85, -80, -75, -70])].set_index('x')[
    ['doubles_avoided', 'doubles', 'true_fb_lost', 'true_fb', 'pairs_gained', 'pairs_lost', 'net_pp', 'tx_saved']].round(3)"""))
    C.append(md(r"""**Seed to seed.** The same evaluation per seed (six cells each), to see whether any seed favours a gate."""))
    C.append(code(r"""S = pd.DataFrame([{'seed': s, 'x': x, 'net_pp': R.evaluate(g, R.rule_suppress(g, 'fixed', x), ra)['net_pp']}
                  for s, g in d.groupby('seed') for x in (-105, -95, -85, -80, -75, -70)])
S = S.pivot(index='seed', columns='x', values='net_pp')
pd.concat([S, S.agg(['mean', 'std', 'min', 'max'])]).round(3)"""))
    C.append(md(r"""## 4. Dynamic thresholds

Two ways to adapt the threshold, both causal (they use only what the leader knew before the check):
- **helper-relative:** withhold when RSSI ≥ P̂_helper − δ, where P̂_helper is an EWMA (α = 0.1, in dB) of the
  helper's PSSCH-RSRP over the earlier checks in which the leader decoded that helper. This follows the helper's
  link, which varies a lot on the roundabout. Before the first estimate it falls back to −95 dBm.
- **per context:** the best fixed threshold chosen separately for each route and traffic type. This is an oracle,
  an upper bound on any context-aware rule.

A dynamic rule is worth having only if it moves the trade-off curve: more doubles avoided for the same true
fallbacks lost."""))
    C.append(code(r"""deltas = np.arange(-10, 31, 1.0)
Rl = pd.DataFrame([R.evaluate(d, R.rule_suppress(d, 'relative', x), ra) | {'x': x} for x in deltas])
fig, ax = plt.subplots(1, 2, figsize=(9, 3.3))
ax[0].plot(F.true_fb_lost / F.true_fb, F.doubles_avoided / F.doubles, color='k', label='fixed threshold')
ax[0].plot(Rl.true_fb_lost / Rl.true_fb, Rl.doubles_avoided / Rl.doubles, color='#F58518', label='helper-relative (RSRP − δ)')
ax[0].set_xlabel('true fallbacks suppressed (share)'); ax[0].set_ylabel('doubles avoided (share)'); ax[0].legend(fontsize=7)
ax[0].set_title('Trade-off: fixed vs helper-relative')
ax[1].plot(F.tx_saved, F.net_pp, color='k', label='fixed')
ax[1].plot(Rl.tx_saved, Rl.net_pp, color='#F58518', label='helper-relative')
ax[1].axhline(0, color='grey', lw=0.8); ax[1].set_xlabel('fallback transmissions withheld'); ax[1].set_ylabel('group PRR change (pp)')
ax[1].legend(fontsize=7); ax[1].set_title('Resources vs reliability')
fig.tight_layout(); fig.savefig(f'{FIG}/dynamic_threshold.pdf'); plt.show()
x = d[d.cls.isin(['B', 'C'])]
print('RSSI - helper reference (dB), by class:'); print((x.rssi - x.helper_ref).groupby(x.cls).describe().round(1))"""))
    C.append(code(r"""# per-context oracle: the best threshold per route and traffic, "none" = no gate
rows = []
for ctx, g in d.groupby('ctx'):
    rag = {ctx: ra.get(ctx, np.nan)}
    best = max([(np.inf, R.evaluate(g, pd.Series(False, index=g.index), rag))] +
               [(x, R.evaluate(g, R.rule_suppress(g, 'fixed', x), rag)) for x in grid], key=lambda p: p[1]['net_pairs'])
    e95 = R.evaluate(g, R.rule_suppress(g, 'fixed', -95), rag)
    rows.append(dict(route=g.route.iloc[0], traffic=g.traffic.iloc[0], B=(g.cls == 'B').sum(), C=(g.cls == 'C').sum(),
                     best_threshold='none' if not np.isfinite(best[0]) else best[0], best_net_pp=best[1]['net_pp'],
                     net_pp_at_minus95=e95['net_pp'], withheld_at_minus95=e95['tx_saved']))
pd.DataFrame(rows).round(3)"""))
    C.append(md(r"""## 5. Decision

**No RSSI gate for reliability, fixed or dynamic.** Over 60 runs (10 seeds), every threshold lowers the group PRR:
- **Fixed:** −0.24 pp at −80 dBm per RB, −0.69 pp at −95 dBm. The sign holds on every seed: at −95 dBm the
  per-seed change ranges from −1.18 to −0.41 pp, at −80 dBm from −0.45 to −0.13 pp. The loss only shrinks towards
  zero as the threshold rises and the gate stops acting.
- **Dynamic:** the helper-relative rule traces about the same trade-off curve as a fixed threshold (no better than
  −0.24 pp), so adapting the threshold to the helper's RSRP does not help. The per-context oracle picks no gate in
  4 of 6 contexts. In the other two (Roundabout W and E, periodic) its best threshold gains under 0.01 pp.
- **Why:** each avoided double gains little. The two copies share the TB, and members recover the TB about as often
  as from a single copy: pooled, 34 % of the members in need decode the double, against 31 % for a helper copy
  alone and 40 % for a true fallback. Each suppressed true fallback loses the last chance of about 40 % of its
  members in need. Interference on the handed subchannels is too common for an energy test to tell "helper
  present" from "someone else present".
- **Where a double does hurt:** on the highway the helper alone recovers far more than the double (aperiodic 0.57
  against 0.31, periodic 0.74 against 0.44). Even there, the oracle prefers no gate: the true fallbacks it would
  suppress (998 C checks against 274 B on highway aperiodic) are worth more. On the roundabouts the double recovers
  more than the helper alone.

**If the objective were resources or interference.** A gate saves transmissions: −80 dBm per RB withholds 529
fallback copies over 60 runs (23 305 TBs) for −0.24 pp; −70 dBm withholds 123 for −0.04 pp. That is a separate
trade-off and should be stated as one. It needs no dynamic rule.

**For the paper.** Report the double transmissions as a measured limitation: 22 % of fallbacks with 60 vehicles,
for both traffic types. Add that an energy gate does not pay off, because doubles cost little reliability and true
fallbacks are valuable. The better levers are those that make the helper's SCI decodable at the leader, not
detection after the fact.

**Limits of this analysis.**
- The helper-alone counterfactual is estimated from other checks (A checks, which saw less interference), not
  simulated. If anything it overstates the gain of a gate.
- Recoveries are counted at s3 only.
- Gating the fallback in the simulator and measuring PRR directly would be the stronger test. Given a loss on every
  seed, it is not needed for the decision."""))
    return C


def main():
    nb = nbf.v4.new_notebook()
    nb['cells'] = cells()
    nb['metadata']['kernelspec'] = {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}
    nbf.write(nb, os.path.join(HERE, 'RSSI_FALLBACK.ipynb'))
    print('wrote RSSI_FALLBACK.ipynb')


if __name__ == '__main__':
    main()
