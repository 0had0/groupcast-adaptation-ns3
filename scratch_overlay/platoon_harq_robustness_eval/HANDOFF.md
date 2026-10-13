# Handoff — Robustness of NR-V2X groupcast HARQ (Option 1) for a platoon: highway vs roundabout

Status (2026-10-10): **experiment complete.** 270/270 ns-3 runs, 0 failures, 3 seeds per cell,
independently reproduced on a second machine. Code on branch `platoon-harq-robustness-eval`
(pushed to `origin` = GitHub `0had0/groupcast-adaptation-ns3` and `imta` = IMT Atlantique GitLab
`h25houss/groupcast-adaptation-ns3`). Everything lives in
`scratch_overlay/platoon_harq_robustness_eval/` of the repo `groupcast-adaptation-ns3`.

**Update (2026-10-11).**
- Variant sweep done (`run_variants.py`: MCS 5/9, dedicated carrier, blind retransmission;
  IVD 30 m, N ∈ {0, 45, 60}): 215/216 runs on each machine, laptop and server agree (§6, §7.5).
- **RLC artifact found: `prr_pdb` and delays of every run so far are invalid; PRR is not
  affected** (§4.8). The program now defaults to t-Reordering = 0; delay results need a
  re-run (`results/campaign_rlc0`, `results/variants_rlc0`, §9).
- Open bug: truncated application packet in MCS 5 / roundabout / periodic2 (§8).
- **Branch `coop-harq-nr`** (2026-10-11): cooperative HARQ helper arms (`--helperMode`
  `none | leaderRes | ownRes`, `--helper`), NACK-triggered helper that overhears the PSFCH,
  soft combining in the leader's HARQ process, sensing attribution of collided attempts; NR
  patch byte-identical to stock with cooperation off. See README "Cooperative HARQ".

**Update (2026-10-12).** Cooperative-HARQ follow-up implemented and piloted (§7.8, README
"Cooperative HARQ: follow-up"): arms `split` and `additive`, corrected relay check, missed-NACK
KPI, collision breakdown, sensing-map divergence, link conditions, helper sweep and oracle
analysis, server driver `run_coop_followup.sh`. Found on the way: 5G-LENA dynamic grants
skip the sensing exclusion (§8); the leader NACKed helper copies of its own TBs (fixed;
re-run of `leaderRes` / `ownRes`); `none4` is infeasible in this pool. **Server campaign
done 2026-10-12; verdicts in §7.8.**

**Update (2026-10-13). SB-SPS re-run done on the server — the new reference results**
(§7.10, README "SB-SPS re-run: results"): periodic traffic on SB-SPS (misfit reselection,
keep 0.8, 14-entry RRI list), D1 + D2 sensing for every UE; campaign 270 + variants 276 +
coop 500 runs, 0 failed, every flag and coop check 0. Headline: periodic1 no longer
loses PRR to background traffic (0 -> 60: <= 1 pp, was 3-7 pp), periodic2 unchanged net
(fewer collisions, fewer retransmission slots), aperiodic1 +0.02-0.05; the coop verdicts
hold in direction (blockage + periodic1: helpers help), handing over no longer loses
under collisions with periodic1. §7.1-7.8 stay as the record of the dynamic-grant runs.

**Update (2026-10-13, later). Hidden-node test** (branch `hidden-node`, §7.11, README
"Hidden-node test"): instrumentation of the leader–helper sensing gap at every leader
selection and at every use of a leader-selected resource (`--hiddenNode`: `_use.csv`,
`_sel.csv`, `hn_*` columns), a sensing-threshold flag (`--slThresRsrp`, every UE) and an
oracle IUC arm `leaderResIuc` (upper bound of Rel-17 Scheme 1). All opt-in; all off:
byte-identical outputs. Definitions, checks and the pre-registered go / decision rules are
in the README; stage A (3 seeds) runs on the server, stage B only after a go.

**Update (2026-10-12, later).** Standard Mode-2 grant types and the sensing fixes,
implemented on `coop-arms` (README "Standard Mode-2 grant types and sensing fixes"),
all opt-in: `--spsReselectOnMisfit` (TS 38.321 5.22.1.1 reselection when an SDU does not
fit the SPS grant), `--slDynExclusion` (D1, dynamic grants apply the step-6 exclusion),
`--slProjectAnnounced` (D2, announced current-period / dynamic retransmissions are
projected), with the existing `--leaderSps`. All off: byte-identical to `7fd137f` (9
pilots). Option-1 wrapper fixed for SPS (an implicit ACK after a period's last reserved
retransmission could erase the next period's grant and flush its TB). Pilots (seed 1) and
the decision on the full re-run (`run_rerun_sps.sh`): README section above / §7.9.

**Update (2026-10-11, evening).**
- **t-Reordering 0 re-run done on the server** (reference): campaign 270/270 runs
  (`results/campaign_rlc0`, 3 seeds) and variants (`results/variants_rlc0`: MCS 14, dedicated,
  blind; periodic1/2 MCS 14 + dedicated at 10 seeds), 0 failures. **PRR identical** to the
  100 ms runs; `prr_pdb` and delays are now valid (§6, §7.6). Laptop copies of the server
  aggregates: `~/Documents/remote-results/analysis_rlc0/`, `.../variants_rlc0/analysis/`.
- **Cooperative HARQ comparison** (`run_coop.py`, 180 runs, 10 seeds): done on the laptop
  (§7.7); running on the server (started 16:35, branch `coop-harq-nr`). The two
  `FLAG CHECK FAILED` runs are false positives of the check, not of the scheme (§6).
- `run_coop.py --analyze-only` now also reports **latency** (mean / percentiles, reliability
  vs latency) from the existing per-packet files (§5, §7.7).
- Branch `coop-harq-nr` pushed to `origin` (commits `749e2cc`, `b5461ee`, `8a6fbf2`). **The
  server is now on `coop-harq-nr`** (`./setup.sh --force`, coop NR patch installed;
  non-coop runs are identical to stock). Back to stock: `git checkout
  platoon-harq-robustness-eval && ./setup.sh --force`, rebuild.

---

## 1. Research question (as given by the user)

A 9-vehicle platoon; the **leader** (front vehicle) sends **groupcast** traffic to the 8 members
behind it using **NR Mode-2 sidelink with HARQ feedback Option 1 (NACK-only), up to 2
retransmissions**. Compare:

1. **Channel robustness** — highway vs urban roundabout (more NLOS from buildings), *no
   background traffic*.
2. **Interference / collision robustness** — the same, with background (surrounding) vehicles
   sharing the resource pool, added incrementally.

Factors: scenario {highway: 3 km pass; roundabout: enter from the northern entry, leave by the
western exit} × IVD {5, 15, 30 m} × leader traffic {periodic1, periodic2, aperiodic1} ×
background density (4 levels + none), background = 60 % periodic1 / 40 % aperiodic1.
Resource pool: numerology μ, M subchannels per slot, 20 MHz (μ, M and the densities were left
to us; densities had to make collisions visible). KPIs: packet collision rate, packet drop due to
channel, average PRR per vehicle over the whole run.

## 2. What was built

| piece | role |
|---|---|
| `gen_mobility.py` | SUMO/TraCI trace generator for both scenarios (platoon + background "seats") |
| `platoon_harq_robustness_eval.cc` | ns-3 program (CTTC 5G-LENA NR-V2X, ns-3.42), KPIs computed in-run |
| `nack_only_harq.{h,cc}` | leader-side HARQ Option-1 decision + exact packet→TB tagging |
| `sumo_buildings.{h,cc}` | registers the SUMO building polygons as ns-3 buildings (NLOS) |
| `run_campaign.py` | resumable parallel sweep; `analyze.py` aggregation + figures |
| `README.md` | full design doc + results section + server instructions |

Scenarios were derived from existing repo work: highway from `highway_kuo_eval` (3-lane SUMO
highway), roundabout from `roundabout_rsu_eval` / `joint-coop-harq-mac/sumo-exports` (synthetic
4-arm roundabout, ring radius 25 m, 1 km arms, 98 buildings of 30×40 m, 15 m tall).

## 3. Configuration (final)

**Radio.** 5.89 GHz, 20 MHz, **μ = 1** (30 kHz, 0.5 ms slots), **M = 5 subchannels × 10 PRB**
(55 PRB available, 50 used). Repo-standard pool: SL bitmap `111111000111` on the UL slots of TDD
`DL DL DL F UL×6` → **9 SL slots per 20 physical slots = 900 SL slots/s = 4500 subchannel-slots/s**.
MCS 14 fixed for all UEs, 23 dBm spread over the allocated PRBs, 1×2 isotropic antennas,
sensing-based selection (T1 = 2, T2 = min(33 slots, PDB), RSRP threshold −128 dBm).

**HARQ.** sl-MaxTxTransNumPSSCH = 3 (initial + 2 retx), sl-MaxNumPerReserve = 3 (retx resources
reserved in SCI-1A), PSFCH period 1 slot, MinTimeGapPSFCH 3, MinTimeGapProcessing 2,
castType GroupcastNegativeOnly, MCR 500 m (never binding).

**Channel.** 3GPP TR 37.885: V2V_Highway (highway), V2V_Urban (roundabout) with path loss,
shadowing, fast fading. Geometric NLOSv: every vehicle is a 5 × 1.8 m blocker (axis-aligned box,
Liang–Barsky segment test). Roundabout buildings make NLOS reachable.

**Mobility.**
- Platoon: 9 cars of 5 m, **IVD = bumper-to-bumper gap**, ideal constant-spacing (member i stays
  exactly i·(IVD+5 m) of arc length behind the leader along the leader's own path).
  Leader-to-tail distance 80 / 160 / 280 m.
- Highway: two carriageways × 3 lanes, 7 km road; platoon in the middle eastbound lane at a
  constant 30 m/s; KPI window = the 3 km pass (100 s) after 3 s warm-up. Platoon lane reserved.
- Roundabout: leader driven by SUMO (yields at the entry, ~8 m/s on the ring, 13.9 m/s on arms),
  members moved by TraCI to the arc-length positions; KPI window from leader 250 m before the
  give-way line until the tail is 100 m into the west exit (≈ 40–66 s).
- Background: **N ∈ {0, 15, 30, 45, 60}** vehicles in the *area of interest* — highway: within
  ±1 km of the platoon centre on the 5 non-platoon lanes (both directions, mean count = N);
  roundabout: within 500 m of the centre, density held at exactly N by the original TraCI
  controller. Vehicles entering/leaving the area occupy reusable ns-3 node "seats" (idle seats
  parked > 1000 km away, beyond `MaxLossDb` = 150 dB). Measured mean in-area count 29.6 / 29.9
  (highway / roundabout) averaged over all N — on target.

**Traffic.**
- periodic1: 100 ms, sizes cycle {300,190,190,190,190} B. periodic2: 10 ms, 800 B (p 0.8) /
  1200 B (p 0.2). aperiodic1: 50 ms + Exp(50 ms), uniform 200–2000 B step 200.
- Leader → 8 members, dstL2Id 254, **dynamic grant per packet**, PDB 100 / 10 / 50 ms.
- Background: periodic1 → SPS (RRI 100 ms), aperiodic1 → dynamic grant; broadcast, no HARQ,
  one private dstL2Id per vehicle (everyone still decodes everyone's SCI-1A for sensing; nobody
  decodes background PSSCH — saves CPU, does not change interference or sensing).

## 4. Key design decisions / deviations (read before reusing the results)

1. **Option-1 TX behaviour had to be added.** CTTC pre-reserves all retransmission resources
   and only cancels them on an ACK. Option-1 receivers never ACK, so stock behaviour would be
   blind 3× transmission. `nack_only_harq` wraps the leader's PHY↔MAC SAPs: for every PSSCH in
   slot n it finds the PSFCH occasion m (first PSFCH slot ≥ n+3) and, ½ symbol after the NACKs
   would reach the MAC (m + 13 symbols + 1 slot), decides **no NACK ⇒ success** (TS 38.321) and
   delivers an implicit ACK that cancels the remaining reserved retx; NACK ⇒ next reserved retx
   goes out. Receivers drop their ACKs (`common/mcd/mcr_nack_only_gate`). **Verified**: 0
   retransmissions after an implicit ACK, every retransmission preceded by a NACK.
   Known Option-1 property reproduced: a member that misses the SCI is silent → treated as
   success.
2. **Leader uses dynamic grants, not SPS.** CTTC's SPS sizes the reservation on the first packet
   and never re-selects when a larger SDU arrives (3GPP would). Pilot with SPS: every 300 B
   periodic1 packet was split across two 100 ms periods, mean delay 94 ms, 28 % within PDB.
   `--leaderSps=true` reproduces it. Background periodic1 keeps SPS (its segmentation only shifts
   load slightly and is not measured). *Since 2026-10-12* `--spsReselectOnMisfit=true`
   adds the 3GPP reselection (every SPS UE); with it the SPS leader's delay is a few ms (§7.9).
3. **Densities capped at 60** because the roundabout gridlocks above ~70–80 vehicles in the
   500 m area; same N used in both scenarios. Measured ≈ 0.73 % of the pool per background
   vehicle → background CBR ≈ 0.11 / 0.22 / 0.32 / 0.43 for N = 15/30/45/60; the leader adds
   0.003 (periodic1), 0.02 (aperiodic1) or 0.10–0.16 (periodic2). Total CBR at N = 60:
   0.43–0.59.
4. **Packet → TB mapping is exact**: a byte tag with the application sequence number is added
   before the socket send and read back from every MAC PDU handed to the PHY (survives RLC
   segmentation and HARQ retx).
5. **Background cut-ins on the roundabout**: SUMO's keep-right/speed-gain lane changes made
   background cars cut into the 30 m platoon gaps (and overlap TraCI-placed members). Disabled
   those two lane-change motives for background cars (strategic/cooperative kept). Residual
   overlap ≤ 11 samples per run (bg within 3 m of a member).
6. **No contrib/nr change is required.** The local NR tree of the original laptop carries
   unrelated, uncommitted coop-HARQ patches (for `highway_kuo_eval`); some are not gated by
   `EnableChainedRes` and apply to every simulation: "cancel only when `NUM_MEMBERS` (default 8)
   members ACK", SCI-2A serialized as 15 B instead of 5 B (used by the SCI-2 error model),
   per-slot `NS_LOG_UNCOND`. This program sets `NUM_MEMBERS`, sends one ACK per member,
   strips `MAGIC_*` env vars and silences `std::clog`, so only the SCI-2A size remains; the
   cross-machine agreement (§6) bounds its effect to noise. **The server is the reference.** The second machine used
   only the committed `patches/nr/local.patch` (two asserts relaxed) — results identical within
   noise (§6).
   *Since 2026-10-11 (branch `coop-harq-nr`)* the coop changes are reworked and committed as
   `patches/nr/local.patch` of that branch, gated by `EnableChainedRes` (off ⇒ byte-identical
   to stock, 4 pilots / 16 output files); the old laptop state is kept in
   `highway_kuo_eval/nr_patch_backup/nr_coop_before_2026-10-11.patch`. The laptop's working tree equals that
   patch. Uncommitted edits in `scratch_overlay/common/` on the laptop are not part of this
   study.
7. Shared-code fixes needed by this scenario (committed): geometric NLOSv no longer lets a
   vehicle block its own link (previously every link was forced NLOSv); `harqMode=` flow key
   (was shadowed by `harq=`); `SlStackCtx::channelConditionModel`; NACK-only gate populates
   its own RNTI→mobility registry.
8. **RLC t-Reordering (found 2026-10-11).** 5G-LENA's sidelink uses LTE RLC UM
   (`ns3::LteRlcUm`): in-order delivery with a 100 ms `ReorderingTimer`. NR RLC UM
   (TS 38.322) has no t-Reordering. Every SDU behind a lost PDU is held up to 100 ms, so
   lossy members get artificial delay (tail, periodic2, hwy IVD 30, N = 0: 92 % of delivered
   packets > 100 ms late, median 150 ms; member 1 ≈ 5 ms). Pilot 100 → 0 ms: **PRR identical**,
   tail `prr_pdb` 0.00 → 0.47 (periodic2) and 0.73 → 0.85 (periodic1), tail delay 149 → 7 ms.
   The program now sets `ReorderingTimer` = 0 by default (`--ns3::LteRlcUm::ReorderingTimer=100ms`
   reproduces the old runs) and writes `rlc_reordering_ms`; runners check it (old CSVs without
   the column = 100 ms).
9. **Variant flags** (all verified with pilots, README "Variants"): `--mcs` (members decode the
   requested MCS: run-CSV `mcs_rx_min/max`); `--dedicatedCarrier` (new: TDD `UL×10` +
   all-ones bitmap, `SlStackParams::dedicatedCarrier`); **blind = `--option1Tx=false
   --psfchPeriod=0`** — `--option1Tx=false` alone keeps PSFCH, CTTC then keeps the PSFCH gap
   between reserved resources and gives only 2-3 tx per TB (periodic2 never 3); without PSFCH
   CTTC reserves all 3.

## 5. KPI definitions (per run, per member, KPI window)

- **PRR** = packets delivered to the member's application / packets generated by the leader.
  "Average PRR per vehicle" = mean over the 8 members. Also `prr_pdb` (delivered within PDB) —
  **invalid in all runs made with t-Reordering 100 ms** (§4.8), valid in the `*_rlc0` runs.
- **Latency** (coop analysis, `run_coop.py`): application end-to-end delay of a packet at a
  member = first delivery to its application − generation at the leader (resource wait, HARQ
  retransmissions and relayed copies included), per (packet, member) in `<tag>_pkt.csv.gz`
  (`delay_ms`, written with 2 significant digits: 0.1 ms below 10 ms, 1 ms above).
  Percentiles are over delivered packets; the reliability-latency curve P(delivered within
  t) counts every packet, so it ends at the PRR.
- Every lost (packet, member) gets exactly one cause:
  - **collision**: ≥ 1 *failed* attempt of that packet at that member overlapped in time (slot)
    and frequency (≥ 1 common subchannel) with another active vehicle's PSSCH;
  - **channel**: transmitted, no failed attempt in collision (path loss, shadowing, NLOS/NLOSv,
    fading);
  - **not_tx**: never transmitted (measured 0 everywhere); **other**: all attempts decoded but
    not delivered (≤ 1.1 %).
  - ⚠️ Collision takes precedence: a packet whose channel was also bad but had a collided failed
    attempt is "collision". The channel share therefore *shrinks* as density grows — compare
    **total loss** across densities, read the split as "dominant cause".
- **leader_tx_coll_rate**: share of the leader's PSSCH transmissions overlapping another PSSCH
  (resource-level collision rate). **CBR** (`cbr`): subchannel-slots of all PSSCHs / available,
  all active vehicles, KPI window — overlapping (collided) transmissions count twice, so it is an
  offered load and can exceed 1 (MCS 5 / N = 60: 1.37); `cbr_occupied` (variants on) counts each
  subchannel-slot once. **tx_per_pkt**: PSSCH transmissions per packet (aperiodic1 > 2 because
  packets > ~1.4 kB need 2 TBs at MCS 14). **nack_decision_share**: share of HARQ decisions that
  saw a NACK.

## 6. Validation

- Pilots: TB per packet = 1 for periodic traffic; slot alignment of the decision timer 0
  mismatches; 0 unmatched PHY receptions; 0 explicit ACKs (Option 1 holds); 0 stale decisions.
- Full sweep: 270 runs, 0 failures, 3 seeds in every one of 90 cells.
- **Cross-machine reproduction** (Linux server: optimized build, apt SUMO, committed NR patch
  only; 239 runs, missing cells were killed by OOM when started 31-parallel): over 81 common
  cells, mean PRR difference 0.0001, mean |Δ| 0.005, max |Δ| 0.024, **no cell outside the 95 %
  CIs**.
- **Variants, laptop vs server** (90 common cells): mean |ΔPRR| 0.007, max 0.030; tail
  mean |Δ| 0.024; no cell outside the combined 95 % CI (PRR or tail). Every variant flag is
  re-checked after each run (`FLAG CHECK FAILED` otherwise; none occurred).
- **t-Reordering 0 vs 100 ms** (server, same seeds and traces): campaign, 80 of 81 common
  cells have the same PRR to 3 decimals (the other, roundabout IVD 30 periodic2 N = 60, had
  2 seeds in the 100 ms run), none outside the CI; collision / channel loss, CBR and tx per
  packet unchanged. Variants, cells with the same seeds: |ΔPRR| and |Δ tail PRR| ≤ 0.0015.
  With t-Reordering 0, `prr_pdb` = PRR for periodic1 and aperiodic1 (≤ 0.0006); periodic2
  keeps a real gap (mean 0.03, up to 0.14 for a member: multi-TB packets finishing after the
  10 ms PDB).
- **Coop campaign checks** (laptop, 180 runs): 2 runs flagged `relay_without_nack = 1`
  (`leaderRes/hwy_ivd30_periodic2_n45_s1`, `leaderRes/hwy_ivd30_periodic1_n60_s4`), one relay
  each among 374 / 3827. Debugged on the second (deterministic, reproduced to 4.6 s with
  PHY / channel logging): the tail member NACKed the leader's TB; every member and the helper
  received the NACK, but the leader's link had a **path loss of 156.9 dB > `MaxLossDb` 150 dB**
  (LOS ≈ 95 dB + the TR 37.885 NLOSv log-normal blockage loss, drawn anew per transmission,
  in its far tail) so the channel never delivered it. The leader took "no NACK" as success,
  the helper correctly relayed. **The check is too strict** (it uses the NACKs the leader
  received, not those the members sent); both runs are valid. The second case has the same
  signature (one relay, 0 NACKs at the leader, relay 8 slots later) but was not re-run.
  Side observation: the tail member sent its PSFCH one slot after the first PSFCH occasion
  ("processing delay 3 slots", stock); the leader's decision timer still catches it.

## 7. Results

Full table: `results/analysis/summary.csv` (one row per scenario × IVD × traffic × N with mean
and 95 % CI over seeds of every KPI). Per member: `per_member_summary.csv`. Raw per run:
`runs_all.csv`, `members_all.csv`. Per packet: `results/campaign/*_pkt.csv.gz`.

### 7.1 Average PRR per member — no background (channel only), highway / roundabout

| IVD | periodic1 | periodic2 | aperiodic1 |
|---|---|---|---|
| 5 m | 0.997 / 0.999 | 0.982 / 0.991 | 0.951 / 0.974 |
| 15 m | 0.988 / 0.965 | 0.950 / 0.891 | 0.868 / 0.824 |
| 30 m | 0.968 / 0.858 | 0.878 / 0.745 | 0.739 / 0.670 |

Tail member (member 8), no background, mean over traffic types:

| IVD | dist hwy / rbt | NLOS share hwy / rbt | mean SINR hwy / rbt | PRR hwy / rbt |
|---|---|---|---|---|
| 5 m | 80 / 78 m | 0 / 0.04 | 20.8 / 24.9 dB | 0.92 / 0.96 |
| 15 m | 160 / 153 m | 0 / 0.34 | 14.7 / 13.6 dB | 0.79 / 0.70 |
| 30 m | 280 / 263 m | 0 / 0.45 | 9.9 / 9.0 dB | 0.61 / 0.45 |

All leader→member links beyond member 1 are NLOSv (blocked by intermediate platoon cars) in
both scenarios; the roundabout adds building NLOS for the rear members while the leader turns.

### 7.2 Average PRR per member — 60 background vehicles (CBR 0.43–0.59)

| IVD | periodic1 | periodic2 | aperiodic1 |
|---|---|---|---|
| 5 m | 0.964 / 0.963 | 0.916 / 0.928 | 0.786 / 0.805 |
| 15 m | 0.932 / 0.906 | 0.846 / 0.839 | 0.667 / 0.683 |
| 30 m | 0.864 / 0.804 | 0.741 / 0.712 | 0.535 / 0.556 |

### 7.3 Loss increase from 0 → 60 background vehicles (percentage points of 1 − PRR)

| IVD | periodic1 hwy / rbt | periodic2 hwy / rbt | aperiodic1 hwy / rbt |
|---|---|---|---|
| 5 m | 3.2 / 3.6 | 6.6 / 6.3 | 16.5 / 16.9 |
| 15 m | 5.7 / 5.9 | 10.4 / 5.2 | 20.1 / 14.2 |
| 30 m | 10.4 / 5.3 | 13.8 / 3.2 | 20.5 / 11.4 |

At N = 60, 36–58 % of the leader's transmissions are in resource collision (≈ CBR); collision
loss per member-packet 3.5–43 %, channel loss 0.1–5 %.

### 7.4 Findings

1. **Urban NLOS hurts long platoons; HARQ cannot fix it.** IVD 5 m: roundabout ≥ highway
   (platoon fits on one arm + ring, lower speed, lower urban LOS path loss at these distances).
   IVD ≥ 15 m: the tail is still on the northern arm when the leader turns west → buildings block
   34–45 % of leader→tail transmissions, PRR −2 to −13 points. The NLOS outages last 15–20 s
   (`fig7_prr_timeline.png`: tail PRR ≈ 0 during the turn), orders of magnitude longer than the
   HARQ RTT, so retransmissions only help against fast fading, not against blockage.
2. **The HARQ budget is traffic-dependent.** periodic1 (100 ms PDB) recovers most errors
   (PRR ≥ 0.86 without background). periodic2 (10 ms PDB) **almost never gets its 2nd
   retransmission** in this pool (HARQ RTT ≥ 5 slots with only 9 SL slots per 20): third
   transmissions are 0.8 % of its PSSCH transmissions vs 4.8 % for periodic1 (over all runs).
   aperiodic1 packets above ~1.4 kB need two TBs at MCS 14, both must survive.
3. **Background traffic degrades PRR almost linearly with N, faster on the highway.** Open LOS
   means every interferer is heard (and sensing sees them, but dynamic-grant aperiodic traffic is
   unpredictable). From 0 to 60 vehicles the highway loses 3–21 points vs 3–17 on the
   roundabout, where buildings shield the platoon from most interferers. Consequence: at IVD 30 m
   the highway's channel advantage (11–13 points with no background) shrinks to 3–6 points.
4. **Traffic type ranking** (worst → best): aperiodic1 (fresh dynamic selection per packet,
   large multi-TB packets) < periodic2 (heavy load, tight PDB) < periodic1.
5. Losses concentrate on rear members (`fig3*.png`); member 1 stays ≈ 1.0 everywhere.

### 7.5 Variants (IVD 30 m; server = reference; full tables in README "Variant results")

Average PRR per member at N = 60, highway / roundabout:

| traffic | MCS 14 (base) | MCS 9 | MCS 5 | dedicated | blind |
|---|---|---|---|---|---|
| periodic1 | 0.89 / 0.80 | 0.83 / 0.77 | 0.73 / 0.73 | 0.93 / 0.83 | 0.90 / 0.81 |
| periodic2 | 0.74 / 0.69 | 0.30 / 0.39 | 0.29 / 0.40 | 0.86 / 0.75 | 0.80 / 0.72 |
| aperiodic1 | 0.55 / 0.55 | 0.38 / 0.45 | 0.30 / 0.43 | 0.66 / 0.61 | 0.54 / 0.54 |

1. **Lower MCS does not rescue the tail**: marginal gains without background (inside the
   CIs), losses everywhere with background. Per-attempt decoding improves, but the 23 dBm are
   spread over ~3x more subchannels (~4 dB SINR), roundabout losses are long building
   blockages that also kill the SCI (~20 % of tail attempts), packets split into 2-4 TBs, and
   the platoon's 2-3x larger footprint drives collisions (offered load > 1 at N = 60).
2. **Dedicated carrier**: best under load (+0.03-0.12 at N = 60), periodic2 gets its 2nd retx.
3. **Blind ≈ Option 1**; slightly better for periodic2 without background (Option 1's 2nd
   retx does not fit in the 10 ms PDB), at 3 transmissions per TB.

### 7.6 Delay-bounded PRR (t-Reordering 0, server)

Tail member (member 8) **PRR within the PDB**, IVD 30 m, 10 seeds, ± 95 % CI; in brackets
the invalid 100 ms value. Source: `remote-results/variants_rlc0/analysis/variants_summary.csv`
(`prr_pdb_tail`).

| cell | N = 0 | N = 45 | N = 60 |
|---|---|---|---|
| hwy periodic1, MCS 14 | 0.925 ± 0.044 (0.744) | 0.835 ± 0.029 (0.738) | 0.819 ± 0.056 (0.721) |
| hwy periodic1, dedicated | 0.930 ± 0.041 (0.781) | 0.879 ± 0.037 (0.824) | 0.897 ± 0.029 (0.763) |
| hwy periodic2, MCS 14 | 0.683 ± 0.106 (0.025) | 0.519 ± 0.101 (0.016) | 0.488 ± 0.098 (0.002) |
| hwy periodic2, dedicated | 0.751 ± 0.122 (0.034) | 0.691 ± 0.100 (0.086) | 0.687 ± 0.082 (0.038) |
| rbt periodic1, MCS 14 | 0.608 ± 0.016 (0.532) | 0.607 ± 0.060 (0.545) | 0.585 ± 0.057 (0.486) |
| rbt periodic1, dedicated | 0.621 ± 0.023 (0.546) | 0.622 ± 0.047 (0.525) | 0.620 ± 0.060 (0.522) |
| rbt periodic2, MCS 14 | 0.418 ± 0.029 (0.115) | 0.449 ± 0.066 (0.037) | 0.416 ± 0.058 (0.078) |
| rbt periodic2, dedicated | 0.457 ± 0.028 (0.165) | 0.478 ± 0.069 (0.103) | 0.470 ± 0.075 (0.177) |

1. The "periodic2 tail almost never meets the PDB" result of the 100 ms runs was entirely
   the RLC artifact: 0.49-0.75 on the highway.
2. Under load the dedicated carrier helps the tail most on the highway (N = 60 periodic2:
   0.687 vs 0.488).
3. On the roundabout the tail stays at 0.42-0.62 whatever the load or carrier: building
   blockage, not collisions, limits it.

### 7.7 Cooperative HARQ: leader only vs helper (laptop, 10 seeds; server re-run pending)

`run_coop.py`, helper = member 4, IVD 30 m. Tail = member 8, ± 95 % CI. Latency = mean over
all members of delivered packets; tail p95 = 95th percentile of member 8's delivered packets.

| cell | arm | PRR | tail PRR | tail PRR in PDB | tx / pkt | latency mean | tail p95 |
|---|---|---|---|---|---|---|---|
| rbt N 0 periodic1 | leader only | 0.854 | 0.597 ± 0.013 | 0.597 | 1.86 | 5.1 ms | 11.8 ms |
| | helper, leader's slots | **0.870** | **0.697 ± 0.020** | **0.697** | 1.48 | 5.2 ms | 13.5 ms |
| | helper, own resources | 0.867 | 0.677 ± 0.016 | 0.677 | 1.47 | 11.0 ms | 27.0 ms |
| rbt N 0 periodic2 | leader only | **0.752** | 0.464 ± 0.031 | **0.433** | 1.64 | 4.9 ms | 10.5 ms |
| | helper, leader's slots | 0.729 | 0.462 ± 0.038 | 0.405 | 1.25 | 5.8 ms | 10.5 ms |
| | helper, own resources | 0.699 | 0.411 ± 0.050 | 0.359 | 1.10 | 6.5 ms | 10.5 ms |
| hwy N 45 periodic1 | leader only | **0.898** | **0.811 ± 0.048** | **0.811** | 1.61 | 5.0 ms | 12.1 ms |
| | helper, leader's slots | 0.868 | 0.785 ± 0.041 | 0.785 | 1.40 | 4.8 ms | 10.4 ms |
| | helper, own resources | 0.867 | 0.773 ± 0.040 | 0.773 | 1.37 | 10.1 ms | 24.1 ms |
| hwy N 45 periodic2 | leader only | **0.785** | **0.591 ± 0.105** | **0.520** | 1.77 | 5.2 ms | 10.1 ms |
| | helper, leader's slots | 0.689 | 0.510 ± 0.077 | 0.429 | 1.30 | 6.0 ms | 10.1 ms |
| | helper, own resources | 0.657 | 0.449 ± 0.096 | 0.378 | 1.13 | 6.6 ms | 10.1 ms |
| hwy N 60 periodic1 | leader only | **0.872** | **0.797 ± 0.040** | **0.797** | 1.65 | 5.6 ms | 12.1 ms |
| | helper, leader's slots | 0.835 | 0.766 ± 0.027 | 0.766 | 1.38 | 5.2 ms | 10.7 ms |
| | helper, own resources | 0.838 | 0.740 ± 0.036 | 0.740 | 1.40 | 10.8 ms | 25.6 ms |
| hwy N 60 periodic2 | leader only | **0.738** | **0.546 ± 0.085** | **0.490** | 1.71 | 5.6 ms | 9.8 ms |
| | helper, leader's slots | 0.645 | 0.465 ± 0.061 | 0.400 | 1.26 | 6.2 ms | 9.8 ms |
| | helper, own resources | 0.610 | 0.403 ± 0.076 | 0.355 | 1.09 | 6.9 ms | 9.8 ms |

1. **Blockage (roundabout, periodic1): the helper helps** — tail PRR 0.60 → 0.70 (leader's
   slots), CIs disjoint; confirms the pilot. With periodic2 it does not (0.464 → 0.462;
   not investigated yet).
2. **Collisions (highway): the helper hurts** — mean PRR −0.03 to −0.13, tx per packet
   1.6-1.8 → 1.1-1.4. The leader transmits once and hands its retx slots to the helper; when the helper
   itself missed the TB (frequent when a collision hits the whole platoon) nobody uses them
   (Option 1: the leader cannot tell who NACKed).
3. **Sensing attribution of collided, failed attempts**: an interferer had been sensed by
   **both** leader and helper in 94-96 % (periodic1) / 79-82 % (periodic2) of them, by
   neither in 2 % / 8-10 %. The helper does not see interferers the leader misses: these are
   not hidden-terminal collisions, so the helper's own sensing cannot avoid them.
4. **Latency**: the helper on the leader's reserved slots keeps the mean latency (same
   reservation timing; tail p95 −1.7 to +1.8 ms). The helper on its own resources (Mode-2 selection over the remaining PDB) doubles
   the periodic1 mean latency (≈ 5 → 10-11 ms) and the tail p95 (≈ 12 → 24-27 ms), and
   reaches the plateau of the reliability-latency curve much later (`figc5`). periodic2: all
   arms within 1-2 ms; its p95 sits at the 10 ms PDB and is too coarse (delay rounding) to
   separate them — read `figc5`.
5. Laptop leader-only arm ≈ server MCS 14 baseline (hwy N 45 periodic1: 0.898 vs 0.899).
   Compare with the server's coop runs cell by cell before quoting.

### 7.8 Cooperative HARQ follow-up (2026-10-12): split / additive arms, footprint test, helper sweep

Design, checks and the pre-registered decision rules: README "Cooperative HARQ: follow-up".
Status: **done on the server** (2026-10-11 22:30 -> 2026-10-12 06:52, 760 runs, every check
0). Full tables: README "Cooperative HARQ: follow-up / Results"; data in
`~/Documents/remote-results/{coop2_rlc0,coop_rbt_rlc0,coop_sweep_rlc0}`. The 2026-10-11
server coop runs agree with the laptop (18 cells, none outside the combined CI).

Verdicts (server, 10 seeds):
1. **A helper beats the standard only under blockage with periodic1** (roundabout N 0: all
   four arms +0.07 to +0.10 tail `prr_pdb`, disjoint CIs; N 45 / 60: +0.04 to +0.06,
   paired 10/10). `additive` never loses and gains +0.02 to +0.03 on the highway (paired)
   for 12-25 % more transmissions; a cost-matched leader-only arm is infeasible here.
   Handing retransmissions over (`leaderRes`, `ownRes`) loses under collisions and with
   periodic2 (up to -0.14).
2. **Footprint: supported by the pre-registered rule** (turn phase: 22.7 % leader_only +
   helper_only, median divergence 0.40) **but not a turn effect** (outside the turn: 18.9 %,
   0.40). Buildings cause it (roundabout P(sensed) 0.29 at 300-500 m vs 0.80 highway). It
   cannot avoid collisions in 5G-LENA, whose dynamic grants skip the sensing exclusion.
3. **Helper position: helper 4 is the best static choice** (picked on seeds 1-5 for
   periodic1 in every arm); rear helpers are worse; the leader-side rule reduces to helper 4.
   The dynamic oracle (+0.09 to +0.12) is an upper bound inflated by noise, not evidence.

- `split` = leader initial + 1st retx, 2nd reserved retx handed to the helper (NACK on the
  leader's retx required); `additive` = unchanged Option-1 leader + one helper copy on its
  own resources after a NACK on the leader's initial transmission.
- `none4` (leader-only, 4 transmissions) is **not feasible** in this pool: with T2 = 33
  slots and the PSFCH / processing gaps the scheduler never fits a 4th resource. Cost is
  compared through `tx_per_pkt`, `cbr_occupied` and `figc7_reliability_vs_cost` instead.
- **The leader NACKed helper copies of its own TB** (it monitors the group destination):
  the helper took that NACK for one on its copy and sent its 2nd relay without any member
  asking (`leaderRes` / `ownRes`, relay budget 2; 8 of 71 relays in a 25 s roundabout
  pilot with helper 7). Fixed in the NR PHY (the original transmitter ignores relayed
  copies of its own TBs). **The 2026-10-11 `leaderRes` / `ownRes` results (§7.7) contain
  these spurious relays**; step 1 re-runs them (`results/coop2_rlc0`). §7.7 stays as the
  record of that version.
- Cooperation off and `none` are byte-identical to the 2026-10-11 code (pilots, every
  output file): `none` is reused.
- Pilots (laptop, seed 1): both new arms pass every check (README "Pilots").
- Sensing in 5G-LENA: dynamic grants never exclude sensed reservations (§8) — bears on the
  footprint question: the leader's own selections ignore what it senses.
- Highway helper sweep skipped (pre-registered condition not met).

### 7.9 Standard grant types and sensing fixes: pilots (2026-10-12, laptop, seed 1)

README "Standard Mode-2 grant types and sensing fixes / Pilots" (table). In short: the SPS
leader with the misfit reselection has a few-ms delay (not ~94 ms); 1.4-1.6 (periodic1) /
3.5-3.6 (periodic2) reselections/s, about half of them misfits; D1 + D2 cut the highway
leader's collision rate 0.25 -> 0.07 (periodic1) and 0.39-0.46 -> 0.20 (periodic2);
periodic2 SPS grants reserve only 1.44 slots under load (2.24 dynamic), so the SPS
periodic2 arm loses retransmissions (PRR -0.03 with D1 + D2). Flags off: byte-identical
(9 short + 4 full + 3 full coop pilots). Every coop check passes under SPS. Later: sl-ProbResourceKeep 0.8 (5G-LENA never applied keep: implemented),
14-entry sl-ResourceReservePeriodList, leader NACK attribution fixed for SPS; the
periodic2 retransmission-slot loss is SPS-specific (step 6 over C_resel periods; a
dynamic leader with D1 + D2 keeps 2.11 slots).

### 7.10 SB-SPS re-run (server, 2026-10-13; new reference)

Configuration, checks, full tables: README "SB-SPS re-run: results". Same traces and seeds
as the `*_rlc0` runs; the comparison mixes the leader's grant type, D1 + D2 for every UE
and the background's SPS rules, so read it as a new baseline. Dynamic `*_rlc0` in
brackets.

Loss increase N = 0 -> 60 (pp of 1 - PRR), highway / roundabout:

| IVD | periodic1 | periodic2 | aperiodic1 |
|---|---|---|---|
| 5 m | 0.1 (3.0) / 0.2 (3.4) | 4.1 (6.8) / 2.8 (6.4) | 10.0 (14.1) / 12.0 (16.9) |
| 15 m | 0.0 (5.7) / -0.1 (5.3) | 6.6 (10.4) / 2.5 (5.3) | 14.8 (18.8) / 10.4 (13.0) |
| 30 m | -1.8 (7.4) / 1.0 (5.9) | 10.1 (13.9) / 3.1 (5.7) | 15.6 (19.2) / 9.5 (12.0) |

1. **periodic1**: background no longer costs PRR; leader collision rate at N = 60 0.16
   (0.36), collision loss 0.020 (0.077); highway N 60 PRR 0.973-0.995 (0.893-0.967), 8/15
   highway cells better with disjoint CIs. Highway tail `prr_pdb` (IVD 30, 10 seeds)
   0.929 / 0.919 at N 45 / 60 (0.835 / 0.819).
2. **periodic2**: net unchanged (mean dPRR ~0): collision rate 0.34 (0.58) at N = 60, but
   1.4-1.5 reserved slots per grant under load, tx/pkt 1.25 (1.54) — the accepted SB-SPS
   property (§8). Tail `prr_pdb` within the CIs everywhere.
3. **aperiodic1** (leader still dynamic): +0.02 to +0.05 at N >= 30 from D1 + D2.
4. **N = 0, long IVD: SB-SPS slightly worse** (roundabout IVD 30: periodic1 0.830 (0.856),
   periodic2 0.715 (0.751), disjoint CIs; tail `prr_pdb` periodic1 0.573 (0.608)). Likely
   (not isolated): an SPS grant keeps the size of its largest SDU (1.62 subchannels for
   periodic1 vs 1.20 per-packet dynamic; 4.56 vs 4.20), so smaller SDUs get less power
   per PRB.
5. Unchanged: urban NLOS limits the long platoon; HARQ cannot fix blockage; ranking
   aperiodic1 < periodic2 < periodic1; dedicated carrier best for periodic2 (highway N 60
   tail 0.719 vs 0.490).
6. Blind with SB-SPS: periodic2 gets 2.1-2.2 tx/pkt under load (3 only at N = 0).

Coop (tail `prr_pdb`, 10 seeds, paired vs `none`):
1. **Blockage + periodic1: every helper arm still helps**, a little less: roundabout N 0
   +0.04 to +0.07 (`leaderRes`, `ownRes`, `additive` with disjoint CIs), N 45 / 60 +0.03 to
   +0.05 paired (8-10/10). `none` itself is lower (0.556 vs 0.597 at N 0; finding 4).
2. **Highway: handing over no longer loses** for periodic1 (all n.s.), `leaderRes` no
   longer loses with periodic2 (-0.01 / -0.02 n.s.; was -0.09); `ownRes` still loses with
   periodic2 (-0.06 / -0.07).
3. `additive`: +0.02 / +0.03 paired (7/10) in two highway cells only, for 10-30 % more
   transmissions. `split` is inert with periodic2 (no third slot to hand over).
4. **Footprint: supported, still not a turn effect** (turn 46.8 %, outside 43.9 %; was
   22.7 % / 18.9 %). With sensing working, the leader's residual collided attempts are
   mostly with interferers only the helper sensed (40 % vs 12 % before; both: 17 % vs
   63 %) — the helper's sensing would now carry information the leader lacks.

### 7.11 Hidden-node test (branch `hidden-node`, 2026-10-13; stage A pending)

Design, definitions, checks and rules: README "Hidden-node test" (written before any
result). Code:
- NR patch: `SensingData::txRnti` (set by the PHY from the PSCCH's MAC PDU tag);
  `NrSlUeMac::SetSensingPeer(peer, exclude)` — at each selection the peer's sensing data
  (same window, platoon-internal SCIs ignored) is evaluated with the same projections and
  reported in the trace `SensingGap` (per step-5 candidate: in final list, excluded by the
  real step 6, by own / peer data at the final / initial threshold, peer at the own-only
  final threshold); with `exclude` it is also removed from the candidates (union, same
  +3 dB / 20 % loop) = the oracle IUC; `EvaluateSensedExclusion` (step 6 for one resource,
  C_resel = 1, const). Without a peer the selection code path is unchanged.
- `SlStackParams::thresRsrp` (default -128) replaces the hard-coded `SlThresPsschRsrp`.
- Program: `--slThresRsrp`, `--hiddenNode`, `--helperMode=leaderResIuc`; uses linked to
  selections through `GrantCreated` (same instant as the selection's `SensingGap`) and
  `GrantPublished` (slot -> selection, keep count, period); outcomes from the existing
  `UTx` / collision / reception bookkeeping; no new channel-condition query.
- Runner `run_hidden_node.py`, driver `run_hidden_node.sh`, analysis
  `analyze_hidden_node.py` (stageA / full); `run_coop.py` knows `leaderResIuc`.

Rel-17 IUC clauses checked in ETSI TS 138 214 V17.5.0 (sec. 8.1.4A, 8.1.4C) and TS 138 321
V17.5.0 (sec. 5.22.1.10.1-2, 6.1.3.53); quoted in the README.

## 8. Limitations / caveats

- Single MCS (14). High MCS + power spread over wide allocations (6 dBm/PRB at 5 subchannels)
  makes large packets fragile; results are MCS-specific.
- PSFCH modelled as in CTTC: lossless, no capacity limit, no PSFCH collisions.
- Pool is the repo's TDD-shared configuration (45 % of slots), not a dedicated ITS carrier
  (100 % SL); a dedicated carrier would allow 2 retx for periodic2 and halve the CBR per vehicle.
- Ideal constant-spacing platoon (no controller dynamics, no reaction to cut-ins).
- Highway area of interest is ±1 km (mean count N), roundabout is r = 500 m (exact N): same N
  but different spatial distributions.
- Axis-aligned 5 × 1.8 m blocker boxes ignore heading (slight over-approximation on the N/S arm).
- Background periodic1 SPS inherits the CTTC SPS-size limitation (some background TBs segmented).
- Collision attribution precedence (see §5).
- 3 seeds; widest 95 % CIs ≈ ±0.14–0.16 on PRR in a few aperiodic1 / N = 45 cells.
- **RLC t-Reordering artifact** (§4.8): `prr_pdb` / delays of all runs made before 2026-10-11
  are invalid; use the `*_rlc0` results (§7.6) for any delay result. MCS 5 / 9 were not
  re-run (their PRR stands, their `prr_pdb` does not).
- **PSFCH reach = `MaxLossDb`** (lossless PSFCH): with the TR 37.885 NLOSv blockage loss drawn
  per transmission, a leader occasionally misses a member's NACK (path loss > 150 dB) and
  treats the TB as delivered (§6, coop checks). Inherent to Option 1; not counted as a KPI yet.
- Coop: the helper's `tx_in_tb` in `<tag>_htx.csv` is a running counter per HARQ id (never
  reset, wraps at 255) — no KPI uses it. Per-packet `delay_ms` has 2 significant digits.
- **Open bug, MCS 5 / roundabout / periodic2**: a member's app receives a truncated packet
  (`SeqTsSizeHeader::Deserialize` reads past the buffer; RLC SL reassembly or MAC suspected,
  independent of t-Reordering). Debug build aborts (laptop N = 0 s3, deterministic, reproduce
  in 20 s); optimized build hung once (server N = 45 s3, killed). Those cells have 2 seeds.
  Completed debug runs cannot have hit it and the server agrees with them, so results are
  not contaminated; fix before running more MCS 5/9 cells.
- **5G-LENA dynamic grants never apply sensing-based exclusion** (found 2026-10-12): the
  scheduler passes `cResel = 0` for a dynamic grant and the MAC's step-6 loop over the
  candidate's reservation periods runs `cResel` times, so no candidate is ever excluded by a
  sensed reservation (only step 5, the UE's own transmissions). Affects the leader in every
  run of this study and the aperiodic background; SPS background (periodic1) excludes
  normally. TS 38.214 sec. 8.1.4 checks the candidate itself when the reservation period is
  0. Also, retransmission reservations announced in an SCI (TRIV gaps) are projected only
  into the next period, not the current one. The collision breakdown (`_coll.csv` column
  `why`, run columns `cc_*`) shows the consequence: reservations sensed well above the
  threshold and still selected (`not_excluded`). *Opt-in fixes since 2026-10-12*:
  `--slDynExclusion` (D1) and `--slProjectAnnounced` (D2), README "Standard Mode-2 grant
  types"; used by the SB-SPS re-run (§7.10), not by §7.1-7.8.
- **periodic2 on SB-SPS loses retransmission slots under load (accepted, 2026-10-12)**:
  an SPS candidate must be free of sensed reservations at y + j P' for every j < C_resel =
  10 x counter (TS 38.214 sec. 8.1.4 step 6); with 10 ms periods, a loaded 45 % TDD pool and
  TBs of 4-5 of the 5 subchannels, few candidates survive (3.2 vs 10.9 for a dynamic grant
  with D1 + D2) and the leader reserves ~1.5 slots per TB instead of ~2.1-2.2, so most
  periodic2 TBs get at most one retransmission (highway N 45, seed 1). Standard behaviour,
  kept as a property of the configuration in the SB-SPS re-run; periodic1 is not affected
  (2.9 slots).
- **SB-SPS re-run comparisons mix several changes** (grant type, D1 + D2 for every UE,
  background SPS rules): per-option effects are only indicated by the seed-1 pilots (§7.9).
- **SB-SPS grant size**: a grant keeps the size of the SDU it was (re)selected for (the
  misfit rule only grows it), so smaller SDUs use more subchannels than a per-packet
  grant would; likely cause of the N = 0 deficit (§7.10.4), not isolated.
- **Under SB-SPS the collision `why` breakdown** (`_coll.csv`, `cc_*`) is not comparable:
  a period reusing a reservation has no selection to attribute (88 % `no_selection` for
  the leader). Footprint divergence is per selection, and the SPS leader selects ~70x
  less often: compare its shares, not its divergence, with the dynamic runs.
- `--collCond` (link condition leader / helper -> interferer in `_coll.csv`) queries the
  channel-condition model for extra pairs, which can change its random draws: used only in
  fresh campaigns (step 2), never mixed seed by seed with runs made without it.
- Laptop NR tree ≠ server NR tree (§4.6); the server is the reference. Since 2026-10-11 both
  run the `coop-harq-nr` NR patch, but the laptop build also has uncommitted edits in
  `scratch_overlay/common/` (not part of this study) and is a debug build: never pool seeds
  across the two machines (same ns-3 seeds, different SUMO traces).

- **Hidden-node test (§7.11)**: the oracle IUC arm is an upper bound (zero delay, no loss,
  no signalling, no PC5-RRC link needed; Rel-17 reporting is per PC5-RRC connection) and
  models only the RSRP condition of the non-preferred set. L, H and the use evaluations
  ignore platoon-internal SCIs (leader, helper). The use evaluation looks at the used
  resource alone (C_resel = 1) with the sensing data at the time of use; 5G-LENA has no
  re-evaluation / pre-emption, so an obsolete resource is still used. `cond_leader_helper`
  of a helper copy is the value at the leader's latest transmission.

## 9. Reproduce

```bash
./setup.sh                                   # submodules + patches + scratch symlinks
cd ns-3-dev && ./ns3 configure --build-profile=optimized --disable-werror
./ns3 build platoon_harq_robustness_eval     # (needs liblz4-dev libzstd-dev or remove the
                                             #  traffic_class_compress_eval scratch symlink)
cd ../scratch_overlay/platoon_harq_robustness_eval
export SUMO_HOME=/usr/share/sumo             # or a SUMO build; TraCI from $SUMO_HOME/tools
python3 run_campaign.py --seeds 1 2 3 --jobs 12   # ≤ ~12 on 32 GB: heavy runs > 0.5 GB each
python3 analyze.py --results results/campaign_rlc0 --out results/analysis_rlc0
python3 run_variants.py --jobs 12                 # reuses campaign_rlc0 for MCS 14
```

Runners default to t-Reordering 0 (`--rlc-reordering-ms`) and write to
`results/campaign_rlc0/` and `results/variants_rlc0/`; the original 100 ms runs stay in
`results/campaign/` and `results/variants/` (analyse them with `--rlc-reordering-ms 100
--results results/variants --baseline-dir results/campaign`). On the server, campaign +
variants with 12 jobs ≈ 4-5 h + 5 h.

Cost (laptop, 7 parallel): 6.2 h. Mean wall per run: highway N = 0/15/30/45/60 →
39 / 237 / 563 / 985 / 2000 s; roundabout → 17 / 103 / 289 / 533 / 896 s.
Single run example in `README.md`. Server t-Reordering 0 re-run (24 parallel): campaign
2.7 h, variants 1.6 h + 2.9 h.

Cooperative HARQ (branch `coop-harq-nr`; needs `./setup.sh --force` after the checkout to
install its NR patch, then rebuild):

```bash
python3 run_coop.py --dry-run           # 180 runs: 3 arms x 3 cells x 2 traffic x 10 seeds
python3 run_coop.py --jobs 24           # server; laptop --jobs 7 took 7.9 h
python3 run_coop.py --analyze-only      # results/coop_rlc0/analysis/ (~6 s)
```

Inside an existing tmux session use `tmux new -d -s coop -c "$PWD" '...'`.

## 10. Where things are

- Code + docs: `scratch_overlay/platoon_harq_robustness_eval/` (branch
  `platoon-harq-robustness-eval`, commits `edcdfb2`, `9c292d8`, `8e8b353`, `ac560e6`,
  `0bc97b9` (variants), `0b79f66` (restore patches), `54b7fbb` (fig6 axis) and the
  t-Reordering commit after it.
- Laptop results (not in git, git-ignored): `results/campaign/` (per run: `*_run.csv`,
  `*_member.csv`, `*_pkt.csv.gz`, `*_ltx.csv.gz`, `*.log`), `results/analysis/` (CSVs +
  `fig1`–`fig7`). Mobility traces: `traces/` (+ `.json` seat histories).
- Variant results: laptop `results/variants/` (+ `analysis/`); server summary copied to
  `~/Documents/remote-results/variants_summary.csv` on the laptop (reference values).
- Server replica (aggregates only): `~/Documents/remote-results/` on the laptop:
  `summary.csv`, `variants_summary.csv` (100 ms runs; PRR only), `analysis_rlc0/` (campaign,
  t-Reordering 0: CSVs + fig1-fig7) and `variants_rlc0/analysis/` (variants, t-Reordering 0:
  CSVs + figv1-figv4). Copy with
  `ssh -F ~/.ssh/ease-config ease-rtx 'cd ~/Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/results && tar czf - <dirs>' | tar xzf - -C ~/Documents/remote-results`.
- Coop: branch `coop-harq-nr` (`749e2cc` NR patch + program, `b5461ee` `run_coop.py`,
  `8a6fbf2` latency analysis). Laptop results `results/coop_rlc0/<arm>/` (+ `_htx`, `_coll`
  files) and `results/coop_rlc0/analysis/`: `coop_summary.csv`, `coop_runs.csv`,
  `coop_members.csv`, `coop_latency_cdf.csv`, figc1 PRR · figc1b tail PRR · figc2 tail PRR in
  PDB · figc3 sensing attribution · figc4 latency (mean, tail p95) · figc5 reliability vs
  latency. Server: same paths (running).
- **SB-SPS re-run** (2026-10-13, branch `coop-arms` @ `1ef63a6`): server
  `results/{campaign,variants,coop}_sps/`, `results/analysis_sps/`, driver
  `results/rerun_sps/`. Laptop copy (per-run / per-member CSVs and analyses, no per-packet
  files): `~/Documents/remote-results/{analysis_sps,campaign_sps,variants_sps,coop_sps,rerun_sps}`.
- Figures: fig1 PRR vs density · fig2 loss decomposition · fig3/3b PRR along the platoon
  (N = 0 / 60) · fig4 link condition per member · fig5 CBR & leader collision rate · fig6 HARQ
  transmissions per packet · fig7 PRR timeline (IVD 30, periodic1, seed 1).

## 11. Suggested next steps

1. ~~Dedicated ITS carrier~~ — done (§7.5).
2. ~~MCS sweep 5 / 9 / 14~~ — done (§7.5): lower MCS does not help the tail.
3. Option 2 (ACK/NACK) vs Option 1 on the same traces (Option 1's "missed SCI = success" vs
   Option 2's DTX handling). ~~Blind retransmission baseline~~ — done (§7.5).
4. Relay / multi-hop for the rear members during the roundabout NLOS outage (the dominant
   channel failure is a long blockage, which suits relaying rather than HARQ).
5. ~~Delay-bounded PRR~~ — done with the t-Reordering 0 re-run (§7.6).
6. Alternative loss attribution (e.g. SINR-based counterfactual without interferers) to split
   compound channel+collision losses.
7. Fix the truncated-packet bug (§8) before more MCS 5/9 runs (they are also the only cells
   without valid `prr_pdb`).
8. ~~Gate the laptop coop-HARQ NR changes behind `EnableChainedRes`~~ — done on
   `coop-harq-nr`: scheme reworked (SCI-2A side info, combining in the original HARQ
   process), byte-identical to stock with cooperation off (§4.6).
9. Coop, in order:
   a. Copy the server's `results/coop_rlc0/analysis/` back and compare cell by cell with the
      laptop (§7.7); quote the server.
   b. ~~Fix the `relay_without_nack` check~~ — done 2026-10-12: judged on the NACKs the
      members sent (gate observer), old definition kept as `relay_without_rx_nack`, new KPI
      `leader_nack_missed` for every arm; verified on the flagged run (1 -> 0, missed 1).
   c. Why the helper does not help periodic2 on the roundabout (§7.7.1).
   d. Helper selection: fixed helpers 2 / 4 / 6 / 7, static and dynamic oracle and one
      leader-side rule — step 3 of the follow-up (§7.8). A per-TB `HelperId` policy in the
      simulator is still not implemented (the rule is evaluated by composing static runs).
   e. ~~`delay_ms` precision, helper `tx_in_tb`~~ — done 2026-10-12 (1 us; reset per TB).
   f. ~~Run the follow-up campaign on the server~~ — done, verdicts in §7.8.
   h. If the sensing question is pursued: an opt-in, TS-conformant fix of the
      dynamic-grant sensing gap (g) is the precondition — the measured leader-helper
      sensing difference (§7.8.2) can only matter once selections use sensing.
   g. ~~5G-LENA: dynamic grants skip the sensing exclusion~~ — fixed opt-in (D1, D2) and
      re-run with SB-SPS (§7.10).
   i. Now that sensing works, the helper senses 40 % of the interferers behind the
      leader's residual collided attempts that the leader missed (§7.10, footprint): a
      helper-assisted selection (helper reports or vetoes resources) is the natural next
      arm.
10. SB-SPS follow-ups: isolate the N = 0 grant-size effect (e.g. a grant that also shrinks
    to the new SDU, or per-SDU-size statistics), and, if per-option effects matter,
    a factorial run (grant type x D1/D2) on a few cells.
11. Hidden-node test (§7.11): stage A on the server, report, stop; stage B only after a
    go and the user's OK (README "Hidden-node test").
