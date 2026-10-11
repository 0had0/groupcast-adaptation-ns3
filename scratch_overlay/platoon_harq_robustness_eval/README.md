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
