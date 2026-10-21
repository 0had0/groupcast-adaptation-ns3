# TEST_PLAN — sanity and validity tests for `platoon_harq_robustness_eval`

Author: orchestrator (Opus), 2026-10-17. Executor: a Sonnet agent. File discovery and code understanding were done beforehand by Haiku agents; their findings are summarised in §6 so the executor only needs targeted reads.

Location: `scratch_overlay/platoon_harq_robustness_eval/tests/` (visible to ns-3 as `ns-3-dev/scratch/platoon_harq_robustness_eval/tests/` through the existing symlink).

---

## 0. Why these tests exist

The user wants to trust the results that compare:

* **Blind retransmission + Kuo's helper selection** (the user calls this "R1 + H1"; see §3 for the mapping to code flags),
* **HARQ Option 1 (NACK-only) + Kuo helper**,
* **HARQ Option 2 (ACK/NACK) + per-TB (per-HARQ-process) helper**, where the named helper never sends an ACK and the leader only listens for its NACK.

Questions the test suite must answer, each with evidence:

| ID | Question | Main tests |
|----|----------|-----------|
| Q1 | Do blind, Option 1 and Option 2 follow the 3GPP Rel-16 workflow (TX → RXs, PSFCH timing, ACK/NACK semantics, DTX handling, retransmission triggering)? | A02, B01–B06 |
| Q2 | Is every ACK/NACK bound to the right TB / HARQ process and sent in the right PSFCH occasion? | A02, B04, B05 |
| Q3 | Do PRR, PIR and spectrum usage vary logically per traffic class, per RX (channel / distance) and with background interference? | E01–E06, G06 |
| Q4 | Do SB-SPS and dynamic scheduling behave as specified, and how do they compare? | F01–F04 |
| Q5 | Does the cooperative helper work as designed (signalling, per-TB selection, relay gating, feedback, Option-2 silent helper with NACK-reclaim)? | C01–C04, D01–D03, G04 |
| Q6 | Are the patched NR module and the wrappers wired correctly (no hidden behaviour changes, deterministic, debug hooks off)? | A01, A03–A06 |
| Q7 | **Study question.** How many blind retransmissions of the Kuo method actually help, especially for the worst (far) receivers? The ineffective ones bound what HARQ with a per-TB helper can save. | G01, G02 |
| Q8 | **Study question.** Are the HARQ spectrum gains (Option 1, Option 2) real and correctly measured, and is the same PRR reachable? | G02, G03 |
| Q9 | **Study question.** How reliable is the selected helper at raising the PRR of the worst receiver? | G05 |

### 0.1 USER DECISIONS (2026-10-17). These OVERRIDE anything below that conflicts

* **D-1 Arms: Kuo only.** Drop the static member 4 (H1) and harqKuo (H5) everywhere. The comparison is **Kuo + blind (R4_S1_H2) vs Kuo + Option 1 (R5_S1_H2) vs Kuo + Option 2 (R6_S1_H2)**. Leader-only R1, R2 and R3 stay as no-helper references; they are needed for the helper-effect tests G05 and G06. Campaign arms (6): **R1, R2, R3, R4_S1_H2, R5_S1_H2, R6_S1_H2**. Synthetic run-sets use H2 in place of H1. With static geometry, Kuo's choice is deterministic; read the helper from `_hsel.applied`. In the oracle pairs (G02), drop the H1 rows. D01 may still unit-test the selector's static mode as a property, but no H1/H5 simulations run.
* **D-2 Spectrum: report BOTH transmitted and reserved/announced resources**, per node (leader, helper) and per **resource origin**. Build `common/spectrum_accounting.{h,cc}` as a reusable module, so that later studies can compare: (a) the helper relaying in resources the leader reserved with **SB-SPS** (S1, `--leaderSps=true`, current BASE_FLAGS); (b) the helper relaying in resources the leader reserved with a **dynamic grant** (S1, `--leaderSps=false`, the SPSDYN run-set); (c) later, the helper using its **own dynamic resources** (S3 ownRes, not run now; keep the API ready).
  * Classify every reserved leader subchannel-slot as one of: used by leader / used by helper / reclaimed and used by leader / unused because an ACK cancelled it / unused because the relay was not triggered (coop NO_NACK) / unused because the helper did not decode / unused because of an empty SPS period (no data) / unused because of reselection.
  * Reserved totals come from the leader grant events in `_grants.csv` (created/kept: sb_len × slots per period × periods actually in force). Confirm the meaning of `slots_or_counter` in `OnGrantCreated`/`OnSpsKept` (main.cc ≈1207-1257) before using it. **Never** use `res_reserved_*`/`res_unused_*` from `_run.csv` (F02).
  * Known gap, report as `INFO`: the helper's own grants and SCI-1A announcements are not logged by the simulator (members are skipped at main.cc:1876-1879, and there is no SCI-1A file). Case (c) therefore needs a later logging flag; recommend it in the final report.
* **D-3 PSFCH stays lossless.** No PSFCH-loss sensitivity test.
* **D-4 Quantify the HARQ trade-offs** (new test **G07_harq_tradeoffs**, §8): for each trade-off event give the count, the probability (Wilson CI), and **the conditions under which it happens**. Conditions: scenario, IVD, traffic class, background level, member index / distance bin, link condition (LOS/NLOSv/NLOS), SINR bin, collision flag. Rank the conditions by probability.
* **D-5 Quantify the Option-2 silent-helper gap** (new test **G08_silent_helper_gap**, §8): count, probability and the conditions under which it happens. Also evaluate the user's protocol requirement: *"if this happens, the leader senses the retransmission slot allocated to the helper; if nothing was sensed, the leader performs the retransmission afterwards if possible."* Check whether the code does it (expected not: reclaim exists only on the helper's NACK, nack_only_harq.cc:392-415). Quantify the PRR lost and the feasible-fallback upper bound.
* **D-6 Helper selection stays per TB** (per published grant). A per-HARQ-process helper is future work.
* **D-7 A03b APPROVED.** Reconfigure with the current flags plus tests, exactly: `cd ns-3-dev && ./ns3 configure --enable-examples --disable-werror --enable-tests`. The current build was configured by `build.sh:13` as `./ns3 configure --enable-examples --disable-werror`, profile `default`. Procedure:
  1. Do it only when no build and no simulation is running.
  2. Snapshot `grep -E '^(NS3_|CMAKE_BUILD_TYPE)' ns-3-dev/cmake-cache/CMakeCache.txt` before and after; the only allowed difference is `NS3_TESTS:BOOL=OFF→ON`. If anything else changed, stop and report.
  3. Rebuild the production binary and the tests.
  4. Re-run the DET reference and require byte-identical outputs against the pre-reconfigure DET outputs (enabling tests must not change the simulator).
  5. Then run the stock suites `nr-test-sidelink-harq`, `nr-sl-sensing`, `nr-sl-sci-headers` and the `sl-multi-lc-*` example-as-test suites (`./test.py --suite=<name>`, or `./ns3 run "test-runner --suite=<name>"`).
  6. Report the stock pass/fail and compare it with the predictions in A03.

### 0.2 FIXES APPLIED 2026-10-18 (user chose: fix F25, F30, F01, then the reduced profile)

The orchestrator applied these fixes with user approval. Each one has a flag, so the pre-fix behaviour can be reproduced.

* **F25, Option-2 re-ACK (3GPP TS 38.321 §5.22.2.2).**
  * New attribute `ns3::NrSpectrumPhy::SlAckPrevDecodedTb` (default **false**, so other studies are unchanged).
  * When true, a receiver that already decoded a TB sends an ACK for its retransmissions whenever the SCI-2A enables HARQ feedback (nr-spectrum-phy.cc, the `ackPrevDecoded` lambda in `RxSlPssch`, used in both `IsPrevDecoded` branches).
  * The platoon sim sets it from the new flag `--reAckDecoded` (default **true**).
  * `patches/nr/local.patch` was regenerated (`git -C ns-3-dev/contrib/nr diff`).
  * Residual (legitimate) DTX: an already-decoded member whose SCI-2 of the retransmission fails (eval `_att` status 2 for a member that had decoded earlier), or whose SCI-1 is missed, still sends nothing. Option 2 reads that as NACK, which is 3GPP-correct (§4.4). Tests must classify the remaining "wasted" transmissions into this legitimate DTX and unexplained cases. Only unexplained ones are FAIL.
* **F30, PSFCH binding.** `NackOnlyHarqController::SetExactPsfchAttribution` (platoon flag `--psfchExactAttribution`, default **true**):
  * feedback binds only to the pending transmission whose decision is due within the next slot;
  * feedback outside every occasion goes into `nFbLate` (new run.csv column `fb_late`);
  * a helper NACK for a TB that its HARQ id no longer carries does not reclaim; it is counted in `helper_nack_stale`.
* **F01, `res_used_helper`.** It now sums the PSSCH of members 1..numMembers (relayed copies), instead of node `--helper`. So `res_per_pkt_used`, `eff_member_pkts_per_res` and `platoon_cbr_share` are corrected.
  * New `_run.csv` columns at the end: `reack_decoded, psfch_exact_attr, fb_late, helper_nack_stale`.
* **Orchestrator's regression check on SYN_MARGINAL** (49 runs × {fixes off, fixes on}):
  * Fixes off: byte-identical to the pre-fix outputs (except the wall-clock `sel_cpu_us_mean`).
  * Fixes on: R1, R2, R4 and R5 byte-identical; only R3 and R6 change.
  * Transmissions sent after all 8 members had decoded, before → after: R3 periodic1tb 110 → 9, R6 periodic1tb 207 → 21, R6 periodic2tb 700 → 126.
  * Leader/helper same-slot clashes: 12 → 0.
  * Data: `results/FIX_regression_SYN_MARGINAL.csv`.
* **F31 (found by checkpoint 2, fixed by the orchestrator).**
  * The problem: with re-ACK on, the PHY "already decoded" flag outlived a new TB whose SCI-2A failed (5G-LENA's NDI is 1/0, not toggled), so its retransmission was falsely ACKed.
  * The fix, under the same attribute: `RemovePrevDecoded` on an SCI-2A failure.
  * The platoon sim enables the attribute only when harqMode == opt2. Blind and Option 1 never use member ACKs, so R1, R2, R4 and R5 stay byte-identical to the pre-fix outputs (verified on SYN_MARGINAL).
  * Leader success decisions while a non-helper member lacked the TB, SYN_MARGINAL: R3 0 (pre-fix) → 122 (F25 only) → 1 (F25+F31); R6 0 → 38 → 0. The remaining case is an SCI-1 miss of the new TB's initial transmission, which is not observable at the PHY.
  * The quick outputs of the F25-only binary are archived in `tests/out_quick_v2/`.
* Not fixed (user did not ask): F02 (reserved columns are formulas), F23 (PDB overshoot ≤ 1 slot), the G08 fallback rule. They stay FAIL/XFAIL as before.
* **Pre-fix outputs** are archived in `tests/out_prefix_v1/` (read-only reference for before/after plots). `tests/out/` is rebuilt from scratch with the fixed binary.
* **Reduced profile** (replaces the full profile for this round):
  * CORE: {hwy ivd15, hwy ivd30, rbt ivd30} × bg {none, moderate (= trace n60, `--bgFlowsPerCar=1.67`)} × {periodic1tb, aperiodic1tb, periodic2tb} × 6 arms × seeds 1–3 = 324 runs.
  * Full trace length: highway `--maxTime=53`, roundabout the whole trace.
  * SPSDYN: the quick spec, re-run with the fixed binary.
  * Synthetic run-sets: re-run in full.
  * Estimated cost: about 70 CPU-hours (moderate runs about 25 min each), so about 9 h at JOBS=8 (peak RSS 224 MB per run).

---

### 0.3 SECOND SIMULATOR CHANGE SET (2026-10-19, user approved; applied and regression-checked by the orchestrator)

* **(1) Blind arms do not soft-combine.** New attribute `ns3::NrSpectrumPhy::SlBlindSoftCombining` (default true, other studies unchanged); the platoon flag `--blindSoftCombining` defaults to **false** and applies to every copy sent with HARQ feedback disabled, i.e. R1 and R4. A modelling choice faithful to Kuo's independent-attempt model (3GPP allows combining). Tests: a blind copy's success must not depend on earlier failed copies at the same SINR bin (B01 for R1, C04 for R4; the combined SINR is not logged, so the per-copy SINR bin is the strongest check).
* **(2) R5 helper fallback.** `--opt1HelperNack` (default **true**): in Option 1 with a helper, the named helper's NACK uses a dedicated resource (non-3GPP: Option 1's shared NACK resource cannot identify the NACKer) and the leader reclaims the next handed slot exactly as in R6. `helper_reclaim`, `helper_nack`, `helper_nack_stale` are non-zero for R5. Members other than the helper stay NACK-only on the shared resource; a decoded helper stays silent and relays only after an overheard NACK. R4 stays Kuo's baseline with no reclaim (test C05).
* **(3) Fixed-size leader classes** `periodic1fx` (300 B / 100 ms), `periodic2fx` (1200 B / 10 ms), `aperiodic1fx` (1200 B, aperiodic arrivals), using the `CustomTrafficApplication` `FixedSize` attribute on the leader only. User decision: the periodic2 cells of the campaign move from periodic2tb to **periodic2fx** (synthetic run-sets keep periodic2tb).
* New `_run.csv` columns: `blind_soft_combining`, `opt1_helper_nack`, `leader_fixed_size` (checked by the A04 provenance check: 0, 1, and 1 exactly for *fx classes).
* **Regression (orchestrator, SYN_MARGINAL):** with all new flags off every run is identical to the previous tree; with them on only R1, R4 and R5 change.
* **Profiles** (traffic set {periodic1tb, aperiodic1tb, periodic2fx}): `pilot` = {hwy ivd30, rbt ivd30} x bg {none, moderate (n60, 1.67)} x 3 traffic x 6 arms x seeds 1-2, maxTime 20 for all cells, synthetic run-sets and SPSDYN at the quick spec (144 CORE runs); `reduced` = {hwy ivd15, hwy ivd30, rbt ivd30} x bg {none, moderate} x 3 traffic x 6 arms x seeds 1-3, hwy maxTime 53, roundabout the whole trace (324 runs).
* **Known open issue:** periodic2fx, no background, hwy ivd15 seed 1 trips the one-TB guard (4 "unqueued" multi-TB packets); possibly an eval TB-identity artefact of SPS HARQ-id reuse. Reported as failed runs, not worked around.
* Archives: `tests/out_prefix_v1` (pre-fix), `tests/out_reduced_v3` (previous tree, before this change set).

### 0.4 periodic2fx misfit: root cause and mitigation (orchestrator, 2026-10-19)

* **Not the packet size.** In the misfit runs every NDI=1 TB carries exactly one 1200 B packet in a full 50-PRB TB, and there are no misfit events. The leader `_ltx` shows retransmissions (NDI=0) that carry the next packet (`tx_in_tb` 1, `n_seq` 2). Their number equals `leader_pkt_multi_tb` (194 = 194 in hwy_ivd30_periodic2fx_moderate_s1, R2).
* **Mechanism.** periodic2 has period = RRI = PDB = 10 ms = 20 slots. The selection window is the full PDB counted from the scheduling slot (F23), so TB k's last reserved transmission can land after packet k+1 is generated. The SB-SPS grant keeps its HARQ process, so the MAC sends the new packet in TB k's retransmission slot. One packet is then counted in two TBs, which trips the one-TB guard and builds a backlog.
* **Mitigation (no code change, 3GPP-compliant: T2 ≤ remaining PDB):** in the `reduced` profile, periodic2fx runs with `--t2Ms=9` (T2 = 18 slots).
  * Aborted cell R2 hwy_ivd30_periodic2fx_none_s2: aborts at the default T2, runs clean at 18 slots (0 multi-TB, PRR 0.935).
  * Moderate cell, s1: R2 goes from 194 multi-TB packets to 0, PRR 0.674 → 0.717.
  * Moderate cell, s1, R6: PRR 0.846 → 0.813, leader collision rate 0.65 → 0.76. This is the cost of a narrower window: fewer candidates. It is a single seed.
* The proper fix (pass the remaining PDB measured from packet generation into the scheduler) is a code change that has not been made.
* The pilot outputs predate this mitigation: their periodic2fx cells carry the misfit.

---

## 1. Sources of truth (strict)

1. **3GPP-compliant behaviour** (blind retransmission, Option 1/2, PSFCH, SCI, SB-SPS/dynamic): only the 3GPP specs as summarised in §4, which comes from the 3GPP skill. Items marked **[V]** are recalled clause details. A test that depends on one must label its check `ref: "[V] TS xx.xxx §y"` so the user can audit it.
2. **Custom cooperative-helper design** (R4–R6, S1–S3, H1–H6, SCI-2A coop fields, silent helper): `scratch_overlay/platoon_harq_robustness_eval/README.md`, summarised in §5. It is the reference *for intent*. The code is the reference *for what runs*. Where they disagree (§6.3), the test reports the disagreement.
3. **Stock 5G-LENA behaviour**: `ns-3-dev/contrib/nr/examples/sl-examples/`, `.../nr-v2x-examples/`, `ns-3-dev/contrib/nr/test/nr-sl-*.cc`, `test/sl-test-data/*.reflog`.
4. **Memory notes, HANDOFF.md, SESSION_HANDOFF.md, *_CONCLUSION.md and LOCAL_H_ANALYSIS.md are NOT authoritative.** Do not base an expected value on them.

---

## 2. Rules for the executor (Sonnet)

1. **Do not modify simulator sources.** Never edit `platoon_harq_robustness_eval.cc`, `nack_only_harq.*`, `helper_select.*`, `sumo_buildings.*`, anything in `scratch_overlay/common/`, or `ns-3-dev/contrib/nr/`. The only allowed edit outside `tests/` is one line appended to `scratch_overlay/platoon_harq_robustness_eval/CMakeLists.txt`: `add_subdirectory(tests)`. If a test reveals a bug, record it (status `FAIL`, or `XFAIL` when it is a pre-registered finding from §6). Do not fix it.
2. **Never run `./ns3 configure`**, with ONE exception: the D-7 command and procedure in §0.1, which the user approved. It rewrites the shared CMake cache the user's campaigns depend on. Only `./ns3 build <target…>` or `ninja -C ns-3-dev/cmake-cache <target…>`. The profile is `default` with asserts and logs ON; keep it. Enabling `--enable-tests` for the stock NR test suites is **optional test A03b** and needs explicit user approval. Skip it and mark it `SKIP (needs user approval)`.
3. **No git commits, no pushes, no branch changes.**
4. **Environment hygiene**: every simulation launch must run with `MAGIC_HELPER_ID` and `NUM_MEMBERS` unset (`env -u MAGIC_HELPER_ID -u NUM_MEMBERS …`). The launch script enforces this.
5. **Resources**: the machine has 10 cores, 24 GB RAM and about 32 GB free disk. Default `JOBS=4`. Record peak RSS per run (`/usr/bin/time -l`). Do not start a job when free memory is below 4 GB (`vm_stat`). Gzip every output CSV larger than 1 MB right after its run finishes. Tests read `.csv` or `.csv.gz` transparently.
6. **Resumable**: every simulation run is skipped if `<tag>_run.csv(.gz)` exists and its `.log` ends with the summary line. Every test is rerunnable on its own.
7. **Code understanding**: §6 already holds the needed facts with `file:line` references. Make only targeted reads to confirm signatures. If the Agent tool is available to you and you need broad searching, delegate it to a Haiku subagent. Do not open the docs listed in §1.4 for expected values.
8. **Report honestly**: a check whose expectation you cannot derive from §4 or §5 must be `INFO`, never `PASS`. Do not tune tolerances to make a test pass. If you change a tolerance, write why in the check `note`.
9. **Work order**: follow §12. Run the quick profile end to end (all stages, notebook included) before the full profile.

---

## 3. Terminology: user labels → code flags

The code arms are defined in `platoon_harq_robustness_eval.cc:1601-1624` (presets) and `run_retx.py:70-168` (campaign arms). The README defines H1 as the static middle member, which it calls Kuo's "middle Tx" baseline (README ≈L1224), and H2 as Kuo's max-mean GPRR selection (README ≈L1477-1492).

| Code arm | Leader | Helper | Flags (beyond BASE_FLAGS) |
|---|---|---|---|
| R1 | blind (`harq=0`, broadcast cast type, `maxNumTx` copies via the patched blind path) | none | `--retx=R1` |
| R2 | Option 1 NACK-only | none | `--retx=R2` |
| R3 | Option 2 ACK/NACK, DTX = NACK | none | `--retx=R3` |
| R4_S1_Hx | blind | relays in every handed-over leader slot if it decoded (no feedback) | `--retx=R4 --helperSched=S1 --helperSelect=<static\|kuo> --helper=4` |
| R5_S1_Hx | Option 1 | relays only after an overheard NACK | `--retx=R5 --helperSched=S1 --helperSelect=<…> --helper=4` |
| R6_S1_Hx | Option 2 | named helper never ACKs; relays unless all other members ACKed; leader reclaims a handed slot on the helper's NACK | `--retx=R6 --helperSched=S1 --helperSelect=<…> --helper=4` |

H1 = `--helperSelect=static` (always member 4). H2 = `--helperSelect=kuo` (Kuo eq. 12-13, max-mean, distance-only P_LOS). H5 = `--helperSelect=harqKuo` (Kuo with per-member HARQ statistics; Option 2 only).

**Default interpretation (to be confirmed by the user, §13 Q1):**

* user "R1 + H1 (blind + Kuo)" → **R4_S1_H2**, with **R4_S1_H1** alongside because the README calls static member 4 Kuo's baseline. R1 itself cannot take a helper: `--retx=R1` forces `helperMode=off`, and `--helperSelect≠off` aborts (`platoon_harq_robustness_eval.cc:1813`). Leader-only R1 is kept as the no-helper baseline.
* "Kuo + Option 1" → **R5_S1_H2** (and R5_S1_H1).
* "Kuo + Option 2, per-TB helper" → **R6_S1_H2** (and R6_S1_H1). Also **R6_S1_H5**, the closest thing to a "per-HARQ-process" helper. Note: the code chooses per published grant (NDI slot), and the H5 statistics are per member over a time window across all HARQ processes, not per HARQ process.

**Arms in the campaign (6, per D-1):** R1, R2, R3, R4_S1_H2, R5_S1_H2, R6_S1_H2. Every "H1"/"H5" mention below is superseded by D-1: read H1 as H2 and drop H5.

**BASE_FLAGS**: copy them exactly from `run_retx.py` (around L30-39), and assert in the launch script that they are identical (import `run_retx` and compare). As read:
`--dedicatedCarrier=true --leaderSps=true --spsReselectOnMisfit=true --slDynExclusion=true --slProjectAnnounced=true --slProbResourceKeep=0.8 --rriList=0,10,20,50,100,200,300,400,500,600,700,800,900,1000 --ns3::LteRlcUm::ReorderingTimer=0ms --maxNumTx=3`, plus `--aperiodicRri=20` for `aperiodic1tb` and `--maxTime=53` for highway (as in `run_retx.py:350-371`). Background levels come from `run_retx.py:get_bg_levels`: none = trace `n0`, flows 1.0; moderate = trace `n60`, `--bgFlowsPerCar=1.67`; high = trace `n60`, `--bgFlowsPerCar=3.33`.

**Diagnostics flags added to every test run**: `--writeAtt=true --writeAllTx=true --writeGrants=true` (`writePkt` is on by default). Test A06 must first prove these flags do not change results (finding F04). If they do, see A06.

---

## 4. 3GPP reference workflow (oracle for the B, F and G tests)

### 4.1 RRC parameter ranges (TS 38.331 Rel-16)

| Parameter | Allowed | Study value | Note |
|---|---|---|---|
| sl-PSFCH-Period | {0,1,2,4} slots | 1 | 0 = no PSFCH |
| sl-MinTimeGapPSFCH | {2,3} slots | 3 (`nr_sl_stack.cc:394`) | The NR RRC struct also accepts 4 [V]; the study value must be in {2,3} |
| sl-MaxNumPerReserve | {2,3} | 3 | stack default 1 is non-compliant, but the study overrides it |
| sl-MaxTxTransNumPSSCH | 1..32 | 3 | total transmissions incl. the initial one |
| sl-ProbResourceKeep | {0,0.2,0.4,0.6,0.8} | 0.8 | |
| sl-ResourceReservePeriod | {0,100,200,…,1000} ms and 1..99 ms; list ≤ 16 entries | 0,10,20,50,100…1000 (14) | |
| sl-SensingWindow | {100,1100} ms | 100 | |
| sl-SelectionWindow (T2min) | {1,5,10,20}·2^μ slots | 5 | |
| sl-NumMuxCS-Pair | {1,2,3,6} | not modelled | |
| sl-PSFCH-CandidateResourceType | {startSubCH, allocSubCH} | not modelled | |

### 4.2 SCI (TS 38.212 §8.3.1.1 SCI 1-A, §8.4.1.1 SCI 2-A/2-B)

* SCI 1-A: priority, frequency and time resource assignment (up to N_MAX = sl-MaxNumPerReserve resources, **all within a 32-slot window**, so each gap is 1..31 slots), resource reservation period, DMRS pattern, 2nd-stage format, β-offset, DMRS ports, MCS, additional MCS table, PSFCH overhead indicator.
* SCI 2-A: HARQ process number (4 bit), NDI (1), RV (2), source ID (8), destination ID (16), HARQ-feedback enabled/disabled (1), **cast type (2): 00 broadcast, 01 groupcast with ACK/NACK (Option 2), 10 unicast, 11 groupcast NACK-only (Option 1)**, CSI request (1).
* SCI 2-B: as 2-A without cast type and CSI request, plus zone ID (12 bit) and communication range requirement (4 bit). It enables distance-based Option 1 (NACK only if TX–RX distance ≤ range).
* ns-3 `SidelinkInfo::CastType`: Groupcast=1 (= 3GPP 01, Option 2), GroupcastNegativeOnly=3 (= 3GPP 11, Option 1), Broadcast=0, Unicast=2. A01 checks the mapping.

### 4.3 Blind retransmission (HARQ feedback disabled)

1. The TX selects up to sl-MaxTxTransNumPSSCH resources (initial plus retransmissions) inside the selection window [n+T1, n+T2], with T2 ≤ remaining PDB.
2. All copies are transmitted whatever the reception outcome. No PSFCH is expected, and the SCI-2A feedback indicator is *disabled*.
3. Each SCI 1-A announces up to N_MAX−1 further resources of the same TB, within 32 slots.
4. No minimum gap is needed between copies (no PSFCH round trip).
5. Same TB in every copy (same HARQ process). RV order 0,2,3,1 (implementation; 5G-LENA `GetRv`). NDI marks a new TB: 5G-LENA sets NDI=1 on the first copy and 0 on the retransmissions. That is an implementation convention and acceptable.

### 4.4 HARQ feedback enabled (groupcast)

**Receiver** (for each PSSCH whose SCI-2 it decoded, with the feedback indicator enabled):

* Option 1 (cast 11): send **NACK only if the TB decode failed**, nothing if it succeeded. With SCI 2-B, only if distance ≤ communication range [V for the "position unknown" case]. SCI not decoded → nothing (DTX).
* Option 2 (cast 01): send **ACK if decoded (including a TB already decoded on an earlier copy), NACK otherwise**, on the RX's own PSFCH resource (M_ID = member ID). SCI not decoded → DTX.

**PSFCH timing** (TS 38.213 §16.3): PSFCH goes in **the first slot that contains PSFCH resources and is at least sl-MinTimeGapPSFCH slots of the resource pool after the last slot of the PSSCH reception**. PSFCH slots recur every sl-PSFCH-Period slots of the resource pool. In a PSFCH slot the PSSCH is shortened.

**PSFCH resource** (TS 38.213 §16.3) [V for the exact form]:

* PRBs for PSSCH slot i (0..N−1 within the PSFCH period) and subchannel j: `[(i + j·N)·M_subch,slot, (i+1+j·N)·M_subch,slot − 1]`, where `M_subch,slot = M_PRB,set / (N_subch · N)`.
* `R_PRB,CS = N_type · M_subch,slot · N_CS`.
* Resource index = `(P_ID + M_ID) mod R_PRB,CS`, where P_ID is the SL source ID from SCI-2, M_ID = 0 for unicast and Option 1, and M_ID = member ID for Option 2.
* Cyclic shift: ACK m_cs = 6, NACK m_cs = 0; NACK-only uses m_cs = 0.
* **Option 2 needs group size ≤ R_PRB,CS.** If it is larger, Option 2 cannot be used [V; RAN1 discussion and TS 23.287].

**Transmitter interpretation** (TS 38.213 §16.3/§16.5, TS 38.321 §5.22.1.3) [V]:

* Option 1: NACK detected → NACK; **no NACK detected → ACK (DTX = ACK)**.
* Option 2: **ACK only if ACK is detected on the PSFCH of every expected member; otherwise NACK (DTX = NACK)**.
* On ACK, the TX stops retransmitting. Already announced reserved resources are simply left unused (they stay announced in earlier SCI 1-A). On NACK, it retransmits on the next reserved resource. It stops at sl-MaxTxTransNumPSSCH transmissions or when the PDB expires.
* **Resource selection with feedback enabled** (TS 38.321 §5.22.1.1, TS 38.214 §8.1.4) [V]: consecutive selected resources of a TB leave room for the PSFCH occasion of the earlier one plus processing time (5G-LENA: `IsMinTimeGapSatisfied`, NR scheduler .cc:1998-2013, with MinTimeGapProcessing = 2 slots).

### 4.5 Mode-2 resource selection

* **SB-SPS** (P_rsvp ≠ 0): the selected resource repeats every P_rsvp for SL_RESOURCE_RESELECTION_COUNTER periods. The counter is uniform in **[5,15] for P ≥ 100 ms**, and uniform in **[5·⌈100/max(20,P)⌉, 15·⌈100/max(20,P)⌉] for P < 100 ms**: P=10 or 20 → [25,75]; P=50 → [10,30] (TS 38.321 §5.22.1.1). At counter expiry the resource is kept with probability sl-ProbResourceKeep. C_resel = 10·counter is used for sensing (TS 38.214 §8.1.4 [V]).
* **Sensing**: exclude candidates reserved by others with RSRP above the threshold, and candidates hit by own half-duplex history. Raise the threshold by 3 dB until X% (20/35/50) of candidates remain. 3GPP uses priority-dependent thresholds; 5G-LENA uses one threshold (deviation).
* **Dynamic** (P_rsvp = 0): fresh selection per TB. Others' periodic reservations are still excluded. Up to N_MAX resources in 32 slots are announced.
* Re-evaluation and pre-emption checks (Rel-16 §8.1.4) are not modelled in 5G-LENA (deviation; INFO only).

### 4.6 What 5G-LENA (+patch +wrappers) models differently: expected deviations, not failures

| ID | Deviation | Effect on the study |
|---|---|---|
| DV1 | PSFCH is a logical message: no PRB/cyclic-shift mapping, **no channel errors, no collisions**, perfect overhearing. Only half-duplex loss exists (NR phy `RxSlPsfch` nr-spectrum-phy.cc:2965-2984; `StartRx` drops in TX state 607/645) | Option 2 (8 PSFCH per TB) and NACK-triggered helpers look optimistic |
| DV2 | Stock TX MAC: **one ACK from any member cancels the TB's remaining retransmissions** (nr-sl-ue-mac.cc:2877-2905; nr-sl-ue-mac-harq.cc:221-287) | The study bypasses it: in Option 2 `UserWrap::ReceivePsfch` does not forward member PSFCH to the MAC (nack_only_harq.cc:108-115), and the controller delivers a synthetic ACK only on all-ACK. Verify (B03) |
| DV3 | Stock RX sends ACK *and* NACK for cast 3 (no NACK-only rule, nr-spectrum-phy.cc:2922-2947) | The study fixes it with the MCR NACK-only gate (mcr_nack_only_gate.cc:78-90), which drops ACKs, and drops everything beyond 500 m. Verify (B02) |
| DV4 | Feedback bound only by (txRnti, HARQ id[, dstL2Id]), not by slot/NDI | A late ACK can hit a reused HARQ id (B04/B05) |
| DV5 | Single RSRP threshold, no pre-emption or re-evaluation | INFO |
| DV6 | SCI-2A carries 46 B of coop info that is not charged on air (`GetAirSize()`=5 B) | Helper signalling is free: INFO |
| DV7 | A helper transmits on the leader's reserved resources (S1). Non-standard by design (README) | INFO |
| DV8 | The 32-slot reservation window is not enforced in the code (B16) | Test F04 checks that gaps ≤ 31 slots in practice |
| DV9 | Blind copies for HARQ-disabled LCs come from an **ungated** patch change (scheduler .cc:1830-1832). Stock 5G-LENA sends 1 copy | R1 depends on the patch (A04) |
| DV10 | RLC UM t-Reordering forced to 0 ms | INFO |

---

## 5. Custom helper protocol (README = intent), as tests must check it

* **Selection, per TB**: `NrSlCoopScheduler::HelperFor` (nr_sl_coop_scheduler.cc:1556-1577) runs at each grant publish and calls `HelperSelector::Choose` (main L2148-2161). The result goes into SCI-2A `CoopInfo.helperId` (helper RNTI) for all transmissions of that grant. H1 always returns the fallback, member 4. H2 Kuo: `score(i) = mean_r s_r(i)`, where `s_r(i) = pL[r]` if r == i, else `pL[r] + (1−pL[r])·pL[i]·pRelay`. Tie-break: higher pL[i], then lower index. Candidates are members 1..8 only, never the leader (helper_select.cc:489-575). The link model is distance-only P_LOS plus TR 37.885 PL plus shadowing σ 3/4 dB plus NLOSv blockage integral, against PL_max = 23 dBm − 10log10(nPRB) − (−174+10log10(360 kHz)+9) − 8 dB (helper_select.cc:142-305).
* **S1 (leaderRes)**: the leader reserves initial plus 2 retransmission slots and keeps LeaderTxCount=1. The reserved retransmission slots are "usurped": the leader does not transmit there, and the helper is told `relaySlot[0..1]` in SCI-2A (coop .cc:1690-1705; NR mac 1688-1700, 1875-1938).
* **Helper relay** only if the helper decoded the leader's TB (NR mac 1394-1513). Feedback mode (`HelperFeedbackMode`): 0 = blind (R4: relay in every handed slot), 1 = after an overheard NACK (R5), 2 = unless at least HelperGroupSize (= members−1) ACKs were overheard and no NACK (R6). The relayed copy keeps the leader's HARQ id and source, has the same MCS/size/subchannels, and carries `relayedFromRnti`. Members soft-combine it. The leader ignores relayed copies of its own TB (nr-spectrum-phy.cc:2605-2620).
* **Option 2 silent helper (R6)**: a receiver-side shim suppresses the named helper's ACK when it decoded; its NACK passes (nack_only_harq.cc:574-620). On the helper's NACK the leader calls `ReclaimSlHelperSlot`, retaking the next handed slot (nack_only_harq.cc:392-415; NR mac 2737-2760). Leader success = every non-helper member ACKed and nNack == 0 (nack_only_harq.cc:515-541). The helper's silence counts as "has it". If the helper missed the SCI, it is silent and is never served (README blind spot ≈L1419-1443).
* **Option 1 leader (R2/R5)**: PSFCH occasion m = first PSFCH slot ≥ n + 3; decision at m + 1 slot + 13.5 symbols (nack_only_harq.cc:343-357). No NACK → `DeliverSuccess` gives a synthetic ACK to the MAC, which cancels reserved retransmissions. `SetSpsSafeAck` delivers it only while reserved retransmissions remain in the period (nack_only_harq.cc:477-481).
* **README invariants (must be 0)**: relay_without_nack (R5), relay_without_decode, relay_into_reused_harq, relay_by_unchosen, explicit_ack_rx, helper_ack_rx (R6), leader retx without NACK (Option 1), leader retx after all-ACK (Option 2), phy_unmatched, sfn_offset_mismatch, leader_pkt_multi_tb_unqueued (one-TB classes).

---

## 6. Pre-registered findings from the code reading (tests must CONFIRM or REFUTE)

### 6.1 Findings list

Severity: H = can bias study results; M = wrong in some configurations; L = hygiene. "Test" names the test that settles it. A confirmed finding is `XFAIL` with evidence; a refuted one is `PASS`.

| ID | Sev | Finding | Where | Test |
|---|---|---|---|---|
| F01 | **H** | `res_used_helper` sums transmissions of node `helper`; helper=0 for R1–R3, so it re-counts the **leader**. `res_per_pkt_used`, `eff_member_pkts_per_res` and `platoon_cbr_share` are wrong for the leader-only arms. In hselOn arms, copies by non-default members are missed. **Verified by the orchestrator.** | main.cc:3985-3993 | E01, G03 |
| F02 | **H** | `res_reserved_*` and `res_unused_*` are closed-form estimates, not measurements (`res_per_pkt_reserved` is identical across R4/R5/R6) | main.cc:3997-4020 | G03 |
| F03 | H | `MAGIC_HELPER_ID` env hook forces PSCCH/PSSCH decode success; `std::stoi` throws on bad input | nr-spectrum-phy.cc:2407-2420, 2790-2807 | A05 |
| F04 | H | Channel-condition queries run in `OnPlatoonPsschTx` whenever `g.ccm` is set; `collCond`, `writeAtt` and related flags may change random draws → diagnostics may perturb results | main.cc:580-585, 1421-1422, 1541-1542 | A06 |
| F05 | M | `chosenOfHarq` is keyed by HARQ id (overwritten when a HARQ id is reused); side-1 geometry uses the static `g.helperNode` | main.cc:330, 566-576, 2159 | D02 |
| F06 | M | The selector runs per published grant, including empty SPS periods (aperiodic SPS), so `helper_assigned`, `helper_changes_per_s` and `helper_share_m*` are inflated (≈5× for aperiodic1tb) | main.cc:3978-3993 | D03 |
| F07 | M | R6: silence of a helper that missed the SCI is treated as success, so the helper's own packet is lost (non-3GPP: DTX should be NACK) | nack_only_harq.cc:518-524 | C03, G04 |
| F08 | M | Stale decisions (HARQ id reused before the decision) drop feedback, including the selector's statistics | nack_only_harq.cc:455-461 | B05, G04 |
| F09 | M | H2 (Kuo) ignores vehicle blockage, but the channel applies NLOSv to in-platoon links, so H2 is nearly static (member 3 chosen ≈99% on highway) | helper_select.cc:169-183 vs geometric channel | D02, G05 |
| F10 | M | H4 treats members with no data as perfectly reliable; the named helper's own records are skipped | helper_select.cc:384-386, nack_only_harq.cc:497-501 | (H4 not in campaign; INFO from D01 unit) |
| F11 | M | Blind copies for HARQ-disabled LCs are an ungated patch change (DV9); R1 depends on it | NR scheduler .cc:1830-1836 | A04, B01 |
| F12 | M | Stock "first ACK cancels" (DV2) must be bypassed in R3/R6 | see DV2 | B03 |
| F13 | M | Option-1 members must never send ACK (DV3) | see DV3 | B02 |
| F14 | M | Coop scheduler `GetFutureSfnSf(rri_ms*4)` assumes 4 slots/ms (μ=2), but the study uses μ=1, so its own future-reservation overlap filter is wrong | nr_sl_coop_scheduler.cc:≈1841 | F04 |
| F15 | L | Coop scheduler LCP erase-in-reverse-loop iterator bug; only reachable with several LCs per destination. Expected unreachable in the study (one leader LC) | nr_sl_coop_scheduler.cc:890-945 | F01 (assert 1 LC) |
| F16 | M | `helper_recovered` / `helper_att_ok` count a copy that arrives after the member already decoded through a leader retransmission; `att_*` counts redundant decodes | main.cc:2926-2978 | E01, G05 |
| F17 | M | NACK attribution in the eval uses a hard-coded 5 ms window; unmatched NACKs are dropped silently | main.cc:2417-2469 | B04 |
| F18 | M | R4 gives far fewer copies than maxNumTx−1 per TB (≈34.5% of TBs with no copy in rbt periodic2tb). Unknown whether the helper did not decode or no handed slot existed | — | G01, C02 |
| F19 | L | `--helperSched=S2` (helperSps) is not implemented and schedules like S1 | main.cc:1615, 1854 | not in campaign; INFO note |
| F20 | M | Option 1 + SPS: a synthetic ACK could cancel the next period's TB; guarded by `SetSpsSafeAck` | nack_only_harq.cc:477-481 | B06 |
| F21 | L | PSFCH min gap 3 is a literal in the controller (main.cc:1995, 2052) and in the stack (nr_sl_stack.cc:394) | — | A02 |
| F22 | L | `RemoveOldSensingData` / `RemoveOldTransmitHistory` erase-then-++ skips elements (stale SCIs survive one slot) | NR mac 1070-1115 | INFO (no ASan build) |
| F23 | M | The PDB must bound every copy (T2 ≤ PDB). periodic2tb has PDB 10 ms = 20 slots and a HARQ round trip of ≥ 3+2 slots | NR mac 412-434 | F04 |
| F24 | L | "GPRR" means predicted (selector) in one place and measured mean PRR in another | — | notebook note |
| F25 | M | Option 2 RX must re-ACK a TB it already decoded when a retransmission arrives (otherwise DTX → NACK → wasted retransmissions). Not verified in code | nr-spectrum-phy.cc:2922-2947 | B03 |
| F26 | M | One-TB guard aborts only when numBg == 0 | main.cc:2760-2763 | E05 |
| F27 | L | Blind arms keep psfchPeriod=1 (PSFCH symbols reserved but unused); coop arms abort with psfchPeriod=0 | main.cc:1683 | INFO in G03 |
| F28 | M | `relay_needed` = SCHEDULED + NO_NACK (opportunities, not needs); `helper_drop_rate` divides a selection count by a decode count | main.cc:3980-3981 | G05 (recompute) |
| F29 | M | Stock MAC feedback binding has no NDI/slot check (DV4) | NR mac 2877-2905 | B04 micro |

### 6.2 Facts the executor needs (verified file locations)

**Build**

* `scratch_overlay/platoon_harq_robustness_eval/CMakeLists.txt` uses `build_exec(EXECNAME … EXECNAME_PREFIX scratch_platoon_harq_robustness_eval_ SOURCE_FILES … LIBRARIES_TO_LINK scratch_common "${ns3-libs}" "${ns3-contrib-libs}" EXECUTABLE_DIRECTORY_PATH ${CMAKE_OUTPUT_DIRECTORY}/scratch/platoon_harq_robustness_eval/)`. The macro is defined in `ns-3-dev/build-support/custom-modules/ns3-executables.cmake:70`. The binary is `ns-3-dev/build/scratch/platoon_harq_robustness_eval/ns3.42-<EXECNAME>-default` (RPATH set, so no DYLD vars are needed).
* For the new tests, use one `build_exec` per test with an explicit `EXECNAME test_<ID>_<name>` (no dots), output dir `${CMAKE_OUTPUT_DIRECTORY}/scratch/platoon_harq_robustness_eval/tests/`. Source files are `<ID>_<name>.test.cc` plus the harness `.cc` files, plus `../helper_select.cc` / `../nack_only_harq.cc` / `../sumo_buildings.cc` only where needed. Ninja target name: `scratch_platoon_harq_robustness_eval_test_<ID>_<name>`.
* Build: `cd ns-3-dev && ./ns3 build scratch_platoon_harq_robustness_eval_platoon_harq_robustness_eval <test targets>` (adding .cc files triggers an automatic reconfigure, which is expected and fine). Rebuild the production binary first: its sources may be newer than the Oct-15 binary.
* `NS3_TESTS=OFF`, so `./ns3 test` will not run these. Each test is a standalone `main()` that returns 0 (no FAIL), 1 (FAIL) or 77 (all SKIP).
* Existing test pattern: `test_helper_select.cc` (plain main, `TEST_ASSERT` macro, 65 assertions, passes today) and `common/psfch_kpi/test_psfch_kpi.cc`.

**Main binary**: CLI flags are listed at main.cc:1479-1591. Required: `--scenario {highway,roundabout} --mobility <trace.csv.gz> --traffic <class> --seed --run --out <prefix>`. Roundabout also needs `--sumoNet=…/sumo/roundabout/roundabout.net.xml`. `run_retx.py` passes `--seed=s --run=s`.

**Traces**: `scratch_overlay/platoon_harq_robustness_eval/traces/<hwy|rbt>_ivd<IVD>_n<NBG>_s<SEED>.csv.gz`, each with a `.json` sidecar (`kpi_start`, `veh_len_m`, `dt`=0.1, …). IVD ∈ {5,15,20,30,60}, NBG ∈ {0,15,30,45,60}, seeds 1..10 (not every combination exists, so check). The loader is main.cc:91-170: `#key=value` meta lines, then rows. Read it to learn the exact column order before writing synthetic traces. Node 0 = leader, nodes 1..8 = members, ≥9 = background.

**Outputs per run** (`<out>_*`):

* `_run.csv`: one wide row, ~276 columns.
* `_member.csv`: rows for members 1..8 plus `all`. Columns: `member,sent,rx,prr,prr_pdb,late,lost_collision,lost_channel,lost_notx,lost_other,…,mean_delay_ms,delay_p50/p95/p99_ms,attempts,att_ok,att_corrupt,att_missed,mean_sinr_db,mean_dist_m,frac_los,frac_nlosv,frac_nlos`.
* `_pkt.csv`: `seq,t_tx,size,member,rx,delay_ms,cause,n_att,n_att_coll,n_att_fail,dist_m,cond,best_sinr_db`; cause 0 rx, 1 coll, 2 chan, 3 notx, 4 other.
* `_ltx.csv` / `_htx.csv`: `t,slot_abs,rb_start,rb_len,harq_id,ndi,rv,tx_in_tb,n_seq,n_nack,decision,n_coll,nearest_collider_m,n_nack_sent`; decision −1 pending, 0 NACK/keep, 1 ACK, 2 stale.
* `_nack.csv`: `t,member,tx_rnti,harq_id` (time the NACK was generated at the RX, i.e. PSSCH decode time; whole run).
* `_att.csv`: `side,t,slot_abs,tb,tx_in_tb,ndi,member,status,sinr_db,cond,dist_m,geo_*`; side 0 leader, 1 relayed copy; status 0 no decode attempt (SCI missed), 1 ok, 2 corrupt (soft-combined result); one row per (platoon TX, member), **the helper's own decode is the row with member == helper**.
* `_alltx.csv`: `t,node,slot_abs,rb_start,rb_len,harq_id,ndi,rv,dst,x,y` (every PSSCH of every UE; dst 254 = platoon). Join `_att` side 1 with `_alltx` on `slot_abs` to learn which member relayed.
* `_grants.csv`: `t,node,event,sps,rri_ms,first_slot,sb_start,sb_len,slots_or_counter,bytes,extra,reselected`; events c = created, k = kept, m = misfit; leader (node 0) and background only.
* `_hsel.csv` (hselOn): `t_decide,t_ndi,decide_to_ndi_ms,cam_age_ms,harq_id,n_subch,pl_max_db,chosen,applied,score_m1..m8`.
* `_coll.csv`, `_div.csv`: optional.

**KPI window**: [kpi_start (3 s), t_end − 0.3 s). PDB: periodic1 100 ms, periodic2 10 ms, aperiodic1 50 ms. Slot = 0.5 ms (μ=1). TB max = 1333 B (MCS 14, 5×10 PRB); leader overhead 37 B.

**Traffic classes** (custom-traffic-application.cc:44-61, 396-452; `tb` variants map to the base class, and `aperiodic1tb` sets `Aperiodic1MaxSize=1200` on the leader):

* periodic1: 100 ms, sizes cycle {300,190,190,190,190} (from a random start).
* periodic2: 10 ms, 1200 B with p=0.2, else 800 B.
* aperiodic1: inter-arrival 50 ms + ⌊Exp(mean 50 ms)⌋, size uniform over {200,400,…,max}.
* Sizes are wire bytes including the 20 B SeqTsSize header.

**In-process micro scenario recipe** (stock 5G-LENA, from `contrib/nr/examples/sl-examples/sl-harq-example.cc:289-752` and `test/nr-sl-harq-test.cc`):

* NrPointToPointEpcHelper + NrHelper; band `CcBwpCreator::SimpleOperationBandConf(5.89e9, BW, 1, …)`.
* `SetUeMacTypeId(NrSlUeMac)` and `SetBwpManagerTypeId(NrSlBwpManagerUe)` before `InstallUeDevice`, then `UpdateConfig`.
* NrSlHelper: `SetSlErrorModel("ns3::NrEesmIrT1")`, AMC ErrorModel, scheduler FixedMcs; `PrepareUeForSidelink` before `InstallNrSlPreConfiguration`.
* Pool via `NrSlCommResourcePoolFactory` (`SetSlPsfchPeriod`, `SetSlMinTimeGapPsfch`, `SetSlMaxNumPerReserve`, RRI list) and pre-config with `slMaxTxTransNumPssch`.
* `SidelinkInfo`: **castType defaults to Invalid, harqEnabled to false, dynamic to false (SPS), rri to 0 → set all of them.**
* `ActivateNrSlBearer` with TRANSMIT tft on the TX and RECEIVE on the RXs; OnOff app with SeqTsSize header.
* Hooks:
  * `NrSlUeMacHarq` traces `RxHarqFeedback(SlHarqInfo)`, `Allocate`, `Deallocate`, `RequestPacketBurst`, `Timeout`.
  * `NrSpectrumPhy` traces `TxFeedbackTrace(Time)`, `RxPsschTraceUe(SlRxDataPacketTraceParams)`, `RxPscchTraceUe`, `SlTbDecodeFailure`.
  * `NrSlUeMac` traces `SlPsschScheduling(SlPsschUeMacStatParameters)`, `SlPscchScheduling`.
  * Feedback content per RX: wrap `NrSpectrumPhy::SetPhySlHarqFeedbackCallback` (nr-spectrum-phy.cc:294-297, 3063-3066), or follow `nack_only_harq.cc:574-620`.
* Deterministic error injection pattern: `SlHarqTestErrorModel` (nr-sl-harq-test.cc:115-176). A cleaner option for the tests: a test-only `NrErrorModel` subclass, registered with its own TypeId and set via `SetSlErrorModel`, that returns TBLER 1 for data MCS when the RX node is in a "fail set" and 0 otherwise. It needs to know the RX node: wrap per-PHY, or key on SINR thresholds with deterministic geometry. Pick whichever works and document it.
* Pool function `NrSlCommResourcePool::SlotHasPsfch` (src/lte/model/nr-sl-comm-resource-pool.cc:191-222).

**NR example regression targets**: `sl-harq-example`, `sl-multi-lc-example`, `cttc-nr-v2x-demo-simple` (ninja targets exist; binaries are from Oct 8 and stale, so rebuild them). Reflogs are in `contrib/nr/test/sl-test-data/`. Predicted by code reading: `sl-multi-lc-dyn-gcast-no-harq` reflog **differs** (DV9: "no-harq:1" becomes up to 5 copies); harq/blind/prio/rri reflogs match.

### 6.3 README vs code disagreements to report (INFO in the notebook)

* README says `--option1Tx=false` keeps the flow HARQ-enabled with PSFCH. The code sets `harq=0` (broadcast, HARQ disabled) (main.cc:1909-1912).
* S2 is described in the README but not implemented (F19).
* run_coop "kuoBlind / s4Blind" are an Option-1 leader with a blind helper, not R4.
* The README top section describes the shared 9/20 pool with dynamic leader grants; the R/H/S campaign uses a dedicated carrier with an SPS leader.
* The RRI list has 28 values in the code, not 27.

---

## 7. Test infrastructure

### 7.1 Layout

```
tests/
  TEST_PLAN.md                  (this file)
  CMakeLists.txt                (one build_exec per test)
  run_tests.sh                  (launch script, §10)
  .gitignore                    (out/, results/*.json are fine to ignore or keep: ignore out/)
  common/
    harness.h / harness.cc      (TestReport, checks, JSON writer, exit codes)
    csv.h / csv.cc              (CSV/CSV.GZ reader by column name)
    stats.h / stats.cc          (mean/CI-t, Wilson, Spearman ρ + p, Welch, paired bootstrap, binomial)
    run_outputs.h / .cc         (load a run's CSVs; TB reconstruction; joins att↔alltx↔hsel↔ltx/htx)
    runsets.h / .cc             (enumerate runs of a run-set from tests/out/<runset>/manifest.csv)
    synth_trace.h / .cc         (write synthetic static/linear traces in LoadTrace format + .json sidecar)
    micro_sl.h / micro_sl.cc    (in-process stock-NR groupcast builder + event log)
  campaign/
    runsets.py                  (single source of truth for run-sets: arms × cells × seeds; imports run_retx.py for BASE_FLAGS and bg levels)
    run_matrix.py               (parallel, resumable runner; gzip; RSS log; manifest.csv)
    make_synth_traces.py        (optional Python twin of synth_trace if easier; must match LoadTrace)
  unit/        A01, A02 (in-process), D01
  workflow/    A03..A06, B01..B06, C01..C04 (production binary on synthetic traces + micro_sl)
  campaign_checks/ D02, D03, E01..E06, F01..F04, G01..G06
  notebook/
    build_notebook.py           (nbformat; writes TEST_RESULTS.ipynb)
  TEST_RESULTS.ipynb            (generated and executed)
  out/                          (simulation outputs per run-set; git-ignored)
  results/                      (one JSON per test + CSV artefacts)
```

The flat layout `tests/<ID>_<name>.test.cc` is also acceptable if subfolders complicate CMake. The file names must be `<ID>_<name>.test.cc`.

### 7.2 Result schema (`results/<ID>_<name>.json`)

```json
{"id":"G01","name":"blind_effectiveness","group":"G","status":"PASS|FAIL|XFAIL|SKIP|INFO",
 "summary":"one sentence", "ref":["TS 38.321 §5.22.1.3 [V]","README §..."],
 "runsets":["CORE"], "n_runs":120, "wall_s":12.3,
 "checks":[{"name":"...","status":"PASS|FAIL|XFAIL|SKIP|INFO","expected":"...","observed":"...",
            "tolerance":"...","severity":"H|M|L","finding":"F18|null","ref":"...","note":"..."}],
 "metrics":{...}, "artefacts":["results/G01_ineffective_retx_worst.csv", "..."]}
```

Test status = worst of its checks (FAIL > XFAIL > PASS > INFO > SKIP). XFAIL is allowed **only** for checks tied to a finding ID in §6.1 or a deviation ID in §4.6, and the note must hold the evidence (numbers).

### 7.3 Statistics conventions

* Per-cell estimates use mean over seeds ± 95% t-CI. With n<3 seeds, report the value and mark the comparison `INFO` (no verdict).
* Proportions pooled over TBs or packets use Wilson 95% CI.
* Arm comparisons use the **paired difference per (cell, seed)** with the same trace. It is paired in geometry, not in RNG path, so per-TB pairing is impossible. Report the mean diff and t-CI; when n ≥ 5, also give a sign test.
* Monotonicity uses Spearman ρ with p-value. A directional check passes if ρ has the expected sign and p < 0.05, or |ρ| ≥ 0.6. Otherwise it is INFO, not FAIL (statistical power).
* Exact invariants (counts that must be 0, identities) have tolerance 0. They are FAIL on any violation and list up to 20 offending rows in the artefact CSV.

### 7.4 Run-sets (defined in `campaign/runsets.py`)

**Synthetic run-sets** use the production binary on synthetic traces (scenario `highway`, no background, 20 s run with `kpiStart=3`, so `maxTime` = trace end). They give controlled geometry and exact expectations:

| Run-set | Geometry (static unless noted) | Purpose | Arms |
|---|---|---|---|
| SYN_NEAR | leader + 8 members in a line, centre spacing 10 m (gap 5 m) | everything decodes: exact workflow counts | R1,R2,R3,R4H1,R5H1,R6H1 |
| SYN_MARGINAL | spacing 60 m (tail ≈ 480 m) | frequent failures for far members: per-event invariants | all 10 arms |
| SYN_DTX | members 1..7 at 10 m spacing; member 8 at 3000 m; `--maxLossDb=110` so member 8 receives nothing (verify PL(3000 m) > 110 dB and PL(70 m) ≪ 110 dB) | DTX semantics, Option 1 vs 2 | R1,R2,R3,R5H1,R6H1 |
| SYN_MCR | members 1..7 at 10 m; member 8 at 650 m (> MCR 500 m, still decodable sometimes) | Option-1 range gating vs Option 2 | R2,R3 |
| SYN_DIST | spacing ∈ {10,20,30,45,60,80} m | PRR/SINR vs distance curves | R1,R2,R3,R4H2,R5H2,R6H2 |
| SYN_SPS | as SYN_MARGINAL, `--leaderSps` true vs false, periodic1tb and aperiodic1tb | SPS vs dynamic exact behaviour | R2,R6H1 |
| DET | real trace hwy_ivd30_n60_s1 moderate, periodic1tb | determinism and diagnostic neutrality | R2,R6H2 |

Synthetic traffic: periodic1tb and aperiodic1tb for all; add periodic2tb for SYN_NEAR and SYN_MARGINAL (PDB 10 ms stress). Seeds 1..3.

**Real-trace run-sets:**

| Run-set | Cells | Arms | Seeds |
|---|---|---|---|
| CORE (full) | {hwy ivd5, hwy ivd15, hwy ivd30, rbt ivd30} × bg {none, moderate, high} × traffic {periodic1tb, aperiodic1tb, periodic2tb} | 10 arms | 1..5 (periodic2tb: 1..3) |
| CORE (quick) | {hwy ivd30, rbt ivd30} × {none, high} × {periodic1tb, aperiodic1tb} | 10 arms | 1..2 |
| SPSDYN (full) | {hwy ivd15, hwy ivd30} × 3 bg × 3 traffic, `--leaderSps=false` | R1,R2,R3,R4H2,R5H2,R6H2 | 1..3 |
| SPSDYN (quick) | hwy ivd30 × {none, high} × periodic1tb | same | 1..2 |

Before scheduling, `runsets.py` must check that each trace file exists. Drop missing combinations and log them in `out/<runset>/missing.txt`. The notebook shows them.

Per-run cost observed: periodic1tb/aperiodic1tb ≈ 9–16 s, periodic2tb ≈ 34–41 s (default build, 53 s highway). The full profile is ≈ 1,800 runs, about 2–2.5 h at JOBS=4. The quick profile takes about 10 min.

---

## 8. Test catalogue

Each test lists: **type** (U = unit in-process, M = in-process micro NR scenario, W = workflow on synthetic run-sets, C = checks on real-trace run-sets), **inputs**, **checks** (exact criteria), and **artefacts** for the notebook.

### Group A — NR stack, patch and harness integrity

**A01_rrc_sci_compliance** (U)

* Inputs: none (constructs headers and pool objects).
* Checks:
  1. The study configuration values (pull defaults from `SlStackParams` plus the study flags, as a table hard-coded from BASE_FLAGS) lie in the 3GPP sets of §4.1. Out-of-set values FAIL, except values the study overrides (note).
  2. Every RRI in `--rriList` is a legal TS 38.331 value (0, 1..99, 100..1000 step 100). The list has ≤ 16 entries.
  3. NrSlSciF1aHeader round-trip for maxNumPerReserve 2 and 3, gaps 1..31. Report whether the header accepts gap ≥ 32 (DV8 → INFO).
  4. NrSlSciF2aHeader round-trip for cast types 0,1,2,3 and HARQ id 0..15. A HARQ id of 16 masks to 0 → INFO. CastType numeric values match 3GPP 2-bit codes (Groupcast=1 ↔ 01 ACK/NACK, GroupcastNegativeOnly=3 ↔ 11 NACK-only).
  5. With CoopInfo set: `GetSerializedSize() == 5+46` and `GetAirSize() == 5` → XFAIL(DV6) documented.
* Artefact: table of parameters vs allowed sets.

**A02_psfch_timing** (U + M)

* (U) `NrSlCommResourcePool::SlotHasPsfch` for periods 1, 2, 4 on the study's dedicated-carrier pool and on the shared TDD pool. PSFCH slots must recur every N **SL slots** (logical).
* (U) Re-derive the occasion `m(n) = first PSFCH slot ≥ n + K` (K = 3) in logical slots, and compare with the controller's arithmetic (nack_only_harq.cc:343-357; copy the formula into the test and compare it with an independent implementation over 10,000 random n). With the dedicated carrier, logical = physical. With the shared pool, report whether ns-3 counts physical slots (`NrUePhy::SlFeedback`, nr-ue-phy.cc:1959-1975), which 3GPP does not ([V] logical) → INFO with numbers.
* (M) Micro scenario: 1 TX + 3 RX groupcast, HARQ on, Option 2 cast (`Groupcast`), psfchPeriod ∈ {1,2,4}, K = 3, dynamic grants, 100 packets. For every PSSCH reception at RX r in slot n (RxPsschTraceUe), the RX's PSFCH transmission (TxFeedbackTrace at r) happens exactly in slot m(n). Its content (ACK/NACK from the feedback-callback wrapper) equals the decode outcome (`!corrupt && !sci2Corrupt`). The TX receives it (RxHarqFeedback) with the same HARQ id as that PSSCH. Pass = 100% match.
* Check F21: the controller literal 3 == the pool's `GetMinTimeGapPsfch()` for the study pool.
* Artefact: per-period histogram of (PSFCH slot − PSSCH slot).

**A03_nr_examples_regression** (W, external binaries)

* Build `sl-harq-example` and `sl-multi-lc-example` (`./ns3 build sl-harq-example sl-multi-lc-example`).
* Run the command lines encoded in `contrib/nr/test/nr-sl-simple-multi-lc-example-test.cc` (the example-as-test registrations) and diff stdout+stderr against `sl-test-data/*.reflog`.
* Expected: the HARQ / blind / prio / rri reflogs match; `sl-multi-lc-dyn-gcast-no-harq` differs because of DV9 → XFAIL(F11) with the diff excerpt. Any other diff → FAIL.
* `sl-harq-example --castType=groupcast` with `--harqEnabled={0,1}` and psfch {0,4}: count TX copies per TB from its verbose output, or by attaching to the SQLite DB if available. Expected: HARQ on + psfch 4 → copies ≤ 5 with retransmissions only after NACK; HARQ on + psfch 0 → 5 blind copies; HARQ off → 5 copies (patched) vs 1 (stock) → XFAIL(F11).
* **A03b** (optional, SKIP unless the user approves reconfiguring with `--enable-tests`): `./ns3 test --suite=nr-test-sidelink-harq`, `nr-sl-sensing`, `nr-sl-sci-headers`.

**A04_patch_gating** (W: SYN_NEAR, SYN_MARGINAL)

* R1: every leader TB has exactly `maxNumTx` = 3 PSSCH transmissions with the same HARQ id and `rb_len`. If fewer, report the count distribution and FAIL unless the shortfall is explained by the PDB (periodic2tb: the selection window is too short; record it).
* R1: `_nack.csv` is empty, no ACK/NACK counters, `nack_rx=0`, `implicit_ack=0` (feedback disabled).
* R1 vs R2 on SYN_NEAR: identical `tb_max_b`, `payload_max_b` and `n_subch` distribution (the slot format is the same because psfchPeriod=1 in both, F27).
* `helperMode=off` vs `helperMode=none` (R2 flags + `--helperMode=none`): same PRR within CI (different scheduler class, both without cooperation) → INFO with the diff.

**A05_env_guard** (U)

* `getenv("MAGIC_HELPER_ID") == nullptr` and `getenv("NUM_MEMBERS") == nullptr` in the test process (the launch script unsets them; this checks the script).
* Grep `ns-3-dev/contrib/nr/model/nr-spectrum-phy.cc` for `MAGIC_HELPER_ID`. If present → XFAIL(F03) with line numbers (hook exists but is inactive in this run).
* Run SYN_NEAR R6H1 once with `MAGIC_HELPER_ID=4` set (only here, explicitly) and confirm the outputs differ from the unset run (proves the hook is live). Then confirm that every other run-set manifest recorded the unset env.

**A06_determinism** (W: DET)

1. Same config, same seed, run twice → byte-identical `_run.csv` (except `wall_s`, and any column documented as wall-clock), `_member.csv`, `_pkt.csv`, `_ltx.csv`. FAIL on any difference.
2. Diagnostics neutrality: run with `--writeAtt/--writeAllTx/--writeGrants` all false vs all true → `_member.csv` and the KPI columns of `_run.csv` must be identical.
   * If they differ → FAIL(F04). In that case the campaign must also be run **without** diagnostics for the KPI comparisons (G02/G03 use diag-off KPIs and diag-on logs only for attribution). Tell the orchestrator in the final report.
3. Same check for `--collCond=true` → INFO if it differs (documented as RNG-affecting).

### Group B — 3GPP retransmission workflow

**Common TB reconstruction** (in `run_outputs`): a TB = leader `_ltx` rows from an NDI=1 row through the following rows with the same `harq_id` and `tx_in_tb` 2,3…, plus `_htx` copies mapped to it by the eval's `tb` column in `_att`. Attempts are ordered by `t`. Per member, take the status sequence from `_att`.

**B01_blind_workflow** (W: SYN_NEAR, SYN_MARGINAL; R1, R4H1)

* R1: per TB, leader transmissions == maxNumTx (see A04). NDI sequence 1,0,0. RV sequence ⊂ (0,2,3,1) in order. All copies in distinct slots, inside [t_gen, t_gen + PDB]. The SCI-1A time gaps implied by `slot_abs` differences ≤ 31 within each reservation group of `maxNumPerReserve` (DV8 check).
* R1: copies are sent even when every member decoded the first one (SYN_NEAR: 100% of TBs have 3 transmissions while all members decode at attempt 1). That is the blind definition.
* R4H1: leader transmits once per TB (`tx_in_tb==1` only). The helper (member 4) sends exactly 2 copies per TB **iff** it decoded the initial transmission (status 1 in `_att` side 0 for member 4). Otherwise 0 copies. Copies use the slots the leader reserved (S1): no leader PSSCH in those slots. FAIL on any TB violating the iff. On SYN_MARGINAL, count TBs whose copies are missing although the helper decoded → separates F18 causes.

**B02_option1_workflow** (W: SYN_NEAR, SYN_MARGINAL, SYN_DTX, SYN_MCR; R2, R5H1)

* SYN_NEAR R2: 0 NACKs. Every TB has 1 transmission and decision 1 (implicit ACK). `implicit_ack == #TBs` (KPI-window-matched). `explicit_ack_rx == 0` (members never ACK; F13).
* SYN_MARGINAL R2, per leader transmission k of a TB:
  * (a) If any member NACKed transmission k (per `_nack.csv`, attributed by HARQ id and the PSSCH slot of k), then transmission k+1 exists if k < maxNumTx (no missed retransmission). FAIL lists exceptions; allowed exceptions are PDB expiry and stale.
  * (b) If no member NACKed k, transmission k+1 does not exist (no retransmission without NACK).
  * (c) A NACK is only sent by members with status 2 (corrupt) for that transmission and within MCR. A member with status 1 never NACKs; status 0 never sends feedback.
  * (d) decision == 0 ⇔ n_nack > 0.
* SYN_DTX R2: member 8 status 0 on every attempt. Member 8 never NACKs. TBs end after 1 transmission whenever members 1..7 decode. Member 8 PRR ≈ 0. Pass = 3GPP Option-1 semantics (DTX = ACK) reproduced; record it as an *inherent Option-1 property*, not a bug.
* SYN_MCR R2: member 8 (650 m) sends 0 NACKs even with status 2 (gate drops beyond 500 m). Its failures trigger no retransmission. The R3 control on the same geometry does send member-8 NACKs (Option 2 has no range gate).
* R5H1 on SYN_MARGINAL: same leader checks, plus C02 relay gating.

**B03_option2_workflow** (W: SYN_NEAR, SYN_MARGINAL, SYN_DTX; R3, R6H1) and (M)

* SYN_NEAR R3: every member ACKs each TB's first transmission. decision 1 after transmission 1. 1 transmission per TB. `nAllAckDecision == #TBs`.
* SYN_MARGINAL R3, per transmission k: decision 1 ⇔ every member has decoded the TB by transmission k (cumulative status, from `_att`) **and** sent an ACK. Re-ACK check (F25): if a member decoded at k' < k and the leader retransmits at k, that member must ACK transmission k again. Measure the fraction of retransmissions where an already-decoded member was silent. If > 0 → FAIL(F25): wasted Option-2 retransmissions.
* SYN_DTX R3: member 8 DTX on every TB → every TB uses all maxNumTx = 3 transmissions (DTX = NACK). The wasted resources are quantified (this is 3GPP-correct behaviour and is reported as the cost of Option 2 with an unreachable member).
* (M) Stock-MAC characterisation (DV2/F12): micro scenario with HARQ on, Groupcast cast type, RX1 always OK and RX2 always failing (error injection). Expected in stock: RX1's ACK cancels the remaining retransmissions → XFAIL(DV2) documenting stock behaviour. Then verify in SYN_MARGINAL R3 that the study bypass works: no TB is ended (decision ≠ 0 and no further transmission) while some member's last feedback was NACK or DTX and transmissions remain.

**B04_feedback_binding** (W: SYN_MARGINAL, CORE quick; R2, R3, R5H2, R6H2) and (M)

* Every `_nack.csv` row (member, tx_rnti, harq_id, t) must map to exactly one platoon PSSCH (leader or helper copy, same HARQ id), whose slot end is within 1 slot before `t`. Report the unmatched fraction (must be 0) and the ambiguous fraction (two candidate TBs with the same HARQ id within 32 slots; must be 0).
* The NACK's TB must be the one whose decision counted it: n_nack of that transmission ≥ 1. A mismatch → FAIL.
* Compare with the eval's own 5 ms attribution (F17). Count NACKs that the eval dropped.
* (M) Micro: the stock MAC binds by (txRnti, harqId). Build a case where a late ACK for TB A arrives after HARQ id reuse by TB B (dynamic, short PDB). Characterise whether B's retransmissions get cancelled → XFAIL(DV4/F29) if they do.

**B05_harq_process_management** (W: SYN_MARGINAL, CORE quick)

* HARQ ids ∈ [0,15]. No two TBs active at the same time with the same HARQ id (a TB is active from its first transmission to max(last transmission, decision time)). Per TB, transmissions ≤ maxNumTx, and leader + helper copies ≤ maxNumTx. A new NDI=1 on a HARQ id only after the previous TB on it ended.
* Stale decisions (decision 2) count per arm; F08 → INFO with rate.
* `chosenOfHarq` overwrite risk (F05): count HARQ ids whose `_hsel` row changes between a TB's NDI and its last copy.

**B06_sps_harq_interaction** (W: SYN_SPS; C: CORE)

* Re-implement `check_sps.py` (retx_after_ack == 0, retx_without_nack == 0 for Option 1; periods kept at +RRI). Add: a synthetic ACK never cancels the next period's TB (F20). The next period's NDI=1 transmission exists for every SPS period that had data.

### Group C — Cooperative helper protocol (README reference)

**C01_helper_signalling** (W: SYN_NEAR, SYN_MARGINAL; C: CORE)

* For each TB of an hselOn arm, the `_hsel` row (matched on `t_ndi` = the NDI=1 transmission time, with tolerance 0.5 slot) gives `applied`. Every relayed copy of that TB (join `_att` side 1 ↔ `_alltx` on slot_abs, dst 254, node ≠ 0) comes from node `applied`. `relay_by_unchosen == 0`, recomputed.
* Copy vs original: same `harq_id`, same `rb_len`, NDI=0. No two platoon PSSCH of the same TB in one slot. The leader never transmits in a slot where its TB's copy is sent (S1 usurp).
* Static H1: every copy comes from member 4.

**C02_relay_gating** (W: SYN_MARGINAL; C: CORE)

* R4: copies of TB ⇔ the helper decoded the leader's initial transmission (status 1 for the helper member, side 0, tx_in_tb 1). Copies per TB == number of handed slots (2) unless COOP_RELAY_TOO_LATE. Recompute `relay_without_decode == 0`.
* R5: each copy follows ≥ 1 NACK on the previous transmission of the TB (leader or copy) at its PSFCH occasion. `relay_without_nack == 0` recomputed. Copies are cancelled (coop NO_NACK) when no NACK.
* R6: a copy is sent ⇔ not (all non-helper members ACKed the previous transmission and no NACK). `relay_after_allack == 0` recomputed.
* Report the distribution of copies per TB per arm.

**C03_option2_helper_silence** (W: SYN_NEAR, SYN_MARGINAL, SYN_DTX; C: CORE R6H1/R6H2)

* The named helper never ACKs: `helper_ack_rx == 0`. `helper_ack_suppressed` == the number of (transmission, helper) decodes with status 1 for TBs named to that helper. Recompute from `_att` + `_hsel`; tolerance 0.
* The helper NACK (from `_nack.csv`, member == applied helper) triggers a reclaim: `helper_reclaim + helper_nack_no_slot == #helper NACKs` (window-matched). After a reclaim the leader transmits in the reclaimed slot (a leader PSSCH exists at the next handed slot).
* Leader success rule: for every transmission with decision 1, all non-helper members decoded by then (cumulative status). FAIL on any false ACK.
* Blind spot (F07): TBs where the helper had status 0 (SCI missed) and the leader declared success → the helper's packet is lost unless a later copy or transmission arrives. Report the rate and the helper's PRR vs the other members' PRR. This is not a FAIL unless the helper's PRR is below that of the worst non-helper member.

**C04_relay_combining** (W: SYN_MARGINAL)

* Members that failed the leader initial transmission (status 2) and succeed on a copy: success counted once at the app level (`_pkt.rx` = 1, no duplicates: sum rx ≤ sent per member).
* The leader never logs reception of its own relayed copy. A copy is never counted as a leader transmission in `_ltx`.
* Soft-combining evidence: P(success on copy | failed initial) > P(success on initial | same SINR bin) → INFO (directional).

### Group D — Helper selection

**D01_kuo_objective** (U, links `../helper_select.cc`)

* Independent re-implementation in the test of Kuo eq. 12–13 with the same link model formulas (§5). For 200 random synthetic platoon geometries (spacing 5–80 m, with and without one background blocker on the line), compute the scores of every candidate and compare with `HelperSelector` (drive it as `test_helper_select.cc` does: CAM snapshots plus Choose). `max |score_test − score_sel| < 1e-3`, `chosen == argmax` with the tie-break rules. FAIL otherwise.
* Properties: the score is non-decreasing in pRelay and in pL[i]. kuoObjective=min gives a value ≤ the mean. The leader is never chosen. H1 always returns the fallback. Shadow mode returns applied == fallback and chosen == the non-shadow choice.
* H4 note (F10): with feedback only for some members, demonstrate that a member with no data is treated as failRate 0 → INFO.
* Also run the existing `test_helper_select` binary and include its pass count.

**D02_selection_invariants** (C: CORE hselOn arms; W: SYN_DIST)

* `chosen ∈ [1,8]`. `applied == chosen` (no shadow). `cam_age_ms ≥ helperDecisionMs`. Each NDI=1 leader TB has exactly one `_hsel` row (at `t_ndi`).
* SYN_DIST: the chosen helper equals D01's independent argmax for the known static positions (the score rows in `_hsel` match the recomputation within 1e-3).
* F09: report the helper-choice distribution per scenario and IVD (H2 vs the geometry). Expected near-static for H2 on the highway → INFO with numbers.
* F05: count TBs where a later `_hsel` row on the same HARQ id overwrote the choice before the TB's last copy.

**D03_selection_accounting** (C: CORE)

* `n_hsel_rows / n_NDI1_TBs` per traffic class and leaderSps. Expected 1.0; > 1 → XFAIL(F06) with the factor.
* Recompute `helper_changes_per_s` and `helper_share_m*` per TB (only rows that start a TB) and compare with the run.csv values. Report the inflation.

### Group E — KPI validity and logical variation

**E01_kpi_recompute** (C: CORE; W: all synthetic)

* Recompute independently from raw logs and compare with `_member.csv` / `_run.csv`:
  * per-member sent, rx, prr, prr_pdb, late, and the loss-cause counts (from `_pkt`);
  * prr_mean / prr_min;
  * leader_new_tb / leader_retx (from `_ltx` in the window) and helper_tx (`_htx`);
  * tx_per_pkt;
  * cbr and cbr_occupied (from `_alltx`, same formula as main.cc:2689-2722);
  * res_used_leader and res_used_helper (from `_alltx`: dst 254, node 0, and nodes ≠ 0 respectively).
* Tolerance: counts exact, ratios 1e-6.
* `res_used_helper` for R1–R3 must be 0 by definition. If the CSV equals res_used_leader → XFAIL(F01) and report the error in `res_per_pkt_used` per arm. **This is the key spectrum-metric validation.**
* Independent recount of attempts vs `att_*` (F16) → report the redundant-decode share.

**E02_pir** (C: CORE; W: SYN_DIST)

* PIR per member: t_rx = t_tx + delay_ms for received packets in the window, sorted; PIR_j = t_rx,j − t_rx,j−1. Report mean, p95, max.
* Identities: mean PIR = (t_last − t_first)/(n−1). For periodic classes with i.i.d. losses, E[PIR] ≈ T / PRR. Check |mean PIR − T/PRR| / (T/PRR) ≤ 0.15 in no-background cells (INFO if it fails in background cells: bursty collisions). mean PIR ≥ 0.95·T always.
* For aperiodic1tb, use the measured mean generation interval instead of T.
* Directional: PIR p95 of the worst member is lower for helper arms (R4/R5/R6) than for leader-only arms in the same family (paired) → INFO/PASS per §7.3.
* Artefact: PIR CDF per member per arm (one cell per scenario).

**E03_channel_variation_per_rx** (C: CORE none-bg cells; W: SYN_DIST)

* Per run, Spearman ρ(member index, PRR_pdb) < 0, ρ(index, mean_sinr_db) < 0, ρ(index, channel_loss_rate) > 0, for leader-only arms R1–R3 on highway with no background.
* SYN_DIST: PRR of the tail is non-increasing with spacing for every arm (allow CI overlap).
* Highway straight-line geometry: member 1 frac_los ≥ 0.9; members ≥ 2 mostly NLOSv (frac_nlosv ≥ 0.8). Roundabout: frac_nlos > 0 for some members (buildings). Roundabout without `--sumoNet` must never happen in the run-sets (assert the flag).
* With no background, `lost_collision == 0` exactly (only platoon transmitters; the leader and the helper never share a slot) → FAIL otherwise, listing the colliding slots.

**E04_background_interference** (C: CORE)

* For each (scenario, ivd, traffic, arm), the paired over seeds none → moderate → high:
  * cbr increases (strict, every seed);
  * collision_loss_rate is non-decreasing;
  * prr_mean is non-increasing (§7.3 directional);
  * leader_tx_coll_rate is non-decreasing.
* nbg none: cbr equals the platoon's own share (`res_used_*` / window) → identity check.
* Artefact: PRR and collision-rate vs bg level per arm.

**E05_traffic_classes** (C: CORE; W: SYN_NEAR)

* From `_pkt` (unique seq, t_tx, size), on the leader app:
  * periodic1tb: intervals = 100 ms ± 1 µs; sizes ∈ {190,300} with the 300 share 0.2 ± 0.02 (cycle).
  * periodic2tb: intervals 10 ms; sizes {800,1200} with the 1200 share 0.2 ± Wilson CI.
  * aperiodic1tb: intervals − 50 ms ~ ⌊Exp(50)⌋ (mean 49.5 ± CI; KS test p > 0.01); sizes uniform on {200..1200 step 200} (χ² p > 0.01).
* Which "size" is logged (wire vs payload): confirm via `custom-traffic-application.cc:301-331` and state it.
* One TB per packet: every `_ltx` NDI=1 row has n_seq == 1 (or report the distribution). `leader_pkt_multi_tb_unqueued == 0`. payload + 37 ≤ tb_max_b (1333). F26 → INFO.
* PDB used in prr_pdb matches the class (100/10/50 ms): recompute prr_pdb with the class PDB and compare.

**E06_spectrum_usage_nodes** (C: CORE; W: SYN_NEAR)

* Per node, PSSCH subchannel-slots per second (from `_alltx`, dst 254 for platoon nodes):
  * leader > 0;
  * members 0, except relaying helpers (H1: only node 4; H2/H5: only the `applied` helpers);
  * background nodes never use dst 254.
* PSFCH usage per member: NACK count (`_nack.csv`) and ACK count (Option 2: from run.csv `explicit_ack_rx`/`nack_rx`/`ack…` counters, or from in-run counters if present). Option 1: ACK = 0. Option 2: ACK + NACK ≈ (members − helperSilent) × transmissions, within DTX.
* 3GPP PSFCH resource budget (analytical, §4.4): with M_PRB,set = 50 PRB (assumption, labelled), N_subch = 5, N = 1, N_CS ∈ {1,2,3,6}, N_type = 1: R_PRB,CS = 10·N_CS ≥ 8 → Option 2 feasible for 8 members (PASS). Show PSFCH resource-element counts per TB: Option 1 uses one shared resource (all NACKers on M_ID=0), Option 2 uses one per member.
* Report CBR per arm and platoon_cbr_share recomputed (fixing F01).

### Group F — Scheduling (SB-SPS vs dynamic)

**F01_sbsps_behaviour** (W: SYN_SPS; C: CORE, leaderSps=true)

* Leader grants (`_grants.csv`, node 0, event c, sps=1):
  * rri_ms == class RRI (periodic1tb 100, periodic2tb 10, aperiodic1tb 20);
  * consecutive NDI=1 transmissions within a grant are spaced exactly RRI·2^μ slots on the same sb_start/sb_len;
  * the reselection counter (slots_or_counter at creation) lies in the §4.5 range: RRI 100 → [5,15], RRI 10/20 → [25,75], RRI 50 → [10,30]; FAIL outside;
  * the empirical mean is within the CI of the uniform mean.
* Keep events (k): the fraction kept at counter expiry = 0.8 ± Wilson CI.
* One LC per leader (F15 unreachable): assert from GrantCreated that each leader grant serves exactly one LC (or from the flow spec).
* Aperiodic SPS: count periods with no data (reserved but unused) → spectrum waste metric for G03.

**F02_dynamic_behaviour** (W: SYN_SPS; C: SPSDYN)

* leaderSps=false: one grant per TB (sps=0, rri 0). Every transmission of a TB lies in [t_gen + T1, t_gen + min(T2, PDB)]. No periodic repetition (consecutive TBs not forced to +RRI).

**F03_sps_vs_dynamic** (C: CORE vs SPSDYN, paired by cell/seed/arm)

* Report ΔPRR, Δleader_tx_coll_rate, Δcbr, Δtx_per_pkt with CIs.
* Directional expectations (INFO unless strongly violated): with background, SPS lets background UEs sense and avoid the leader's periodic reservations → leader collision rate with SPS ≤ dynamic for periodic classes. For aperiodic1tb, SPS wastes empty periods (F01 count).

**F04_retx_resource_constraints** (C: CORE; W: SYN_MARGINAL, SYN_SPS)

* HARQ arms (R2/R3/R5/R6): for consecutive transmissions k, k+1 of a TB (leader or copy), slot(k+1) ≥ m(slot(k)) + MinTimeGapProcessing (2) [5G-LENA rule; §4.4]. FAIL on violation.
* Blind arms: no constraint. Report the gap distribution.
* All arms: last transmission of a TB ≤ t_gen + PDB. Any violation → FAIL(F23). For periodic2tb, report how many TBs could not fit maxNumTx transmissions inside the PDB per arm. This explains the HARQ vs blind PRR gaps.
* SCI-1A reservation gaps ≤ 31 slots within each N_MAX group (DV8).
* F14 (μ=1 projection): from `_grants` and `_alltx` for the leader, check whether any new leader grant overlaps the leader's own still-valid periodic reservation (same slot, overlapping subchannels). Any overlap → XFAIL(F14) with examples; otherwise PASS with "not triggered in study".

### Group G — Study questions

**G01_blind_effectiveness** (C: CORE + W: SYN_MARGINAL; arms R1, R4_S1_H1, R4_S1_H2)

* For every TB and every attempt k ≥ 2 (leader retransmission in R1; helper copies in R4), and every member m, classify (cumulative `_att` status):
  * **effective**: m had not decoded before k and decodes at k;
  * **redundant**: m had decoded before k;
  * **futile**: m had not decoded and still fails at k. Sub-reasons: `sci_missed` (status 0), `corrupt_collision` (attempt's n_coll > 0 from `_ltx`/`_htx`), `corrupt_channel`.
* Per attempt:
  * **wasted** = redundant for all 8 members (an oracle HARQ would skip it);
  * **useful** = effective for ≥ 1 member;
  * **pure futile** = neither.
* Metrics per arm, cell and attempt index: share wasted / useful / futile. Per member: P(effective | needed). Specifically for the **worst receivers**, defined two ways: (i) tail = member 8, (ii) empirical worst = member with the lowest prr_pdb in R1 of the same (cell, seed).
* R4 extra: TBs with fewer than 2 copies, split into: helper did not decode (status ≠ 1) / handed slot too late (`coop_too_late`) / other. Settles F18.
* **Artefact `results/G01_ineffective_retx_worst.csv`**: every futile attempt for the worst receivers, with `arm,cell,seed,tb,attempt_idx,side,tx_node,member,dist_m,status,sinr_db,cond,n_coll,reason`.
* Checks:
  * effective + redundant + futile sums to the attempts × members count (identity).
  * SYN_NEAR-like no-loss cells: wasted share ≈ 100% for k ≥ 2 (PASS if ≥ 0.99).
  * R4 effective share for the worst members > R1 effective share for the same members (paired) → INFO/PASS per §7.3.

**G02_harq_upper_bound** (C: CORE)

* From the blind arm traces, build the **oracle HARQ**: same decode outcomes, but attempt k is sent only if ≥ 1 member had not decoded before k.
  * oracle resources = Σ subchannel-slots of the non-wasted attempts;
  * oracle PRR = blind PRR (by construction).
* Pairs: R1 → R2, R3 (leader-only); R4_S1_H2 → R5_S1_H2, R6_S1_H2 (Kuo helper); R4_S1_H1 → R5_S1_H1, R6_S1_H1.
* Checks per (cell, traffic), paired over seeds:
  * `res_used(HARQ) ≥ res_oracle(blind) × (1 − ε)`, with ε from the seed CI. HARQ using clearly fewer resources than the oracle → FAIL: an accounting error, or HARQ dropped needed retransmissions (then the PRR shows it).
  * `PRR(HARQ) ≤ PRR(blind) + CI` in no-background cells. Clearly higher → FAIL-suspicious (HARQ cannot beat blind with the same budget without collisions). With background → INFO (fewer self-collisions can help).
  * Spectrum gain_HARQ = 1 − res(HARQ)/res(blind) ≤ gain_oracle + CI.
* Report a **gap decomposition** of the PRR loss of HARQ vs blind:
  * Option 1 SCI-missed members (cannot NACK: inherent 3GPP);
  * MCR-gated members;
  * retransmissions that could not fit in the PDB (F04);
  * stale decisions;
  * Option-2 silent-helper blind spot;
  * residual.

**G03_spectrum_gain_prr_parity** (C: CORE)

* Per arm, measured from raw logs (never from `res_*` columns, F01/F02):
  * PSSCH subchannel-slots per generated packet and per delivered member-packet (leader + all copies);
  * PSFCH transmissions per TB (Option 1 NACKs; Option 2 ACK+NACK) and the 3GPP PSFCH resource count (E06);
  * **announced/reserved** subchannel-slots per packet: from SCI-1A reservations. Leader grant slots from `_grants` (created grants: sb_len × slots per period × periods). For SPS, include unused periods; for HARQ arms, reserved retransmission slots stay announced even when ACK cancels them (§4.4) → reserved ≈ blind reserved.
* Gains vs the blind counterpart (same helper):
  * transmitted gain (interference/energy) and reserved gain (capacity for sensing UEs) with CIs;
  * PRR parity: ΔPRR_mean and ΔPRR_worst (both worst definitions) with paired CI.
  * Verdict wording: "same PRR achievable" iff the CI of ΔPRR_worst includes 0 and |mean| ≤ 0.01, else report the gap.
* Cross-check: E01's corrected `res_per_pkt_used` vs the CSV value per arm → table of the error introduced by F01, for the user's existing results.

**G04_option2_helper_protocol_scale** (C: CORE R6H1/R6H2/R6H5)

* C03 invariants at scale (0 tolerance): helper_ack_rx == 0; suppressed == helper decodes; reclaim accounting; no false ACK.
* Blind-spot rate (helper SCI missed and TB declared success) and its PRR cost to the helper.
* Stale decision rate (F08).
* Compare with R3 (Option 2 without helper): PSFCH count reduction from the silent helper (one fewer ACK per TB), and retransmissions triggered by the helper's NACK.
* Check that the leader **only** reacts to the helper's NACK (never to an ACK, since none arrive) and that every reclaim follows a helper NACK.

**G05_helper_reliability_worst_rx** (C: CORE R4/R5/R6 × H1/H2/H5; W: SYN_DIST)

* Per TB:
  * helper decode of the initial transmission (P_hd);
  * worst member (both definitions) failed the initial transmission (needs help);
  * a copy reached the worst member and succeeded (recovered).
* Metrics:
  * P_hd;
  * P(recovered | needed);
  * P(recovered | needed, helper decoded);
  * the hindsight-best helper per TB = a member that decoded the initial transmission and is nearest to the worst member (or best link SINR from `_att`);
  * coverage of the chosen helper vs the hindsight best (share of needed TBs where some other member could have relayed but the chosen one could not).
* PRR_worst(helper arm) − PRR_worst(leader-only counterpart) with paired CI: R4 vs R1, R5 vs R2, R6 vs R3. The helper must not reduce PRR_worst (FAIL if the CI excludes 0 on the negative side in no-background cells).
* Selector calibration (H2/H5): predicted score of the applied helper (`_hsel`) vs measured prr_pdb mean → calibration plot (INFO). Relates to F09/F24.
* Recompute `helper_drop_rate` and `relay_needed` correctly (F28) and compare.

**G06_distance_link_conditions** (W: SYN_DIST; C: CORE ivd5/15/30)

* PRR_pdb vs leader distance, per arm (member mean_dist_m on the x-axis). Check monotone non-increasing per arm (§7.3).
* At equal distance, ordering expectation with no background: blind ≈ HARQ ≥ leader single transmission; helper arms ≥ leader-only for members beyond the helper → directional INFO/PASS.
* IVD effect on the tail: PRR_tail(ivd5) ≥ PRR_tail(ivd15) ≥ PRR_tail(ivd30) per arm.
* Roundabout vs highway at ivd30: report the NLOS share and the PRR difference.
* Artefact: curves for the notebook.

**G07_harq_tradeoffs** (C: CORE + SPSDYN; W: SYN_MARGINAL; arms R2, R3, R5_S1_H2, R6_S1_H2 vs blind counterparts R1, R4_S1_H2)

For every event below, report a count, a probability with Wilson 95% CI, and conditional tables over the D-4 conditions. Give the top-5 condition cells by probability and a heatmap (member × background level) per traffic class in the notebook. Each event's unit (per TB, per member-TB, per transmission) must be stated. Status is `INFO` (measurement), except identities, which are FAIL on violation.

* **T1 latency cost of the PSFCH round trip**:
  * the gap between consecutive transmissions of a TB (HARQ vs blind distribution);
  * per delivered member-packet, extra delay vs the blind arm's delay distribution at the same (cell, seed, member);
  * P(the HARQ retransmission that delivered the packet came later than the blind arm's last copy would have).
* **T2 PDB fit**:
  * P(TB that needed a retransmission could not get one because the next feasible slot after PSFCH + processing was beyond the PDB);
  * P(member lost a packet with HARQ budget remaining but deadline passed);
  * split by traffic class (periodic2tb, PDB 20 slots, expected dominant).
* **T3 Option 1 SCI miss**: P(member not yet decoded, missed the SCI (status 0) on the TB's last transmission → no NACK → TB ended → member lost the packet), per member-TB. Also the version where the miss happens on any transmission and the member was then never served. This is inherent to 3GPP Option 1.
* **T4 Option 1 MCR gating**: member beyond 500 m failed, no NACK. Expected ≈0 in real traces (leader-to-tail ≤ 280 m); report it anyway.
* **T5 Option 2 DTX cost**: P(retransmission triggered only by DTX, i.e. no NACK and ≥1 silent non-helper member, and redundant because all members had decoded). This counts wasted Option-2 retransmissions. Also the re-ACK case from F25.
* **T6 stale / HARQ-id reuse**: P(decision stale), and P(feedback dropped) (F08).
* **T7 collision exposure of retransmissions**: P(retransmission collided, n_coll > 0) for HARQ vs blind, conditioned on background level. The HARQ retransmissions sit farther from the initial transmission, which changes exposure to new SPS selections by others.
* **T8 resource-geometry difference**: distribution of (retx slot − initial slot) and subchannel reuse, HARQ vs blind. This explains why per-TB pairing is impossible (§13 Q6).
* Summary table: PRR loss of each HARQ arm vs its blind counterpart decomposed into T2 + T3 + T4 + T5-related + T6 + G08 + residual. The decomposition must sum to the measured ΔPRR within rounding; otherwise FAIL (accounting error).

**G08_silent_helper_gap** (C: CORE + SPSDYN, arm R6_S1_H2; W: SYN_MARGINAL, SYN_DTX)

* Events (unit = TB named to helper h, using the helper's status on the leader's initial transmission from `_att`):
  * **H0**: helper missed the SCI (status 0);
  * **H0a** = H0 and all non-helper members ACKed → the leader declares success, so the helper's own packet is lost unless delivered later;
  * **H0b** = H0 and ≥1 non-helper member NACKed or was silent → a relay was expected, but the helper cannot relay (no decode), so the handed slot(s) stay empty;
  * **H2c**: helper SCI OK but TB corrupt → helper NACK → reclaim. This is the implemented path; report it for comparison.
* For each event: count, probability (Wilson CI), conditions (helper distance to leader, link condition, SINR bin, n_coll of the initial transmission, background, traffic, scenario/IVD), and PRR impact (helper's PRR, and PRR of the members who needed the relay in H0b).
* **Protocol requirement check (user D-5)**: for every H0b TB, did the leader transmit the TB again after the (empty) handed slot, within the PDB and the maxNumTx budget? Look for a leader `_ltx` row with the same HARQ id/TB after the handed slot.
  * Expected with the current code: none. If none → `FAIL` against the user's requirement (not an XFAIL: it is a requirement, not a pre-registered finding), with counts.
  * If some exist → PASS for those, and report the coverage.
* **Feasibility / benefit upper bound**: for each H0b TB, is there a slot after the last handed slot with remaining PDB ≥ 1 slot + T1 and remaining transmission budget? Count the TBs that a fallback could have served and the PRR gain that would bring (upper bound, assuming the fallback copy decodes with the member's empirical success rate).
* **Leader-side sensing proxy**: the leader can only infer "nothing sensed" if the handed slot carried no PSSCH/SCI-1A from the helper. Use `_alltx` (no helper transmission in that slot) as the proxy, and state that the leader's own PSCCH decode is not logged.
* **Ambiguity check (orchestrator's argument; report it):** when all non-helper members ACKed, an empty handed slot is *expected* even if the helper decoded, because R6 suppresses the relay. A "fallback if nothing sensed" rule must therefore fire only when the leader saw ≥1 NACK/DTX from a non-helper member for that TB. Otherwise it retransmits after every all-ACK TB. Quantify both:
  * spurious fallbacks under the unconditioned rule = all-ACK TBs with empty handed slots;
  * useful fallbacks under the conditioned rule = H0b TBs.

---

## 9. What NOT to test (out of scope; state it in the notebook)

* μ=3 or other numerologies (study uses μ=1).
* S2 / S3 helper scheduling, split, additive, leaderResIuc (not in the user's comparison). F19 is an INFO note.
* Stock NR suites unless the user approves A03b.
* ASan/UBSan builds (would need a reconfigure).

---

## 10. Launch script `tests/run_tests.sh`

```
Usage: tests/run_tests.sh [--profile quick|full] [--jobs N] [--stage STAGE[,STAGE...]] [--only REGEX] [--resume] [--no-notebook]
Stages (default all, in this order):
  env       unset MAGIC_HELPER_ID/NUM_MEMBERS; print machine, git rev of repo and contrib/nr, `git -C ns-3-dev/contrib/nr diff --stat | tail -1`; assert BASE_FLAGS == run_retx.py
  build     ./ns3 build <production binary> <all test targets> [+ NR examples for A03]; fail fast
  unit      run U-type tests (A01, A02, A05, D01) + existing test_helper_select
  synth     generate synthetic traces (tests/out/traces/), run synthetic run-sets via campaign/run_matrix.py
  campaign  run CORE and SPSDYN for the profile via campaign/run_matrix.py (resumable, gzip, RSS log)
  check     run all W/C tests (each reads tests/out/<runset>/manifest.csv), JSON into tests/results/
  notebook  python3 tests/notebook/build_notebook.py && jupyter nbconvert --to notebook --execute --inplace tests/TEST_RESULTS.ipynb
Exit code: 0 if no FAIL, 1 otherwise; prints a summary table (id, status, #checks FAIL/XFAIL/PASS/INFO/SKIP).
```

* Use the system `python3` (pyenv 3.10, which has pandas 2.3, matplotlib, nbformat, nbconvert, nbclient, ipykernel, scipy). Do not use `.venv`.
* Use absolute paths derived from the script location. Log every stage to `tests/out/logs/<stage>.log`.
* `run_matrix.py`:
  * runs with `subprocess` plus a pool of size JOBS;
  * per run, wraps the command as `/usr/bin/time -l` with `env -u MAGIC_HELPER_ID -u NUM_MEMBERS`;
  * applies a timeout (10 min per run) and records the exit code;
  * after success, gzips CSVs > 1 MB;
  * appends a row to `manifest.csv` (`runset,arm,scenario,ivd,bg,traffic,seed,leader_sps,tag,out_prefix,exit,wall_s,max_rss_mb,cmd`);
  * skips existing complete runs when `--resume` is set (the default).

---

## 11. Notebook `tests/TEST_RESULTS.ipynb`

Generate it with `notebook/build_notebook.py` (nbformat), then execute it. Keep explanations short: 2–4 sentences per test covering what is checked, why, the reference (3GPP clause or README) and the pass criterion. Sections:

1. **Overview**: environment (from the `env` stage); run-set sizes and missing combinations; status table of all tests (colour by status); the list of FAIL and XFAIL checks with notes.
2. **Findings ledger**: F01–F29 and DV1–DV10, each marked confirmed / refuted / not triggered / not tested, with a link to the check.
3. **Groups A–F**: one subsection per test with its check table and at most one plot each. Required plots:
   * A02 PSFCH gap histogram;
   * B03 DTX cost;
   * E02 PIR CDFs;
   * E03 PRR vs member index;
   * E04 PRR and collisions vs background;
   * F01 reselection-counter histogram vs the 3GPP range;
   * F04 inter-transmission gap distribution for HARQ vs blind.
4. **Study answers (G)**, with plots:
   * G01 stacked bars of effective / redundant / futile per attempt index and per member (worst highlighted), plus the head of the ineffective-retransmission log;
   * G02/G03 scatter of PRR_worst vs transmitted subchannel-slots per packet per arm, with the oracle-HARQ point and CI bars; reserved-vs-transmitted bars; the PRR-gap decomposition table;
   * G04 helper-protocol counters;
   * G05 helper reliability bars plus the calibration plot;
   * G06 PRR vs distance curves.
5. **Answers to Q1–Q9** (§0): one short paragraph each, quoting the numbers.
6. **Limitations**: DV list, statistical power (seeds), synthetic-trace caveats.

Use matplotlib only (no plotly in the system python). Use clear labels and one colour per arm, consistent across plots (R1/R2/R3 grey scale; R4 blue, R5 orange, R6 green; H1 dashed, H2 solid, H5 dotted).

---

## 12. Execution order and milestones

1. **Scaffold**: tests/CMakeLists.txt, `add_subdirectory(tests)`, common harness (harness, csv, stats, run_outputs, synth_trace), A01 and A05. Build. Run.
2. **Synthetic traces**: read main.cc:91-170 and `gen_mobility.py` (output writer only) for the trace format. Write `synth_trace` plus a sanity run of SYN_NEAR R2 (one seed) and check that the outputs exist.
3. **A06 determinism first** (affects the campaign plan). If FAIL(F04), switch the CORE runs to produce both diag-off KPI runs and diag-on log runs (2× cost; use the quick profile for the diag-off duplicates of full if time is short, and say so).
4. Groups B, C on synthetic run-sets; A02 micro; A03; A04.
5. Quick profile of CORE + SPSDYN, then all C-type tests, then the notebook end to end. **Checkpoint: report to the orchestrator** (a short message with the status table) before launching the full profile.
6. Full profile in the background (resumable), while D/E/F/G analyses are refined on quick data.
7. Final notebook. Final report: status table, the list of FAIL/XFAIL with evidence, confirmed findings, runtime, disk used, anything not done and why.

Time guidance: steps 1–5 ≈ 4–6 h of agent work; the full campaign ≈ 2–3 h of wall time.

---

## 13. Open questions and points of disagreement with the user

**Status 2026-10-17: answered, see §0.1 (D-1 … D-7).** Q1 → D-1, Q2 → D-6, Q3 → D-2, Q4 → D-3, Q6 trade-offs → D-4/G07, Q7 → D-5/G08, Q10 → D-7. Q5 (R1 with psfch 0) is not requested, so keep psfchPeriod=1. The original text is kept below for traceability.

* **Q1 (naming)**: "R1 + H1 = blind + Kuo" does not exist in the code (R1 is leader-only; H1 is the static member 4). Default: user "R1+H1" ≡ **R4_S1_H2** (blind leader + blind Kuo helper), reported next to R4_S1_H1 and leader-only R1.
* **Q2 ("per TB / HARQ process helper")**: the code picks a helper per published grant (= per TB for dynamic grants, per SPS period, including empty ones, for SPS). H5 statistics are per member over a window, not per HARQ process. Default: treat per-grant as "per TB".
* **Q3 (what "spectrum gain" means)**: under Mode 2 the HARQ retransmission resources are announced in SCI-1A when the TB starts. When an ACK cancels them, sensing UEs have already excluded them. So HARQ saves **transmitted** resources (interference, energy, CBR), not **reserved capacity**. G03 reports both. Which one should the paper claim?
* **Q4 (PSFCH realism)**: 5G-LENA PSFCH has no errors and no collisions (DV1). Option 2 carries 7–8 PSFCH per TB and benefits most. Proposal: an optional G07 sensitivity test that drops PSFCH with probability p ∈ {1%, 5%} in a test-only wrapper. It needs a hook in `nack_only_harq.cc` (a simulator change), so it is not done without approval.
* **Q5 (blind baseline format)**: all arms keep psfchPeriod=1, so blind arms carry the PSFCH symbol overhead (fair per subchannel-slot; pessimistic for "pure blind"). R4 cannot run with psfchPeriod=0 (abort at main.cc:1683). Add an R1@psfch0 reference?
* **Q6 (upper-bound logic)**: oracle HARQ from blind logs keeps the blind decode outcomes. HARQ arms use different retransmission timing (PSFCH round trip) and resource geometry, so arms compare only statistically over seeds, never per TB. For periodic2tb (PDB 20 slots), HARQ round trips may not fit 3 transmissions; G02 separates that loss.
* **Q7 (Option-2 silent helper)**: when the helper misses the SCI, its silence counts as success (unlike 3GPP DTX = NACK), and the helper's own packet is lost. Default: report it, and FAIL only if the helper becomes the worst member. Is a stricter criterion wanted?
* **Q8 (worst receiver)**: the tail (member 8) and the empirically worst member are both reported.
* **Q9 (bugs)**: findings such as F01 (spectrum metric) are only flagged, not fixed, in this run. Fixes are a separate step on request.
* **Q10**: A03b (stock NR test suites) needs `./ns3 configure --enable-tests`, which reconfigures the shared build. Default: skipped.
