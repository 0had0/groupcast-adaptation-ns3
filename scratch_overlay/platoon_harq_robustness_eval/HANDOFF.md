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
   load slightly and is not measured).
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
  **invalid in all runs made with t-Reordering 100 ms** (§4.8).
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
  are invalid; re-run with t-Reordering 0 for any delay result.
- **Open bug, MCS 5 / roundabout / periodic2**: a member's app receives a truncated packet
  (`SeqTsSizeHeader::Deserialize` reads past the buffer; RLC SL reassembly or MAC suspected,
  independent of t-Reordering). Debug build aborts (laptop N = 0 s3, deterministic, reproduce
  in 20 s); optimized build hung once (server N = 45 s3, killed). Those cells have 2 seeds.
  Completed debug runs cannot have hit it and the server agrees with them, so results are
  not contaminated; fix before running more MCS 5/9 cells.
- Laptop NR tree ≠ server NR tree (§4.6); the server is the reference.

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
Single run example in `README.md`.

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
- Server replica (aggregates only): `~/Documents/remote-results/` on the laptop.
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
5. Delay-bounded PRR (`prr_pdb` is already in `*_member.csv`) as a stricter metric —
   **requires the t-Reordering 0 re-run** (§4.8, §9).
7. Fix the truncated-packet bug (§8) before more MCS 5/9 runs.
8. Laptop NR tree: gate the coop-HARQ changes behind `EnableChainedRes` and fix the coop
   scheme (soft-combining alias order, helper HARQ id, gaps from the last sensed SCI,
   off-by-one second gap, `RNTI == 1` override, MCS 14 hard-coded) — reviewed 2026-10-11,
   not yet fixed.
6. Alternative loss attribution (e.g. SINR-based counterfactual without interferers) to split
   compound channel+collision losses.
