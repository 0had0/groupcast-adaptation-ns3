# platoon_harq_robustness_eval

How robust is **standard NR-V2X Mode-2 groupcast HARQ** (feedback Option 1,
NACK-only, up to 2 retransmissions) for a 9-vehicle platoon,
* against **channel variation**: highway (open, LOS / vehicle-blocked) versus
  urban roundabout (buildings, NLOS), with no background traffic, and
* against **interference and resource collisions**: with an increasing number
  of surrounding vehicles that share the same Mode-2 resource pool.

Built from the two scenarios already in this repo:
`highway_kuo_eval` (3-lane SUMO highway) and `roundabout_rsu_eval`
(synthetic 4-arm roundabout with 98 buildings, `joint-coop-harq-mac/sumo-exports`).

**Current reference results: "SB-SPS re-run: results" below** (periodic traffic on SB-SPS,
D1 + D2 sensing, 2026-10-13). The earlier results sections are the record of the
dynamic-grant runs (`*_rlc0`) and stay valid for that configuration.

## Scenario matrix

| factor | values |
|---|---|
| scenario | **highway**: 3 km pass, two carriageways x 3 lanes, platoon in the middle eastbound lane at 30 m/s, 3GPP V2V_Highway. **roundabout**: enter from the northern arm (`N_in`), leave through the western exit (`W_out`), 98 buildings (15 m) registered so NLOS exists, 3GPP V2V_Urban |
| platoon | 9 cars (5 m), node 0 = leader (front) transmits, nodes 1..8 receive |
| IVD (bumper-to-bumper) | 5, 15, 30 m (leader-to-tail 80 / 160 / 280 m) |
| leader traffic | periodic1 (100 ms, 190/300 B), periodic2 (10 ms, 800 B p=0.8 / 1200 B p=0.2), aperiodic1 (50 ms + Exp(50 ms), 200..2000 B) |
| background vehicles N | 0 (channel only), 15, 30, 45, 60 - 60 % periodic1 / 40 % aperiodic1 |
| seeds | 3 (different SUMO traffic and ns-3 RNG); one mobility trace is shared by the three traffic types |

## Radio configuration

| parameter | value |
|---|---|
| carrier / bandwidth | 5.89 GHz, 20 MHz |
| numerology mu | 1 (30 kHz, 0.5 ms slots) |
| subchannels M | **5 x 10 PRB** per slot (55 PRBs in 20 MHz at mu=1, 50 used) |
| SL slots | 9 of every 20 physical slots (repo-standard pool: bitmap `111111000111` on the UL slots of `DL DL DL F UL UL UL UL UL UL`) -> 900 SL slots/s, **4500 subchannel-slots/s** |
| MCS | 14 (16QAM, R=0.54), fixed |
| TX power | 23 dBm, spread over the allocated PRBs |
| resource selection | sensing-based (T1=2, T2=min(33 slots, PDB)), RSRP threshold -128 dBm |
| HARQ | sl-MaxTxTransNumPSSCH = 3 (initial + 2 retx), sl-MaxNumPerReserve = 3, sl-PSFCH-Period = 1, MinTimeGapPSFCH = 3, castType GroupcastNegativeOnly |
| channel | 3GPP TR 37.885 path loss + shadowing + fast fading, geometric NLOSv (every vehicle is a 5 x 1.8 m blocker), buildings (roundabout) |

Leader flow: dynamic grant per packet (PDB 100 / 10 / 50 ms). Background:
periodic1 = SPS (RRI 100 ms), aperiodic1 = dynamic grant, broadcast, no HARQ,
each background vehicle on its own destination L2 ID. Every UE still decodes
everybody's SCI-1A for sensing, but nobody spends CPU decoding background
PSSCH.

> **Why the leader uses dynamic grants.** CTTC's SPS sizes the reservation
> from the first packet in the buffer and never re-selects when a later SDU
> does not fit (TS 38.321 would trigger reselection). With periodic1 (190/300 B)
> every 300 B packet was split across two 100 ms periods (pilot: 94 ms mean
> delay, 28 % within the PDB). `--leaderSps=true` reproduces it.

## Choice of the background densities

Measured per-vehicle load of the 60/40 mix at MCS 14 is ~33 subchannel-slots/s,
i.e. **~0.73 % of the pool per background vehicle** (pilot: CBR 0.21 at N=30, 0.44 at N=60).

| N | CBR from background | regime |
|---|---|---|
| 0 | 0 | channel only |
| 15 | ~0.11 | light |
| 30 | ~0.22 | moderate |
| 45 | ~0.33 | high |
| 60 | ~0.44 | very high (around the 3GPP congestion-control thresholds) |

The leader adds 0.01 (periodic1), 0.02 (aperiodic1) or 0.11-0.14 (periodic2).
N is capped at 60 because the roundabout gridlocks above ~70-80 vehicles in
the 500 m area (the original export already gridlocks at density 80). The same N
is used in both scenarios.

**Area of interest** (who is simulated in ns-3): highway: vehicles within
+/-1 km of the platoon centre on the 5 non-platoon lanes (both directions);
roundabout: within 500 m of the roundabout centre (density kept constant with the
original TraCI controller). Vehicles entering / leaving the area occupy reusable
ns-3 "seats" (parked > 1000 km away when idle, beyond `MaxLossDb`).

## HARQ Option 1 emulation (no change to contrib/nr)

* **Receivers**: `common/mcd/mcr_nack_only_gate` drops ACKs (MCR 500 m), so only
  NACKs are sent on PSFCH.
* **Leader**: `nack_only_harq.{h,cc}` wraps the leader's PHY<->MAC SAPs. For each
  PSSCH transmission in slot n it finds the PSFCH occasion m (first PSFCH slot
  >= n+3), and half a symbol after the NACKs would reach the MAC
  (m + 13 symbols + 1 slot) it decides: **no NACK => success** (TS 38.321),
  i.e. the remaining reserved retransmissions are cancelled; **NACK => the next
  reserved retransmission goes out**. Without it CTTC, which cancels pending
  retransmissions only on an ACK, would retransmit every TB 3 times blindly.
  One ACK per member is delivered, so this also works with a local
  "all members must ACK" variant of `NrSlUeMacHarq::RecvHarqFeedback`.
* No contrib/nr modification is needed beyond `patches/nr/local.patch`.
* Verified: 0 retransmissions after an implicit ACK, every retransmission is
  preceded by a NACK.

## KPIs (per run, computed in C++)

For every leader packet in the KPI window and every member:

| cause | definition |
|---|---|
| received | delivered to the member's application (PRR counts it) |
| **collision** | lost, and at least one *failed* transmission attempt of the packet at that member overlapped in time (slot) and frequency (>= 1 subchannel) with another vehicle's PSSCH |
| **channel** | lost, transmitted, but no failed attempt was in collision: path loss, shadowing, NLOS / NLOSv, fading |
| not_tx | the leader never transmitted it (no resource within the PDB) |
| other | every attempt decoded at PHY but not delivered (RLC); 0 in practice |

The packet-to-TB mapping is exact: the leader's packets carry a `PlatoonSeqTag`
byte tag, read back from every MAC PDU handed to the PHY.

Also reported: leader resource-collision rate (share of its transmissions that
overlap another PSSCH), CBR, transmissions per packet, NACK statistics, mean
SINR, LOS/NLOSv/NLOS share of the leader->member links, PRR within the PDB
(`prr_pdb`) and mean delay. Run CSV columns added with the variants:
`option1_tx`, `sl_slot_frac`, `mcs_rx_min/max` (MCS decoded by the members),
`cbr_occupied` (each subchannel-slot once; `cbr` counts overlaps twice and is an
offered load that can exceed 1) and `rlc_reordering_ms`.

### RLC t-Reordering: delay KPIs of the original runs are invalid

NR RLC UM (TS 38.322) has no t-Reordering: a complete SDU goes up as soon as it is
received. 5G-LENA's sidelink reuses **LTE RLC UM** (`ns3::LteRlcUm`), whose
in-order delivery and 100 ms `ReorderingTimer` hold every SDU behind a lost PDU.
Members that lose packets often get up to ~100 ms of artificial delay: tail
member, periodic2, highway IVD 30, N = 0: 92 % of its delivered packets arrive
> 100 ms late (median 150 ms), member 1 within ~5 ms.

Pilot (same trace, 20 s), t-Reordering 100 ms -> 0 ms:

| traffic, MCS | PRR | tail PRR | tail `prr_pdb` | tail mean delay |
|---|---|---|---|---|
| periodic1, 14 | 0.963 (same) | 0.853 (same) | 0.73 -> 0.85 | 22.5 -> 7.4 ms |
| periodic1, 5 | 0.954 (same) | 0.822 (same) | 0.66 -> 0.82 | 26.5 -> 5.8 ms |
| periodic2, 14 | 0.874 (same) | 0.466 (same) | 0.00 -> 0.47 | 149 -> 6.8 ms |
| periodic2, 5 | 0.767 (same) | 0.246 (same) | 0.00 -> 0.00 | 146 -> 15.3 ms |

* **PRR is identical**, so every PRR / loss result in this README stands.
* **`prr_pdb` and delays of the original runs (`results/campaign`,
  `results/variants`) are invalid.** Without the artifact `prr_pdb` = PRR except
  periodic2 at MCS 5/9, whose ~15 ms delay is real (2-3 TBs per packet).
* The program now sets `ReorderingTimer` = 0 by default (before the command line,
  so `--ns3::LteRlcUm::ReorderingTimer=100ms` reproduces the old behaviour) and
  writes `rlc_reordering_ms` to the run CSV. The runners pass
  `--rlc-reordering-ms` (default 0), check it after every run and write to
  `results/campaign_rlc0/` and `results/variants_rlc0/`; `analyze.py` refuses to
  mix values. Old CSVs without the column count as 100 ms.
* **Re-run done (server, 2026-10-11)**: `campaign_rlc0` (270 runs) and `variants_rlc0`
  (MCS 14, dedicated, blind; MCS 5/9 not re-run). Same seeds and traces: PRR identical
  (campaign: 80 / 81 cells to 3 decimals, the last one had 2 seeds before; variants:
  ≤ 0.0015), losses, CBR and tx per packet unchanged. Now `prr_pdb` = PRR for periodic1 and
  aperiodic1; periodic2 keeps a real gap (mean 0.03) from multi-TB packets finishing after
  the 10 ms PDB. Results: "Variant results with t-Reordering 0".

## Files

| file | role |
|---|---|
| `gen_mobility.py` | SUMO/TraCI mobility traces (both scenarios, platoon + seats) |
| `platoon_harq_robustness_eval.cc` | ns-3 program |
| `nack_only_harq.{h,cc}` | Option-1 TX decision + packet->TB tagging |
| `sumo_buildings.{h,cc}` | SUMO buildings -> ns3::BuildingList (from roundabout_rsu_eval) |
| `sumo/highway`, `sumo/roundabout` | copied scenario assets |
| `run_campaign.py` | resumable parallel sweep (`results/campaign_rlc<ms>/`) |
| `analyze.py` | aggregation + figures |
| `run_variants.py` | MCS / dedicated carrier / blind sweep + its analysis (`results/variants_rlc<ms>/`) |
| `run_coop.py` | cooperative HARQ comparison + its analysis, branch `coop-harq-nr` (`results/coop_rlc0/`) |
| `run_hidden_node.{py,sh}`, `analyze_hidden_node.py` | hidden-node test: runner, server driver, analysis (branch `hidden-node`, README "Hidden-node test") |

```bash
cd ns-3-dev/cmake-cache && ninja scratch_platoon_harq_robustness_eval_platoon_harq_robustness_eval
cd ../../scratch_overlay/platoon_harq_robustness_eval
python3 run_campaign.py --seeds 1 2 3 --jobs 7          # -> results/campaign_rlc0
python3 analyze.py --results results/campaign_rlc0 --out results/analysis_rlc0
```

Single run:

```bash
python3 gen_mobility.py --scenario roundabout --ivd 15 --nbg 30 --seed 1 --out traces/x.csv.gz
../../ns-3-dev/build/scratch/platoon_harq_robustness_eval/ns3.42-platoon_harq_robustness_eval-default \
  --scenario=roundabout --mobility=traces/x.csv.gz --sumoNet=sumo/roundabout/roundabout.net.xml \
  --traffic=periodic2 --out=results/x
```

## Results (3 seeds, 270 runs; `results/analysis/`)

Original runs (`results/campaign`, RLC t-Reordering 100 ms): PRR and loss
figures below are valid; their `prr_pdb` / delays are not (see "RLC t-Reordering").
The t-Reordering 0 re-run (server `results/campaign_rlc0`, aggregates in
`results/analysis_rlc0`) gives the same PRR and valid `prr_pdb` / `mean_delay_ms` per member
(`members_all.csv`; `summary.csv` aggregates PRR and losses only).

Reproduced on a second machine (stock-patch NR build, different SUMO
version): mean |PRR difference| over the 81 cells 0.005, max 0.024, never
outside the 95 % CIs.

**Average PRR per member, no background (channel only)**

| IVD | periodic1 hwy / rbt | periodic2 hwy / rbt | aperiodic1 hwy / rbt |
|---|---|---|---|
| 5 m | 0.997 / 0.999 | 0.982 / 0.991 | 0.951 / 0.974 |
| 15 m | 0.988 / 0.965 | 0.950 / 0.891 | 0.868 / 0.824 |
| 30 m | 0.968 / 0.858 | 0.878 / 0.745 | 0.739 / 0.670 |

**Average PRR per member, 60 background vehicles (CBR 0.43-0.59)**

| IVD | periodic1 hwy / rbt | periodic2 hwy / rbt | aperiodic1 hwy / rbt |
|---|---|---|---|
| 5 m | 0.964 / 0.963 | 0.916 / 0.928 | 0.786 / 0.805 |
| 15 m | 0.932 / 0.906 | 0.846 / 0.839 | 0.667 / 0.683 |
| 30 m | 0.864 / 0.804 | 0.741 / 0.712 | 0.535 / 0.556 |

1. **Channel.** With a short platoon (IVD 5 m, 80 m long) the roundabout is as
   good as or better than the highway (all of it fits on one arm + the ring,
   lower speed). From IVD 15 m on, the tail is still on the northern arm while
   the leader turns west: buildings block 34 % (15 m) / 45 % (30 m) of the
   leader->tail transmissions and the roundabout loses 2-13 points of PRR. The
   NLOS episodes last 15-20 s (fig7), far longer than the HARQ round trip, so
   the two retransmissions cannot bridge them; every link beyond member 1 is
   NLOSv in both scenarios (intra-platoon blockage).
2. **HARQ budget.** periodic1 (100 ms PDB) recovers most channel errors
   (PRR >= 0.86 everywhere). periodic2 (10 ms PDB) almost never gets a second
   retransmission in this pool (HARQ RTT >= 5 slots, 9 SL slots / 20): third
   transmissions are 0.8 % of its PSSCH transmissions vs 4.8 % for periodic1.
   aperiodic1 packets > ~1.4 kB need two TBs at MCS 14 and both must survive.
3. **Interference / collisions.** Loss grows almost linearly with the number of
   surrounding vehicles; at 60 vehicles 36-58 % of the leader's transmissions
   overlap another PSSCH. The highway degrades faster (open LOS: every
   interferer is heard): from 0 to 60 vehicles its loss grows by 3-21 points vs
   3-17 on the roundabout, and at IVD 30 m the highway's channel advantage
   shrinks from 11-13 points to 3-6 points. Aperiodic (dynamic-grant) traffic is
   hit hardest because its resources cannot be anticipated by sensing.
4. **Reading the loss split.** A lost packet with any failed attempt in
   collision is counted as a collision loss even if its channel was also poor,
   so the channel share shrinks as density grows (fig2); compare total loss
   across densities.

## Variants: MCS, dedicated carrier, blind retransmission (`run_variants.py`)

Reduced one-factor-at-a-time sweep around the baseline (HANDOFF.md §11, steps 1-3):
IVD 30 m × N ∈ {0, 45, 60} × both scenarios × the three traffic types × 3 seeds, same
mobility traces as the campaign. The MCS-14 baseline is reused from the campaign
directory of the same RLC t-Reordering (`mcs14` runs the cells it lacks).

| variant | flags |
|---|---|
| `mcs5`, `mcs9` | `--mcs=5`, `--mcs=9` |
| `dedicated` | `--dedicatedCarrier=true`: TDD `UL×10` + all-ones bitmap, every slot SL (new, `SlStackParams::dedicatedCarrier`) |
| `blind` | `--option1Tx=false --psfchPeriod=0`: no PSFCH, always 3 transmissions |

Flag checks (pilots, highway IVD 30 N = 0):
- `--mcs`: the MCS decoded by the members equals the requested one (new run-CSV columns
  `mcs_rx_min/max`); subchannels per TB 1.2 / 2.2 / 3.2 at MCS 14 / 9 / 5 (periodic1).
- `--dedicatedCarrier`: transmissions on all 10 slot offsets (4-9 only otherwise), `sl_slot_frac` = 1.
- **`--option1Tx=false` alone is not blind retransmission.** The flow stays HARQ-enabled with
  PSFCH, so CTTC's `SelectResourcesWithConstraint` keeps the PSFCH gap between the reserved
  resources: 2-3 transmissions per TB, periodic2 never gets a third. With `--psfchPeriod=0`
  CTTC uses `SelectResourcesForBlindRetransmissions` and reserves all 3: tx per TB = 3.00
  (periodic1/2), 0 NACKs.
- `run_variants.py` re-checks MCS, option1_tx, psfch_period, slot fraction, RLC t-Reordering
  and explicit ACKs after every run and logs `FLAG CHECK FAILED` on a mismatch.

Caveats found on the way:
- `cbr` sums all PSSCHs, so collided transmissions count twice: it is the offered load and can
  exceed 1 (MCS 5, periodic2, N = 45: 1.11). New column `cbr_occupied` counts each
  subchannel-slot once (0.69 in that cell). The campaign CSVs only have `cbr`.
- periodic2 at MCS 5/9 needs 2-3 TBs per packet; segments pipeline with the next packet and
  the delay settles at ≈ 15 ms > 10 ms PDB, so `prr_pdb` ≈ 0 although `prr` is not. This
  delay is real; the much larger delays in the original runs are the RLC artifact above.
- **Known issue (open):** MCS 5, roundabout, periodic2 can hit a truncated application
  packet (`SeqTsSizeHeader::Deserialize` reads past the buffer). The debug build aborts
  (laptop: N = 0 seed 3, deterministic, also with t-Reordering 0); the optimized build
  hung once (server: N = 45 seed 3, killed). Each machine has that one cell at 2 seeds.
  Runs that completed in the debug build cannot have hit it, and the server agrees with
  them, so the results are not contaminated; fix before running more MCS 5/9 cells.

### Variant results (server = reference, original runs with t-Reordering 100 ms)

PRR is valid (unaffected by t-Reordering); `prr_pdb` is not reported here. IVD 30 m,
3 seeds (2 for MCS 5 / roundabout / periodic2 / N = 45). Values highway / roundabout.

**PRR, mean of the 8 members**

| traffic | N | MCS 14 (base) | MCS 9 | MCS 5 | dedicated | blind |
|---|---|---|---|---|---|---|
| periodic1 | 0 | 0.97 / 0.86 | 0.96 / 0.87 | 0.96 / 0.86 | 0.97 / 0.86 | 0.97 / 0.86 |
| periodic1 | 45 | 0.90 / 0.83 | 0.86 / 0.81 | 0.78 / 0.77 | 0.94 / 0.84 | 0.90 / 0.84 |
| periodic1 | 60 | 0.89 / 0.80 | 0.83 / 0.77 | 0.73 / 0.73 | 0.93 / 0.83 | 0.90 / 0.81 |
| periodic2 | 0 | 0.88 / 0.75 | 0.70 / 0.64 | 0.77 / 0.71 | 0.91 / 0.76 | 0.91 / 0.78 |
| periodic2 | 45 | 0.80 / 0.71 | 0.37 / 0.44 | 0.36 / 0.44 | 0.88 / 0.76 | 0.84 / 0.75 |
| periodic2 | 60 | 0.74 / 0.69 | 0.30 / 0.39 | 0.29 / 0.40 | 0.86 / 0.75 | 0.80 / 0.72 |
| aperiodic1 | 0 | 0.74 / 0.67 | 0.78 / 0.68 | 0.79 / 0.74 | 0.74 / 0.66 | 0.74 / 0.66 |
| aperiodic1 | 45 | 0.59 / 0.58 | 0.47 / 0.53 | 0.41 / 0.48 | 0.69 / 0.64 | 0.60 / 0.59 |
| aperiodic1 | 60 | 0.55 / 0.55 | 0.38 / 0.45 | 0.30 / 0.43 | 0.66 / 0.61 | 0.54 / 0.54 |

**PRR, tail member (member 8)** (95 % CIs ±0.1-0.5 with 3 seeds)

| traffic | N | MCS 14 (base) | MCS 9 | MCS 5 | dedicated | blind |
|---|---|---|---|---|---|---|
| periodic1 | 0 | 0.86 / 0.58 | 0.87 / 0.64 | 0.85 / 0.62 | 0.88 / 0.61 | 0.89 / 0.60 |
| periodic1 | 45 | 0.86 / 0.61 | 0.80 / 0.60 | 0.67 / 0.57 | 0.91 / 0.59 | 0.86 / 0.62 |
| periodic1 | 60 | 0.85 / 0.56 | 0.75 / 0.53 | 0.52 / 0.49 | 0.87 / 0.60 | 0.85 / 0.58 |
| periodic2 | 0 | 0.59 / 0.41 | 0.28 / 0.26 | 0.39 / 0.37 | 0.64 / 0.46 | 0.66 / 0.48 |
| periodic2 | 45 | 0.54 / 0.44 | 0.14 / 0.19 | 0.13 / 0.22 | 0.65 / 0.51 | 0.60 / 0.49 |
| periodic2 | 60 | 0.51 / 0.46 | 0.09 / 0.19 | 0.08 / 0.21 | 0.68 / 0.51 | 0.61 / 0.48 |
| aperiodic1 | 0 | 0.37 / 0.31 | 0.43 / 0.33 | 0.43 / 0.41 | 0.37 / 0.32 | 0.37 / 0.31 |
| aperiodic1 | 45 | 0.40 / 0.31 | 0.28 / 0.28 | 0.21 / 0.28 | 0.47 / 0.37 | 0.39 / 0.31 |
| aperiodic1 | 60 | 0.30 / 0.28 | 0.19 / 0.21 | 0.18 / 0.26 | 0.37 / 0.32 | 0.32 / 0.28 |

**Reproduction.** Laptop (local NR tree with extra coop-HARQ changes, debug build) vs
server (committed NR patch only, optimized build), 90 common cells: mean |ΔPRR| 0.007,
max 0.030; tail mean |Δ| 0.024; **no cell outside the combined 95 % CI** (PRR or tail).

1. **Lower MCS does not rescue the tail.** Without background it helps only marginally
   (aperiodic1 +0.05 / +0.09 hwy / rbt tail, inside the CIs); with background it hurts
   everywhere. Per attempt, MCS 5 decodes more often (tail periodic1, N = 0: 0.68 -> 0.77
   hwy, 0.35 -> 0.50 rbt) but (a) 23 dBm spread over ~3x more subchannels costs ~4 dB of
   SINR, (b) on the roundabout the tail misses the SCI in ~20 % of attempts behind
   buildings (blockage of tens of dB lasting 15-20 s; the PSCCH does not depend on the
   data MCS), (c) packets split into 2-4 TBs that must all survive, (d) the platoon
   occupies 2-3x more resources, so collisions dominate under load (offered load > 1 at
   N = 60).
2. **The dedicated carrier is the clear winner under load** (same traffic occupies
   < half the pool): +0.03-0.12 mean PRR at N = 60; periodic2 gets its second
   retransmission.
3. **Blind retransmission ~ Option 1**, slightly better for periodic2 without background
   (Option 1 cannot fit its second retransmission before the 10 ms PDB), paid with 3
   transmissions per TB (more load under background).

### Variant results with t-Reordering 0 (server, `results/variants_rlc0`)

periodic1 / periodic2 at MCS 14 and on the dedicated carrier have **10 seeds**, the other
cells 3. PRR equals the 100 ms runs (tables above). **Tail member PRR within the PDB**
(`prr_pdb_tail`), IVD 30 m, ± 95 % CI, highway / roundabout:

| traffic, variant | N = 0 | N = 45 | N = 60 |
|---|---|---|---|
| periodic1, MCS 14 | 0.925 ± 0.044 / 0.608 ± 0.016 | 0.835 ± 0.029 / 0.607 ± 0.060 | 0.819 ± 0.056 / 0.585 ± 0.057 |
| periodic1, dedicated | 0.930 ± 0.041 / 0.621 ± 0.023 | 0.879 ± 0.037 / 0.622 ± 0.047 | 0.897 ± 0.029 / 0.620 ± 0.060 |
| periodic2, MCS 14 | 0.683 ± 0.106 / 0.418 ± 0.029 | 0.519 ± 0.101 / 0.449 ± 0.066 | 0.488 ± 0.098 / 0.416 ± 0.058 |
| periodic2, dedicated | 0.751 ± 0.122 / 0.457 ± 0.028 | 0.691 ± 0.100 / 0.478 ± 0.069 | 0.687 ± 0.082 / 0.470 ± 0.075 |

With 100 ms the highway periodic2 tail values were 0.002-0.086 — that was the RLC
artifact. Under load the dedicated carrier gives the tail its largest gain on the highway
(periodic2, N = 60: 0.687 vs 0.488); on the roundabout the tail stays at 0.42-0.62 whatever
the load or carrier (building blockage). Mean PRR within the PDB and the 3-seed cells
(aperiodic1, blind): `variants_summary.csv`.

```bash
python3 run_variants.py --jobs 7          # t-Reordering 0: mcs14 + 4 variants -> results/variants_rlc0
python3 run_variants.py --analyze-only    # results/variants_rlc0/analysis/
# original 100 ms results
python3 run_variants.py --analyze-only --rlc-reordering-ms 100 \
      --results results/variants --baseline-dir results/campaign
```

On another machine (after `git pull` and `./ns3 build platoon_harq_robustness_eval`),
traces missing from `traces/` are generated with the local SUMO. Heavy runs
(highway N = 60) take > 0.5 GB, so keep `--jobs` ≤ RAM/2.5 GB. Run the campaign first
so the variants reuse its MCS-14 cells:

```bash
nohup sh -c 'python3 run_campaign.py --jobs 12 && python3 run_variants.py --jobs 12' \
      > results/rlc0_stdout.log 2>&1 &
```

## Cooperative HARQ: helper arms (branch `coop-harq-nr`)

A designated platoon member (the *helper*) retransmits the leader's TB for the members that
NACKed it. Needs the `coop-harq-nr` NR patch (`patches/nr/local.patch` on that branch:
`NrSlUeMac::EnableChainedRes`). With cooperation off the patched NR tree is **byte-identical**
to the stock tree (4 pilots, Option 1 / blind / dedicated carrier / N = 45, every output file).
On another machine: `git checkout coop-harq-nr && ./setup.sh --force` (installs that patch),
then rebuild; back to stock the same way from `platoon-harq-robustness-eval`.

| `--helperMode` | leader | helper (member `--helper`) |
|---|---|---|
| `off` (default) | stock scheduler, Option 1, 2 retx | — (original campaign) |
| `none` | `NrSlCoopScheduler`, cooperation off: Option 1, 2 retx | — (leader-only arm of the comparison) |
| `leaderRes` | 1 transmission; its 2 reserved retx slots are handed to the helper (SCI-2A) | relays in those slots |
| `ownRes` | 1 transmission, no reservation; SCI-2A carries the packet deadline (allocation slot + PDB) and the relay budget (2) | selects its own resources (Mode 2, its own sensing) when the NACK is heard |

Helper rules (both arms, HARQ Option 1): a relay goes out only if the helper **decoded** the
leader's copy and **overheard a NACK** on the PSFCH of the previous transmission of the TB (the
leader's, then the helper's own copy, which the members NACK too); no NACK = success, the
remaining relay slots are cancelled. Receivers **soft-combine** a relayed copy in the leader's
HARQ process (SCI-2A `relayedFromRnti`, leader's HARQ id and source L2 id). The helper relays
the TB unchanged (MCS, TB size, subchannels).

Run-CSV columns: `helper_tx`, `helper_tx_coll_rate`, `coop_*` (relay events: scheduled, sent,
no NACK, too late, no HARQ process, no resource), the checks `relay_without_nack` and
`relay_without_decode` (must be 0), `helper_att(_ok)`, `helper_recovered` (members that decoded
a TB only thanks to the helper's copy), and the **sensing attribution of failed, collided
attempts** `lcoll_*` (leader attempts) / `hcoll_*` (helper attempts): `neither`,
`helper_only`, `leader_only`, `both` = which of leader / helper had decoded an SCI-1A of an
interferer in the window [attempt − (100 ms sensing window + PDB), attempt). Per interferer:
`<tag>_coll.csv`; helper transmissions: `<tag>_htx.csv`. Packet loss causes, attempts and
`tx_per_pkt` include the helper's copies.

Pilot (roundabout, IVD 30 m, N = 0, periodic1, helper = member 4, seed 1):

| arm | PRR | worst member | leader retx | relays sent | relay w/o NACK | relay w/o decode |
|---|---|---|---|---|---|---|
| none | 0.839 | 0.576 | 459 | — | — | — |
| leaderRes | 0.878 | 0.731 | 0 | 258 | 0 | 0 |
| ownRes | 0.846 | 0.656 | 0 | 232 | 0 | 0 |

Relayed copies at the members: 455 / 471 (leaderRes) combined with >= 1 earlier attempt
(the other 16: members that had missed the leader's SCI, decoded alone).

Caveats: PSFCH is lossless in 5G-LENA (overhearing is limited only by `MaxLossDb`); with Option 1
the leader cannot tell who NACKed, so a handed-over slot is lost if the helper itself missed
the TB (`leaderRes`); a member that misses a TB's first transmission may combine with a stale
history of the same HARQ id (stock behaviour, rare); RNTIs exist only once the run starts
(the helper is configured at ~2 s, before the KPI window).

Comparison campaign (`run_coop.py`): arms `none` / `leaderRes` / `ownRes` x {roundabout
IVD 30 N = 0 (blockage), highway IVD 30 N = 45, 60 (collisions)} x {periodic1, periodic2} x
seeds 1-10, helper = member 4, t-Reordering 0; every run re-checked (mode, helper,
t-Reordering, no explicit ACK, relay_without_nack = relay_without_decode = 0). Outputs in
`results/coop_rlc0/` (`analysis/`: `coop_summary.csv`, `coop_runs.csv`, `coop_members.csv`,
`coop_latency_cdf.csv`, `figc1_prr.png`, `figc1b_tail_prr.png`, `figc2_tail_prr_pdb.png`,
`figc3_collision_sensing.png`, `figc4_latency.png`, `figc5_reliability_latency.png`).

**Latency** (analysis only, from `<tag>_pkt.csv.gz`, no re-run needed): application
end-to-end delay = first delivery at the member − generation at the leader (resource wait,
HARQ retransmissions and relayed copies included). `coop_summary.csv`: `lat_mean_ms`,
`lat_p50/p95/p99_ms` (all members) and `lat_tail_*` (tail member), over delivered packets,
mean and 95 % CI over seeds. `coop_latency_cdf.csv` / figc5: P(delivered within t), every
packet counted (lost = never), so the curve ends at the PRR — the fair comparison, since
the percentiles ignore lost packets. `delay_ms` is written with 2 significant digits (0.1 ms
below 10 ms, 1 ms above), too coarse to separate the arms in periodic2's p95 (≈ PDB).

```bash
python3 run_coop.py --jobs 7            # 180 runs (laptop: 7.9 h)
python3 run_coop.py --analyze-only      # ~6 s
```

**Results (laptop, 10 seeds; server re-run pending — quote the server once available).**
Tail = member 8, ± 95 % CI; latency = mean over the members' delivered packets.

| cell | leader only | helper, leader's slots | helper, own resources |
|---|---|---|---|
| rbt N 0 periodic1, tail PRR | 0.597 ± 0.013 | **0.697 ± 0.020** | 0.677 ± 0.016 |
| rbt N 0 periodic2, tail PRR | **0.464 ± 0.031** | 0.462 ± 0.038 | 0.411 ± 0.050 |
| hwy N 45 periodic1, tail PRR | **0.811 ± 0.048** | 0.785 ± 0.041 | 0.773 ± 0.040 |
| hwy N 45 periodic2, tail PRR | **0.591 ± 0.105** | 0.510 ± 0.077 | 0.449 ± 0.096 |
| hwy N 60 periodic1, tail PRR | **0.797 ± 0.040** | 0.766 ± 0.027 | 0.740 ± 0.036 |
| hwy N 60 periodic2, tail PRR | **0.546 ± 0.085** | 0.465 ± 0.061 | 0.403 ± 0.076 |
| periodic1, mean latency (3 cells) | 5.0-5.6 ms | 4.8-5.2 ms | 10.1-11.0 ms |
| periodic1, tail p95 latency | 11.8-12.1 ms | 10.4-13.5 ms | 24.1-27.0 ms |
| periodic2, mean latency | 4.9-5.6 ms | 5.8-6.2 ms | 6.5-6.9 ms |

1. **Blockage: the helper helps** (roundabout periodic1, tail 0.60 -> 0.70, CIs disjoint);
   not with periodic2 (not investigated yet).
2. **Collisions: the helper hurts** (highway: mean PRR −0.03 to −0.13, tx per packet 1.6-1.8 ->
   1.1-1.4). The leader sends once and hands its retx slots over; when the helper also missed
   the TB nobody uses them (Option 1: the leader cannot tell who NACKed).
3. An interferer of a collided, failed attempt had been sensed by **both** leader and helper
   in 94-96 % (periodic1) / 79-82 % (periodic2) of the cases: not hidden-terminal
   collisions, the helper's sensing cannot avoid them.
4. **Latency**: on the leader's reserved slots the helper keeps the leader-only latency; on
   its own resources (Mode-2 selection over the remaining PDB) it doubles the periodic1 mean
   and tail p95.

**Check `relay_without_nack` is too strict.** 2 of the 180 laptop runs flagged one relay each
(`leaderRes`, highway N 45 periodic2 s1 and N 60 periodic1 s4). Debugged on the second
(deterministic; PHY / channel logs): the tail member NACKed, the helper and every member got
the NACK, but the leader's link had 156.9 dB path loss (TR 37.885 NLOSv log-normal blockage
loss, drawn per transmission, in its tail) > `MaxLossDb` = 150 dB, so the PSFCH never reached
it. The leader took "no NACK" as success; the helper relayed correctly. The check compares
with the NACKs the leader *received*; it should use those the members *sent* (to do), and
"NACK missed by the leader" is worth reporting as an Option-1 KPI.

```bash
./ns3 build platoon_harq_robustness_eval
build/scratch/platoon_harq_robustness_eval/ns3.42-platoon_harq_robustness_eval-<profile> \
  --scenario=roundabout --mobility=traces/rbt_ivd30_n0_s1.csv.gz \
  --sumoNet=sumo/roundabout/roundabout.net.xml --traffic=periodic1 \
  --helper=4 --helperMode=leaderRes --out=results/x
```

## Cooperative HARQ: follow-up (2026-10-12, branch `coop-harq-nr`)

Three questions: can a helper beat standard Option 1, and where (step 1); is the "sensing
footprint" hypothesis supported, i.e. do leader and helper sense different interferers
when buildings separate them (step 2); does the helper's position, or dynamic helper
selection, matter (step 3). Driver: `run_coop_followup.sh` (server), analysis:
`run_coop.py --analyze-only` (step 1), `analyze_coop_followup.py footprint | sweep`.

### New arms (step 1), HARQ Option 1, helper = member 4

| arm | leader | helper |
|---|---|---|
| `split` | initial + 1st reserved retx (retx on NACK, implicit ACK otherwise); the 2nd reserved retx slot is handed over | uses that slot only if it decoded the TB **and** heard a NACK on the PSFCH of the leader's retx |
| `additive` | unchanged Option 1 (initial + up to 2 retx, nothing handed over) | after a NACK on the leader's **initial** transmission, one copy (`--relayBudget`, default 1) on its own Mode-2 resource, window = remaining PDB |

`LeaderTxCount = 2` + `leaderRes` was not exactly `split`: (1) the scheduler handed the
*last* selected slot to the helper unconditionally, so with only 2 slots (frequent for
periodic2) the leader lost its own retx; (2) a NACK on the leader's *initial* transmission
already enabled the helper's slot, even if the leader's retx then reached everyone. Fixed:
only the slots after the first `LeaderTxCount` are handed over (identical for
`LeaderTxCount = 1`), and in split mode the leader puts its last own reserved slot before
the handed-over one into the SCI-2A `deadline` field (unused in handed-over mode); the
helper counts only NACKs received after that slot. `additive` marks the SCI-2A
`ownRes = 2`: the helper keeps only the leader's initial transmission (rv 0) and listens
to its PSFCH occasion only (`NackKnownAfter` + 1 slot for a member whose PSFCH is pushed to
the next occasion); a copy that cannot go out before the deadline counts as `too late`.
Members soft-combine the copies in the leader's HARQ process (as before). HARQ ids are
reused FIFO over 16 processes, so a leader reuses an id only ~16 TBs later (>= 150 ms,
above every PDB): a copy cannot hit a reused process; the run check
`relay_into_reused_harq` (latest leader transmission on that HARQ id before the copy
belongs to another TB) verifies it.

**`none4` (leader-only with `sl-MaxTxTransNumPSSCH = 4`) is not feasible here.** The
scheduler draws the transmissions of a TB inside T2 = 33 slots with the PSFCH and
processing gaps between them; in this pool (9 SL slots per 20) it never finds a 4th
(roundabout pilot: 138 TBs with 3 resources, 41 with 2, none with 4). A cost-matched
leader-only arm would need a longer T2 or another pool, i.e. a different configuration.
Instead every arm reports `tx_per_pkt` and `cbr_occupied`, and `figc7_reliability_vs_cost`
plots tail `prr_pdb` against `tx_per_pkt`.

**The leader NACKed the helper's copies of its own TB (fixed).** The leader monitors the
group destination, so it received the helper's relayed copies of its own TBs, tried to
decode them (combining into its own HARQ id) and, when that failed, sent a NACK; the helper
took it as a NACK on its copy and sent its 2nd relay although no member had asked
(`leaderRes` / `ownRes` have a relay budget of 2; roundabout N 0, helper 7, 25 s: 8 of 71
relays). Found by the corrected `relay_without_nack` check. Now the original transmitter
ignores relayed copies of its own TBs (no decoding, no feedback; NR PHY). **The 2026-10-11
`leaderRes` / `ownRes` results contain these spurious relays and are re-run in step 1**;
`none` has no relays and is unchanged (reused), `split` / `additive` have a budget of 1.

**Identity.** Cooperation off (stock path) and `none` are byte-identical to the 2026-10-11
code (pilots: highway N 45 and roundabout N 0, every output file). `leaderRes` / `ownRes`
are identical where no spurious 2nd relay happened (roundabout N 0, helper 4) and differ
where it did (highway).

**Pilots** (laptop, seed 1, full runs; every check 0):

| cell | arm | PRR | tail `prr_pdb` | `tx_per_pkt` | helper copies | combined with an earlier attempt / first attempt | other |
|---|---|---|---|---|---|---|---|
| roundabout N 0 periodic1 | split | 0.893 | 0.662 | 1.87 | 122 | 315 / 0 | leader <= 2 tx per TB |
| | additive | 0.901 | 0.715 | 2.32 | 234 | 320 / 3 | 137 copies redundant |
| highway N 45 periodic2 | split | 0.791 | 0.266 | 1.88 | 889 | 1434 / 99 | leader <= 2 tx per TB |
| | additive | 0.796 | 0.318 | 2.06 | 1511 | 2431 / 770 | 1727 too late, 2047 no resource, 856 redundant |

(leader-only, same seed: roundabout 0.839 / 0.576 / 1.90, highway 0.791 / 0.276 / 1.89.)
One seed: no conclusion; they show the arms work as designed and their cost. "Redundant" =
the copy followed the leader's own retransmission, which had already reached everyone
(`relay_without_nack` for this arm). Members combine 76-100 % of the copies they attempt
with an earlier failed attempt (lowest: highway additive, 76 %; the cause of the rest is
not analysed).

**Checks** (run CSV, `FLAG CHECK FAILED` otherwise): `leader_retx_without_nack` = 0 (every
arm); `relay_without_decode`, `relay_into_reused_harq` = 0 (helper arms);
`relay_without_nack` = 0 (leaderRes, ownRes, split; members' NACKs only — the leader's own
NACK on a copy no longer exists, see above), `relay_before_leader_retx` = 0 and
`leader_tx_per_tb_max` <= 2 (split), `relay_without_initial_nack` = 0 (additive; its
`relay_without_nack` counts the redundant copies, sent after the leader's own retx already
reached everyone). New KPIs: `leader_tx_nacked`, `leader_nack_missed` (every arm),
`helper_att_after_fail` / `helper_att_first` (helper copies a member combined with an
earlier failed attempt / received first).

Report per cell (± 95 % CI): tail `prr_pdb` (primary), mean and worst-member `prr_pdb`,
`tx_per_pkt`, `cbr_occupied`, leader / helper collision rates, latency mean and tail p95,
`helper_recovered`; `coop_verdict.csv` states for every arm whether it beats `none` on
tail `prr_pdb` with disjoint CIs.

### Footprint test (step 2) — pre-registered before any result

Cells: roundabout IVD 30, N in {45, 60}, periodic1 and periodic2, seeds 1-10, helper 4,
arms `none`, `leaderRes`, `ownRes`, `split`, `additive` (both new arms passed the pilots;
all runs fresh, with `--collCond`).
Instrumentation: for every collided failed attempt the link condition leader -> interferer
and helper -> interferer at the attempt (`--collCond`: extra channel-condition queries,
which can change the random draws, hence only in fresh campaigns) and whether leader and
tail are on different road segments (ring / arm; the turn phase); for every selection of
the leader or the helper the **sensing-map divergence**: the (slot, subchannel) units of
the selection window that the selecting UE's sensing data excludes (RSRP above the
selection's initial threshold; periodic and same-period retransmission reservations, as
TS 38.214) vs those the other UE's sensing data excludes, same window, same threshold
(`<tag>_div.csv`; divergence = |A xor B| / |A or B|).

**Decision rule, fixed now:** the footprint hypothesis is **supported** if, in the turn
phase, `leader_only` + `helper_only` >= 15 % of the collided failed leader attempts, **or**
the median divergence of the leader's selections in the turn phase >= 15 %; otherwise it is
**rejected** and the footprint framing is dropped. Computed by
`analyze_coop_followup.py footprint` (`footprint_verdict.txt`), pooled over the step-2 runs.

### Helper sweep (step 3) — protocol fixed before any result

Roundabout IVD 30 N 0 (N 45 only if step 2 ran), periodic1/2, helpers {2, 4, 6, 7} (8 is
the tail), arms `leaderRes`, `ownRes`, `split`, `additive`, seeds 1-10; `none` reused.
Static oracle: best helper per traffic x arm on seeds 1-5 (tail `prr_pdb`), reported on
seeds 6-10 only, next to helper 4 and `none`. Dynamic oracle: tail `prr_pdb` in 1 s windows
per helper (same seed = same trace), per-window maximum over helpers = upper bound of
per-packet helper selection; how often the best helper changes in the turn phase.
Implementable rule (the only one): **the rearmost swept helper within X m (straight line,
from CAM positions) of the leader**, X in {50, 100, 150, 200, 250, 300} tuned on seeds 1-5,
evaluated on seeds 6-10 by composing the static runs window by window (an approximation).
The highway sweep runs only if step 1 shows an arm beating `none` on the highway.

```bash
JOBS=24 ./run_coop_followup.sh 1 2 3        # server; 3n45 after step 2 if wanted
```

Results: step 1 `results/coop2_rlc0/analysis/` (`coop_verdict.csv`, `figc2_tail_prr_pdb`,
`figc7_reliability_vs_cost`, ...), step 2 `results/coop_rbt_rlc0/analysis/` and
`analysis_footprint/` (`footprint_verdict.txt`, `figf1`, `figf2`), step 3
`results/coop_sweep_rlc0/analysis_sweep/` (`sweep_static.csv`, `sweep_gaps.csv`,
`sweep_rule.csv`, `figs1_tail_vs_helper`, `figs2_oracle_timeline`). New per-run files:
`<tag>_nack.csv` (every NACK a member sent), `<tag>_div.csv` (divergence per selection).

### Results (server = reference, 10 seeds, 2026-10-12; every run passed every check)

Tail (member 8) PRR within the PDB, mean over seeds; "paired" = per-seed difference to
`none` with its 95 % CI (same trace and seed in every arm), wins = seeds where the arm is
better; "CI" = the pre-registered criterion (disjoint 95 % CIs of the two means).

| cell | none | leaderRes | ownRes | split | additive | tx/pkt none -> additive |
|---|---|---|---|---|---|---|
| rbt N 0 periodic1 | 0.597 | **0.695** (+0.099, 10/10, CI) | **0.683** (+0.086, 10/10, CI) | **0.667** (+0.070, 10/10, CI) | **0.691** (+0.094, 10/10, CI) | 1.86 -> 2.26 |
| rbt N 0 periodic2 | 0.433 | 0.407 (n.s.) | 0.358 (worse, paired) | 0.423 (n.s.) | 0.463 (+0.030, 9/10, paired) | 1.64 -> 1.84 |
| rbt N 45 periodic1 | 0.601 | 0.652 (+0.051, 10/10) | 0.661 (+0.060, 10/10) | 0.637 (+0.036, 10/10) | 0.661 (+0.060, 10/10) | 1.82 -> 2.19 |
| rbt N 45 periodic2 | 0.441 | 0.414 (n.s.) | 0.373 (worse) | 0.443 (n.s.) | 0.461 (n.s.) | 1.65 -> 1.91 |
| rbt N 60 periodic1 | 0.567 | 0.613 (+0.046, 10/10) | 0.625 (+0.059, 10/10) | 0.610 (+0.043, 10/10) | 0.628 (+0.061, 10/10) | 1.83 -> 2.19 |
| rbt N 60 periodic2 | 0.416 | 0.375 (worse) | 0.328 (worse) | 0.412 (n.s.) | 0.417 (n.s.) | 1.67 -> 1.86 |
| hwy N 45 periodic1 | 0.797 | 0.784 (n.s.) | 0.801 (n.s.) | 0.799 (n.s.) | 0.817 (+0.020, 8/10) | 1.62 -> 2.00 |
| hwy N 45 periodic2 | 0.518 | 0.429 (worse) | 0.378 (worse) | 0.518 (n.s.) | 0.547 (+0.029, 10/10) | 1.77 -> 2.03 |
| hwy N 60 periodic1 | 0.783 | 0.767 (n.s.) | 0.773 (n.s.) | 0.781 (n.s.) | 0.817 (+0.034, 9/10) | 1.64 -> 2.04 |
| hwy N 60 periodic2 | 0.485 | 0.398 (worse) | 0.352 (worse) | 0.487 (n.s.) | 0.507 (+0.021, 8/10) | 1.71 -> 1.90 |

(+x, k/10) = paired better; "worse" = paired worse; CI = also disjoint 95 % CIs (only
roundabout N 0 periodic1). `cbr_occupied`: +0.001 to +0.014 for `additive`, down by up to
0.05 for `leaderRes` / `ownRes` (fewer transmissions), unchanged for `split`. Tables (mean /
worst-member `prr_pdb`, collision rates, latency, `helper_recovered`):
`remote-results/coop2_rlc0/analysis/coop_summary.csv`, `coop_rbt_rlc0/analysis/`.

**1. Can a helper beat the standard, and where?** With disjoint CIs only under blockage
with periodic1 traffic (roundabout N 0: all four helper arms, +0.07 to +0.10). Paired, the
same holds at N 45 / 60 on the roundabout (+0.04 to +0.06, 10/10 seeds). `additive` is the
only arm that never loses: it also gains a little on the highway (+0.02 to +0.03, paired) at
12-25 % more transmissions per packet; whether a leader with the same budget would do as
well could not be tested (`none4` infeasible). Handing the leader's retransmissions over
(`leaderRes`, `ownRes`) loses under collisions and with periodic2 (up to -0.14). `split`
equals `none` on the highway (the helper rarely gets its slot) and gains under blockage.

**2. Footprint hypothesis: supported by the pre-registered rule, but not as a turn effect.**
Turn phase: `leader_only` + `helper_only` = 22.7 % of the collided failed leader attempts
(rule: >= 15 %), median divergence of the leader's selections 0.40 (rule: >= 0.15). Outside
the turn: 18.9 % and 0.40, also above the threshold — the leader-helper sensing difference
on the roundabout exists all along, a little larger in the turn (periodic1: 10-11 % -> 17-19
%). It comes from buildings: P(interferer sensed by the leader) vs distance falls to 0.29 at
300-500 m and 0.06 at 500-750 m on the roundabout (highway: 0.80, 0.75); when the leader ->
interferer link is building-NLOS and the helper's is not, the helper senses 0.90, the
leader 0.61. In this simulator the difference cannot avoid collisions: dynamic grants skip
the sensing exclusion (5G-LENA, HANDOFF §8), for the leader and the helper alike.

**3. Helper position and dynamic selection.** Roundabout N 0, helpers 2 / 4 / 6 / 7: the
best static helper picked on seeds 1-5 is helper 4 for periodic1 in every arm (seeds 6-10:
same value as helper 4); rear helpers are worse (6, 7: 0.61-0.66 vs 0.69, leaderRes /
ownRes). The leader-side rule (rearmost helper within X m, X tuned on seeds 1-5) picks
X = 150 m, i.e. helper 4: no gain. The per-window dynamic oracle is +0.09 to +0.12 above
(periodic1), with the best helper changing 8-10 times in ~23 turn windows per seed; with
~10 packets per 1 s window this maximum over four noisy estimates is biased upward, so it
is an upper bound, not evidence that dynamic selection would pay. Highway sweep: skipped
as pre-registered (no arm beats `none` there with disjoint CIs); `additive`'s small paired
gain on the highway was measured with helper 4 only.

## Standard Mode-2 grant types and sensing fixes (2026-10-12, branch `coop-arms`)

Four opt-in options, all off by default; **with all of them off every output file is
byte-identical to `coop-arms` @ `7fd137f`** (9 pilots: highway N 45 periodic1/2/aperiodic1,
roundabout N 0 `none` / `leaderRes` / `split` / `additive` + `--collCond`, highway `ownRes`,
`--leaderSps=true` periodic1; the run CSV only gains the columns listed below).

| program flag | NR attribute (`ns3::NrSlUeMac::`) | what |
|---|---|---|
| `--leaderSps` (existing) | — | periodic leader traffic on SB-SPS (RRI = period) instead of a dynamic grant per packet |
| `--spsReselectOnMisfit` | `SpsReselectOnMisfit` | TS 38.321 sec. 5.22.1.1: an RLC SDU the selected SPS grant cannot accommodate at the configured MCS triggers a resource reselection sized for the new data, instead of CTTC's segmentation across periods. Every SPS UE (leader, background periodic1) |
| `--slDynExclusion` (D1) | `SlDynExclusion` | TS 38.214 sec. 8.1.4 step 6 with C_resel = 1 when no reservation interval is given: dynamic grants exclude sensed reservations (stock: the loop runs C_resel = 0 times) |
| `--slProjectAnnounced` (D2) | `SlProjectAnnounced` | step 6 also projects the retransmission resources an SCI-1A announces in its own period (TRIV gaps from the sensed slot), including SCIs of dynamic grants (stock: only into the next periods, nothing for a period of 0) |

Misfit rule (both schedulers, stock `NrSlUeMacSchedulerFixedMcs` and
`NrSlCoopScheduler`; trace `SpsMisfit` of `NrSlUeMacScheduler`): at the reselection
check of an SPS grant with data pending, if the LC buffer exceeds what the grant
allocates to the LC but fits the grant's TB (same sizing as the selection: 9 PSSCH
symbols, configured MCS, 5 B SCI-2A) the allocation is enlarged (no segmentation, no
reselection, "enlarged"); if it exceeds the TB the grant is cleared and the LCP selects a
new SPS grant for the data, with a new reselection counter ("misfit reselection"). The old
HARQ process is kept until the slots already published to the MAC (retransmissions of the
previous TB) are used.

Option-1 wrapper under SPS (`nack_only_harq`): the HARQ process is kept across periods,
so an implicit ACK decided after the last reserved retransmission of a period has nothing
to cancel and may hit the next period's TB (already handed to the HARQ buffer at publish
time): the MAC would erase that grant and the HARQ entity flush the TB. With an SPS
leader the ACK is delivered only while that period's reserved retransmissions remain
(`NrSlUeMac::IsSlRetxPending`); otherwise it is counted in `ack_no_retx_left`. The MAC's
ACK handling for SPS already erases only that period's published grant and keeps the
HARQ id and the scheduler's grant. Check: `check_sps.py` (0 retransmissions after an
implicit ACK, every retransmission after a NACK, share of TBs followed by the next period
on the same HARQ id).

Coop arms under SPS: `leaderRes` / `split` hand over each period's reserved
retransmission slots via SCI-2A (the handover is applied to every published period);
`ownRes` / `additive` helpers relay on dynamic (event-triggered) grants as before; the
packet deadline signalled to them now moves with the reservation period.

Which TB an overheard NACK is for (`ownRes` / `additive` helper; found with the SPS
pilots: under SPS the HARQ id is the same in every period, so a late NACK of the previous
period's retransmission matched the next TB the helper had just stored, by (source, HARQ
id), and the helper relayed a TB nobody had NACKed, `relay_without_initial_nack` 2 / 37):
- TS 38.213 sec. 16.3: a PSFCH carries no HARQ process id; it is tied to its PSSCH only
  through the resource. The PSFCH is sent in the first slot with PSFCH resources at
  least `sl-MinTimeGapPSFCH` resource-pool slots after the PSSCH. Within that slot,
  `sl-PSFCH-RB-Set` is split into M_subch,slot = M_PRB,set / (N_subch * N) PRBs per
  (PSSCH slot i of the N = `sl-PSFCH-Period` slots associated with the PSFCH slot,
  subchannel j): PRBs [(i + j N) M_subch,slot, (i + 1 + j N) M_subch,slot - 1];
  R_PRB,CS = N_type * M_subch,slot * N_CS (`sl-NumMuxCS-Pair`; N_type = 1 for
  `startSubCH`, N_subch of the PSSCH for `allocSubCH`); index (P_ID + M_ID) mod R_PRB,CS
  with P_ID the physical-layer source id of the SCI-2 and M_ID = 0 for groupcast
  Option 1 (every member NACKs on the same resource, one NACK-only cyclic shift).
- So (PSFCH slot, PRB, source id) identifies the PSSCH slot and subchannel. A third UE
  overhearing the PSFCH is not specified by 3GPP (it is an assumption of the helper
  scheme), but it can apply the same mapping. With N = 1 (this pool) every PSFCH slot
  serves exactly one PSSCH slot (i = 0), and two transmissions on one HARQ process are in
  different slots, so the PSFCH occasion alone identifies the transmission: matching by
  time is the standard mapping. With N = 2 / 4 the slot index i (the PRB) would be needed.
- 5G-LENA's PSFCH has no resources (lossless, `SlHarqInfo` without the PSSCH slot) and
  counts the gap in physical slots, so the helper matches by time: a NACK counts for the
  stored TB only if it arrives at or after that PSSCH's PSFCH occasion + 1 slot (the PHY's
  PSFCH decode latency), in absolute slots (`m_currentSfn` advances only in SL slots,
  a NACK can be delivered in a DL slot of the TDD pattern), with the PSSCH slot recovered
  from the delivery time (the PHY hands the PSSCH over 100 us after its end, possibly
  in the next slot). The existing upper bound stays (`additive`: occasion + 1 slot for a
  member whose PSFCH is pushed to the next occasion by its processing time; `ownRes`:
  the deadline). Verified: dynamic-grant `ownRes` / `additive` runs are byte-identical
  (3 full roundabout runs + 2 short), and no SPS run has `relay_without_initial_nack` > 0.
  A first version that used the slot of the PSSCH's delivery to the MAC rejected genuine
  NACKs (68 of 296 in an 8 s pilot); it never reached a result.

New run-CSV columns (appended): `leader_grant` (`sps` | `dynamic`, effective type of the
leader's grants), `sps_reselect_on_misfit`, `sl_dyn_exclusion`, `sl_project_announced`
(0/1), `leader_grants_sps`, `leader_grants_dyn`, `leader_resel_per_s` (SPS grants created
per s in the KPI window = (re)selections), `leader_misfit_resel`, `leader_misfit_enlarged`,
`leader_grant_subch_mean`, `leader_grant_bytes_mean`, `bg_sps_grants_per_s`,
`bg_dyn_grants_per_s`, `bg_misfit_resel_per_s`, `bg_misfit_enlarged_per_s`,
`ack_no_retx_left`, `leader_grant_slots_mean` (slots reserved per period, initial +
retransmissions, mean over the leader's grants).

SPS configuration of the re-run (`run_rerun_sps.sh`; program flags, defaults = old values):
- `--slProbResourceKeep=0.8` (sl-ProbResourceKeep, its maximum; stack default 0).
  **5G-LENA never applied it**: an SPS grant holds exactly `counter` periods of slots and
  is erased when its last period is published, at the moment the counter expires, so
  the keep branch of `TxResourceReselectionCheck` never finds the grant and a new
  resource is always selected (keep 0.8 gave the same reselections/s as 0 in a pilot).
  Now both schedulers decide at that moment (`KeepSpsResource`, trace `SpsResourceKept`):
  with probability sl-ProbResourceKeep the same slots are reserved for another
  `counter` periods (the counter drawn at the selection, 5G-LENA's own choice; whether
  TS 38.321 re-draws it there is not checked). With 0 no random number is drawn
  (stock streams). The HARQ process timer is renewed for `counter + 1` periods from that
  publication (with `counter` the kept cycle's last retransmission coincided with the
  timer expiry, the HARQ buffer was flushed first, and stock 5G-LENA leaves an empty
  grant behind after a wasted retransmission: assertion in `DoNrSlSlotIndication`).
- `--rriList=0,10,20,50,100,200,...,1000` (sl-ResourceReservePeriodList, 14 entries;
  RRC allows <= 16; 10 and 100 ms needed by periodic2 / periodic1). The stack default
  advertises 27 values, and step 5 projects the UE's own past transmissions with each.
- Run-CSV columns `sl_prob_resource_keep`, `rri_list` (`:`-separated, `default` for the
  stack's list), `leader_kept_per_s`, `bg_kept_per_s`; the re-run's `verify_dir` checks
  the first two.

Leader NACK attribution under SPS (`nack_only_harq`): the wrapper credited a received NACK
to the *latest* pending transmission of its HARQ id. A PSFCH carries no HARQ id (TS
38.213 sec. 16.3: the occasion and PRB identify the PSSCH); with SPS the next period's
initial transmission on the same HARQ id can be pending while the NACK of the previous
period's last retransmission arrives, so that TB was treated as delivered and the new
one got a false NACK. Pilots: 0 misattributed with dynamic grants and SPS periodic1,
540-715 NACKed transmissions taken as "no NACK" per SPS periodic2 run (column
`leader_nack_missed`). Now the NACK goes to the pending transmission whose decision
(its PSFCH occasion) comes first; with one pending transmission per HARQ id (dynamic)
the two rules coincide.

Pool checks for SPS:
- `sl-ResourceReservePeriodList` (`nr_sl_stack.cc`) contains 10 ms (periodic2) and 100 ms
  (periodic1); it advertises 27 values (0, 10-100, 150-1000 without 250), more than the 16
  entries RRC allows, and step 5 projects the UE's own transmissions with every one of
  them; the re-run uses a 14-entry list (`--rriList`, above).
- NOTE 3A (TS 38.321 5.22.1.1, reservation interval larger than the remaining PDB):
  periodic1 100 ms / PDB 100 ms, periodic2 10 ms / PDB 10 ms; the selection runs at or
  after the arrival, so the remaining PDB is <= the interval (equal only at the arrival
  slot itself).
- Selection window <= period (`GetCandidateResources` abort): periodic2 T2 = PDB = 20
  slots, (T2 - T1 + 1) x 0.5 ms = 9.5 ms < 10 ms (the check truncates to 9): no abort.

### Pilots (laptop debug build, seed 1, whole trace, IVD 30 m, highway N 45 / roundabout N 0)

`dynamic` = all new flags off (byte-identical to `coop-arms`); `SPS` = `--leaderSps=true
--spsReselectOnMisfit=true`; `+D1 D2` = also `--slDynExclusion=true --slProjectAnnounced=true`
(the `run_rerun_sps.sh` configuration). Delays over all members' delivered packets. One
seed: differences of a few points are noise (server 10-seed CIs: +-0.03-0.10).

| cell | version | PRR | tail `prr_pdb` | delay mean / p95 (ms) | tx/pkt | leader coll. rate | leader SPS (re)selections/s (misfit) | grant subch / bytes / slots | NACKed TBs with no retx left | bg SPS grants/s (misfit resel/s) |
|---|---|---|---|---|---|---|---|---|---|---|
| hwy p1 | dynamic | 0.907 | 0.826 | 4.4 / 10.5 | 1.62 | 0.25 | - | 1.20 / 247 / 2.93 | 0.00 | 29.7 (-) |
| | SPS | 0.909 | 0.821 | 4.8 / 10.5 | 1.74 | 0.23 | 1.55 (0.69) | 1.55 / 286 / 2.95 | 0.00 | 41.4 (17.6) |
| | SPS +D1 D2 | 0.956 | 0.912 | 4.7 / 11.0 | 1.68 | 0.07 | 1.55 (0.69) | 1.55 / 286 / 2.94 | 0.01 | 41.4 (17.6) |
| hwy p2 | dynamic | 0.787 | 0.264 | 4.5 / 10.5 | 1.91 | 0.46 | - | 4.20 / 917 / 2.24 | 0.29 | 29.7 (-) |
| | SPS | 0.690 | 0.172 | 6.1 / 10.5 | 1.31 | 0.39 | 3.49 (1.56) | 4.55 / 1055 / 1.44 | 0.66 | 41.4 (17.6) |
| | SPS +D1 D2 | 0.761 | 0.214 | 5.7 / 10.5 | 1.35 | 0.20 | 3.49 (1.56) | 4.55 / 1055 / 1.43 | 0.61 | 41.4 (17.6) |
| rbt p1 | dynamic | 0.857 | 0.591 | 5.7 / 9.4 | 1.83 | 0 | - | 1.20 / 247 / 2.80 | 0.07 | - |
| | SPS (= +D1 D2) | 0.819 | 0.536 | 6.0 / 9.4 | 1.86 | 0 | 1.39 (0.53) | 1.62 / 293 / 2.77 | 0.13 | - |
| rbt p2 | dynamic | 0.745 | 0.378 | 5.9 / 9.4 | 1.59 | 0 | - | 4.20 / 916 / 1.79 | 0.46 | - |
| | SPS (= +D1 D2) | 0.736 | 0.404 | 5.9 / 9.4 | 1.69 | 0 | 3.58 (1.61) | 4.54 / 1053 / 1.81 | 0.40 | - |

(misfit column: misfit reselections per s = `leader_misfit_resel` / KPI window; "enlarged"
was 0 everywhere: a larger SDU never fitted the TB of a grant sized for a smaller one.)

1. **The SPS leader's delay is a few ms** (mean 4.7-6.1 ms, p95 <= 11 ms) instead of
   ~94 ms: with the misfit reselection no SDU is split across periods.
2. **Reselections**: 1.4-1.6/s for periodic1 (38-45 % triggered by a 300 B SDU after a
   grant sized on a 190 B one; the rest are counter expiries), 3.5-3.6/s for periodic2
   (45 % misfit: 1200 B after an 800 B grant). Background periodic1 with the misfit
   rule: 41 SPS grants/s instead of 30, 18 of them misfit reselections.
3. **periodic2 on SPS loses retransmission slots under load** (highway): 1.44 reserved
   slots per grant against 2.24 with dynamic grants, so 61-66 % of the TBs end on a NACK
   with no retransmission left (29 % dynamic); PRR -0.10 (SPS) / -0.03 (+D1 D2), tail
   `prr_pdb` -0.09 / -0.05. Without background (roundabout N 0) SPS and dynamic reserve
   the same number of slots (1.8). Cause (confirmed with a dynamic leader + D1 + D2:
   10.9 candidates after step 6, 2.11 slots, threshold raised in 4 % of selections;
   SPS: 3.2 candidates, 1.44 slots, raised in 96 %): TS 38.214 step 6 checks an SPS
   candidate at y + j P'_rsvp_TX for every j < C_resel = 10 x counter (50-150 periods of
   10 ms, up to 1.5 s), against every sensed reservation projected into that horizon, so
   with 10 ms periods almost every slot of the window collides with something. Standard
   behaviour, not a 5G-LENA artifact; it is the price of announcing a 10 ms reservation
   in a loaded, 45 % TDD pool with TBs of 4-5 of the 5 subchannels.
4. **D1 + D2 cut the leader's resource-collision rate** on the highway (0.25 -> 0.07
   periodic1, 0.46/0.39 -> 0.20 periodic2) and raise PRR (periodic1 +0.05, tail
   `prr_pdb` +0.09). In a 10 s aperiodic1 pilot the leader's dynamic selections excluded
   sensed candidates in 91 % of cases (0 % stock) and collisions on reservations sensed
   above threshold ("not_excluded") fell 43 -> 2. Without background they change nothing
   (roundabout N 0 rows identical).
5. Option-1 wrapper under SPS (`check_sps.py`, every SPS pilot): 0 retransmissions after
   an implicit ACK, 0 without a NACK; 86-97 % of the TBs with an implicit ACK are followed
   by the next period on the same HARQ id (the grant is kept); the rest are reselections.
6. Coop arms with SPS + D1 + D2 (roundabout N 0 periodic1/2, highway N 45 periodic2 25 s):
   every `run_coop.py` check passes (after the NACK-matching fix above), `leader_grant` =
   `sps`, helper copies only after members' NACKs (`leaderRes`, `ownRes`, `split`),
   `additive` copies only after a NACK on the leader's initial transmission.

Re-run configuration (SPS + misfit + D1 + D2 + keep 0.8 + 14-entry list + the corrected
leader NACK attribution), same seed, against the table above:

| cell | PRR | tail `prr_pdb` | delay mean | tx/pkt | leader coll. | leader (re)selections/s (misfit) | kept/s | slots/grant | NACKs misattributed | bg SPS grants/s (kept/s) |
|---|---|---|---|---|---|---|---|---|---|---|
| hwy p1 | 0.965 | 0.910 | 4.5 | 1.66 | 0.04 | 0.31 (0.12) | 0.75 | 2.93 | 0 | 10.1 (21.4) |
| hwy p2 | 0.750 | 0.227 | 6.2 | 1.27 | 0.20 | 0.75 (0.36) | 1.61 | 1.49 | 0 (681 before) | 10.1 (21.4) |
| rbt p1 | 0.834 | 0.583 | 5.1 | 1.95 | 0 | 0.39 (0.12) | 0.66 | 2.75 | 0 | - |
| rbt p2 | 0.715 | 0.304 | 5.8 | 1.59 | 0 | 0.86 (0.37) | 1.45 | 1.77 | 0 (625 before) | - |

Keep 0.8 cuts the leader's reselections ~4-5x and the background's ~4x. Every check of
`check_sps.py` and `run_coop.py` passes (4 cells + 4 arms x roundabout N 0 / highway N 45
periodic2). The roundabout periodic2 tail drops 0.40 -> 0.30 with the attribution fix: the
misattributed NACKs had also given the next TBs extra (false-NACK) retransmissions
(tx/pkt 1.69 -> 1.59); one seed.

Decision for the full re-run (2026-10-12): **accepted** — periodic traffic on SB-SPS in
every experiment, with the periodic2 retransmission-slot loss as a stated property of the
configuration (finding 3: standard step-6 sensing over C_resel periods in a loaded 45 %
TDD pool with 4-5-subchannel TBs; no pool change). Code and checks ready
(`run_rerun_sps.sh check` passes locally).

## SB-SPS re-run: results (server, 2026-10-12/13, `run_rerun_sps.sh`, commit `1ef63a6`)

**Configuration** (every run): leader periodic1 / periodic2 on SB-SPS (RRI = period) with the
misfit reselection, sl-ProbResourceKeep 0.8 and the 14-entry sl-ResourceReservePeriodList;
D1 + D2 sensing for every UE; background periodic1 on SB-SPS with the same rules,
aperiodic1 (leader and background) and helper relays on dynamic Mode-2 grants;
t-Reordering 0. Same traces and seeds as the `*_rlc0` runs.

**Runs and checks**: campaign 270, variants 276, coop 500 (15 h 41 min at 24 jobs),
0 failed. Every run CSV carries the six flags and the effective `leader_grant` (`sps` for
periodic, `dynamic` for aperiodic): 0 mismatches. Every coop run passes every
`run_coop.py` design check. Leader NACKs missed: 37 of 3.47 M NACKed transmissions (path
loss above `MaxLossDb`, §6 of HANDOFF; before the attribution fix SPS periodic2 lost
540-715 per run). Data: `~/Documents/remote-results/{analysis_sps, campaign_sps,
variants_sps, coop_sps}` (per-run and per-member CSVs, analyses; per-packet files stay on
the server), driver log and provenance in `remote-results/rerun_sps/`.

**Read this as a new baseline, not as a correction of the `*_rlc0` numbers**: the
comparison below changes the leader's grant type, the sensing (D1 + D2) of every UE and
the background's SPS rules at once; which option moved what is only indicated by the
pilots (section above). In brackets: the dynamic-grant `*_rlc0` value, same cell.

### Campaign (3 seeds, 90 cells; `analysis_sps/summary.csv`)

Average PRR per member, highway / roundabout:

| IVD | N | periodic1 | periodic2 | aperiodic1 |
|---|---|---|---|---|
| 5 m | 0 | 0.996 (0.997) / 0.999 (0.999) | 0.982 (0.982) / 0.994 (0.992) | 0.951 (0.951) / 0.974 (0.974) |
| 15 m | 0 | 0.985 (0.989) / 0.947 (0.965) | 0.944 (0.950) / 0.873 (0.895) | 0.864 (0.864) / 0.824 (0.824) |
| 30 m | 0 | 0.956 (0.967) / 0.830 (0.856) | 0.857 (0.879) / 0.715 (0.751) | 0.741 (0.741) / 0.672 (0.672) |
| 5 m | 60 | 0.995 (0.967) / 0.997 (0.965) | 0.942 (0.914) / 0.966 (0.928) | 0.851 (0.810) / 0.855 (0.805) |
| 15 m | 60 | 0.985 (0.932) / 0.948 (0.912) | 0.878 (0.847) / 0.848 (0.842) | 0.716 (0.677) / 0.721 (0.695) |
| 30 m | 60 | 0.973 (0.893) / 0.820 (0.797) | 0.756 (0.740) / 0.684 (0.694) | 0.585 (0.549) / 0.577 (0.552) |

Loss increase from N = 0 to 60 (percentage points of 1 - PRR), highway / roundabout:

| IVD | periodic1 | periodic2 | aperiodic1 |
|---|---|---|---|
| 5 m | 0.1 (3.0) / 0.2 (3.4) | 4.1 (6.8) / 2.8 (6.4) | 10.0 (14.1) / 12.0 (16.9) |
| 15 m | 0.0 (5.7) / -0.1 (5.3) | 6.6 (10.4) / 2.5 (5.3) | 14.8 (18.8) / 10.4 (13.0) |
| 30 m | -1.8 (7.4) / 1.0 (5.9) | 10.1 (13.9) / 3.1 (5.7) | 15.6 (19.2) / 9.5 (12.0) |

Mean over scenarios and IVDs, per N (SB-SPS vs dynamic): leader resource-collision rate,
transmissions per packet, collision loss per member-packet:

| traffic | N | collision rate | tx/pkt | collision loss |
|---|---|---|---|---|
| periodic1 | 30 | 0.08 (0.19) | 1.51 (1.46) | 0.013 (0.044) |
| periodic1 | 60 | 0.16 (0.36) | 1.44 (1.46) | 0.020 (0.077) |
| periodic2 | 30 | 0.16 (0.35) | 1.28 (1.48) | 0.039 (0.093) |
| periodic2 | 60 | 0.34 (0.58) | 1.25 (1.54) | 0.085 (0.153) |
| aperiodic1 | 30 | 0.21 (0.29) | 2.65 (2.68) | 0.141 (0.189) |
| aperiodic1 | 60 | 0.39 (0.49) | 2.69 (2.75) | 0.244 (0.294) |

1. **periodic1 no longer loses PRR to background traffic.** The 0 -> 60 loss is <= 1 pp
   (was 3-7 pp): the leader's collision rate at N = 60 halves (0.16 vs 0.36) and the
   collision loss falls to a quarter. Highway N 60: 0.973-0.995 (was 0.893-0.967); 8 of
   the 15 highway cells are better with disjoint 95 % CIs, none worse.
2. **periodic2: no net change** (mean dPRR -0.000 highway, -0.004 roundabout; 2 cells
   better, 1 worse with disjoint CIs). Collisions fall (0.34 vs 0.58 at N = 60), but SPS
   reserves fewer retransmission slots under load (1.41-1.46 per grant at N >= 30, 1.85 at
   N = 0; tx/pkt 1.25 vs 1.54): the accepted SB-SPS property (HANDOFF §8).
3. **aperiodic1 (leader still on dynamic grants): +0.02 to +0.05 at N >= 30** (2 roundabout
   cells with disjoint CIs): D1 + D2 alone, collision rate 0.39 vs 0.49 at N = 60.
4. **Without background SB-SPS is equal or slightly worse at long IVD**: roundabout IVD 30
   periodic1 0.830 vs 0.856 and periodic2 0.715 vs 0.751 (disjoint CIs), highway IVD 30
   -0.011 / -0.022 (inside the CIs), IVD 5 identical. Likely cause (not isolated): an SPS
   grant keeps the size of the largest SDU it was (re)selected for, so smaller SDUs are
   sent over more subchannels with the same 23 dBm (lower power per PRB): mean grant
   1.62 subchannels for periodic1 (dynamic per packet: 1.20) and 4.56 for periodic2
   (4.20).
5. Unchanged: urban NLOS limits the long platoon (roundabout IVD 30 periodic1 0.82-0.83
   whatever the load), HARQ cannot fix the blockage, aperiodic1 < periodic2 < periodic1.
   Changed: the "highway degrades faster with N" finding now holds for periodic2 and
   aperiodic1 only; for periodic1 neither scenario degrades.

Grant statistics (leader, mean over runs): periodic1 0.29-0.42 (re)selections/s, 0.7-0.8
kept resources/s, 1.62 subchannels / 293 B per grant, 2.85-2.91 slots per grant;
periodic2 0.73-0.86 /s, 1.5 kept/s, 4.56 subchannels / 1058 B, 1.42-1.85 slots.
Background periodic1: 3.3-12.7 SPS (re)selections/s and 7.5-28.4 kept/s for N = 15-60.

### Variants (IVD 30 m; `variants_sps/analysis/variants_summary.csv`)

Average PRR per member at N = 60, highway / roundabout (MCS 5 / 9 not re-run):

| traffic | MCS 14 (base) | dedicated | blind |
|---|---|---|---|
| periodic1 | 0.97 (0.88) / 0.83 (0.80) | 0.97 (0.94) / 0.83 (0.84) | 0.98 (0.90) / 0.82 (0.81) |
| periodic2 | 0.77 (0.74) / 0.68 (0.69) | 0.90 (0.86) / 0.76 (0.75) | 0.86 (0.80) / 0.74 (0.72) |
| aperiodic1 | 0.59 (0.55) / 0.58 (0.55) | 0.69 (0.66) / 0.63 (0.61) | 0.58 (0.54) / 0.57 (0.54) |

Tail member (member 8) PRR within the PDB, 10 seeds, +- 95 % CI (dynamic `*_rlc0`):

| cell | N = 0 | N = 45 | N = 60 |
|---|---|---|---|
| hwy periodic1, MCS 14 | 0.895 +- 0.064 (0.925) | **0.929 +- 0.029** (0.835) | **0.919 +- 0.060** (0.819) |
| hwy periodic1, dedicated | 0.900 +- 0.059 (0.930) | 0.904 +- 0.051 (0.879) | 0.938 +- 0.030 (0.897) |
| hwy periodic2, MCS 14 | 0.627 +- 0.128 (0.683) | 0.487 +- 0.102 (0.519) | 0.490 +- 0.110 (0.488) |
| hwy periodic2, dedicated | 0.744 +- 0.139 (0.751) | 0.705 +- 0.119 (0.691) | 0.719 +- 0.092 (0.687) |
| rbt periodic1, MCS 14 | 0.573 +- 0.009 (0.608) | 0.602 +- 0.066 (0.607) | 0.585 +- 0.059 (0.585) |
| rbt periodic1, dedicated | 0.554 +- 0.011 (0.621) | 0.594 +- 0.050 (0.622) | 0.589 +- 0.062 (0.620) |
| rbt periodic2, MCS 14 | 0.393 +- 0.042 (0.418) | 0.408 +- 0.074 (0.449) | 0.365 +- 0.055 (0.416) |
| rbt periodic2, dedicated | 0.474 +- 0.045 (0.457) | 0.510 +- 0.067 (0.478) | 0.509 +- 0.066 (0.470) |

1. **Highway periodic1 tail under load: +0.09 to +0.10** (MCS 14, N 45 / 60; also the
   mean PRR 0.96 vs 0.88-0.90 with disjoint CIs, and for blind). On the highway the
   dedicated carrier no longer adds anything for periodic1 (0.97 both at N = 60).
2. **periodic2 tail: unchanged within the CIs** on both scenarios (fewer collisions vs fewer
   retransmissions); the dedicated carrier still helps it most (highway N 60: 0.719 vs
   0.490).
3. **Roundabout tail: unchanged, blockage-limited** (0.57-0.60 periodic1, 0.37-0.41
   periodic2); at N = 0 periodic1 it is lower (0.573 vs 0.608; dedicated 0.554 vs 0.621,
   disjoint CIs): finding 4 of the campaign.
4. **Blind retransmission is no longer "3 transmissions" for periodic2**: an SPS grant
   reserves 3 slots only without load (2.86 tx/pkt at N = 0, 2.11-2.16 at N 45 / 60).

### Cooperative HARQ (10 seeds, helper 4, IVD 30; `coop_sps/analysis/`)

Tail (member 8) PRR within the PDB, mean over seeds; paired = per-seed difference to `none`
with its 95 % CI, wins = seeds better than `none`; CI = also disjoint 95 % CIs (same
method as the dynamic-grant table above, which it reproduces exactly).

| cell | none | leaderRes | ownRes | split | additive | tx/pkt none -> additive |
|---|---|---|---|---|---|---|
| rbt N 0 periodic1 | 0.556 | **0.630** (+0.074, 10/10, CI) | **0.612** (+0.056, 10/10, CI) | **0.600** (+0.044, 9/10) | **0.606** (+0.050, 10/10, CI) | 1.95 -> 2.37 |
| rbt N 0 periodic2 | 0.407 | 0.430 (n.s.) | 0.321 (worse, -0.086) | 0.424 (n.s.) | 0.412 (n.s.) | 1.59 -> 1.75 |
| rbt N 45 periodic1 | 0.602 | **0.646** (+0.044, 10/10) | **0.636** (+0.034, 9/10) | **0.630** (+0.028, 9/10) | **0.641** (+0.039, 9/10) | 1.87 -> 2.30 |
| rbt N 45 periodic2 | 0.399 | 0.384 (n.s.) | 0.381 (n.s.) | 0.399 (n.s.) | 0.425 (n.s.) | 1.31 -> 1.48 |
| rbt N 60 periodic1 | 0.571 | **0.622** (+0.050, 10/10) | **0.618** (+0.047, 10/10) | **0.606** (+0.035, 9/10) | **0.597** (+0.026, 8/10) | 1.91 -> 2.36 |
| rbt N 60 periodic2 | 0.360 | 0.388 (n.s.) | 0.344 (n.s.) | 0.356 (n.s.) | 0.367 (n.s.) | 1.34 -> 1.36 |
| hwy N 45 periodic1 | 0.888 | 0.888 (n.s.) | 0.903 (n.s.) | 0.887 (n.s.) | 0.908 (n.s.) | 1.68 -> 2.19 |
| hwy N 45 periodic2 | 0.485 | 0.472 (n.s.) | 0.425 (worse, -0.060) | 0.490 (n.s.) | **0.514** (+0.029, 7/10) | 1.38 -> 1.52 |
| hwy N 60 periodic1 | 0.888 | 0.892 (n.s.) | 0.896 (n.s.) | 0.901 (n.s.) | **0.910** (+0.022, 7/10) | 1.72 -> 2.24 |
| hwy N 60 periodic2 | 0.505 | 0.483 (n.s.) | 0.435 (worse, -0.070) | 0.504 (n.s.) | 0.516 (n.s.) | 1.36 -> 1.48 |

1. **The blockage verdict stands, somewhat smaller**: on the roundabout with periodic1 every
   helper arm helps (N 0: +0.04 to +0.07, disjoint CIs for `leaderRes`, `ownRes`,
   `additive`; N 45 / 60: +0.03 to +0.05 paired, 8-10/10 seeds). The `none` tail is lower
   than with dynamic grants (0.556 vs 0.597 at N 0, campaign finding 4).
2. **Handing retransmissions over no longer loses under collisions with periodic1, and
   `leaderRes` no longer loses with periodic2** (highway -0.014 / -0.022, n.s.; was -0.09):
   collisions are rarer. `ownRes` still loses with periodic2 (-0.06 to -0.09).
3. **`additive`: gains only where it gained before, and smaller** (highway +0.02 / +0.03
   paired, 7/10, two of four cells; the other two n.s.), for 10-30 % more transmissions.
4. **`split` is inert with periodic2 under SB-SPS**: the leader's periodic2 grants rarely
   reserve a third slot to hand over (0-25 helper copies per run).
5. Latency: `ownRes` still doubles the periodic1 mean latency (10.6-11.2 vs 4.5-5.5 ms);
   the other arms stay within 1 ms of `none`.

**Footprint (pre-registered rule, roundabout N 45 / 60): supported, still not a turn
effect** — turn phase: `leader_only` + `helper_only` = 46.8 % of the collided failed
leader attempts (outside the turn 43.9 %; dynamic grants: 22.7 % / 18.9 %), median
divergence of the leader's selections 0.85 (dynamic 0.40). What changed is the mix (all
phases): with sensing working, an interferer sensed by both is now behind only 17 % of
these attempts (was 63 %), and one sensed by the helper only behind 40 % (was 12 %) — the residual
collisions are the ones the leader could not see, the helper often could. Divergence is
computed per selection and the SPS leader selects ~70x less often (4.6 k vs 313 k
selections in the turn phase), so 0.85 vs 0.40 is not a like-for-like comparison.

The collision `why` breakdown (`coop_why_summary.csv`) classifies a collided transmission
by the selection that picked its resource; an SPS period that reuses a reservation has
none (88 % `no_selection` for the leader), so it is not comparable with the dynamic-grant
runs.

## Hidden-node test (branch `hidden-node`, from 2026-10-13)

Definitions, checks and decision rules written **before any result**; results follow in
the subsections below once they exist. Configuration of every run: the SB-SPS reference
(`run_rerun_sps.sh` NEW_FLAGS), periodic1, `--collCond`, `--hiddenNode`, one sensing
threshold `--slThresRsrp` of {-128, -110} dBm (every UE).

**Question.** When does a resource the leader selected become obsolete for the helper that
transmits on it (`leaderRes`)? How does the PRR of helper copies degrade with the
leader–helper sensing gap? How much could inter-UE coordination recover?

### Definitions

- **Use**: a transmission on a leader-selected resource — the helper's copy on a slot
  handed over by the leader (`leaderRes`, `leaderResIuc`), and, as the control, every
  transmission of the leader (all arms). `ownRes` helper copies are on the helper's own
  resources and are not uses.
- **Candidate** (the "unit" of L and H): a candidate single-slot resource R_x,y of 5G-LENA's
  list (one slot x L_subCH contiguous subchannels); |cand| = candidates entering step 6
  (after step 5 / 5a).
- **Step-6 evaluation**: the MAC's own TS 38.214 sec. 8.1.4 step 6 (projections with D1 + D2,
  candidate periods j < C_resel, RSRP above the threshold, frequency overlap), run on one
  UE's sensing data in a window [n - T0, n - Tproc0). Implemented in the NR patch
  (`NrSlUeMac::SetSensingPeer`, trace `SensingGap`, `EvaluateSensedExclusion`), the same
  projection routine as the selection; at every selection the recomputed remaining set and
  final threshold are compared with the selection's own (check `hn_chk_gap_mismatch`).
- **Platoon-internal SCIs are ignored** in L, H and the use evaluations (SCIs whose
  transmitter is the leader or the helper; `SensingData::txRnti`): otherwise the helper's
  data would "exclude" the leader's own reservation of the very slot it hands over.
- **At each leader selection** (SPS and dynamic; kept periods are not selections), with the
  window, candidates and **final (relaxed) threshold** of that selection (initial threshold
  also logged): L = candidates the leader's data excludes, H = candidates the helper's data
  excludes, G_H = |H \ L| / |cand| (the gap the helper could close), G_L = |L \ H| / |cand|,
  and the existing `_div` divergence of the same selection.
- **Veto** of a use: its base candidate (the same position in the grant's first period) was
  in H \ L at the selection's final threshold.
- **At each use**: `excl_helper_now` / `excl_leader_now` = step 6 for the used resource
  alone (C_resel = 1: this use), with that UE's sensing data in [use - T0, use - Tproc0),
  at the producing selection's final threshold (and its initial one, `_init` columns).
  Category (final threshold): **hidden** (helper yes, leader no), **stale** (both),
  **unannounced** (neither), **leader_only** (helper no, leader yes).
- **Reservation age**: use time - time of the leader selection that produced the resource;
  sl-ProbResourceKeep cycles belong to the same selection; `keep_count` = keeps before the
  use's period was published, `period` = periods published before it.
- **Outcome**: `overlap` = the use overlapped another PSSCH (existing method); `coll_rear` /
  `coll_tail` = overlap and >= 1 of members 5-8 (excluding the transmitter) / member 8 that
  still needed the TB failed it; needing = had not decoded the TB from an earlier
  transmission; **success** = members that needed it and decoded it / members that needed
  it (member level, pooled over uses); interferer ids and how many the leader / helper had
  sensed (`Sensed()`, as `_coll.csv`).
- Also per use: leader–helper distance, link condition leader -> helper at the leader's
  latest transmission (recorded value, no new channel-condition query), background vehicles
  in the area, roundabout turn flag.

### Oracle IUC arm `leaderResIuc` (an upper bound, not IUC)

Like `leaderRes`, but at every leader selection the candidates the helper's sensing data
excludes (H, same window, platoon-internal SCIs ignored) are excluded too, with zero delay,
no loss and no signalling cost; the 20 % rule and the +3 dB relaxation apply to the union.
Logged: `relax_extra_iuc` (selections where the union needed more relaxation steps than the
leader's own data), `iuc_sel_in_h_relaxed` (selected resources in H at the threshold the
leader alone would have used). Only the RSRP condition of the non-preferred set is
modelled (no half-duplex condition).

Rel-17 inter-UE coordination, Scheme 1 (checked in ETSI TS 138 214 V17.5.0 and
TS 138 321 V17.5.0):
- TS 38.214 sec. 8.1.4A (UE-A determines a non-preferred set): resources "indicated by a
  received [SCI format 1-A]" whose RSRP is higher than Th(prio_RX)
  (`sl-ThresholdRSRP-Condition1-B-1-Option1List`), or slots of the UE's own half-duplex
  transmissions when it is a destination of the TB.
- TS 38.214 sec. 8.1.4C (UE-B uses a received non-preferred set): "the UE excludes in Step
  6b) of clause 8.1.4 resource(s) overlapping with the non-preferred resource set"; if fewer
  than X·M_total candidates would remain, "it is up to UE implementation" whether the set is
  taken into account. The union relaxation above is such an implementation choice.
- TS 38.321 sec. 5.22.1.10.2: on an SL-IUC Information MAC CE with a non-preferred set, the
  MAC shall "indicate the received non-preferred resource set to physical layer"; sec.
  5.22.1.10.1: the reporting is per PC5-RRC (unicast) connection; MAC CE in sec. 6.1.3.53.
  A real helper would need a PC5-RRC link to the leader (or SCI format 2-C) and its report
  would be older than the leader's selection by the processing and signalling delay.

### Outputs and checks

Per run (only with `--hiddenNode`): `<tag>_use.csv` (one row per use in the KPI window:
slot, subchannels, selection id, age, keep count, the four exclusions, category, veto,
G_H / G_L of the selection, thresholds, outcome, distance, link condition, N in area, turn,
interferers) and `<tag>_sel.csv` (one row per leader selection: grant type, window, |cand|,
|L|, |H|, |H \ L|, |L \ H| at the final and initial threshold, G_H, G_L, divergence,
thresholds, relaxation steps with and without the union, base-slot flags). Run CSV
(appended, 0 without the flag): `sl_thres_rsrp`, `hidden_node`, `hn_*` aggregates
(category shares and veto rates of leader / helper uses, mean / median G_H, mean G_L,
helper-copy success hidden / other / vetoed / not vetoed, `relax_extra_iuc`,
`iuc_sel_in_h_relaxed`). Checks, all must be 0 (`run_hidden_node.py`, `verify_dir`):
- `hn_chk_sel_in_l`: a selected resource excluded by the leader's real step 6 at its final
  threshold, or not in the final candidate list;
- `hn_chk_age0_unexplained`: a use in the first period of its selection excluded by the
  leader's own data through an SCI that was already in the selection's window (at the
  initial threshold: only without relaxation) — i.e. `excl_leader_now` at age 0 must be 0,
  or explained by SCIs received after the selection, or by relaxation;
- `hn_chk_gap_mismatch`: recomputed step 6 differs from the selection's (members or final
  threshold);
- `hn_chk_link`: a grant without its selection, a selected slot that was not a candidate,
  a first slot outside [n + T1, n + T2], a published period with another slot count;
- `hn_chk_unlinked_uses`: a use not linked to a selection;
- `hn_chk_iuc_sel_in_h` (`leaderResIuc`): a selected resource in H at the final threshold;
- every `run_coop.py` design check (`leaderResIuc` as `leaderRes`).

**Pilots (laptop, seed 1, 2026-10-13).**
- Identity, new options off vs the `coop-arms` code: 16 runs (stock `off` periodic1/2; SPS
  reference flags x `none` / `leaderRes` / `ownRes` / `split` / `additive` on roundabout N 0
  and highway N 45 with `--collCond`; `leaderRes` periodic2; 2 full runs): 121 output files,
  0 differences (run CSV: original columns).
- Passivity, the same 14 helper runs with `--hiddenNode`: every pre-existing file identical
  (the instrumentation does not perturb the run).
- Function (25 s, highway N 45 helper 4 / roundabout N 45 helper 7, `none` / `leaderRes` /
  `leaderResIuc`, -128 / -110 dBm): every check 0 in all 12 runs; `run_hidden_node.sh
  check` passes (4 smoke runs, `verify_dir`, analysis). One seed and 22 s: no conclusion.
- `rbt_ivd60_n45_s1` generates (88.5 s, background mean 44.96; 93 background–platoon
  overlap samples vs 38 at IVD 30).

### Stage A (pilot and go / no-go) — rule fixed before the results

Server, seeds 1-3, cells roundabout IVD 30 N 45, highway IVD 30 N 45, highway IVD 60 N 45;
helpers 4 and 7; thresholds -128 / -110 dBm; arms `none` (once per cell x threshold, helper
4 as observer), `leaderRes`, `ownRes`, `leaderResIuc` (126 runs). Report per cell: category
shares, veto rate, G_H distribution, helper-copy success hidden vs other and vetoed vs not,
tail `prr_pdb` per arm (paired).

**Go** if, for some (cell, helper, threshold), the `leaderRes` helper copies pooled over
seeds 1-3 have `hidden` >= 10 % of the copies **and** a member-level success of the vetoed
copies at least 10 points below that of the non-vetoed ones, with >= 30 vetoed and >= 30
non-vetoed copies. Otherwise: "hidden-node effect not reproduced in this setting", with the
numbers, and stop.

### Stage B (only after a go) — decision rules fixed before the results

Server, 10 seeds, `--collCond`; sweep 1: N in {0, 15, 30, 45, 60} x {highway, roundabout} x
IVD 30 x helper 4 x arms {none, leaderRes, ownRes, leaderResIuc}; sweep 2: N 45 x {highway,
roundabout} x helpers {2, 4, 7} x IVD {30, 60} x arms {leaderRes, ownRes, leaderResIuc}
(+ `none` per scenario x IVD x threshold); the thresholds that produced the go; cells stage
A showed to be flat dropped (stated with the run counts before the launch).

Analysis (`analyze_hidden_node.py full`, fixed bins): obsolescence curves (hidden / stale
shares and the use's resource-collision rate against N, leader–helper distance 0-50 /
50-100 / 100-150 / 150-250 / 250-400 / > 400 m, threshold, scenario and reservation age
0-0.5 / 0.5-1 / 1-2 / 2-5 / > 5 s; the leader's own uses as the reference); helper-copy
success and tail `prr_pdb` (per packet, by the selection behind the leader's transmission)
by G_H bin 0 / 0-5 / 5-10 / 10-20 / > 20 % and by veto; run-level OLS of the paired
(`leaderRes` - `ownRes`) and (`leaderResIuc` - `leaderRes`) tail `prr_pdb` on the
`leaderRes` run's mean G_H (slope, 95 % CI). CIs: 95 % cluster bootstrap over runs (2000
draws, seed 1) for pooled use / packet ratios; paired t-intervals over seeds for tail
`prr_pdb` differences.

- **Hidden-node effect reproduced** if, in some cell (scenario x IVD x N x helper x
  threshold), `hidden` >= 10 % of the `leaderRes` helper copies and the vetoed copies lose
  >= 10 points of success, with the 95 % CI of that loss above 0.
- **IUC worth pursuing** if `leaderResIuc` - `leaderRes` >= +0.03 tail `prr_pdb` with a
  paired 95 % CI excluding 0 in at least one realistic cell (background N > 0); reported
  with its cost (extra relaxation, latency, transmissions per packet, CBR).
- Otherwise: "IUC upper bound below threshold in this setting", with the numbers.
- Many cells are tested: a single cell passing a rule is reported as such, with the number
  of cells tested, not as a general effect.

```bash
python3 run_hidden_node.py --stage stageA --dry-run          # counts and time estimate
tmux new -d -s hn -c "$PWD" 'JOBS=24 ./run_hidden_node.sh check stageA analyzeA'   # server
./run_hidden_node.sh status
```

## Per-TB helper selection (Kuo et al. 2021) — design, checks, pilot (2026-10-13)

**Why.** Laptop geometry analysis (`analyze_helper_geom.py`, roundabout IVD 30 N 0 periodic1,
10 seeds, every arm): the helper helps only while a **building** blocks leader -> tail (46 % of
the tail's TBs; there `none` delivers 0.06, `leaderRes` 0.23; vehicle-only blockage: 0.97
with or without helper). Inside the platoon "LOS to both" never occurs (platoon cars block
member 4 towards both ends); the right criterion is "no building on either hop". Fixed member
4 has a building-free path on both hops in only 2 % of those TBs, while **some** member does
in 52 % (members 2, 6, 1, 7 depending on the turn phase). Kuo et al. select the retransmission
UE per TB (assigned with the reservation in the SCI), from CAM positions (100 ms) and a
distance-only TR 37.885 LOS model, with blind retransmissions.

**Arms** (`run_coop.py` `DYN_ARMS`; one helper per TB, the leader keeps its initial
transmission and hands the reserved retransmission slots over, SB-SPS re-run flags):

| arm | selection | relay trigger | flags |
|---|---|---|---|
| `none` | — | — | `--helperMode=none` |
| `s4Nack` | static member 4 (Kuo's "middle Tx", baseline) | NACK (Option 1) | `--helperMode=leaderRes --helper=4` |
| `s4Blind` | static member 4 | every handed slot | + `--helperBlind=true` |
| `kuoNack` / `kuoBlind` | max-mean GPRR (Kuo eq. 12–13, one relay), distance-only P(LOS) | NACK / blind | + `--helperSelect=kuo` |
| `mapNack` / `mapBlind` | the same with the building map + CAM positions | NACK / blind | + `--helperSelect=map` |

**Selection** (`helper_select.{h,cc}`): GPRR(i) = mean_r [1 - Pf(L->r)(Pf(L->i) + (1 -
Pf(L->i)) Pf(i->r))], Pf = 1 - P(success); P(success) as Kuo eq. 7 with the TR 37.885 urban
models (LOS / NLOSv = LOS + blockage max{0, N(mu, 4.5 dB)}, mu = 9 + max(0, 15 log10 d - 41) /
NLOS building), shadowing 3 / 4 dB, success if the path loss stays below the TB's link budget
PL_max = 23 dBm - 10 log10(PRBs) - (noise of a PRB + 9 dB NF) - 8 dB (MCS 14; `--kuoSinrReqDb`,
`--kuoPlMaxDb`; 114.4 dB for 1 subchannel, 111.4 dB for 2). `kuo`: P_LOS(d) = min(1, 1.05
exp(-0.0114 d)) mixes LOS / NLOSv, no buildings. `map`: a building on the segment (CAM
positions) -> NLOS, another platoon car within 0.9 m of the segment -> NLOSv, else the LOS /
NLOSv mixture (the simulator's own condition model). Inputs: positions sampled every
`--camPeriodMs` (100), usable `--helperDecisionMs` (0) after sampling, the map.

**Implementation.** `NrSlCoopScheduler::SetHelperSelector`: called when the leader publishes a
period's grant (after its resource selection), returns the helper RNTI written into that
period's SCI-2A; unset = the static `HelperId`. No NR change (any member named in SCI-2A
relays). Blind = `NrSlUeMac::HelperNackTriggered=false`. The program records copies from every
member (`NackOnlyHarqController::AddObservedDevice`; per-transmission node / RNTI for PHY
matching, NACK attribution, geometry). The sensing-observer role (footprint, divergence)
stays on `--helper`.

**No interference with resource allocation (SB-SPS and dynamic).** The selector only writes
the helper id of a period already selected; no slot, subchannel, counter, keep / misfit or
sensing input changes; no random draw, no channel-model query. Verified: **shadow mode**
(`--helperSelect=map --helperSelectShadow=true`: compute and log, signal member 4) is
byte-identical to `leaderRes` in every output file for periodic1 / periodic2 (SB-SPS) and
aperiodic1 (dynamic), roundabout N 0 and N 45 (run CSV: only the selection columns differ);
`--helperSelect=static` through the selector is byte-identical too; all new options off: 9
short pilots byte-identical to the previous binary.

**Delay log** (`<tag>_hsel.csv`, one row per TB: `t_decide`, `t_ndi`, `decide_to_ndi_ms`,
`cam_age_ms`, `pl_max_db`, chosen / applied member, GPRR score of every candidate; run CSV:
`helper_changes_per_s`, `helper_share_m1..m8`, `hsel_cam_age_ms_mean/p95`,
`relay_after_initial_ms_mean/p95`, `relay_by_unchosen`, `helper_select`, `helper_blind`,
`cam_period_ms`, `helper_decision_ms`, `helper_shadow`). The decision comes T1 = 1 ms before
the TB's new transmission; positions are 28-31 ms old for periodic1 (SPS phase vs CAM phase),
up to ~100 ms for periodic2 / aperiodic1; the first helper copy follows the leader's initial
transmission by ~4 ms.

**Pilot** (laptop, roundabout IVD 30 N 0 periodic1, seed 1, whole trace; every `run_coop.py`
check passes, 0 relays by an unchosen member):

| arm | tail `prr_pdb` | GPRR | tx/pkt | helper copies | chosen members |
|---|---|---|---|---|---|
| `none` | 0.530 | 0.820 | 1.97 | 0 | — |
| `s4Nack` | 0.611 | 0.809 | 1.48 | 244 | 4 |
| `s4Blind` | 0.607 | 0.806 | 2.38 | 700 | 4 |
| `kuoNack` | 0.633 | 0.873 | 1.90 | 460 | 2 (93 %), 3 (6 %) |
| `kuoBlind` | 0.593 | 0.855 | 2.85 | 939 | same |
| `mapNack` | **0.680** | **0.884** | 1.74 | 375 | 2 (67 %), 3, 4, 5, 6; 0.26 changes/s |
| `mapBlind` | 0.695 | 0.880 | 2.74 | 886 | same |

When a building blocks leader -> tail and a member with a building-free path on both hops
exists, `map` picks such a member in 100 % of the TBs (positions ~29 ms old), `kuo` in 38 %,
static member 4 in 4 %. One seed: no verdict.

**Pre-registered rules (fixed before the server run, `analyze_dynhelper.py`):**
1. Dynamic selection pays if `mapNack` beats `s4Nack` on the tail `prr_pdb`, paired (95 % CI of
   the per-seed difference > 0), roundabout N 0 periodic1; `kuoNack` reported the same way.
2. Feedback pays over blind if, for the same selection, NACK >= blind on the tail `prr_pdb`
   (paired n.s. or better) at lower tx/pkt; reported for N 0 / 45 / 60.
3. Collisions / hidden nodes: the paired gains vs `none` at N 45 / 60 against N 0.

**Run** (server; resumable; ~2-3 h at 24 jobs): `./run_dynhelper.sh build check`, then
`tmux new -d -s dynhelper -c "$PWD" 'JOBS=24 ./run_dynhelper.sh'`; 7 arms x roundabout IVD 30
N {0, 45, 60} x periodic1 / periodic2 x seeds 1-10 = 420 runs in `results/coop_dyn_sps/`
(`--collCond --writeAtt`); `check` = 4 smoke runs incl. the shadow identity test. Outputs:
`results/coop_dyn_sps/analysis/` (run_coop tables, figures) and `analysis_dyn/`
(`dyn_paired.csv`, `dyn_geometry.csv`, `dyn_verdict.txt`); logs `results/rerun_dyn/`. The
earlier campaigns need no re-run: the `none` and `s4Nack` runs must reproduce `coop_sps` for
the same cells and seeds (the logs draw no random numbers).

**Validation logs** used here (opt-in, outputs otherwise byte-identical): `--writeAtt`
(`_att.csv`: every platoon transmission x member: status, SINR, channel condition,
deterministic geometry leader->member, helper->member, leader->helper, member->tail),
`--writeAllTx` (`_alltx.csv`: every PSSCH of every UE with position), `--writeGrants`
(`_grants.csv`: every grant created / kept / misfit). Background check
(`validate_alloc.py`, highway IVD 30 periodic1 N 60, seed 1, re-run configuration vs every
option off): background periodic1 always SB-SPS 100 ms, counter mean 10.0, 0.30 / 0.70
reselections / kept per vehicle-s (stock: 0.97 / 0), grants 225 or 335 B only (stock also 1328
B backlog and 118 B segments), 96.5 % of new transmissions exactly one period later on the same
subchannels; aperiodic1 dynamic, no retransmission; aperiodic-periodic collisions within
300 m 128 (stock 2954), periodic-leader within 600 m 0 (stock 112); CBR recomputed = run CSV
(0.433 / 0.425) once the parked seats are excluded. The remaining collisions are mostly two
aperiodic vehicles choosing the same fresh resource (nothing to sense) and periodic pairs
beyond 600 m.

## One-TB leader traffic classes (2026-10-14, for the PLAN.md campaign)

**Why.** In the pool of the new campaign (μ 1, dedicated carrier, 5 × 10 PRB, MCS 14,
PSFCH period 1) the largest TB is **1333 B**. This is 5G-LENA's `NrAmc::CalculateTbSize`
with 9 PSSCH symbols, as `NrSlCoopScheduler` sizes a grant; 1, 2, 3 and 4 subchannels give
264 / 531 / 798 / 1066 B.

A leader packet adds 37 B on its way to the TB: UDP 8, IPv4 20, PDCP 2, the RLC UM header
estimate 2, and SCI-2A 5. The app's `SeqTsSizeHeader` is inside the payload. So the largest
single-TB payload is 1296 B. This overhead model reproduces every size → PRB mapping
measured in `results/campaign`.

aperiodic1 (200–2000 B) breaks this. Packets of 200–1200 B take 1 TB, but 1400–2000 B take
**3 TBs** (50 + 10 + 10 or 50 + 20/30 + 10 PRB), which gives 1.8 TBs per packet in every
aperiodic1 run, on both carriers and at N 0. Every TB must arrive, and each one has its own
HARQ process, helper and reservation. The comparison of retransmission strategies would then
measure segmentation.

| `--traffic` | base | sizes (B) | arrivals | PDB |
|---|---|---|---|---|
| `periodic1tb` | periodic1 | 190 / 300 (unchanged, already 1 TB) | 100 ms | 100 ms |
| `periodic2tb` | periodic2 | 800 (p 0.8) / 1200 (unchanged, already 1 TB) | 10 ms | 10 ms |
| `aperiodic1tb` | aperiodic1 | **{200, 400, …, 1200} uniform** (aperiodic1: up to 2000) | 50 ms + Exp(50 ms), unchanged | 50 ms |

Only the leader's flow changes; background aperiodic1 keeps sizes up to 2000 B. With the
`*tb` classes unused every output file is byte-identical to before (pilots: highway
aperiodic1 on the dedicated carrier, roundabout periodic1 `leaderRes` + map selection;
20 s each). The run CSV gains the columns below.

**Implementation.**
- `CustomTrafficApplication` attribute `Aperiodic1MaxSize` (default 2000; must be 200 + k·200).
  It changes only the upper bound of the same uniform draw, so the size stream and the
  arrival times stay identical.
- The program maps a `*tb` class to its base class (`BaseTraffic`) and sets the attribute on
  the leader's application only.

**Assertions** (the run aborts):
- At start-up, the largest packet of a `*tb` class + 37 B must fit the pool's largest TB,
  computed with the leader scheduler's own AMC. Example: `periodic2tb` at μ 2 (2 × 10 PRB,
  531 B) aborts.
- At the end of the run, no leader packet of a `*tb` class may be split over several TBs
  when its first TB carried no earlier packet, i.e. when it did not fit on its own. Stock
  SPS without `--spsReselectOnMisfit` makes `periodic1tb` abort (8 packets in 12 s).

**Run CSV columns** (appended):

| column | meaning |
|---|---|
| `tb_max_b` | largest TB of the pool |
| `payload_max_b` | largest packet of the class |
| `leader_pkt_multi_tb` | leader packets carried by more than one TB (whole run) |
| `leader_pkt_multi_tb_unqueued` | the subset that did not fit on its own |

**Pilots** (laptop, highway IVD 30 N 0, seed 1, 20 s, SB-SPS re-run flags + dedicated carrier):

| class | TBs per packet | packets split | grant slots | group / worst PRR |
|---|---|---|---|---|
| `aperiodic1` | 1.76 | 67 | 3.00 | 0.743 / 0.275 |
| `aperiodic1tb` | **1.000** | 0 | 3.00 | 0.929 / 0.677 |
| `periodic2tb` | 1.000 | 0 | 3.00 | 0.895 / 0.489 |
| `periodic1tb` (roundabout, `leaderRes` + map) | 1.000 | 0 | 3.00 | 0.984 / 0.952 |

`aperiodic1tb` has the same packet arrival times as `aperiodic1`; only the sizes change.
One seed: no verdict on the PRR.

## HARQ Option 2 with a per-TB helper (R6): the helper never ACKs (2026-10-15)

**Problem found.** With Option 2 every member that decoded a TB sends its ACK in the same
PSFCH occasion, the named helper included. 5G-LENA is half-duplex for PSFCH
(`nr-spectrum-phy.cc`: an incoming PSFCH is ignored while the UE transmits), as is a real UE.
So the helper did not hear the other members.

Pilot (roundabout IVD 30 N 0, periodic1tb, dedicated carrier, helper 4, 20 s, seed 1):
- In 8 s the helper ignored 405 PSFCH and received 35.
- It also waited for 8 ACKs, its own included, which it can never hear.
- Result: R6 cancelled **0** relays, i.e. it was blind relaying (2.98 tx/packet, like R4).

**Rule (user's design).**
- **The leader** names the TB's helper in SCI-2A (as before).
- **The non-helper members** answer ACK / NACK (Option 2).
- **The named helper never ACKs.**
  - It decoded the TB: it stays silent, listens to the other members' PSFCH, and relays in
    its handed-over slot unless all `members − 1` others ACKed.
  - It did not decode: it NACKs.
- **The leader ignores the helper's silence** and reacts only to its NACK. It then takes back
  the next handed-over slot and retransmits itself. The helper listens to that
  retransmission and, once it has the TB, relays after it.
- **Success** for the leader = every member but the helper ACKed and nobody NACKed.

**Implementation.**
- **NR patch** (`patches/nr/local.patch`):
  - `SlHarqInfo::m_helperRnti` carries the helper named in the TB's SCI-2A to the receiver's
    feedback (`nr-spectrum-phy.cc`; 0 for a relayed copy).
  - `NrSlUeMac::ReclaimSlHelperSlot(dst, harqId)` turns the earliest remaining handed-over
    slot of the current TB back into a leader slot.
- **Receivers** (`InstallOption2HelperSilence`, `nack_only_harq.cc`): for Option 2 a shim on
  the PHY feedback callback drops the ACK of a member whose RNTI is the TB's `m_helperRnti`.
  Everything else passes, so it is a pass-through for R3. It also records every ACK / NACK
  sent.
- **Leader** (`NackOnlyHarqController`, Option 2):
  - The helper of each transmission is read from the SCI-2A the leader sends.
  - A NACK from that member triggers the reclaim.
  - The all-ACK decision and the H4–H6 statistics leave the helper's silence out.
- **Helper:** `HelperGroupSize` = members − 1.

**Known blind spot, measured, not hidden.** A helper that cannot give feedback is silent
too, and the leader then assumes the helper has the TB. Two cases:
- it missed the SCI-1A (`helper_sci_missed`);
- it decoded SCI-1A but not SCI-2A (`helper_corrupt_sci2`).

Plain Option 2 detects such silence as DTX; the "helper never ACKs" rule gives that up for
the helper only.

**Run-CSV columns** (appended; leader transmissions with a named helper, KPI window):

| column | meaning |
|---|---|
| `helper_named_tx` | leader transmissions whose SCI-2A named a helper |
| `helper_decoded_tx` / `helper_corrupt_tx` | the helper decoded / got the TB corrupt (ground truth, PHY) |
| `helper_nack_sent` | the helper's PHY sent a NACK for it |
| `helper_nack_not_sent` | `helper_corrupt_tx − helper_nack_sent`: corrupt but no NACK left the helper |
| `helper_corrupt_sci2` | corrupt because SCI-2A failed: the helper does not know the TB's HARQ process, destination or feedback indicator, so it sends no PSFCH (TS 38.213 §16.3; 5G-LENA skips the feedback). Explains `helper_nack_not_sent` exactly in the pilots |
| `helper_nack_lost` | NACK sent but not credited by the leader |
| `helper_nack` | NACKs the leader credited |
| `helper_reclaim` | … after which it took back a handed-over slot |
| `helper_nack_no_slot` | … with no handed-over slot left (always for S2 / S3: the leader reserves none) |
| `helper_sci_missed` | **the helper missed the SCI and stayed silent** |
| `helper_sci_missed_rate` | `helper_sci_missed / helper_named_tx` |
| `helper_sci_missed_tb` | distinct TBs with such a miss |
| `helper_sci_missed_unserved` | misses after which no copy of the TB followed while some member still lacked it |
| `helper_ack_suppressed` | ACKs the shim kept the helper from sending (whole run) |
| `helper_ack_rx` | ACKs the leader received from the named helper (must be 0) |
| `relay_after_allack` | relays although every other member had ACKed the previous transmission |

**Assertions** (the run aborts):
- `helper_ack_rx > 0`;
- a helper that decoded TBs but had no ACK suppressed;
- (existing) a leader retransmission after all-ACK.

R4 / R5 (S1 and S3) stay byte-identical; the run CSV only gains the columns above.

**Pilots** (laptop):

| run | relays sent / cancelled | tx/packet | worst PRR | helper NACK → reclaim | SCI missed |
|---|---|---|---|---|---|
| R4 blind, S1 | 352 / 0 | 2.96 | 0.928 | — | — |
| R5 Option 1, S1 | 84 / 175 | 1.47 | 0.952 | — | — |
| R6 before the fix, S1 | 354 / **0** | 2.98 | 0.922 | — | — |
| **R6, S1** | 192 / **81** | **2.14** | 0.952 | 4 → 4 | 0 |
| R6, S3 | 188 / 75 | 2.07 | 0.922 | 3 → 0 (no slot) | 0 |

These are roundabout IVD 30 N 0, 20 s, as above.

A loaded pilot (highway IVD 30 N 60, `--bgFlowsPerCar=3.33`, periodic2tb, 9 s):
- the helper missed the SCI on 63 of 901 named transmissions (7 %);
- 353 helper NACKs were credited, 332 slots taken back.
- Of the helper's 568 corrupt receptions, 215 had a failed SCI-2A and sent no NACK
  (`helper_nack_not_sent` = `helper_corrupt_sci2` = 215, `helper_nack_lost` = 0).
- So 278 of the helper's 831 failures (33 %) were silent; 54 of the 63 SCI misses got no
  later copy of the TB.

## Helper selection: Kuo one-relay objective, fixes, H4 rules (2026-10-15)

**Objective = Kuo et al. 2021, one-time retransmission (Sec. IV-C, eq. 11–13).** With
P_a−b = 1 − P(success) of a link:

GPRR_i = 1/|ω−t| · Σ_{r∈ω−t} [1 − P_t−r·(P_t−i + (1 − P_t−i)·P_i−r)] (eq. 12),
and the helper is argmax_i (eq. 13, "maxmean once").

- `--kuoObjective=min` uses Kuo's "maxmin once" benchmark: argmax_i of min_r.
- One helper relays one TB: the two-retransmitter case (eq. 14–18) is not used.
- Two deviations from Kuo, both stated:
  - candidates are the members only (Kuo also lets the transmitter itself retransmit; here
    R1–R3 are the leader-only arms);
  - the helper's own term is its direct link (it gets no retransmission).
- The code already computed eq. 12. The earlier comments and README cited eq. 17 (the
  two-relay formula); corrected.
- The link model stays the simulator's: TR 37.885 path loss + shadowing + NLOSv blockage
  against the TB's link budget. Kuo uses a −90 dBm sensitivity step.

**Fixes** (review 2026-10-15):

| # | before | now | affects |
|---|---|---|---|
| 1 | a member's ACK on the leader's *retransmission* of a TB overwrote its failure on the *first* transmission | the selector gets the first transmission's outcome only (controller and `OnFeedback`) — the direct link pL[r] that eq. 12 and H4 estimate | H4–H6 (R6) |
| 2 | P(LOS) = urban 1.05·e^(−0.0114 d) in every scenario | the scenario's TR 37.885 P(LOS): highway = Kuo eq. 2–3 (min(1, 2.1013e-6 d² − 0.002 d + 1.0193), d ≤ 475 m; max(0, 0.54 − 0.001 (d − 475)) beyond) | kuo / harqKuo on the highway. map: unchanged in practice — the in-lane platoon blocks every non-adjacent link, adjacent links succeed with P ≈ 1 (pilot: identical scores) |
| 3 | H4 distance tie window 1 mm (never a tie under position error) | `--hstatTieM` (default max(1 m, 2·posErrM)) | H4 |

Not changed, by evidence: `map` keeps the LOS / NLOSv mixture on a geometrically clear link.
ns-3's `ThreeGppV2v{Urban,Highway}ChannelConditionModel` still draws NLOSv with 1 − P(LOS)
there, and the geometric wrapper only adds vehicle blockage. So returning pure LOS would make
the selector overconfident.

**H4 candidate rules** (`--hstatRule`, w = the member with the highest failure rate in the
window):

| rule | helper |
|---|---|
| `nearest` (default) | the member nearest to w; ties within `--hstatTieM` → the one ahead of w |
| `nearestAhead` | the nearest member ahead of w (closer to the leader); w = 1 → nearest |
| `nearestReliable` | nearest to w among members with own failure rate ≤ `--hstatCandMaxFail` (0.5); none → the most reliable member |
| `reliableAhead` | among the members ahead of w, the lowest failure rate (ties → nearest to w); w = 1 → nearestReliable |

Run CSV (appended): `hstat_rule`, `hstat_tie_m`, `hstat_cand_max_fail`, `kuo_objective`.

**Trying strategies** (`run_retx.sh`, stage B). The base arms keep their names, so completed
runs are reused. Variants are extra arms:
- `H4_RULES=nearest,reliableAhead,nearestReliable` adds `R6_S1_H4-<rule>_w<w>`;
- `H4_CAND_MAX_FAIL=0.5` sets the nearestReliable threshold;
- `KUO_OBJECTIVES=mean,min` adds `<arm>-maxmin` for every kuo / map / harqKuo / harqMap arm;
- `ARM_FILTER=<regex>` runs only matching arms (all stages).

The run check verifies `hstat_rule` / `kuo_objective` against the arm's flags.

**Validation:**
- `test_helper_select`: 65 / 65. Test 20 was updated to the first-transmission rule; new
  tests 22 (the four H4 rules, the tie under 1.5 m position error) and 23 (mean / min,
  urban / highway).
- Pilots (roundabout / highway IVD 30 N 0, 20 s):
  - roundabout map, highway map and roundabout harqMap are byte-identical to before;
  - highway kuo changed (3 of 180 choices);
  - `reliableAhead` moves the H4 helper ahead (members 4–5 instead of 6–7);
  - maxmin = maxmean at N 0.

**Follow-up (same day).**
- **Helper statistics bias fixed.** The member named as helper for a TB is now left out of
  the H4–H6 statistics of that TB. Before, its NACKs were recorded but its silent successes
  were not, so its estimated leader link was biased downward.
- **Position error for the geometric selectors.** `run_retx.py --posErrM` (default 1.5 m,
  env `POS_ERR_M`) adds `--posErrM` to every H3 (`map`) and H6 (`harqMap`) arm. The run
  CSV records it (`pos_err_m`) and the run check verifies it. H2 / H4 / H5 use exact CAM
  positions.
- **Runner options.** `--bg`, `--scenarios`, `--ivds` and `--traffics` restrict a stage's
  cells. A run that exits without its run CSV counts as failed.

**Laptop test of the H strategies:** `./run_local_h.sh` runs the stage-B arms with no
background traffic and one seed (IVD 15 / 30, both scenarios, all three traffic classes:
216 runs at the default 18 arms; JOBS = 2). It is resumable and writes
`results/retx_local_h/summary_h.csv` (worst-member PRR, GPRR, tx/packet, helper changes per
arm and scenario). Variants via `H4_RULES` / `KUO_OBJECTIVES`, as for the server.

**Stale runs:** `move_stale_runs.py results/retx_campaign [--apply] [--posErrM 1.5]` moves
the runs these fixes and the R6 fix make stale. It decides from each run's own CSV (missing
columns = older binary): R6 before the R6 fix; H4–H6 before the statistics fix; H3 / H6
without the position error; H2 on the highway before the P(LOS) fix. Nothing is deleted; each
application moves to a new `pre_fix_<date-time>` folder.

## Running on another machine (e.g. a Linux server)

```bash
# 0. system packages (Debian/Ubuntu): compiler, build tools, SQLite, Eigen, SUMO
sudo apt install g++ cmake ninja-build python3-pip libsqlite3-dev libeigen3-dev sumo sumo-tools
python3 -m pip install --user pandas numpy matplotlib scipy
export SUMO_HOME=/usr/share/sumo        # apt SUMO; or point it at any SUMO build

# 1. assemble the tree (submodules at pinned SHAs + patches + scratch symlinks)
./setup.sh

# 2. configure ns-3 once and build only this target
cd ns-3-dev
./ns3 configure --build-profile=optimized --disable-werror
./ns3 build platoon_harq_robustness_eval

# 3. sweep (resumable; up to ~1 GB RAM per heavy job) and analysis
cd ../scratch_overlay/platoon_harq_robustness_eval
python3 run_campaign.py --seeds 1 2 3 --jobs 12        # -> results/campaign_rlc0
python3 analyze.py --results results/campaign_rlc0 --out results/analysis_rlc0
```

`run_campaign.py` picks up the newest `ns3*-platoon_harq_robustness_eval-*`
binary (any build profile), or use `--bin`. SUMO is found through `$SUMO_HOME`
(`tools/` for TraCI, `bin/` for the binaries), otherwise on the `PATH`.
Long runs: start it inside `tmux`/`screen` or with `nohup`.
