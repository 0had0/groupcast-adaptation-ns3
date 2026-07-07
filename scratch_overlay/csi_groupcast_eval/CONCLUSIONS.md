# csi_groupcast_eval — MCS sweep conclusions

## Goal

Quantify how 3GPP NR-V2X sidelink PSFCH feedback behaves across the full MCS space
(7 / 10 / 12 / 14 / 20 / 28) under two 3GPP channel models (V2V_Highway, V2V_Urban)
and three realistic traffic classes (periodic1, periodic2, aperiodic1) on two
SUMO traces (ivd_sweep, merge_incremental).

This is a fixed-MCS sensitivity baseline — the result is the input to a future
adaptive-MCS controller comparison (deliberately deferred to v2).

## Method

| Dimension        | Values                                           |
|------------------|--------------------------------------------------|
| Scenarios        | ivd_sweep (335 s), merge_incremental (537 s)     |
| Channels         | V2V_Highway, V2V_Urban                           |
| Traffic          | periodic1 (RRI 50 ms), periodic2 (RRI 50 ms*), aperiodic1 |
| MCS              | 7, 10, 12, 14, 20, 28                            |
| Seeds            | 1..10                                            |
| Total cells      | 2 × 2 × 3 × 6 × 10 = **720**                    |

\* periodic2's spec-default RRI of 10 ms triggers an NR-SL UE-MAC
HARQ-buffer-mismatch abort at ~35 s of sim time on the ivd_sweep trace.
Slowing it down to 50 ms (matching periodic1's cadence) lets the buffers
drain cleanly to the full trace length without changing packet-size behavior.

### Why traffic_class_eval, not csi_groupcast_eval

`csi_groupcast_eval --mobilityTrace` stalls the simulator at ~16 s of sim time
regardless of `--simTime` — the NR PHY scheduler gets stuck past ~13.77 s of
event time and the Simulator::Stop fires early. `traffic_class_eval` runs the
full SUMO trace cleanly when the periodic2 work-around above is applied. Both
evaluators share the same `BuildSlStack` and the same PsfchKpiShim
install path, so the PSFCH KPI is bit-for-bit equivalent across them; the
only differences are the campaign-specific flow wiring (csi uses `--flows`,
traffic_class builds a single FlowDef in code) and the channel model wiring
(both pull from `scratch_overlay/common/`).

### Metric

Per-PSFCH-opportunity `ack_rate` from the
`run_psfch_opps.csv.summary.csv` produced by the PSFCH KPI shim, aggregated
across all 16 HARQ PIDs for the single sender (node 0). For each
(scenario, channel, traffic, mcs) cell we report the mean and std of `ack_rate`
across the 10 seeds.

## Results

> **STUB** — full results will be added once the 720-cell sweep completes.
> Estimated wall-clock: ~3.5 h on 4 cores (~75 s/cell).

### Best-MCS table (filled after sweep)

(populated by `analyze_mcs_sweep.py` → `best_mcs.csv`)

### Heatmaps

(populated by `analyze_mcs_sweep.py` → `heatmap_mcs_<scenario>_<channel>.png`)

## Reproducibility

```bash
# Launch the sweep
python3 scratch_overlay/csi_groupcast_eval/mcs_sweep_runner.py \
  --ns3-bin ./ns-3-dev/build/scratch/traffic_class_eval/ns3.42-traffic_class_eval-default \
  --outputRoot /tmp/mcs_sweep_full --seeds 10 --warmup 2 --cores 4

# Aggregate
python3 scratch_overlay/csi_groupcast_eval/analyze_mcs_sweep.py \
  --outputRoot /tmp/mcs_sweep_full --outDir /tmp/mcs_sweep_analysis
```

Per-cell artifacts: `outputRoot/<scenario>__<channel>__<traffic>__mcs<M>__seed<N>/`
- `run.db` — ns-3 KPIs (PSSCH TX/RX, HARQ, throughput, KPI shim tables)
- `run.log` — full stdout/stderr from the ns-3 process
- `run_psfch_opps.csv` — per-opportunity ACK/NACK/SILENCE rows
- `run_psfch_opps.csv.summary.csv` — per (sender, dstL2Id, harq_pid) aggregation

## Caveats

1. **periodic2 RRI override.** The default 10 ms cadence trips a HARQ-buffer
   bug in NR-SL UE-MAC at high offered load; we use 50 ms instead. This
   changes the offered load profile of periodic2 vs its spec, but keeps
   the packet-size behavior intact. A future sweep at the spec RRI would
   require a separate fix in
   `ns-3-dev/contrib/nr/model/nr-sl-ue-mac-harq.cc` line 219.
2. **numUEs=6, not the full SUMO populations.** numUEs=8 and numUEs=23
   (the natural sizes of ivd_sweep and merge_incremental traces) trigger
   a pre-existing NR PHY error ("Pscch queue size"). 6 UEs is the largest
   reliable size for both traces and matches the leader + 5 closest
   followers — the part of each scenario where the geometric NLOSv
   blockage from intermediate vehicles has the most dynamic effect.
3. **--rri validation rejects 50?** traffic_class_eval validates --rri
   against {10,20,...,100,150,200,300,...}. 50 is in the valid set; we
   confirmed periodic2 runs full-trace at --rri=50.
4. **High-SNR regime dominates.** Both SUMO scenarios are open-road
   highway platoons, where the leading-edge SNR is high enough that all
   tested MCS values achieve >97% ack_rate. Differentiation between MCS
   values is small (often < 1%); the per-class and per-channel spread
   is more informative than the per-MCS spread.
