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
SINR, LOS/NLOSv/NLOS share of the leader->member links, PRR within the PDB.

## Files

| file | role |
|---|---|
| `gen_mobility.py` | SUMO/TraCI mobility traces (both scenarios, platoon + seats) |
| `platoon_harq_robustness_eval.cc` | ns-3 program |
| `nack_only_harq.{h,cc}` | Option-1 TX decision + packet->TB tagging |
| `sumo_buildings.{h,cc}` | SUMO buildings -> ns3::BuildingList (from roundabout_rsu_eval) |
| `sumo/highway`, `sumo/roundabout` | copied scenario assets |
| `run_campaign.py` | resumable parallel sweep |
| `analyze.py` | aggregation + figures |

```bash
cd ns-3-dev/cmake-cache && ninja scratch_platoon_harq_robustness_eval_platoon_harq_robustness_eval
cd ../../scratch_overlay/platoon_harq_robustness_eval
python3 run_campaign.py --seeds 1 2 3 --jobs 7
python3 analyze.py
```

Single run:

```bash
python3 gen_mobility.py --scenario roundabout --ivd 15 --nbg 30 --seed 1 --out traces/x.csv.gz
../../ns-3-dev/build/scratch/platoon_harq_robustness_eval/ns3.42-platoon_harq_robustness_eval-default \
  --scenario=roundabout --mobility=traces/x.csv.gz --sumoNet=sumo/roundabout/roundabout.net.xml \
  --traffic=periodic2 --out=results/x
```

## Results (3 seeds, 270 runs; `results/analysis/`)

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
mobility traces as the campaign. The MCS-14 baseline is reused from `results/campaign`.

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
- `run_variants.py` re-checks MCS, option1_tx, psfch_period, slot fraction and explicit ACKs
  after every run and logs `FLAG CHECK FAILED` on a mismatch.

Caveats found on the way:
- `cbr` sums all PSSCHs, so collided transmissions count twice: it is the offered load and can
  exceed 1 (MCS 5, periodic2, N = 45: 1.11). New column `cbr_occupied` counts each
  subchannel-slot once (0.69 in that cell). The campaign CSVs only have `cbr`.
- periodic2 at MCS 5/9 needs 2-3 TBs per packet; segments pipeline with the next packet and
  the delay settles at ≈ 15 ms > 10 ms PDB, so `prr_pdb` ≈ 0 although `prr` is not.

```bash
python3 run_variants.py --jobs 7            # 216 runs, then analysis
python3 run_variants.py --analyze-only      # results/variants/analysis/
```

On another machine (after `git pull` and `./ns3 build platoon_harq_robustness_eval`), list
`mcs14` too: baseline cells missing from that machine's `results/campaign` are then run into
`results/variants/mcs14` (cells present there are reused). Traces missing from `traces/` are
generated with the local SUMO. Heavy runs (highway N = 60) take > 0.5 GB, so keep
`--jobs` ≤ RAM/2.5 GB:

```bash
nohup python3 run_variants.py --variants mcs14 mcs5 mcs9 dedicated blind --jobs 12 \
      > results/variants_stdout.log 2>&1 &
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

# 3. sweep (resumable; ~250 MB RAM per job) and analysis
cd ../scratch_overlay/platoon_harq_robustness_eval
python3 run_campaign.py --seeds 1 2 3 --jobs $(( $(nproc) - 1 ))
python3 analyze.py
```

`run_campaign.py` picks up the newest `ns3*-platoon_harq_robustness_eval-*`
binary (any build profile), or use `--bin`. SUMO is found through `$SUMO_HOME`
(`tools/` for TraCI, `bin/` for the binaries), otherwise on the `PATH`.
Long runs: start it inside `tmux`/`screen` or with `nohup`.
