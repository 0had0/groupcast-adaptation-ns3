# scratch_overlay/common/README.md

# `scratch_common` — Shared NR-V2X Simulation Modules

This directory is built as a **static library** (`scratch_common`) that every simulation in `scratch_overlay/` links against.  All headers are included with a root-on-include-path convention:

```cpp
#include "common/stack/nr_sl_stack.h"
#include "common/flows/groupcast_flows.h"
#include "common/kpi/kpi_hooks.h"
```

The include root (`scratch_overlay/`) is added as a PUBLIC include directory by `common/CMakeLists.txt`, so every consumer inherits it automatically.

---

## Module groups

### `channel/` — Geometry-aware NLOSv channel
| File | Purpose |
|------|---------|
| `three-gpp-geometric-nlosv.h/.cc` | `ThreeGppGeometricNlosvModel`: replaces the 3GPP probabilistic NLOSv condition with a Liang–Barsky segment test against all other vehicle bounding boxes. |
| `geometric_channel.h/.cc` | `ApplyGeometricNlosv()`: installs `ThreeGppGeometricNlosvModel` on every BWP CCM after `NrHelper::InitializeOperationBand()`. |

### `mobility/` — Node creation + SUMO/TraCI / waypoint / static platoon
| File | Purpose |
|------|---------|
| `mobility.h/.cc` | `MobConfig` struct + `InstallMobility(cfg)`. Three modes: SUMO/TraCI (live), CSV waypoint trace, or static platoon (`staticPlatoon=true`). Returns a `NodeContainer`. |

**Key fields of `MobConfig`:**
```cpp
uint32_t    numUEs{8};
std::string sumoCfg;           // path to .sumocfg (required for SUMO)
std::string mobilityTraceCsv;  // pre-recorded FCD trace (alternative to SUMO)
bool        staticPlatoon{false};
double      ivd{20.0};         // inter-vehicle distance (m) for static mode
```

### `stack/` — NR + NR-Sidelink protocol stack
| File | Purpose |
|------|---------|
| `nr_sl_stack.h/.cc` | `SlStackParams` + `BuildSlStack()`. Builds NrHelper + NrSlHelper + geometric NLOSv channel + IP stack. Returns `SlStackCtx`. |
| `nr-sl-adaptive-mcs-scheduler.h/.cc` | `NrSlAdaptiveMcsScheduler`: NACK-rate driven MCS adaptation. |
| `nr-sl-adaptive-mcs-controller.h` | Header-only controller logic (no .cc). |
| `nr-sl-deterministic-scheduler.h/.cc` | `NrSlDeterministicScheduler`: collision-free FDM/TDM lane scheduler for relay studies. |

**Key fields of `SlStackParams`:**
```cpp
uint32_t mu{1};            // numerology (0/1/2)
uint32_t bwPRBs{50};       // subchannel width in PRBs
uint32_t mcs{14};          // fixed MCS (used when adaptiveMcs=false)
bool     adaptiveMcs{true};// use NrSlAdaptiveMcsScheduler on TX node 0
std::string schedulerMode{"sbsps"}; // "sbsps" | "deterministic"
```

### `flows/` — Groupcast bearer + app installation
| File | Purpose |
|------|---------|
| `groupcast_flows.h/.cc` | `FlowDef`, `FlowResult`, `DefaultGroupcastFlows()`, `ParseFlowsSpec()`, `InstallGroupcastFlows()`. Activates SL-DRBs and installs OnOff/PacketSink (or CustomTraffic) apps on every node for each enabled flow. |

**Canonical call:**
```cpp
std::vector<FlowDef> flowDefs = flows.empty()
    ? DefaultGroupcastFlows()
    : ParseFlowsSpec(flows);
FlowResult res = InstallGroupcastFlows(ueNodes, stack, flowDefs,
                                       simTime, harq, stack.stream);
```

### `kpi/` — KPI hooks, SQLite output, V2xKpi engine
| File | Purpose |
|------|---------|
| `kpi_hooks.h/.cc` | `KpiHooks` struct, `ConnectPhyMacTraces()`, `ConnectAppTraces()`, `Flush()`. Owns the SQLite DB, CSV file, HARQ feedback logger, V2xKpi engine. Also writes the `nodeInfo` table (node positions, IMSI, IP) at `Flush()` time. |
| `v2x-kpi.h/.cc` | `V2xKpi`: computes PRR, PIR, throughput, collision stats from the DB tables. (Derived from `contrib/nr/examples/nr-v2x-examples/`.) |
| `kpi_postprocess.py` | **Canonical Python KPI post-processor.** Reads the SQLite DB, computes per-(UE, flow) KPIs in memory (no ns-3 required), and writes a tidy CSV. See [KPI Post-Processor](#kpi-post-processor-kpi_postprocesspy) below. |

**Canonical call sequence (C++):**
```cpp
KpiHooks hooks(dbPath, csvPath);
hooks.warmupTime = warmup;       // seconds before KPI measurement
ConnectPhyMacTraces(hooks);
ConnectAppTraces(hooks, flows);
// ... Simulator::Run() ...
Flush(hooks);                    // drains caches, writes V2xKpi + nodeInfo tables
```

#### Tables written to SQLite DB

| Table | Written by | Content |
|-------|-----------|---------|
| `pktTxRx` | `kpi_hooks.cc` | Per-packet TX/RX events: time, node, IP, port, seq, size |
| `rlcRx` | `kpi_hooks.cc` | RLC PDU delivery: IMSI, RNTI, delay |
| `psschTxUeMac` | `kpi_hooks.cc` | PSSCH grant per transmission: RNTI, slot, RB, subchannel |
| `psschRxUePhy` | `kpi_hooks.cc` | PSSCH reception per decode attempt: corrupt flag, SINR |
| `pscchTxUeMac` | `kpi_hooks.cc` | PSCCH control grant per transmission |
| `pscchRxUePhy` | `kpi_hooks.cc` | PSCCH reception decode attempt |
| `slHarqFeedback` | `kpi_hooks.cc` | HARQ ACK/NACK per transport block |
| `nodeInfo` | `kpi_hooks.cc` → `Flush()` | UE identity: `(nodeId, imsi, ip, x, y, z, SEED, RUN)` — written once at flush time from `NodeList` |

### `relay/` — L2 decode-and-forward relay apps
| File | Purpose |
|------|---------|
| `boundary_relay_app.h/.cc` | `BoundaryRelayApp`: relay elected by proximity to a configurable distance boundary (used by `relay_groupcast_eval`). `InstallRelayGroup()` helper. |
| `fixed_relay_app.h/.cc` | `FixedRelayApp`: relay pinned to a fixed node index (used by `static_relay_collision_eval`). |

### `traffic/` — Custom traffic applications
| File | Purpose |
|------|---------|
| `custom-traffic-application.h/.cc` | `CustomTrafficApplication`: CBR / bursty / Poisson / video-trace traffic sources. |
| `custom-traffic-helper.h/.cc` | `CustomTrafficHelper`: ns-3 helper for `CustomTrafficApplication`. |
| `test-custom-traffic.cc` | Smoke-test binary (target `scratch_common_test-custom-traffic`). |

---

## Adding a new simulation

```bash
./scripts/new-simulation.sh my_new_eval
```

This copies `scratch_overlay/eval_template/` to `scratch_overlay/my_new_eval/`, renames files, and creates `ns-3-dev/scratch/my_new_eval` symlink.

```bash
cd ns-3-dev
./ns3 build scratch/my_new_eval/my_new_eval
./ns3 run  scratch/my_new_eval/my_new_eval -- --numUEs=8 --simTime=10
```

The template `eval_template.cc` demonstrates the full composition recipe with comments at each step.

---

## KPI Post-Processor (`kpi_postprocess.py`)

`scratch_overlay/common/kpi/kpi_postprocess.py` is the canonical Python script for computing all V2X KPIs from a simulation SQLite DB.  It **bulk-loads** all relevant tables into Python memory (no per-link SQL queries), so it processes a typical 300 MB DB in ~20 seconds.

### Computed metrics (per `(rx_node, tx_node, flow)` row)

| Column | Description |
|--------|-------------|
| `n_tx` / `n_rx` | Total packets transmitted / received in measurement window |
| `prr_mean/p5/p95` | Packet Reception Ratio over sliding windows |
| `pir_mean/p5/p95_ms` | Packet Inter-Reception interval (ms) over sliding windows |
| `thput_mean/p5/p95_kbps` | Throughput (kbps) over sliding windows |
| `aoi_mean/peak_ms` | Time-average and peak Age-of-Information (ms) via sawtooth integral |
| `harq_ack` / `harq_nack` / `harq_nack_rate` | HARQ ACK/NACK counts and NACK rate per receiver |
| `hd_count` | Half-duplex slot overlaps (RX node was TX-ing on same slot as sender) |
| `rc_count` / `ce_count` | Corrupt frames attributed to resource collision / channel error |
| `total_corrupt` | Total corrupt PSSCH receptions for this (rx, tx) pair |
| `ch_occupancy` | Channel occupancy ratio for the TX node [0, 1] |

### CLI usage

```bash
python3 kpi_postprocess.py \
    --db path/to/sim.db \
    --out results/kpi.csv \
    --warmup 2.0 \          # skip first 2 s (default)
    --sim-end 12.0 \        # inferred from DB if omitted
    --window 1.0 \          # sliding-window width (s); 1.0 suited to μ=1
    --step 0.1 \            # window advance step (s)
    --seed 1 --run 1        # SQLite SEED/RUN discriminators
```

Optional flags: `--range <meters>` (spatial gate), `--seed`, `--run`.

### Identity resolution

The post-processor prefers the `nodeInfo` table (written by `Flush()` since the audited C++ update).  For older DBs lacking `nodeInfo` it falls back to reconstructing identity from `pktTxRx + rlcRx` (no positions, limited RNTI coverage — HARQ/occupancy/collision columns will be zero).

### Tests

```bash
# Unit tests only (no DB needed — ~30 ms):
python3 -m unittest tests.test_kpi_helpers

# Unit + real-DB invariant tests (needs a .db file — ~20 s):
KPITEST_REAL=1 python3 -m unittest tests.test_kpi_helpers
```

`KPITEST_REAL` auto-detects the first `.db` under `experiment_*/`.

---

## include-path convention

The `common/CMakeLists.txt` adds `scratch_overlay/` (the parent of `common/`) as a `PUBLIC` include directory on the `scratch_common` target. Every consumer therefore inherits it and can write:

```cpp
#include "common/channel/geometric_channel.h"
#include "common/stack/nr_sl_stack.h"
```

Do **not** use `../common/...` or bare `"geometric_channel.h"` — these fail when the ns-3 build system sets the working include root to `ns-3-dev/scratch/`.
