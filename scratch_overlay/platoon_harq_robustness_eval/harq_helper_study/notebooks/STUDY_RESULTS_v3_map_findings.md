## Findings: map vs Kuo (2026-11-15, campaign v3, 1500 paired runs)

Counts below are cells out of 20 on the curve (both roundabout routes x 2 traffic types x 5 loads) and 10 on the
highway, per helper arm; *better* / *worse* = the paired 95 % CI excludes 0. Full table:
`out/study_v3_map_cmp.csv` and the verdict-count table above.

**1. On the curve, map is better than Kuo on every reliability, disconnection and freshness metric, for every
helper arm.**
- Group PRR: better in 20 / 20 cells for R4, R4.2, R5 and R6, in 17 / 20 for R4.1 (the rest ties). R6: +0.045 to
  +0.120 (+6 to +16 %).
- Worst-member PRR: better in 20 / 20 cells for every arm. R6: +0.16 to +0.32 (+31 to +78 %).
- PRR within the PDB moves exactly like the PRR: the PDB is never what limits delivery.
- Disconnection time: lower in 19-20 / 20 cells for every arm. R6: mean DT 47-148 ms shorter (-16 to -36 %),
  worst member 160-555 ms shorter (-35 to -59 %).
- Age of information: lower in 20 / 20 cells for every arm. R6: worst receiver 249-800 ms lower (-57 to -81 %).

**2. Map also costs less spectrum per delivered byte with Option 2, but its packets arrive slightly later.**
- Transmitted subchannel-slots per kB (`ss_kB`) on the curve: lower for R6 in 20 / 20 cells (-3 to -13 %) and for
  R4.2 in 19 / 20. Fewer leader retransmissions and takeovers are needed when the helper reaches the tail. For the
  blind helper (R4) it is mixed: 10 better, 7 ties, 3 worse.
- Mean latency on the curve: higher with map in 13 / 20 cells for R4, R4.2, R5 and R6, by up to 0.9 ms (R6: at most
  +13 %), and never significantly lower. More packets reach the tail through the helper's copy or a later copy instead of being
  lost, and a later copy takes longer. The disconnection time and AoI, which count the losses, improve far more.

**3. On the highway the two selections are the same.**
- Map names member 3 in every TB; Kuo names member 3 in 84 % of TBs with aperiodic traffic (member 2 in 16 %) and
  in 97 % with periodic traffic.
- Every metric ties in nearly every highway cell. Across the 11 metrics x 5 arms (550 highway verdicts) there are
  7 *better* and 17 *worse*, all small (group PRR within ±0.018). That is about what chance gives at 95 % (5 % of 550 is 28).

**4. The cooperative protocol keeps its lead, and map widens it on the curve.**
- R6 - R3 (leader-only Option 2), group PRR, curve: +0.17 to +0.28 with map, +0.11 to +0.19 with Kuo.
- Highway: R6 is at or slightly above R3 with either selection (+0.001 to +0.014).
- R4.2 (helper + Option 2, no takeover) with map is still below R3 on the loaded highway (-0.15 at 60 vehicles,
  aperiodic) and is below R6 in every cell (by at least 0.017).

**5. Why: map moves the helper back along the platoon.**
- Roundabout W: Kuo names member 2 in 68-89 % of TBs (else 3); map spreads the choice over members 2-6.
- Roundabout E: Kuo names members 1-4; map names members 2-6, most often member 5 (27-30 %).
- With periodic traffic the map helper misses the first copy more often (roundabout W: 0.13-0.21 against
  0.08-0.14), yet every reliability metric is far better: map gives up some of the leader -> helper link to get a
  clear helper -> tail link.

**Caveats**
- **Best case for a map choice:** the selector has exact CAM positions (`posErrM` = 0) and a perfect building map.
- **Timing of the choice:** the helper is chosen before the first copy; a choice after the first copy is not tested.
- **R4_bsc** (Kuo blind helper with soft combining) has no map run, so the DT section shows R1_bsc only.
