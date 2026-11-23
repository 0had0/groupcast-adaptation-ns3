#!/usr/bin/env python3
"""Build notebooks/PRR_VS_CBR.ipynb: group and worst-member PRR against the measured pool CBR for R3, R4+C (R4_bsc)
and R6, every route and traffic type, campaign v3 (out/remote_v3). R4 + C + fallback (R4_bsc_fb, out/remote_v3_fb)
is added automatically once that campaign is copied. Plot code: scripts/plot_prr_vs_cbr.py.

  python3 scripts/build_cbr_notebook.py
  python3 -m jupyter nbconvert --to notebook --execute --inplace notebooks/PRR_VS_CBR.ipynb
"""
import nbformat as nbf
import nb_layout

md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell


def cells():
    C = []
    C.append(md(r"""# PRR against the pool CBR (campaign v3)

**Schemes.** Leader HARQ Option 2 (**R3**), the blind helper with soft combining (**R4+C**, `R4_bsc`: Kuo's helper
relays in every handed-over slot, receivers combine the blind copies) and the cooperative protocol (**R6**).
**R4 + C + fallback** (`R4_bsc_fb`) is drawn too once `out/remote_v3_fb` exists (campaign `v3_fb`).

**Data.** `out/remote_v3`: 3 routes × 2 traffic types × 0 / 15 / 30 / 45 / 60 background vehicles × 10 seeds per
scheme. Points: mean over seeds; bars: 95 % Student-t half-width on both axes.

**Pool CBR** = `cbr_occupied` of the run: the share of the pool's (slot, subchannel) cells used by any vehicle in the
KPI window, each cell counted once. The run's `cbr` column is the *offered* load (every transmitted
subchannel-slot, overlapping transmissions counted more than once); it reaches 0.98–1.07 at 60 vehicles and is not a
CBR. The platoon's own share is under 1 % of the pool, so the CBR is the same for every scheme at a given load. This
is a global occupancy count, not the 3GPP CBR measured by one UE (S-RSSI above a threshold over 100 ms).

**Δ** panels: change against the scheme's own value at 0 vehicles. The 0-vehicle and loaded runs use different
SUMO traces, so the interval of a Δ combines both intervals (unpaired)."""))
    C.append(code(r"""import pandas as pd
import plot_prr_vs_cbr as P
D = P.load()
M = P.means(D)
print(len(D), 'runs;', 'arms:', ', '.join(P.ARMS[a][0] for a in M.arm.unique()))
print('seeds per point:', sorted(D.groupby(['arm', 'sc', 'tr', 'nbg']).size().unique()))
cbr = M.groupby('nbg').cbr.agg(['mean', 'min', 'max']).mul(100).round(1)
cbr.join(M.groupby('nbg').cbr_offered.mean().mul(100).round(1).rename('offered load (%)')) \
   .rename(columns=lambda c: c if 'offered' in c else f'pool CBR {c} (%)')"""))
    C.append(md("## 1. Group PRR (mean member PRR)"))
    C.append(code(r"""P.grid(M, 'prr', 'group PRR (%)', 'prr_vs_cbr');"""))
    C.append(code(r"""P.grid(M, 'dprr', 'Δ group PRR vs 0 vehicles (pp)', 'dprr_vs_cbr', hline=0);"""))
    C.append(md("## 2. Worst receiver (worst member PRR)"))
    C.append(code(r"""P.grid(M, 'worst', 'worst-member PRR (%)', 'worst_vs_cbr');"""))
    C.append(code(r"""P.grid(M, 'dworst', 'Δ worst-member PRR vs 0 vehicles (pp)', 'dworst_vs_cbr', hline=0);"""))
    C.append(md(r"""## 3. Tables

Mean group and worst-member PRR (%) per route, traffic and load, and the drop from 0 to 60 vehicles (pp)."""))
    C.append(code(r"""lab = {a: P.ARMS[a][0] for a in P.ARMS}
T = M.assign(scheme=M.arm.map(lab), route=M.sc.map(P.SCN), traffic=M.tr.map(P.TRN), CBR=(100 * M.cbr).round(0))
with pd.option_context('display.max_rows', 100):
    display(T.pivot_table(index=['route', 'traffic', 'nbg'], columns='scheme', values=['prr', 'worst']).round(1))
    display(T[T.nbg == 60].pivot_table(index=['route', 'traffic'], columns='scheme',
                                       values=['dprr', 'dworst']).round(1))
M.round(4).to_csv(f'{P.FIG}/prr_vs_cbr.csv', index=False)"""))
    C.append(md(r"""## 4. Findings (pool CBR 0 → 0.70)

**The load axis.** 0 / 15 / 30 / 45 / 60 vehicles give a pool CBR of 0.01 / 0.23–0.24 / 0.42–0.43 / 0.58–0.59 /
0.69–0.70 (offered load 0.01 / 0.25 / 0.49 / 0.74 / 0.99). Earlier notes quoting "CBR 0.99" at 60 vehicles used the
offered load.

**Group PRR.**
- R4+C is the most load-sensitive scheme: its blind copies add collisions. Aperiodic traffic, 0 → 60 vehicles:
  −28 pp on the highway (96.7 → 68.3 %), −16 to −21 pp on the roundabouts; at CBR 0.70 it falls to R3's level on both
  roundabouts (59.1 vs 59.1 % W, 65.1 vs 64.8 % E), having lost its blockage advantage.
- R3 is the least sensitive on the roundabouts (−3 to −5 pp) but starts low there (64–75 %): it cannot get around the
  buildings.
- R6 tracks R3 on the highway (aperiodic 85.6 vs 85.5 % at CBR 0.70) and loses more than R3 on the roundabouts with
  aperiodic traffic (−10 to −11 pp against −5) but stays on top at every CBR (10–13 pp above both at 0.70).
- Periodic traffic suffers far less for every scheme (reservations are announced, so the background avoids them).

**Worst receiver.**
- R6 has the highest mean worst member in every route, traffic and load (not always significantly against R4+C
  with periodic traffic on the roundabouts; e.g. 30.9 / 49.6 % at 60 vehicles, aperiodic, W / E,
  against 23.9 / 39.2 % for R4+C and 15.1 / 22.3 % for R3).
- The load costs the worst member more than the group: on the highway, aperiodic, 0 → 60 vehicles R3 −27.5, R4+C
  −33.6, R6 −24.0 pp. On the roundabouts R4+C and R6 lose 6–20 pp, R3 only 2–7 pp, from a much lower start
  (18–34 %): its worst member is limited by blockage, not by the load.
- R4+C keeps most of R6's worst-member level with periodic traffic (40.4 vs 42.4 % at 60 vehicles, W) but not with
  aperiodic traffic (23.9 vs 30.9 %, E: 39.2 vs 49.6 %).

**R4 + C + fallback** (campaign `v3_fb`; paired analysis in `R4C_FALLBACK.ipynb`).
- It lies between R4+C and R6 everywhere and removes most of R4+C's load sensitivity. At CBR 0.70 with aperiodic
  traffic its group PRR is 78.9 % on the highway, 65.2 % on W and 71.1 % on E, against 68.3 / 59.1 / 65.1 % for
  R4+C and 85.6 / 69.8 / 77.4 % for R6.
- It stays below R3 on the highway with aperiodic traffic (78.9 vs 85.5 %) and above R3 on both roundabouts.
- **Worst member:** it gains on the highway (64.3 vs 56.4 % at 60 vehicles, aperiodic) but stays at R4+C's level on
  the roundabouts (25.6 vs 23.9 % W, 40.1 vs 39.2 % E), well below R6.

**R4 + C + fallback, spaced** (`R4_bsc_fbs`, campaign `v3_fbs`, commit a54a785; the handed slots keep the PSFCH
minimum gap so the leader can always take the second one back). Paired over the same 10 seeds:
- **Highway, aperiodic:** the spacing gain grows with the load, from +0.2 pp at 0 vehicles to +4.3 ± 2.2 pp at CBR 0.70
  (83.2 vs 78.9 %), which closes about two thirds of the gap to R6 (85.6 %, still −2.4 ± 1.3 pp). With periodic traffic
  the arms are within 1 pp of each other.
- **Roundabouts:** spacing is worth 0 to +2 pp, never significant (largest +2.0 ± 2.0 pp, E aperiodic at 60 vehicles).
  R6 stays 2–4 pp ahead with aperiodic traffic at every load (W 60 vehicles: 66.2 vs 69.8 %, −3.6 ± 1.8 pp).
- **Worst member:** spacing does not move it on the roundabouts; R6 keeps a 4–9 pp lead with aperiodic traffic
  (E at 60 vehicles: 40.6 vs 49.6 %, −9.0 ± 2.6 pp).
- So the slot spacing explains the R6 gap when the helper is silent (helper-miss sweep), and part of it under load
  on the highway, but not the remaining gap on the roundabouts without forced helper misses."""))
    C.append(code(r"""if 'R4_bsc_fb' in M.arm.values:
    print('R4 + C + fallback is included above; see the fallback notebook for the paired takeover gain.')
else:
    print('R4 + C + fallback: not yet copied (out/remote_v3_fb). Rebuild after copying to include it.')"""))
    return C


def main():
    nb = nbf.v4.new_notebook()
    nb['cells'] = cells()
    nb['metadata']['kernelspec'] = {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}
    nbf.write(nb_layout.add_preamble(nb), nb_layout.notebook_path('PRR_VS_CBR.ipynb'))
    print('wrote PRR_VS_CBR.ipynb')


if __name__ == '__main__':
    main()
