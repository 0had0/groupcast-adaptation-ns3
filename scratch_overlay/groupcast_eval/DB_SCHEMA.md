# groupcast_eval — SQLite Database Schema Reference

Each simulation run produces a `<simTag>.db` SQLite file (path recorded in the
`kpiDb` column of `*_run.csv`).  The file contains six **raw trace tables**
collected during the simulation and five **computed KPI tables** written by
`V2xKpi::WriteKpis()` at the end.  Every table carries `SEED` and `RUN`
columns so that multiple seeds / runs can be stored in the same database.

---

## 1. `pktTxRx` — Application-layer packet trace

**Source:** `UeToUePktTxRxOutputStats`, wired in `kpi_hooks.cc`.  
**NS-3 trace hooks:** `OnOffApplication::TxWithSeqTsSize` (event = `"tx"`) and
`PacketSink::RxWithSeqTsSize` (event = `"rx"`).  
One row is written for every packet sent **and** for every packet successfully
received at the application layer.

| Column | Type | Unit | Description |
|---|---|---|---|
| `timeSec` | REAL | s | `Simulator::Now()` at the TX or RX event |
| `txRx` | TEXT | — | `"tx"` (sent by app) or `"rx"` (delivered to sink) |
| `nodeId` | INTEGER | — | NS-3 node ID of the TX or RX node |
| `imsi` | INTEGER | — | IMSI of the UE |
| `pktSizeBytes` | INTEGER | bytes | Packet payload + `SeqTsSizeHeader` serialised size |
| `srcIp` | TEXT | — | Source IPv4 address |
| `srcPort` | INTEGER | — | Source UDP port (8001 = urllc_cam, 8002 = v2x_video, 8003 = non_gbr) |
| `dstIp` | TEXT | — | Destination IPv4 address (multicast group `225.63.63.1`) |
| `dstPort` | INTEGER | — | Destination UDP port (mirrors `srcPort` for the same flow) |
| `pktSeqNum` | INTEGER | — | Sequence number from `SeqTsSizeHeader::GetSeq()`, monotonically increasing per sender |
| `SEED` | INTEGER | — | `RngSeedManager::GetSeed()` |
| `RUN` | INTEGER | — | `RngSeedManager::GetRun()` |

**Notes:**
- A packet that is lost in the PHY will have a `"tx"` row but no matching `"rx"` row.
- Delay for a packet = `timeSec(rx) − timeSec(tx)` joined on `(srcIp, dstPort, pktSeqNum)`.
- The in-memory cache is flushed via `EmptyCache()` after `Simulator::Run()`.

---

## 2. `pscchTxUeMac` — PSCCH (SCI-1A) MAC scheduling record

**Source:** `UeMacPscchTxOutputStats`, wired in `kpi_hooks.cc`.  
**NS-3 trace hook:** `NrSlUeMac::SlPscchScheduling`.  
One row per PSCCH grant published to the PHY (one per SL transmission slot
that carries a new SCI-1A).

| Column | Type | Unit | Description |
|---|---|---|---|
| `timeMs` | REAL | ms | Simulation time of the scheduling event |
| `imsi` | INTEGER | — | IMSI of the transmitting UE |
| `rnti` | INTEGER | — | RNTI of the transmitting UE |
| `frame` | INTEGER | — | 5G-NR frame number |
| `subFrame` | INTEGER | — | Subframe index (0–9) |
| `slot` | INTEGER | — | Slot index within the subframe (0 to `2^µ − 1`) |
| `symStart` | INTEGER | — | First OFDM symbol of the PSCCH allocation (0–13) |
| `symLen` | INTEGER | symbols | Number of OFDM symbols occupied by the PSCCH |
| `rbStart` | INTEGER | — | First resource block of the PSCCH |
| `rbLen` | INTEGER | RBs | Number of resource blocks occupied |
| `priority` | INTEGER | — | SCI-1A priority field (lower = higher priority in 3GPP; mapped to LC priority) |
| `mcs` | INTEGER | — | MCS index used for the PSCCH (fixed by `NrSlUeMacSchedulerFixedMcs`) |
| `tbSize` | INTEGER | bytes | Transport-block size of the SCI-1A |
| `rsvpMs` | INTEGER | ms | Resource-reservation interval (RRI) carried in SCI-1A: 20, 50, or 100 ms |
| `totSbCh` | INTEGER | — | Total sub-channels in the SL resource pool |
| `sbChStart` | INTEGER | — | First sub-channel of the linked PSSCH allocation |
| `sbChLen` | INTEGER | sub-channels | Number of sub-channels allocated for PSSCH |
| `maxNumPerReserve` | INTEGER | — | Maximum number of PSSCH repetitions per reservation period |
| `gapReTx1` | INTEGER | slots | Slot gap to the first HARQ retransmission (0 if none) |
| `gapReTx2` | INTEGER | slots | Slot gap to the second HARQ retransmission (0 if none) |
| `SEED` | INTEGER | — | RNG seed |
| `RUN` | INTEGER | — | Run index |

---

## 3. `psschTxUeMac` — PSSCH MAC scheduling record

**Source:** `UeMacPsschTxOutputStats`, wired in `kpi_hooks.cc`.  
**NS-3 trace hook:** `NrSlUeMac::SlPsschScheduling`.  
One row per PSSCH grant (both NDI=1 new transmissions and NDI=0 retransmissions).

| Column | Type | Unit | Description |
|---|---|---|---|
| `timeMs` | REAL | ms | Simulation time of the scheduling event |
| `imsi` | INTEGER | — | Transmitting UE IMSI |
| `rnti` | INTEGER | — | Transmitting UE RNTI |
| `srcL2Id` | INTEGER | — | Sidelink source Layer-2 ID |
| `dstL2Id` | INTEGER | — | Sidelink destination Layer-2 ID (254 for groupcast in this simulation) |
| `frame` | INTEGER | — | Frame number |
| `subFrame` | INTEGER | — | Subframe index (0–9) |
| `slot` | INTEGER | — | Slot index |
| `symStart` | INTEGER | — | First OFDM symbol of the PSSCH |
| `symLen` | INTEGER | symbols | OFDM symbols occupied (9 when PSFCH is present, else 12) |
| `sbChSize` | INTEGER | RBs | Sub-channel size in resource blocks |
| `rbStart` | INTEGER | — | First RB of the PSSCH |
| `rbLen` | INTEGER | RBs | Number of RBs |
| `harqId` | INTEGER | — | HARQ process ID (0–15) |
| `ndi` | INTEGER | — | New Data Indicator: 1 = new TB, 0 = HARQ retransmission |
| `rv` | INTEGER | — | Redundancy Version (0–3); increases for each retransmission |
| `reselCounter` | INTEGER | — | Remaining resource-reselection counter ticks for this SPS grant |
| `cReselCounter` | INTEGER | — | Remaining candidate-reselection counter ticks |
| `csiReq` | INTEGER | — | CSI feedback requested: 0 or 1 |
| `castType` | INTEGER | — | 0 = unicast, 1 = groupcast, 2 = broadcast |
| `SEED` | INTEGER | — | RNG seed |
| `RUN` | INTEGER | — | Run index |

---

## 4. `pscchRxUePhy` — PSCCH PHY reception record

**Source:** `UePhyPscchRxOutputStats`, wired in `kpi_hooks.cc`.  
**NS-3 trace hook:** `NrSpectrumPhy::RxPscchTraceUe`.  
One row per PSCCH (SCI-1A) decode attempt at each receiving UE.

| Column | Type | Unit | Description |
|---|---|---|---|
| `timeMs` | REAL | ms | Simulation time of the decode attempt |
| `cellId` | INTEGER | — | Cell / SL pool identifier |
| `rnti` | INTEGER | — | RNTI of the **receiving** UE |
| `bwpId` | INTEGER | — | Bandwidth-part ID of the SL BWP |
| `frame` | INTEGER | — | Frame number |
| `subFrame` | INTEGER | — | Subframe index |
| `slot` | INTEGER | — | Slot index |
| `txRnti` | INTEGER | — | RNTI of the transmitting UE (extracted from decoded SCI-1A) |
| `dstL2Id` | INTEGER | — | Groupcast destination L2 ID from SCI-1A |
| `pscchRbStart` | INTEGER | — | First RB of the received PSCCH |
| `pscchRbLen` | INTEGER | RBs | Number of RBs |
| `pscchMcs` | INTEGER | — | MCS index of the PSCCH |
| `avrgSinr` | REAL | dB | Average post-processing SINR across the PSCCH RBs |
| `minSinr` | REAL | dB | Minimum per-subcarrier SINR |
| `tbler` | INTEGER | % | Block Error Rate estimate (0–100) |
| `corrupt` | INTEGER | — | 0 = SCI-1A decoded successfully, 1 = CRC failure |
| `psschStartSbCh` | INTEGER | — | PSSCH start sub-channel advertised in SCI-1A |
| `psschLenSbCh` | INTEGER | sub-channels | PSSCH length in sub-channels from SCI-1A |
| `maxNumPerReserve` | INTEGER | — | Max repetitions from SCI-1A |
| `rsvpMs` | INTEGER | ms | Resource-reservation interval from SCI-1A |
| `SEED` | INTEGER | — | RNG seed |
| `RUN` | INTEGER | — | Run index |

---

## 5. `psschRxUePhy` — PSSCH PHY reception record

**Source:** `UePhyPsschRxOutputStats`, wired in `kpi_hooks.cc`.  
**NS-3 trace hook:** `NrSpectrumPhy::RxPsschTraceUe`.  
One row per PSSCH (data channel) decode attempt at each receiving UE.  This is
the primary table for link-level analysis.

| Column | Type | Unit | Description |
|---|---|---|---|
| `timeMs` | REAL | ms | Simulation time of the decode attempt |
| `cellId` | INTEGER | — | Cell / SL pool identifier |
| `rnti` | INTEGER | — | RNTI of the **receiving** UE |
| `bwpId` | INTEGER | — | Bandwidth-part ID |
| `frame` | INTEGER | — | Frame number |
| `subFrame` | INTEGER | — | Subframe index |
| `slot` | INTEGER | — | Slot index |
| `txRnti` | INTEGER | — | RNTI of the transmitting UE |
| `srcL2Id` | INTEGER | — | Source Layer-2 ID |
| `dstL2Id` | INTEGER | — | Destination Layer-2 ID (groupcast = 254) |
| `psschRbStart` | INTEGER | — | First RB of the PSSCH |
| `psschRbLen` | INTEGER | RBs | Number of RBs |
| `psschSymStart` | INTEGER | — | First OFDM symbol |
| `psschSymLen` | INTEGER | symbols | Number of OFDM symbols |
| `psschMcs` | INTEGER | — | MCS index (0–28) |
| `ndi` | INTEGER | — | 1 = new TB, 0 = HARQ retransmission |
| `rv` | INTEGER | — | Redundancy Version (0–3) |
| `tbSizeBytes` | INTEGER | bytes | Transport-block size |
| `avrgSinr` | INTEGER | dB | Average SINR (integer, rounded) |
| `minSinr` | INTEGER | dB | Minimum subcarrier SINR (integer, rounded) |
| `psschTbler` | INTEGER | % | PSSCH BLER estimate (0–100) |
| `psschCorrupt` | INTEGER | — | 0 = PSSCH CRC pass, 1 = CRC failure |
| `sci2Tbler` | INTEGER | % | SCI-2B BLER estimate (0–100) |
| `sci2Corrupt` | INTEGER | — | 0 = SCI-2B CRC pass, 1 = CRC failure |
| `SEED` | INTEGER | — | RNG seed |
| `RUN` | INTEGER | — | Run index |

**Notes:**
- A row is written even when `psschCorrupt = 1`; lost packets appear here with
  `psschCorrupt = 1` but have no corresponding `pktTxRx` RX row.
- `avrgSinr` and `minSinr` use the `NrEesmIrT1` error model SINR estimate.

---

## 6. `rlcRx` — RLC PDU reception record

**Source:** `UeRlcRxOutputStats`, wired in `kpi_hooks.cc`.  
**NS-3 trace hook:** `NrSlUeMac::RxRlcPduWithTxRnti`.  
One row per RLC PDU successfully reassembled at the receiving RLC entity.

| Column | Type | Unit | Description |
|---|---|---|---|
| `timeMs` | REAL | ms | Simulation time of the RLC delivery event |
| `imsi` | INTEGER | — | IMSI of the receiving UE |
| `rnti` | INTEGER | — | RNTI of the receiving UE |
| `txRnti` | INTEGER | — | RNTI of the transmitting UE |
| `lcid` | INTEGER | — | Logical Channel ID (identifies the flow; distinct per flow/priority) |
| `rxPduSize` | INTEGER | bytes | Size of the reassembled RLC SDU |
| `delayMicroSec` | REAL | µs | End-to-end delay from RLC TX enqueue to RLC RX delivery |
| `SEED` | INTEGER | — | RNG seed |
| `RUN` | INTEGER | — | Run index |

**Notes:**
- `delayMicroSec` is measured at the RLC layer; it includes MAC scheduling
  latency, PHY transmission, propagation delay, and HARQ combining time.  It
  does **not** include IP/UDP/application queuing time.
- For a full application-layer delay, use `pktTxRx` joined on sequence number.

---

## 7. `avrgPir` — Average Packet Inter-Reception interval (computed)

**Source:** `V2xKpi::SaveAvrgPir()`, written after `Simulator::Run()`.  
**Input table:** `pktTxRx` (RX rows only).  
One row per `(RX node, TX source IP)` pair with at least two received packets.

| Column | Type | Unit | Description |
|---|---|---|---|
| `txRx` | TEXT | — | Always `"rx"` |
| `nodeId` | INTEGER | — | RX node ID |
| `imsi` | INTEGER | — | RX node IMSI |
| `srcIp` | TEXT | — | IP address of the transmitting node |
| `dstIp` | TEXT | — | IP address of the receiving node |
| `avrgPirSec` | REAL | s | Mean inter-reception interval; computed as `Σ(t_i − t_{i−1}) / (N−1)` over all N received packets from that TX |
| `TxRxDistance` | REAL | m | 2-D Euclidean distance between the TX and RX positions (only when range filtering is enabled via `SetRangeForV2xKpis`) |
| `SEED` | INTEGER | — | RNG seed |
| `RUN` | INTEGER | — | Run index |

**Relationship to 3GPP KPI:** PIR ≤ PDB (Packet Delay Budget) is the primary
latency criterion for V2X QoS profiles.  For flow 1 (urllc_cam, RRI=20 ms)
the target is PIR ≤ 20 ms.

---

## 8. `thput` — Per-link throughput (computed)

**Source:** `V2xKpi::SaveThput()`, written after `Simulator::Run()`.  
**Input table:** `pktTxRx` (RX rows only).  
One row per `(RX node, TX source IP)` pair.

| Column | Type | Unit | Description |
|---|---|---|---|
| `txRx` | TEXT | — | Always `"rx"` |
| `nodeId` | INTEGER | — | RX node ID |
| `imsi` | INTEGER | — | RX node IMSI |
| `srcIp` | TEXT | — | TX node IP |
| `totalPktTxed` | INTEGER | — | Total packets transmitted by that TX (derived from `pktTxRx` TX rows) |
| `dstIp` | TEXT | — | RX node IP |
| `totalPktRxed` | INTEGER | — | Packets received by this RX from that TX |
| `thputKbps` | REAL | kbit/s | `(Σ pktSizeBytes × 8) / txAppDuration / 1000`, where `txAppDuration` is set via `V2xKpi::SetTxAppDuration()` |
| `SEED` | INTEGER | — | RNG seed |
| `RUN` | INTEGER | — | Run index |

**Notes:**
- `txAppDuration` = `appStop − appStart` from `groupcast_eval.cc`; if
  `ConsiderAllTx(true)` is set, rows with 0 received packets are also written.

---

## 9. `avrgPrr` — Average Packet Reception Ratio (computed)

**Source:** `V2xKpi::SaveAvrgPrr()`, written after `Simulator::Run()`.  
**Input tables:** `pktTxRx` + optional position map from `SavePositionPerIP()`.  
One row per transmitting node.

| Column | Type | Unit | Description |
|---|---|---|---|
| `txRx` | TEXT | — | Always `"tx"` |
| `nodeId` | INTEGER | — | TX node ID |
| `imsi` | INTEGER | — | TX node IMSI |
| `Ip` | TEXT | — | TX node IP address |
| `range` | REAL | m | Distance threshold used (0 if range filtering is disabled) |
| `numNieb` | INTEGER | — | Number of neighbour nodes within `range` that could have received |
| `avrgPrr` | REAL | — | `(Σ_n received_from_TX_n) / (numNieb × totalPktTxed)`, in [0, 1] |
| `SEED` | INTEGER | — | RNG seed |
| `RUN` | INTEGER | — | Run index |

**Computation details:**
1. For each RX node within `range` of the TX, count the unique sequence numbers
   received from that TX.
2. Sum across all in-range neighbours and divide by `numNieb × totalPktTxed`.
3. Nodes outside `range`, or runs with `numNieb = 0`, are skipped.

**Relationship to output CSV:** `groupPrr` in `*_run.csv` is derived from this
table as `min(avrgPrr)` over all TX nodes, across all three flows jointly.

---

## 10. `simulPsschTx` — Simultaneous PSSCH collision statistics (computed)

**Source:** `V2xKpi::ComputePsschTxStats()`.  
**Input table:** `psschTxUeMac`.  
One row for the entire simulation run.

| Column | Type | Unit | Description |
|---|---|---|---|
| `totalTx` | INTEGER | — | Total number of PSSCH scheduling records |
| `numNonOverlapping` | INTEGER | — | PSSCH transmissions with no time-frequency overlap with any other TX |
| `numOverlapping` | INTEGER | — | PSSCH transmissions that share at least one RB and at least one symbol with another simultaneous TX |
| `SEED` | INTEGER | — | RNG seed |
| `RUN` | INTEGER | — | Run index |

**Overlap criterion:** Two PSSCH records collide if they share the same
`(frame, subFrame, slot)` **and** their `(rbStart, rbLen)` ranges intersect
**and** their `(symStart, symLen)` ranges intersect.  Collisions inflate
interference and are a direct indicator of NR-SL resource-pool congestion.

---

## 11. `PsschTbRx` — PSSCH transport-block decode summary (computed)

**Source:** `V2xKpi::ComputePsschTbCorruptionStats()`.  
**Input table:** `psschRxUePhy`.  
One row for the entire simulation run.

| Column | Type | Unit | Description |
|---|---|---|---|
| `totalRx` | INTEGER | — | Total rows in `psschRxUePhy` (all decode attempts) |
| `psschSuccessCount` | INTEGER | — | Rows where `psschCorrupt = 0` |
| `psschFailCount` | INTEGER | — | `totalRx − psschSuccessCount` |
| `sci2SuccessCount` | INTEGER | — | Rows where `sci2Corrupt = 0` |
| `sci2FailCount` | INTEGER | — | `totalRx − sci2SuccessCount` |
| `SEED` | INTEGER | — | RNG seed |
| `RUN` | INTEGER | — | Run index |

**Derived metrics:**
- **PSSCH BLER** = `psschFailCount / totalRx`
- **SCI-2 BLER** = `sci2FailCount / totalRx`
- A high `sci2FailCount` with low `psschFailCount` indicates that the control
  sub-channel (SCI-2B carried on PSSCH) is the bottleneck, not the data.

---

## Flow → port mapping

| Port | Label | Priority | Scheduling | RRI |
|---|---|---|---|---|
| 8001 | `urllc_cam` | 1 (highest) | Dynamic Grant (per-PDU) | 20 ms |
| 8002 | `v2x_video` | 2 | SPS | 50 ms |
| 8003 | `non_gbr` | 3 (lowest) | SPS | 100 ms |

To isolate a single flow in any query, filter on `srcPort` / `dstPort` in
`pktTxRx`, or join `pscchTxUeMac.rsvpMs` with the RRI column above.

---

## Example queries

```sql
-- Per-flow application-layer PRR (fraction of TX packets that were received)
SELECT dstPort,
       CAST(SUM(txRx='rx') AS REAL) / SUM(txRx='tx') AS prr
FROM pktTxRx
GROUP BY dstPort;

-- Mean application delay per flow (ms)
SELECT t.dstPort,
       AVG((r.timeSec - t.timeSec) * 1000) AS mean_delay_ms
FROM pktTxRx t
JOIN pktTxRx r
  ON t.srcIp = r.srcIp
 AND t.dstPort = r.dstPort
 AND t.pktSeqNum = r.pktSeqNum
 AND t.txRx = 'tx'
 AND r.txRx = 'rx'
GROUP BY t.dstPort;

-- PSSCH BLER over time (100 ms bins)
SELECT ROUND(timeMs / 100) * 100 AS bin_ms,
       AVG(psschCorrupt) AS bler
FROM psschRxUePhy
GROUP BY bin_ms
ORDER BY bin_ms;

-- Per-UE average PRR
SELECT nodeId, imsi, avrgPrr
FROM avrgPrr
ORDER BY avrgPrr;

-- Collision rate
SELECT CAST(numOverlapping AS REAL) / totalTx AS collision_rate
FROM simulPsschTx;
```
