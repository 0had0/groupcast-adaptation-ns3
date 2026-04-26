# Adaptive PHY Configuration Feasibility Evaluation

Does the optimal (μ, W, MCS) for NR Sidelink Groupcast shift under varying conditions?

## Objective

Phase 0 feasibility study: determine whether dynamic PHY reconfiguration is worth pursuing.

**Research Question:**
> Does the optimal PHY configuration (μ, W, MCS) for a groupcast link change when the environment changes (group size, inter-vehicle distance, speed, propagation)?

## Proof Structure

```
PRR_static  = max_{config} min_{condition} GroupPRR(config, condition)
PRR_oracle  = mean_{condition} max_{config} GroupPRR(config, condition)

If (PRR_oracle - PRR_static) > threshold → dynamic reconfiguration justified
```

## Scenario Families

| Scenario | Variable | Values | Fixed |
|---|---|---|---|
| S1: Group Size | N_rx | {2, 4, 8, 12, 16, 20} | speed=100km/h, IVD=20m |
| S2: Channel Heterogeneity | IVD | {10, 20, 50, 100, 150} m | N_rx=8, speed=100km/h |
| S3: Speed/Doppler | Speed | {30, 60, 100, 140, 200} km/h | N_rx=8, IVD=20m |
| S4: Propagation | Environment | {highway_los, urban_nlos, mixed} | N_rx=8, speed=60km/h, IVD=20m |

## PHY Sweep Space

- **Numerology (μ):** {0, 1, 2} → SCS {15, 30, 60} kHz
- **Bandwidth (W):** {25, 50, 75, 100} PRBs
- **MCS:** {0, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27}

Total configs: 3 × 4 × 14 = **168 sweep points per condition**

## Directory Structure

```
adaptive-phy-eval/
├── README.md
├── sumo/
│   ├── generate_scenarios.py      # Generates ALL SUMO configs
│   ├── validate_scenario.sh     # SUMO GUI validation
│   ├── s1_group_size/         # 6 sub-folders
│   ├── s2_channel_heterogeneity/ # 5 sub-folders
│   ├── s3_speed_doppler/     # 5 sub-folders
│   └── s4_propagation/       # 3 sub-folders
├── ns3/
│   ├── common/
│   │   ├── eval-config.h/.cc
│   │   ├── eval-metrics-collector.h/.cc
│   │   └── eval-csv-writer.h/.cc
│   ├── s1-group-size.cc
│   ├── s2-channel-heterogeneity.cc
│   ├── s3-speed-doppler.cc
│   └── s4-propagation.cc
├── scripts/
│   ├── run_sweep.py
│   ├── aggregate_results.py
│   └── find_optimals.py
└── results/
    ├── raw/       # One CSV per simulation run
    ├── summary/    # Aggregated per scenario
    └── logs/      # Simulation stdout/stderr
```

## Usage

### Phase 1: SUMO Validation

```bash
# Generate all scenarios
python3 sumo/generate_scenarios.py --all

# Validate in SUMO GUI
./sumo/validate_scenario.sh s1 nrx08
./sumo/validate_scenario.sh s2 ivd050m
# ... (repeat for all scenarios)
```

### Phase 2-3: Run Simulations

```bash
# Build
cd ns-3-dev && ./ns3 build adaptive-phy-eval

# Test single config
./ns3 run "adaptive-phy-eval --scenario=s1 --condition=nrx08 --numerology=1 --bandwidthPrbs=50 --mcsIndex=7 --seed=1"

# Run full sweep
python3 scripts/run_sweep.py --ns3-dir ns-3-dev --scenarios all --seeds 10 --parallel 4
```

### Phase 4: Analysis

```bash
# Aggregate results
python3 scripts/aggregate_results.py

# Find optimal configs and compute gap
python3 scripts/find_optimals.py
```

## Metrics

- **Primary:** GroupPRR = min(PRR_rx_1, ..., PRR_rx_N) — worst-case reliability
- **Secondary:**
  - Per-RX SINR (mean/min/max/std/5th percentile)
  - Per-RX BLER
  - Per-RX latency (mean/max/95th percentile)
  - Resource cost (PRBs per transmission)

## Expected Output

- 19 SUMO scenario sub-folders, each visually validated
- ~31,920 raw CSV files (168 configs × 19 conditions × 10 seeds)
- Final table showing whether optimal config shifts

## Key Insight

If gaps in final table consistently near zero → static config dominates → research direction is moot.
If gaps significant → dynamic reconfiguration has quantitative justification.

## Requirements

- NS3 with LENA NR module (cttc-nr)
- SUMO with TraCI
- Python 3 with numpy, pandas