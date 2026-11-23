# HANDOFF: blind helper fallback, spaced reservations, paper skeleton (2026-11-23)

Follows `HANDOFF_v3.md` (campaign v3 = paper data). This file covers the work since campaign `v3_fb`:
the R4 + C + fallback arm, the helper-miss sweeps, the spaced arm (`R4_bsc_fbs`) and its load campaign `v3_fbs`,
the PRR-vs-distance figures, and the new conference-paper skeleton.

## Schemes (paper names; `\scheme{...}` in the skeleton prints these)
| arm | paper name | what it adds |
|---|---|---|
| `R3` | source Option 2 HARQ | leader HARQ with group feedback, no helper |
| `R4_bsc` (R4+C) | blind helper relaying | Kuo's helper (`--helperSelect=kuo`) relays blindly in the slots the leader hands over; receivers soft-combine |
| `R4_bsc_fb` | ... with fallback | `--blindHelperFallback`: the leader watches the helper's first handed slot and reclaims the next one if the helper is silent |
| `R4_bsc_fbs` | ... with fallback and spaced reservations | `--blindPsfchGap=true`: handed slots keep the PSFCH minimum time gap (3 + 2 slots) |
| `R6` | cooperative HARQ | relay on NACK, takeover on a helper NACK, fallback when the helper is silent, stop on all-ACK |

## Code (commit `a54a785` on `coop-arms`; NOT on GitHub, on ease-rtx via git bundle)
- `common/stack/nr_sl_coop_scheduler.{h,cc}`: attribute `BlindPsfchGap`. In `SelectResourcesForBlindRetransmissions`
  it switches from `SelectBlindOnce` to `SelectResourcesWithConstraint(txOpps, true)` (the selector R6 uses).
- `platoon_harq_robustness_eval.cc`: `--blindPsfchGap` (aborts without `--helperBlind`); `run.csv` gains `blind_psfch_gap`.
- `scripts/study.py`: arm `R4_bsc_fbs`, profiles `v3_fbs` / `v3_fbs_smoke`, a check on `blind_psfch_gap`.
- `scripts/launch_v3.sh --fbs`.

**Root cause the spacing fixes.** Without it the blind selector ignores `MinTimeGapPsfch`, so the two handed slots
can be adjacent and the leader cannot reclaim the second one ("no slot", fbchk outcome 2). Adjacent pairs: about 11 %
of TBs on average, 0-46 % per seed (SPS keeps the same resources with keep probability 0.8, so seed 3 is the worst).

## Data
| folder | what | runs |
|---|---|---|
| `out/remote_v3` | R3, R4_bsc, R6 (+ others), 3 routes x 2 traffic x 5 loads x 10 seeds | v3 |
| `out/remote_v3_fb` | R4_bsc_fb, same grid | 300 |
| `out/remote_v3_fbs` | R4_bsc_fbs, same grid (ease-rtx `out/v3_fbs`, COMMIT a54a785, check 0 failures, copied 2026-11-23, 745 MB with `_alltx`) | 300 |
| `out/local_helperrx` | forced helper miss sweep: R4_bsc data, R6 data + sci; p = 0.1, 0.2, 0.3, 0.5, 0.7; periodic, no background | |
| `out/local_helperrx_fb/controlled` | R4_bsc sci, R4_bsc_fb data + sci, R4_bsc_fbs data + sci (+ fbs's own p = 0 runs) | |

Forced helper miss (`--helperMissProb`, `--helperMissMode`): `data` = SCI-2A decoded, TB failed (helper NACKs, soft
buffer dropped); `sci` = SCI-2A failed (helper silent). Only the leader's first copy of a new TB, own RNG stream.

File names: `{hwy|rbt|rbtE}_ivd30_{periodic1tb|aperiodic1tb}_{none|n15|n30|n45|moderate}_s{1..10}_{kind}`;
`moderate` = 60 background vehicles (`nbg` column). Per run: `_run` (1 KPI row), `_member` (8 rows), `_pkt`
(packet x member, `dist_m`, `cond`, `delay_ms`), `_ltx` / `_htx` (leader / helper transmissions), `_hsel` (helper
choice), `_fbchk` (fallback checks: outcome 0 heard, 1 takeover, 2 no slot, 3 stale; -999 dBm = helper sent nothing),
`_att` (every reception attempt), `_nack`, `_coll` (collision causes), `_div`, `_grants.csv.gz`, `_alltx.csv.gz`,
`.cmd.json`, `.log`.
Format traps: `run.cbr` is the OFFERED load (0.99 at 60 cars); the pool CBR is `cbr_occupied` (0.01 / 0.23 / 0.42 /
0.58 / 0.69). `member.prr` has no deadline, `prr_pdb` does; from `_pkt` use `rx==1 & delay_ms<=PDB`
(100 ms periodic, 50 ms aperiodic).

## Results
**Helper-miss sweep, SCI drop, p = 0.7 (periodic, no background).** Spacing removes the "no slot" fallbacks
(~10 % -> 0 %). Gap to R6: W +0.3 +- 1.8 pp, E +0.1 +- 1.0 pp (was 1.3 / 1.7 without spacing). Highway: the spaced arm
beats R6 by 2.8 pp (group) and 17.9 pp (worst member). Payload drop (`data`): R6 stays 10-11 pp ahead, since the
blind arms cannot react to a helper that decoded the SCI but not the TB. The p = 0 roundabout gap (W +1.6 pp) is not
explained by spacing.

**Load sweep v3_fbs (paired over 10 seeds, 95 % t-CI).**
- Highway aperiodic: spacing gain grows with load, +4.3 +- 2.2 pp at CBR 0.70 (83.2 vs 78.9 %); R6 still +2.4 +- 1.3 pp
  (85.6 %). Periodic: arms within 1 pp.
- Roundabouts: spacing 0 to +2 pp, never significant; R6 2-4 pp ahead with aperiodic traffic at every load.
- Worst member: unchanged by spacing on the roundabouts; R6 leads by 4-9 pp aperiodic (E, 60 cars: 49.6 vs 40.6 %).

**Open lead.** In `hwy_..._moderate_s1_fbchk.csv` the first checks are outcome 1 (takeover) with the helper's power at
-999 (helper silent) although no miss was forced. Measure how often the selected helper is silent at p = 0 per route;
it may explain the remaining roundabout gap (e.g. helper did not decode the leader's copy behind the building).

**Abstract numbers (verified).** R6 vs R3: up to +18.7 pp; R6 uses 2.4-11.5 % fewer transmissions than R3; R6 vs R4+C at
helper miss p = 0.7: payload 17.3 / 24.6 / 25.5 pp (hwy / W / E), SCI ~13-14 pp.

## Notebooks and figures (all rebuilt from builders in `scripts/`)
| notebook | builder | content |
|---|---|---|
| `notebooks/PRR_VS_CBR.ipynb` | `build_cbr_notebook.py` (+ `plot_prr_vs_cbr.py`) | group / worst PRR vs pool CBR, 5 arms incl. fbs; findings section 4 |
| `notebooks/R4C_FALLBACK.ipynb` | `build_fb_notebook.py` | paired fallback gain, takeover mechanics |
| `notebooks/HELPER_MISS_FB.ipynb` | `build_hrx_fb_notebook.py` | helper-miss sweep R4_bsc / fb / fbs / R6 |
| `figures/prr_vs_distance/` | `plot_prr_vs_distance.py` | per route x traffic; rows payload / SCI drop, colour = p (viridis), R3 dashed |
| `figure_proposals/fig_scenario/fig_scenario_trim.{pdf,png}` | `make_fig_scenario_calm.py` | roundabout W / E scenario figure, transparent, caption in the script's history |

Rebuild a notebook: `python3 scripts/build_X.py && python3 -m jupyter nbconvert --to notebook --execute --inplace notebooks/X.ipynb`.

## Paper skeleton (`/paper_skeleton`, repo root; built WITHOUT reading `paper_v2`, at the user's request)
`main.tex` (IEEE conference), `refs.bib`, `IEEEtran.cls/.bst`. Builds: `pdflatex; bibtex; pdflatex x2` (4 pages).
- `\scheme{R6}` / `\Scheme{R6}` print the full names (table above); `\todo{}` marks open items.
- User's draft text merged in (abstract, background, platoon, traffic, radio table) with TODOs on unverified values:
  speed 110 km/h, background radius 1 km vs 500 m, RSRP threshold -128 dBm, PSFCH gap 3 + 2, ~2 subchannels periodic,
  and the abstract's 17 vs 25 pp and resource claims.
- Citations checked 2026-11-23 (notes in comments above the related-work paragraph): `kuo2021reliable`,
  `leibel2020harq` (Zotero full text), `aoki2026enhancing` = **Takahashi**, Aoki, Tang, Sensors 2026 (broadcast,
  read on mdpi.com; key name kept), `kim2019efficient` (IEEE Access 7:171333, read on IEEE Xplore). Takahashi and Kim
  are not in Zotero yet. Still placeholders: 3GPP spec versions, `ali20213gpp` (5G-LENA, Ali et al. IEEE Access 2021,
  doi 10.1109/ACCESS.2021.3090855), `sumo`.
- Fig. 1 is still a placeholder box; `fig_scenario_trim.pdf` is ready to drop in.

## Remote (ease-rtx = odysseus)
Repo: `~/Documents/groupcast-adaptation-ns3`, on `a54a785`. Idle (no simulations) after `v3_fbs`; `out/v3_fbs` still there.
Sync code: git bundle -> scp `/tmp` -> `git fetch` into `refs/remotes/bundle/coop-arms` -> `merge --ff-only` -> ninja.
Push / ssh only when the user asks for it.

## Not committed (everything since a54a785)
Builders and notebooks above, `helper_rx_campaign/{campaign.py,monitor.py,launch.sh}` (`--set main|fb|fbs`,
`HRX_OUT`), `plot_prr_vs_distance.py`, `plot_prr_vs_cbr.py`, `paper_skeleton/`, `figure_proposals/`, this file.
Commit with `--no-verify` (large tarballs trip the hook). Do not commit `out/`.

## Next steps
1. Commit the above (user's go-ahead).
2. Silent-helper rate at p = 0 from `_fbchk` / `_htx` per route (the open lead above).
3. Optional, offered and unanswered: aperiodic helper-miss sweep (~900 runs).
4. Paper: insert Fig. 1, resolve the parameter TODOs, fill the remaining bib entries, add Takahashi and Kim to Zotero.
