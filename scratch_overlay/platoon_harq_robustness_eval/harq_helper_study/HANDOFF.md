# HANDOFF: HARQ x Kuo-helper study (2026-10-21)

## What this folder is
A self-contained study comparing, on the platoon simulator (`../platoon_harq_robustness_eval.cc`):
- leader only: R1 blind, R2 HARQ Option 1, R3 HARQ Option 2;
- Kuo helper (H2), no takeover: R4 blind (Kuo's scheme), R4.1 Option 1, R4.2 Option 2;
- Kuo helper + takeover: R5 Option 1, R6 Option 2 (the helper NACK lets the leader retake a handed slot; leader fallback, G08);
- HARQ-IR ablation: R2 / R3 / R4.1 / R4.2 `_noIR` (`--softCombining=false`).

Cells: {highway, roundabout} ivd 30 m x {periodic1tb, aperiodic1tb} x background {none, moderate (60 cars, 1.67 flows)}.

## Files
| file | role |
|---|---|
| `study.py` | run planner and parallel, resumable runner; `run` / `check` / `list` actions; profiles `smoke` (12 x 8 s), `local1` (seed 1, 96 runs, background cells 20 s), `full` (`--seeds`, highway 53 s, roundabout whole trace) |
| `build_notebook.py` | builds and executes `STUDY_RESULTS_<profile>.ipynb` |
| `plot_spectrum_prr.py` | stacked spectrum per packet by component plus PRR (right axis); writes `figures/spectrum_prr_{prb,subch}_<profile>.png`; also run inside the notebook |
| `launch_full.sh` | remote launcher: optional `--pull BRANCH`, `setup.sh --force`, build, smoke test (aborts on failure), then the full profile under nohup and the notebook |
| `out/` | outputs (git-ignored) |

Resuming is command-aware: a run is skipped when its `_run.csv` exists and its `.cmd.json` equals the current command.

## Notebook contents
- PRR vs distance from the leader: the main arms, IR on vs off, and PRR within the PDB.
- Summary per cell and arm: PRR mean and worst, tx/pkt, reserved/pkt, platoon CBR, PSFCH per TB.
- CBR, transmitted and reserved savings against the blind counterpart (leader arms vs R1, helper arms vs R4).
- Spectrum by component: leader initial, leader retx, takeover after a helper NACK, fallback takeover, helper relay, helper feedback, members feedback. Shown in PRB x symbols and in subchannel-slots, with PRR on the right axis.
- HARQ-IR gain: arm minus arm_noIR.
- Takeover: helper NACKs, reclaims, fallback check / heard / tx / no slot, PRR recovered per mechanism, R5 - R4.1 and R6 - R4.2.

## Simulator changes made for this study (all flag-gated; regression-checked byte-identical when off)
| flag (platoon sim) | NR / controller | effect |
|---|---|---|
| `--reAckDecoded` (default true, Option 2 only) | `NrSpectrumPhy::SlAckPrevDecodedTb` | a receiver ACKs retransmissions of a TB it already decoded (F25); clears the flag on an SCI-2A failure (F31) |
| `--psfchExactAttribution` (true) | `NackOnlyHarqController::SetExactPsfchAttribution` | PSFCH bound to its own occasion; stale helper NACK no reclaim (F30) |
| `--blindSoftCombining` (false) | `NrSpectrumPhy::SlBlindSoftCombining` | blind copies decoded independently (Kuo's model; 3GPP allows combining) |
| `--softCombining` (true) | `NrSpectrumPhy::SlSoftCombining` | false = no HARQ-IR at all (ablation) |
| `--opt1HelperNack` (true) | `SetOpt1HelperNack` | R5: helper NACK on a dedicated resource, leader reclaim (non-3GPP: Option 1 shares one NACK resource) |
| `--helperTakeover` (true); `--retx=R4.1/R4.2` sets false | `SetHelperTakeover` | R4.1/R4.2: the helper is a plain member, no reclaim, no fallback |
| `--leaderFallback` (true, R5/R6) | `SetLeaderFallback`, MAC `GetSlNextHelperSlot` | after a NACK decision the leader listens in the helper's handed slot; no helper SCI decoded there means it takes the next handed slot |
| `--traffic=periodic1fx/periodic2fx/aperiodic1fx` | `CustomTrafficApplication::FixedSize` | fixed-size leader classes (periodic2fx needs `--t2Ms=9`, see tests F32) |
| (always) | `res_used_helper` | counts any member's copies (F01) |

New `_run.csv` columns: `reack_decoded, psfch_exact_attr, fb_late, helper_nack_stale, blind_soft_combining, opt1_helper_nack, leader_fixed_size, helper_takeover, leader_fallback, soft_combining, fallback_check, fallback_heard, fallback_tx, fallback_no_slot`. `_ltx.csv` gains `origin` (0 regular, 1 takeover after a helper NACK, 2 fallback).
`patches/nr/local.patch` = `git -C ns-3-dev/contrib/nr diff` (in sync at commit time).

## Validation
`../tests/` holds the sanity suite: 38 tests, `run_tests.sh`, `TEST_PLAN.md` (§0.1-§0.4 hold the decisions and findings), and `TEST_RESULTS.ipynb` (not committed). Open simulator-side findings are:
- F23: sub-slot PDB overshoot (periodic2 without `--t2Ms`);
- F32: periodic2 retransmission slots after the next arrival on the same SPS HARQ id;
- F33: the feedback arms get fewer reserved slots in a PDB-limited window;
- F34 and DV1: rare R5/R6 feedback edge cases;
- DV11: C_resel keep cap;
- SPS counter mean about 3-5 % low (unresolved).

## State at handoff
- `local1` complete: 96/96 runs, smoke invariants pass; `STUDY_RESULTS_local1.ipynb` built (not committed). One seed, background cells 20 s: indicative only.
- Remote: old campaign (commit 282dfc6, `run_retx.py`) StageA at 91.5 %. Plan: let StageA finish, stop before StageB (B/C would run on pre-fix code), then run this study:
  ```
  cd ~/Documents/groupcast-adaptation-ns3
  scratch_overlay/platoon_harq_robustness_eval/harq_helper_study/launch_full.sh --pull coop-arms --jobs <cores> --seeds 1-10
  tail -f scratch_overlay/platoon_harq_robustness_eval/harq_helper_study/out/full_run.log
  ```
  Highway no-background traces exist only for seeds 1-5 locally (`traces/hwy_ivd30_n0_s{1..5}`). Seeds 6-10 of that cell report a missing trace unless they are generated with `gen_mobility.py --scenario highway --ivd 30 --nbg 0 --seed N` (needs SUMO/traci).
- StageA results (old code): its helper arms use H3 (map), not Kuo. It predates F25, F30, F31, F01, blind no-combining and the R5 fallback, and its periodic2tb cells are unusable.

## Next steps
1. Remote full run, then the notebook `STUDY_RESULTS_full.ipynb`; copy back `out/full/*_run.csv`, `_member.csv` and the notebook.
2. Optional: fix F33 (window too short for 3 HARQ transmissions) and F23 properly (remaining PDB measured from packet generation).

## 2026-10-22: roundabout = curve only
`full` profile roundabout runs use a per-seed KPI window from the trace (`study.curve_window`): first sample where any leader-member link bends (leader enters the ring) to the last one (tail leaves the curve), +/- 1 s; `--kpiStart` and `--maxTime` set accordingly (e.g. n60 s1: 29.2-58.5 s). Straight approach/exit excluded. Restart on the remote: `launch_full.sh --stop`, then `launch_full.sh --no-smoke --seeds ... --jobs ...` (highway runs are skipped as done; roundabout runs re-run since their command changed).

## 2026-10-22: scenario roundaboutE (N -> E exit, 3/4 of the ring)
`gen_mobility.py --rbt-exit E` (default W unchanged, regenerated W trace is byte-identical). Traces `traces/rbtE_ivd30_n{0,60}_s{1..10}.csv.gz` are generated locally and are git-ignored; launch_full.sh generates the missing ones (needs SUMO) like `gen_mobility.py --scenario roundabout --ivd 30 --nbg N --seed S --rbt-exit E --out traces/rbtE_ivd30_nN_sS.csv.gz`. `full` profile now has highway, roundabout, roundaboutE (240 runs each at 5 seeds); tag prefix `rbtE`, simulator `--scenario=roundabout`, same curve-only KPI window. Check a trace: `render_rbt_trace.py rbtE_ivd30_n60_s1`.
