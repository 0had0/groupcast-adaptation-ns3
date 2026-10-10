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
