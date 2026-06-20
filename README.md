# groupcast-adaptation-ns3

NR-Sidelink V2X groupcast PHY adaptation research platform built on NS-3 / NR-V2X.

Experiments cover: adaptive MCS scheduling, relay-assisted groupcast, redundancy vs. robustness trade-offs, traffic class adaptation, compression, and range-aware power control.  All simulations share a reusable C++ library (`scratch_common`) and a Python KPI post-processor.

---

## Quick start (fresh clone)

```bash
git clone <this-repo-url> groupcast-adaptation-ns3
cd groupcast-adaptation-ns3

./setup.sh          # clone submodules, apply patches, unpack extras, create symlinks
./build.sh          # ns3 configure + build all scratch targets
source env.sh       # activate SUMO/NetAnim PATH + Python venv (every shell session)
```

`setup.sh` is idempotent — safe to re-run.  Pass `--force` to discard any uncaptured edits in submodules.

---

## Repository layout

```
groupcast-adaptation-ns3/
│
├── setup.sh                        # one-time source-tree assembly
├── build.sh                        # ns3 configure + build
├── env.sh                          # source to activate SUMO + Python venv
│
├── scratch_overlay/                # NS-3 scratch programs (tracked here, symlinked into ns-3-dev/scratch/)
│   ├── common/                     # shared C++ library (scratch_common) + Python tools
│   │   ├── channel/                # ThreeGppGeometricNlosvModel (geometry-aware NLOSv)
│   │   ├── flows/                  # groupcast bearer + app installation helpers
│   │   ├── kpi/                    # KPI hooks (SQLite logging), V2xKpi engine, kpi_postprocess.py
│   │   ├── mobility/               # SUMO/TraCI, CSV waypoint, and static-platoon mobility
│   │   ├── relay/                  # BoundaryRelayApp + FixedRelayApp
│   │   ├── stack/                  # NR+SL protocol stack builder, adaptive/deterministic schedulers
│   │   └── traffic/                # CustomTrafficApplication (CBR/bursty/Poisson/video-trace)
│   │
│   ├── eval_template/              # copy-paste starting point for new simulations
│   ├── groupcast_eval/             # baseline groupcast (MCS sweep, SUMO/static)
│   ├── platoon_baseline_eval/      # platoon baseline with MCS sweep campaigns
│   ├── relay_groupcast_eval/       # relay-assisted groupcast study
│   ├── static_relay_collision_eval/# static relay with collision attribution
│   ├── traffic_class_eval/         # multi-flow traffic class study
│   ├── traffic_class_adaptive_eval/# traffic class + adaptive MCS
│   ├── traffic_class_compress_eval/# traffic class + header compression
│   ├── traffic_class_raapc_eval/   # range-aware adaptive power control
│   ├── csi_groupcast_eval/         # CSI-based groupcast
│   ├── nlosv_model_eval/           # NLOSv channel model validation
│   ├── sl_bler_sweep/              # SL block-error-rate sweep
│   └── adaptive-phy-eval/          # (legacy) sweep + analysis scripts
│
├── experiment_*/                   # per-experiment results, run scripts, analysis notebooks
│   ├── experiment_adaptive_mcs/
│   ├── experiment_dual_mcs_groupcast/
│   ├── experiment_groupcast_mcs_sweep/
│   ├── experiment_ra_apc/
│   ├── experiment_redundancy_vs_robustness/
│   ├── experiment_relay_groupcast/
│   ├── experiment_static_relay_collection/
│   └── experiment_static_relay_collision/
│
├── scripts/
│   ├── new-simulation.sh           # scaffold a new scratch_overlay simulation from eval_template
│   ├── regen-patches.sh            # regenerate patches/ from submodule working trees
│   └── hooks/pre-commit            # auto-regens patches on every parent-repo commit
│
├── patches/                        # local modifications to upstream submodules
│   ├── netanim/local.patch
│   ├── ns-3-dev/local.patch
│   ├── ns-3-dev/extras/            # tarballs for whole untracked dirs (traci, vehicle-visualizer)
│   └── nr/local.patch
│
├── ns-3-dev/                       # submodule: gitlab.com/cttc-lena/ns-3-dev (branch v2x-lte-dev)
│   └── contrib/nr/                 # submodule: gitlab.com/cttc-lena/nr        (branch nr-v2x-dev)
├── netanim/                        # submodule: gitlab.com/nsnam/netanim
└── sumo/                           # submodule: github.com/eclipse-sumo/sumo
```

`ns-3-dev/scratch/<sim>` entries are **symlinks** into `scratch_overlay/` created by `setup.sh`.  Edit source in `scratch_overlay/`; NS-3 picks them up automatically.

---

## Shared library (`scratch_overlay/common/`)

All simulations link against the `scratch_common` static library.  See [`scratch_overlay/common/README.md`](scratch_overlay/common/README.md) for full API details.

| Module | Purpose |
|--------|---------|
| `channel/` | `ThreeGppGeometricNlosvModel` — geometry-aware NLOSv using Liang-Barsky segment test against vehicle bounding boxes |
| `mobility/` | `InstallMobility()` — SUMO/TraCI (live), CSV FCD trace, or static platoon modes |
| `stack/` | `BuildSlStack()` — NrHelper + NrSlHelper + SL bearer config; adaptive MCS and deterministic schedulers |
| `flows/` | `InstallGroupcastFlows()` — SL-DRB activation + OnOff/CustomTraffic app installation |
| `kpi/` | `KpiHooks` + `V2xKpi` — SQLite logging of all PHY/MAC/APP events; `kpi_postprocess.py` Python post-processor |
| `relay/` | `BoundaryRelayApp`, `FixedRelayApp` — L2 decode-and-forward relay |
| `traffic/` | `CustomTrafficApplication` — CBR / bursty / Poisson / video-trace sources |

---

## KPI pipeline

### 1. C++ logging (`kpi_hooks.cc`)

`KpiHooks` hooks ns-3 trace sources and writes raw events to a **SQLite DB** alongside every simulation run.  Tables written:

| Table | Content |
|-------|---------|
| `pktTxRx` | Per-packet TX/RX events: time, node, IP, port, sequence, size |
| `psschTxUeMac` | PSSCH MAC grant per transmission: RNTI, slot, RB, subchannel |
| `psschRxUePhy` | PSSCH PHY decode attempt: corrupt flag, SINR |
| `pscchTxUeMac` / `pscchRxUePhy` | PSCCH control grant / decode |
| `slHarqFeedback` | HARQ ACK/NACK per transport block |
| `rlcRx` | RLC PDU delivery: IMSI, RNTI, delay |
| `nodeInfo` | UE identity snapshot at sim end: nodeId, IMSI, IP, x/y/z, SEED, RUN |

### 2. Python post-processing (`kpi_postprocess.py`)

```bash
python3 scratch_overlay/common/kpi/kpi_postprocess.py \
    --db path/to/sim.db \
    --out results/kpi.csv \
    --warmup 2.0 \       # skip warm-up period (default 2 s)
    --window 1.0 \       # sliding-window width (default 1 s, suited to μ=1)
    --step   0.1 \       # window step size (default 0.1 s)
    --seed 1 --run 1
```

Produces a tidy CSV with one row per `(rx_node, tx_node, flow)`:

| Metric group | Columns |
|-------------|---------|
| Packet counts | `n_tx`, `n_rx` |
| PRR | `prr_mean`, `prr_p5`, `prr_p95` |
| PIR (ms) | `pir_mean_ms`, `pir_p5_ms`, `pir_p95_ms` |
| Throughput (kbps) | `thput_mean_kbps`, `thput_p5_kbps`, `thput_p95_kbps` |
| AoI (ms) | `aoi_mean_ms`, `aoi_peak_ms` |
| HARQ | `harq_ack`, `harq_nack`, `harq_nack_rate` |
| Collisions | `hd_count`, `rc_count`, `ce_count`, `total_corrupt` |
| Channel occupancy | `ch_occupancy` |

The post-processor **bulk-loads** each table once into Python memory — no per-link SQL queries — and completes a 300 MB / 2M-row DB in ~20 seconds.

### 3. Tests

```bash
cd scratch_overlay/common/kpi

# Unit tests only (~30 ms, no DB needed):
python3 -m unittest tests.test_kpi_helpers

# Unit + real-DB invariant tests (~20 s):
KPITEST_REAL=1 python3 -m unittest tests.test_kpi_helpers
```

35 tests total: 26 unit (percentile, PIR, AoI, PRR, half-duplex, collision attribution, occupancy) + 9 real-DB invariants (auto-detects first `.db` under `experiment_*/`).

---

## Running a simulation

```bash
cd ns-3-dev

# Single run:
./ns3 run "platoon_baseline_eval --numUEs=8 --simTime=12 --mu=1 --mcs=14 --seed=1"

# Post-process:
python3 ../scratch_overlay/common/kpi/kpi_postprocess.py \
    --db results/platoon_baseline_eval_seed1.db \
    --out results/kpi.csv
```

### Adding a new simulation

```bash
./scripts/new-simulation.sh my_new_eval
```

Copies `eval_template/` to `scratch_overlay/my_new_eval/`, renames files, and creates the `ns-3-dev/scratch/my_new_eval` symlink.  The template `.cc` is a fully annotated composition recipe.

---

## Modifying upstream submodule code

1. Edit files inside the submodule (e.g. `ns-3-dev/contrib/nr/model/...`).
2. Commit in the **parent** repo — the pre-commit hook runs `regen-patches.sh` automatically and stages updated patch files.
3. Never commit inside the submodule itself.

To manually regenerate patches:
```bash
scripts/regen-patches.sh
```

To verify patches are current (CI / pre-push guard):
```bash
scripts/regen-patches.sh --check
```

---

## Experiments

| Directory | Study |
|-----------|-------|
| `experiment_adaptive_mcs/` | NACK-driven adaptive MCS scheduling vs. fixed MCS; MCS sweep campaigns |
| `experiment_dual_mcs_groupcast/` | Dual-MCS groupcast (near + far zone split) |
| `experiment_groupcast_mcs_sweep/` | PRR vs. distance for a range of fixed MCS values |
| `experiment_ra_apc/` | Range-aware adaptive power control with NLOSv channel |
| `experiment_redundancy_vs_robustness/` | Redundancy (HARQ/retx) vs. robustness (MCS back-off) trade-off |
| `experiment_relay_groupcast/` | Single relay-assisted groupcast with boundary relay election |
| `experiment_static_relay_collection/` | Static relay with IVD / occupancy sweeps |
| `experiment_static_relay_collision/` | Static relay collision attribution (HD / RC / CE) |

Each experiment directory contains run scripts (`.sh`), analysis notebooks (`.ipynb`), Python plotting scripts, and `results_*/` output.

---

## Dependencies

| Tool | Version | Notes |
|------|---------|-------|
| NS-3 | `v2x-lte-dev` branch | Managed as submodule |
| NR module | `nr-v2x-dev` branch | Managed as submodule |
| SUMO | ≥1.18 | Managed as submodule; built by `build.sh` |
| NetAnim | latest | Managed as submodule |
| Python | ≥3.10 | `uv` venv; stdlib only for KPI post-processor |
| CMake | ≥3.16 | Required by NS-3 build system |
| C++ | C++17 | Required by NR module |
