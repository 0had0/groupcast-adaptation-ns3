# groupcast_eval — Running Guide

## Prerequisites

| Requirement | Version / notes |
|---|---|
| NS-3 | 3.42 (this tree) |
| CTTC NR module | present in `contrib/nr/` |
| C++ compiler | Clang or GCC with C++17 |
| CMake | ≥ 3.16 |
| SQLite3 | system library (for trace DB and V2xKpi) |
| Python 3 | ≥ 3.8 (for the sweep driver) |
| SUMO | ≥ 1.14 — **optional**, only needed for SUMO-driven mobility |

---

## 1. Build

All commands are run from the NS-3 root:

```bash
cd /path/to/groupcast-adaptation-ns3/ns-3-dev
```

### Configure (first time only)

```bash
./ns3 configure --enable-examples
```

The `--enable-examples` flag is required because `v2x-kpi.cc` is borrowed
from the NR examples and shares the same include paths.

### Build the binary

```bash
./ns3 build groupcast_eval
```

The optimised binary is written to:

```
build/scratch/groupcast_eval/ns3.42-groupcast_eval-optimized
```

The debug binary (default unless you pass `--build-profile optimized`) is at:

```
build/scratch/groupcast_eval/ns3.42-groupcast_eval-debug
```

Use the **optimised** binary for sweep runs — it is ~5× faster.  
To build optimised:

```bash
./ns3 configure --enable-examples --build-profile=optimized
./ns3 build groupcast_eval
```

---

## 2. Run a single simulation

Use `./ns3 run` to launch the debug binary (convenient for development):

```bash
./ns3 run "groupcast_eval \
  --runId=test \
  --seed=1 \
  --numUEs=8 \
  --mu=1 \
  --bwPRBs=50 \
  --mcs=14 \
  --simTime=10 \
  --warmup=1 \
  --outputDir=/tmp/results/ \
  --outPacketsCsv=/tmp/results/test_packets.csv \
  --outRunCsv=/tmp/results/test_run.csv"
```

Or call the optimised binary directly (faster, no build overhead):

```bash
./build/scratch/groupcast_eval/ns3.42-groupcast_eval-optimized \
  --runId=test \
  --seed=1 \
  --numUEs=8 \
  --mu=1 \
  --bwPRBs=50 \
  --mcs=14 \
  --simTime=10 \
  --warmup=1 \
  --outputDir=/tmp/results/ \
  --outPacketsCsv=/tmp/results/test_packets.csv \
  --outRunCsv=/tmp/results/test_run.csv
```

### Enabling NS-3 logs

Prepend `NS_LOG` to see detailed per-module output:

```bash
NS_LOG="GroupcastEval=info:GroupcastEval.Mobility=info:\
GroupcastEval.NrSlStack=info:GroupcastEval.GroupcastFlows=info:\
GroupcastEval.KpiHooks=info" \
./ns3 run "groupcast_eval --numUEs=4 --simTime=5"
```

For verbose MAC/PHY traces add:

```bash
NS_LOG="NrSlUeMac=level_all:NrSpectrumPhy=level_all"
```

---

## 3. CLI reference

| Flag | Type | Default | Description |
|---|---|---|---|
| `--runId` | string | `run0` | Unique identifier for this run; used as the SQLite DB filename tag unless `--simTag` is set |
| `--seed` | uint | `1` | NS-3 `RngSeedManager::SetRun` value; controls all random variables (jitter, channel, resource selection) |
| `--numUEs` | uint | `8` | Number of group members (minimum 2); all UEs both transmit and receive all three flows |
| `--mu` | uint | `1` | Numerology: `0` = 15 kHz SCS / 1 ms slots, `1` = 30 kHz / 0.5 ms, `2` = 60 kHz / 0.25 ms |
| `--bwPRBs` | uint | `50` | Subchannel size W in PRBs (the pool `SlSubchannelSize`); physical bandwidth is always 20 MHz |
| `--mcs` | uint | `14` | Fixed MCS index for `NrSlUeMacSchedulerFixedMcs` (0–28) |
| `--simTime` | double | `10.0` | Per-flow traffic duration in seconds, measured from the first packet sent |
| `--warmup` | double | `1.0` | Seconds to discard in post-processing (not enforced by the binary; passed through to the run CSV for `aggregate_results.py`) |
| `--harq` | bool | `true` | Enable HARQ for all three flows (`slMaxTxTransNumPssch = 5` when true, `= 1` when false) |
| `--outPacketsCsv` | string | *(empty)* | Path for the per-packet CSV; no file is written if empty |
| `--outRunCsv` | string | *(empty)* | Path for the one-line run-summary CSV; no file is written if empty |
| `--outputDir` | string | `./` | Directory where `<simTag>.db` is written |
| `--simTag` | string | = `runId` | Stem of the SQLite DB filename: `<outputDir><simTag>.db` |
| `--sumoCfg` | string | *(empty)* | Full path to a SUMO `.sumocfg` file; when empty the highway fallback is used |
| `--sumoBinary` | string | `sumo` | Path or name of the SUMO executable |
| `--sumoSync` | double | `0.1` | SUMO–NS-3 position synchronisation interval in seconds |

---

## 4. Outputs

A successful run writes up to three files.

### 4a. Per-packet CSV (`--outPacketsCsv`)

One row per application-layer TX or RX event across all three flows.

```
time_s,nodeId,flowId,localIp,pktSize,seq,delay_ms,event
2.022456,1,8001,7.0.0.3,500,0,-1.0,tx
2.024064,0,8001,7.0.0.2,500,0,1.0,rx
...
```

| Column | Description |
|---|---|
| `time_s` | Simulation time of the event |
| `nodeId` | NS-3 node ID |
| `flowId` | UDP destination port (8001 / 8002 / 8003) |
| `localIp` | IPv4 address of this node |
| `pktSize` | Packet size in bytes (payload + SeqTsSizeHeader) |
| `seq` | Application-layer sequence number |
| `delay_ms` | End-to-end delay in ms for RX events; `-1` for TX events |
| `event` | `tx` or `rx` |

**Useful one-liners:**

```bash
# Per-flow event counts
awk -F, 'NR>1{print $3"_"$8}' packets.csv | sort | uniq -c

# Average delay per flow (RX events only)
awk -F, 'NR>1 && $8=="rx" {sum[$3]+=$7; cnt[$3]++}
         END {for(f in sum) print f, sum[f]/cnt[f] " ms"}' packets.csv
```

### 4b. Run-summary CSV (`--outRunCsv`)

One header row + one data row, consumed by `aggregate_results.py`.

```
runId,seed,numUEs,mu,bwPRBs,mcs,simTime,warmup,harq,kpiDb
s1_nrx08_mu1_w50_mcs14_seed1,1,8,1,50,14,10,1,1,/results/s1_nrx08_mu1_w50_mcs14_seed1.db
```

The `kpiDb` column is the full path to the SQLite database.

### 4c. SQLite KPI database (`<outputDir><simTag>.db`)

Contains raw trace tables and computed KPI tables written by `V2xKpi::WriteKpis()`.

**Raw trace tables** (written during simulation):

| Table | Contents |
|---|---|
| `pktTxRx` | Application TX/RX events with seq numbers and IMSI |
| `pscchTxUeMac` | PSCCH (SCI-1A) scheduling decisions at MAC |
| `psschTxUeMac` | PSSCH grant parameters (RBs, symbols, MCS) |
| `pscchRxUePhy` | Received PSCCH decode results at PHY |
| `psschRxUePhy` | Received PSSCH decode results (SINR, BLER) |
| `rlcRx` | RLC-layer PDU reception with end-to-end delay |

**Computed KPI tables** (written by `V2xKpi::WriteKpis()` after simulation):

| Table | Primary metric | Notes |
|---|---|---|
| `avrgPir` | Average PIR (ms) per TX→RX link | Range-gated to 200 m |
| `avrgPrr` | Average PRR (0–1) per transmitter | Range-gated to 200 m |
| `thputKbps` | Throughput (kb/s) per TX→RX link | Normalised to TX app duration |
| `simultPsschTx` | Collision stats | Total, non-overlapping, overlapping PSSCH TX |
| `psschTbCorruption` | TB decode | PSSCH and SCI-2A success counts |

**Query examples (sqlite3 CLI):**

```sql
-- Average PRR per transmitter
SELECT srcIp, avrgPrr FROM avrgPrr ORDER BY srcIp;

-- Average PIR across all TX→RX links
SELECT AVG(avrgPir) FROM avrgPir;

-- PSSCH SINR histogram
SELECT ROUND(sinrDb, 0) AS sinr_bin, COUNT(*) AS count
FROM psschRxUePhy
GROUP BY sinr_bin ORDER BY sinr_bin;

-- Per-flow packet counts from raw pktTxRx table
-- (flowId not stored directly; filter by dstPort using pktSize)
SELECT txRx, COUNT(*) FROM pktTxRx GROUP BY txRx;
```

---

## 5. Run the full PHY sweep

The sweep driver lives in `scratch/adaptive-phy-eval/scripts/run_sweep.py`.
It calls the optimised `groupcast_eval` binary for every
(scenario, condition, μ, W, MCS, seed) tuple.

### Quick test (one scenario, one config, one seed)

```bash
python3 scratch/adaptive-phy-eval/scripts/run_sweep.py \
  --scenarios s1 \
  --mu 1 \
  --w 50 \
  --mcs 14 \
  --seeds 1 \
  --parallel 1
```

Results land in `scratch/adaptive-phy-eval/results/raw/`.

### Full sweep (all scenarios, all configs, 5 seeds)

```bash
python3 scratch/adaptive-phy-eval/scripts/run_sweep.py \
  --scenarios all \
  --seeds 1,2,3,4,5 \
  --parallel 8
```

This launches 3 × 4 × 14 × 19 conditions × 5 seeds = **15,960 runs**.
Each run takes 2–10 s on an optimised build depending on simTime.

### Dry run (print commands without executing)

```bash
python3 scratch/adaptive-phy-eval/scripts/run_sweep.py \
  --scenarios s1 --dry-run
```

### Aggregate results after the sweep

```bash
python3 scratch/adaptive-phy-eval/scripts/aggregate_results.py
```

Reads all `*_run.csv` files from `results/raw/`, groups by
(scenario, condition, μ, W, MCS), and prints mean group PRR per config.

### Run the optimality analysis

```bash
python3 scratch/adaptive-phy-eval/scripts/find_optimals.py
```

Computes `PRR_static` (best config across all conditions) and
`PRR_oracle` (best config per condition) and reports the gap.

---

## 6. Run with SUMO mobility

SUMO scenarios are pre-generated in `scratch/adaptive-phy-eval/sumo/`.
Each scenario folder contains:

```
highway.sumocfg   ← root SUMO config (pass this to --sumoCfg)
highway.net.xml   ← road network
vehicles.rou.xml  ← vehicle departure schedule
```

Example (scenario S1, 8 UEs, single seed):

```bash
./build/scratch/groupcast_eval/ns3.42-groupcast_eval-optimized \
  --runId=s1_sumo_test \
  --numUEs=8 \
  --mu=1 --bwPRBs=50 --mcs=14 \
  --simTime=10 \
  --sumoCfg=scratch/adaptive-phy-eval/sumo/s1_group_size/s1_nrx08/highway.sumocfg \
  --sumoBinary=sumo \
  --outputDir=/tmp/results/ \
  --outRunCsv=/tmp/results/s1_sumo_test_run.csv
```

**Requirements for SUMO mode:**
- `sumo` executable must be in PATH (or pass full path via `--sumoBinary`)
- NS-3 must have been compiled with the `traci` module enabled (it is; check
  `Modules configured to be built` output of `./ns3 configure`)
- The TraCI port (default 3400) must be free

---

## 7. Reproducing the smoke test

The fastest sanity check — 4 UEs, 3 s, highway fallback:

```bash
mkdir -p /tmp/gce
./ns3 run "groupcast_eval \
  --runId=smoke \
  --seed=1 \
  --numUEs=4 \
  --mu=1 \
  --bwPRBs=50 \
  --mcs=14 \
  --simTime=3 \
  --warmup=1 \
  --outputDir=/tmp/gce/ \
  --outPacketsCsv=/tmp/gce/smoke_packets.csv \
  --outRunCsv=/tmp/gce/smoke_run.csv"
```

**Expected outputs:**

```
/tmp/gce/smoke.db              # ~960 KB SQLite database
/tmp/gce/smoke_packets.csv     # ~1500 rows, all three ports present
/tmp/gce/smoke_run.csv         # two rows (header + data)
```

**Verify all three flows are active:**

```bash
awk -F, 'NR>1{print $3"_"$8}' /tmp/gce/smoke_packets.csv | sort | uniq -c
```

Expected output (counts vary by seed/simTime):
```
 470 8001_rx
 600 8001_tx
  12 8002_rx
 240 8002_tx
 118 8003_rx
 120 8003_tx
```

If any port is missing, the corresponding SL bearer was not activated —
check that the RRI of that flow is present in `rriList` inside `nr_sl_stack.cc`.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `NS_FATAL: Unknown address type!` | Passing empty `Address()` to `UeToUePktTxRxOutputStats::Save` | Ensure `srcAddrs`/`dstAddrs` from the trace callback are forwarded, not replaced with `Address()` |
| `NS_FATAL: SUMO vehicle pool exhausted` | SUMO scenario has more vehicles than `--numUEs` | Increase `--numUEs` to match the vehicle count in `vehicles.rou.xml` |
| `Could not connect callback to …NrGnbPhy…` | Calling `nrHelper->EnableTraces()` without a gNB in the topology | Remove `EnableTraces()`; use manual `ConnectWithoutContext` for SL-only traces |
| Flow 2 (8002) has very low RX count | 1000 kb/s × 50 ms = 6250-byte packet; may exceed TBS for small W/MCS | Lower `dataRateKbps` for that flow or increase `bwPRBs`/`mcs` |
| Build error `non-const lvalue reference … initializer list` | C++ cannot bind a temporary `{...}` to a non-const `std::vector&` | Assign to a named variable first: `std::vector<std::bitset<1>> bm = {1,1,...}; factory->Set(bm);` |
| DB file is 0 bytes | `EmptyCache()` not called before `Simulator::Destroy()` | Call `Flush(hooks)` before `Simulator::Destroy()` |
