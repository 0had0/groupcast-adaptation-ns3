# traffic_class_raapc_eval

SINR-based RA-APC-Groupcast counterpart to `../traffic_class_adaptive_eval/`.

Same 5-class × 20-seed sweep on the `intersection_nlosv` platoon trace, but
the TX node 0 is driven by an `NrSlAdaptiveMcsController` in **RA-APC mode**.
Instead of counting PSFCH NACKs, the controller measures **per-RX SINR** at
the PHY layer, computes each receiver's maximum feasible MCS from a SINR-BLER
LUT, picks a group-level MCS via Strategy A (min) or B (P5), then refines via
ETP = δ(m) × F(m).

The key advantage over NACK-rate: the controller sees a signal even when
decoders fail completely (no NACK is sent), resolving the "quiet channel vs
dead channel" ambiguity.

## Layout

```
traffic_class_raapc_eval/
├── traffic_class_raapc_eval.cc          ← C++ binary (adaptive eval + SINR sink)
├── CMakeLists.txt
├── run_traffic_class_raapc_campaign.sh  ← 5 classes × 20 seeds, parallel
├── extract_kpis.py                      ← same 5 tidy CSVs as adaptive eval
├── plot_traffic_class_raapc.py          ← adaptive-only PNGs + mcs_timeline
├── plot_fixed_vs_raapc.py               ← Fixed vs RA-APC comparison PNGs
└── README.md
```

## Quick start

```bash
# 1. Build
cd ns-3-dev && ./ns3 build traffic_class_raapc_eval

# 2. Run campaign (~30 min on M1 with 5 parallel jobs)
cd ..
bash scratch_overlay/traffic_class_raapc_eval/run_traffic_class_raapc_campaign.sh

# 3. Extract
/Users/hadih/.pyenv/shims/python3 \
    scratch_overlay/traffic_class_raapc_eval/extract_kpis.py

# 4. Render PNGs
/Users/hadih/.pyenv/shims/python3 \
    scratch_overlay/traffic_class_raapc_eval/plot_traffic_class_raapc.py
/Users/hadih/.pyenv/shims/python3 \
    scratch_overlay/traffic_class_raapc_eval/plot_fixed_vs_raapc.py
```

## RA-APC controller CLI flags

| CLI flag          | Default    | Meaning                                              |
|-------------------|------------|------------------------------------------------------|
| `--adaptiveMcs`   | `true`     | Master toggle                                        |
| `--adaptMode`     | `raApcA`   | `raApcA` (worst-case) \| `raApcB` (P5) \| `nackRate`|
| `--adaptWinMs`    | `500`      | SINR sliding-window length (ms)                      |
| `--adaptEvalMs`   | `100`      | Controller tick (ms)                                 |
| `--adaptInitMcs`  | `14`       | Starting MCS                                         |
| `--adaptMcsMin`   | `0`        | Bottom of ladder                                     |
| `--adaptMcsMax`   | `28`       | Top of ladder                                        |
| `--payloadBytes`  | `1500`     | Expected payload (bytes) — used in rbLen / ETP calc  |
| `--poolPrbs`      | `100`      | Total sidelink pool PRBs                             |
| `--blerSlope`     | `1.0`      | Sigmoid steepness around 10%-BLER SINR threshold     |

`payloadBytes` is set per-class in the campaign script:

| Class      | payloadBytes |
|------------|-------------|
| periodic1  | 200          |
| periodic2  | 1 000        |
| periodic3  | 45 000       |
| aperiodic1 | 1 000        |
| aperiodic2 | 20 000       |

## SINR sink wiring

`kpi_hooks.h` exposes `KpiHooks::sinrSink` (`PsschSinrCb`).  Setting it
before `ConnectPhyMacTraces()` causes every received TB's linear SINR to be
converted to dB and forwarded to `adaptCtrl->OnPsschRxSinr(rnti, sinrDb)`.
This measurement arrives **even for failed TBs** (PHY can measure SINR
regardless of CRC outcome), which is why RA-APC doesn't suffer the NACK
silence problem.

## Expected findings vs NACK-rate

The NACK-rate campaign showed mean_mcs ≈ 14 for all classes including
Periodic3 (PRR=0%) — because no NACKs arrived, the controller had no signal.

RA-APC will see actual SINR for Periodic3 and Aperiodic2.  If the SINR is
sufficient for MCS 14 (≥ 14 dB), the controller will pick MCS 14 anyway —
because the SINR says the link is fine.  The root problem for Periodic3 is
**packet size vs TB size**: at 10 PRBs × MCS 14, max TB ≈ 3 kB, but the
packet is 30–60 kB (15–20 RLC segments) — any segment loss kills the SDU.
No MCS choice fixes this without more bandwidth.

If `cmp_summary.csv` shows the same PRR=0% for Periodic3 under RA-APC, that
confirms the diagnosis: the bottleneck is RLC segmentation, not link quality.
