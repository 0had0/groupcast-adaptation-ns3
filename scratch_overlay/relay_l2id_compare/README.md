# relay_l2id_compare

Unified head-to-head of **four relay designs** for NR-V2X mode-2 sidelink groupcast
on a **moving** 9-vehicle platoon with the MCD feedback gate. One binary, selected
by `--relayMode`, so all arms share identical mobility / channel / radio config and
are directly comparable to `results_relay_storm_eval`.

This target **reuses the existing `common/` modules unchanged** (it edits no existing
source): `SameL2IdRelayApp`, `FixedRelayApp`, `BoundaryRelayApp`, `McdFeedbackGate`,
the NR-SL stack, flows, and KPI hooks. Scheduler is **NR mode-2 SB-SPS only**.

It implements the experiment scoped in `../../DISTINCT_L2ID_EXPERIMENT_DESIGN.md`.

## The four arms

| `--relayMode` | Leader group (L2ID 254) | Hop-2 | Feedback coupling | Tests |
|---|---|---|---|---|
| `none` | all followers, HARQ | — | — | 1-hop baseline |
| `sameL2` | all followers, HARQ | re-broadcast on **L2ID 254**, HARQ | **shared** → reTX storm | the storm design |
| `fixed` | **front** 1..r, HARQ | tail on **L2ID 255**, HARQ | **separate** → storm→collision | H1–H4 |
| `broadcast` | all followers, HARQ | tail on **L2ID 255**, **broadcast** (no PSFCH) | none on hop-2 | H5 (low-cost corner) |

Add `--adaptiveMcs=true` to the `none` arm for the **worst-RX NACK-driven** strong
baseline (the C4 competitor relaying must beat).

## Build

The `scratch_overlay/` dirs are symlinked into `ns-3-dev/scratch/` by `setup.sh`,
which ns-3 auto-discovers. After adding this dir:

```bash
# 1. register the new target (creates ns-3-dev/scratch/relay_l2id_compare symlink)
bash setup.sh            # re-run; or manually:
# ln -s ../../scratch_overlay/relay_l2id_compare ns-3-dev/scratch/relay_l2id_compare

# 2. build
cd ns-3-dev && ./ns3 build relay_l2id_compare
```

## Run

```bash
# reduced first pass — tests H1–H4 cheaply (MCD {80,300}, 2 op-points, modes x {4,6})
QUICK=1 ./run_relay_l2id_compare.sh ./results_quick 5.0 "$(seq -s' ' 1 10)" 10

# full matched campaign + adaptive strong baseline
ADAPTIVE=1 ./run_relay_l2id_compare.sh ./results 5.0 "$(seq -s' ' 1 30)" 10
```

Each run writes `<tag>.db` (KPI SQLite), `<tag>_summary.csv` (params incl. `relayMode`),
and `<tag>_kpi.csv` (post-processed). Aggregate by `relayMode × relayNode × mcd × (speed,ivd)`.

## What to read out (decision criteria)

- **H1 decouple** — `fixed`/`broadcast` hop-1 retx ratio ≈ 1.0 and **flat in MCD**, vs `sameL2` rising to ~1.65×.
- **H2 benefit kept** — tail-PRR of `fixed` ≥ `sameL2`.
- **H3 leader relaxation** — front-segment resource of `fixed` drops (leader serves only the front); total occupancy `fixed` < `sameL2`.
- **H4 collision cost** — leader↔relay RB-collision rate / front-PRR degradation under `fixed` (the cost that replaces the storm).
- **H5** — `broadcast` lowest hop-2 load but tail-PRR rests on one un-ACKed copy.

A `fixed` arm that satisfies H1–H3 with mild H4 — and whose total resource ≤ the
`none --adaptiveMcs` baseline at equal tail-PRR — flips the C4 negative result into a
**positive design recommendation** (use distinct-L2ID strict-partition, min-MCD).
