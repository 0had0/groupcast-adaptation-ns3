# groupcast_mcr_baseline_eval

**Single-hop (no-relay) baseline harness for the NR-V2X mode-2 groupcast platoon study.**

The system model is shared 1:1 with `relay_l2id_compare` / `relay_storm_eval`:

| Knob                      | Value (as in relay_storm_eval)                      |
| ------------------------- | --------------------------------------------------- |
| Carrier                   | 5.9 GHz, 10 MHz BWP, 1 TTI per slot                 |
| Subchannels               | 10 RBs per slot (per pool)                          |
| MCS                       | 14 (per pool default)                               |
| SST                        | Subchannel-Sidelink Resource Selection               |
| Mobility                  | Urban LOS (3GPP TR 37.885), moving platoon with IVD |
| HARQ feedback             | see `--harqOption`                                  |
| Traffic                   | OnOffConstant 200 B @ 10 Hz (default)               |
| Sensible traffic (smoke)  | see `traffic=`                                      |
| Mu (PSSCH reservation)    | 2 slots                                             |
| # Tx/Rx antennas         | 1, SISO                                             |
| Channel coherence         | 3GPP TR 37.885 NLOSv per UMi-LOS model              |

The single new mechanism vs the relay system model is the **MCD distance gate**: a
HTruck picks which PSFCH slot to listen on based on whether any of the **earlier
transmitting UEs** are inside its MCR distance. This is the exact gateway from the
standard reference (see `common/mcd/McdFeedbackGate`). The harness here stays at
that one-hop level: the relay is removed from the application stack, so all
research questions reduce to *single-hop groupcast feedback*.

---

## Files in this directory

```
scratch_overlay/groupcast_mcr_baseline_eval/
├── groupcast_mcr_baseline_eval.cc     # the binary (mirrors relay_l2id_compare.cc; relMode=none)
├── CMakeLists.txt                     # build_exec(... LIBRARIES_TO_LINK scratch_common ...)
├── gen_mcr_grid.py                    # per-N per-IVD MCR JSON generator
├── run_baseline_eval.sh               # xargs-P sweep + final aggregator pass
├── aggregate_baseline.py              # per-cell finalize-and-prune
├── resume_baseline_eval.py            # idempotent re-runner
├── run_one.sh                         # single-cell helper (debug)
├── analysis.ipynb                     # 6-section analysis (this is the headline)
├── CONCLUSIONS.md                     # generated once results are in
├── README.md                          # this file
└── results/
    ├── sweep.log                       # full sweep log (grows during campaign)
    ├── runlist.txt                     # one line per simulation run
    ├── mcr_grid.json                   # the MCR grid used for the campaign
    ├── cells/<cellTag>/
    │   ├── per_seed.csv                # per-(seed, UE) detail
    │   ├── cell_aggregate.csv          # aggregated cell KPIs (this is the final)
    │   └── FINALIZED                   # atomic marker; safe to delete to force re-aggregation
    ├── <cellTag>/                      # raw simulation output
    │   ├── seed1.db ... seed5.db       # per-seed ns-3 SQLite (pruned M-1 after finalize)
    │   └── sim.log                     # ns-3 stderr/stdout
    └── all_cells.csv                   # master roll-up (rows = one per cell)
```

---

## CLI of the binary

```
./ns3.*-groupcast_mcr_baseline_eval \
    --runId="N8_s80_ivd25_mcr12p0_admit_0_h1_onoff_seed1" \
    --seed=1 --numUEs=8 \
    --speed=80 --ivd=25 \
    --mcr=12.0 \
    --harqOption=1 \                  # 1 = NACK-only groupcast, 2 = ACK/NACK groupcast
    --traffic=onoff \                 # {onoff, periodic1, periodic2, aperiodic1, aperiodic2}
    --simTime=5.0 \
    --outputDir=./results/N8_s80_ivd25_mcr12p0_admit_0_h1_onoff_seed1 \
    --simTag="N8_s80_ivd25_mcr12p0_admit_0_h1_onoff_seed1"
```

Argument defaults match the relay baseline. The single mode is `relayMode="none"`
hard-coded; the `--harqOption` switches the cast type via `FlowDef::harqMode`:

| `harqOption` | `FlowDef.harquMode` | ns-3 cast type                | MCD nack-only gate installed? |
| ------------ | ------------------- | ----------------------------- | ----------------------------- |
| `1`          | `NackOnly`          | `GroupcastNegativeOnly`       | yes — drops ACKs from feedback |
| `2`          | `AckNack`           | `DefaultGroupcast` (ACK/NACK) | no — native behavior          |
| (other)      | `Default`           | `DefaultGroupcast` (ACK/NACK) | no (legacy)                    |

> **Note on `traffic`:** the *periodic* and *aperiodic* class names are parsed but currently
> trigger a pre-existing `Logging component "CustomTrafficApplication" not found`
> registration bug (also reproducible in `ns3 test-custom-traffic`). The campaign
> default is therefore `onoff` (the path the `relay_storm_eval` baseline uses
> successfully). Once the upstream bug is fixed the other class names become
> available at no cost; `aggregate_baseline.py` already skips cells whose
> traffic class failed to run.

---

## Campaign mechanics

The campaign sweeps the 5-D grid:

```
N      ∈ {8, 10, 16, 20}
HARQ   ∈ {1, 2}
traffic ∈ {"onoff"}                                  # default campaign
speed  ∈ {80, 90, 100, 110, 120, 130, 140} km/h      # 7 points (verbatim from run_relay_storm.sh)
IVD    ∈ {25, 27, 29, 32, 34, 37, 39} m              # coupled to speed
MCR    ∈ per-(N, IVD) grid from gen_mcr_grid.py
seeds  ∈ {1..5}                                      # default 5 seeds
```

### MCR grid (`gen_mcr_grid.py`)

For each `(N, IVD)` the grid has:

- `k * IVD - 0.5` for `k = 1, …, N-1`  (admit_k: feedback just covers follower k)
- `admit_0`     ⇒ `--mcr = IVD/2 - 1`         (below k=1; no follower in feedback)
- `admit_all`   ⇒ `--mcr = (N-1)*IVD - 0.5`  (>= k=N-1; full feedback)

The `--quick` flag (`QUICK=1`) drops to N=8 only with one operating point; it is for
debug.

### Aggregate pass (`aggregate_baseline.py`)

After **all M seeds** in a cell have produced `<cellTag>/seed*.db`:

1. Run `kpi_postprocess.py` on each seed → emit `<cellTag>/per_seed.csv`.
2. Compute the cell aggregate → `<cellTag>/cell_aggregate.csv`.
3. Delete M-1 `seed*.db` files (keep `seed1.db` for inspection).
4. Atomically write `<cellTag>/FINALIZED`.
5. Roll up `<cellTag>/cell_aggregate.csv` across all cells → `<results>/all_cells.csv`.

Atomic writes use a temp-file `os.replace(..., FINALIZED)` so a kill at any
mid-state is recoverable.

### Resume (`resume_baseline_eval.py`)

The resume script is idempotent:

- Cells whose `<cellTag>/seed*.db` count == total seeds and `<cellTag>/FINALIZED`
  is present are skipped (no .db deleted).
- Cells short of seed files are listed for re-run.
- Re-running is just `./run_baseline_eval.sh ...`; cells with `FINALIZED` are
  no-ops at sweep-time (no .db written if sim.log + seed*.db already present).

---

## How to launch

### 1. Build the binary (one-time)

```
cd /Users/hadih/Documents/groupcast-adaptation-ns3/ns-3-dev
./ns3 build groupcast_mcr_baseline_eval
```

This also validates that the additive edits to `common/CMakeLists.txt`
(`mcr_nack_only_gate.cc`) did not break `relay_storm_eval` or `relay_l2id_compare`.

### 2. Launch the full campaign

```
cd /Users/hadih/Documents/groupcast-adaptation-ns3/scratch_overlay/groupcast_mcr_baseline_eval
./run_baseline_eval.sh ./results 5.0 "1 2 3 4 5" 8
```

This runs all 4060 simulations (4 N × 2 harq × 1 traffic × 7 speeds × per-N MCR × 5 seeds), then aggregates. With NPAR=8 the wall-time on M-series is ~25 min; with 10 logical cores observed throughput ≈ 290 runs/min.

### 3. Smoke test

```
QUICK=1 ./run_baseline_eval.sh ./results_quick 5.0 "1 2 3" 4
./run_baseline_eval.sh ./results_quick 5.0 "1 2 3" 4   # idempotent re-run
```

The QUICK mode uses N=8 only (56 cells × 3 seeds = 168 runs ≈ 90 s on NPAR=8).

### 4. Analyse

```
jupyter nbconvert --to notebook --execute analysis.ipynb
```

(or open `analysis.ipynb` in a browser kernel + Run All). It writes headline
figures (`fig1_prr_vs_mcr.png` … `fig6_delta_vs_relay.png`) and
`headline_summary.csv`, `baseline_vs_relay_join.csv`.

### 5. Re-run resume

If the campaign was interrupted:

```
./resume_baseline_eval.py ./results 5
```

Prints which seeds still need to run. Re-launch with `./run_baseline_eval.sh` —
FINALIZED cells are no-ops at sweep time; missing seed runs are re-executed; the
aggregator pass at the end re-aggregates everything that finished.

---

## What this campaign answers

The system model is the relay baseline stripped of the relay. The hypotheses:

1. **H1 (PRR vs MCR):** with HARQ option-1 (NACK-only groupcast), `PRR_worst` rises
   sharply as the MCR distance crosses `k*IVD` for k=1..N-1, because that is the
   threshold at which follower k enters the feedback group.

2. **H2 (HARQ option differentiation):** HARQ option-2 (ACK/NACK groupcast) should
   produce better PRR than option-1 *above* an MCR threshold where it can issue
   useful reTX without storming; *below* that threshold, option-2 should be no
   better than option-1.

3. **H3 (PIR / AoI scaling with N):** the single-hop PIR worst grows with
   `(N-1)*RRI` because of the trunk reservation delay; AoI grows similarly.

4. **H4 (collision stack):** increasing MCR beyond `(N-1)*IVD` keeps the
   corrupt-count partition shifted to channel error (CE) and reduces the
   per-cell `ce_total`. Both HD and RC stay near zero because MCD/HARQ gating
   suppresses retransmissions on slots the Tx is unable to listen on.

5. **H5 (delta vs relay):** the relay mechanism should help when MCD is *tight*
   (MCR < (N-1)*IVD); should harm or be neutral when MCR is loose. The Section-6
   `delta_prr_worst` figure is the headline.

---

## What was added vs the upstream tree (additive only)

```
common/mcd/mcr_nack_only_gate.{h,cc}              # HARQ option-1 ACK-drop shim (mirror of McdFeedbackGate)
common/CMakeLists.txt                             # +1 source file in scratch_common
common/flows/groupcast_flows.h                    # +FlowDef::harqMode enum (Default|AckNack|NackOnly)
scratch_overlay/groupcast_mcr_baseline_eval/      # entire new directory
ns-3-dev/scratch/groupcast_mcr_baseline_eval      # symlink (per scratch_overlay convention)
```

All `relay_l2id_compare` / `relay_storm_eval` paths are unchanged. The mcr_nack_only_gate is a separate additive module so it doesn't disturb the existing McdFeedbackGate used by relay_l2id_compare.
