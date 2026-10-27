# HANDOFF: HARQ x Kuo-helper study (updated 2026-10-27)

## Status in one paragraph
All campaigns are complete on the remote (`ease-rtx`, 32 cores) and copied to the laptop: the **full study**
(14 arms x 3 scenarios x 2 traffic x 2 background x 10 seeds = 1680 runs: the original 1440 + R1_bsc / R4_bsc,
all exit 0) and the **density sweep** (R1 / R3 / R6 + R4 / R4.2 / R1_bsc x 3 scenarios x 2 traffic x 15 / 30 /
45 cars x 10 seeds = 1080 runs, all exit 0). Since 2026-10-24: blind-soft-combining arms and the density
additions ran (commit `06dc15a`); cross-source HARQ combining was verified by a new test (C06, **no bug**, one
model caveat F35); the Disconnection Time metric (Rolich et al. 2026) was added to
`STUDY_RESULTS_remote_full.ipynb`; the local1 "moderate > none" roundabout PRR was traced to a KPI-window
artifact. The results support a conference paper (story below); open items before writing: the
background-load wording (60 cars saturate the channel), the DT rate metric, the F33 / F34 check.

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
- Blind soft combining (added 2026-10-26): R1_bsc / R4_bsc = R1 / R4 + `--blindSoftCombining=true` (receivers
  combine the blind copies of a TB, 3GPP-allowed; the campaign default `false` follows Kuo). R4_bsc combines
  leader and helper copies (HARQ owner = SCI-2A relayedFromRnti). Full profile all cells; R1_bsc also in density.

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
| `monitor_study.sh` | `./monitor_study.sh density [--log FILE] [--watch 60]` (or `full`): progress, failures, ETA, running sims, memory, disk; `--log` for runs launched with another log name (e.g. `out/full_bsc_run.log`) |
| `combining_evidence.py` | read-only evidence on `out/remote_full` for cross-source combining (R4.2 vs R4.2_noIR, R3 vs R3_noIR control): next-copy success vs SINR, split by first-copy SINR, SCI miss vs corrupt, HARQ-id reuse, PRR decomposition; writes `figures/combining/` (CSV, PNG, `summary.md`) |
| `build_notebook.py` | `STUDY_RESULTS_<profile>.ipynb`: PRR vs distance, summary, CBR savings, spectrum, latency / PIR, CDFs, AoI, HARQ-IR, takeover; 95 % CI over seeds everywhere (paired for savings / IR gain); one colour per arm, family = line style + marker |
| `build_requests_notebook.py` | `REQUESTS.ipynb`: the session analyses (distance definition, curve windows + trace renders, R1 hwy vs rbt, R1 vs R4, R1 vs R2 vs R3) with logistic fits `b + (a-b)/(1+exp((d-d50)/w))` |
| `build_density_notebook.py` | `DENSITY_RESULTS.ipynb`: merges `--full-dir` (0, 60 cars) and `--density-dir` (15 / 30 / 45); PRR, worst member, paired R6 gains, cost, CBR, takeover activity, PRR vs distance per density |
| `render_rbt_trace.py` | renders a roundabout trace over the SUMO net + buildings (snapshots PNG + GIF, KPI window in red) |
| `plot_prr_dist_arms.py`, `plot_r1_prr_dist.py`, `plot_spectrum_prr.py` | standalone figure scripts |
| `../gen_mobility.py` | traces; `--rbt-exit {W,S,E}`; roundabout platoon insertion retried when SUMO cannot insert every member (fix for the remote n15 s5 failure; first attempt unchanged, meta `platoon_insert_attempts`) |

Resuming is command-aware: a run is skipped when its `_run.csv` exists and its `.cmd.json` equals the command.
Pitfalls: (1) `study.py run` **overwrites `<outdir>/manifest.csv`** with the rows of that call only (the remote
keeps the campaign ones as `manifest.csv`, the add-on runs as `out/full/manifest_bsc.csv`,
`out/density/manifest_add.csv`); (2) `--out` must be an absolute path (the simulator runs with cwd = the arm
folder, so a relative `--out` writes the CSVs into a nested folder and `check` reports "no _run.csv").

## Data (all git-ignored)
| where | what |
|---|---|
| remote `harq_helper_study/out/full/`, `out/density/` | the campaigns (complete) + `STUDY_RESULTS_full.ipynb`, `DENSITY_RESULTS.ipynb` built there |
| laptop `out/remote_full/` | full study, 14 arms: `cmd.json, _run, _member, _pkt, _grants, _ltx, _att, _hsel, _htx, _nack` (R1_bsc / R4_bsc: no `_nack`) |
| laptop `out/remote_density/` | density sweep, 6 arms: `cmd.json, _run, _member, _pkt` |
| laptop `out/local1/`, `out/full/` | early local runs (seed 1; `local1` roundabout = straight approach only, not the curve) |
| laptop notebooks | `STUDY_RESULTS_remote_full.ipynb`, `REQUESTS.ipynb`, `DENSITY_RESULTS.ipynb` (all from the remote data), `DENSITY_RESULTS_remote.ipynb` (built on the remote) |

Rebuild locally:
```
python3 build_notebook.py --profile remote_full
python3 build_requests_notebook.py --hwy-profile remote_full --rbt-profile remote_full --arms-hwy-profile remote_full --arms-rbt-profile remote_full
python3 build_density_notebook.py --full-dir out/remote_full --density-dir out/remote_density
```
Copy from the remote (`~/.ssh/config` does not include `ease-config`, hence `-F`; run it on the laptop, and use
`"ssh -F $HOME/.ssh/ease-config"`: inside single quotes the `~` is not expanded and ssh fails):
```
rsync -av -e "ssh -F $HOME/.ssh/ease-config" --prune-empty-dirs --include='*/' --include='*.cmd.json' --include='*_run.csv*' --include='*_member.csv*' --include='*_pkt.csv*' --exclude='*' 'ease-rtx:Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study/out/<full|density>/' out/remote_<full|density>/
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

Blind soft combining (R1_bsc / R4_bsc, paired per seed, full profile):
- R1_bsc - R1 = +0.004 to +0.056 (largest highway aperiodic 60 cars, 0.604 -> 0.660; curve +0.03..0.04);
  R4_bsc - R4 = +0.001 to +0.011 only (helper copies strong + F35). Same resources (same transmissions).
- R6 - R4_bsc = +0.025 to +0.26, R6 - R1_bsc = +0.019 to +0.157, all significant: R6 still best.
- R3 - R1_bsc on the unloaded curve = -0.008..+0.014 (n.s.): with combining, leader HARQ loses its edge where
  blockage dominates; only relaying helps there.

Density additions (0 / 60 cars from the full study; CBR 0.01 / 0.25 / 0.49 / 0.74 / 0.99):
- R4 - R1 (blind relaying): highway aperiodic -0.004 / -0.046 / -0.096 / -0.099 / -0.158; roundabout E
  aperiodic +0.081 -> -0.060, W +0.056 -> -0.041 (crossover ~45 cars, CBR ~0.74); roundabout periodic
  +0.08..+0.12 at every load. This is the "when relaying hurts" figure.
- Takeover gain R6 - R4.2: highway aperiodic +0.045 -> +0.247; roundabout aperiodic ~+0.045 -> +0.13..0.16;
  periodic +0.015..0.046. R4.2 - R3 highway aperiodic reaches -0.27 at 60 cars (helper without takeover is
  worse than leader HARQ under load).
- R1_bsc - R1: +0.004..0.056, constant-ish with load.

Disconnection Time (DT, Rolich, Yildiz, Baiocchi, IEEE Commun. Lett. 30:1860-1864, 2026, doi
10.1109/LCOMM.2026.3685872; reception times, "never received", 10 seeds):
- Curve: leader-only members blind for long periods: roundabout W aperiodic no load mean DT R1 3545 ms,
  R3 2807, R6 666; worst member R1 10.9 s, R3 7.5 s, R4.2 1.7 s, R6 1.3 s. Periodic: R1 1146, R6 356 ms.
- Highway aperiodic 60 cars: mean DT R1 282, R3 245, R4 372 (blind relaying lengthens outages), worst member
  R6 332 vs R4 576 ms.
- The 4 variants (reception / generation times x never received / not within PDB) coincide here: |rx - gen|
  <= 4 ms in mean DT, and no message is delivered after its PDB. 0 out-of-order intervals; 1836 censored
  outages dropped (window edges).
- P(DT > theta) is a share of the outages, not of time: R6 has far fewer outages, so its share can look
  similar or higher (e.g. 0.106 vs 0.073 for R4.2 in one cell). Proposed fair view (not done): rate of
  outages longer than theta per member-minute.

Cross-source HARQ combining (why `_noIR` changes the helper arms by only +-0.02):
- Test C06 (controlled, 3 UEs, fixed SINR): the helper copy is combined with the leader copy (buffer length 2,
  no reset in between, SCI-2A carries the leader's HARQ id / source, NDI 0, RV 2): 174/174 decoded with
  combining vs 0/174 without. **Not a bug.**
- Campaign data (`combining_evidence.py`): IR shifts the helper copy's 50 % SINR by 2.5-4.3 dB on the highway
  (R3: 2.0-5.0 dB) but 0.1-1.8 dB on the curve. The PRR effect is small because only 10-30 % of member-TBs get
  a helper copy after a corrupt first copy (R3: 12-54 %, every failure gets a retx) and the helper copy usually
  decodes alone: implied dPRR 0.003-0.016 vs observed -0.011..+0.016 (R3: implied 0.007-0.078, observed
  0.001-0.068). SCI-miss share is the same in both families (5-26 %), not the differentiator. HARQ-id reuse
  before a helper copy: 0 of 447,836 copies.
- F35 (model caveat, stock 5G-LENA `nr-eesm-ir.cc` ComputeSINR / GetMcsEq): IR combining is an EESM average of
  the copies, so a weak earlier copy can make a strong copy fail (335 B, MCS 14: 10 dB alone TBLER 0, after a
  0 dB copy TBLER 1, effective 5.9 dB). Data shows it: strong helper copy after a weak first copy 0.75-0.79
  with IR vs 0.83-0.84 without. Makes combining pessimistic for the helper arms; not fixed (would need an
  error-model change and a full re-run).

## Paper story (agreed outline)
(Updated 2026-10-27, see Results above.) Tail members fail for two separable reasons: interference (load, aperiodic traffic) and blockage (the
roundabout curve). Leader HARQ fixes interference but cannot get around buildings; blind relaying gets around
them but adds collisions. R6 (helper + Option 2 + NACK takeover + SCI fallback) keeps both benefits: best or
tied everywhere, about 2x worst-member PRR on the curve, fewer resources than blind, no latency cost; the
fallback matters exactly when the load rises. Proposed title: "When Relaying Hurts: Feedback-Driven Leader
Takeover for Cooperative NR-V2X Groupcast in Platoons". Venues: IEEE VTC / VNC / WCNC or a V2X workshop
(check deadlines). Figure plan: scheme timeline, scenario map, PRR vs distance hwy vs curve (R1 / R3 / R4 / R6),
group + worst PRR bars, PRR vs cost, takeover breakdown, R6 gain vs CBR (density), R4 - R1 crossover vs CBR,
takeover gain R6 - R4.2 vs CBR, disconnection time (bars / CDF, worst member on the curve). Robustness
paragraph: R6 still wins against the combining blind baselines (R1_bsc / R4_bsc).

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
8. F35: the 5G-LENA HARQ-IR abstraction averages the copies' SINR, so combining a weak leader copy with a strong
   helper copy is pessimistic; the helper arms' combining gains are conservative (one sentence in the paper).
9. `STUDY_RESULTS_local1.ipynb` roundabout: "moderate" PRR > "none" is a **KPI-window artifact**, not a load
   effect: local1 runs background cells for 20 s only (window 3-19.7 s = straight approach, 0 % bent links,
   the curve starts at 29.2 s on the laptop n60 trace) while bg none runs the whole trace (3-53.9 s, ~30 % of
   packets on the curve). With matched windows (3-19.7 s) none > moderate (R1 0.98 / 0.95 vs 0.89 / 0.78).
   The full profile uses the curve window for both: moderate < none in every seed. Do not compare loads with
   local1 roundabout cells.

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
| `--testFixedSinr="rx:tx=dB;..."` (empty) | `NrSpectrumPhy::SlTestFixedSinr` | validation only: PSSCH SINR of the given links (node ids) fixed on every RB (C06) |
| `--writeHarqTrace` (false) | trace `NrSpectrumPhy::SlHarqCombine` | `<out>_harq.csv`: members' HARQ buffer events (decode with copies combined, resets, SCI-2A fields); no sink = no cost |

`_run.csv` columns added: `reack_decoded, psfch_exact_attr, fb_late, helper_nack_stale, blind_soft_combining,
opt1_helper_nack, leader_fixed_size, helper_takeover, leader_fallback, soft_combining, fallback_check,
fallback_heard, fallback_tx, fallback_no_slot`. `_ltx.csv` gains `origin` (0 regular, 1 NACK takeover,
2 fallback). `patches/nr/local.patch` = `git -C ns-3-dev/contrib/nr diff` (regenerated by the pre-commit hook).

## Validation
`../tests/`: 39 tests (C06_cross_source_combining added 2026-10-26: run-set `SYN_COMB`, `python3
campaign/run_matrix.py --runset SYN_COMB`, then `run_tests.sh --stage check --only C06`: 74 PASS, 2 XFAIL F35,
0 FAIL; `C06 --calibrate 335 20` / `--seq ...` print the error model). The two new hooks were regression-checked
byte-identical when off (12 smoke arms + 10 moderate cells: only the timing columns `wall_s`,
`sel_cpu_us_mean` differ; A03 unchanged). Older suite: `run_tests.sh`, `TEST_PLAN.md` (§0.1-§0.4 decisions and findings). Open simulator-side
findings: F23 sub-slot PDB overshoot (periodic2 without `--t2Ms`); F32 periodic2 retx slots after the next
arrival; F33 feedback arms get fewer reserved slots in a PDB-limited window; F34 / DV1 rare R5/R6 feedback edge
cases; DV11 C_resel keep cap; SPS counter mean ~3-5 % low. Not yet checked whether F33 / F34 bias the
periodic1tb / aperiodic1tb comparisons (F33 concerns exactly the feedback arms): check before submission.

## Working-tree state (2026-10-27, nothing below committed)
- NR (`ns-3-dev/contrib/nr`): `nr-spectrum-phy.{h,cc}`, `nr-sl-phy-mac-common.h` (the two hooks).
  `patches/nr/local.patch` is stale until the pre-commit hook regenerates it at the next commit; the remote needs
  `setup.sh --force` + rebuild only to run C06 there (the campaigns do not need it).
- `platoon_harq_robustness_eval.cc` (`--testFixedSinr`, `--writeHarqTrace`); `tests/`: `CMakeLists.txt`,
  `run_tests.sh`, `TEST_PLAN.md` (C06, F35, SYN_COMB), `campaign/runsets.py`, `common/synth_trace.cc`,
  `workflow/C06_cross_source_combining.test.cc` (new).
- `harq_helper_study/combining_evidence.py` (new), `figures/combining/` (outputs).
- `STUDY_RESULTS_remote_full.ipynb`: DT section (definition + 4 code cells after the AoI table, ids
  `f2f6ffae`, `887048c2`, `0efed7bb`, `914b794f`, `4f637c1d`), added **in the notebook only**: rebuilding it
  with `build_notebook.py` drops them (port them to the builder first). Tested by a full execution of a copy
  (3 min 40 s, no error); the open file holds the cells without outputs.
- `build_notebook.py`: the user's own uncommitted style rework (STYLE as (colour, linestyle, width), MARK per
  arm, bar hatching). Note: the DT cells use the notebook's current 2-tuple STYLE; adapt them when porting.

## Next steps
1. DT: agree on and add the outage-rate view (outages > theta per member-minute); port the DT cells to
   `build_notebook.py`; DT for the density sweep (`_pkt.csv` is there).
2. Notebooks: add R1_bsc / R4_bsc to `build_notebook.py` and R4 / R4.2 / R1_bsc to `build_density_notebook.py`
   (crossover R4 - R1 and takeover gain R6 - R4.2 vs CBR).
3. Density notebook: CBR-axis versions of the R6-gain, takeover-activity and worst-member figures; relabel
   60 cars "saturated" in `build_notebook.py` / `build_density_notebook.py`.
4. Confirm the R4 aperiodic loss is collisions of the helper copies (`lost_collision`, `_coll.csv`).
5. Archive the remote traces (`traces/*_ivd30_n{15,30,45,60}_*`) next to the results.
6. Check whether F33 / F34 affect the HARQ / R6 cells (see Validation).
7. Paper: outline + abstract, publication figures (start with PRR vs distance hwy vs curve and the takeover
   timeline diagram).
8. Optional: second sensitivity axis (IVD) if a reviewer asks; F33 / F23 fixes.
