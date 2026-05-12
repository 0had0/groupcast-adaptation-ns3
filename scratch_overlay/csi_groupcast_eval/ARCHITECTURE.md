# csi_groupcast_eval — Architecture

SUMO-driven NR-V2X groupcast simulator with a geometry-aware channel model.
Mirrors `scratch_overlay/groupcast_eval` structurally but replaces the
probabilistic V2V_Highway channel condition model with
`ThreeGppGeometricNlosvModel`, which deterministically forces NLOSv whenever
a designated SUMO blocker vehicle's footprint intersects the TX→RX line.

## Module dependency graph

```
csi_groupcast_eval.cc          ← main: CLI + 7-step orchestration
  ├─ mobility.{h,cc}           ← SUMO/TraCI node creation → {ueNodes, traci}
  ├─ nr_sl_stack.{h,cc}        ← NrHelper + NrSlHelper + pool + IP
  │   └─ geometric_channel.{h,cc}  ← ApplyGeometricNlosv (post-InitializeBand)
  │       └─ three-gpp-geometric-nlosv.{h,cc}  ← CCM wrapper + Liang–Barsky test
  ├─ groupcast_flows.{h,cc}    ← bearer activation + OnOff/PacketSink (3 flows)
  ├─ kpi_hooks.{h,cc}          ← SQLiteOutput + trace callbacks + V2xKpi wiring
  └─ v2x-kpi.{h,cc}            ← KPI engine (PIR/PRR/throughput/BLER tables)
```

## Channel model pipeline

```
BandwidthPartInfo::V2V_Urban (default)
    ↓ NrHelper::InitializeOperationBand()
ThreeGppV2vUrbanPropagationLossModel
    + inner ThreeGppV2vUrbanChannelConditionModel (CCM)
    ↓ geometric_channel::ApplyGeometricNlosv()
ThreeGppGeometricNlosvModel            ← wraps the inner CCM
    • if traciClient && blockerIds not empty:
        - on each GetChannelCondition(a, b) call:
          1. ask inner CCM for probabilistic condition
          2. if not NLOS: run Liang–Barsky segment-vs-AABB test against every
             blocker vehicle's current SUMO position
          3. if any blocker intersects the 2-D LOS → override to NLOSv
    • otherwise: transparent (returns inner CCM result unchanged)
```

The wrapper is pushed onto both:
- `ThreeGppPropagationLossModel::SetChannelConditionModel()`
- `ThreeGppChannelModel::SetChannelConditionModel()` (inner model of the
  `ThreeGppSpectrumPropagationLossModel`)

so path loss and fading both see a consistent channel condition.

## Geometry test (Liang–Barsky)

The blocker footprint is an **axis-aligned bounding box** (AABB) centred on
the blocker's 2-D position with half-extents `(length/2, width/2)`.  The
Liang–Barsky algorithm clips the parametric TX→RX segment against the four
AABB half-planes; if the clipped `[tEnter, tExit]` interval is non-empty
(both in `[0,1]`) the blocker lies on the line of sight and NLOSv is forced.

This replaces the previous AABB bounding-box overlap test which falsely
tagged blockers that were merely nearby without actually intersecting the
LOS segment.

## Output

Identical to `groupcast_eval`:
- `<outputDir><simTag>.db` — SQLite with V2xKpi tables (PRR, PIR, BLER, ...)
- `--outRunCsv` — one-line per-run summary CSV
- `--outPacketsCsv` — optional per-packet CSV
