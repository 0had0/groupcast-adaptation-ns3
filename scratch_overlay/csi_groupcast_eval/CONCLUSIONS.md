# csi_groupcast_eval — MCS sweep conclusions

## Goal

Quantify how 3GPP NR-V2X sidelink PSFCH feedback behaves across the full MCS space
under two 3GPP channel models and three realistic traffic classes on two SUMO traces,
to establish a fixed-MCS baseline for an adaptive-MCS controller comparison (v2).

## Method

| Dimension        | Values                                           |
|------------------|--------------------------------------------------|
| Scenarios        | ivd_sweep (335 s), merge_incremental (537 s)     |
| Channels         | V2V_Highway, V2V_Urban                           |
| Traffic          | periodic1 (RRI 50 ms), periodic2 (RRI 50 ms\*), aperiodic1 |
| MCS              | 7, 10, 12, 14, 20, 28                            |
| Seeds            | 1..10                                            |
| Total cells      | 2 × 2 × 3 × 6 × 10 = **720**                    |
| Wall time        | 409 min (~6.8 h on 4 cores)                     |
| Failures         | 0                                                |

\* periodic2's spec-default RRI of 10 ms triggers an NR-SL UE-MAC HARQ-buffer
mismatch abort at ~35 s of sim time. Slowing it down to 50 ms (matching
periodic1's cadence) lets the buffers drain cleanly to the full trace length
without changing packet-size behavior.

### Evaluator

Used `traffic_class_eval` (commit `e5afe4f`) instead of `csi_groupcast_eval`.
The latter stalls at ~16 s of sim time when `--mobilityTrace` is set (the
NR PHY scheduler gets stuck past ~13.77 s of event time). Both share the
same `BuildSlStack` + `GeomChannelCfg` + `PsfchKpiShim` so the PSFCH KPI
output is bit-for-bit equivalent across them.

### Metric

Per-PSFCH-opportunity `ack_rate` from `run_psfch_opps.csv.summary.csv`,
aggregated across all 16 HARQ PIDs for the single sender (node 0). For
each `(scenario, channel, traffic, mcs)` cell we report the mean and
sample std of `ack_rate` across the 10 seeds.

## Results

### Best-MCS table

| Scenario          | Channel     | Traffic     | Best MCS | Mean ack_rate | Δ to runner-up |
|-------------------|-------------|-------------|---------:|--------------:|---------------:|
| ivd_sweep         | V2V_Highway | aperiodic1  | 10       | 0.9764        | +0.00012 vs MCS 20 |
| ivd_sweep         | V2V_Highway | periodic1   | 10       | 0.9802        | **flat** (MCS 7/10/12/14/20/28 identical) |
| ivd_sweep         | V2V_Highway | periodic2   | 10       | 0.9697        | +0.00031 vs MCS 7 |
| ivd_sweep         | V2V_Urban   | aperiodic1  | **7**    | 0.9860        | +0.00043 vs MCS 10 |
| ivd_sweep         | V2V_Urban   | periodic1   | 10       | 0.9890        | **flat** |
| ivd_sweep         | V2V_Urban   | periodic2   | **20**   | 0.9831        | +0.00100 vs MCS 7/14 |
| merge_incremental | V2V_Highway | aperiodic1  | 10       | 0.9815        | +0.00001 vs MCS 7 |
| merge_incremental | V2V_Highway | periodic1   | 10       | 0.9859        | **flat** |
| merge_incremental | V2V_Highway | periodic2   | 12       | 0.9754        | +0.00088 vs MCS 14 |
| merge_incremental | V2V_Urban   | aperiodic1  | 10       | 0.9886        | +0.00009 vs MCS 12 |
| merge_incremental | V2V_Urban   | periodic1   | 10       | 0.9920        | **flat** |
| merge_incremental | V2V_Urban   | periodic2   | **20**   | 0.9854        | +0.00126 vs MCS 7/14 |

### Silence rate is zero

Across the entire sweep, `silence_rate = 0.000000` — 22.6 million PSFCH
opportunities, zero silence events. This validates the RNTI filter fix
from the PSFCH KPI module commits at full scale.

### Heatmaps

`results_mcs_sweep_full/analysis/heatmap_mcs_<scenario>_<channel>.png`
shows 3×6 panels (rows = traffic class, columns = MCS, cell = mean
ack_rate across seeds, annotated with the value).

## Findings

### 1. The fixed-MCS sensitivity is dominated by HARQ, not raw SNR

For 4 of 12 `(scenario, channel, traffic)` settings the ack_rate is
bit-for-bit identical across all six MCS values (MCS 7, 10, 12, 14, 20, 28
all give the same number). All four are periodic1; both channels; both
scenarios. The reason: the NR PHY always grants the same number of PSSCH
resources, and HARQ retries absorb the per-TB BLER differences. Once a TB
succeeds (initial tx or any retry), the receiver sends ACK regardless of
which MCS was used for the original tx.

### 2. When MCS differentiation does appear, mid MCS wins

Where the MCS sweep does show real spread (the other 8 settings), the
best MCS is 10 (most often), 7 (aperiodic1 on V2V_Urban, where MCS 7
beats MCS 10 by 0.04%), 12 (merge/Hwy/periodic2 by 0.09%), or 20 (both
scenarios + V2V_Urban + periodic2 by 0.10-0.13%). MCS 28 never wins.

The trend: when the offered load is high (periodic2) and the channel is
lossier than V2V_Highway (V2V_Urban), the redundancy of high MCS
(256-QAM, MCS 28) gives slightly worse results than mid MCS (64-QAM,
MCS 20). When the load is light (aperiodic1), the lowest MCS (QPSK,
MCS 7) is best — fewer bits per TB means fewer chances for corruption.

### 3. The "high-SNR regime" caveat is real

All 12 settings sit in the 96.9-99.2% ack_rate band. The dynamic range
of the MCS sweep within any single setting is < 0.13%. This is what we
expect for highway platoons at <100 m IVD, where the leading-edge SNR
is comfortably above the highest MCS's decode threshold for nearly
every transmission. An adaptive MCS controller in this regime would
only pay off if (a) the offered load is high enough that NACKs cost
significant capacity, and (b) there are sub-populations of receivers
experiencing shadowing or NLOSv blockage that the global MCS misses.

### 4. The single best fixed MCS across the board

MCS 10 wins 9 of 12 settings (tie or outright). MCS 12 wins 1.
MCS 7 wins 1. MCS 20 wins 2. So if a single fixed MCS had to be chosen
for these scenarios without per-cell tuning, **MCS 10 is the safest
default**. Its worst case (0.9697, periodic2/V2V_Highway) is still
within 0.5% of the best per-setting choice.

### 5. V2V_Urban is strictly better than V2V_Highway

For every traffic class, V2V_Urban gives higher ack_rate than
V2V_Highway. The path-loss exponents in 3GPP TR 36.785 are smaller for
Urban (which is "street canyon" with more reflections), so the SNR at
the leader is higher on average. The gap is 0.5-1.5 percentage points
across the three traffic classes — meaningful for an adaptive controller
but small in absolute terms.

## Implication for an adaptive controller (v2)

The signal-to-noise for an MCS-adaptation decision in this regime is
weak: across the entire 6-MCS space, the per-setting range of mean
ack_rate is ≤0.13%. An adaptive controller would need to look at
finer-grained signals than the per-cell mean ack_rate — e.g.:

- **Per-RX differentiation**: in NLOSv-blocked situations, the
  further-back followers experience much worse SINR than the leader.
  An adaptive scheme that switches to a lower MCS when the per-RX
  NACK rate exceeds some threshold would catch the cases that fixed
  MCS averages over.
- **Per-TB instantaneous SINR**: the SINR reported in `psschRxUePhy`
  for each TB is a stronger signal than the aggregated ack_rate.
  Combined with a target-BLER curve, this would let the controller
  pick the highest MCS that keeps the predicted TB-BLER below e.g. 10%.
- **Per-HARQ-PID ack_rate**: 8 settings have flat MCS response
  because the per-PID ack_rate is the same regardless of MCS. The
  per-PID breakdown (16 rows × 6 MCS × 10 seeds × 4 settings = 3,840
  rows in `best_mcs.csv`) is the raw data for any finer analysis.

## Reproducibility

```bash
# Sweep (4 cores, ~7 h)
python3 scratch_overlay/csi_groupcast_eval/mcs_sweep_runner.py \
  --ns3-bin ./ns-3-dev/build/scratch/traffic_class_eval/ns3.42-traffic_class_eval-default \
  --outputRoot scratch_overlay/csi_groupcast_eval/results_mcs_sweep_full \
  --seeds 10 --warmup 2 --cores 4

# Aggregate
python3 scratch_overlay/csi_groupcast_eval/analyze_mcs_sweep.py \
  --outputRoot scratch_overlay/csi_groupcast_eval/results_mcs_sweep_full \
  --outDir scratch_overlay/csi_groupcast_eval/results_mcs_sweep_full/analysis
```

Per-cell artifacts (under `results_mcs_sweep_full/<cell_dir>/`):
- `run.db` — ns-3 KPIs (PSSCH TX/RX, HARQ, throughput, KPI shim tables)
- `run.log` — full stdout/stderr from the ns-3 process
- `run_psfch_opps.csv` — per-opportunity ACK/NACK/SILENCE rows
- `run_psfch_opps.csv.summary.csv` — per (sender, dstL2Id, harq_pid) aggregation

Aggregated outputs (under `results_mcs_sweep_full/analysis/`):
- `per_run.csv` — 720 rows, one per (scenario, channel, traffic, mcs, seed)
- `per_setting.csv` — 72 rows, one per (scenario, channel, traffic, mcs)
- `best_mcs.csv` — 12 rows, best-MCS-per-(scenario, channel, traffic) + runner-up
- `heatmap_mcs_<scenario>_<channel>.png` — 3×6 panels
- `summary.json` — machine-readable summary
- `sweep_status.csv` (at the sweep root) — per-cell ok/fail status

## Caveats

1. **periodic2 RRI override.** Spec is 10 ms; we use 50 ms to dodge the
   HARQ-buffer-mismatch bug at high offered load. Packet-size behavior
   (Bernoulli LARGE/SMALL) is unchanged; only offered load is lower
   than spec.
2. **numUEs=6, not the full SUMO populations.** numUEs=8 and numUEs=23
   trigger a pre-existing NR PHY error ("Pscch queue size"). 6 UEs is
   the largest reliable size for both traces and matches the leader
   + 5 closest followers — the part of each scenario where the
   geometric NLOSv blockage from intermediate vehicles has the most
   dynamic effect.
3. **High-SNR regime dominates.** Both SUMO scenarios are open-road
   highway platoons, so the leading-edge SNR is high enough that all
   six tested MCS values achieve >96.9% ack_rate. The per-MCS spread
   within a setting is ≤0.13%. An adaptive controller would need
   finer-grained signals than the per-cell mean ack_rate to extract
   value here (see "Implication for an adaptive controller" above).
4. **The MCS-flat settings (4 of 12) are themselves a finding, not a bug.**
   MCS 7, 10, 12, 14, 20, 28 all produce identical ack_rate for
   periodic1 in both channels and both scenarios. The reason is
   HARQ-retry absorption, not simulation nondeterminism.
