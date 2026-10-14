# Session handoff — 2026-10-13 (afternoon; earlier notes below)

Start here. The study is in `HANDOFF.md` and `README.md` (this folder); this file says what
ran where, what is pending, and the decisions. Local only (names the server).

Repo: `~/Documents/groupcast-adaptation-ns3` on the laptop and on the server
(`ease@mfam-Precision-3660`, 32 threads, 125 GB; `ssh -F ~/.ssh/ease-config ease-rtx`,
password).


## 0. 2026-10-13 afternoon: hidden-node test (branch `hidden-node`, pushed, `900b8a2`)

- `coop-arms` got the SB-SPS write-up commit `0ff4789` (local only, not pushed;
  `hidden-node` contains it and is pushed).
- Code, runner (`run_hidden_node.py`), driver (`run_hidden_node.sh`), analysis
  (`analyze_hidden_node.py`), README "Hidden-node test" (definitions, pre-registered rules,
  IUC spec quotes), HANDOFF §7.11. Pilots: identity 16 runs / 121 files 0 diffs; passivity
  14 runs identical; 12 functional runs every check 0; `run_hidden_node.sh check` OK.
- **Stage A not started** (server needs the password). On the server:
  ```
  cd ~/Documents/groupcast-adaptation-ns3 && git fetch && git checkout hidden-node
  cd scratch_overlay/platoon_harq_robustness_eval
  tmux new -d -s hn -c "$PWD" 'JOBS=24 ./run_hidden_node.sh build check stageA analyzeA'
  ./run_hidden_node.sh status
  ```
  (`build` = `setup.sh --force` + build; ~2 h for 126 runs; generates the 3-seed traces of
  hwy IVD 60 N 45 with gen_mobility.) Then copy back:
  `ssh -F ~/.ssh/ease-config ease-rtx 'cd ~/Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/results && tar czf - hidden_hn/analysis_A rerun_hn/*.txt rerun_hn/progress.log' | tar xzf - -C ~/Documents/remote-results`
  and report `remote-results/rerun_hn/analyze_stageA.txt` (verdict line). Stage B only after
  a go and the user's OK.
- Laptop disk: ~0.7 GB free. Pilot outputs in the session scratchpad only.

## 1. What is running / what ran where

| where | what | status |
|---|---|---|
| server | **SB-SPS re-run** (`run_rerun_sps.sh`, tag `sps`, branch `coop-arms` @ `1ef63a6`) | `build check` OK 11:59 (4 smoke runs, 0 mismatches). **Launch** with `tmux new -d -s rerun -c "$PWD" 'JOBS=24 ./run_rerun_sps.sh'` if not yet started; ~14 h (campaign ~3 h, variants ~4.5 h, coop ~6-7 h). Outputs `results/{campaign,variants,coop,analysis}_sps`, log `results/rerun_sps/` |
| server | t-Reordering 0 campaign + variants | done 2026-10-11, copied to `~/Documents/remote-results/{analysis_rlc0,variants_rlc0}` |
| server | coop follow-up (`run_coop_followup.sh 1 2 3`) | done 2026-10-12 06:52, 760 runs; copied to `~/Documents/remote-results/{coop2_rlc0,coop_rbt_rlc0,coop_sweep_rlc0}` |
| laptop | SPS / D1 / D2 / keep pilots (seed 1), identity checks | done, session scratchpad only (temporary); numbers in README "Standard Mode-2 grant types ... / Pilots" |
| laptop | `results/rerun_lapcheck*/` | local `check` smoke runs, throwaway (git-ignored) |

Monitor the server run: `./run_rerun_sps.sh status` (any shell). Resumable: relaunch the
same tmux command after a crash. Note: the `driver: running` line printed at the end of a
`check`-only invocation is the driver seeing its own lock, not a second driver.

## 2. Code state

Branch `coop-arms`, pushed to origin at **`1ef63a6`** (also pushed `7fd137f`, the follow-up
results commit). All new options are opt-in; **all off = byte-identical to `7fd137f`**
(9 short pilots incl. every coop arm and old `--leaderSps`, 4 full dynamic cells, 3 full
`ownRes` / `additive` runs; the run CSV only gains appended columns).

- NR patch (`patches/nr/local.patch`): `NrSlUeMac` attributes `SlDynExclusion` (D1,
  step 6 with C_resel = 1 for dynamic grants), `SlProjectAnnounced` (D2, project announced
  current-period / dynamic retx), `SpsReselectOnMisfit` (TS 38.321 5.22.1.1); schedulers
  (stock fixed-MCS + `NrSlCoopScheduler`): misfit reselection / enlargement, working
  `sl-ProbResourceKeep` (5G-LENA's keep branch was unreachable), traces `SpsMisfit`,
  `SpsResourceKept`; helper NACK matching by PSFCH occasion (absolute slot, PSSCH slot
  recovered from the delivery time).
- Program: `--spsReselectOnMisfit --slDynExclusion --slProjectAnnounced
  --slProbResourceKeep --rriList`; run-CSV `leader_grant`, the flags, `rri_list`, grant
  statistics (reselections/s, misfits, kept/s, subch/bytes/slots per grant,
  `ack_no_retx_left`).
- Option-1 wrapper (`nack_only_harq`) under SPS: no implicit ACK after a period's last
  retx; a NACK goes to the pending transmission whose PSFCH decision comes first (was the
  latest: 540-715 misattributed per SPS periodic2 run, now 0).
- `SlStackParams::probResourceKeep` (default 0) — the only change to shared
  `common/stack/nr_sl_stack.{h,cc}`; **your other uncommitted edits in
  `scratch_overlay/common/` are untouched and still uncommitted.**
- `check_sps.py` (wrapper checks), `run_rerun_sps.sh` (re-run driver: `--leaderSps`,
  misfit, D1, D2, keep 0.8, `--rriList=0,10,20,50,100,200,...,1000`).
- Uncommitted on the laptop: this file (local only), `psfch_prompt_idea.md`,
  `sumo/roundabout/{README_original_export.md,build_network.py}` (not from this session).

Committing on the laptop: stage explicitly, `git diff --cached --stat`, `--no-verify`, then
`git checkout -- patches/ns-3-dev/extras/` if touched. For `nr_sl_stack.cc` stage only
this study's hunk (it was done with `git update-index --cacheinfo` from a patched HEAD copy).

## 3. Decisions of this session

- **Periodic traffic on SB-SPS in every experiment** (leader periodic1/2, background
  periodic1); aperiodic1 and helper relays on single-PDU (dynamic) Mode-2 grants.
- **Accepted**: periodic2 on SB-SPS loses retransmission slots under load (highway N 45:
  ~1.5 reserved slots per TB vs ~2.1-2.2 for a dynamic grant with D1 + D2). Cause:
  TS 38.214 step 6 checks an SPS candidate over all j < C_resel = 10 x counter periods;
  standard behaviour, recorded in HANDOFF §8; no pool change.
- sl-ProbResourceKeep 0.8, 14-entry sl-ResourceReservePeriodList for the re-run.

## 4. Findings of this session (details in README / HANDOFF §7.9, §8)

- SPS leader + misfit reselection: delay a few ms (not ~94 ms).
- D1 + D2 cut the highway leader's collision rate 0.25 -> 0.04-0.07 (periodic1),
  0.39-0.46 -> 0.20 (periodic2).
- 5G-LENA bugs found: dynamic grants skip step-6 exclusion (D1); announced retx not
  projected (D2); sl-ProbResourceKeep never applied; after a wasted retransmission the MAC
  leaves an empty grant (`continue` skips the removal; latent, hit only by my first keep
  timer, fixed by a longer timer).
- Same HARQ id across SPS periods broke three things, all fixed: wrapper implicit ACK,
  helper NACK matching (`additive` `relay_without_initial_nack` 2 / 37), leader NACK
  attribution.

## 4c. 2026-10-13: SB-SPS re-run done and written up

Server run finished 2026-10-13 03:43 (15 h 41 min, 1046 runs, 0 failed, every check 0);
copied to `~/Documents/remote-results/{analysis_sps,campaign_sps,variants_sps,coop_sps,rerun_sps}`.
Results written into README ("SB-SPS re-run: results", new reference) and HANDOFF (§7.10,
§8 caveats, §10, §11) — uncommitted on the laptop until you commit.

## 4d. 2026-10-13/14: per-TB helper selection (Kuo) — done on the server

Commit `2721fcd` on branch `hidden-node` (pushed; this checkout is on `hidden-node`, the
hidden-node session's branch). Server `run_dynhelper.sh`: 420/420 runs, 5 h 03 min, 0 failed;
copied to `~/Documents/remote-results/{coop_dyn_sps,rerun_dyn}` (no per-packet / `_att` files).
Verdicts in HANDOFF §7.11 (map-aware selection pays, Kuo distance-only does not for the tail,
NACK = blind at fewer transmissions). HANDOFF §7.11 written, not committed yet.

## 5. Next steps

1. ~~Launch / watch the server re-run~~ — done, copied. (Original note: when done copy `results/analysis_sps`,
   `results/variants_sps/analysis`, `results/coop_sps/analysis` and the `analyze_*.txt`
   logs in `results/rerun_sps/` to `~/Documents/remote-results/`:
   `ssh -F ~/.ssh/ease-config ease-rtx 'cd ~/Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/results && tar czf - analysis_sps variants_sps/analysis coop_sps/analysis rerun_sps/*.txt' | tar xzf - -C ~/Documents/remote-results`
2. Check the analysis scripts handle the SPS runs (footprint step uses cell filters; the
   driver logs `analyze: footprint FAILED` if not) and compare SPS vs the `*_rlc0` results
   cell by cell — a new baseline, not a correction (do not mix seed by seed).
3. Unverified: whether TS 38.321 re-draws the reselection counter when a resource is kept
   (implementation reuses the counter drawn at selection, as 5G-LENA intended).
4. Still open: truncated-packet bug (MCS 5); cost-matched baseline for `additive`.
