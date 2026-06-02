# traffic_class_eval — Per-RX KPI variation across traffic classes

Isolates the **TrafficClass axis** as the only varying factor on a single,
well-understood platoon geometry. Characterises per-RX behaviour across PRR,
PIR, throughput, resource usage, and HARQ statistics for the five
`CustomTrafficApplication` profiles defined in
[../csi_groupcast_eval/groupcast_flows.h](../csi_groupcast_eval/groupcast_flows.h):

| Class       | Inter-arrival            | Packet size                        | Approx. offered load |
|-------------|--------------------------|------------------------------------|----------------------|
| `periodic1` | 100 ms (CAM-like)        | cycles {300, 190, 190, 190, 190} B | ~17 kb/s             |
| `periodic2` | 10 ms                    | 800 B (80 %) or 1200 B (20 %)      | ~700 kb/s            |
| `periodic3` | 30 ms                    | uniform 30 – 60 kB                 | ~12 Mb/s             |
| `aperiodic1`| 50 ms + Exp(50 ms)       | uniform 200 – 2000 B               | ~88 kb/s             |
| `aperiodic2`| 10 ms + Exp(10 ms)       | uniform 10 – 30 kB                 | ~8 Mb/s              |

## Scenario

- **Mobility**: `experiment_ra_apc/traces/intersection_nlosv.mob.csv` —
  9 vehicles (node 0 = leader/TX, nodes 1..8 = followers/RX) on an L-shaped
  route, 120 s, ≈13.9 m/s.
- **Pathloss**: `V2V_Highway` with shadowing enabled.
- **NLOSv determination**: position-exact, via
  `ThreeGppGeometricNlosvModel` (Liang–Barsky AABB) wrapping the inner CCM.
  All 9 platoon vehicles act as mutual blockers.
- **PHY / MAC (fixed)**: MCS 14, bwPRBs 10, HARQ on,
  PSFCH period = 4 slots, `maxNumTx = 2`, numerology μ = 1 (30 kHz SCS).
- **Traffic**: single flow `tx_only` on UDP port 8001, only **node 0** transmits;
  all 9 UEs install a PacketSink so every RX is measured.
- **Sweep**: 5 traffic classes × 20 seeds = 100 runs, `simTime = 60 s`,
  `warmup = 2 s`.

## Files

| File                            | Purpose                                                                                  |
|---------------------------------|------------------------------------------------------------------------------------------|
| `traffic_class_eval.cc`         | Minimal ns-3 binary. Reuses all support files from `../csi_groupcast_eval/`.             |
| `CMakeLists.txt`                | Build descriptor; CMake `SOURCE_FILES` reference `../csi_groupcast_eval/*.cc` directly.  |
| `run_traffic_class_campaign.sh` | Parallel campaign runner (5 classes × N seeds).                                          |
| `extract_kpis.py`               | Reads every `*.db` → emits 4 tidy CSVs into `results_traffic_class_eval/tidy/`.          |
| `plot_traffic_class.py`         | Headless batch-render of all plots into `results_traffic_class_eval/plots/`.             |
| `evaluation.ipynb`              | Interactive notebook (8 sections): distributions, vs-distance, time-series, resource, HARQ, summary, throughput efficiency. |
| `README.md`                     | This file.                                                                               |

## Build & run

```bash
# 1. Build the binary
cd ns-3-dev
./ns3 build traffic_class_eval

# 2. Run the campaign (5 classes × 20 seeds, parallel)
cd ..
bash scratch_overlay/traffic_class_eval/run_traffic_class_campaign.sh

# Optional: smaller sweep for iteration
bash scratch_overlay/traffic_class_eval/run_traffic_class_campaign.sh --seeds 5 --simTime 30

# Optional: subset of classes
bash scratch_overlay/traffic_class_eval/run_traffic_class_campaign.sh \
    --classes periodic1,periodic3 --seeds 10

# 3. Extract tidy CSVs
python3 scratch_overlay/traffic_class_eval/extract_kpis.py

# 4a. Headless batch plots
python3 scratch_overlay/traffic_class_eval/plot_traffic_class.py

# 4b. Or open the notebook
jupyter notebook scratch_overlay/traffic_class_eval/evaluation.ipynb
```

## Output layout

```
results_traffic_class_eval/
├── periodic1/
│   ├── periodic1_seed1.db
│   ├── periodic1_seed1_run.csv
│   ├── periodic1_seed1.log
│   └── ...
├── periodic2/...
├── periodic3/...
├── aperiodic1/...
├── aperiodic2/...
├── all_runs.csv                       # concatenated per-run summary
├── tidy/
│   ├── per_rx_kpis.csv                # one row per (class, seed, rx_node)
│   ├── time_series.csv                # per-RX 1-s windowed KPIs
│   ├── resource_usage.csv             # one row per PSSCH grant
│   └── harq_stats.csv                 # one row per (class, seed)
└── plots/
    ├── per_rx_distributions.png
    ├── kpi_vs_distance.png
    ├── time_series_prr.png
    ├── time_series_heatmap.png
    ├── resource_usage.png
    └── harq_stats.png
```

## What each plot shows

1. **Per-RX distributions** — Box-plot of PRR / PIR / throughput, one box per
   `trafficClass`, populated by all (RX × seed) values. Reveals how each class
   spreads quality across receivers.
2. **vs distance** — Mean ± std of each KPI per RX node, plotted against the
   initial 2-D TX–RX distance. A monotonic PRR drop with distance for all
   classes confirms the NLOSv attenuation; classes differ in slope.
3. **Time-series PRR** — One subplot per `trafficClass`; one line per RX over
   1-s bins. Used to spot temporary outages aligned with the platoon's
   corner-turn around t ≈ 30 s.
4. **PRR heatmap** — `(rx_node × time)` heatmap per class. Hot/cold cells
   localise where and when each class struggles.
5. **Resource usage** — PRB-allocation histogram, total grants per run, and
   retransmission share (`ndi=0`). Large-packet classes saturate the pool.
6. **HARQ stats** — PSFCH NACK rate, MAC retx ratio, PHY decode-error rate,
   HARQ-process timeouts. Cross-checks PHY vs MAC failure modes.
7. **Summary table** — Notebook-only: ranks classes by `prr_mean`,
   `prr_p5`, `pir_p95_ms`, `tput_p5_kbps`, plus mean HARQ metrics.
8. **Throughput efficiency** — Mean RX kbps / Offered kbps. Values
   approaching 1 mean the link is keeping up with the application; values
   well below 1 indicate dropped/timed-out packets.

## Reusing support files (no duplication)

The CMake `SOURCE_FILES` block references the existing `csi_groupcast_eval`
implementation files via relative paths:

- `../csi_groupcast_eval/mobility.cc` — waypoint-CSV mobility
- `../csi_groupcast_eval/nr_sl_stack.cc` — NR/SL stack + `BuildSlStack()`
- `../csi_groupcast_eval/nr-sl-adaptive-mcs-scheduler.cc` — transitive dependency
- `../csi_groupcast_eval/geometric_channel.cc` — `ApplyGeometricNlosv()`
- `../csi_groupcast_eval/three-gpp-geometric-nlosv.cc` — Liang–Barsky CCM
- `../csi_groupcast_eval/groupcast_flows.cc` — `InstallGroupcastFlows()` + `FlowDef`
- `../csi_groupcast_eval/kpi_hooks.cc` — trace → SQLite wiring
- `../csi_groupcast_eval/v2x-kpi.cc` — V2xKpi (PIR / PRR / thput tables)
- `../custom-traffic/custom-traffic-{application,helper}.cc` — packet generators

No copies; touch `csi_groupcast_eval` and the next build of this scratch
target picks up the change.
