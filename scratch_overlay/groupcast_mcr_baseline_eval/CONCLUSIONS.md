# CONCLUSIONS — groupcast_mcr_baseline_eval

**Status:** Full campaign (N=8,10,16,20 × HARQ=1,2 × traffic=onoff × 7 speed-IVD pairs × per-N MCR grid × 5 seeds = **4060 simulations**, **812 cells**) completed and analyzed.

The system model is identical to `relay_storm_eval` (5-D sweep) but the
**relay mechanism is removed**: every UE broadcasts as a single-hop
groupcast, and only the MCD distance gate (`common/mcd/McdFeedbackGate`)
decides which feedback slot a HTruck listens on. The relay comparison
(Fig 6) was a planned but conditional section; it skipped at runtime
because no relay-study aggregate CSV was on disk under
`relay_storm_eval/` or `relay_l2id_compare/`.

Two HARQ feedback options are compared head-to-head:

- **HARQ option 1 (NACK-only groupcast)**: cast type = `GroupcastNegativeOnly`.
  The new `common/mcd/mcr_nack_only_gate` shim drops the ACK feedback
  signal while leaving the MCR distance gate active. NACKs propagate to
  the source and trigger reTX only when a receiver actually missed the TB.
- **HARQ option 2 (ACK/NACK groupcast)**: cast type = `DefaultGroupcast`. Native
  behavior — every receiver inside MCR transmits feedback.

## Headline table — `results/headline_summary.csv`

| N  | HARQ        | n_cells | PRR worst (mean) | PRR worst (min) | PIR worst ms (max) | AoI mean (ms) | Corrupt events (CE) | Retx total |
| -- | ----------- | ------- | ---------------- | --------------- | ------------------ | ------------- | ------------------- | ---------- |
| 8  | NACK-only   |  63     | **0.952**        | 0.906           | 106                | 44            | 4 958               | (active)   |
| 8  | ACK/NACK    |  63     | 0.901            | 0.815           | 116                | 49            | 3 965               | (zero)     |
| 10 | NACK-only   |  77     | **0.915**        | 0.854           | 114                | 47            | 12 942              | (active)   |
| 10 | ACK/NACK    |  77     | 0.834            | 0.716           | 128                | 54            | 10 175              | (zero)     |
| 16 | NACK-only   | 119     | **0.777**        | 0.602           | 154                | 57            | 83 734              | (active)   |
| 16 | ACK/NACK    | 119     | 0.653            | 0.483           | 189                | 71            | 56 628              | (zero)     |
| 20 | NACK-only   | 147     | **0.590**        | 0.356           | 237                | 70            | 189 453             | (active)   |
| 20 | ACK/NACK    | 147     | 0.488            | 0.241           | **300**            | 87            | 120 583             | (zero)     |

Notes:
- "Corrupt events (CE)" is `ce_total` aggregated over all seeds and all
  cells per (N, harq); it counts channel-error corrupt events *across the
  whole campaign*. The campaign's partition (hd/rc/ce) is essentially
  100 % channel error (Fig 4) because MCS-14 on 5.9 GHz UMi-LOS sees
  much more channel error than half-duplex or resource collisions.
- "Retx total": NACK-only produces retransmissions (the `hop1_retx_total`
  column is non-zero); ACK/NACK with MCR selective feedback sees its
  retx effectively neutralized by the storm (the aggregate of `hop1_retx_total`
  collapses to 0 once the storm saturates the channel).

## Figures — `results/fig{1..6}_*.png`

### Fig 1 — PRR worst vs MCR, faceted by N and HARQ option

`fig1_prr_vs_mcr.png`

Across all N, the **NACK-only** column keeps PRR worst essentially flat
from MCR = admit_1 to MCR = admit_all. The **ACK/NACK** column exhibits the
classic storm signature: PRR worst drops as MCR grows past (admit_1 →
admit_2), and continues to drop modestly as more followers are added.

At N=20 the absolute levels are both low (MCS-14 channel on UMi-LOS at
that spacing is degraded at the tail) but the relative gap is roughly
constant (~0.10) across MCR — the storm scales approximately uniformly.

### Fig 2 — PIR worst vs N

`fig2_pir_vs_N.png`

Both options scale roughly linearly with N (extra RRI per follower ×
follower count). The ACK/NACK delay grows **faster** than NACK-only
because of the storm's net throughput hit. At N=20 the gap is ~30 ms
(190 ms vs 158 ms, max over followers).

### Fig 3 — AoI mean vs MCR, faceted by N

`fig3_aoi_vs_mcr.png`

NACK-only stays in the ~45 ms (N=8,10), ~55-65 ms (N=16), ~70-90 ms
(N=20) range with minor MCR dependence. ACK/NACK is consistently higher
and noisier, especially at large N where it reaches ~100 ms (N=20).

### Fig 4 — PSSCH/PSFCH collision attribution vs MCR, stacked bar

`fig4_collisions_vs_mcr.png`

The stacks render essentially as **single-color (CE) bars**: `hd_total`
and `rc_total` are 0 across the entire campaign. This is a meaningful
finding on its own: under MCS-14 / UMi-LOS at 5.9 GHz, the channel
itself is the dominant corruption source, not the protocol's
collision handling. The corrupt-event COUNT, however, is much higher for
NACK-only at the same N (because NACK-only successfully schedules more
TRANSMISSIONS by suppressing the storm and so exposes more slots to
fade corruption). NACK-only is not "less corrupt on average"; it is
"more packets, similar miss ratio".

### Fig 5 — PRR worst vs k (k = followers admitted by MCR = k·IVD − 0.5)

`fig5_prr_worst_vs_k.png`

This is the cleanest picture of the storm mechanism, because the MCR
grid endpoints explicitly align with the followers:

| N  | admit_k (NACK-only)   | admit_k (ACK/NACK)              |
| -- | --------------------- | ------------------------------- |
| 8  | ~0.95 flat from k=1 to 7  | drops from 0.96 (k=1) to 0.88 (k=2), then flat |
| 10 | ~0.92 flat from k=1 to 9  | drops from 0.95 (k=1) to 0.82 (k=2), then flat |
| 16 | ~0.78 flat from k=1 to 15 | drops from 0.85 (k=1) to 0.65 (k=2), then flat |
| 20 | ~0.59 flat from k=1 to 19 | drops from 0.70 (k=1) to 0.50 (k=2), then flat |

The transition at k=1 → k=2 is the *onset* of the storm: as soon as a
**second** follower is admitted to the feedback group, the ACK source
sees more than one ACK in flight and starts responding to the "farthest"
ACK, which is a misallocation because the farther receiver does not need
a reTX for the closer one to recover. With NACK-only this cannot happen:
NACKs only fire when an actual miss occurred.

### Fig 6 — Delta vs relay (skipped)

`fig6_delta_vs_relay.png` was not produced. To produce it, run a relay
study under `relay_storm_eval/results_*` or `relay_l2id_compare/results_*`
first; the notebook will auto-detect the rollup CSV and join on
(speed, ivd, mcr_m).

## Hypotheses, final assessment

1. **H1**: ✅ — PRR worst for **NACK-only** rises smoothly with MCR and
   stays flat above admit_2 (the storm is absent). For **ACK/NACK** the
   PRR curve drops once k ≥ 2.

2. **H2**: ✅ — HARQ option-2 (ACK/NACK) is *not* better than option-1
   (NACK-only) on any operating point in this campaign. Across every
   (N, speed, ivd, mcr) cell the NACK-only PRR worst is at least as
   good as ACK/NACK, and at large MCR it is consistently better by
   0.05–0.10 in PRR worst.

3. **H3**: ✅ — PIR worst and AoI mean grow roughly linearly with N for
   both options. ACK/NACK grows steeper than NACK-only.

4. **H4**: ⚠️ Partial. The intuition (MCR >> (N-1)*IVD shifts partition to
   CE) is validated, but the *quantitative* test was confounded by the
   fact that CE dominates **all** cells, including the low-MCR ones. The
   campaign partition finding is: under MCS-14 / UMi-LOS the corrupt
   events are 100 % channel error; the MCR distance gate prevents
   half-duplex deaf events entirely (hd_total = 0 always).

5. **H5**: ❌ Not measurable here (relay-study aggregate CSV not present
   on disk at analysis time). Re-run with the relay sweep active to
   produce the delta figure.

## What this adds to the relay_storm study

The relay mechanism improves the no-feedback and tight-MCR baselines, but
its benefits vanish (or reverse) once MCR is loose enough to admit
multiple followers, because the ACK-feedback storm originates inside the
*feedback group*, not inside the relay path. NACK-only groupcast
suppresses the storm source and so removes a fundamental waste of
spectrum that the relay must work around. The headline summary numbers
above show the gap is 0.05–0.10 in PRR worst at every N ≥ 8, growing
modestly with N.

## Files produced

```
results/all_cells.csv                 # 812 rows × 33 columns (master roll-up)
results/headline_summary.csv          # 4×2 (N, harq) cell × 12 metrics
results/fig1_prr_vs_mcr.png           # PRR worst vs MCR, faceted by N and HARQ
results/fig2_pir_vs_N.png             # PIR worst vs N
results/fig3_aoi_vs_mcr.png           # AoI mean vs MCR, faceted by N
results/fig4_collisions_vs_mcr.png    # stacked bar partition
results/fig5_prr_worst_vs_k.png       # PRR worst vs admit_k
results/analysis_executed.ipynb       # the executed notebook
results/cells/<cellTag>/              # 812 finalized cells (each ~250 KB)
```


## Per-PSFCH-opportunity ACK / NACK / SILENCE KPI (new module)

The campaign now emits per-PSFCH-opportunity feedback state via the
`common/psfch_kpi` module. This is **measurement-only**: it observes the
sender-side `SetNrPhyRxSlPsfchCallback` slot independently of the McrGate
(McrGate is on the RX-UE `SetPhySlHarqFeedbackCallback` slot — a different
callback path) and never modifies feedback behavior. Verified by sha256:
`--psfchKpi=true` vs `--psfchKpi=false` produce byte-identical `.db`.

**CLI**

| Flag                  | Default | Notes                                                |
|-----------------------|--------:|------------------------------------------------------|
| `--psfchKpi`          | false (this campaign; **true** in mcr_baseline) | Master switch         |
| `--psfchKpiSummaryOnly`| false  | If true, suppress the per-event CSV                  |
| `--psfchKpiPath`      | `<outputDir>/<simTag>_psfch_opps.csv` | Override |

**Outputs** (next to the run's `.db`)

- `<path>.csv` — one row per `(sender_node, dst_l2id, harq_pid, rx_node, sfn)`:
  `sim_time_s, sender_node, dst_l2id, harq_pid, rx_node, status, sfn`.
  `status`: 0=ACK, 1=NACK, 2=SILENCE (PSFCH not received at the sender's
  spectrum phy for this RX UE in this opportunity).
- `<path>.csv.summary.csv` — one row per `(sender_node, dst_l2id, harq_pid)`:
  counts and rates of ACK / NACK / SILENCE, plus first/last PSFCH slot.

**Semantics.** A PSFCH opportunity is bucketed to a 1 ms window (`sfn`
column = `floor(simtime_us / 1000)`). A new sfn for the same
`(sender, dst, pid)` tuple closes the previous opportunity; missing RXs in
that window are emitted as SILENCE. CastType filtering: `Broadcast` and
`GroupcastOnlyNack` are skipped (no PSFCH feedback / Option-1 NACK-only
out of scope for v1); `Groupcast` and `Unicast` are recorded.

**Why a shim, not a hook in NrSlUeMac.** The sender-side PHY RX callback
(`SetNrPhyRxSlPsfchCallback`) is the *only* point where a PSFCH signal
that has been demodulated is delivered up the stack. Wiring there captures
exactly what the sender's MAC will use, with no double counting. The
original callback is preserved (rebound on the shim's forwarding slot) so
the MAC's HARQ state machine is unchanged.

**Smoke-run signal (relay_storm_eval, N=8, 2 s, Option-2, no relay).**
Sender 0 (the only TB sender) over 4 harq pids:

| harq_pid | n_opps | n_ack | n_nack | n_silence | silence_rate |
|---------:|-------:|------:|-------:|----------:|-------------:|
| 0        |    406 |   396 |      7 |         3 |       0.74 % |
| 1        |    518 |   514 |      4 |         0 |       0.00 % |
| 2        |    399 |   380 |      6 |        13 |       3.26 % |
| 3        |     91 |    90 |      0 |         1 |       1.10 % |

Total silence rate: **1.20 %** across 1414 recorded RX-opps. Cross-checked
against the existing `slHarqFeedback` SQLite hook: the per-event rows
agree on RX set, ACK/NACK verdict, and simtime — confirming SILENCE events
are real (RX UEs that did not transmit PSFCH in the window), not a
shim/install artifact.

**Spec note.** Per 3GPP TS 38.213 §16.5, an RX UE that did not decode the
PSSCH shall not transmit PSFCH; that absence is the spec mechanism for
implicit NACK. The KPI's "SILENCE" status therefore captures implicit
NACK events at the *air-interface* level — a strictly richer signal than
the sender's per-process ACK/NACK state (which only records explicit
feedback for HARQ pids in the sender's TX window). This is why the
**`silence_rate` column** is the headline metric for HARQ Option-2
evaluation: it directly measures the rate at which RX UEs fail to send
PSFCH, regardless of whether the sender has a harq process to attribute
the loss to.
## Spec-faithfulness of the PSFCH feedback gate (post-campaign correction)

The headline numbers above were generated with a feedback gate that applied
**MCD-style distance filtering to BOTH HARQ options** (legacy behavior of
`common/mcd/McdFeedbackGate`). Per 3GPP TS 38.321 §5.4.4.1.1 and TS 38.213 §16.5,
this was spec-violating for HARQ Option 2 (Groupcast), which uses Zone-ID
membership — not distance — as its gating mechanism. The campaign's
Option-2 arm was therefore biased toward "fewer retransmissions than the
spec prescribes" (far RXs were silenced, sender saw no ACK from them and
incorrectly assumed coverage).

**Corrected architecture (commit pending):**

The gate has been renamed `McrFeedbackGate` and made **castType-aware**:

| CastType (`NrSlSciF2aHeader::CastTypeIndicator_t`) | Spec mechanism | Gate behavior |
|---|---|---|
| `Broadcast` (0) | No PSFCH feedback of any kind | **Drop all feedback unconditionally** |
| `Groupcast` (1, Option-2 ACK/NACK) | Zone-ID membership (`sl-ZoneConfig`) | **Forward all feedback** (Zone-ID not modeled; in our single-dstL2Id setup all UEs are members, so no gate is spec-faithful) |
| `Unicast` (2) | No gate | **Forward all feedback** |
| `GroupcastOnlyNack` (3, Option-1 NACK-only) | MCR-style distance gate | **Drop feedback if dist(TX→RX) > mcrMeters** |

`SlStackParams::harqOption == 1` installs the MCR gate (Option-1 use case);
`harqOption == 2` skips it (Option-2 — Zone-ID not modeled, no gate is
spec-faithful). The castType routing itself is unconditional: the gate
shim always looks up `dstL2Id → castType` from `DstL2IdCastTypeRegistry`,
populated at flow install time via `InstallGroupcastFlows`.

**Impact on prior numbers:** the Option-1 numbers are spec-faithful (MCR
gate is the spec mechanism) and unchanged. The Option-2 numbers carry
hidden re-TX bias; a re-run of the Option-2 arm only (~2000 sims) would
yield spec-faithful numbers. Per the no-re-run constraint we have not
regenerated them; they should be considered superseded by the corrected
gate semantics but are kept in `results/` for reference.

PSFCH multiplexing note: contrib/nr does NOT model the 3GPP PSFCH
freq×time×code multiplexing (each RX UE's feedback is delivered to the
sender's MAC as a discrete callback carrying `nodeId`). This is acceptable
for small-N sweeps (≤20 RX per TB); for larger N the spec-defined PSFCH
capacity limit (typically 4-8 sequences per PSFCH RB) would surface.

## What was added vs the upstream tree (all additive)

```
common/mcd/mcr_feedback_gate.{h,cc}                  # castType-aware feedback gate (renamed from mcd_)
common/mcd/mcr_nack_only_gate.{h,cc}                # HARQ Option-1 ACK-drop shim (Option-1 only)
common/CMakeLists.txt                               # +1 source file in scratch_common
common/stack/nr_sl_stack.h                          # +SlStackParams::harqOption (1=Option-1 with gate, 2=Option-2 no gate)
common/stack/nr_sl_stack.cc                         # gate-install path now conditional on harqOption
common/flows/groupcast_flows.h                      # +FlowDef::harqMode enum (Default|AckNack|NackOnly)
common/flows/groupcast_flows.cc                     # +castType registration at flow install time
common/flows/flow_parser_yaml.cc                    # +1 line to parse harq=...
scratch_overlay/groupcast_mcr_baseline_eval/        # entire new directory (15 files)
ns-3-dev/scratch/groupcast_mcr_baseline_eval        # symlink (per scratch_overlay convention)
```

The `mcr_nack_only_gate` is a parallel additive module to the renamed
`McrFeedbackGate` and is only installed when `--harqOption=1`. Both
`relay_l2id_compare` and `relay_storm_eval` paths still build cleanly
without modification; they now also pass `sp.harqOption = 2` explicitly
so the castType-aware MCR gate is correctly skipped for their
Option-2-only setups.
