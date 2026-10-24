# HANDOFF: HARQ x Kuo-helper study (updated 2026-10-24)

## Status in one paragraph
Both campaigns are complete on the remote (`ease-rtx`, 32 cores) and copied to the laptop: the **full study**
(12 arms x 3 scenarios x 2 traffic x 2 background x 10 seeds = 1440 runs, all exit 0) and the **density sweep**
(R1 / R3 / R6 x 3 scenarios x 2 traffic x 15 / 30 / 45 cars x 10 seeds = 540 runs, all exit 0). Notebooks are
built from both. The results support a conference paper (story below); the open item before writing is the
background-load wording (the "moderate" level saturates the channel, see Caveats).

## What is compared
On the platoon simulator (`../platoon_harq_robustness_eval.cc`), 9 vehicles, IVD 30 m:
- leader only: R1 blind, R2 HARQ Option 1, R3 HARQ Option 2;
- Kuo helper (H2), no takeover: R4 blind (Kuo's scheme), R4.1 Option 1, R4.2 Option 2;
- Kuo helper + takeover: R5 Option 1, R6 Option 2. The helper is named per TB in SCI-2A and relays in the
  retransmission slots the leader hands over (S1). Under Option 2 the named helper never ACKs. Two takeovers:
  (1) helper NACK -> the leader reclaims the next handed slot (`ReclaimSlHelperSlot`, `_ltx.csv` origin 1);
  (2) fallback (G08): after a NACK decision with a silent helper, the leader listens in the helper's handed slot
  and, if it decodes no helper SCI, reclaims the following one (origin 2). Code: `../nack_only_harq.cc`.
- HARQ-IR ablation: R2 / R3 / R4.1 / R4.2 `_noIR` (`--softCombining=false`).

Scenarios (`study.SCEN`):
| study name | tag | trace | KPI window |
|---|---|---|---|
| highway | `hwy` | `hwy_ivd30_n*_s*` | 53 s from t = 3 s |
| roundabout | `rbt` | N in -> W exit (1/4 of the ring) | curve only |
| roundaboutE | `rbtE` | N in -> E exit (3/4 of the ring), `gen_mobility.py --rbt-exit E` | curve only |

Curve-only window (`study.curve_window`): from the first trace sample where any leader-member link bends
(straight distance < member x 35 m - 0.5 m, the leader enters the ring) to the last one (the tail leaves the
curve), +/- 1 s, passed as `--kpiStart` / `--maxTime` (maxTime includes the 0.3 s tail guard). Straight approach
and exit are excluded. Background: `none` = 0 cars, `moderate` = 60 cars x 1.67 flows; density sweep 15 / 30 / 45
cars at 1.67 flows (`study.DENSITY`).

Distance (`dist_m` in `_pkt.csv`, `mean_dist_m` in `_member.csv`) is the real transmitter-member distance from the
ns-3 mobility model fed by the SUMO trace, at a packet's first attempt; not index x IVD.

## Files
| file | role |
|---|---|
| `study.py` | planner + parallel resumable runner (`run` / `check` / `list`); profiles `smoke`, `local1`, `full`, `density`, `density_smoke`; `curve_window`, `SCEN`, `DENSITY` |
| `launch_full.sh` | remote launcher of the full profile: `--pull`, `--no-setup`, `--no-smoke`, `--stop`; generates missing `rbtE` traces |
| `launch_density.sh` | remote launcher of the density sweep: `--smoke` (end-to-end check, foreground), `--stop`, `--jobs` / `--gen-jobs` (default all cores); generates missing traces with per-trace logs in `out/traces_log/` and a serial retry; checks the background population reached |
| `monitor_study.sh` | `./monitor_study.sh density [--watch 60]` (or `full`): progress, failures, ETA, running sims, memory, disk |
| `build_notebook.py` | `STUDY_RESULTS_<profile>.ipynb`: PRR vs distance, summary, CBR savings, spectrum, latency / PIR, CDFs, AoI, HARQ-IR, takeover; 95 % CI over seeds everywhere (paired for savings / IR gain); one colour per arm, family = line style + marker |
| `build_requests_notebook.py` | `REQUESTS.ipynb`: the session analyses (distance definition, curve windows + trace renders, R1 hwy vs rbt, R1 vs R4, R1 vs R2 vs R3) with logistic fits `b + (a-b)/(1+exp((d-d50)/w))` |
| `build_density_notebook.py` | `DENSITY_RESULTS.ipynb`: merges `--full-dir` (0, 60 cars) and `--density-dir` (15 / 30 / 45); PRR, worst member, paired R6 gains, cost, CBR, takeover activity, PRR vs distance per density |
| `render_rbt_trace.py` | renders a roundabout trace over the SUMO net + buildings (snapshots PNG + GIF, KPI window in red) |
| `plot_prr_dist_arms.py`, `plot_r1_prr_dist.py`, `plot_spectrum_prr.py` | standalone figure scripts |
| `../gen_mobility.py` | traces; `--rbt-exit {W,S,E}`; roundabout platoon insertion retried when SUMO cannot insert every member (fix for the remote n15 s5 failure; first attempt unchanged, meta `platoon_insert_attempts`) |

Resuming is command-aware: a run is skipped when its `_run.csv` exists and its `.cmd.json` equals the command.

## Data (all git-ignored)
| where | what |
|---|---|
| remote `harq_helper_study/out/full/`, `out/density/` | the campaigns (complete) + `STUDY_RESULTS_full.ipynb`, `DENSITY_RESULTS.ipynb` built there |
| laptop `out/remote_full/` | full study: `cmd.json, _run, _member, _pkt, _grants, _ltx, _att, _hsel, _htx, _nack` (1.3 GB) |
| laptop `out/remote_density/` | density sweep: `cmd.json, _run, _member, _pkt` |
| laptop `out/local1/`, `out/full/` | early local runs (seed 1; `local1` roundabout = straight approach only, not the curve) |
| laptop notebooks | `STUDY_RESULTS_remote_full.ipynb`, `REQUESTS.ipynb`, `DENSITY_RESULTS.ipynb` (all from the remote data), `DENSITY_RESULTS_remote.ipynb` (built on the remote) |

Rebuild locally:
```
python3 build_notebook.py --profile remote_full
python3 build_requests_notebook.py --hwy-profile remote_full --rbt-profile remote_full --arms-hwy-profile remote_full --arms-rbt-profile remote_full
python3 build_density_notebook.py --full-dir out/remote_full --density-dir out/remote_density
```
Copy from the remote (`~/.ssh/config` does not include `ease-config`, hence `-F`):
```
rsync -av -e 'ssh -F ~/.ssh/ease-config' --prune-empty-dirs --include='*/' --include='*.cmd.json' --include='*_run.csv*' --include='*_member.csv*' --include='*_pkt.csv*' --exclude='*' 'ease-rtx:Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study/out/<full|density>/' out/remote_<full|density>/
```
Integrity checks done: every roundabout run has `--kpiStart`, no highway run has it; one flag set per arm;
one binary (`-optimized`); no run with 0 packets; 10 seeds in every cell.

## Results (10 seeds, mean +/- 95 % CI; moderate = 60 cars unless stated)
Group PRR:
| | R1 | R3 | R4 | R4.2 | R6 |
|---|---|---|---|---|---|
| highway periodic | 0.92 | 0.96 | 0.90 | 0.93 | 0.97 |
| highway aperiodic | 0.60 | 0.74 | 0.45 | 0.47 | 0.72 |
| roundabout W periodic | 0.58 | 0.64 | 0.68 | 0.72 | 0.75 |
| roundabout W aperiodic | 0.45 | 0.51 | 0.41 | 0.44 | 0.57 |
| roundabout E periodic | 0.66 | 0.71 | 0.74 | 0.79 | 0.83 |
| roundabout E aperiodic | 0.49 | 0.55 | 0.43 | 0.48 | 0.64 |

- R6 best or tied in every cell (tie: highway aperiodic, R3 0.74 vs R6 0.72).
- Blind relaying (R4) hurts under aperiodic traffic (highway 0.45 vs R1 0.60, already 0.88 vs 0.98 at 35 m)
  and helps on the curve (0.68 vs 0.58). Likely collisions of the helper's copies; not yet confirmed from the
  collision counters.
- Takeover gain R6 - R4.2: +0.25 highway aperiodic, +0.16 roundabout aperiodic, +0.03-0.05 periodic.
- Curve: PRR drops steeply between 70 and 140 m (R1 periodic W 0.92 -> 0.32) and floors at 0.1-0.3 beyond
  ~175 m (blocked by the corner buildings). At 140 m: R1 0.32, R3 0.48, R4 0.67, R6 0.77. E exit does better
  than W at long range (wrapped platoon, shorter straight-line distances).
- Worst member on the curve, periodic W: R1 0.13 -> R6 0.31.
- Cost (resources / packet): periodic highway R6 3.8 vs R1 5.9 (-35 %); aperiodic R6 12.5 vs R1 14.5.
- Latency: R6 within ~1.5 ms of R1; worst-member p95 < 30 ms; PRR within PDB = PRR everywhere.
- HARQ-IR: significant only for the leader arms (R2 / R3 +0.03-0.07 aperiodic); helper arms within +/- 0.02.
- Helper misses the leader SCI in 6-12 % of TBs (aperiodic), ~2 % (periodic): the fallback's case.

Density sweep (R6 gain paired per seed):
- Curve, periodic: R6 - R1 = +0.16 to +0.19 and R6 - R3 = +0.12 to +0.13 at **every** density (+/- 0.01-0.02):
  the blockage gain does not depend on the load.
- Highway aperiodic: R6 - R1 grows with load (+0.06 at 0 cars, +0.14 at 45); R6 - R3 not significant from
  30 cars on (-0.003 +/- 0.02 at 30, -0.02 +/- 0.04 at 60). Worst member R6 - R1 +0.15 to +0.30.
- Takeover activity rises with load: fallback retx / packet 0.02 -> 0.25 and helper SCI missed 1 % -> 10 %
  (highway aperiodic, 0 -> 60 cars).
- R6 is the cheapest of R1 / R3 / R6 at every density.

## Paper story (agreed outline)
Tail members fail for two separable reasons: interference (load, aperiodic traffic) and blockage (the
roundabout curve). Leader HARQ fixes interference but cannot get around buildings; blind relaying gets around
them but adds collisions. R6 (helper + Option 2 + NACK takeover + SCI fallback) keeps both benefits: best or
tied everywhere, about 2x worst-member PRR on the curve, fewer resources than blind, no latency cost; the
fallback matters exactly when the load rises. Proposed title: "When Relaying Hurts: Feedback-Driven Leader
Takeover for Cooperative NR-V2X Groupcast in Platoons". Venues: IEEE VTC / VNC / WCNC or a V2X workshop
(check deadlines). Figure plan: scheme timeline, scenario map, PRR vs distance hwy vs curve (R1 / R3 / R4 / R6),
group + worst PRR bars, PRR vs cost, takeover breakdown, R6 gain vs CBR (density).

## Caveats to handle in the paper
1. **"Moderate" saturates the channel.** Measured CBR is linear in the background: 0.25 / 0.49 / 0.74 / 0.98-0.99
   at 15 / 30 / 45 / 60 cars. Rename 60 cars "saturated", put measured CBR on the density x-axis, state that SL
   congestion control (CR limits) is not modelled, use 30 cars (CBR ~0.5) as the realistic operating point.
2. Highway aperiodic: R6 ~ R3 under load (present as: without blockage leader HARQ suffices, R6 matches it at
   lower cost).
3. Absolute worst-member PRR on the curve stays low (0.31-0.53 for R6): many links fully blocked.
4. The helper id in SCI-2A is a non-3GPP extension (present as minimal, cite Rel-18/19 SL relay work); R5's
   dedicated helper NACK resource is also non-3GPP.
5. Single IVD (30 m), platoon size 9, one helper selector (Kuo distance-only): list as limitations.
6. The remote's n60 roundabout traces differ from the laptop's (SUMO with background not reproducible across
   machines; n0 traces identical). Each remote run is consistent with its own trace (windows computed there);
   the laptop renders (`figures/trace_*`) use the laptop traces. Archive the remote traces with the results.
7. The logistic fit is ill-suited to R4 (step-like fit); use the binned tables for arm comparisons.

## Simulator changes made for this study (flag-gated; regression-checked byte-identical when off)
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

`_run.csv` columns added: `reack_decoded, psfch_exact_attr, fb_late, helper_nack_stale, blind_soft_combining,
opt1_helper_nack, leader_fixed_size, helper_takeover, leader_fallback, soft_combining, fallback_check,
fallback_heard, fallback_tx, fallback_no_slot`. `_ltx.csv` gains `origin` (0 regular, 1 NACK takeover,
2 fallback). `patches/nr/local.patch` = `git -C ns-3-dev/contrib/nr diff` (regenerated by the pre-commit hook).

## Validation
`../tests/`: 38 tests, `run_tests.sh`, `TEST_PLAN.md` (§0.1-§0.4 decisions and findings). Open simulator-side
findings: F23 sub-slot PDB overshoot (periodic2 without `--t2Ms`); F32 periodic2 retx slots after the next
arrival; F33 feedback arms get fewer reserved slots in a PDB-limited window; F34 / DV1 rare R5/R6 feedback edge
cases; DV11 C_resel keep cap; SPS counter mean ~3-5 % low. Not yet checked whether F33 / F34 bias the
periodic1tb / aperiodic1tb comparisons (F33 concerns exactly the feedback arms): check before submission.

## Next steps
1. Density notebook: CBR-axis versions of the R6-gain, takeover-activity and worst-member figures; relabel
   60 cars "saturated" in `build_notebook.py` / `build_density_notebook.py`.
2. Confirm the R4 aperiodic loss is collisions of the helper copies (`lost_collision`, `_coll.csv`).
3. Archive the remote traces (`traces/*_ivd30_n{15,30,45,60}_*`) next to the results.
4. Check whether F33 / F34 affect the HARQ / R6 cells (see Validation).
5. Paper: outline + abstract, publication figures (start with PRR vs distance hwy vs curve and the takeover
   timeline diagram).
6. Optional: second sensitivity axis (IVD) if a reviewer asks; F33 / F23 fixes.
