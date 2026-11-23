# HANDOFF: paper v2 on the post-fix data (2026-10-29)

Follows `HANDOFF_STALE_NDI.md` (the bug, the fix, the v2 re-run and its validation). This file is the current
state of the paper and its data, and what is left.

## State in one paragraph
All simulations are done. The paper (`harq_helper_study/paper/main.tex`, 6 pages) is written on the v2 data only.
Every number in it is traced in `paper/claim_evidence_map.md`, and it has no TODO left. Nothing on the remote is
running. The study code is committed and pushed (last commit `b354902`). `paper/`, `paper_v2/` and
`paper_notes/` are untracked, as before: nothing new to commit unless you decide to track them.

## Data (laptop, `harq_helper_study/out/`)
| folder | runs | commit | use |
|---|---|---|---|
| `remote_full_v2` | 1200 | faefac6 (has `stale-ndi-fix`) | all paper numbers, 3 scenarios x 2 traffic x 5 loads |
| `remote_density_v2` | 1080 | faefac6 | the density panels (Fig. 3) |
| `remote_slots_trace` | 240 | b354902 | the three Fig. 5 cells again with `--writeHarqTrace` (only `_run`, `_harq`, `.cmd.json` copied) |
| `remote_full`, `remote_density` | | pre-fix | invalid for the helper schemes; never mix with v2 |

## What the traced runs showed (2026-10-29)
1. They are the same simulations: for all 240 runs `paper_data.trace_prefix` checked `pkts`, `leader_tx`,
   `helper_tx`, `nack_rx` and `prr_mean` against the v2 run, with no mismatch. Every SCI-2A failure event (event 6
   in `_harq.csv`) falls on a copy with status 2 in `_att.csv`; none falls on any other status.
2. They change no paper number or figure. After rebuilding with the traces, `numbers.json` and `tab_prr.tex` are
   byte-identical, and in `stats_coop_vs.csv` all 1116 verdicts are equal (values differ by < 1e-14).
   Fig. 5 keeps the same bars, because the arms that leave silent dead slots (Option 1) already log their NACKs
   (`_nack.csv`), so "status 2 without NACK" was already counted as silent.
3. What they add is why leader Option 1 leaves slots dead (`option1_silence.py` -> `option1_silence.txt`).
   Per packet; percentages are of the dead slots:

| case | dead slots | only SCI-2A failed | only SCI-1A missed | both mixed |
|---|---|---|---|---|
| highway, aperiodic, 60 vehicles | 0.29 | 52 % | 28 % | 21 % |
| roundabout W, aperiodic, 60 vehicles | 0.49 | 26 % | 21 % | 53 % |
| roundabout E, periodic, 0 vehicles | 0.36 | 21 % | 49 % | 30 % |

   - In every dead slot, every member that still lacked the TB was silent. No member that decoded both SCIs
     failed to NACK, and no NACK was ignored, so the PSFCH model is consistent.
   - Interference (highway, loaded) mostly breaks the SCI-2A. A building (E route, no load) mostly removes the
     SCI-1A.
   - Leader Option 2 has 0 dead slots in all three cases.
   - The same script on the cooperative protocol (R6): 0.30 / 0.19 / 0.004 dead slots, almost all of them
     "helper missed copy 1" in Fig. 5. In 62-72 % of them a lacking member had even sent a NACK on the last
     copy, but nobody was left to retransmit.
4. Written into `main.tex` Section V-C (the old TODO), and the Fig. 5 caption no longer lists an "other" class
   (it is 0 in every cell, asserted in `build_paper_figures.py`).

## Old (pre-fix) vs new results, and why the claims stand
Details: `HANDOFF_STALE_NDI.md`, "Validation results"; per cell: `compare_old_new.csv`.
- The fix raised every helper scheme's group PRR by 0.03-0.06 (worst member +0.07-0.12). Leader-only schemes
  gained +0.015-0.019 (blind, Option 1); Option 2 gained only +0.001. No cell dropped significantly.
- Cooperative vs leader Option 2, mean PRR gap: 0.081 -> 0.111; worst member: 0.137 -> 0.215 (stronger).
- Cooperative vs the Kuo helper: 0.107 -> 0.092. Vs the Option-1 variant: 0.049 -> 0.028 (weaker but still
  positive).
- Disconnection time: on the curve the gain now comes from having a helper, not from the takeover. The paper says
  so: 18 of 20 ties with the Kuo helper.
- Contribution still holds, and the paper claims:
  - best mean group PRR in 29 of 30 cells (tie with Option 2 on the loaded highway);
  - best worst-member PRR in all six 60-vehicle cells;
  - cheaper than leader Option 2 in 30 of 30 cells;
  - the dead-slot mechanism of the Kuo helper (1.35 dead slots per packet) and how the takeover recovers them
    (0.30 left dead).

## Files touched today
- `paper_v2/analysis/cache/remote_full__*`: rebuilt with the traces. The untraced cache is in
  `paper_v2/analysis/cache_pre_trace/`, the pre-fix cache in `cache_pre_ndi/`.
- `paper_v2/tex/figures/fig_slots.pdf` -> `paper/figures/fig_slots.pdf` (same content), `notebooks/paper_v2/analysis/paper_figures.ipynb` re-executed.
- `paper/main.tex`: Section V-C Option-1 paragraph, Fig. 5 caption.
- `paper/claim_evidence_map.md`: rows "slots" and "leader Option 1 dead slots".
- `paper_notes/round4/option1_silence.py`, `option1_silence.txt`: new.

## How to rebuild everything
```
cd scratch_overlay/platoon_harq_robustness_eval/harq_helper_study/paper_v2/analysis
rm cache/*                      # only to force a re-collect (~2 min for the full campaign at 8 processes)
python3 build_paper_figures.py  # figures, tab_prr.tex, numbers.json, stats_coop_vs.csv into ../tex
cp ../tex/figures/*.pdf ../../paper/figures/ && cp ../tex/tab_prr.tex ../../paper/tables/
python3 -I ../../paper_notes/round4/option1_silence.py ../../out R2 R6
```
- The collector in parallel: `collect_v2.py`, which calls `paper_data.collect_arm(camp, arm, 'cache')` for
  every (camp, arm) in `P.CAMPS`.
- `build_paper_figures.py` also collects serially if the cache is empty.
- Compile: Overleaf, or locally drop the `balance` line, since `balance.sty` is not installed on the laptop.

## Left to do (all yours, none needs a simulation)
1. Read the paper end to end, in particular:
   - the sentence on the 35-bit second-stage SCI format (its field layout is not described);
   - where Fig. 5 floats (it tends to land after Section V-C).
2. Decide whether to track `paper/`, `paper_v2/`, `paper_notes/` in git (they are untracked today).
3. Optional, not needed for the paper: remove the Option-2 rule "SCI-2A failed -> RemovePrevDecoded" in
   `nr-spectrum-phy.cc`.
   - It is redundant with the NDI toggle and causes harmless double decodes.
   - It would need a re-run of the Option-2 arms.
4. Optional: report the NDI bug upstream to 5G-LENA (CTTC) with test C07 as the reproducer.
