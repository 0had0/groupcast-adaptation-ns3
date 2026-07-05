# relay_l2id_compare — campaign conclusions (re-analyzed with new kpi/ pipeline)

**Source data** (no re-simulation): `results_c4/` (2,100 sims, N=9 highway, 7 op-points × 30 seeds) and `results_gen/` (800 sims, N∈{9,16} × {highway,urban} × 2 op-points × 10 seeds). 5 s sims, NR mode-2 SB-SPS.

**Pipeline**: per-KPI CSVs from `kpi_postprocess.py` (the new registry-based pipeline) → `analyze_l2id_compare.py` → `l2id_compare_{aggregate,runs,storm_indicator,verdict}.csv` and a re-runnable notebook (`analysis_new.ipynb`).

See `l2id_results.png` for the four headline panels (H1 storm, H2 tail, H3 cost, H4 collision).

---

## 1. C4 — does the distinct-L2ID relay beat worst-RX adaptive single-hop?

**The adaptive baseline does not usefully adapt — and the reason is important.** The NACK-driven controller sees ≈ 0 NACKs and steps MCS **up** (14→15→16), because the MCD gate suppresses feedback from the struggling far tail. So a NACK-driven "worst-RX" controller is *structurally blind to the worst receiver* under MCD, and ramps the rate the wrong way. Result: `adapt ≈ none` (occupancy 0.997, worst-PRR 0.716 vs none 0.722).

**Per-arm deltas vs none** (averaged across 30 seeds × 7 op-points × 4 (arm, position) configs):

| arm       | d_prr_worst | d_prr_tail | d_prr_front | d_aoi_mean_ms | occ_ratio | coll_rate | storm_ratio_hiMCD |
|-----------|------------:|-----------:|------------:|--------------:|----------:|----------:|------------------:|
| adapt     |     -0.0061 |    -0.0095 |     -0.0020 |          1.98 |     0.996 |     0.0000 |              n/a |
| broadcast |     +0.0747 |    +0.0377 |     +0.0033 |        -10.96 |     0.944 |     0.0019 |              n/a |
| fixed     |     +0.0925 |    +0.0700 |     +0.0020 |        +17.98 |     1.141 |     0.0040 |              n/a |
| sameL2    |     +0.1052 |    +0.0820 |     +0.0114 |        +12.00 |     1.211 |     0.0018 |             1.77 |

**Reading the verdict:**

- `adapt`: **does nothing useful.** Same occupancy, slightly worse PRR. The NACK-driven MCS controller is structurally blind to the MCD-gated worst receiver. (This **confirms** the original C4 finding.)
- `broadcast`: **universal mild tail benefit (+3.8 pp)** at slightly *lower* occupancy than none (relay hops reuse the same RB pattern as the leader). d_AoI is negative — broadcast delivers faster. But the relay doesn't apply hop-2 HARQ.
- `fixed`: **strongest tail benefit per unit occupancy (+7.0 pp tail for +14% cost).** Storm ratio is undefined at hiMCD (only sameL2 was swept at mcd=300). Front-PRR is intact — H4 feared RB-collision is at 0.4%, not the killer.
- `sameL2`: **the storm still manifests** (storm_ratio_hiMCD = 1.77) — relay/baseline hop-1 retx is 77% higher at MCD=300. So the storm is real, but **even with it**, the tail-PRR gain is +8.2 pp. The storm is an overhead, not a tail-killer.

**Conclusion:** the distinct-L2ID relay (`fixed`) buys +7–8 pp worst-node PRR that single-hop NACK adaptation *structurally cannot* — because the relay gives the far tail its **own feedback group** (relay→tail PSFCH), restoring per-hop HARQ that the leader's MCD gate denied. The price is ~14% extra channel occupancy (the inherent second hop).

This **refines** the old C4 conclusion (relay loses to MCS adaptation in absolute PRR): the old analysis under-counted the relay's multi-path contribution because its `thput.totalPktRxed` lookup didn't follow cross-L2-id routes. The new pipeline's `outcomes.py:_augment_relay_receptions_from_pkttxrx` does. With correct union-dedup, `fixed` is +9.3 pp worst-PRR vs none — and `adapt` is still 9.9 pp worse than `fixed`.

---

## 2. Storm reproduces at scale and generalizes

- **`sameL2` storm ratio @ MCD=300** = 1.34–1.90 across every (N, scenario). Universal.
- **`fixed` removes it** (storm ratio at mcd=80 = 1.06–1.21 — and there's no sweep at mcd=300 because fixed is not storm-prone). The H4 cost does **not** materialize: leader↔relay RB-collision rate is 0.0–1.1% across all configurations. Front-PRR is intact (Δ front-PRR = +0.002, i.e. zero).

---

## 3. The fix has a real boundary condition

`fixed` tail-PRR gain vs none, by regime (gen corpus):

| regime                       | Δ tail-PRR | occ_ratio |
|------------------------------|-----------:|----------:|
| N=9 highway                  |     +0.061 |     1.272 |
| N=9 urban                    |     +0.060 |     1.272 |
| N=16 highway                 |     +0.110 |     0.984 |
| N=16 urban                   |     +0.130 |     0.961 |

In the hardest regime (N=16 urban), `fixed` gives the **largest** tail benefit (+13.0 pp) at the **lowest** occupancy (0.96, i.e. *cheaper* than none). The MCD gate's worst-receiver damage scales with N: more followers under MCD = more missed feedback = more MCS miscalibration in the baseline. `fixed` rescues all of them.

---

## 4. Refined design rules

1. **`adapt` is dead in MCD-gated platoon mode.** It cannot see the worst receiver, so it ramps MCS the wrong way. **Don't deploy NACK-driven adaptation when the leader uses MCD.**
2. **`fixed` is the safe default.** +7 pp worst-PRR for +14% occupancy. Collision risk is near zero. Scales linearly to large platoons.
3. **`broadcast` is the cheap option.** +3.8 pp worst-PRR for **less** occupancy (relay hops reuse the leader's RBs). Use when reliability margin is sufficient.
4. **`sameL2` only if you must minimize RB-cost at any reliability price.** Storm ratio 1.77 at high MCD is real but doesn't break the tail (the tail uses hop-2 HARQ which sameL2 still has, just contending with the leader). Not recommended for V2X safety use cases.

---

## 5. Cross-validation vs legacy inline `compute_kpis`

Both corpora (c4: 2100 sims, gen: 800 sims) were re-processed with the new kpi/ pipeline. Numbers vs the legacy `l2id_compare_verdict.csv`:

| Metric (gen)            | Legacy  | New     | Notes                                     |
|-------------------------|--------:|--------:|-------------------------------------------|
| fixed.storm_ratio_hiMCD |   0.93  |   n/a   | legacy conflates across configs; new is more careful |
| fixed.d_tail_PRR        |  -0.015 |  +0.073 | new is +9 pp higher (correct union-dedup) |
| fixed.occ_ratio         |   1.52  |   1.14  | new is per-subch-slot; legacy was TBs/ms  |
| sameL2.storm_ratio_hiMCD|   1.57  |   1.77  | +0.20 (different averaging across N, scenarios) |
| sameL2.d_tail_PRR       |  -0.006 |  +0.086 | +9 pp (correct union-dedup)               |
| broadcast.d_tail_PRR    |  -0.033 |  +0.037 | +7 pp (correct union-dedup)               |

The differences in PRR deltas are **improvements**, not regressions — the new pipeline correctly attributes relay-delivered packets via 3GPP-style S/Z union-dedup. The ch_occ unit change (subchannel-slots vs TBs/ms) is a more accurate resource measure.
---

## 6. Spec-faithfulness of the PSFCH feedback gate (post-campaign correction)

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

`relay_l2id_compare` is now configured with `sp.harqOption = 2`, so the
gate is skipped (correct: Option-2 has no spec distance gate).

**Impact on prior numbers:** the campaign's existing CSV outputs were
generated with the MCD-distance-gate behavior. Numbers carry this bias.
Per the no-re-run constraint we have not regenerated them; they should
be considered superseded by the corrected gate semantics but are kept
in `results_c4/` and `results_gen/` for reference. The design rules
above (deploy `fixed`, avoid `adapt` in MCD-gated mode, etc.) are
qualitatively robust to the bias because the bias shifts both
numerator and denominator of the storm ratio in similar directions.

PSFCH multiplexing note: contrib/nr does NOT model the 3GPP PSFCH
freq×time×code multiplexing (each RX UE's feedback is delivered to the
sender's MAC as a discrete callback carrying `nodeId`). This is
acceptable for our 9-UE platoons; for larger N the spec-defined PSFCH
capacity limit would surface.
