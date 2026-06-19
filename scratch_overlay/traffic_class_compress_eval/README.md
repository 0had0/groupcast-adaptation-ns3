# Adaptive Application-Layer Compression Experiment

## Motivation

The `traffic_class_eval` campaign (fixed MCS=14) shows that two large-payload classes
collapse in PRR for a non-link-quality reason:

| Class      | Payload | Fixed PRR | Root cause                              |
|------------|---------|-----------|------------------------------------------|
| aperiodic2 | 20 kB   | ~0.15     | TB cap ≈7 kB → SDU fragments into ~3 RLC segments |
| periodic3  | 45 kB   | ~0.00     | TB cap ≈7 kB → ~7 segments; (1−p)^N collapses PRR |

Neither MCS adaptation recovers these classes because the bottleneck is SDU
fragmentation, not per-TB BLER. This experiment tests:

**Hypothesis:** Adaptive application-layer compression (Zstd / LZ4) shrinks the SDU
below the TB cap, eliminates fragmentation, and recovers PRR exponentially.

---

## Directory layout

```
traffic_class_compress_eval/
├── CMakeLists.txt                    # links libzstd, liblz4 (MacPorts /opt/local)
├── traffic_class_compress_eval.cc    # simulation binary
├── compression_controller.{cc,h}    # per-flow adaptive decision tree
├── compression_tag.h                # ns-3 PacketTag (codec, sizes, latencies)
├── run_traffic_class_compress_campaign.sh
├── extract_kpis.py                   # writes 7 tidy CSVs
├── plot_traffic_class_compress.py    # standalone PNGs (compression-only)
├── plot_compress_vs_fixed.py         # comparison PNGs (vs fixed-MCS=14)
├── evaluation.ipynb                  # compression diagnostics
├── comparison.ipynb                  # fixed-MCS=14 vs compression
└── README.md

results_traffic_class_compress_eval/
├── adaptive/<class>/<class>_seed<N>.{db,log,_run.csv}
├── all_runs.csv
└── tidy/
    ├── per_rx_kpis.csv       (scheme, class, seed, rx_node_id × KPIs)
    ├── time_series.csv
    ├── resource_usage.csv
    ├── harq_stats.csv
    ├── compression_log.csv   (one row per packet event: codec, ratio, latencies)
    ├── rlc_frag.csv          (per-SDU N-segments before/after compression)
    └── pdb_violations.csv    (per-(class, seed, rx_node_id) PDB violation counts)
```

---

## Build

```bash
cd ns-3-dev
./ns3 build traffic_class_compress_eval
```

Prerequisites:
- MacPorts `libzstd` and `liblz4` at `/opt/local/lib` (detected automatically by CMake).

---

## Run

### Full campaign (100 runs: 5 classes × 20 seeds, adaptive mode)

```bash
cd scratch_overlay/traffic_class_compress_eval
bash run_traffic_class_compress_campaign.sh
```

### Ablation modes

```bash
COMP_MODES="none fixed_lz4 fixed_zstd1 fixed_zstd3 adaptive" \
bash run_traffic_class_compress_campaign.sh --modes none,adaptive
```

### Env-tunable thresholds

| Variable        | Default | Meaning                                   |
|-----------------|---------|-------------------------------------------|
| `COMP_NACK_THR` | `0.10`  | Escalate to ZSTD if NACK rate > threshold |
| `COMP_DIST_THR` | `80`    | Escalate if max RX distance > threshold   |
| `COMP_ENT_THR`  | `0.92`  | Skip compression if entropy > threshold   |
| `COMP_PDB_SLACK`| `2.0`   | Prefer LZ4 if (PDB − elapsed) < slack ms  |
| `COMP_PDB_MS`   | `0`     | Override per-class PDB (0 = class default)|

---

## Extract KPIs

```bash
python3 extract_kpis.py
```

Writes 7 tidy CSVs into `results_traffic_class_compress_eval/tidy/`.

---

## Analyse

```bash
# Standalone compression plots
python3 plot_traffic_class_compress.py

# Comparison vs fixed-MCS=14 baseline
python3 plot_compress_vs_fixed.py

# Interactive notebooks
jupyter notebook evaluation.ipynb   # compression diagnostics
jupyter notebook comparison.ipynb   # headline PRR / fragmentation / codec analysis
```

---

## Adaptive controller decision tree

```
N_est = ceil(origSize / TbSize)
entropy = Shannon(sample 512 bytes), normalised to [0,1]

if N_est <= 1:                                              → NONE
elif entropy > 0.92:                                        → NONE
elif (PDB − elapsed) < 2.0 ms:                             → LZ4
elif NACK_rate > 0.10 or max_dist > 80 m:                  → ZSTD_L3
else:                                                       → ZSTD_L1
```

---

## Expected results

| Class      | Fixed PRR | Compress PRR | Mechanism                              |
|------------|-----------|--------------|----------------------------------------|
| periodic1  | ~0.95     | ~0.95        | N=1 before compression → NONE selected|
| periodic2  | ~0.85     | ~0.85        | N=1 before compression → NONE selected|
| periodic3  | ~0.00     | ≥0.60        | N: 7→1 after ~5× compression          |
| aperiodic1 | ~0.85     | ~0.85        | N=1 before compression → NONE selected|
| aperiodic2 | ~0.15     | ≥0.70        | N: 3→1 after ~3× compression          |

If PRR gains do not materialise for aperiodic2/periodic3, check `§F` of
`comparison.ipynb` — `N-seg post` should be ≤ 1. If it is still ≥ 3, the codec
achieved a lower ratio than the deterministic payload synthesis targeted.

---

## Relationship to other experiments

| Experiment                    | MCS      | Compression | Purpose                            |
|-------------------------------|----------|-------------|-------------------------------------|
| `traffic_class_eval`          | fixed=14 | none        | Baseline / fragmentation diagnosis  |
| `traffic_class_adaptive_eval` | adaptive | none        | MCS adaptation (NACK-rate / raAPC)  |
| **`traffic_class_compress_eval`** | fixed=14 | **adaptive** | Fragmentation cure via compression |
