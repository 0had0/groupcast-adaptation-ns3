# HANDOFF: campaign v3 (2026-11-12)

Supersedes the data part of `HANDOFF.md` (2026-10-27). Campaign v3 is now the paper data.

**Update 2026-11-14.** ease-rtx rebooted (unplugged). `v3_map` had finished first: 1500 / 1500, exit 0, copied to
`out/remote_v3_map` (check: 0 failures). `psfch` was cut at 443 / 1080 and relaunched with `scripts/launch_v3.sh --psfch
--no-smoke --jobs 32` (finished runs skipped, ~12 h left). `eval_sections_v3` now includes the v3 map arms
(`tab_selection.tex`, selection subsection, conclusion); only the Kuo prediction statistics remain from v2.

## What v3 is
Commit `32e29dd` on `coop-arms`. Same study as v2 (10 arms, 3 routes, periodic1tb + aperiodic1tb, 10 seeds),
with two changes:
- `--sciOnEveryTx=true` on every UE: SCI 1-A on every PSSCH, announcing the TB's remaining slots (TS 38.212 /
  38.214). Stock 5G-LENA sends it only on the first slot of a reservation, so a member that missed it could not
  decode the later copies. README section "SCI 1-A on every PSSCH".
- The leader's aperiodic traffic on dynamic grants (no `--aperiodicRri`); periodic keeps SB-SPS (100 ms).
  v2 used SB-SPS with a 20 ms RRI for aperiodic traffic, which reserved ~98-100 subchannel-slots per kB for
  10-20 transmitted.

Every arm ran at all five loads (0 / 15 / 30 / 45 / 60 cars): 3000 runs, all exit 0, `scripts/study.py check` 0 failures.
Sensing fixes on in every campaign (`run_retx.BASE_FLAGS`): `--slDynExclusion`, `--slProjectAnnounced`,
`--spsReselectOnMisfit`, keep probability 0.8.

## Remote (ease-rtx = odysseus, 32 cores)
| what | state on 2026-11-12 | output |
|---|---|---|
| v3 | done, copied | `out/v3` -> laptop `out/remote_v3` |
| v3_map (5 map-selection arms, 1500 runs) | running, ~136 / 1500 after 81 min | `out/v3_map`, log `out/v3_map_run.log` |
| psfch (PSFCH period 2 / 4, R1 at 0; 1080 runs) | queued: a waiting script starts it when v3_map ends | `out/psfch`, log `out/psfch_chain.log`, then `out/psfch_run.log` |

`a515e14` (the psfch profile) was pushed on 2026-11-12, so the queued `git pull` + `scripts/launch_v3.sh --psfch` will find it.
Monitor:
```bash
ssh ease-rtx 'cd ~/Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study && ./monitor_study.sh v3_map --log out/v3_map_run.log'
```
Copy back (what the notebooks need; `_alltx`, `_coll` and logs are left on the remote):
```bash
cd ~/Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study
rsync -az --prune-empty-dirs --include='*/' --include='COMMIT' --include='manifest.csv' --include='*.cmd.json' \
  --include='*_run.csv*' --include='*_member.csv*' --include='*_pkt.csv*' --include='*_ltx.csv*' --include='*_htx.csv*' \
  --include='*_nack.csv*' --include='*_hsel.csv*' --include='*_grants.csv*' --include='*_att.csv*' --exclude='*' \
  'ease-rtx:Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study/out/v3_map/' out/remote_v3_map/
```
(same for `out/psfch/` -> `out/remote_psfch/`). Laptop disk: 98 % full, ~11 GB free; each copy is ~1.5-2 GB.

## New metrics (paper_v2/analysis/paper_data.py, appended; old functions untouched)
- `cost_metrics(pre, arm)`: subchannel-slots (10 PRB x whole slot) transmitted per packet `ss_pkt` (= `res_pkt`,
  asserted), per kB of TB payload `ss_kB` (packet + 37 B), reserved `ss_resv_pkt/kB`; PSFCH resources per
  TS 38.213 sec. 16.3 (1 PRB x 1 cyclic-shift pair): `psfch_alloc_pkt` (exact: Option 1 one per PSSCH, + R5
  helper's own; Option 2 eight per PSSCH) and `psfch_tx_pkt` (Option 2 ACKs are a whole-run leader counter scaled
  to the window: an estimate, capped at allocated).
- `cost_collect(...)` (per folder/arm cache) and `collect_folder(out, folder, cache)`: run_metrics + cost +
  slot_outcomes (0 / 60 cars) for a one-folder campaign. v3 cache: `paper_v2/analysis/cache_v3/`.
- v3 has no `--writeHarqTrace` copy, so slot outcomes do not separate a failed SCI-2A from a failed data decode
  (`dead_other` was 0 anyway).

## Notebooks on v3 (all new files; v2 notebooks untouched)
| notebook | state |
|---|---|
| `notebooks/paper_v2/eval_sections_v3/eval_figures.ipynb` | done; figures, `tab_prr.tex`, `numbers.json`, `v2_vs_v3.csv`; map table skipped until `out/remote_v3_map` exists |
| `notebooks/paper_v2/analysis/paper_figures_v3.ipynb` | done; outputs in `paper_v2/tex_v3/` |
| `notebooks/paper_v2/cbr_vs_prr_v3/cbr_vs_prr.ipynb` | done |
| `notebooks/DENSITY_RESULTS_v3.ipynb` | done (`scripts/build_density_notebook.py --full-dir out/remote_v3 --density-dir out/remote_v3 --output ...`) |
| `notebooks/REQUESTS_v3.ipynb` | done (`scripts/build_requests_notebook.py ... remote_v3 --output ...`) |
| `notebooks/paper_notes/round3_v3/collisions.ipynb` | done; `checks.py` re-run there (see below); `v3_out/remote_full` is a symlink to `out/remote_v3` |
| `notebooks/STUDY_RESULTS_v3.ipynb` | done, no errors (copy of `STUDY_RESULTS_remote_full.ipynb` pointed at `out/remote_v3`, plus the cost section) |
| `MAP_HELPER_RESULTS.ipynb` | not redone: needs v3_map. Set `MAP_DIR=remote_v3_map`; its SOURCES list still names the v2 folders and has to point at `remote_v3` |

## Paper text on v3
`paper_v2/eval_sections_v3/eval_results_discussion.tex` and `conclusion.tex`: v3 numbers, checked against
`numbers.json`. Still v2 (marked `% TODO v3_map`): the helper-selection subsection, `tab_selection.tex`, the map
sentence of the conclusion, and the Kuo prediction statistics in that subsection. The abstract is not in the
folder and still quotes v2. Cost figures `fig_cost_ss.pdf` / `fig_cost_psfch.pdf` are generated but not cited.

Main v3 changes against v2 (cooperative protocol): gains over leader Option 2 on the roundabouts hold
(group +17-27 %, worst +105-151 %); over the blind helper they shrink (group +2-27 %, was +2-59 %) because
dynamic grants help the blind helper more; on the highway the group PRR ties with leader Option 2 in 7 of 10
configurations, the worst receiver is still significantly better at 60 cars aperiodic (73 vs 62 %). The protocol
is never significantly worse than any scheme in any configuration.

## Consistency checks on v3 (paper_notes/round3_v3/checks_summary.csv)
Same failure set as v2 (explained in `paper_notes/round3/CHECKS.md`): window-edge off-by-one counters, whole-run
`nack_rx`, the fallback false alarm (4 copies per TB, R5 / R6: 357 runs, v2 221), duplicate decodes under
Option 2, aperiodic arrivals not paired across families. New singles: helper_tx vs `_htx` rows off by 1 in
3 runs, relay_after_allack in 4 R6 runs.

## Uncommitted
- `scripts/build_notebook.py` (cost section, imports `paper_v2/analysis/paper_data.py`), `scripts/build_density_notebook.py`
  and `scripts/build_requests_notebook.py` (`--output`, one-folder campaigns): tracked, modified, not committed.
  `paper_v2/` is untracked, so committing the builders alone breaks `scripts/build_notebook.py` without it.

## Open items
1. When v3_map is done, copy it, re-run `eval_sections_v3/eval_figures.ipynb` (selection table)
   and update the TODO v3_map parts of the text, then the abstract.
2. Redo `MAP_HELPER_RESULTS.ipynb` on v3_map.
3. When psfch is done: copy to `out/remote_psfch`, compare paired by seed with `out/remote_v3` (period 1).
   In LENA the TB is always sized for 9 PSSCH symbols; slots without PSFCH send 12 (lower code rate, same
   subchannels), and feedback waits for the next PSFCH occasion.
4. Decide whether to cite the cost figures and the PSFCH-resource numbers in the paper.
5. Not modelled (stated in Limitations): re-evaluation / pre-emption, PSFCH loss and capacity, `* 4` slots/ms in
   `FilterTxOpportunities` (wrong at mu = 1, affects only background cars holding two SPS grants).
