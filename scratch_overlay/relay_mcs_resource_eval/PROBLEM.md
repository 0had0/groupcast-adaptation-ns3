# `relay_mcs_resource_eval` — Problem statement

## Context

We have a baseline answer: in a static NLOSv 8-vehicle platoon, the leader's TX
MCS is capped by the *worst* RX (node 7), so a single-hop groupcast must use a
low MCS to keep tail PRR ≥ 99%. That low MCS implies a large TB span and a high
per-packet subchannel×slot cost.

Hypothesis: a **single same-L2ID relay** (one node that decodes + forwards)
splits the link into two shorter hops; if each hop is allowed to use a higher
MCS than `mcsBaseline` (because its own worst-RX is closer), the *sum* of
subchannel-slot usage over both hops may be lower than the single-hop baseline,
at equal-or-better tail/mean PRR, PIR, AoI.

We also want the *empirical estimators* the design needs to actually pick a
relay: the PRR-vs-(MCS, distance) surface (R-A), the per-relay latency model
(R-C), and the MCD where no NACK storm is introduced (R-D).

## Sim design (one line)

`relay_mcs_resource_eval` — one binary, `--mode={baseline,relay}`, per-node
MCS via `nodeMcsOverride` (leader = `mcsHop1`, relay = `mcsHop2`), single
global MCD feedback gate, same L2ID for both hops, moving platoon across the
7 tested `SPEED_IVD_PAIRS`, mu=2 / 10 subch / PSFCH-4 / `maxNumTx=5`, 300 B
CAM @ RRI 10 ms, geometric NLOSv channel.

## Requirements

### R-A — PRR reliability surface
For each vehicle `i` and MCS `m` and `(speed, ivd)` pair, estimate
`P_hit(i, m, pair) = Pr[ PRR_i(m) ≥ target ]` from pooled sliding-window PRR
samples across seeds. The per-vehicle max-decodable MCS is the derived contour
`m*_i(pair) = max{ m : P_hit(i, m, pair) ≥ conf }`. If no MCS clears the
`conf` threshold, use the MCS that archives the best tail PRR (OP3).
`mcsBaseline(pair) = m*_7(pair)`.

Distance is fixed per pair: `d_i = i * ivd` for the static-part-relative
geometry, and varies only with `ivd`. MCS_max is *not* an independent input —
it is a derived contour of the `PRR(MCS, distance)` surface.

### R-B — relay selection
Sweep `r ∈ {2, 3, 4, 5, 6}` (skip r=1 too close, r=7 is the tail). At each
`r`, set `mcsHop1 = m*_r(pair)`, `mcsHop2 = m*_{7-r}(pair)`, MCD = `r*ivd`.
For each candidate, measure:
- PSR(r) — first-hop decode ratio at the relay
- storm_ratio(r) — leader NACK inflation vs the MCD-minimum baseline
- |PRR_hop1 − PRR_hop2| (residual imbalance from MCS discreteness)
- per-hop latency and total resource

Pick `r*(pair)` to **minimize total resource** subject to:
- `PSR(r) ≥ target`
- `storm_ratio(r) ≤ threshold` (no storm introduced)
- `|PRR_hop1 − PRR_hop2| ≤ epsilon_balance` (no PRR degradation between hops)

### R-C — relay latency model
Per-packet timestamps from `relayTiming` (gen, decode, send at relay) and
tail RX timestamp:
- `hop1_latency = decode_time − gen_time`
- `relay_proc = send_time − decode_time`
- `hop2_latency = tail_rx_time − send_time`
- `e2e_latency = hop1 + relay_proc + hop2`

Regress vs `(dist(0, r), mcsHop1, dist(r, 7), mcsHop2, reTX)`.

### R-D — storm / MCD threshold
At `r*(pair)`, sweep global MCD = `{(r*−1), r*, (r*+1), (r*+2)} * ivd`. Locate
the smallest MCD that:
- covers hop-2 tail RX (no PRR degradation)
- keeps hop-1 NACK/reTX inflation low (storm-free)
- preserves the `|PRR_hop1 − PRR_hop2|` balance

This is the *minimum-resource storm-free MCD* for that pair.

## Hypotheses (overall study)
- **H1 (resource win):** relay 2-hop total subchannel-slots < baseline 1-hop at
  matched `maxNumTx`, identical pool.
- **H2 (reliability ≥):** relay tail & mean PRR ≥ baseline.
- **H3 (latency ≤):** relay PIR & AoI ≤ baseline, despite the extra hop.
- **H4 (PSR gate):** the win holds only where first-hop PSR ≥ `target`;
  below that the relay drops packets it cannot forward.

## KPIs (defined)

Resource (headline, from new absolute columns in `kpi_postprocess.py`):
- `subch_slots_used_total` = `Σ (rbLen / sbChSize)` over `psschTxUeMac`
- `subch_slots_used_leader`, `subch_slots_used_relay`
- `subch_slots_total` = `pssch_slots * n_subch`
- `subch_slots_per_pkt` = `subch_slots_used_total / (packets delivered)`
- peak instantaneous subchannel utilization
- fragmentation-slot count (TB spanning > pool in one slot)

Reliability:
- tail PRR (worst RX, 99th percentile of 200 ms windows), mean PRR
- per-RX PRR vs distance
- `P_hit(i, m, pair)` (R-A)

Latency:
- PIR mean/p95, AoI mean/peak
- end-to-end 2-hop latency decomposed per hop (R-C)

HARQ / storm:
- per-hop NACK rate, mean reTX count, storm onset vs MCD (R-D)

PSR:
- first-hop decode ratio at the relay

## Phases (concrete ordered steps)

1. **Phase 1 — MCS×distance (R-A).** Baseline, MCS `{0, 2, 4, …, 22}` × 7
   pairs × 5 seeds (~420 runs, ~32 min). `aggregate.py` → `P_hit` surface,
   `m*_i(pair)`, `mcsBaseline(pair)`. Gate: `m*_1 > m*_7` spread is real;
   if not, bump IVD per pair.
2. **Phase 2 — relay sweep (R-B, R-C).** `r ∈ {2..6}` × 7 pairs × 10 seeds
   (~350 runs, ~27 min), with `mcsHop1=m*_r`, `mcsHop2=m*_{7-r}`,
   MCD=`r*ivd`. Select `r*(pair)` from selection frontier.
3. **Phase 2b — storm/MCD threshold (R-D).** At `r*`, MCD in
   `{(r*−1), r*, (r*+1), (r*+2)}*ivd` × 7 pairs × 5 seeds (~140 runs,
   ~11 min). Locate min-resource storm-free MCD.
4. **Phase 3 — head-to-head.** Baseline(`m*_7`) vs relay(`r*`, `m*_{r*}`,
   `m*_{7-r*}`, MCD) × 7 pairs × 10 seeds (~140 runs, ~11 min). Full KPI
   + absolute resource, H1–H4 verdict with Wilcoxon paired by seed.
5. **Notebook.** R-A surface, R-B selection frontier, R-C latency
   regression, R-D storm threshold, H1–H4 verdict table. 0 errors / 0
   warnings.
6. **Commit** code + notebook (not `results_*/`).

Total ≈ 1050 runs, ≈ 80 min on 10 cores, schedule across phases.

## Honest-analysis rules
- Every "relay wins on resource" must be cross-checked against tail PRR
  (never let resource "wins" mask dropped packets).
- PSR reported alongside every R-B frontier point.
- R-A `m*_i` noted as `best` (not `target`) when no MCS meets `conf`.
- Wilcoxon paired by seed (not just mean ± std).
- Forward-compat: any change to `kpi_postprocess.py` (new columns) and
  `nr_sl_stack.{h,cc}` (`nodeMcsOverride{}`) keeps prior consumers byte-
  identical.
