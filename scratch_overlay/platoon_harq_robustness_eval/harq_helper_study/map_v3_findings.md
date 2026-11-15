## 10. Findings (2026-11-15, `remote_v3` + `remote_v3_map`, commit 32e29dd)

**Checks passed:**
- 3900 runs, 10 seeds in every cell, every arm at every load.
- Both folders are at commit `32e29dd`, which contains `stale-ndi-fix`.
- All 1500 map runs have the same command as their Kuo run, except `--helperSelect`.
- `helper_select` matches the selector in every run.

**1. On the curve, map beats Kuo for every helper arm; on the highway they are the same.** This is the v2 result
again, with slightly smaller gains.

Group PRR, map − Kuo, in the 20 curve cells (W and E routes):

| arm | v3 | cells better | v2 |
|---|---|---|---|
| R6 | +0.05 to +0.12 | 20 / 20 | +0.05 to +0.16 |
| R5 | +0.04 to +0.11 | 20 / 20 | +0.04 to +0.12 |
| R4 | +0.03 to +0.13 | 20 / 20 | +0.03 to +0.17 |
| R4.2 | +0.02 to +0.14 | 20 / 20 | +0.01 to +0.19 |
| R4.1 | +0.02 to +0.13 | 17 / 20, the rest ties | +0.02 to +0.16 |

- **Where the gain is largest:** on the W route, aperiodic traffic, little load: +0.11 to +0.14 at 0 vehicles.
  It shrinks with load but stays clear at 60 vehicles. Coop at 60 vehicles: +0.10 on W aperiodic, +0.08 on W
  periodic, +0.05 on E aperiodic.
- **Worst-member PRR moves more:** on the curve it is better in 20 / 20 cells for every arm. Coop: +0.16 to +0.31.
- **Highway:** map names member 3 in every TB. Kuo names member 3 in 84 % of the TBs with aperiodic traffic
  (member 2 in 16 %) and in 97 % with periodic traffic. Every highway cell is within ±0.018.
  - One of the 150 cells is significantly worse: R4.1, highway aperiodic, 0 vehicles, −0.003. At 95 %, chance
    alone can produce that.

**2. Coop stays the best scheme, and its lead over Opt 2 grows on the curve.**
- **Rank:** Coop has the highest group PRR of the five helper arms in all 30 cells, with either selector.
- **Coop − Opt 2 on the curve:**
  - roundabout W: +0.11 to +0.18 with Kuo, +0.20 to +0.28 with map;
  - roundabout E: +0.13 to +0.19 with Kuo, +0.17 to +0.27 with map.
- **Margin over the other helper arms:** on the curve, Coop with map is at least 0.020 above Coop-1 with map, and
  at least 0.024 above the arms without takeover.
- **The v2 weak spot is gone in v3.** On the loaded highway with aperiodic traffic, v2 had Coop at 60 vehicles
  below Opt 2 (0.721 / 0.725 against 0.745). In v3, Coop is at or slightly above Opt 2 in every highway cell, by
  +0.001 to +0.014 with either selector. At 60 vehicles it is 0.858 with map, 0.856 with Kuo, 0.855 for Opt 2.
- **The arms without takeover still lose on the loaded highway:** −0.15 to −0.19 against Opt 2 with aperiodic
  traffic at 60 vehicles, with either selector. On roundabout E aperiodic at 60 vehicles, map lifts them from
  −0.023 to +0.001 (Kuo) to −0.004 to +0.044 (map) against Opt 2.

**3. Map also costs less spectrum on the curve and shortens the time disconnected.**
- **Spectrum:** `res_deliv` (subchannel-slots per delivered packet and member) is lower on the curve in 20 / 20
  cells for every arm. Coop: −0.10 to −0.28. More packets are delivered for about the same transmissions.
- **Time disconnected (`disc_frac`) on the curve:**
  - Coop and Coop-1: lower in 20 / 20 cells; Coop by 0.03 to 0.15.
  - Arms without takeover: lower in 13 (R4.1) to 17 (R4) of 20 cells, the rest ties.
- **Mean disconnection time on the curve:** lower in 20 / 20 cells for every arm, by 0.05 to 0.15 s (Coop) and
  0.06 to 0.14 s (Kuo). The only significantly worse cells are two R4.1 cells on the highway.

**4. Why: map moves the helper back along the platoon.**
- **Which member:** on roundabout W, Kuo names member 2 (69–88 % of TBs) or 3; map spreads the choice over members
  2–6 (2: 37–40 %, 3: 22–25 %, 4–6: 10–15 % each). On roundabout E, Kuo names members 1–4 and map names members 2–6,
  most often member 5 (29–30 %).
- **How often it changes:** on the curve, map changes helper 0.49–1.17 times per second. Kuo changes 0.07–0.17
  times per second with periodic traffic. With aperiodic traffic Kuo changes 1.5–2.1 times per second, which v2
  did not show. The likely cause, not checked here, is that the TB width now varies with the dynamic grants.
- **Periodic traffic:** the map helper misses copy 1 more often than Kuo's (roundabout W: 0.13–0.21 against
  0.07–0.14), yet the PRR is far higher. Map gives up some reliability on the leader → helper link to get a clear
  helper → tail link.
- **Aperiodic traffic on roundabout W:** the map helper also misses copy 1 less often at low load (0.09 against
  0.22 at 0 vehicles).

**Caveats**
- **Best case for a map choice:** the selector has exact CAM positions (`posErrM` = 0, positions about 30–50 ms
  old) and a perfect building map. A run with position error (`--posErrM`) would test how robust it is.
- **Timing of the choice:** the helper is still chosen before copy 1. A choice made after copy 1 is not tested.
