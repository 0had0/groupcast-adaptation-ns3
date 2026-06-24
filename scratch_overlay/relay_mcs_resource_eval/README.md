# `relay_mcs_resource_eval` — NR-V2X platoon relay + per-hop MCS resource study

**Question:** *does a higher-than-worst-RX MCS at the leader + a single same-L2ID
relay consume fewer subchannel×slot resources than a single-hop baseline, at
equal-or-better PRR / PIR / AoI?*

## What this sim does

A moving platoon of 8 UEs (one leader + 7 followers, inter-vehicle distance =
`ivd` metres, platoon speed = `speed` m/s) runs NR-V2X sidelink groupcast.
Two modes:

* `--mode=baseline` — only the leader TXs, at a single global MCS
  (`--mcsBaseline`).
* `--mode=relay` — the leader TXs at `--mcsBaseline` (= mcsHop1) and a chosen
  follower (`--relayNode`) is wired up as a same-L2ID forwarder at a separate
  MCS (`--mcsHop2`). The MCD is `--mcd`.

The per-node MCS override is implemented in
`scratch_overlay/common/stack/nr_sl_stack.{h,cc}` as `nodeMcsOverride{}`:
empty by default, populated by this sim. The override replaces the default
scheduler on the listed TX nodes with a fresh `NrSlUeMacSchedulerFixedMcs`
+ AMC and is **backward-compatible** (the `eval_template` sim builds clean
with the override empty).

## Running

Sweep scripts live next to this README:

| script | role | runs |
|---|---|---|
| `run_phase1_mcs_sweep.sh`   | PRR(MCS, distance) surface per (speed, ivd) | 420 |
| `run_phase2_relay_sweep.sh` | per-relay-node resource frontier at chosen (mcs1, mcs2, MCD) | 350 |
| `run_phase2b_mcd_sweep.sh`  | MCD threshold curve at r\* | 140 |
| `run_phase3_compare.sh`     | paired baseline-vs-relay head-to-head | 140 |

All scripts take optional `[outputDir] [simTime] [seeds] [nParallel]` args;
default output dir is `./results_relay_mcs_resource_eval/` (gitignored).
Results land in `results_relay_mcs_resource_eval/phase{1,2,2b,3}/<tag>/`.

`aggregate.py` reads the per-tag `_kpi.csv` + `.db` and emits
`phase{1,2,2b,3}_*.csv` summary files plus a Wilcoxon-paired verdict.
`_build_notebook.py` materialises `analysis.ipynb`.

## Headline finding (see analysis.ipynb §6)

**Across all 7 (speed, ivd) pairs a same-L2ID relay at the chosen (r\*, mcs1,
mcs2, MCD\*) costs 1.15× – 2.53× more subchannel-slots than the optimal
single-hop baseline (Wilcoxon paired-by-seed, all p ≤ 0.01).** The relay
does not reliably improve worst-RX PRR (median diff –0.07 to +0.04, mostly
insignificant) and adds 3-15 ms e2e latency at the chosen r\*.

In other words: in this 8-UE moving-platoon setting with one fixed relay at
any node 2..6, the relay **fails** the H1 "resource win" hypothesis.

## Files

* `relay_mcs_resource_eval.cc` — the sim binary source (~325 lines).
* `aggregate.py` — phase 1/2/2b/3 CSV aggregation + Wilcoxon.
* `_build_notebook.py` — `analysis.ipynb` generator.
* `PROBLEM.md` — full problem framing (R-A..R-D + H1-H4).

The shared changes that make this sim possible:

* `scratch_overlay/common/kpi/kpi_postprocess.py` — adds `subch_slots_used /
  subch_slots_total / subch_slots_per_pkt` to `KpiRow`; populates
  `hop1_retx / hop2_retx` for relay mode.
* `scratch_overlay/common/stack/nr_sl_stack.{h,cc}` — adds
  `nodeMcsOverride{}` (no-op when empty).