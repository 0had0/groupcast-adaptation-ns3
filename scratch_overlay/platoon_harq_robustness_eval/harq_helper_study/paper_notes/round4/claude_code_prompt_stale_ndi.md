# Prompt for Claude Code: fix the stale HARQ state after a missed new-data SCI, test it, push, prepare the re-run

Paste everything below the line into Claude Code, started at the repository root
(`groupcast-adaptation-ns3`, branch `coop-arms`).

---

Read first, in this order:
1. `scratch_overlay/platoon_harq_robustness_eval/harq_helper_study/paper_notes/round4/HANDOFF_STALE_NDI.md` (why, evidence, plan)
2. `scratch_overlay/platoon_harq_robustness_eval/harq_helper_study/HANDOFF.md` (study, arms, campaigns, launchers)
3. `scratch_overlay/platoon_harq_robustness_eval/tests/TEST_PLAN.md` (test suite, C06)

## The bug (verified in the code, do not re-discover it, reproduce it with a test)
`contrib/nr` sidelink receive path, `NrSpectrumPhy` PSSCH decoding (`model/nr-spectrum-phy.cc`, around
l.2860-2965 with the error model, l.3040-3055 without):
- `NrHarqPhy::m_slDecodedTb` marks (tx RNTI, HARQ id) as "previously decoded". The mark is removed only when a
  second-stage SCI with `GetNdi() == 1` arrives (or, with `SlAckPrevDecodedTb`, when the SCI-2A fails).
- The soft buffer of the process (`ResetSlDataHarqProcessStatus`) is also reset only when `GetNdi() == 1`.
- The simulator's NDI is a "new data" flag (1 on the first transmission of a TB, 0 on its retransmissions), not
  the 3GPP bit that toggles at each new TB and is compared with the value stored for the sidelink process
  (TS 38.321 5.22.2.2, TS 38.212 8.4.1.1).

So when a receiver misses the SCI (either stage) of copy 1 of a new TB:
- (A) if it decoded the previous TB on that HARQ id, it skips copies 2 and 3 as "already decoded" (no decode
  attempt) and, when `SlAckPrevDecodedTb` is on (Option 2 arms R3, R4.2, R6), it ACKs them. The false ACK can
  stop the leader (R3) or the helper (R4.2, R6: `relayOverheardAcks` reaches `HelperGroupSize`).
- (B) if the previous TB on that HARQ id was not decoded, copy 2 is soft-combined with the failed copies of the
  previous TB (wrong, optimistic).
A 3GPP receiver sees a toggled NDI on copy 2, flushes the process, and decodes copy 2 as a new transmission.

Evidence: `harq_helper_study/paper_notes/round4/stale_harq_check.py` (R6, roundabouts: after a missed copy-1
SCI, 97.6 % of later copies get no decode attempt when the previous TB was decoded, 47.0 % otherwise).

## Rules
- Write the failing test first, then fix. Do not weaken, skip or re-baseline any existing test to make it pass;
  if an existing test changes outcome, explain why in TEST_PLAN.md and ask me before changing its expectation.
- No `./ns3 configure` (build with ninja as run_tests.sh does). Unset MAGIC_HELPER_ID and NUM_MEMBERS.
- Do not launch anything on the remote. Stop after step 6 and print the launch commands.
- Commit only the files of this work. These local modifications are NOT part of it, leave them uncommitted and
  list them in your final report: `scratch_overlay/common/kpi/kpi_hooks.cc` (a debug NS_LOG_UNCOND),
  `.gitignore`, `netanim`, `scratch_overlay/co_tx_interference/`, `scratch_overlay/mikami_v2v_eval/`,
  `scratch_overlay/relay_*`, and the untracked notebooks / paper folders under `harq_helper_study/`.

## Step 0: commit the pending C06 validation work (separate commit)
The cross-source combining validation (C06) left uncommitted changes: `platoon_harq_robustness_eval.cc`
(`--writeHarqTrace`, `--testFixedSinr`), `tests/CMakeLists.txt`, `tests/TEST_PLAN.md`,
`tests/campaign/runsets.py`, `tests/common/synth_trace.cc`, `tests/run_tests.sh`,
`harq_helper_study/HANDOFF.md`, `harq_helper_study/scripts/build_notebook.py`, and `contrib/nr` changes
(`SlTestFixedSinr`, the HARQ combine trace) that are NOT in `patches/nr/local.patch` yet.
Build, run `tests/run_tests.sh --profile quick --stage env,build,unit --no-notebook` and C06, then
`scripts/regen-patches.sh` and commit as "tests: C06 cross-source combining hooks (writeHarqTrace,
testFixedSinr)". Check that `scripts/regen-patches.sh --check` is clean afterwards.

## Step 1: failing test C07_stale_harq_state (tests/workflow/)
Add a validation-only hook `ns3::NrSpectrumPhy::SlTestDropSci`, string `"rxNode:txNode:k[,k...];..."`: at
receiver rxNode, drop both SCI stages of the k-th PSSCH reception from txNode (count from 1, the PSCCH and
the PSSCH of that reception are ignored as if the PSCCH was not decoded). Empty = off. Expose it in the
platoon program as `--testDropSci` like `--testFixedSinr`. Use `--testFixedSinr` to make decoding
deterministic and `--writeHarqTrace` (`_harq.csv`) to read the buffer state.
Cases (one TX, receivers at fixed SINR, short run, HARQ ids reused):
- (a) TB n decoded on HARQ id h; copy 1 of TB n+1 on h dropped at RX. Expect: copy 2 gets a decode attempt
  with an empty buffer (1 copy combined), copy 3 is combined with copy 2.
- (b) same as (a) with Option 2 (`SlAckPrevDecodedTb` on): RX never ACKs TB n+1 because of TB n; it NACKs or
  ACKs according to the decoding of TB n+1.
- (c) TB n NOT decoded on h (its copies stay in the buffer); copy 1 of TB n+1 dropped. Expect: copy 2 is not
  combined with TB n's copies (buffer flushed first).
- (d) cross-source: (a) to (c) with copy 2 sent by a helper (R6, `relayedFromRnti` = leader, leader's HARQ id).
- (e) regression: nothing dropped. Copies 2 and 3 combine with copy 1 (2 and 3 copies); an already decoded TB
  is skipped (and ACKed with `SlAckPrevDecodedTb`); blind copies without `SlBlindSoftCombining` are still
  decoded alone.
Run it on the current code and record which cases FAIL (expected: a, b, c, d) in TEST_PLAN.md.

## Step 2: fix (minimal, 3GPP semantics)
- TX: keep the internal "new data" flag (`GetNdi()`, `ndi` columns in the logs) unchanged. Add the real NDI
  bit: per (destination, HARQ id) the transmitter flips it at each new TB and puts it in SCI-2A. A helper copy
  carries the leader's bit, as it carries the leader's HARQ id (coop info of the relayed TB).
- SCI-2A header: serialize the bit in one of the existing padding bits of the last byte (the lowest one flags
  the coop side information, use another). `GetAirSize()` stays 5 bytes: the real SCI-2A NDI is 1 bit already.
- RX (`NrSpectrumPhy`, both error-model and no-error-model paths): keep the last NDI bit per (harqRnti, HARQ
  id). After a successful SCI-2A decode, if there is no stored bit or it differs from the received one, the
  reception is a new TB: `RemovePrevDecoded` and `ResetSlDataHarqProcessStatus` for that process, then store
  the bit. Only then apply `IsPrevDecoded` / combining as today. Keep the existing NDI==1 reset (it is
  consistent with the new rule).
- Check every other place that relies on the "new data" flag at the receiver (MAC HARQ, helper relay setup in
  `nr-sl-ue-mac.cc` ~l.1395-1480 and the `relayOverheardAcks` logic, `InstallOption2HelperSilence`, the platoon
  program's attribution of `_att.csv` / `_pkt.csv`) and report whether it needs the same change.
- Add a run-level counter `ndi_toggle_new_tb` to `_run.csv` (end of the header): receptions where the toggle
  rule detected a new TB while the "new data" flag was 0 (= the cases the bug mishandled).

## Step 3: tests after the fix (all must pass, report the table)
1. C07: all cases pass.
2. `tests/run_tests.sh --profile quick --jobs <n>`: every stage. Compare the summary with the last committed
   `tests/results/` summary: no new FAIL. Any change of status (including XFAIL -> PASS) must be explained.
   Pay attention to B03 (Option 2), B04 (feedback binding), B05 (HARQ process management), B06 (SPS x HARQ),
   C01-C06 (helper, cross-source combining), G04, G08, A06 (determinism).
3. `harq_helper_study/scripts/study.py run --profile smoke` then `../../scripts/study.py check --profile smoke`.
4. Before/after mini comparison, same commands with the old binary (build of the step-0 commit, keep it as
   `STUDY_BIN`) and the new one, output in `harq_helper_study/out/ndi_ab/{old,new}`: arms R1, R3, R4, R6 x
   {roundaboutE periodic1tb none seed 1, roundabout aperiodic1tb none seed 1} (`--only` regex on ../../scripts/study.py
   profile full, `--seeds 1`). Then run
   `paper_notes/round4/stale_harq_check.py` on both: in `new`, the share of later copies with no decode attempt
   after a missed copy-1 SCI must no longer depend on "prev TB decoded" (within a few points), and
   `ndi_toggle_new_tb` > 0. Report PRR old vs new per run (expected: equal or higher, the change bounded by
   `paper_notes/round4/stale_harq_bound.py` on the old run).

## Step 4: commit and push
`scripts/regen-patches.sh` (the patch must contain the fix), `scripts/regen-patches.sh --check` clean.
Commit: "nr sidelink RX: 3GPP NDI toggle; flush stale HARQ state after a missed new-data SCI (C07)".
Tag `stale-ndi-fix`. Push branch and tag to both remotes: `git push origin coop-arms --tags` and
`git push imta coop-arms --tags`.

## Step 5: launchers for a clean re-run
The new runs must not overwrite or be skipped by the old ones (../../scripts/study.py skips a run whose `.cmd.json`
matches; the commands are unchanged, so a re-run into `out/full` would skip everything).
- `../../scripts/launch_full.sh` and `../../scripts/launch_density.sh`: add `--out DIR` (passed to `../../scripts/study.py run --out`, log to
  `out/<basename DIR>_run.log`, default unchanged) and `--only REGEX` (passed to `../../scripts/study.py`). Make `--stop` and
  the "already running" check match the chosen out dir. `../../scripts/launch_full.sh`: keep the smoke test.
- `../../scripts/monitor_study.sh`: works with `--log out/full_v2_run.log` / `out/density_v2_run.log` (check).
- Record the commit hash in each run: write `git rev-parse HEAD` into `<out>/COMMIT` at launch.
Commit "harq_helper_study launchers: --out / --only, commit stamp" and push to both remotes.

## Step 6: stop and print
Print: the test table, the A/B table, the commit hashes, the list of files left uncommitted, and the launch
commands below adapted to what you implemented. Do not run them.

```
ssh -F ~/.ssh/ease-config ease-rtx
cd ~/Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study
# 1) full campaign, 10 arms of the paper (no _noIR), 10 seeds: setup.sh --force rebuilds contrib/nr from the patch
./launch_full.sh --pull coop-arms --seeds 1-10 --jobs 32 --out out/full_v2 \
    --only '^(R1|R1_bsc|R2|R3|R4|R4_bsc|R4\.1|R4\.2|R5|R6)/'
./monitor_study.sh full --log out/full_v2_run.log --watch 300
# 2) when (1) is done: density sweep (same binary, no setup)
./launch_density.sh --seeds 1-10 --jobs 32 --out out/density_v2
./monitor_study.sh density --log out/density_v2_run.log --watch 300
```
