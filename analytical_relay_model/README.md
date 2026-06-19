# analytical_relay_model

Analytical (MATLAB) companion to the `relay_groupcast_eval` ns-3 study. Models the
NR-SL groupcast resource pool, link, multi-flow coexistence, PRR/PIR/throughput,
and relay selection/placement over the two platoon traces, calibrated to the
simulated PRR(d) curves. Produces a LaTeX report.

## Layout
- `matlab/` — model + driver
  - `params.m` — all pool/traffic/HARQ/channel parameters as variables
  - `load_trace.m`, `allpair_geom.m`, `count_blockers.m`, `pair_dn.m` — trace +
    pairwise distance / NLOSv blocker geometry
  - `prr_cal.m` — **active** calibrated link model `PRR_k = ½erfc((g−θ_k)/(√2 s_k))`,
    `g(d,n)=PLexp·log10(d)+Cblock·n`
  - `sinr_dB.m`, `bler_mcs.m`, `prr_link.m` — the physical SINR→BLER→HARQ chain
    (report §3) that `prr_cal` is the calibrated reduced form of
  - `calibrate.m` — fits the link model to `data/calib_*_prr_vs_dist.csv`
  - `relay_sweep.m` — single-relay placement sweep (strict-closest, union, within-PDB)
  - `collision_model.m` / `run_collision.m` — **SB-SPS resource-collision model**
    (pairwise TX↔relay + aggregate) vs distance/packet size/PDB/RRI/sensing
    window/reselection counter; see `COLLISION_MODEL.md` (figs 07–09)
  - `run_all.m` — **entry point**: calibrate → all figures → numeric summaries
- `figures/` — generated PNGs (fig01..fig06)
- `data/` — input traces, calibration targets (from the MCS sweep), and outputs
  (`calibrated_params.txt`, `resource_table.csv`, `relay_placement_summary.csv`)
- `report/relay_analytical_model.{tex,pdf}` — the report

## Run
```
cd matlab
/Applications/MATLAB_R2025b.app/bin/matlab -batch run_all
cd ../report && latexmk -pdf relay_analytical_model.tex
```

Calibration RMSE ≈ 0.030. Key result: a single mid-platoon relay at
x*≈20–40 m lifts merge GPRR by up to +0.16 (high MCS); the relay hop is best at a
robust MCS; strict-closest selection is near-optimal; chains saturate after the
2nd relay.
