Context. Repo groupcast-adaptation-ns3, study folder scratch_overlay/platoon_harq_robustness_eval/harq_helper_study/.
Read HANDOFF.md, ../scripts/study.py, ../scripts/launch_full.sh, ../scripts/launch_density.sh and ../scripts/monitor_study.sh first. Both campaigns are complete on
the remote (`ssh -F ~/.ssh/ease-config ease-rtx`, 32 cores): out/full (profile `full`, seeds 1-10, 1440 runs) and
out/density (profile `density`, seeds 1-10, 540 runs). New runs must go on the remote too: its n60 roundabout traces
differ from the laptop's and the existing results were made with them.

Task. Prepare, and give me the commands to launch, ONLY the runs below. Nothing else may run.

A. Blind soft combining for the blind arms. 3GPP lets a receiver combine blind retransmissions of the same TB; the
   campaign turned this off (`--blindSoftCombining=false`, the program default) to match Kuo et al.
   - new arm `R1_bsc` = the R1 flags + `--blindSoftCombining=true`
   - new arm `R4_bsc` = the R4 flags (incl. KUO) + `--blindSoftCombining=true`. Note: NrSpectrumPhy uses
     SCI-2A relayedFromRnti as the HARQ owner, so members then combine leader and helper copies, the same
     capability R4.1/R4.2/R5/R6 already have.
   - cells: the `full` profile cells (highway, roundabout, roundaboutE x periodic1tb, aperiodic1tb x none, moderate
     x seeds 1-10): 2 x 120 = 240 runs into out/full.
   - plus `R1_bsc` in the `density` cells (n15, n30, n45): 180 runs into out/density, so the density results can
     use the combining baseline. Do NOT add R4_bsc to the density profile.
B. R4 and R4.2 (flags unchanged) in the `density` cells (n15, n30, n45 x 3 scenarios x 2 traffic x seeds 1-10):
   360 runs into out/density.
Expected total: 780 new runs, 0 re-runs.

Rules
1. No existing run may be re-run. ../scripts/study.py skips a run when `_run.csv` exists and `.cmd.json` equals the planned
   command exactly. So do not touch BASE_FLAGS, DIAG, KUO, existing ARMS entries, curve_window, the binary
   selection, run_retx.py, or the cells of any existing profile. Only add.
2. Add `R1_bsc`, `R4_bsc` to ARMS and FAMILY. In the `density` profile, extend `arms` to
   ['R1', 'R3', 'R6', 'R4', 'R4.2', 'R1_bsc'] (append only).
3. `cmd_check`: `blind_soft_combining` must be '1' for `*_bsc` arms and '0' for all others; strip `_bsc` like `_noIR`
   when deriving the base arm so every other invariant still applies.
4. Touch ../scripts/build_notebook.py / ../scripts/build_density_notebook.py only if new arm names would make them crash (STYLE/LABEL
   dicts); do not change their outputs otherwise.
5. Before any launch, on the remote, print these counts with a short script using study.plan() and study.done(),
   and stop if any differs:
   - `full`, seeds 1-10: 1440 done, 240 not done, and the 240 are exactly R1_bsc + R4_bsc;
   - `density`, seeds 1-10: 540 done (R1, R3, R6), 540 not done (R4, R4.2, R1_bsc).
6. Smoke test: one short run per new arm (highway, periodic1tb, none, seed 1, --maxTime=8) written to a scratch
   output dir (NOT out/full or out/density); check `blind_soft_combining` = 1 in `_run.csv` and that the run exits 0.
7. Launch with nohup, resumable, all cores, the two campaigns one after the other (not in parallel):
     python3 ../scripts/study.py run --profile full    --seeds 1-10 --only '^R(1|4)_bsc/'          --jobs 32
     python3 ../scripts/study.py run --profile density --seeds 1-10 --only '^(R4|R4\.2|R1_bsc)/'   --jobs 32
   (check the regexes against `../scripts/study.py list` first: '^R4/' must not match R4.1 / R4.2 / R4_bsc).
   Logs: out/full_bsc_run.log, out/density_add_run.log. Then `../scripts/study.py check` for both profiles.
   Give a runtime estimate from the wall_s of comparable existing runs in out/full/manifest.csv.
8. Give me: the git diff, then the exact commands in order (git pull on the remote, smoke, count check, launch,
   monitor with ../scripts/monitor_study.sh, check), and an rsync command that copies back only the new arms
   (cmd.json, _run, _member, _pkt; for out/full also _att, _ltx, _hsel, _htx, _grants) into out/remote_full and
   out/remote_density on the laptop.
Do not run anything on the remote yourself; show me the commands first.
