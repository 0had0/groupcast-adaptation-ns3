# Per-TB helper selection under SB-SPS — setup, results and conclusions

Experiment `coop_dyn_sps` of `platoon_harq_robustness_eval`. Server campaign of
2026-10-13 (host `odysseus`, branch `hidden-node`, commit `2721fcd`, 24 jobs, 5 h 03 min,
420 / 420 runs, 0 failed, 0 flag mismatches). Analysed on 2026-10-14 from the copy in
`~/Documents/remote-results/coop_dyn_sps` and `~/Documents/remote-results/rerun_dyn`.

Contents

1. [Question and short answer](#1-question-and-short-answer)
2. [What was simulated](#2-what-was-simulated)
3. [Simulator settings](#3-simulator-settings)
4. [The seven strategies](#4-the-seven-strategies)
5. [How the selector works](#5-how-the-selector-works)
6. [Metrics and statistics](#6-metrics-and-statistics)
7. [Results](#7-results)
8. [Which member gets selected](#8-which-member-gets-selected)
9. [Helper coverage: not every TB gets a helper](#9-helper-coverage-not-every-tb-gets-a-helper)
10. [Helper choice over time](#10-helper-choice-over-time)
11. [Conclusions](#11-conclusions)
12. [Limitations and caveats](#12-limitations-and-caveats)
13. [How to reproduce](#13-how-to-reproduce)
14. [File index](#14-file-index)

---

## 1. Question and short answer

**Question.** A 9-car platoon (leader + 8 members) groupcasts with NR-V2X Mode 2 on a
semi-persistent (SB-SPS) grant. The leader keeps its first transmission of each data block
(TB) and hands its reserved retransmission slots to one member, the *helper*, who relays a
copy. Does choosing that helper **per TB**, from position information, beat a **fixed**
helper (member 4, the middle car)? And does the helper need HARQ feedback (relay only after
a NACK), or is relaying blindly in every slot just as good?

Kuo et al. (IEEE Access 2021) propose per-TB selection with a distance-only line-of-sight
model and blind retransmissions. We tested that rule (`kuo`), the same rule with a building
map (`map`), and the fixed member 4 (`s4`), each with NACK-triggered and blind relaying,
against the standard leader-only HARQ (`none`).

**Short answer.**

| Question | Answer |
|---|---|
| Does per-TB selection beat the fixed member 4 on the worst receiver (tail)? | **Only with the building map.** `mapNack` vs `s4Nack`, roundabout N 0 periodic1: **+0.058 ± 0.026, 10/10 seeds** (pre-registered rule 1: YES). Kuo's distance-only rule: −0.013 ± 0.025, not significant (rule 1: NO). |
| Does it raise the group PRR (GPRR)? | **Yes, both kuo and map**, periodic1, all loads: +0.03 to +0.07 over `s4Nack`, 10/10 seeds. kuo raises GPRR without raising the tail. |
| NACK or blind? | **Same reliability, much cheaper with NACK.** No paired difference in any periodic1 cell; NACK uses 1.5–1.8 transmissions per packet vs 2.3–2.9 blind (pre-registered rule 2: YES in all 9 cells). |
| Under background load? | periodic1: the gains shrink a little but survive at N 45 / 60 (mapNack vs none: +0.13 / +0.07 / +0.11 at N 0 / 45 / 60). periodic2 (10 ms): nearly everything within noise on the tail; only map keeps a significant GPRR gain. |
| Why does periodic2 not benefit? | Under load **more than half of periodic2 TBs have a single-slot grant**, so no slot can be handed over and no helper is named (coverage 85 % at N 0, 38–49 % at N 45 / 60). |

---

## 2. What was simulated

### Scenario

- **Roundabout** (synthetic 4-arm urban roundabout, 98 buildings of 15 m registered so NLOS
  exists, 3GPP V2V_Urban). The platoon enters from the northern arm (`N_in`) and leaves
  through the western exit (`W_out`), so it drives through a turn where buildings come
  between the leader and the rear of the platoon.
- **Platoon:** 9 cars of 5 m, inter-vehicle distance (IVD, bumper to bumper) **30 m**, node 0
  = leader (transmitter), nodes 1..8 = members (receivers), leader-to-tail ≈ 280 m along
  the road. Mean straight-line distance from the leader over the run: member 1 ≈ 35 m,
  2 ≈ 69, 3 ≈ 103, 4 ≈ 136, 5 ≈ 169, 6 ≈ 201, 7 ≈ 232, 8 (tail) ≈ 263 m (shorter than
  the road distance because of the curve).
- **Background vehicles N ∈ {0, 45, 60}** within 500 m of the roundabout centre (60 %
  periodic1 on SPS, 40 % aperiodic1 on dynamic grants, broadcast, no HARQ, each on its own
  destination L2 ID). Measured occupied channel busy ratio (CBR) in this campaign: ≈ 0.01
  (N 0, periodic1 leader), 0.30 (N 45), 0.38 (N 60); with the periodic2 leader 0.17 / 0.41
  / 0.48.

### Leader traffic

| profile | period | size | packet delay budget (PDB) | grant |
|---|---|---|---|---|
| periodic1 | 100 ms | 190 B or 300 B | 100 ms | SB-SPS, RRI 100 ms |
| periodic2 | 10 ms | 800 B (p = 0.8) / 1200 B (p = 0.2) | 10 ms | SB-SPS, RRI 10 ms |

One run lasts the whole SUMO trace; KPIs are counted from 3.0 s to 53.8 s (≈ 508 packets
for periodic1, ≈ 5 080 for periodic2, per run).

### Design

7 strategies × N {0, 45, 60} × traffic {periodic1, periodic2} × seeds 1–10 = **420 runs**.
A seed fixes the SUMO trace (background traffic) and the ns-3 RNG; the same seed has the
same trace in every strategy, which is what makes the per-seed (paired) comparison valid.

---

## 3. Simulator settings

### Radio and stack

| parameter | value |
|---|---|
| simulator | ns-3.42 + CTTC 5G-LENA NR (NR-V2X Mode 2), repo patches (`coop-harq-nr`), optimized build |
| carrier / bandwidth | 5.89 GHz, 20 MHz |
| numerology | μ = 1 (30 kHz SCS, 0.5 ms slots) |
| sidelink pool | 5 subchannels × 10 PRB; 9 SL slots of every 20 physical slots (bitmap `111111000111` on the UL slots of TDD `DL DL DL F UL UL UL UL UL UL`), SL slot share 0.45 → 900 SL slots/s |
| MCS | 14 (16QAM, R ≈ 0.54), fixed (`mcs_rx_min = mcs_rx_max = 14` in every run) |
| TX power | 23 dBm, spread over the allocated PRBs |
| noise figure (selector link budget) | 9 dB |
| resource selection | sensing-based, T1 = 2 slots, T2 = min(33 slots, PDB); RSRP threshold −128 dBm (`sl_thres_rsrp`) |
| HARQ | groupcast Option 1 (NACK-only, `GroupcastNegativeOnly`), sl-MaxTxTransNumPSSCH = 3 (initial + 2 retx), sl-MaxNumPerReserve = 3, sl-PSFCH-Period = 1, MinTimeGapPSFCH = 3 |
| RLC | UM, t-Reordering = 0 ms |
| channel | 3GPP TR 37.885 path loss + shadowing + fast fading; geometric NLOSv (every vehicle a 5 × 1.8 m blocker); buildings (roundabout) give NLOS |
| leader grant counts | periodic1: 2.9 reserved slots per period on average (≈ 1.6 subchannels, ≈ 290 B); periodic2: 1.35–1.65 slots per period (≈ 4.6 subchannels, ≈ 1 060 B) |

### SB-SPS re-run configuration (base flags of every run)

These are the "standard Mode-2" fixes introduced for the SB-SPS re-run (README,
"Standard Mode-2 grant types and sensing fixes"). They apply to every UE.

| program flag | value | meaning |
|---|---|---|
| `--leaderSps` | `true` | periodic leader traffic on SB-SPS (RRI = period) instead of a dynamic grant per packet |
| `--spsReselectOnMisfit` | `true` | TS 38.321 §5.22.1.1: an SDU the SPS grant cannot carry triggers a reselection (or enlarges the allocation if it fits the TB) instead of CTTC's segmentation across periods |
| `--slDynExclusion` (D1) | `true` | TS 38.214 §8.1.4 step 6 with C_resel = 1 for dynamic grants: dynamic selections exclude sensed reservations |
| `--slProjectAnnounced` (D2) | `true` | step 6 also projects the retransmission resources an SCI-1A announces in its own period |
| `--slProbResourceKeep` | `0.8` | sl-ProbResourceKeep at its maximum; with this probability the same SPS resource is kept for another counter cycle (5G-LENA never applied it before) |
| `--rriList` | `0,10,20,50,100,200,…,1000` | sl-ResourceReservePeriodList, 14 entries (RRC allows ≤ 16; contains 10 ms and 100 ms) |

### Strategy-specific flags (campaign-wide)

| program flag | value | meaning |
|---|---|---|
| `--helperMode` | `none` / `leaderRes` | `leaderRes`: the leader hands the reserved retransmission slots after its first transmission to the helper named in SCI-2A |
| `--helper` | `4` | static helper and fallback member; also the sensing-observer role for the attribution logs |
| `--helperSelect` | `static` (unset) / `kuo` / `map` | per-TB selection policy |
| `--helperBlind` | `false` / `true` | `true` sets `NrSlUeMac::HelperNackTriggered = false`: the helper relays in every handed slot |
| `--camPeriodMs` | `100` | how often member positions are sampled (CAM period) |
| `--helperDecisionMs` | `0` | processing delay before a CAM sample may be used |
| `--helperSelectShadow` | `false` | compute and log, but signal member 4 (used only in the identity check) |
| `--kuoSinrReqDb` / `--kuoPlMaxDb` | `8` dB / `0` (derived) | link-budget threshold for MCS 14; 0 = PL_max from the TB's PRBs |
| `--collCond` | `true` | collision-condition attribution log |
| `--writeAtt` | `true` | `_att.csv.gz`: every platoon transmission × member, with status, SINR, channel condition and deterministic geometry |

### Derived values used by the selector

| quantity | value |
|---|---|
| PL_max (1 subchannel, 10 PRB) | 114.4 dB |
| PL_max (2 subchannels) | 111.4 dB (typical periodic1 TB) |
| PL_max (4 / 5 subchannels) | 108.4 / 107.4 dB (typical periodic2 TB) |
| P_LOS(d) (kuo; also map when no building or vehicle is on the segment) | min(1, 1.05 · exp(−0.0114 d)) |
| NLOSv extra loss | max{0, N(μ, 4.5 dB)}, μ = 9 + max(0, 15 log10 d − 41) |
| shadowing σ (LOS / NLOS) | 3 / 4 dB |
| CAM age at decision | periodic1 ≈ 30–35 ms mean; periodic2 ≈ 49–51 ms mean (p95 up to ~92 ms) |
| decision → new transmission | 1 ms (T1) |
| leader first transmission → first helper copy | ≈ 5–6 ms mean, p95 6–9 ms |

---

## 4. The seven strategies

In every strategy except `none`, the leader sends the TB's first transmission itself. It
then hands the remaining reserved slots of that period to **one** member, named in SCI-2A,
who relays a copy in those slots.

| strategy | who relays | when the relay transmits | flags |
|---|---|---|---|
| `none` | nobody: the leader keeps its own reserved retransmissions (standard Mode-2 NACK-only HARQ) | leader retransmits after a NACK | `--helperMode=none` |
| `s4Nack` | always member 4, the middle car (Kuo's "middle Tx" baseline) | only after a member sends a NACK (Option 1) | `--helperMode=leaderRes --helper=4` |
| `s4Blind` | member 4 | in every handed slot, no feedback needed | + `--helperBlind=true` |
| `kuoNack` / `kuoBlind` | chosen per TB (Kuo eq. 17, distance-only LOS model, **no building map**) | NACK / blind | + `--helperSelect=kuo` |
| `mapNack` / `mapBlind` | chosen per TB, same score, link conditions from the building map + positions | NACK / blind | + `--helperSelect=map` |

Some terms used throughout:

- **NACK-triggered (Option 1).** A member that fails to decode sends a NACK on the shared
  PSFCH resource. The helper overhears it and relays only then. A PSFCH carries no HARQ
  process id, so the helper ties the NACK to the stored TB by time (PSFCH occasion + 1 slot
  of decode latency). The checks found no relay without a preceding NACK
  (`relay_without_nack = 0`, `relay_without_initial_nack = 0`).
- **Blind.** The helper relays in every slot handed over to it, whether anyone needs the copy
  or not.
- **The helper can only relay what it decoded.** If it missed the leader's first transmission
  it stays silent (`relay_without_decode = 0` in every run).
- **The helper loses its own retransmissions.** The slots are handed away, and the helper
  doesn't receive its own relayed copy. So the named member only gets the leader's first
  transmission. This drives the "dip" in the distance plots (§7.1).

---

## 5. How the selector works

`helper_select.{h,cc}`, called by `NrSlCoopScheduler::SetHelperSelector` when the leader
publishes a period's grant, after its resource selection. It returns the member written into
that period's SCI-2A.

For each candidate helper i ∈ {1..8} and each receiver r ∈ {1..8}:

```
GPRR(i) = (1/8) · Σ_r  [ 1 − Pf(L→r) · ( Pf(L→i) + (1 − Pf(L→i)) · Pf(i→r) ) ]   (r ≠ i)
          + for r = i: P(L→i)      (the helper only gets the leader's transmission)
Pf = 1 − P(success),  P(success) = P( path loss + shadowing ≤ PL_max of the TB )
```

The leader picks argmax_i GPRR(i). Ties go to the lowest index.

- **What the two policies feed in.**
  - `kuo`: link condition from distance only. P_LOS(d) mixes LOS and NLOSv path loss; no
    buildings.
  - `map`: a building on the segment between the two CAM positions → NLOS; another platoon
    car within 0.9 m of the segment → NLOSv; otherwise the LOS / NLOSv mixture (the
    simulator's own condition model).
- **Inputs are what a leader could know:** member positions from CAM samples every 100 ms,
  the building map, and the TB's allocation size (which sets PL_max).
- **The selector does not query the channel model and draws no random numbers.**
- **No interference with resource allocation.** The selector only writes the helper id of a
  period that is already selected: no slot, subchannel, counter, keep / misfit or sensing
  input changes. Verified by shadow mode (`--helperSelect=map --helperSelectShadow=true`
  computes and logs the selection but signals member 4). It is byte-identical to
  `leaderRes` in every output file, for SB-SPS periodic1 / periodic2 and dynamic
  aperiodic1, roundabout N 0 and N 45. The server's `check` step repeated this ("identical").
- **The helper is only named when there is something to hand over.**
  `nr_sl_coop_scheduler.cc:1690`: the `leaderRes` branch runs only if
  `grant.slotAllocations.size() > 1`. A single-slot grant (no reserved retransmission) is
  sent once by the leader, with no helper and no selector call. This matters for periodic2
  (§9).

---

## 6. Metrics and statistics

| metric | definition |
|---|---|
| member PRR within PDB (`prr_pdb`) | packets a member's application received within the PDB / packets the leader generated |
| tail PRR | `prr_pdb` of member 8 (the pre-registered metric) |
| **worst-RX PRR** | min over the 8 members of `prr_pdb` in a run. It is member 8 in 380 / 420 runs, member 7 in 30, members 5 / 6 in 10 |
| **GPRR** (in this document and the figures) | mean over the 8 members of `prr_pdb` (Kuo's metric, with the deadline) |
| `gprr` column of `dyn_runs.csv` / paired GPRR tests of `analyze_dynhelper.py` | mean member PRR **without** the PDB (`prr_mean`). Equal to the above for periodic1; for periodic2 about 0.03 higher on average, up to 0.31 in single seeds (multi-TB packets finishing late) |
| tx/pkt | PSSCH transmissions by the leader + helper per generated packet |
| `cbr_occupied` | channel busy ratio with parked (idle) background seats excluded |

**Statistics.**

- **Paired tests.** The tests are paired per seed. For each seed, arm − reference on the same
  trace and RNG, then the mean of the 10 differences ± its 95 % t-interval. "**better**" /
  "worse" means the interval excludes 0; "(k/10)" = seeds where the arm is better.
- **Unpaired error bars in the figures.** The figure error bars and bands are unpaired 95 %
  CIs over seeds. Seeds differ strongly in trace and load, so those bars are much wider than
  the paired intervals. Overlapping bars in a figure do **not** mean "no difference".

**Pre-registered rules** (fixed before the server run, `analyze_dynhelper.py`):

1. Dynamic selection pays if `mapNack` beats `s4Nack` on tail `prr_pdb`, paired, roundabout
   N 0 periodic1 (`kuoNack` reported the same way).
2. Feedback pays over blind if, for the same selection, NACK ≥ blind on tail `prr_pdb`
   (paired n.s. or better) at lower tx/pkt; reported for N 0 / 45 / 60.
3. Collisions / hidden nodes: the paired gains vs `none` at N 45 / 60 against N 0.

---

## 7. Results

### 7.1 PRR versus distance from the leader

![PRR vs distance](figures/dynhelper_sps/fig_d1_prr_vs_distance.png)

*Each member's PRR within the PDB against its mean distance from the leader. Rows: traffic;
columns: background N. Colour = selection policy (grey `none`, blue `s4`, orange `kuo`,
green `map`); solid = NACK, dashed = blind; band = 95 % CI over 10 seeds.*

Mean member PRR within PDB, periodic1 N 0 (other cells in §7.4):

| arm | m1 | m2 | m3 | m4 | m5 | m6 | m7 | m8 (tail) |
|---|---|---|---|---|---|---|---|---|
| `none` | 1.000 | 0.997 | 0.971 | 0.908 | 0.799 | 0.715 | 0.633 | 0.556 |
| `s4Nack` | 1.000 | 0.994 | 0.919 | **0.763** | 0.815 | 0.770 | 0.692 | 0.630 |
| `s4Blind` | 1.000 | 0.995 | 0.929 | **0.761** | 0.816 | 0.766 | 0.687 | 0.624 |
| `kuoNack` | 0.999 | 0.979 | 0.972 | 0.955 | 0.898 | 0.812 | 0.703 | 0.617 |
| `kuoBlind` | 1.000 | 0.984 | 0.968 | 0.954 | 0.892 | 0.807 | 0.700 | 0.614 |
| `mapNack` | 1.000 | 0.988 | 0.965 | 0.952 | 0.903 | **0.840** | **0.772** | **0.688** |
| `mapBlind` | 1.000 | 0.986 | 0.963 | 0.945 | 0.907 | 0.832 | 0.751 | 0.690 |

What the curves show:

- **The fixed helper pays with its own reliability.** Under `s4`, member 4 (≈ 136 m) drops
  from 0.91 (`none`) to 0.76. The helper only receives the leader's first transmission,
  since the retransmission slots are now its own to send in. Member 3 also loses a little
  (0.97 → 0.92): it no longer gets the leader's retransmissions and is close enough to the
  leader that a relay from member 4 is no better. Behind member 4 every member gains (tail
  0.56 → 0.63). Net effect on GPRR ≈ 0.
- **`kuo` hides that cost.** It names member 2 (≈ 69 m) almost always, a member that decodes
  the leader nearly always anyway, so the dip is invisible (member 2: 0.997 → 0.98). The
  relay from member 2 helps the middle members (3–6) the most: member 4 0.91 → 0.95,
  member 5 0.80 → 0.90. But it reaches the tail no better than member 4 does (0.62 vs
  0.63).
- **`map` gets both.** It has the best curve beyond ≈ 150 m (members 5–8). The tail is 0.69
  vs 0.63 (`s4`) and 0.56 (`none`). It mostly uses member 2 too, but it switches to a rear
  member (4–6) in the TBs where the map shows that member has a building-free path on both
  hops. Those are the TBs where the tail would otherwise be lost.
- **Load (N 45 / 60) changes the levels, not the shape** in periodic1. Same ordering:
  `map` > `s4` ≈ `kuo` > `none` at the tail; `kuo` ≈ `map` > `none` ≈ `s4` in the middle.
- **In periodic2 all curves collapse together.** The tail is at 0.36–0.45 and the spread
  between strategies is within the seed-to-seed noise. The `s4` dip at member 4 is still
  visible at N 0 (0.72 → 0.65).

### 7.2 GPRR and worst-RX PRR versus background traffic

![GPRR and worst-RX vs load](figures/dynhelper_sps/fig_d2_gprr_worst_vs_load.png)

*Top: GPRR (mean member PRR within PDB). Bottom: worst-RX PRR. Columns: traffic. Mean over
10 seeds, unpaired 95 % CI.*

| metric | traffic | arm | N 0 | N 45 | N 60 |
|---|---|---|---|---|---|
| GPRR | periodic1 | `none` | 0.822 | 0.833 | 0.825 |
| | | `s4Nack` / `s4Blind` | 0.823 / 0.822 | 0.834 / 0.831 | 0.817 / 0.815 |
| | | `kuoNack` / `kuoBlind` | 0.867 / 0.865 | 0.862 / 0.866 | 0.862 / 0.862 |
| | | `mapNack` / `mapBlind` | **0.889** / 0.884 | 0.872 / **0.886** | **0.877** / 0.875 |
| GPRR | periodic2 | `none` | 0.700 | 0.674 | 0.619 |
| | | `s4Nack` / `s4Blind` | 0.693 / 0.687 | 0.659 / 0.653 | 0.622 / 0.590 |
| | | `kuoNack` / `kuoBlind` | 0.705 / 0.715 | 0.686 / 0.686 | 0.606 / 0.624 |
| | | `mapNack` / `mapBlind` | **0.733** / 0.728 | 0.662 / 0.684 | 0.635 / **0.638** |
| worst RX | periodic1 | `none` | 0.556 | 0.602 | 0.571 |
| | | `s4Nack` / `s4Blind` | 0.630 / 0.620 | 0.646 / 0.644 | 0.622 / 0.614 |
| | | `kuoNack` / `kuoBlind` | 0.617 / 0.614 | 0.632 / 0.627 | 0.612 / 0.621 |
| | | `mapNack` / `mapBlind` | 0.688 / **0.690** | 0.670 / **0.688** | **0.679** / 0.674 |
| worst RX | periodic2 | `none` | 0.400 | 0.391 | 0.343 |
| | | `s4Nack` / `s4Blind` | 0.427 / 0.419 | 0.383 / 0.386 | 0.369 / 0.346 |
| | | `kuoNack` / `kuoBlind` | 0.410 / 0.421 | **0.422** / 0.408 | 0.347 / 0.356 |
| | | `mapNack` / `mapBlind` | **0.448** / 0.430 | 0.382 / 0.409 | **0.377** / **0.377** |

- **periodic1 is flat in N.** The roundabout loss is driven by buildings, not by
  collisions, so N 45 / 60 move the curves by ≤ 0.05. The `none` worst-RX even rises at
  N 45 (0.60 vs 0.56). This is a trace effect: a different SUMO trace per N, so the
  platoon's position in the turn differs. The paired gains (below) are the reliable
  quantity.
- **periodic2 falls with N** for every strategy (GPRR ≈ 0.70–0.73 → 0.59–0.64).
  Collisions and single-slot grants dominate there; the helper choice has little to work
  with.

### 7.3 Paired comparisons (the inference)

Tail `prr_pdb`, arm − reference, mean ± 95 % CI of the per-seed difference, (seeds better /
10):

| cell | s4Nack − none | kuoNack − s4Nack | mapNack − s4Nack | mapBlind − s4Nack | mapNack − none |
|---|---|---|---|---|---|
| periodic1 N 0 | **+0.074 ± 0.023 (10)** | −0.013 ± 0.025 (4) | **+0.058 ± 0.026 (10)** | **+0.060 ± 0.036 (9)** | **+0.132 ± 0.026 (10)** |
| periodic1 N 45 | **+0.044 ± 0.017 (10)** | worse −0.015 ± 0.014 (3) | +0.023 ± 0.024 (6) | **+0.042 ± 0.018 (10)** | **+0.067 ± 0.034 (9)** |
| periodic1 N 60 | **+0.050 ± 0.019 (10)** | −0.010 ± 0.024 (4) | **+0.057 ± 0.028 (9)** | **+0.053 ± 0.024 (9)** | **+0.107 ± 0.024 (10)** |
| periodic2 N 0 | +0.023 ± 0.058 (5) | −0.021 ± 0.034 (4) | +0.018 ± 0.026 (7) | +0.005 ± 0.021 (5) | +0.041 ± 0.049 (8) |
| periodic2 N 45 | −0.015 ± 0.040 (5) | **+0.038 ± 0.035 (7)** | +0.001 ± 0.035 (4) | **+0.036 ± 0.028 (8)** | −0.014 ± 0.049 (4) |
| periodic2 N 60 | +0.028 ± 0.055 (7) | −0.025 ± 0.036 (2) | +0.003 ± 0.040 (6) | −0.004 ± 0.049 (5) | +0.031 ± 0.048 (6) |

GPRR without PDB (`gprr` column), paired vs `s4Nack`:

| cell | kuoNack | kuoBlind | mapNack | mapBlind |
|---|---|---|---|---|
| periodic1 N 0 | **+0.044 ± 0.011 (10)** | **+0.042 ± 0.011 (10)** | **+0.066 ± 0.011 (10)** | **+0.061 ± 0.010 (10)** |
| periodic1 N 45 | **+0.028 ± 0.009 (10)** | **+0.032 ± 0.010 (10)** | **+0.038 ± 0.011 (10)** | **+0.052 ± 0.007 (10)** |
| periodic1 N 60 | **+0.046 ± 0.017 (10)** | **+0.046 ± 0.018 (10)** | **+0.061 ± 0.015 (10)** | **+0.059 ± 0.014 (10)** |
| periodic2 N 0 | **+0.017 ± 0.009 (10)** | **+0.027 ± 0.012 (10)** | **+0.054 ± 0.007 (10)** | **+0.051 ± 0.010 (10)** |
| periodic2 N 45 | **+0.024 ± 0.012 (10)** | +0.018 ± 0.022 (7) | +0.022 ± 0.027 (7) | **+0.035 ± 0.023 (9)** |
| periodic2 N 60 | +0.001 ± 0.019 (5) | +0.002 ± 0.023 (5) | **+0.025 ± 0.014 (9)** | **+0.026 ± 0.018 (9)** |

`s4Nack` vs `none` on GPRR: never significant (−0.009 to +0.001), i.e. the fixed helper
moves reliability from member 3–4 to the rear but does not add any in total.

NACK vs blind (same policy), tail `prr_pdb` and cost:

| cell | s4: NACK − blind | kuo: NACK − blind | map: NACK − blind | tx/pkt NACK vs blind (s4 / kuo / map) |
|---|---|---|---|---|
| periodic1 N 0 | +0.006 ± 0.025 | +0.003 ± 0.022 | −0.002 ± 0.028 | 1.50 / 1.81 / 1.71 vs 2.47 / 2.85 / 2.81 |
| periodic1 N 45 | +0.003 ± 0.012 | +0.005 ± 0.015 | −0.018 ± 0.036 | 1.47 / 1.75 / 1.64 vs 2.46 / 2.79 / 2.76 |
| periodic1 N 60 | +0.008 ± 0.018 | −0.009 ± 0.022 | +0.004 ± 0.021 | 1.50 / 1.78 / 1.67 vs 2.30 / 2.74 / 2.66 |
| periodic2 N 0 | +0.011 ± 0.029 | −0.014 ± 0.032 | +0.013 ± 0.023 | 1.36 / 1.52 / 1.58 vs 1.54 / 1.72 / 1.77 |
| periodic2 N 45 | −0.004 ± 0.019 | +0.013 ± 0.033 | **worse −0.035 ± 0.031** | 1.18 / 1.29 / 1.26 vs 1.25 / 1.33 / 1.37 |
| periodic2 N 60 | +0.025 ± 0.032 | −0.000 ± 0.037 | +0.006 ± 0.032 | 1.21 / 1.24 / 1.33 vs 1.27 / 1.36 / 1.37 |

### 7.4 Per-member PRR in the loaded cells

periodic1 N 60:

| arm | m1 | m2 | m3 | m4 | m5 | m6 | m7 | m8 |
|---|---|---|---|---|---|---|---|---|
| `none` | 1.000 | 0.997 | 0.961 | 0.904 | 0.806 | 0.717 | 0.645 | 0.571 |
| `s4Nack` | 0.997 | 0.989 | 0.918 | 0.763 | 0.806 | 0.752 | 0.685 | 0.622 |
| `kuoNack` | 0.999 | 0.972 | 0.963 | 0.949 | 0.883 | 0.804 | 0.714 | 0.612 |
| `mapNack` | 0.998 | 0.982 | 0.956 | 0.937 | 0.896 | 0.836 | 0.735 | 0.679 |

periodic2 N 0 and N 60:

| arm | cell | m1 | m2 | m3 | m4 | m5 | m6 | m7 | m8 |
|---|---|---|---|---|---|---|---|---|---|
| `none` | N 0 | 0.989 | 0.975 | 0.843 | 0.719 | 0.638 | 0.566 | 0.462 | 0.407 |
| `s4Nack` | N 0 | 0.983 | 0.963 | 0.824 | 0.654 | 0.617 | 0.569 | 0.504 | 0.430 |
| `kuoNack` | N 0 | 0.984 | 0.955 | 0.812 | 0.768 | 0.640 | 0.576 | 0.495 | 0.410 |
| `mapNack` | N 0 | 0.983 | 0.956 | 0.893 | 0.786 | 0.679 | 0.600 | 0.514 | 0.448 |
| `none` | N 60 | 0.916 | 0.886 | 0.741 | 0.633 | 0.525 | 0.466 | 0.424 | 0.360 |
| `s4Nack` | N 60 | 0.908 | 0.868 | 0.727 | 0.604 | 0.528 | 0.510 | 0.442 | 0.388 |
| `kuoNack` | N 60 | 0.894 | 0.851 | 0.701 | 0.630 | 0.514 | 0.482 | 0.413 | 0.363 |
| `mapNack` | N 60 | 0.913 | 0.865 | 0.748 | 0.658 | 0.540 | 0.501 | 0.460 | 0.390 |

### 7.5 Where the gain comes from: blockage geometry (periodic1)

Tail delivery split by the deterministic leader → tail geometry (B = a building blocks it,
V = only vehicles block it):

| cell | LT | none | s4Nack | s4Blind | kuoNack | kuoBlind | mapNack | mapBlind |
|---|---|---|---|---|---|---|---|---|
| N 0 | B | 0.063 | 0.225 | 0.209 | 0.187 | 0.178 | **0.345** | 0.339 |
| N 0 | V | 0.971 | 0.970 | 0.972 | 0.978 | 0.980 | 0.976 | 0.983 |
| N 45 | B | 0.076 | 0.192 | 0.180 | 0.145 | 0.143 | 0.254 | **0.289** |
| N 45 | V | 0.970 | 0.963 | 0.961 | 0.970 | 0.969 | 0.964 | 0.974 |
| N 60 | B | 0.058 | 0.172 | 0.160 | 0.173 | 0.178 | **0.306** | 0.290 |
| N 60 | V | 0.935 | 0.942 | 0.941 | 0.936 | 0.951 | 0.953 | 0.959 |

- **All the action is in building-blocked TBs.** With only vehicles in the way, the leader
  reaches the tail ≈ 97 % of the time with or without a helper. When a building blocks
  leader → tail, `none` delivers 6 %, a fixed or distance-only helper 15–23 %, and the
  map-aware helper 25–35 %.
- **This is where geometry knowledge pays.** Kuo's model treats a 260 m link as "probably
  NLOSv" regardless of the buildings in between, so it can't know that member 2 is behind
  the same building as the leader.

### 7.6 Cost and side effects

- **Channel load.** `cbr_occupied` is unchanged within ±0.005 for NACK arms (periodic1);
  blind adds ≈ +0.004. In periodic2 blind adds +0.01–0.02.
- **Helper transmissions per run** (periodic1 N 0): `s4Nack` 245, `kuoNack` 396,
  `mapNack` 346; blind arms ≈ 700–900. NACK arms relay 2–3× less.
- **Helper changes.** `map` changes the helper ≈ 0.23–0.28 times/s (periodic1),
  0.12–0.23 (periodic2); `kuo` 0.03–0.08 times/s. Every change is free here: it costs
  nothing but a different id in SCI-2A.
- **Relays by an unchosen member:** 0 in all 420 runs.

---

## 8. Which member gets selected

![helper choice](figures/dynhelper_sps/fig_d3_helper_choice.png)

*Share of the helper-assigned TBs on which each member was named in SCI-2A. Solid = NACK,
hatched = blind. `kuo` / `map`: from the per-TB selector log `_hsel.csv`. `s4`: member 4 by
construction. `none` has no helper.*

| strategy | periodic1 (N 0 / 45 / 60) | periodic2 (N 0 / 45 / 60) |
|---|---|---|
| `s4Nack`, `s4Blind` | member 4 on 100 % of helper TBs (fixed) | member 4 on 100 % |
| `kuoNack`, `kuoBlind` | member 2: 93–95 %, member 3: 5–6 %, member 1: < 0.5 % | member 3: 75–87 %, member 2: 13–25 % |
| `mapNack`, `mapBlind` | member 2: 68–74 %, member 3: 10–13 %, members 4, 5, 6: 4–8 % each | about the same: member 2 ≈ 71–77 %, member 3 ≈ 8–11 %, members 4–6 ≈ 2–8 % |
| `none` | no helper | no helper |

Exact shares (`helper_choice_counts.csv`):

| traffic | N | arm | m1 | m2 | m3 | m4 | m5 | m6 | m7 | m8 |
|---|---|---|---|---|---|---|---|---|---|---|
| periodic1 | 0 | kuo (both) | 0.004 | 0.933 | 0.063 | 0 | 0 | 0 | 0 | 0 |
| periodic1 | 0 | map (both) | 0 | 0.680 | 0.126 | 0.052 | 0.064 | 0.077 | 0 | 0 |
| periodic1 | 45 | kuo (both) | 0.003 | 0.949 | 0.048 | 0 | 0 | 0 | 0 | 0 |
| periodic1 | 45 | map (both) | 0 | 0.736 | 0.103 | 0.044 | 0.053 | 0.064 | 0 | 0 |
| periodic1 | 60 | kuo (both) | 0.003 | 0.946 | 0.051 | 0 | 0 | 0 | 0 | 0 |
| periodic1 | 60 | map (both) | 0 | 0.711 | 0.119 | 0.046 | 0.056 | 0.068 | 0 | 0 |
| periodic2 | 0 | kuo (both) | 0 | 0.249 | 0.751 | 0 | 0 | 0 | 0 | 0 |
| periodic2 | 0 | map (both) | 0 | 0.712 | 0.110 | 0.054 | 0.052 | 0.073 | 0 | 0 |
| periodic2 | 45 | kuoNack / kuoBlind | 0 | 0.191 / 0.134 | 0.809 / 0.866 | 0 | 0 | 0 | 0 | 0 |
| periodic2 | 45 | mapNack / mapBlind | 0 | 0.762 / 0.774 | 0.083 / 0.090 | 0.055 / 0.017 | 0.036 / 0.041 | 0.064 / 0.078 | 0 | 0 |
| periodic2 | 60 | kuoNack / kuoBlind | 0 | 0.188 / 0.220 | 0.812 / 0.780 | 0 | 0 | 0 | 0 | 0 |
| periodic2 | 60 | mapNack / mapBlind | 0 | 0.727 / 0.748 | 0.092 / 0.100 | 0.053 / 0.038 | 0.049 / 0.047 | 0.079 / 0.067 | 0 | 0 |

How to read it:

- **Members 7 and 8 are never chosen, and member 1 almost never.** In the score, a helper near
  the tail rarely decodes the leader's first transmission. Member 1 adds little beyond the
  leader itself: it sits 35 m behind it, so its links to the rear are almost the leader's.
- **`kuo` jumps from member 2 to member 3 between traffic types.** Its distance-only model
  reacts only to the link budget, which depends on TB size. periodic1 TBs mostly use
  2 subchannels (PL_max 111.4 dB); periodic2 TBs use 5 (107.4 dB). That budget change alone
  moves the choice. Example, one seed at N 0: periodic1, 478 / 522 decisions at 2
  subchannels → member 2; periodic2, 3 699 / 4 469 at 5 subchannels → member 3.
- **`map` keeps the same pattern in both traffic types**, because buildings, not TB size,
  drive its choice. It is the only strategy that sometimes names a rear member (4–6), when
  the map shows those members have a clear path on both hops. Those are the TBs where it
  beats the fixed member 4 (§7.5).
- **NACK and blind make the same choice for the same policy.** In periodic1 the shares are
  identical: the choice uses only CAM positions, the map and the TB size, and the leader's
  grants are the same in both arms. In periodic2 they differ slightly because the runs
  themselves drift apart (different helper transmissions → different sensing by background
  UEs → different leader grants).
- **Why `s4` has no selector log.** The s4 runs don't log which TBs got a helper, because the
  selector doesn't run for them. Their bars mean "member 4 on every TB that had a helper" by
  construction, and `relay_by_unchosen = 0` across all 420 runs confirms no other member
  relayed.

---

## 9. Helper coverage: not every TB gets a helper

![helper coverage](figures/dynhelper_sps/fig_d3b_helper_coverage.png)

*Share of the leader's TBs whose grant had more than one reserved slot, i.e. that got a
helper. kuo / map arms (selector rows / leader TBs, `helper_coverage.csv`).*

The leader names a helper only when its grant has more than one reserved slot
(`nr_sl_coop_scheduler.cc:1690`). A single-slot grant has no retransmission to hand over,
so that TB goes out once with no help.

| traffic | N 0 | N 45 | N 60 |
|---|---|---|---|
| periodic1 | 100 % | 100 % | 100 % |
| periodic2 | 85 % (55.7–100 % per seed) | 38–49 % (15–78 % per seed) | 41–49 % (17–79 % per seed) |

(periodic1 shows 100.1–100.2 %: the selector runs once more per run than there are TBs in
the KPI window, at the window edge.)

- **The run CSVs show the same thing.** `leader_grant_slots_mean` is 2.9 for periodic1 and
  1.35–1.65 for periodic2.
- **Why periodic2 grants are short.** Packets are 800–1200 B every 10 ms. A TB needs ≈ 4.6
  of the 5 subchannels, the selection window is ≤ 20 slots (PDB 10 ms), and the pool has
  9 SL slots per 20. With the PSFCH gaps and the sensing exclusions under load, the
  scheduler often finds no second resource in time.
- **Under background load, more than half of periodic2 TBs get a one-slot grant.** Neither
  the helper choice nor NACK versus blind can then make a difference on them. This likely
  explains much of why the gains disappear in periodic2 under load, though it hasn't been
  tested separately (e.g. by conditioning PRR on multi-slot TBs).
- **The s4 arms have no coverage log.** Their coverage should be close to the kuo / map
  numbers, since the rule is the same and the grants depend only on the leader's resource
  selection, but it isn't measured.

---

## 10. Helper choice over time

The selector log `_hsel.csv` has one row per helper-assigned TB with `t_ndi`, the time of
the TB's first transmission. So the helper choice can be plotted against time directly. The
leader → tail geometry of every leader transmission (`_att.csv`, `geo_tx = 0`: a building
on the segment) gives the times when a building blocks the tail. It is the same in every
strategy of a seed, since the selector does not change the trace or the grants.

![helper over time, periodic1](figures/dynhelper_sps/fig_d4_periodic1_s1.png)

*periodic1, seed 1, N = 0 / 45 / 60. Upper panels: member signalled in SCI-2A per TB
(`kuoNack` orange, `mapNack` green, shifted −0.12 / +0.12 so they don't overlap; `s4Nack`
dashed at 4). Grey band: a building blocks leader → tail. Lower strips: one tick per TB the
tail did not decode from any transmission (leader or helper), per strategy.*

![helper over time, periodic2](figures/dynhelper_sps/fig_d4_periodic2_s1.png)

*periodic2, seed 1. Same layout. A dot per helper-assigned TB; gaps are single-slot TBs that
have no helper (§9). The lower strips show the tail loss rate per 1 s bin, because at 100
TBs/s single ticks merge into solid bars.*

![helper share over time](figures/dynhelper_sps/fig_d5_helper_share_time.png)

*N = 0, all 10 seeds pooled: share of the helper-assigned TBs of each 1 s bin that went to
each member (light = front, dark = rear). Black line: share of the leader's TBs in the bin
with a building between leader and tail. Pooled at N 0 only, because the platoon reaches
the turn at about the same time in every N 0 trace (blocked interval ≈ 22–44 s, 17.6–47.1 s
over seeds). Under load the timing varies from trace to trace: one N 45 trace reaches it
at 126 s, one N 60 trace at 92 s.*

Switches per run and median time between switches, mean over 10 seeds (a switch counts as
"in the turn" if it falls within ±1 s of the blocked interval):

| traffic | N | blocked share of TBs | `kuo` switches (in turn) | `kuo` median hold | `map` switches (in turn) | `map` median hold | highest member `map` uses in the turn |
|---|---|---|---|---|---|---|---|
| periodic1 | 0 | 0.45 | 4.0 (4.0) | 3.0 s | 13.5 (13.5) | 2.3 s | 6 (every seed) |
| periodic1 | 45 | 0.40 | 4.2 (4.2) | 2.9 s | 13.3 (13.3) | 2.5 s | 6 (every seed) |
| periodic1 | 60 | 0.42 | 4.4 (4.4) | 3.9 s | 13.7 (13.7) | 2.5 s | 6 (every seed) |
| periodic2 | 0 | 0.45 | 2.6 (2.5) | 11.0 s | 10.8 (10.2) | 2.4 s | 6 (every seed) |
| periodic2 | 45 | 0.40 | 2.4 (2.1) | 10.6 s | 6.6 (6.0) | 1.9 s | 5.6 (3–6) |
| periodic2 | 60 | 0.42 | 2.0 (1.9) | 10.0 s | 8.1 (7.3) | 2.2 s | 5.9 (5–6) |

What the time axis shows:

1. **The helper choice follows the turn.**
   - Outside the blocked interval (before ≈ 23 s and after ≈ 46 s in seed 1), both `kuo`
     and `map` stay on member 2 (periodic1). For periodic2, `kuo` stays on member 3, the
     smaller link budget of §8.
   - Inside it, `map` (seed 1, N 0) steps through 4 → 3 → 2 → 3 → 2 → 3 → 2 → 3 → 4 → 5 → 6, then drops back
     to 2 as soon as the platoon leaves the turn. As the platoon goes round the roundabout,
     the member with a building-free path on both hops moves towards the rear.
   - In every seed, every load and both traffic types, `map` reaches member 6 by the end of
     the turn. The one exception is periodic2 under load, where some seeds stop at 3 or 5:
     there the TBs near the end of the turn are often single-slot and get no helper.
2. **`kuo` barely moves.** It makes 4 switches per run in periodic1 (2 → 3 → 2, plus one
   brief 1) and about 2 in periodic2. It reacts only to distance changes as the platoon
   compresses or stretches in the curve; it can't see the building. That is why it does no
   better than the fixed member 4 at the tail.
3. **The `map` choice is stable.** It makes 13–14 switches per periodic1 run, all within the
   blocked interval (±1 s), and holds each choice for a median of 2.3–2.5 s. This matches the
   ≈ 0.28 changes/s measured from the run CSV (13.5 switches over ≈ 51 s). The geometry
   changes slowly compared with the 100 ms CAM period, which is why positions 30–50 ms old
   are good enough (§3).
4. **The pattern is the same across seeds and load.** The pooled plot shows the same
   2 → 3 → 4 → 5 → 6 sequence in all 10 seeds, aligned with the rise and fall of the blocked
   share. Load shifts only *when* the platoon reaches the turn and how long it stays there:
   at N 60, seed 1 the blocked interval is 26–59 s instead of 23–46 s, probably because the
   platoon moves slower in heavier traffic.
5. **While a building blocks the tail, the tail loses most TBs in every strategy.**
   - periodic1: the loss ticks are dense inside the grey band for all three strategies;
     `map` thins them, mostly in the second half of the turn, where it uses a rear helper (4–6).
   - In the pooled geometry analysis (§7.5), the tail delivers 6 % of the building-blocked
     TBs with `none`, ≈ 22 % with the fixed member 4, and ≈ 35 % with `map`.
   - So map-aware selection rescues roughly a third of the TBs the building would otherwise
     cost, not most of them. Outside the turn, the three strategies lose the same rare TBs.
6. **periodic2 cannot be saved during the blockage.**
   - The tail loss rate is ≈ 100 % throughout the grey band in all strategies, at every load.
   - The gaps in the dotted helper lines are single-slot TBs without a helper; at N 60, seed 1
     almost the whole blocked interval has no helper decision.
   - This is the coverage limit of §9, now visible in time: when the helper is needed most,
     the short-deadline grants rarely have a slot to hand over.
   - The small periodic2 differences between strategies (§7.3) come from outside the turn
     and from its edges.

---

## 11. Conclusions

1. **Per-TB helper selection is worth it only if it knows where the buildings are.** On the
   roundabout the tail's losses come from building blockage. The map-aware rule raises the
   tail PRR over the fixed middle helper by +0.06 (N 0 and N 60, 9–10/10 seeds) and over
   leader-only HARQ by +0.07 to +0.13. Kuo's distance-only rule, as published, doesn't
   beat the fixed helper on the tail in any periodic1 cell: n.s. at N 0 and 60, slightly
   worse at N 45.

2. **GPRR and tail PRR rank strategies differently; report both.**
   - Kuo's objective is the mean member PRR, and on that metric `kuo` does well (+0.03 to
     +0.05 over `s4Nack`, 10/10 seeds). It gets there by improving members 3–6 with a
     near-front helper.
   - The fixed helper does the opposite: it helps the rear and costs its own reliability
     (member 4: 0.91 → 0.76), leaving GPRR unchanged.
   - Only `map` improves both, because it can choose a rear helper when that is what the
     geometry requires.
   - For a platoon, where the worst member sets the safe gap, the tail metric is the
     relevant one, and on it only `map` wins.

3. **Use NACK-triggered relaying, not blind.** In all 9 periodic1 cells NACK and blind are
   statistically equal on the tail (differences −0.018 to +0.008, all n.s.). NACK uses 35–40
   % fewer transmissions per packet (≈ 1.5–1.8 vs 2.3–2.9). Blind relaying, Kuo's choice,
   buys nothing here, because the NACK reaches the helper in time: the first copy follows the
   leader's transmission by ≈ 5–6 ms against a 100 ms PDB. The one significant periodic2
   difference (map, N 45: NACK worse by 0.035) is a single cell among 18 NACK-vs-blind
   comparisons, in the regime where coverage is lowest. It is not a pattern.

4. **The helper benefit is robust to background load for the 100 ms profile.** periodic1
   gains vs `none` shrink from N 0 to N 45 and come partly back at N 60. Rule 3: mapNack
   +0.132 / +0.067 / +0.107, s4Nack +0.074 / +0.044 / +0.050, kuoNack +0.061 / +0.029 /
   +0.041. They stay significant everywhere. Loss on the roundabout is dominated by
   buildings, not by collisions, at these CBRs (0.30–0.38).

5. **For the 10 ms profile the bottleneck is the grant, not the helper.**
   - periodic2 TBs fill almost the whole pool width with a ≤ 10 ms window.
   - Under load fewer than half of them get a second slot, and those that don't have no
     helper at all.
   - Tail differences between all seven strategies are within noise. Only `map` keeps a
     significant GPRR gain (≈ +0.025 to +0.05).
   - Improving periodic2 needs more retransmission opportunities first (a lower MCS / larger
     pool, longer PDB, or a helper on its own resources), before helper choice can matter.

6. **Position staleness is not limiting.** The map rule works with CAM positions 30–35 ms old
   (periodic1) and ≈ 50 ms old (periodic2, p95 ≈ 90 ms) at platoon speeds. The selection is
   decided 1 ms before the new transmission. Over time (§10) it makes 13–14 switches per run,
   all inside the turn, and holds each choice for a median of ≈ 2.5 s. So the geometry it
   reacts to (a building between two cars) lasts about 25 CAM periods.

7. **The helper moves to the rear as the platoon goes round the turn, and only the map sees
   it.** In every seed `map` steps from member 2 to member 6 across the blocked interval and
   returns to 2 when it ends; `kuo` stays at the front. Even so, the tail still loses about
   two thirds of the building-blocked TBs with the best strategy (§10). A single helper with
   one hand-over per TB leaves most of the blockage loss unrecovered.

8. **Implementation cost is low, and the selector is clean.** No NR change is needed: any
   member named in SCI-2A relays. The selector cannot change resource allocation (shadow mode
   byte-identical; no random draws), and no relay ever came from an unchosen member. The
   gains are therefore attributable to the choice of helper alone.

**Bottom line.** On an urban platoon with building blockage, choose the helper per TB using
the building map and CAM positions, and trigger it with Option-1 NACKs. This is the best
of the seven strategies on the tail (+0.06 over a fixed middle helper, +0.11–0.13 over
leader-only HARQ) at ≈ 1.7 transmissions per packet. Kuo's distance-only, blind variant
costs ≈ 2.8 transmissions per packet and doesn't improve the tail over the fixed helper.

---

## 12. Limitations and caveats

- **One scenario.** Roundabout only (IVD 30 m). The highway was not run with these arms: on
  the highway no helper arm beat `none` with disjoint CIs in the earlier campaign, so
  geometry-aware selection is not expected to matter there. Not tested.
- **10 seeds per cell.** periodic2 cells have large seed-to-seed spread (tail CI half-widths
  0.03–0.06 paired), so small effects there can't be resolved.
- **Ideal map and positions.** The map is exact, the CAMs are lossless and on time; position
  error and CAM loss are not modelled. The map model uses the same building geometry as the
  channel, so `map` is close to a best case for a map-aware rule. A real map and GNSS error
  would reduce the gain.
- **Single helper per TB.** Kuo's multi-helper variants were not tested.
- **The selector ignores interference and collisions.** It scores links by path loss only.
  It can't avoid a helper whose handed slot collides.
- **Coverage is inferred, not measured, for the s4 arms** (§9).
- **The GPRR definition differs between analyses.** The figures and §7.2 use the
  within-PDB GPRR; the paired GPRR tests (§7.3, from `analyze_dynhelper.py`) use the
  no-PDB `gprr`. For periodic1 they coincide.
- **Shared simulator caveats** (README / HANDOFF): 5G-LENA's PSFCH has no resources
  (lossless, matched by time); sensing / SPS fixes are this repo's opt-in implementations
  of the 3GPP rules.

---

## 13. How to reproduce

### 13.1 Build (once per machine)

```bash
# system packages (Debian/Ubuntu)
sudo apt install g++ cmake ninja-build python3-pip libsqlite3-dev libeigen3-dev sumo sumo-tools
python3 -m pip install --user pandas numpy matplotlib scipy
export SUMO_HOME=/usr/share/sumo
```

```bash
# repository at commit 2721fcd (branch hidden-node)
git checkout 2721fcd
./setup.sh                         # submodules at pinned SHAs + patches + scratch symlinks
cd ns-3-dev
./ns3 configure --build-profile=optimized --disable-werror
./ns3 build platoon_harq_robustness_eval
```

### 13.2 Run the campaign (server, 24 jobs, ≈ 5 h)

From `scratch_overlay/platoon_harq_robustness_eval`:

```bash
./run_dynhelper.sh build check
```

```bash
tmux new -d -s dynhelper -c "$PWD" 'JOBS=24 ./run_dynhelper.sh'
```

```bash
./run_dynhelper.sh status
```

What the driver does:

| step | action |
|---|---|
| `build` | `setup.sh --force` (reinstalls `patches/` into the submodules) and rebuilds the target |
| `check` | checks the binary has every required option; writes the wrapper `results/rerun_dyn/ns3_dyn` (binary + SB-SPS base flags) and `provenance.txt`; runs 4 smoke runs of 20 s (roundabout N 0 periodic1 seed 1): `leaderRes` reference, map in shadow mode (must be byte-identical to the reference), map, kuo blind (must have `relay_by_unchosen = 0`); verifies the run-CSV flags |
| `campaign` | `python3 run_coop.py --arms none s4Nack s4Blind kuoNack kuoBlind mapNack mapBlind --cells roundabout:0 roundabout:45 roundabout:60 --coll-cond --write-att --helper 4 --jobs $JOBS --results results/coop_dyn_sps --bin results/rerun_dyn/ns3_dyn`; resumable (a run with an existing `_run.csv` is skipped); every run checked (helper mode / member, t-Reordering 0, no explicit ACK, `leader_retx_without_nack = 0`, `relay_without_decode = 0`, `relay_into_reused_harq = 0`), then `verify_dir` checks the SB-SPS flags in all 420 run CSVs |
| `analyze` | `run_coop.py --analyze-only` (summary, figures in `analysis/`) and `analyze_dynhelper.py` (paired tables, pre-registered verdicts, geometry → `analysis_dyn/`, log `results/rerun_dyn/analyze_dynhelper.txt`) |

Environment variables: `JOBS` (default 24), `TAG` (default `dyn` → `results/coop_dyn_sps`),
`BIN` (explicit binary).

One run by hand (equivalent to `mapNack`, periodic1, N 45, seed 1):

```bash
results/rerun_dyn/ns3_dyn --scenario=roundabout --mobility=traces/rbt_ivd30_n45_s1.csv.gz --sumoNet=sumo/roundabout/roundabout.net.xml --traffic=periodic1 --seed=1 --run=1 --helperMode=leaderRes --helper=4 --helperSelect=map --collCond=true --writeAtt=true --ns3::LteRlcUm::ReorderingTimer=0ms --out=results/x/rbt_ivd30_periodic1_n45_s1
```

This is the command line `run_coop.py` builds (`run_coop.py:176`). The IVD and the
background vehicles are in the mobility trace `traces/rbt_ivd30_n<N>_s<seed>.csv.gz`. The
runner generates the trace with SUMO (`run_campaign.py`) if it is missing.

### 13.3 Copy the results to the laptop

The analysis here used a copy of the server's `results/coop_dyn_sps` and `results/rerun_dyn`
in `~/Documents/remote-results/`. Replace `<server>` with your SSH host:

```bash
rsync -av <server>:~/Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/results/{coop_dyn_sps,rerun_dyn} ~/Documents/remote-results/
```

### 13.4 Figures and tables of this document

From `scratch_overlay/platoon_harq_robustness_eval`:

```bash
python3 plot_dynhelper_lines.py ~/Documents/remote-results/coop_dyn_sps results/dyn_sps_plots
```

```bash
python3 plot_dynhelper_choice.py ~/Documents/remote-results/coop_dyn_sps results/dyn_sps_plots
```

```bash
python3 plot_dynhelper_timeline.py ~/Documents/remote-results/coop_dyn_sps results/dyn_sps_plots 1
```

```bash
cp results/dyn_sps_plots/*.png results/dyn_sps_plots/*.csv figures/dynhelper_sps/
```

- `plot_dynhelper_lines.py` → `fig_d1_prr_vs_distance.png`, `fig_d2_gprr_worst_vs_load.png`,
  `gprr_worst_vs_load.csv`. Inputs: `analysis/coop_members.csv`, `analysis_dyn/dyn_runs.csv`.
- `plot_dynhelper_choice.py` → `fig_d3_helper_choice.png`, `fig_d3b_helper_coverage.png`,
  `helper_choice_counts.csv`, `helper_coverage.csv`. Inputs: per-run `_hsel.csv.gz` and
  `_att.csv.gz`.
- `plot_dynhelper_timeline.py` → `fig_d4_periodic1_s<seed>.png`, `fig_d4_periodic2_s<seed>.png`
  (one seed, third argument, default 1) and `fig_d5_helper_share_time.png` (N 0, 10 seeds).
  Inputs: `_hsel.csv.gz` (`t_ndi`, `applied`) and `_att.csv.gz` (`geo_tx`, `status` of member 8).

The switch table of §10 was computed from `_hsel.csv.gz` / `_att.csv.gz` (one-off script, not saved). The paired tables of §7.3 / §7.5 are copied from `results/rerun_dyn/analyze_dynhelper.txt`.

---

## 14. File index

| file | content |
|---|---|
| `helper_select.{h,cc}` | the selector (GPRR score, link models, CAM buffer, `_hsel.csv` rows) |
| `../common/stack/nr_sl_coop_scheduler.{h,cc}` | `SetHelperSelector`, `HelperFor`, the `leaderRes` hand-over (`slotAllocations.size() > 1`) |
| `nack_only_harq.{h,cc}` | Option-1 emulation, helper NACK matching, copies observed from any member |
| `platoon_harq_robustness_eval.cc` | program, flags, KPIs, `_att.csv` / `_hsel.csv` writers |
| `run_coop.py` | arm definitions (`DYN_ARMS`), campaign runner, per-run checks, `--analyze-only` |
| `run_dynhelper.sh` | server driver (build / check / campaign / analyze) |
| `analyze_dynhelper.py` | paired tables, pre-registered verdicts, geometry split |
| `plot_dynhelper_lines.py`, `plot_dynhelper_choice.py`, `plot_dynhelper_timeline.py` | figures and tables of this document |
| `figures/dynhelper_sps/` | the seven figures and three CSVs referenced above |
| `results/coop_dyn_sps/<arm>/<tag>_{run,member}.csv, _att.csv.gz, _hsel.csv.gz` | per-run outputs (server; laptop copy in `~/Documents/remote-results/`) |
| `results/coop_dyn_sps/analysis/` | `coop_runs.csv`, `coop_members.csv`, `coop_summary.csv`, figc1–figc7 |
| `results/coop_dyn_sps/analysis_dyn/` | `dyn_runs.csv`, `dyn_paired.csv`, `dyn_geometry.csv`, `dyn_verdict.txt` |
| `results/rerun_dyn/` | `provenance.txt` (host, commit, binary sha256 `fbd509fc…`, base flags), `progress.log`, `analyze_*.txt` |
| `README.md`, "Per-TB helper selection (Kuo et al. 2021)" | design notes, pilot and checks |
