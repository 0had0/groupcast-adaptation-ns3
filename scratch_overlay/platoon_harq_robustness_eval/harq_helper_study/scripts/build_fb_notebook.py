#!/usr/bin/env python3
"""Build notebooks/R4C_FALLBACK.ipynb: what the leader fallback adds to the blind helper with soft combining.
"R4 + C + fallback" (R4_bsc_fb, campaign v3_fb, out/remote_v3_fb) against R4+C (R4_bsc) and R6 of campaign v3
(out/remote_v3), paired by route, traffic, load and seed (same commands except --blindHelperFallback=true).

  python3 scripts/build_fb_notebook.py
  python3 -m jupyter nbconvert --to notebook --execute --inplace notebooks/R4C_FALLBACK.ipynb
"""
import nbformat as nbf
import nb_layout

md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell


def cells():
    C = []
    C.append(md(r"""# R4 + C + fallback: the leader fallback with the blind helper

**The arm.** R4+C (`R4_bsc`): the leader sends the first copy, every later reserved slot of the TB is handed to the
Kuo helper, which relays a decoded TB blindly in each of them; receivers soft-combine the copies. No HARQ feedback.
**R4 + C + fallback** (`R4_bsc_fb`, `--blindHelperFallback=true`) adds R6's fallback: after each of its own
transmissions the leader listens in the helper's next handed slot; if it decodes no SCI of the helper there (the
helper missed the TB), it takes back the following handed slot and retransmits itself (`_ltx.csv` origin 2). Without
feedback this is the only takeover; R6 also takes over on a helper NACK and stops on all-ACK.

**Question.** How much of R6's gain over R4+C is the takeover alone, and what does it cost?

**Data.** `out/remote_v3_fb` (campaign `v3_fb`, 300 runs) and `out/remote_v3` (R4_bsc, R6): 3 routes × 2 traffic types
× 0 / 15 / 30 / 45 / 60 background vehicles × 10 seeds; the commands differ only in the new flag, so runs pair by
seed. Intervals: 95 % Student-t over the paired differences; `*` = excludes 0."""))
    C.append(code(r"""import glob, os, re, numpy as np, pandas as pd, matplotlib.pyplot as plt
from scipy import stats
import plot_prr_vs_cbr as P
FIG = 'figures/r4c_fallback'; os.makedirs(FIG, exist_ok=True)
NBG = {'none': 0, 'n15': 15, 'n30': 30, 'n45': 45, 'moderate': 60}
COLS = ['prr_mean', 'prr_min', 'tx_per_pkt', 'cbr_occupied', 'pkts', 'leader_tx', 'helper_tx', 'relay_sent',
        'fallback_check', 'fallback_heard', 'fallback_tx', 'fallback_no_slot', 'fallback_late', 'fallback_late_no_slot',
        'helper_assigned', 'helper_decoded', 'helper_sci_missed', 'res_used_leader', 'res_used_helper',
        'leader_fallback']
def load(folder, arm):
    rows = []
    for f in glob.glob(f'out/{folder}/{arm}/*_run.csv*'):
        sc, _, tr, bg, s = os.path.basename(f).split('_run.csv')[0].split('_')
        r = pd.read_csv(f).iloc[0]
        rows.append({k: r[k] for k in COLS if k in r} | dict(arm=arm, sc=sc, tr=tr, nbg=NBG[bg], seed=int(s[1:])))
    return pd.DataFrame(rows)
fb = load('remote_v3_fb', 'R4_bsc_fb')
ref = pd.concat([load('remote_v3', 'R4_bsc'), load('remote_v3', 'R6')])
assert len(fb), 'no R4_bsc_fb runs in out/remote_v3_fb: copy the campaign first'
print(len(fb), 'R4 + C + fallback runs;', fb.groupby(['sc', 'tr', 'nbg']).size().describe()[['min', 'max']].to_dict(),
      'seeds per cell; leader_fallback flag', sorted(fb.leader_fallback.unique()))
K = ['sc', 'tr', 'nbg', 'seed']
J = fb.merge(ref[ref.arm == 'R4_bsc'], on=K, suffixes=('', '_r4')).merge(ref[ref.arm == 'R6'], on=K, suffixes=('', '_r6'))
J['cbr'] = J.cbr_occupied
print(len(J), 'paired runs')
def ci(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    m = x.mean(); h = stats.t.ppf(.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x)) if len(x) > 1 else np.nan
    return m, h
def fmt(x, f=1):
    m, h = ci(f * x); return f'{m:+.2f} ± {h:.2f}' + ('*' if abs(m) > h else ' ')"""))
    C.append(md(r"""## 1. Fallback activity

Per packet: fallback checks (a handed slot was watched), checks where the helper's SCI was heard, takeovers (the
leader sent in the next handed slot), checks with no handed slot left, and late checks; helper SCI misses as a
share of TBs. Mean over seeds."""))
    C.append(code(r"""A = fb.assign(**{c + '/pkt': fb[c] / fb.pkts for c in ['fallback_check', 'fallback_heard', 'fallback_tx',
                                                      'fallback_no_slot', 'fallback_late']},
              helper_relays_pkt=fb.helper_tx / fb.pkts, leader_tx_pkt=fb.leader_tx / fb.pkts)
cols = [c for c in A.columns if c.endswith('/pkt') or c.endswith('_pkt')]
with pd.option_context('display.max_rows', 100):
    display(A.groupby(['sc', 'tr', 'nbg'])[cols].mean().round(3))"""))
    C.append(md(r"""## 2. Takeover gain: R4 + C + fallback − R4+C (paired by seed)

Group PRR (pp), worst-member PRR (pp), transmissions per packet (leader + helper)."""))
    C.append(code(r"""rows = []
for (sc, tr, n), q in J.groupby(['sc', 'tr', 'nbg']):
    rows.append({'route': P.SCN[sc], 'traffic': P.TRN[tr], 'bg': n, 'CBR (%)': round(100 * q.cbr.mean()),
                 'Δ prr_mean (pp)': fmt(q.prr_mean - q.prr_mean_r4, 100),
                 'Δ prr_min (pp)': fmt(q.prr_min - q.prr_min_r4, 100),
                 'Δ tx/pkt': fmt(q.tx_per_pkt - q.tx_per_pkt_r4)})
G = pd.DataFrame(rows).set_index(['route', 'traffic', 'bg'])
with pd.option_context('display.max_rows', 100):
    display(G)"""))
    C.append(md(r"""## 3. What is left to R6: R6 − R4 + C + fallback (paired by seed)

The part of R6's gain over R4+C that the fallback alone does not give: feedback-driven relaying (stop on all-ACK),
takeover on a helper NACK, and Option 2 retransmissions."""))
    C.append(code(r"""rows = []
for (sc, tr, n), q in J.groupby(['sc', 'tr', 'nbg']):
    t6 = q.prr_mean_r6 - q.prr_mean_r4
    rows.append({'route': P.SCN[sc], 'traffic': P.TRN[tr], 'bg': n,
                 'R6 − R4+C (pp)': fmt(t6, 100),
                 'R6 − R4+C+fb (pp)': fmt(q.prr_mean_r6 - q.prr_mean, 100),
                 'share of R6 gain from fallback (%)': round(100 * (q.prr_mean - q.prr_mean_r4).mean() / t6.mean())
                 if abs(t6.mean()) > 1e-3 else np.nan,
                 'R6 − R4+C+fb worst (pp)': fmt(q.prr_min_r6 - q.prr_min, 100),
                 'R6 − R4+C+fb tx/pkt': fmt(q.tx_per_pkt_r6 - q.tx_per_pkt)})
with pd.option_context('display.max_rows', 100):
    display(pd.DataFrame(rows).set_index(['route', 'traffic', 'bg']))"""))
    C.append(md("## 4. Against the pool CBR (R3, R4+C, R4 + C + fallback, R6)"))
    C.append(code(r"""D = P.load(); M = P.means(D)
P.grid(M, 'prr', 'group PRR (%)', None); plt.savefig(f'{FIG}/prr_vs_cbr.pdf'); plt.show()
P.grid(M, 'worst', 'worst-member PRR (%)', None); plt.savefig(f'{FIG}/worst_vs_cbr.pdf'); plt.show()"""))
    C.append(code(r"""fig, axs = plt.subplots(3, 2, figsize=(8.2, 8.6), sharex=True)
for i, sc in enumerate(P.SCN):
    for j, tr in enumerate(P.TRN):
        a = axs[i, j]
        for k, f, lab, col in [('prr_mean', 100, 'Δ group PRR', '#8e24aa'), ('prr_min', 100, 'Δ worst member', '#4a148c')]:
            pts = [(100 * q.cbr.mean(), *ci(f * (q[k] - q[k + '_r4']))) for n, q in
                   J[(J.sc == sc) & (J.tr == tr)].groupby('nbg')]
            x, m, h = map(np.array, zip(*pts))
            a.errorbar(x, m, yerr=h, marker='o', ms=4, capsize=2, color=col, label=lab, ls='-' if k == 'prr_mean' else '--')
        a.axhline(0, color='k', lw=.6); a.grid(alpha=.3); a.set_title(f'{P.SCN[sc]}, {P.TRN[tr]}')
        if i == 2: a.set_xlabel('pool CBR (%)')
        if j == 0: a.set_ylabel('R4+C+fallback − R4+C (pp)')
axs[0, 0].legend(fontsize=7)
fig.tight_layout(); fig.savefig(f'{FIG}/fallback_gain_vs_cbr.pdf'); plt.show()"""))
    C.append(md(r"""## 5. Findings

Campaign `v3_fb` (commit 3d66267): 300 / 300 runs, 10 seeds per cell, `leader_fallback` = 1 in every run.

**The takeover gain (R4 + C + fallback − R4+C).**
- The fallback never hurts the group PRR, and the gain grows with the load. **Highway:** +2.3 pp (aperiodic) and
  +1.2 pp (periodic) at 0 vehicles, rising to +10.6 / +5.2 pp at CBR 0.70. **Roundabouts, aperiodic:** +0.6 to
  +0.9 pp (not significant) at 0 vehicles, +6.0 / +6.1 pp at CBR 0.70. **Roundabouts, periodic:** at most +3.6 pp,
  and on W it is significant only at 30 vehicles.
- The load makes the helper miss more TBs, so the leader takes over more often. On the highway, aperiodic,
  takeovers per packet rise from 0.10 at 0 vehicles to 0.42 at 60, and the share of checks in which the helper is
  heard falls from 90 % to 54 %. The fallback mends exactly the losses that make R4+C the most load-sensitive scheme.
- **Worst member:** clear gains on the highway (+4.7 to +10.8 pp, every load significant). On the roundabouts the
  change is within ±3.3 pp and never significant. A likely reason: the blocked member does not hear the leader's
  own copy either, so taking the slot back does not help it.
- **Cost:** +0.03 to +0.39 transmissions per packet, growing with the load (largest on the highway, aperiodic).
  R4 + C + fallback uses 2.6–2.9 transmissions per packet against R6's 1.7–2.7. R6 is cheaper everywhere, except
  with aperiodic traffic on roundabout W at 45–60 vehicles and on E at 60, where the two are equal within the
  interval.

**How much of R6's gain over R4+C the fallback alone gives.**
- **Highway:** most of it, 61–93 %. At CBR 0.70 the fallback closes 10.6 of R6's 17.3 pp (aperiodic) and 5.2 of
  6.6 pp (periodic). With periodic traffic and up to 30 vehicles, what is left to R6 is not significant.
- **Roundabouts:** less, 14–76 %, and only 14–53 % at 0 vehicles. R6 stays significantly ahead with aperiodic
  traffic at every load (+2.4 to +6.3 pp in group PRR, +4.3 to +9.5 pp in the worst member).
- What the fallback does not give is the feedback. R6 retransmits only on a NACK and stops on an all-ACK, which
  saves transmissions where the channel is good; the remaining worst-member gap suggests it also serves the
  blocked members better.

**Against R3.**
- On the roundabouts R4 + C + fallback beats R3 at every CBR (65.2 vs 59.1 % W, 71.1 vs 64.8 % E at CBR 0.70,
  aperiodic).
- On the highway with aperiodic traffic it still falls below R3 under load (78.9 vs 85.5 % at CBR 0.70). Its blind
  relays still add collisions that R3's Option 2 avoids; R6 matches R3 there (85.6 %).

Fallback checks per packet rise from about 1.7 to 2.0–2.2 on the roundabouts at 45–60 vehicles, and not on the
highway. This is not explained here."""))
    return C


def main():
    nb = nbf.v4.new_notebook()
    nb['cells'] = cells()
    nb['metadata']['kernelspec'] = {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}
    nbf.write(nb_layout.add_preamble(nb), nb_layout.notebook_path('R4C_FALLBACK.ipynb'))
    print('wrote R4C_FALLBACK.ipynb')


if __name__ == '__main__':
    main()
