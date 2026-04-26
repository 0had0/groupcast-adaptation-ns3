# groupcast_eval — Architecture & Developer Guide

## Purpose

`groupcast_eval` is an NS-3 scratch simulation that measures V2X KPIs (PRR,
PIR, throughput, BLER) for **NR Sidelink groupcast** communication under
varying PHY configurations (numerology, subchannel size, MCS).  
It is the computational core of the PHY sweep study defined in
`scratch/adaptive-phy-eval/`.

The key research question it answers per run:

> *For a given (μ, W, MCS) triple and a given vehicular scenario, what is the
> Packet Reception Ratio achieved by each of the three V2X traffic classes?*

---

## Reference examples

Every design decision in this simulation traces back to one of two canonical
NR-SL reference examples provided by the CTTC NR module:

| Reference file | What we borrow from it |
|---|---|
| `contrib/nr/examples/sl-examples/sl-multi-lc-example.cc` | Three concurrent flows on one groupcast destination — `SidelinkInfo`, `LteSlTft`, `OnOffHelper`, `PacketSinkHelper` |
| `contrib/nr/examples/nr-v2x-examples/nr-v2x-west-to-east-highway.cc` | NR/SL helper chain, resource pool/preconfig, trace-DB callbacks, `V2xKpi` |

---

## Module layout

```
scratch/groupcast_eval/
│
├── groupcast_eval.cc       ← main(): 7-step orchestrator, CLI parsing
│
├── mobility.h / .cc        ← Step 1 — node creation + mobility models
├── nr_sl_stack.h / .cc     ← Step 2 — full NR+SL protocol stack
├── groupcast_flows.h / .cc ← Step 3 — 3-flow bearer activation + apps
├── kpi_hooks.h / .cc       ← Steps 4 & 6 — trace callbacks, DB, V2xKpi
│
└── v2x-kpi.h / .cc         ← KPI engine (copied from contrib NR examples)
```

Each module has a single, clearly bounded responsibility. The dependencies
flow strictly downward: `groupcast_eval.cc` calls everything; nothing calls
back into `main()`.

---

## Step-by-step simulation flow

```
main()
 │
 ├─ 1. InstallMobility()     → creates N nodes; attaches ConstantVelocity
 │                              (highway) or TraCI/SUMO positions
 │
 ├─ 2. BuildSlStack()        → NrHelper, NrSlHelper, BWP, pool,
 │                              SidelinkPreconfigNr, IPv4 stack
 │
 ├─ 3. InstallGroupcastFlows() → for each of 3 flows:
 │                                • SidelinkInfo (priority, RRI, PDB, DG/SPS)
 │                                • LteSlTft (BIDIRECTIONAL, groupAddr, port)
 │                                • ActivateNrSlBearer()
 │                                • OnOffHelper (TX) on every UE
 │                                • PacketSinkHelper (RX) on every UE
 │
 ├─ 4. KpiHooks(dbBase, csvPath)  → opens SQLite DB, binds stats objects
 │    ConnectPhyMacTraces()       → hooks PSCCH/PSSCH MAC & PHY traces
 │    ConnectAppTraces()          → hooks TxWithSeqTsSize / RxWithSeqTsSize
 │
 ├─ 5. Simulator::Stop() / Run()
 │
 ├─ 6. Flush()               → EmptyCache() on all stats objects
 │                              V2xKpi::WriteKpis() → PIR/PRR/thput tables
 │
 └─ 7. Write run-summary CSV (one line consumed by aggregate_results.py)
```

---

## Module details

### `mobility.h / .cc` — Node creation & mobility

**Exported:** `InstallMobility(MobConfig)` → `NodeContainer`

**What it does:**  
Creates `numUEs` NS-3 nodes and installs one of two mobility models:

- **SUMO mode** (when `--sumoCfg` is non-empty and NS3_TRACI is defined):  
  Nodes are parked at off-map positions. `TraciClient::SumoSetup()` hands
  each NS-3 node to a SUMO vehicle as it departs; thereafter SUMO drives the
  position at each synchronisation step.

- **Highway fallback** (default):  
  `ConstantVelocityMobilityModel` places all UEs in a single lane, 20 m
  apart, all travelling at 100 km/h (27.78 m/s). Antenna height is 1.6 m
  per 3GPP TR 37.885 §6.1.2.

**To change the highway layout:** edit the constants at the top of
`mobility.cc`:
```cpp
const double interVehicleDist = 20.0;   // metres between UEs
const double speed            = 27.78;  // m/s (100 km/h)
const double antennaHeight    = 1.6;    // metres
```

---

### `nr_sl_stack.h / .cc` — NR + NR-Sidelink protocol stack

**Exported:** `BuildSlStack(SlStackParams, NodeContainer)` → `SlStackCtx`

**What it does (in order):**

1. **Band / BWP** — `CcBwpCreator::SimpleOperationBandConf` at 5.89 GHz
   (band n47), 20 MHz (200 × 100 kHz), `V2V_Highway` channel model.  
   The physical bandwidth is always 20 MHz; `bwPRBs` controls only the
   *subchannel size* inside the resource pool.

2. **NrHelper attributes**:
   - Antenna: 1 row × 2 columns, `IsotropicAntennaModel` (quasi-omni SISO)
   - TX power: 23 dBm
   - MAC: `NrSlUeMac`, `EnableSensing = false` (Mode-2 pre-configured)
   - Selection window: T1 = 2 slots, T2 = 33 slots
   - BWP manager: `NrSlBwpManagerUe` mapping `GBR_MC_PUSH_TO_TALK` → BWP 0

3. **UE device installation** + stream assignment seeded from `--seed`.

4. **NrSlHelper**: error model `NrEesmIrT1`, `NrSlUeMacSchedulerFixedMcs`
   at the MCS requested by `--mcs`.

5. **Resource pool** (`NrSlCommResourcePoolFactory`):
   - SL bitmap: `1|1|1|1|1|1|0|0|0|1|1|1` (9 of 12 subframes available)
   - T0 sensing window: 100 ms (unused since sensing is off)
   - T2min selection window: 5 slots
   - PSCCH: 10 RBs
   - Subchannel size: `bwPRBs`
   - Max reservations per SCI: 3
   - RRI list: {0, 20, 50, 100} ms — must contain the RRI of every flow

6. **SidelinkPreconfigNr**:
   - TDD pattern: `DL|DL|DL|F|UL|UL|UL|UL|UL|UL|`
   - `slMaxTxTransNumPssch = 5` (initial + up to 4 HARQ retransmissions)
   - `slProbResourceKeep = 0`

7. **IPv4 stack** with EPC default-gateway routes.

**Key struct fields:**
```cpp
struct SlStackCtx {
    Ptr<NrPointToPointEpcHelper> epc;
    Ptr<NrHelper>                nr;
    Ptr<NrSlHelper>              sl;
    NetDeviceContainer           ueNetDev;
    Ipv4InterfaceContainer       ueIpIface;
    int64_t                      stream;   // next free RNG stream index
};
```

**To change antenna configuration:** edit `SetUeAntennaAttribute` calls in
`nr_sl_stack.cc:54–60`.  
**To change error model:** replace `"ns3::NrEesmIrT1"` on line ~80.  
**To enable sensing:** set `EnableSensing` to `true` and adjust T0.

---

### `groupcast_flows.h / .cc` — Traffic flows

**Exported:** `kFlows[3]`, `InstallGroupcastFlows(...)` → `FlowResult`

#### The flow table — the single place to change traffic profiles

```cpp
// groupcast_flows.cc:16-20
const FlowDef kFlows[kNumFlows] = {
  // port  prio  rri           dynamic  pdb           rateKbps  label
  { 8001,  1,    20 ms,   true,   20 ms,   200.0,  "urllc_cam" },
  { 8002,  2,    50 ms,   false,  50 ms,   1000.0, "v2x_video" },
  { 8003,  3,    100 ms,  false,  100 ms,  100.0,  "non_gbr"   },
};
```

| Field | Meaning |
|---|---|
| `port` | UDP destination port; also used as the `flowId` column in the CSV |
| `priority` | SL logical-channel priority — 1 is highest; maps to PQI in the scheduler |
| `rri` | Resource Reservation Interval; must appear in `rriList` in `nr_sl_stack.cc` |
| `dynamic` | `true` = dynamic grant (DG); `false` = semi-persistent scheduling (SPS) |
| `pdb` | Packet Delay Budget passed to `SidelinkInfo::m_pdb` |
| `rateKbps` | OnOff application data rate |
| `label` | Logged to NS_LOG_INFO; not used in computation |

**What `InstallGroupcastFlows` does for each flow:**
1. Builds a `SidelinkInfo` from the flow descriptor.
2. Creates a `LteSlTft` with `Direction::BIDIRECTIONAL` (every UE both TX
   and RX — true groupcast).
3. Calls `nrSlHelper->ActivateNrSlBearer()` at t = 2.0 s.
4. Installs one `OnOffHelper` on **every** UE (all transmit simultaneously).
5. Installs one `PacketSinkHelper` on every UE (all receive simultaneously).
6. Staggers TX start times with up to 50 ms of uniform jitter to avoid a
   simultaneous first-packet burst.

Packet size is derived as: `pktSize = rateKbps × 1000 × rri_s / 8` bytes,
so exactly one packet per RRI saturates the bearer at the requested rate.

**To add a fourth flow:** add a fourth entry to `kFlows`, change
`kNumFlows` to `4`, and add the new RRI to `rriList` in `nr_sl_stack.cc`.

---

### `kpi_hooks.h / .cc` — Trace callbacks & KPI extraction

**Exported:** `KpiHooks` (struct), `ConnectPhyMacTraces()`,
`ConnectAppTraces()`, `Flush()`

#### Trace sources connected

| Trace path | Callback | DB table |
|---|---|---|
| `NrUeMac/SlPscchScheduling` | `OnSlPscchScheduling` | `pscchTxUeMac` |
| `NrUeMac/SlPsschScheduling` | `OnSlPsschScheduling` | `psschTxUeMac` |
| `NrUePhy/SpectrumPhy/RxPscchTraceUe` | `OnSlPscchRx` | `pscchRxUePhy` |
| `NrUePhy/SpectrumPhy/RxPsschTraceUe` | `OnSlPsschRx` | `psschRxUePhy` |
| `NrUeMac/RxRlcPduWithTxRnti` | `OnSlRlcPduRx` | `rlcRx` |
| App `TxWithSeqTsSize` | `OnUePacket` | `pktTxRx` + CSV |
| App `RxWithSeqTsSize` | `OnUePacket` | `pktTxRx` + CSV |

#### `KpiHooks` struct

```cpp
struct KpiHooks {
    SQLiteOutput             db;          // one .db file per run
    UeMacPscchTxOutputStats  pscchTxMac;
    UeMacPsschTxOutputStats  psschTxMac;
    UePhyPscchRxOutputStats  pscchRxPhy;
    UePhyPsschRxOutputStats  psschRxPhy;
    UeRlcRxOutputStats       rlcRx;
    UeToUePktTxRxOutputStats pktTxRx;
    PktCsv                   pktCsv;      // optional CSV
    V2xKpi                   v2xKpi;      // post-sim KPI engine
};
```

The constructor opens the SQLite DB and calls `SetDb()` on each stats object.
All objects live in `main()` so they outlast `Simulator::Run()`.

#### `Flush()` sequence

```
EmptyCache() × 6 stats objects   → writes in-memory rows to DB
FillPosPerIpMap() for every UE    → populates range gate for V2xKpi
V2xKpi::WriteKpis()               → reads DB tables, writes KPI tables
pktCsv.out.close()
```

**Important:** `EmptyCache()` must be called *before* `V2xKpi::WriteKpis()`.
If you skip it, the last batch of cached rows never lands in the DB and the
KPI calculation will be silently incomplete.

---

### `v2x-kpi.h / .cc` — KPI engine (read-only, upstream file)

Copied verbatim from
`contrib/nr/examples/nr-v2x-examples/v2x-kpi.{h,cc}`.  
**Do not edit this file.** If the upstream file is updated, replace the copy.

**KPIs written to the DB after the simulation:**

| KPI | DB table | Description |
|---|---|---|
| Average PIR | `avrgPir` | Mean packet inter-reception time per TX→RX link |
| Average PRR | `avrgPrr` | Packet reception ratio per transmitter over all neighbours within range |
| Throughput | `thputKbps` | Received bits / TX app duration per TX→RX link |
| PSSCH collision | `simultPsschTx` | Overlapping vs non-overlapping simultaneous PSSCH transmissions |
| TB decode | `psschTbCorruption` | PSSCH and SCI-2A decode success rates |

Range is set to 200 m (configurable via `SetRangeForV2xKpis()`).

---

## How to edit common scenarios

### Change the number of UEs

Pass `--numUEs=N` at runtime. No code change needed. Minimum is 2.

### Change the PHY sweep point

Pass `--mu=M --bwPRBs=W --mcs=MCS` at runtime. These three parameters are
the entire sweep axis; everything else is fixed at the 3GPP baseline.

### Change a traffic profile (rate, RRI, priority)

Edit `kFlows[]` in `groupcast_flows.cc`. If you change an RRI value, also
add it to `rriList` in `nr_sl_stack.cc` so the pool accepts it.

### Enable sensing-based resource selection (Mode-1 → Mode-2)

In `nr_sl_stack.cc`, set:
```cpp
ctx.nr->SetUeMacAttribute("EnableSensing", BooleanValue(true));
```
and set `slSensingWindow` to match T0 (e.g., 100 ms).

### Change the channel model

In `nr_sl_stack.cc`, replace `BandwidthPartInfo::V2V_Highway` with
`V2V_Urban` or any other `BandwidthPartInfo::Scenario` enum value, and
adjust `SetPathlossAttribute("ShadowingEnabled", ...)` accordingly.

### Enable per-run shadowing randomness

In `nr_sl_stack.cc`:
```cpp
ctx.nr->SetChannelConditionModelAttribute("UpdatePeriod",
                                           TimeValue(MilliSeconds(500)));
ctx.nr->SetPathlossAttribute("ShadowingEnabled", BooleanValue(true));
Config::SetDefault("ns3::ThreeGppChannelModel::UpdatePeriod",
                   TimeValue(MilliSeconds(500)));
```

---

## Dependency graph

```
groupcast_eval.cc
    ├── mobility.h
    ├── nr_sl_stack.h
    │       └── (ns3-libs: nr, lte, internet, antenna, stats)
    ├── groupcast_flows.h
    │       └── nr_sl_stack.h
    ├── kpi_hooks.h
    │       ├── groupcast_flows.h
    │       └── v2x-kpi.h
    └── (ns3-libs: core)
```

---

## Known limitations

- **Single lane, single group.** There is no multi-lane topology or multiple
  independent groupcast groups. All UEs share the same `dstL2Id = 254`.
- **No blocker vehicles.** The original code had blocker UEs for NLOSv
  channel mixing; these were removed. The V2V_Highway channel condition model
  still computes LOS/NLOSv/NLOS internally based on geometry.
- **Fixed HARQ max-TX = 5** across all flows when `--harq=true`. Per-flow
  HARQ tuning is not yet exposed as a CLI flag.
- **V2xKpi is not per-flow.** The `pktTxRx` table conflates all three flows.
  Filtering by destination port to get per-flow PRR requires a custom
  SQL query against the `.db` file.
