# Audit of notebooks/TAKEOVER_RESULTS_v3.ipynb (2026-11-12)

- Original: `notebooks/TAKEOVER_RESULTS_v3.ipynb` and `figures/takeover_v3/`. Not modified.
- Corrected copy: `notebooks/TAKEOVER_RESULTS_v3_audited.ipynb`, built by `scripts/build_takeover_audit_notebook.py`, with all outputs
  regenerated. Its figures and tables are in `figures/takeover_v3_audit/`.
- Analysis code: `scripts/takeover_audit.py`. It reads only `out/cache_takeover_v3/<arm>_{win,sinr,lat}.csv` (written by
  `scripts/takeover_data.py` from `out/remote_v3`). It imports no project module, and none of the original notebook's cells
  were executed.
- Scope: 8 arms (R1, R2, R3, R4, R4.1, R4.2, R5, R6), i.e. 2400 runs. The `_bsc` arms are not used.

Cell numbers count every cell, Markdown included, starting at 1: "orig N" is the original notebook, "new N" the
corrected copy.

## Verification of the audit hypotheses

| # | hypothesis | verdict | evidence (recomputed) |
|---|---|---|---|
| 1 | no paired takeover comparison | **verified error (omission)** | Matching is complete. R6/R4.2 and R5/R4.1 each have 300 matched runs, 30 cells × 10 seeds, 5764 matched windows. Pair counts are identical in every seed (asserted in new 4, `check_coverage.csv`). |
| 2 | each arm binned by its own helper failure | **verified** | In matched windows the two arms fall in different bins 50.2 % (R6/R4.2) and 49.2 % (R5/R4.1) of the time, and 15 % / 14 % differ by two or more bins. Overall rates are equal (0.150 vs 0.149; 0.148 vs 0.150). The per-window correlation is 0.72 / 0.74 (`check_bin_mismatch.csv`). |
| 3 | bins without seed support | **verified** | Fig. 2/3: 37 of 326 points have fewer than 5 seeds, five of them only 1 seed. All are periodic-traffic bins at high helper failure, and the original drew bootstrap bands for them. Fig. 4: 39 of 186 points. |
| 4 | efficiency overclaim | **verified error** | Reserved resources are identical across matched arms (≤ 0.1 % difference), so per-reserved efficiency is PRR rescaled. Per transmitted resource (paired, 95 %): R5 − R4.1 is significantly lower in 26 of 30 cells and never higher. R6 − R4.2 is higher in 10, lower in 11, and n.s. in 9. Transmitted subchannel-slots per TB change by +4 to +35 % (R5) and −13 to +28 % (R6). |
| 5 | terminology | **verified** | "Residual BLER" was used for pair failure by the PDB. "P_recovery" was presented as if it were the takeover benefit. "False alarm" was used for redundant fallback attempts. |
| 6 | zero-loss bins dropped | **verified** | Orig 6, line 23 (`p = p[p.r > 0]`) dropped 97 supported bins with 0 observed failures (608,729 pairs, mostly R1/R3/R5/R6 at high SINR) and joined the remaining points across the gaps. |

## Changes per cell

| orig | new | misleading statement or calculation | correction | validation |
|---|---|---|---|---|
| 1 | 1 | "residual BLER = 1 − delivery"; P_recovery defined as the takeover measure; fallback with nobody lacking called a false alarm; no statement on the design | Terms table: pair delivery probability, residual pair failure probability by the PDB (includes SCI misses and deadline failures; not PHY BLER; not all-member delivery), group TB-equivalents, attempt success (attributed, not counterfactual), redundant attempt. Gain formulas, statistics, seed-support rule. Helper-failure plots labelled as descriptive. | text |
| 2 | 2 | `TD.collect` imported the collector module; independent bootstrap (1000) per arm only; 1-seed bins received a CI | Loads the CSVs through `takeover_audit`. Paired bootstrap: 20,000 replicates, fixed RNG 20261112, same seed indices in both arms. No CI with fewer than 5 seeds. | module test |
| 4 | 4 | only a PRR cross-check | Adds the coverage assertions (runs, windows, pair counts) and the bin-mismatch table. | PRR = cache_v3 for all 2400 runs (max diff 2.2e-16); assertions pass |
| 5–6 | 5–6 | y = "residual BLER"; zero-failure bins hidden and curves bridged across them; CI floor clipping not disclosed | y = residual pair failure probability by the PDB. Zero-failure bins drawn as hollow ▽ on the axis floor, with no CI and lines broken there. Bin sizes in a table and in `fig1_*_points.csv`. Unsupported bins (13 of 972) not drawn. The high-SINR floor explanation is marked as a seeds-1–4 diagnostic not recomputed here. | 97 zero bins listed with counts |
| 7–10 | 7–10 | none | Kept. Regenerated with the new conventions. | — |
| — | 11–12 | **missing** | **New primary takeover figure (Fig. 2):** paired PRR gain vs load, R6 − R4.2 and R5 − R4.1, with absolute (pp), relative (%) and loss-reduction (%) gains. Each row reports pointwise 95 % paired intervals, matched seeds (10) and seeds better / worse. An "all loads" row resamples seeds jointly across loads. | 59 of 60 intervals exclude 0. The exception is R6 − R4.2, roundabout W periodic, 45 cars: +1.7 pp [−0.4, 3.7], 7/10 seeds better. |
| 11–12 | 13–14 | own-arm bins presented as "reliability at a given helper failure probability" and as the "most important figure"; points with 1–4 seeds drawn with CIs | Fig. 2b retitled as a descriptive association, with the caveat about bins changing between arms. Seed-support rule applied. Points exported with seed, window and pair counts (`fig2b_*_points.csv`); unsupported points listed and not drawn. | 37 of 326 unsupported |
| — | 15–16 | **missing** | Fig. 2c (secondary): paired gain within bins of a **common reference** (mean of the two arms' realized rates in the matched window), with limitations stated. | supported bins only |
| 13–14 | 17–18 | conditional delivery read as a comparison between arms | Labelled descriptive (different TBs per arm), n_seeds and pair counts added. | — |
| 15–16 | 23–24 | windowed efficiency per reserved resource vs helper failure was the main efficiency figure | Kept only as a descriptive secondary figure, reserved and transmitted side by side, with the time-base mismatch stated (deliveries by generation time, costs by transmission time). | — |
| 17–18 | 19–22 | efficiency vs load per reserved only; reading: "the reliability gain does not come from more resources" | Fig. 3 is now whole-run, reserved (top) and transmitted (bottom) side by side per traffic class, labelled "group TB-equiv." with PSFCH excluded. Explains that reserved efficiency is PRR rescaled. Paired efficiency differences, a `resv_up_tx_down` flag, and the change in transmitted slots per TB. | see hypothesis 4 |
| 19–20 | 25–26 | "P_recovery" vs own-arm helper failure; points with < 5 seeds drawn | Renamed "attempt success (attributed rescue)", with the note that the causal effect is Fig. 2. Seed rule plus ≥ 30 attempts; per-point seed, window, attempt and TB counts in `fig4_points.csv`. | 39 of 186 unsupported |
| 21 | 27 | "P(recovery)" labels | Same figure vs load with the new labels. | — |
| 22–23 | 28–29 | "false alarms"; per-TB recovery not separated from member rescue | Columns: attempt success (with CI), `recovered_tb_frac`, member rescue rate, `redundant` (nobody lacking; not proof that silence detection was wrong), and n_seeds. | — |
| 24–27 | 30–33 | none found | Kept. The p95 bootstrap now uses 2000 replicates and the fixed RNG; n_seeds added. | values unchanged within bootstrap noise |
| 28–29 | 34–35 | summary mixed "deliv", "bler", per-reserved efficiency and P_recovery as if equivalent | (a) Per-arm table: pair delivery, residual pair failure, all-member delivery, both efficiencies, p95, takeover share, attempt success. (b) Separate table of the paired takeover effect with the change in transmitted efficiency. | — |

## Claims in my earlier chat reply that this audit corrects
- "R6 delivers the most TBs per reserved resource in every pair, so the reliability gain does not come from more
  resources": **unsupported**. Reserved resources are identical across arms. Per transmitted resource, R5 loses to
  R4.1 in 26 of 30 cells, and R6 vs R4.2 is mixed.
- "A helper-NACK takeover recovers the TB in 68–94 % of attempts" and "up to 20 % of fallback attempts are false
  alarms": these are attributed attempt-success rates and redundant attempts, not recovery effects or detection
  errors. The causal effect is the paired PRR gain.
- "When the helper fails, R5 / R6 deliver 44–90 % vs 19–45 %": a **descriptive** comparison of different TBs in each
  arm, not a paired effect.

## Limitations that remain
- 10 seeds per cell: the percentile bootstrap with 10 clusters tends to undercover, so the seeds-better counts are
  shown beside each interval.
- Windows within a seed are correlated. Only seeds are resampled, so no window-level independence is assumed.
- The common reference for Fig. 2c is still an outcome of the runs. An arm-independent alternative is the geometric
  leader→helper link condition in `_att.csv` (`geo_leader_helper`), which needs a re-collection. Decision
  (2026-11-12): keep the mean-of-both-arms reference; the geometric proxy is not used.
- The high-SINR floor explanation (Fig. 1) comes from a 4-seed diagnostic on one cell.

## Round 2 (2026-11-12, second review)

Cell numbers in this section refer to the current notebook (36 cells). Inserting the high-SINR check (new cell 6)
shifts every later cell by one compared with the round-1 table above; for example round-1 cells 11–12 are now 12–13.
Corrections 1–5 and the attempt rule were written into `scripts/takeover_audit.py` and `scripts/build_takeover_audit_notebook.py` by
the reviewing session, without executing the notebook. This session verified each against the data, added the
all-seed high-SINR check, rebuilt the notebook, executed it with every output regenerated (0 errors; pandas
FutureWarnings only), and inspected the figures.

| # | finding | verdict | correction | validation |
|---|---|---|---|---|
| 1 | SINR-share table (cell 9): `pd.cut` with `right=True` put the 10, 20 dB … bins in the range below their labels | **verified error** | `right=False`; labels `0-<10`, `10-<20` | highway periodic ≥ 20 dB: 42.1 % → **50.0 %** (recomputed independently). Paired gains unaffected. |
| 2 | shaded intervals bridged gaps (cells 7, 15, 27) | **verified** | `A.band()` draws one band per contiguous run of valid points; `A.full_bins()` reindexes each curve to the full bin sequence, so an absent bin breaks the line and the band | Fig. 1 / 2b / 4 inspected: no band crosses a missing, unsupported or zero point |
| 3 | "reserved resources are the same in every arm" (cell 20) | **verified overclaim** | scoped to the matched pairs, with the spread printed | reserved slots per generated TB, largest within-cell spread: R6/R4.2 0.070 %, R5/R4.1 0.108 %, all 8 arms 1.291 %. My own 2.6 % compared raw totals and is withdrawn. Per run the spread reaches 17.8 % (leader-only vs helper arms). |
| 4 | Fig. 2c read as an effect at a given helper failure (cells 14–16) | **verified** | retitled as a paired, descriptive contrast within outcome-defined strata, explicitly not the effect at a controlled failure probability; Fig. 2 stays the primary result | text |
| 5 | coverage check matched observed keys only (cell 4) | **verified gap** (no current omission) | `assert_complete`: every arm has exactly the 300 expected runs (3 × 2 × 5 × 10); duplicate (arm, run, window) rows rejected; `coverage_check` adds `validate='one_to_one'` and per-window pair equality | 2400 = 300 × 8; a run deleted from both arms of a pair is now caught (checked) |
| 6 | fallback interval from 8 attempts (cell 30) | **verified** | table cells need ≥ 30 attempts from ≥ 5 seeds that have an attempt; otherwise `supported = False` and no interval | R5, roundabout W periodic, 0 cars, fallback: 8 attempts in 4 seeds → flagged. 12 load cells are below 30 attempts, all for the fallback trigger. |
| 7 | high-SINR explanation rested on 4 seeds of one cell (cells 5–6) | **verified; the claim was wrong in magnitude** | `scripts/high_sinr_check.py` (raw `_att.csv`, all 1800 runs of R1, R3, R4, R4.2, R5, R6) → `check_high_sinr.csv`; new cell 6 shows it and asserts 1800 runs | first copies above 25 dB fail in 0.69–0.87 % of pairs in every arm, not ~3 %. Of those, R4 / R4.2 never deliver 79.5 % / 77.8 %; R1 5.7 %, R3 1.8 %, R5 7.9 %, R6 5.4 %. The cause (SCI-2A vs partial-band interference) is still not separated. |

Latent issue, not fixed: `binned_points` counts every seed with a window in a bin, including seeds with a zero
denominator. It changes no plotted point now (checked for Fig. 2b, 3w and 4: 0 points pass the rule only because of
such seeds). Counting the seeds with a positive denominator, as the takeover table now does, would make it robust.

## Round 3 (2026-11-12): R6 − R4.2 is a protocol-package contrast

Prompted by the ADTMC report (external, `adtmc_rewrite/MODEL_AND_VALIDATION.md` §4), verified here against the code
and the logs.

| finding | verdict | evidence | correction |
|---|---|---|---|
| R6 and R4.2 were described as differing "only in the takeover" (cells 1 and 12; `takeover_audit.PAIRS` comment; `NOBG_DECOMPOSITION_v3` sections 0, 2, 5b) | **verified error** | `platoon_harq_robustness_eval.cc:2111` installs `InstallOption2HelperSilence` only when `harqMode == "opt2" && helperTakeover`, i.e. for R6 only. In R4.2 the named helper ACKs like any member and, being half duplex, cannot hear the others in that PSFCH occasion. Run CSVs: `helper_ack_suppressed` per packet R6 1.32–1.52, R4.2 0. Helper copies per packet at 0 cars: R4.2 1.25, R6 1.11. Redundant helper copies at 0 cars: R4.2 3–18 %, R6 ≈ 0. | Text now calls R6 − R4.2 the takeover **package**: takeover + fallback + helper ACK suppression and listening. The transmitted-resource difference mixes extra leader copies with relay cancellation. R5 − R4.1 (Option 1) has no ACK difference; R5 adds the helper's dedicated NACK resource. |
| does the listening difference change delivery? | **no (at 0 cars)** | `NOBG_DECOMPOSITION_v3` section 5b: in TBs whose helper received the first copy, R4.2 and R6 deliver alike, and the received-group part of the paired gain has a 95 % interval including 0 in all 6 cells. | PRR gains attributed to the takeover stand; the cost comparison must not be attributed to the takeover alone. Not checked under load. |

Numbers are unchanged; both notebooks were rebuilt and re-run with no errors.
