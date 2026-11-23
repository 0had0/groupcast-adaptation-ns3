# Who was the helper? (R4, R4.1, R4.2, R5, R6; full campaign, KPI window only)

Rule (helper_select.cc, policy `kuo` = Kuo eq. 12-13 "maxmean once"):
- per TB, at grant publication, the leader takes the newest CAM positions (100 ms period, 28-50 ms old),
- predicts each link's success from straight-line distance only: TR 37.885 LOS / NLOSv mixture
  (highway or urban model), shadowing, success if path loss < PL_max,
- PL_max = 23 dBm - 10 log10(#PRB) - noise(1 PRB, NF 9 dB) - 8 dB  -> 114.4 dB (1 subch) ... 107.4 dB (5 subch),
- picks the member maximising the mean predicted group PRR; ties -> stronger direct link, then lower index.
- No buildings, no interference in the prediction. One helper per TB, it gets both handed slots.

Findings
- The choice does not depend on the scheme: matched TBs give the same helper in R4..R6 in 99-100 % of cases
  (100 % when the TB size is the same). Differences come only from TB size.
- Highway: member 3 (105 m) for TBs of 2-5 subchannels, member 2 for 1-subchannel TBs. ~97-100 % member 3.
- Roundabout W: member 2 or 3; periodic 88/12, aperiodic 50/50 (bigger TBs -> lower PL_max -> member 3).
- Roundabout E: members 1-4 (shares ~15/26-57/17-49/9-12 %), changes as the platoon wraps around the ring.
- Switches are rare: 0.06-0.27 per second (one helper kept 4-20 s on average).
- The model predicts a best group PRR of 0.998-0.9999 in every case (real: 0.57-0.99) and the top two candidates
  differ by less than 0.001. In this 280 m platoon the selector sees "everyone in range", so the pick is close to a tie.

Files: hsel_runs.csv (per run), hsel_summary.csv, hsel_agreement_vs_R4.csv, hsel_tb.csv.gz (per TB),
helper_timeline_{0,60}cars.png, helper_vs_tb_size.png; scripts hsel_agg.py, hsel_plots.py.
