# HANDOFF — paper session (2026-10-26)

## What this paper is
IEEE conference paper, 6 pages, pdfLaTeX + IEEEtran, source in
`harq_helper_study/paper/` (Overleaf-ready zip at
`harq_helper_study/harq_helper_paper_overleaf.zip`).

Working title: *When Spatial Diversity Meets Sidelink HARQ: Robust
Cooperative Groupcast in 3GPP NR V2X*. Author block already set to
Hadi Houssainy, Bruno Stevant, Jean-Marie Bonnin (IMT Atlantique).

Story (last agreed version, see conversation):
- Platoons use NR SL groupcast across hundreds of metres.
- Tail members fail from (a) interference and (b) persistent blockage on curves.
- Feedback HARQ (Option 1 / Option 2) recovers interference losses at **lower**
  spectrum cost than blind retransmission, but can't go around a building.
- Kuo et al.: blind helper in reserved slots, periodic traffic only, no
  combining. Helps when the tail is blocked; **under load, the handed
  slots stay empty when the helper itself missed the packet**.
- Proposed R6: name helper in SCI-2A; helper silent on success, NACKs on
  failure; leader takes the slot back on NACK or on a silent slot it
  senses (SCI-miss fallback).
- Simulation shows R6 keeps the curve gain, matches leader HARQ where
  there is no blockage, uses fewer resources than blind, and adds
  ≤ 1.5 ms mean delay.

## Current state of the paper
`paper/main.tex` compiles clean (6 pages, no overfull, no undefined cites).
Figures and tables are built from `paper/data/*.csv` by
`paper/scripts/make_figs.py` and `paper/scripts/gen_tables.py`; those csvs
come from `paper/scripts/agg.py` + `summ.py` reading `out/remote_full`
and `out/remote_density` on the laptop.

Verified: run integrity, helper choice (identical across schemes), Kuo
selector matches literature (one-helper form; two-helper form from Kuo
Sec. IV-D not implemented), 14 references verified via DOI/arXiv.

**Pending edits already decided in-session but NOT yet applied:**
1. Replace Section II with the new "Group Communication in NR Sidelink
   Mode 2" draft I sent in the last reply (resource pool table, PSFCH
   mapping with the one-sentence TS 38.213 note after eq. (2),
   Option 1 vs 2, retransmission-scheme list). This replaces the current
   `\section{Background and Related Work}` + Section III scheme table.
   The new section is a single LaTeX block, ready to paste.
2. Rewrite the intro using the user's rewritten story (plus the six
   corrections I listed: name interference as a cause, fix the "HARQ
   costs more spectrum" claim, don't credit SCI-2A naming to Kuo,
   drop/reframe combining gains, split takeover vs fallback mechanism,
   state where the gains show).
3. Add the R4 ceiling inequality
   `PRR_j^(R4) ≤ 1 − (1 − p_{0,j})(1 − p_{0,l})` in the "why R4 loses
   under load" subsection, with the member-1 numerical check
   (ceiling 0.92, measured 0.88 at highway/aperiodic/60 cars).

## Open numerical/code tasks
- **New runs (prompt ready at `paper_notes/claude_code_prompt_rerun.md`,
  user approved).** 780 remote runs, 0 reruns:
  - `R1_bsc`, `R4_bsc` with `--blindSoftCombining=true` in the `full` cells
    (240 runs; adds standard-compliant blind-combining baselines).
  - `R1_bsc` in the `density` cells (180 runs).
  - `R4`, `R4.2` in the `density` cells (360 runs; closes the sweep).
  Not launched yet; user needs to pass the prompt to Claude Code.
- **Soft-combining investigation (prompt below in the previous reply,
  not saved to disk yet).** The ablation in the data shows ±0.02 PRR
  from turning combining off for helper arms vs +0.02 to +0.07 for
  leader arms. Code path appears correct (`relayedFromRnti` keys the
  HARQ buffer, see `nr-sl-ue-mac.cc` ~l.1474 and `nr-spectrum-phy.cc`
  ~l.2648) but a controlled 3-UE test and a per-attempt look at the
  existing _att.csv files are needed. If it's a bug, R6's numbers may
  move; if it's physical (persistent blockage leaves nothing to combine
  with, and SCI misses leave an empty buffer), it's just a finding.
- **"Resource reserved" counter (`res_per_pkt_reserved` and related
  columns in `_run.csv`) is miscalculated.** It averages grant size over
  creations instead of weighting by lifetime. The paper and both
  notebooks do NOT read it (they recompute from `_grants.csv`), so no
  numbers in the paper are affected. User asked to decide: fix in sim
  (one-line patch at `platoon_harq_robustness_eval.cc` ~l.4076) or
  simply drop the column. Not actioned.

## Big design decisions already settled
- Split the paper from the analytical Markov-chain framework (the long
  LaTeX doc the user shared). The conference paper stays as the
  measurement study; the Markov framework becomes a separate journal
  paper later. The ceiling inequality is the only piece borrowed.
- 6 pages is a hard limit.
- Audience: both standards/V2X people and modelling people (so keep the
  takeover rule central AND use probability-based language where it fits
  in one line).
- Keep the Kuo helper selector exactly as implemented; don't change the
  simulated R6; don't rerun R4/R4.2 with `aperiodic1fx` (fixed-size)
  unless the paper claims size doesn't matter (it doesn't).
- F33/F34 and other open simulator findings stay out of the paper
  (per user request).
- No new runs beyond the 780 in the Claude Code prompt.

## Files added this session (all on the user's Mac)
- `paper_notes/claude_code_prompt_rerun.md` — launch prompt for the 780 runs
- `paper_notes/helper_selection/` — Kuo-selector replica, agreement check,
  per-TB helper timelines, helper-vs-TB-size figure, README
- `paper_notes/r4_decomposition.py`, `.csv` — per-attempt breakdown of
  the R4 loss (how often the helper's copy decodes vs a leader
  retransmission, split by scenario/load, used in the "empty slots vs
  collisions" check)
- `paper_notes/render/` — rasterised copies of the 6 paper pages (for
  layout checks)
- `paper/main_v1_backup.tex` — pre-rewrite backup

Also staged `agg.py`, `summ.py`, the summary csvs and `geo.json` into
`paper/data/` and `paper/scripts/`.

## Risks / caveats to carry
- The paper's cost figures use *transmitted* resources, which the sim
  counts exactly. The *reserved* counter in `_run.csv` is wrong but
  unused.
- Helper selector matches `kuo` policy in simulator at 100% (highway,
  no bg) and 87–100% (roundabout with bg); the 60-car roundabout
  mismatch is because remote vs laptop traces differ for n60 (known
  HANDOFF caveat).
- Aperiodic traffic in the sim = random arrivals + random sizes +
  SB-SPS reservation at 20 ms (documented in PLAN.md). Current paper
  text calls this out; keep that phrasing in any rewrite.
- The simulator uses `UNIFORM_POWER_ALLOCATION_USED`, so power per PRB
  shrinks when a TB occupies more subchannels. This is the 5G-LENA UE
  default and matches 3GPP; keep as-is.

## Immediate next step for the new agent
Apply the three "pending edits" listed above in order (Section II
rewrite first since it's self-contained, then intro using the agreed
story + corrections, then insert the ceiling inequality). Compile,
re-render the 6 pages, confirm overflow is still zero, re-zip, send to
user. The agent should NOT: launch any runs, change figure scripts,
touch the Markov framework document, or re-attribute Kuo's work.
