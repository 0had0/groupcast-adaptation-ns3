# traffic_class_adaptive_eval

NACK-rate Adaptive-MCS counterpart to `../traffic_class_eval/`.

This subdirectory runs the **same** 5-class × 20-seed sweep on the
`intersection_nlosv` platoon trace, but the TX node 0 is now driven by an
`NrSlAdaptiveMcsController` (sliding-window NACK-rate algorithm) instead of
the fixed MCS=14 baseline.  The deliverable is a side-by-side comparison
notebook showing where adaptation helps and where it doesn't.

## Layout

```
traffic_class_adaptive_eval/
├── traffic_class_adaptive_eval.cc              ← C++ binary (same setup as
│                                                  traffic_class_eval.cc + adaptive wiring)
├── CMakeLists.txt                              ← reuses csi_groupcast_eval support
├── run_traffic_class_adaptive_campaign.sh      ← 5 classes × 20 seeds, parallel
├── extract_kpis.py                             ← 5 tidy CSVs (4 + new adapt_log.csv)
├── plot_traffic_class_adaptive.py              ← PNG plots: 5 baseline + mcs_timeline + adapt_decisions
├── plot_fixed_vs_adaptive.py                   ← Side-by-side PNGs into plots/comparison/
├── evaluation.ipynb                            ← Standalone adaptive-only notebook (+§6b)
├── comparison.ipynb                            ← Fixed vs Adaptive notebook
└── README.md
```

## Quick start

```bash
# 1. Build (the C++ support files come from csi_groupcast_eval/)
cd ns-3-dev
./ns3 build traffic_class_adaptive_eval

# 2. Run campaign (~30 min on M1 with 6 parallel jobs)
cd ..
bash scratch_overlay/traffic_class_adaptive_eval/run_traffic_class_adaptive_campaign.sh

# 3. Extract tidy CSVs (5 files including adapt_log.csv)
/Users/hadih/.pyenv/shims/python3 \
    scratch_overlay/traffic_class_adaptive_eval/extract_kpis.py

# 4. Render PNGs
/Users/hadih/.pyenv/shims/python3 \
    scratch_overlay/traffic_class_adaptive_eval/plot_traffic_class_adaptive.py
/Users/hadih/.pyenv/shims/python3 \
    scratch_overlay/traffic_class_adaptive_eval/plot_fixed_vs_adaptive.py

# 5. Open notebooks (use the pyenv python kernel)
#   scratch_overlay/traffic_class_adaptive_eval/evaluation.ipynb
#   scratch_overlay/traffic_class_adaptive_eval/comparison.ipynb
```

The comparison notebook requires both campaigns to have been run — it reads
`../traffic_class_eval/results_traffic_class_eval/tidy/` for the fixed
baseline.

## Adaptive controller (NACK-rate mode)

| CLI flag           | Default     | Meaning                                              |
|--------------------|-------------|------------------------------------------------------|
| `--adaptiveMcs`    | `true`      | Master toggle (set `false` to behave like fixed)     |
| `--adaptMode`      | `nackRate`  | Algorithm: `nackRate \| raApcA \| raApcB`            |
| `--adaptWinMs`     | `500`       | Sliding window length (ms)                           |
| `--adaptEvalMs`    | `100`       | Controller evaluation tick (ms)                      |
| `--adaptDownThr`   | `0.15`      | NACK rate above which MCS steps **down**             |
| `--adaptUpThr`     | `0.08`      | NACK rate below which MCS may step **up**            |
| `--adaptHyst`      | `2`         | Up-hysteresis (consecutive evals before stepping up) |
| `--adaptInitMcs`   | `14`        | Starting MCS (matches fixed baseline)                |
| `--adaptMcsMin`    | `0`         | Bottom of MCS ladder                                 |
| `--adaptMcsMax`    | `28`        | Top of MCS ladder                                    |
| `--adaptPerFlow`   | `true`      | Per-flow (LCID) adaptation instead of per-destination|
| `--adaptMcsLadder` | (empty)     | Comma-separated override, e.g. `28,24,14,4,0`        |

Defaults match `csi_groupcast_eval` baselines.  All flags are also
overridable from the campaign script via env vars (`ADAPT_WIN_MS=300 bash
run_traffic_class_adaptive_campaign.sh`).

## Tidy CSVs

`extract_kpis.py` produces five files in `results_traffic_class_adaptive_eval/tidy/`:

| File                  | Rows expected            | Key columns                                                    |
|-----------------------|--------------------------|----------------------------------------------------------------|
| `per_rx_kpis.csv`     | 5 × 20 × 8 = 800         | `prr`, `pir_ms`, `tput_kbps`, `init_dist_m`, `n_rx_pkts`       |
| `time_series.csv`     | ~38 k                    | 1-s windowed per-RX `prr_window`, `pir_window_ms`              |
| `resource_usage.csv`  | ~500 k                   | One row per PSSCH grant: `rbLen`, `ndi`, `harqId`              |
| `harq_stats.csv`      | 5 × 20 = 100             | `nack_rate`, `retx_ratio`, `phy_err_rate`, `timeout_count`     |
| `adapt_log.csv`       | ~120 k (varies by class) | `t_s`, `new_mcs`, `decision`, `nack_rate`, `ack_count`, …      |

The new `adapt_log.csv` is unique to the adaptive campaign — one row per
controller evaluation that produced a logged decision.

## Expected findings

For the same MCS=14 fixed baseline that achieved PRR=0% on Periodic3 and ~15%
on Aperiodic2 in `traffic_class_eval`, the NACK-rate adaptive controller
should:

- **Drop MCS sustainedly** on Periodic3 and Aperiodic2 (visible in
  `mcs_timeline.png` and §6b of the notebook).  Whether that recovers PRR
  depends on whether the lower MCS reduces packet-segment count (NO — the TB
  size grows but app packets are 10–60 kB, still way larger than even an
  MCS=0 TB) or just improves per-segment BLER (YES — lower MCS = more robust).
  The headline finding will be in `cmp_per_class_violins.png`.
- **Stay near MCS=14** on Periodic1 / Aperiodic1, which were already healthy.
  Any large excursions would mean the controller is overshooting on
  short-term NACK spikes — visible as wide MCS distributions in §6b.
- **Higher MAC retx_ratio** on hard classes (proof the lower MCS unlocked
  retransmission attempts that fixed MCS gave up on).
