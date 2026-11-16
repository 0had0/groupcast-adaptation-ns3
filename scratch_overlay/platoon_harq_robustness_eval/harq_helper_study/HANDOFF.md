# HANDOFF: cooperative HARQ helper study (updated 2026-11-16)

Replaces the 2026-10-27 handoff (v2 era, in git history) and `HANDOFF_v3.md` (2026-11-12, untracked).

## Status
- **Paper data = campaign v3** (commit `32e29dd`). Every follow-up campaign is finished and copied to the laptop:
  - map-selection arms;
  - PSFCH period;
  - timing sweep;
  - 10-seed RSSI fallback.
- **v2 and pre-fix results are wrong.** They were deleted on 2026-11-15 / 16, on the laptop and on ease-rtx. Do not
  use or re-create them.
- **Folder layout:** since f71d0c2 the study folder has `scripts/` and `notebooks/` (see Layout).
- **Git and remote:**
  - `coop-arms` is pushed to origin at f71d0c2. ease-rtx is on the same commit and idle.
  - Seven files carry the user's own uncommitted edits (see Working tree).

## What is compared
Platoon simulator `../platoon_harq_robustness_eval.cc`: 9 vehicles, IVD 30 m.

**Leader only:**
- R1 blind;
- R1_bsc: blind with soft combining of the blind copies;
- R2: HARQ Option 1 (NACK-only);
- R3: HARQ Option 2 (ACK/NACK).

**Helper without takeover:**
- R4: blind helper (Kuo);
- R4_bsc: R4 with soft combining;
- R4.1: Option 1;
- R4.2: Option 2.

**Helper with takeover:** R5 (Option 1) and R6 (Option 2), the cooperative protocol.
- The helper is named per TB in SCI-2A. It relays in retransmission slots the leader hands over.
- On a helper NACK, the leader reclaims the next handed slot.
- Fallback: after a NACK decision with a silent helper, the leader listens in the helper's slot. If it decodes no
  helper SCI there, it takes the next handed slot.
- Code: `../nack_only_harq.cc`.

**Helper selection:**
- Kuo: distance only. This is the default and what campaign v3 uses.
- map (`--helperSelect=map`): building map plus CAM positions, `posErrM` = 0. Campaign `v3_map` uses it.

**Scenarios** (`scripts/study.py` `SCEN`):
| route | tag | KPI window |
|---|---|---|
| highway | `hwy` | 53 s from t = 3 s |
| roundabout W exit | `rbt` | curve only (`study.curve_window`) |
| roundabout E exit | `rbtE` | curve only |

**Traffic:** periodic1tb (SB-SPS, 100 ms) and aperiodic1tb (dynamic grants since v3).

**Background:** 0 / 15 / 30 / 45 / 60 cars at the moderate per-car load. Measured CBR is about 0.01 / 0.25 / 0.49 /
0.74 / 0.99, so 60 cars saturate the channel.

## Layout (since f71d0c2)
```
harq_helper_study/
  scripts/     every script; run from harq_helper_study/:  python3 scripts/X.py, scripts/launch_v3.sh
  notebooks/   every notebook; a subfolder notebook keeps its path,
               e.g. notebooks/paper_v2/eval_sections_v3/eval_figures.ipynb
  out/  figures/  paper_v2/  paper_notes/  paper/  presentation/   data, figures, tex (unchanged)
```

**Notebook path setup:**
- Every notebook starts with a cell tagged `path-setup`, defined in `scripts/nb_layout.py`.
- It changes to the folder the notebook belongs to (`harq_helper_study/` or e.g. `paper_v2/eval_sections_v3`) and puts
  `scripts/` on `sys.path`, so relative paths like `out/...` keep their meaning.
- Builders write through `nb_layout.notebook_path(name, rel)` and `nb_layout.add_preamble(nb, rel)`.
- `nbformat` is only installed on the laptop, so build notebooks there.

**Tracked or not:** only part of this is committed. `paper_v2/`, `paper_notes/`, `paper/`, most `*_v3` notebooks
and five scripts are untracked (see Working tree). The remote therefore has only the tracked part.
- One consequence: `scripts/takeover_data.py` imports `paper_v2/analysis/paper_data.py`, which is untracked, so it
  does not import on the remote.

## Data (laptop `out/`, all git-ignored)
| folder | campaign | commit | runs |
|---|---|---|---|
| `remote_v3` | v3: 10 Kuo arms x 3 routes x 2 traffic x 5 loads x 10 seeds | 32e29dd | 3000 |
| `remote_v3_map` | R4, R4.1, R4.2, R5, R6 with map selection, same cells | 32e29dd | 1500 |
| `remote_psfch` | PSFCH period 2 / 4 (R2, R3, R4.2, R6), R1 at period 0; 0 and 60 cars | a515e14 (same sim code) | 1080 |
| `timing` | PSFCH processing delay x activation gap, R3 vs R6 | 32e29dd | 1242 |
| `rssi10` | R6, 60 cars, seeds 1-10, fallback RSSI log | 32e29dd | 60 |
| `study_v3_map/` | symlinks: leader arms from `remote_v3`, helper arms from `remote_v3_map`, `<arm>_kuo` from `remote_v3` | | |
| `remote_slots_trace` | slot-level trace runs | | |

**v3 against v2:**
- `--sciOnEveryTx=true`: SCI 1-A on every PSSCH (TS 38.212 / 38.214). With it, a member that missed copy 1 can
  still decode the later copies.
- The leader's aperiodic traffic is on dynamic grants. v2 used SB-SPS with a 20 ms RRI.
- The stale-NDI receiver fix (tag `stale-ndi-fix`) is in every valid campaign.
- Check `<out>/COMMIT` before using a folder.

**Caches:**
- `paper_v2/analysis/cache_v3`: built by `paper_data.collect_folder`.
- `out/cache_nobg_v3`, `out/cache_takeover_v3`.

**On ease-rtx** (`~/Documents/groupcast-adaptation-ns3/.../harq_helper_study/out/`):
- Folders: `v3`, `v3_map`, `psfch`, `timing`, `rssi10`, plus their smoke runs and logs (17 GB).
- These hold the full outputs, including `_alltx` and `_coll`, which were not copied to the laptop.

## Notebooks (all in `notebooks/`)
| notebook | builder / source | content |
|---|---|---|
| `STUDY_RESULTS_v3.ipynb` | hand-edited copy (no builder run) | Kuo study: PRR vs distance, per load, spectrum and cost, latency, AoI, DT, takeover |
| `STUDY_RESULTS_v3_map.ipynb` (+ `_findings.md`) | `scripts/build_study_map_notebook.py` | same analysis with map arms + map vs Kuo comparison (`out/study_v3_map_cmp.csv`) |
| `TIMING_SWEEP.ipynb` | `scripts/build_timing_notebook.py` | PSFCH delay x processing gap |
| `RSSI_FALLBACK.ipynb` | `scripts/build_rssi_notebook.py` | RSSI-gated fallback, 10 seeds |
| `DENSITY_RESULTS_v3.ipynb`, `REQUESTS_v3.ipynb` | `scripts/build_density_notebook.py`, `scripts/build_requests_notebook.py` (`--output`) | load sweep; session analyses |
| `NOBG_DECOMPOSITION_v3.ipynb` | `scripts/build_nobg_notebook.py` | 0-background loss decomposition |
| `TAKEOVER_RESULTS_v3.ipynb`, `TAKEOVER_RESULTS_v3_audited.ipynb` | `scripts/build_takeover_notebook.py`, `scripts/build_takeover_audit_notebook.py` | takeover mechanics (audit log: `TAKEOVER_AUDIT_CHANGELOG.md`) |
| `paper_v2/eval_sections_v3/eval_figures.ipynb` | | paper figures, `tab_prr.tex`, `numbers.json`, map selection table |
| `paper_v2/analysis/paper_figures_v3.ipynb`, `paper_v2/cbr_vs_prr_v3/`, `paper_v2/psfch_period/`, `paper_notes/round3_v3/collisions.ipynb` | | paper figures, CBR, PSFCH period, consistency checks |
| `pre_ndi/`, `paper_v2/eval_sections/`, `paper_v2/analysis/paper_figures.ipynb`, `paper_v2/cbr_vs_prr/`, `paper_notes/round3/` | | older notebooks whose data is deleted: do not re-run |

Rebuild and execute from `harq_helper_study/`:
```bash
python3 scripts/build_study_map_notebook.py
jupyter nbconvert --to notebook --execute --inplace notebooks/STUDY_RESULTS_v3_map.ipynb
```

## Results (10 seeds, paired by seed, 95 % t intervals)
**Campaign v3, Kuo selection.** The cooperative protocol (R6) is never significantly worse than any scheme in any
configuration.
- Roundabouts, against R3: group PRR +17 to +27 %, worst receiver +105 to +151 %.
- Against the blind helper: group PRR +2 to +27 %.
- Highway: group PRR ties R3 in 7 of 10 configurations. The worst receiver is better at 60 cars, aperiodic
  (73 vs 62 %).

**Map against Kuo** (`notebooks/STUDY_RESULTS_v3_map_findings.md`):
- On the curve, map is better on every reliability, DT and AoI metric for every helper arm.
  - R6 group PRR +0.045 to +0.120; worst member +0.16 to +0.32.
  - Mean DT −16 to −36 %; worst-receiver AoI −57 to −81 %.
  - `ss_kB` −3 to −13 %.
  - The cost: mean latency is up to +0.9 ms higher in 13 of 20 cells.
- On the highway the two rules tie: map always names member 3, which Kuo names in 84 to 97 % of TBs.
- R6 − R3 on the curve: +0.17 to +0.28 with map, against +0.11 to +0.19 with Kuo.
- Why: map moves the helper back along the platoon (members 2 to 6 instead of 2 or 3).
- Best case: exact positions and a perfect building map; the choice is made before copy 1.

**PSFCH period** (`notebooks/paper_v2/psfch_period/`, `paper_v2/psfch_period/numbers.json`): R6's advantage holds at
every period.
- Against R3, roundabouts: group PRR +17 to +28 % and worst receiver +102 to +177 % at periods 2 and 4. This is
  significant in 8 of 8 configurations, as at period 1.
- Against R3, highway: −0.4 to +1.4 %, a tie.
- Against R4.2: +1 to +22 % at every period.
- Without background, R1 at period 0 equals R1 at period 1 in every seed. So the 12-symbol PSSCH of a slot without
  PSFCH does not change decoding in the model.

**Timing sweep** (`TIMING_SWEEP.ipynb`):
- At gap 2, PSFCH delays of 100 and 250 µs give results identical to v3 (138 / 138 runs).
- Late feedback appears only at 500 µs with gap 2:
  - R3: 90 / 60 late cancels per 1000 packets (0 / 60 cars).
  - R6: 1748 / 1475 late helper feedbacks.
  - The cost is +0.04 to +0.065 transmissions per packet, with no PRR loss.
- Gap 3 removes every late event.
- R6 − R3 ≈ +12 pp at 0 cars and +8 to +9 pp at 60 cars, all from the roundabouts.

**RSSI-gated fallback** (`RSSI_FALLBACK.ipynb`, R6, 60 cars, 10 seeds): gating the fallback on RSSI does not help.
- PRR −0.69 pp at a −95 dBm gate and −0.24 pp at −80 dBm, negative on every seed.
- 22 % of fallbacks are false; the oracle picks no gate in 4 of 6 contexts.
- Keep the ungated fallback.

## Remote (ease-rtx, 32 cores)
- **Access:** `ssh ease-rtx` works from the laptop in batch mode. Claude uses it only when the user asks, for that
  task.
- **Push:** the user pushes, or Claude when asked.
- **Code sync:** `git pull --ff-only origin coop-arms` on the remote. Without a push, send a bundle:
```bash
git bundle create /tmp/x.bundle <remote-head>..coop-arms && scp /tmp/x.bundle ease-rtx:/tmp/
ssh ease-rtx 'cd ~/Documents/groupcast-adaptation-ns3 && git fetch /tmp/x.bundle coop-arms:refs/remotes/bundle/coop-arms && git merge --ff-only bundle/coop-arms'
```
- **Launch** (from `harq_helper_study/`, after `--setup` once per NR patch change):
  `scripts/launch_v3.sh [--map | --psfch] [--jobs N] [--seeds 1-10] [--only REGEX] [--no-smoke]`. Stop with `--stop`.
  - The timing and RSSI campaigns have their own runners: `scripts/timing_sweep.py` and `scripts/rssi_campaign.py`.
- **Monitor:** `scripts/monitor_study.sh v3 --log out/v3_run.log --watch 300`.
- **Copy back** (what the notebooks need):
```bash
rsync -az --prune-empty-dirs --include='*/' --include='COMMIT' --include='manifest.csv' --include='*.cmd.json' \
  --include='*_run.csv*' --include='*_member.csv*' --include='*_pkt.csv*' --include='*_ltx.csv*' --include='*_htx.csv*' \
  --include='*_nack.csv*' --include='*_hsel.csv*' --include='*_grants.csv*' --include='*_att.csv*' --exclude='*' \
  'ease-rtx:Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study/out/<folder>/' out/remote_<folder>/
```
- **study.py pitfalls:**
  - `run` overwrites `<outdir>/manifest.csv`.
  - `--out` must be absolute.
  - Resuming skips a run when its `.cmd.json` equals the command.

## Git conventions
- **Commit with `--no-verify`:** the pre-commit hook stages the traci / vehicle-visualizer tarballs, which the user
  keeps uncommitted. Regenerate `patches/nr/local.patch` with `scripts/regen-patches.sh` only when the NR tree
  changed, and stage it explicitly.
- **Stage only your own changes.** When a file also carries the user's uncommitted edits, stage a blob made from HEAD
  plus your change (`git hash-object -w` + `git update-index --cacheinfo`).

## Working tree (2026-11-16)
**The user's own uncommitted edits.** Do not commit them without asking:
- `scripts/build_notebook.py`, `scripts/build_density_notebook.py`, `scripts/build_requests_notebook.py`: the
  `--output` option, the cost section, and the import of `paper_v2/analysis/paper_data.py`.
- `scripts/build_takeover_audit_notebook.py`, `scripts/takeover_audit.py`, `TAKEOVER_AUDIT_CHANGELOG.md`,
  `notebooks/TAKEOVER_RESULTS_v3_audited.ipynb`.

**Untracked:**
- Folders: `paper_v2/`, `paper_notes/`, `paper/`, `presentation/`, `figures/`.
- Notebooks: the `*_v3` notebooks other than the map, timing, RSSI and audited ones, plus `notebooks/paper_v2/`,
  `notebooks/paper_notes/` and `notebooks/pre_ndi/`.
- Scripts: `scripts/adtmc_*.py`, `scripts/build_nobg_notebook.py`, `scripts/nobg_decomposition.py`,
  `scripts/combining_evidence.py`.
- Docs and archives: `ADTMC_REPLAY_v3.md`, `HANDOFF_v3.md`, `harq_helper_paper_overleaf.zip`.

**Risk:** committing the builders without `paper_v2/` breaks them elsewhere.

## Caveats (state in the paper)
1. 60 cars saturate the channel (CBR ≈ 0.99). Put the measured CBR on the load axis; SL congestion control is not
   modelled.
2. The helper id in SCI-2A is a non-3GPP extension, and so is R5's dedicated helper NACK resource.
3. The 5G-LENA HARQ-IR abstraction (F35) averages the copies' SINR, so combining for the helper arms is conservative.
4. Map selection is a best case: exact positions, a perfect building map, and a choice made before copy 1.
5. Not modelled: re-evaluation and pre-emption, PSFCH loss and capacity.
6. One IVD (30 m) and one platoon size (9).
7. Seeds are never pooled across machines, because the SUMO traces with background differ between them.

## Open items
1. **Paper text:** the selection subsection and `tab_selection.tex` in `paper_v2/eval_sections_v3` use the v3 map
   arms. The Kuo prediction statistics in that subsection and the abstract still quote v2: update them to v3, and
   add the map-vs-Kuo result (R6 − R3 widens on the curve).
2. **Decide what goes in the paper:**
   - the PSFCH period robustness (one sentence or a small table);
   - the timing sweep (a robustness note: gap 3 needed at 500 µs);
   - the cost figures (`fig_cost_ss.pdf`, `fig_cost_psfch.pdf`, generated, not cited).
3. **Untested map variants:** position error (`posErrM` > 0) and a helper choice after copy 1. They need new runs.
4. **Optional:** a second IVD, if a reviewer asks.
5. **Housekeeping:**
   - Commit or drop the untracked `paper_v2/` and the v3 notebooks.
   - Rerunning `STUDY_RESULTS_v3` from a builder would need `scripts/build_notebook.py` to include the hand-added
     sections.
