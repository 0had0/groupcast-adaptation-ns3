# HANDOFF: stale HARQ state fix and full re-run (2026-10-27)

## Why
While checking the paper's numbers we found that the 5G-LENA sidelink receiver treats NDI as a "new data"
flag, not as the 3GPP bit that toggles at each new TB. A member that misses the SCI (either stage) of
copy 1 of a new TB then:
- skips copies 2 and 3 as "already decoded" if it decoded the previous TB on that HARQ id, and under Option 2
  (`SlAckPrevDecodedTb`) ACKs them; the false ACK can stop the leader (R3) or the helper (R4.2, R6);
- soft-combines copy 2 with the failed copies of the previous TB if that TB was not decoded (optimistic).
Code: `contrib/nr/model/nr-spectrum-phy.cc` (~l.2860-2965 and ~l.3040-3055), `nr-harq-phy.cc`
(`IndicatePrevDecoded` / `RemovePrevDecoded`, cleared only on NDI == 1).

Evidence (scripts in this folder, on `out/remote_full`):
- `stale_harq_check.py`: R6 on the roundabouts, after a missed copy-1 SCI, 97.6 % of later copies get no
  decode attempt when the previous TB on that HARQ id was decoded, 47.0 % otherwise. Leader-only arms: later
  leader copies are missed 100 % either way (same blocked path), but after "SCI ok, data failed" (status 2,
  which includes a failed SCI-2A) 21-30 % of later copies get no attempt when the previous TB was decoded,
  against 3-4 % otherwise.
- `stale_harq_bound.py`, upper bound of the PRR the bug can cost (assumes every skipped copy decodes):

| cell | R1 | R3 | R4 | R6 |
|---|---|---|---|---|
| roundabout E, periodic, 0 cars | 0.010 | 0.008 | 0.041 | 0.036 |
| roundabout W, aperiodic, 60 cars | 0.044 | 0.022 | 0.021 | 0.039 |
| highway, aperiodic, 60 cars | 0.106 | 0.047 | 0.013 | 0.051 |

It touches every scheme, can move the highway R6 vs R3 tie either way, and explains most of R6's "dead" slots
without background (false ACKs). Decision (user, 2026-10-27): fix and re-run everything (option 1).

## Status (2026-10-29)
Code pushed (2026-10-27); both v2 campaigns ran on ease-rtx and are copied to `out/remote_full_v2`,
`out/remote_density_v2`; validation done (section "Validation results" below). Next: the paper text.

| commit | content |
|---|---|
| `adec989` | tests: C06 cross-source combining hooks (writeHarqTrace, testFixedSinr) |
| `f2334fb` (tag `stale-ndi-fix`) | the fix + test C07 + run-set SYN_STALE + counter `ndi_toggle_new_tb` |
| `faefac6` | launchers: `--out` / `--only` / `<out>/COMMIT`, `../../scripts/launch_density.sh --full-dir`, `../../scripts/study.py --out` made absolute, `../../scripts/monitor_study.sh` follows `--log` |

Fix (`contrib/nr`, in `patches/nr/local.patch`):
- SCI-2A carries the 3GPP NDI bit (padding bit 1, air size still 5 B). The TX toggles it per new TB per
  (destination, HARQ id); a helper copy carries the leader's bit. `GetNdi()` (the "new data" flag, `ndi` in the
  logs) is unchanged.
- RX (`NrSpectrumPhy::SlNdiNewTb`, error-model and no-error-model paths): after a decoded SCI-2A, a bit that
  differs from the one stored for (HARQ owner, HARQ id) is a new TB; when the new-data flag is 0 (copy 1 missed)
  the "decoded" mark and the soft buffer are flushed. `_harq.csv` event 9; run counter `ndi_toggle_new_tb`
  (last column of `_run.csv`).
- Checked, no change needed: helper relay setup, `relayOverheardAcks` (keyed by HARQ id and time), Option-2
  helper silence shim, `_att` / `_pkt` attribution (none uses the NDI flag at the receiver).

Deviation in the test hook: in 5G-LENA only the first slot of a reservation carries an SCI-1A and the receiver
expects copies 2 / 3 from it, so a missed copy-1 PSCCH hides all leader copies (the "100 % either way" of the
evidence). The stale state is reached after a failed SCI-2A of copy 1 or on helper copies (own SCI-1A).
`--testDropSci` therefore ignores the k-th expected PSSCH reception and keeps the reservation (as in 3GPP, where
every copy has its own SCI-1A).

### Tests
- C07 (`tests/workflow/C07_stale_harq_state.test.cc`, SYN_STALE: 12 cases x 2 seeds): unfixed code FAIL 35 /
  PASS 26 (every behaviour check of cases a-d fails: copy skipped as the previous TB, false ACK, combined with the
  previous TB's copy); fixed: PASS 61 / 61.
- Quick profile, both binaries run fresh on the same run-sets: FAIL checks 66 (old) -> 50 (fixed). Better: B03,
  C01, C02, C04, E06, C07. Worse (expectations not changed, explained in TEST_PLAN C07, to review):
  E01 1 run (SYN_MARGINAL R6 periodic2tb s2: E01's TB->packet mapping assumes every packet is sent, the run has a
  20 ms SPS phase with 10 ms packets); G04 SPSDYN R6 `helper_ack_suppressed` 711 vs 710 (CORE R6 failed already
  before); A06 `--collCond` no longer output-neutral; B04 / B05 / D02 / G08 known findings with moved counts.
- `../../scripts/study.py` smoke: 14 / 14 runs, check 0 failures.

### A/B (seed 1, no background, `out/ndi_ab/{old,new}` on the laptop)
| arm | rbtE periodic PRR old -> new | rbt aperiodic PRR old -> new | `ndi_toggle_new_tb` |
|---|---|---|---|
| R1 | 0.698 -> 0.699 | 0.516 -> 0.518 | 72 / 123 |
| R3 | 0.753 -> 0.754 | 0.575 -> 0.575 | 77 / 119 |
| R4 | 0.805 -> 0.879 | 0.608 -> 0.635 | 382 / 258 |
| R6 | 0.859 -> 0.931 | 0.732 -> 0.769 | 301 / 367 |

- After a missed leader copy 1, the first helper copy without a decode attempt: old R4 100 % / R6 99.4 % when the
  previous TB was decoded (58 % / 50 % otherwise); new 0.6 % / 0.0 % (15 % / 20 % otherwise: helper SCI missed).
- `stale_harq_check.py` alone is not a clean signal: its "later copies" mix leader copies (always missed after a
  lost SCI-1A) with helper copies, and for R1 / R3 "SCI ok, data failed" the 21-24 % vs 3-4 % gap is the same old
  and new (copy 2 decodes on a good link, copy 3 is then skipped legitimately).
- `stale_harq_bound.py` UNDERESTIMATES the cost for the helper arms (0.037 vs gains of +0.07): it requires the
  member to have decoded the immediately previous TB, but the stale mark survives several missed copy-1s. With
  "any earlier TB on the HARQ id decoded" the bound is R4 0.112 / 0.084, R6 0.080 / 0.073 and covers every gain.
  Leader-only arms barely move (a missed SCI-1A hides all leader copies anyway).
- Expect the v2 campaign to move the helper arms up by several points, the leader arms by ~0.1-0.2 points: the
  R6 vs R3 comparison and the R6 dead-slot story of the paper must be redone.

## Re-run plan (remote ease-rtx, 32 cores)
| campaign | arms | cells | runs | CPU time of the old runs | wall at 32 jobs |
|---|---|---|---|---|---|
| full -> `out/full_v2` | R1, R1_bsc, R2, R3, R4, R4_bsc, R4.1, R4.2, R5, R6 | 3 scen x 2 traffic x {0, 60} x 10 seeds | 1200 | 563 h | about 18 h |
| density -> `out/density_v2` | R1, R1_bsc, R3, R4, R4.2, R6 | 3 x 2 x {15, 30, 45} x 10 | 1080 | 339 h | about 11 h |
The `_noIR` arms are not re-run (not in the paper). Old data is archived in `out/pre_ndi_2026-10-27/`.

Launch. The remote still has the old `../../scripts/launch_full.sh` (it rejects `--out` before `--pull` runs): pull by hand
first.
```
ssh -F ~/.ssh/ease-config ease-rtx
tmux new -s ndi                      # later: tmux attach -t ndi
cd ~/Documents/groupcast-adaptation-ns3
git fetch origin && git checkout coop-arms && git pull --ff-only origin coop-arms && git log -1 --oneline   # faefac6
cd scratch_overlay/platoon_harq_robustness_eval/harq_helper_study

# 1) archive the old results (refuses if a study / simulator runs); out/ stays git-ignored
! pgrep -fl "../../scripts/study.py|ns3.42-platoon_harq_robustness_eval" && A=out/pre_ndi_2026-10-27 && mkdir -p $A && \
  find out -mindepth 1 -maxdepth 1 ! -name pre_ndi_2026-10-27 -exec mv {} $A/ \; && ls out $A

# 2) after clearing: full campaign (setup.sh --force rebuilds contrib/nr from the patch, build, smoke test, nohup)
./launch_full.sh --seeds 1-10 --jobs 32 --out out/full_v2 \
    --only '^(R1|R1_bsc|R2|R3|R4|R4_bsc|R4\.1|R4\.2|R5|R6)/'
cat out/smoke_check.log                                   # smoke check: 0 failure(s)
git merge-base --is-ancestor stale-ndi-fix $(cat out/full_v2/COMMIT) && echo "COMMIT has the fix"
python3 ../../scripts/study.py list --profile full --seeds 1-10 --only '^(R1|R1_bsc|R2|R3|R4|R4_bsc|R4\.1|R4\.2|R5|R6)/' | wc -l   # 1200

# 3) density, chained: waits for the full study, then launches (same binary, no setup)
while pgrep -f "../../scripts/study.py run --profile full .*out/full_v2" > /dev/null; do sleep 600; done; \
  ./launch_density.sh --seeds 1-10 --jobs 32 --out out/density_v2 --full-dir out/full_v2

# 4) monitor (second tmux window: Ctrl-b c; detach: Ctrl-b d)
./monitor_study.sh full --log out/full_v2_run.log --watch 300
./monitor_study.sh density --log out/density_v2_run.log --watch 300
```
Status checks while it runs: `./monitor_study.sh full --log out/full_v2_run.log` (done / total, failures,
ETA); `grep -c "exit   0" out/full_v2_run.log`; `grep -v "exit   0" out/full_v2_run.log | grep "^\["`
(failed runs); `cat out/full_v2/COMMIT` (faefac6, contains `stale-ndi-fix`); `df -h ~` (disk).
A failed run is resumed by launching the same command again (finished runs are skipped).
Stop: `./launch_full.sh --out out/full_v2 --stop`, `./launch_density.sh --out out/density_v2 --stop`.

Copy back (on the laptop, from `harq_helper_study/`):
```
rsync -av -e "ssh -F $HOME/.ssh/ease-config" --prune-empty-dirs --include='*/' --include='COMMIT' \
  --include='*.cmd.json' --include='*_run.csv*' --include='*_member.csv*' --include='*_pkt.csv*' \
  --include='*_att.csv*' --include='*_ltx.csv*' --include='*_htx.csv*' --include='*_hsel.csv*' \
  --include='*_nack.csv*' --include='*_grants.csv*' --exclude='*' \
  'ease-rtx:Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study/out/full_v2/' out/remote_full_v2/
# same with density_v2 -> out/remote_density_v2/
```

## Validation after the runs (before any number goes into the paper)
1. Integrity: 1200 + 1080 `_run.csv`, all exit 0, 10 seeds per cell, one binary, every `.cmd.json` equal to
   the old run's command except `--out`, `COMMIT` = fix commit, no run with 0 packets.
2. `paper_notes/round3/checks.py` on the new dirs (point it at `remote_full_v2` / `remote_density_v2`): same
   passes as before; the known failures (window edge off by 1, whole-run feedback counters, fallback double
   copy, aperiodic arrivals per family) may remain; the "member decodes the same TB twice" count should drop.
3. `stale_harq_check.py` on the new full runs, plus the first-helper-copy check of the A/B (helper copy after a
   missed leader copy 1: no-attempt share must not depend on "prev TB decoded"); `ndi_toggle_new_tb` > 0.
4. Old vs new per cell (write `compare_old_new.py`): PRR new - old per scheme, paired by seed; expected >= 0
   for most cells and below the LOOSE bound (any earlier TB on the HARQ id decoded, see A/B above; the strict
   `stale_harq_bound.py` is too low for R4 / R6); any cell where it drops by more than the CI must be explained.
5. C06 cross-source combining still holds (rerun `../../scripts/combining_evidence.py` on the new data).
6. Re-check the collision figures (`notebooks/paper_notes/round3/collisions.ipynb`) and the paper numbers on the new data.

## Validation results (2026-10-29, on `out/remote_full_v2`, `out/remote_density_v2`)
1. Integrity: PASS. 1200 + 1080 runs, seeds 1-10 in all 120 + 108 cells, every run has its `.cmd.json`
   (written only on exit 0), every command equal to the old campaign's except the binary path and `--out`,
   `COMMIT` = faefac6 in both (contains `stale-ndi-fix`), no run with 0 packets, `ndi_toggle_new_tb` > 0 in every
   run (median per run: leader arms 100-156, helper arms 193-392).
2. `round3/checks.py` (v2 results now in `round3/checks_*.csv`, pre-fix ones in `round3/pre_ndi/`): same failure
   classes as before, counts nearly equal (new_tb = pkts 209 -> 211 of 2280, nack_rx <= nack_sent 820 -> 809,
   pkt n_att <= 3 179 -> 190, tx_per_pkt 283 -> 282, ...). "No member decodes the same TB twice" did NOT drop
   (full: R3 3965 -> 3911, R4.2 1815 -> 1812, R6 1916 -> 1909 double decodes): they do not come from the stale
   state. In 290 of 294 sampled cases a status-2 copy lies between the two decodes: after a TB is decoded, a later
   copy can only fail at the SCI-2A, and the Option-2 rule "SCI-2A failed -> RemovePrevDecoded"
   (nr-spectrum-phy.cc, the pre-fix mitigation) makes the next copy decode the TB again. Harmless for the PRR
   (the packet counts once), redundant with the NDI toggle; removing it is a possible follow-up (needs a re-run of
   the Option-2 arms, not needed for the paper).
3. Stale state gone, campaign-wide (roundabouts, R4 / R4.2 / R6): after a missed leader copy 1, the first helper
   copy gets no decode attempt in 94-96 % (old) vs 1.9-2.3 % (v2) of cases when the previous TB was decoded;
   39-55 % (old) vs 19-21 % (v2) otherwise (the rest = the member misses the helper's own SCI).
4. `compare_old_new.py` (this folder; `compare_old_new.csv` per cell, `_runs.csv` per run): 228 cells, no cell
   where the PRR drops beyond its 95 % CI, no gain significantly above the loose bound (the density bound is
   NaN: the old density copy has no `_att` / `_ltx`). Mean PRR gain per arm, full: R3 +0.001, R1 +0.015, R2
   +0.016, R1_bsc +0.019, R4.2 +0.030, R6 +0.035, R4.1 +0.040, R4 +0.046, R4_bsc +0.053, R5 +0.056 (density: same
   pattern); worst-member PRR gains are larger (helper arms +0.07 to +0.12). R3 barely moves because its Option-2
   SCI-2A rule already cleared the stale mark in the leader-only path.
5. Cross-source combining still holds (`../../scripts/combining_evidence.py`, v2 R3 / R4.2 vs the old `_noIR` arms, which were
   not re-run; the metric is conditioned on a corrupt first copy of the same TB, untouched by the fix):
   SINR50 shift R3 3.27 -> 3.28 dB, R4.2 1.82 -> 1.71 dB on average, next-copy success unchanged. C06 also passes
   unchanged on the fixed binary.
6. Rebuilt on v2 (pre-fix copies kept): `paper_v2/analysis/paper_data.py` now reads `out/remote_*_v2` (map
   `DIRS`, override `PAPER_OUT`), caches in `analysis/cache/` (old: `analysis/cache_pre_ndi/`), outputs in
   `paper_v2/tex/` (old: `paper_v2/tex/pre_ndi/`); `round3/collisions.ipynb` rebuilt with
   `COLL_OUT=out/v2` (link folder `out/v2/remote_{full,density}` -> the v2 folders; old figures in
   `round3/pre_ndi/`). Overlap (collision) rate unchanged (0.34 in every arm); collision and channel losses of the
   helper arms drop (R6 0.121 / 0.077 -> 0.108 / 0.056), p_fail of a copy drops (old p_fail counted the skipped
   stale copies as failures).

R6 (proposed) vs each scheme, paired by seed, 30 cells (3 scen x 2 traffic x 5 loads; R2 / R4_bsc / R5: 12), PRR:
| vs | better / tie / worse old -> v2 | mean PRR diff old -> v2 | worst-member diff old -> v2 |
|---|---|---|---|
| R1 | 30/0/0 -> 30/0/0 | 0.132 -> 0.148 | 0.190 -> 0.257 |
| R1_bsc | 30/0/0 -> 28/2/0 | 0.104 -> 0.116 | 0.162 -> 0.223 |
| R2 | 12/0/0 -> 11/1/0 | 0.102 -> 0.120 | 0.157 -> 0.223 |
| R3 | 27/3/0 -> 28/2/0 | 0.081 -> 0.111 | 0.137 -> 0.215 |
| R4 | 30/0/0 -> 30/0/0 | 0.107 -> 0.092 | 0.121 -> 0.101 |
| R4.2 | 28/2/0 -> 30/0/0 | 0.073 -> 0.076 | 0.056 -> 0.071 |
| R4_bsc | 12/0/0 -> 12/0/0 | 0.100 -> 0.082 | 0.106 -> 0.083 |
| R5 | 12/0/0 -> 11/1/0 | 0.049 -> 0.028 | 0.085 -> 0.054 |
- PRR: R6 is never significantly worse. Its margin over the leader schemes (R3 especially) grows; over the
  helper baselines (Kuo blind R4 / R4_bsc, R5) it shrinks, because those gained most from the fix.
- Disconnection time: 108 verdict flips in `stats_coop_vs.csv`, mostly better -> tie for dt_mean / dt_worst vs
  R4, R4_bsc and R5 (R6's DT advantage over the helper baselines largely disappears). New "worse" verdicts:
  vs R4.2 dt_mean / dt_worst (rbt aperiodic 60, rbt periodic 30), vs R5 res_deliv (hwy ape 60, rbt ape 60, rbtE
  ape 60) and dt_worst (rbtE ape 0). R3 hwy aperiodic 60 disc_frac: worse -> tie.
- `numbers.json`: nearly every quoted number changed (`diff` against `tex/pre_ndi/numbers.json`).

## Paper status (2026-10-29)
- Manuscript: `harq_helper_study/paper/main.tex`, rewritten from v1 on the v2 data (v1 kept in `paper/pre_ndi/`).
  6 pages (compiled locally without `balance.sty`; Overleaf has it). No R-names; figures from `paper_v2/tex/figures`
  (Fig. 3 time disconnected vs load, Fig. 4 DT on the roundabout, Fig. 5 slot outcomes) and Table III from
  `paper_v2/tex/tab_prr.tex` (short row labels now set in `build_paper_figures.py`); every number checked against
  the v2 data, see `paper/claim_evidence_map.md`. DT reference added: Rolich et al., IEEE Commun. Lett. 2026.
- Dropped from v1 (pre-fix data, no v2 counterpart): PRR vs distance, bar figure, cost scatter, takeover split.
- `paper_data.slot_outcomes`: now counts the reserved slots of the leader's actual grant (changes Fig. 5 by at most
  0.055 slots per packet) and, for traced runs, treats a failed SCI-2A like a missed SCI.
- DONE 2026-10-29, see `HANDOFF_PAPER_V2.md` (no number changed; Option-1 causes written in V-C). Was: traced re-run of the three Fig. 5 cells (8 schemes x 10 seeds, 240 runs, `--writeHarqTrace`, ~5 h at 32
  jobs, ../../scripts/study.py `--extra`, commit b354902) to separate a failed SCI-2A from a failed data decode. After copying it to
  `out/remote_slots_trace/`: delete `paper_v2/analysis/cache/remote_full__*`, rerun the collector and
  `build_paper_figures.py`, copy `fig_slots.pdf` to `paper/figures/`, and write the Option-1 dead-slot causes into
  Section V-C (TODO comment in main.tex).
- Check before submission: the 35-bit second-stage SCI format sentence (field layout not described), and whether
  Fig. 5 should move before Section V-C (it floats to the last page).
