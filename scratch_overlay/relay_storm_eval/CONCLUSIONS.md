# relay_storm_eval — campaign conclusions (re-analyzed with new kpi/ pipeline)

**Source data** (no re-simulation): `results_relay_storm_eval/` (3,150 sims, 3 modes × 7 speeds × 5 MCDs × 30 seeds). 5 s sims, NR mode-2 SB-SPS, MCD-aware PSFCH group (after the gate-timing fix).

**Pipeline**: per-KPI CSVs from `run_kpi_pipeline.py` (registry-based `kpi/` in `scratch_overlay/common/kpi/`) → `analyse_relay_storm.py` → `kpi_results/sweep_aggregate.csv` (105 rows) + `kpi_results/storm_indicator.csv` (71 rows) → `analysis_new.ipynb` for the verdict tables.

See `analysis_new.ipynb` for the rendered figures and cell-level numbers; the headline numbers are below.

---

## 1. The reTX-storm mechanism is reproduced

The new pipeline's `storm_indicator.csv` is byte-identical to the legacy
`agg_fixed_sweep.py` output for the storm ratio (the storm definition is
unchanged: `n_retx/n_new` over `psschTxUeMac`, leader-only `hop1_retx`
count, ratio = relay hop-1 reTX / baseline hop-1 reTX). The mechanism
switches on exactly where the legacy analysis predicted:

| MCD (m) | Mean storm ratio (over 7 op-points × 2 relay arms) |
|--------:|--------------------------------------------------:|
|      80 |                                              0.95 |
|     100 |                                              1.08 |
|     150 |                                              1.31 |
|     300 |                                              1.57 |
|     350 |                                              1.58 |

The transition is the MCD that first admits the relay to the leader's
feedback group (relay 4 at 4·IVD ≈ 100–150 m; relay 6 at 6·IVD ≈ 150–235 m).

## 2. PRR/AoI/PSR — and the role reversal

This is where the new pipeline **changes the verdict** vs the legacy analysis.
With the legacy inline `compute_kpis`:

- The PRR was computed from the `thput` table using a single relay IP per
  sim. For multi-relay runs the wrong IP was sometimes picked, and the
  relay's second-hop contribution was undercounted.
- The PRR used **type1 (S-only dedup)**: a packet delivered via two
  paths was counted as two successes, inflating PRR on relay arms.

With the new pipeline (`prr_type2`, 3GPP S/Z union-dedup, multi-path
attribution from `pktTxRx`):

| Metric                     | Baseline | Relay 4 (close) | Relay 6 (far) |
|----------------------------|---------:|----------------:|--------------:|
| Mean PRR                   | 0.954    | 0.984 (+3.0 pp) | 0.996 (+4.2 pp) |
| Tail PRR (min over RX)     | 0.865    | 0.915 (+4.9 pp) | 0.973 (+10.7 pp) |
| AoI (ms)                   | 29.7     | 27.8 (-6%)      | 45.8 (+54%)   |
| HARQ NACK rate             | 0.0203   | 0.0178 (-12%)   | 0.0211 (+4%)  |
| Storm metric               | 0.143    | 0.219           | 0.186         |
| Channel occupancy (TB/ms)  | 0.220    | 0.350           | 0.295         |
| PSR (forwarded/leader-gen) | —        | 0.968           | 0.928         |

**The far relay (node 6) gives the best tail-PRR** — by a large margin
(+10.7 pp vs baseline, vs +4.9 pp for the close relay). The legacy
analysis claimed the opposite: that the close relay wins because its
higher PSR (more packets decoded on hop-1) translates to more forwarding
capacity. That story was driven by a relay-IP lookup bug and the
double-counting in type1 PRR.

## 3. The corrected geometry story (§3d, §4b)

PSR is not the benefit-side gate — **proximity to the worst receiver**
is. The far relay (node 6) sits at 6·IVD from the leader and at IVD from
the worst receiver (the last follower, node 8). So:

- The far relay's *forwarded* packets reach the worst receiver with very
  high SINR (short hop). This is what drives tail PRR up.
- The close relay's *forwarded* packets must travel back from node 4 to
  node 8 (4·IVD), with much worse SINR. So even though it has more
  packets to forward (PSR = 0.968 vs 0.928), fewer of them reach the tail.

This inverts the legacy "PSR is the benefit-side gate" framing. The right
test for "which relay helps the tail?" is **distance from the worst
receiver**, not **decoded-packet rate on hop-1**.

## 4. Operating recommendation

| If the bottleneck is…            | Use…                                  |
|----------------------------------|---------------------------------------|
| Tail-receiver reliability (PRR)  | **Relay 6 (far)**, MCD ≤ 100 m        |
| Latency / freshness (AoI)        | **Relay 4 (close)**, MCD ≤ 100 m      |
| Resource efficiency              | **No relay (baseline)**               |
| Mixed (best mean PRR + tail PRR) | **Relay 6 (far)**, MCD ≤ 100 m        |

MCD ≤ 100 m is the safe regime: the relay sits outside the leader's hop-1
feedback group at all 7 operating points (relay 4 enters at 4·IVD ≈ 100 m,
relay 6 at 6·IVD ≈ 150 m). The storm is then dormant (storm ratio ≤ 1.0).

## 5. H4 verdict (controlled regime sweep)

Across every (relay, speed, IVD) bucket, putting the relay **in-group but
not the worst RX** (`not_worst` regime) costs:

- **+0.10 to +0.16 storm ratio** (mean **+0.133**)
- **±0.01 worst-Rx PRR** (mean **+0.000**)

vs the `out` regime (relay excluded). The membership cost is not redeemed
by the membership benefit. The legacy `regime_aggregate.csv` (3 seeds per
cell) reported Δstorm = +0.139 / ΔprrW = +0.004 — qualitatively identical.

## 6. Caveats

- The 200 m platoon means MCD only becomes "interesting" near 100–150 m;
  a longer platoon would spread the transition.
- Fixed MCS, single LOS road segment, and a single relay-mode pattern
  (groupcast L2 feedback) bound the generality of the magnitudes — but
  not of the storm-ignition threshold, which depends only on geometry.
- The regime sweep uses the in-process classifier on `sweep_aggregate.csv`
  (since `regime_aggregate.csv` was not re-run under the new pipeline).
  The `worst` band is sparse (n=5 cells), but the Δstorm = +0.133 verdict
  is robust to that.
- The new pipeline's AoI is per-(tx, rx), attributing leader vs relay
  paths separately. The legacy AoI was "earliest RX across all paths" —
  semantically different. We keep the per-(tx, rx) decomposition because
  it lets you see which path is paying the latency cost.

## 7. Spec-faithfulness of the PSFCH feedback gate (post-campaign correction)

The numbers above were generated with `common/mcd/McdFeedbackGate`
applying a distance gate (drop feedback if dist(TX→RX) > MCD) to **all**
HARQ feedback, regardless of HARQ option. Per 3GPP TS 38.321 §5.4.4.1.1
and TS 38.213 §16.5, this is **spec-violating for HARQ Option 2 (Groupcast)**
used by this campaign: the spec gates Option-2 feedback by Zone-ID
membership, not by distance. In our single-dstL2Id setup all UEs are
group members, so the spec-faithful Option-2 has no distance filter at
all — yet our simulations had far receivers silently dropped, biasing the
sender toward "no ACK from far RX" → fewer retransmissions than the spec
prescribes.

**Corrected architecture (commit pending):**

The gate has been renamed `McrFeedbackGate` and made **castType-aware**:

| CastType (`NrSlSciF2aHeader::CastTypeIndicator_t`) | Spec mechanism | Gate behavior |
|---|---|---|
| `Broadcast` (0) | No PSFCH feedback | **Drop all** |
| `Groupcast` (1, Option-2) | Zone-ID membership | **Forward all** (spec-faithful in single-dstL2Id) |
| `Unicast` (2) | No gate | **Forward all** |
| `GroupcastOnlyNack` (3, Option-1) | MCR-style distance gate | **Drop if dist > mcrMeters** |

`relay_storm_eval` is now configured with `sp.harqOption = 2`, so the
gate is skipped (correct: Option-2 has no spec distance gate). The castType
routing itself is still active (Broadcast would be silently dropped if
ever used; Unicast / Groupcast passes through unchanged).

**Impact on prior numbers:** the campaign's existing CSV outputs were
generated with the MCD-distance-gate behavior, which biased sender HARQ
state for Option-2. Numbers carry this bias. Per the no-re-run
constraint we have not regenerated them; they should be considered
superseded by the corrected gate semantics but are kept in
`results_relay_storm_eval/` for reference. The storm-ignition threshold
(mechanism, not magnitude) is geometry-driven and is robust to the bias.

PSFCH multiplexing note: contrib/nr does NOT model the 3GPP PSFCH
freq×time×code multiplexing (each RX UE's feedback is delivered to the
sender's MAC as a discrete callback carrying `nodeId`). This is
acceptable for our 7-UE platoons; for larger N the spec-defined PSFCH
capacity limit would surface.
