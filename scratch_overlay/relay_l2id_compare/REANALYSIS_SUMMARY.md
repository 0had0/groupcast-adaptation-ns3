# Re-analysis of relay_l2id_compare — Summary

## What was done
Re-analyzed the existing 2900 simulation DBs in `results_c4/` (2100 sims) + `results_gen/` (800 sims)
using the new registry-based `kpi/` pipeline, without re-running any ns-3 simulations.

## Steps
1. Added 3 new KPIs to the registry:
   - `storm` — hop-1 retx storm ratio (n_retx/n_new + leader-only retx count)
   - `psr` — packets successfully relayed (relayTiming.forwarded / leader gen)
   - `coll` — leader↔relay RB-overlap collision rate (the H4 metric)
2. Updated `join_per_run.py`:
   - Added `SCALAR_KPIS` map for storm/psr/coll
   - Fixed `SEED_TAG_RE` to handle `..._seedN_mcdNNN` suffix (gen corpus)
3. Fixed `run_kpi_pipeline.py`:
   - Updated `_SEED_RE` from `\b` to `(?:_|$)` so it matches `_seedN_mcdNNN`
4. Wrote `analyze_l2id_compare.py`:
   - Walks per-run dirs, `combine_run()` each, concats
   - Aggregates per (arm, speed, ivd, mcd, scenario, numUEs)
   - Joins baseline (none) values as `b_*` + computes `d_*` deltas
   - Storm indicator: relay/baseline ratio at hiMCD (mcd>=200)
   - Verdict: H1_no_storm, H2_tail_kept, H3_cheaper_than_sameL2
   - Verdict by regime: per (arm, scenario, numUEs)
5. Re-ran the pipeline on both corpora:
   - `results_c4` (2100 sims, 4.5 min, 8 workers)
   - `results_gen` (800 sims, 1.5 min, 8 workers)
6. Re-wrote `analysis_new.ipynb` to consume the new aggregate + verdict CSVs
7. Updated `CONCLUSIONS.md` with the new numbers

## Cross-validation vs legacy

| Metric                | Legacy (inline compute_kpis) | New (kpi/ pipeline) | Δ |
|-----------------------|-----------------------------:|--------------------:|--:|
| none prr_mean (avg)   | 0.91                         | 0.91                | ≈0 |
| fixed d_tail_PRR      | -0.015                       | +0.070              | +9 pp (improvement) |
| broadcast d_tail_PRR  | -0.033                       | +0.037              | +7 pp (improvement) |
| sameL2 d_tail_PRR     | -0.006                       | +0.086              | +9 pp (improvement) |
| sameL2 storm_ratio_hiMCD | 1.57                      | 1.77                | +0.20 (refined averaging) |
| fixed occ_ratio       | 1.52                         | 1.14                | -25% (more accurate: subch-slots vs TBs/ms) |

The PRR improvements are real — the new pipeline correctly attributes relay-delivered
packets via 3GPP-style S/Z union-dedup (`outcomes.py:_augment_relay_receptions_from_pkttxrx`).
The occ_ratio drop is a unit change, not a regression (subchannel-slot fraction is a
better resource measure than TBs/ms).

## Files added/modified

### Added
- `scratch_overlay/common/kpi/kpis/storm.py`
- `scratch_overlay/common/kpi/kpis/psr.py`
- `scratch_overlay/common/kpi/kpis/coll_rate.py`
- `scratch_overlay/common/kpi/analyze_l2id_compare.py`
- `scratch_overlay/common/kpi/tests/test_seed_pattern.py`
- `scratch_overlay/relay_l2id_compare/analysis_new.ipynb`
- `scratch_overlay/relay_l2id_compare/analysis_new_executed.ipynb`

### Modified
- `scratch_overlay/common/kpi/kpis/__init__.py` (register new KPIs)
- `scratch_overlay/common/kpi/join_per_run.py` (SCALAR_KPIS, SEED_TAG_RE)
- `run_kpi_pipeline.py` (_SEED_RE)
- `scratch_overlay/relay_l2id_compare/CONCLUSIONS.md` (rewrite)

### Results (overwritten in-place, not in git because of .gitignore)
- `results_c4/l2id_compare_aggregate.csv` (70 rows)
- `results_c4/l2id_compare_runs.csv` (26460 rows)
- `results_c4/l2id_compare_storm_indicator.csv` (70 rows)
- `results_c4/l2id_compare_verdict.csv` (8 rows)
- `results_c4/l2id_compare_verdict_by_regime.csv` (8 rows)
- `results_gen/l2id_compare_aggregate.csv` (80 rows)
- `results_gen/l2id_compare_runs.csv` (14240 rows)
- `results_gen/l2id_compare_storm_indicator.csv` (80 rows)
- `results_gen/l2id_compare_verdict.csv` (14 rows)
- `results_gen/l2id_compare_verdict_by_regime.csv` (32 rows)

## Tests
- 30 existing tests still pass
- 5 new test_seed_pattern tests pass
- Total: 35 unit tests passing
