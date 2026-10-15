# Plan — groupcast retransmission × helper selection × helper scheduling campaign

## Step 0 (first action after approval)

Write this plan verbatim to
`scratch_overlay/platoon_harq_robustness_eval/PLAN.md`. The user will hand it to another
agent, so it must be self-contained. Commit nothing unless the user asks.

## Hand-off notes for the implementing agent (read first)

- **Repository and branch.** Repo `/Users/hadih/Documents/groupcast-adaptation-ns3`, branch
  `hidden-node`, base commit `2721fcd`. The study folder is
  `scratch_overlay/platoon_harq_robustness_eval/` (program `platoon_harq_robustness_eval.cc`).
  Shared stack code is in `scratch_overlay/common/` (stack, flows, traffic, kpi, mcd).
- **The working tree has unrelated edits.** `scratch_overlay/common/*`, other studies and
  ~100 untracked `fix_*.py` / `debug_*.py` scripts at the repo root are not part of this work.
  Never stage or commit them; stage files explicitly.
- **NR changes live in a patch.** `ns-3-dev/contrib/nr` is a submodule. Its changes for this
  study are captured in `patches/nr/local.patch`; after editing contrib/nr, run
  `scripts/regen-patches.sh`. `setup.sh --force` on the server reinstalls `patches/`, so
  uncaptured contrib/nr edits are lost there. The repo pre-commit hook regenerates and stages
  `patches/`: stage explicitly and commit with `--no-verify`.
- **Every new option must default off**, and with all of them off the outputs must be
  byte-identical to `2721fcd`. This has been the rule for every previous change (README
  sections "Standard Mode-2 grant types…", "Per-TB helper selection").
- **The selector must not touch resource allocation.** No ns-3 random draws and no channel
  queries; prove it with shadow mode. Any error RNG (position error) is a separate
  deterministic PRNG.
- **Machines.**
  - The server is the reference: `ssh -F ~/.ssh/ease-config ease-rtx`, repo in
    `~/Documents/groupcast-adaptation-ns3`, optimized build, 24 jobs.
  - The laptop is crash-prone: JOBS ≤ 2, debug build, short pilots (`--maxTime 10–20`) only.
    Never pool seeds across machines (the SUMO traces differ).
- **Existing driver to copy:** `run_dynhelper.sh` (build / check / campaign / analyze,
  `flock` lock, provenance, `verify_dir`, the shadow `same_outputs` identity test). The
  runner and arm registry are in `run_coop.py` (`ARMS`, `check()`, `tag_of`, the command line
  at `run_coop.py:176`). Traces come from `run_campaign.make_trace / trace_path`
  (`traces/<sc>_ivd<ivd>_n<N>_s<seed>.csv.gz`, SUMO via `gen_mobility.py`).
- **Background reading:**
  - `README.md`: scenario matrix, radio configuration, the Option-1 emulation, the SB-SPS
    re-run, per-TB helper selection.
  - `DYNHELPER_SPS_CONCLUSION.md`: the latest results and the helper-coverage finding that
    motivates Phase 0.
  - `HANDOFF.md`: simulator caveats.
- **Base flags of the current reference configuration** (SB-SPS re-run): `--leaderSps=true
  --spsReselectOnMisfit=true --slDynExclusion=true --slProjectAnnounced=true
  --slProbResourceKeep=0.8 --rriList=0,10,20,50,100,200,300,400,500,600,700,800,900,1000`,
  plus `--ns3::LteRlcUm::ReorderingTimer=0ms`.
- **Configuration is pinned (Phase 0).** Pool, leader traffic classes and background load
  are decided; `POOL_STUDY.md` is superseded (its pilot tables were not produced by runs).
  **Stop after `run_retx.sh check`** (Phase 1–2 done, smoke pilots passing) and report the
  smoke results and the measured CBR of the `calib` pilots to the user before stages A–C.

## Context

The user wants one study, with run files to submit on the server, that compares on a 9-car
platoon (leader + 8 members), highway (3GPP V2V_Highway) and roundabout (V2V_Urban), IVD
{5, 15, 20, 30} m, leader traffic {periodic1tb, periodic2tb, aperiodic1tb} (the one-TB
classes, README "One-TB leader traffic classes"), background {none,
moderate, high}, MCS 14:

- **Retransmission strategies (R)**, each with 1 or 2 retransmissions (`maxNumTx` 2 / 3):
  - R1 leader HARQ-less blind
  - R2 leader Option 1
  - R3 leader Option 2
  - R4–R6: the leader reserves with SB-SPS and delegates the retransmission slots to a helper
    selected per TB. The helper relays blind (R4), on overheard Option-1 NACKs (R5), or on
    overheard Option-2 feedback (R6).
  - R6 rule (implemented 2026-10-15, README "HARQ Option 2 with a per-TB helper"):
    - the named helper never ACKs: when it decoded the TB it is silent and listens; it
      relays unless all members − 1 others ACKed; when it did not decode it NACKs;
    - the leader ignores the helper's silence and, on its NACK, takes back the next
      handed-over slot and retransmits;
    - success = all non-helper members ACKed and no NACK.
    A helper that misses the SCI is silent too; this is measured (`helper_sci_missed*`).
- **Helper selection (H)**:
  - H1 mid member (4)
  - H2 Kuo, distance-only
  - H3 Kuo + geometric line-of-sight test (buildings and vehicles) on CAM positions with
    random error
  - Option 2 only:
    - H4 HARQ statistics (helper next to the member that NACKs most over a window,
      several windows)
    - H5 = H4 + Kuo
    - H6 = H4 + Kuo + geometry
- **Helper scheduling (S)**:
  - S1 the leader reserves everything with SB-SPS and delegates (the slots of R4–R6)
  - S2 the helper senses and reserves its own SB-SPS
  - S3 the helper senses and uses dynamic grants
- **Metrics and assertions**: per member PRR within / outside the PDB, collisions, loss reasons,
  resources reserved vs used (leader + helper, helper only), helper drop rate, latency,
  spectrum use. Assertions throughout, and enough seeds.

Evidence from this session that shapes the plan:

- **The current pool already limits retransmissions.** It has 5 × 10 PRB at μ=1 and 9 of 20
  slots for sidelink. periodic2 TBs fill about 4.6 of the 5 subchannels, so the leader
  reserves on average 1.8 slots per TB even at N 0, and under load 51–62 % of its TBs get
  a single slot with nothing to hand to a helper.
- **Runtime grows roughly with N².** Median wall time is about 20 s at N 0, about 1000 s at
  N 45 and about 3000 s at N 60.
- **μ=2 is not a bigger pool.** At fixed 20 MHz the resource elements per second are the same
  for every μ. μ=2 has about 27 PRBs per slot, so a 1200 B periodic2 packet (~45 PRBs at
  MCS 14) needs two TBs.

### Decisions taken with the user

- **Platoon:** leader + 8 members, as now; the mid helper is member 4.
- **Background (decided 2026-10-14):** the full simulated area and today's car count
  (N = 60) for both loaded levels. The load is raised **per car** (k independent background
  flows per car) to reach the CBR targets none 0 / moderate ≈ 0.3 / high ≈ 0.6. k comes from
  the measured per-car load, and the measured CBR is reported in every table. No lever
  validation runs (Phase 0).
- **Design:** staged, with 5 screening seeds and top-up seeds only where paired CIs are too
  wide.
- **Leader traffic:** the one-TB classes `periodic1tb`, `periodic2tb`, `aperiodic1tb`
  (implemented, README "One-TB leader traffic classes"): every leader packet is one TB of the
  pool, asserted at run time. Background keeps periodic1 / aperiodic1.
- **aperiodic1tb:** the leader, and the helper under S2, reserve with SB-SPS at RRI 20 ms
  (Phase 0). The retransmission slots sit inside each period's
  reservation, an arrival uses the next reserved period, and empty periods count as
  "reserved, not used". (The RRI does not change the retransmissions inside a period;
  it only sets how long an arrival waits and how much is reserved for nothing.)

## What exists vs what is new

| item | status | where |
|---|---|---|
| R1 blind (leader always sends `maxNumTx`) | exists (`--option1Tx=false`) | `nack_only_harq.{h,cc}` |
| R2 Option 1 at the leader | exists | `nack_only_harq.*`, `common/mcd/mcr_nack_only_gate.*` |
| R3 Option 2 at the leader | **new**: stock CTTC cancels on the *first* ACK; needs "all 8 ACK, a missing ACK (DTX) counts as a NACK" | extend `NackOnlyHarqController` with mode `opt2`; `sp.harqOption=2` path in `common/stack/nr_sl_stack.cc` (no NACK-only gate) |
| R4 / R5 with S1 | exist (`leaderRes`, `--helperBlind`) | `nr_sl_coop_scheduler.cc:1690`, `contrib/nr` `NrSlUeMac` relay path (`nr-sl-ue-mac.cc:1395-1480`) |
| R6 helper gated by Option-2 feedback | **exists** (2026-10-15): `HelperFeedbackMode=2`, `HelperGroupSize` = members − 1; the named helper never ACKs (receiver shim `InstallOption2HelperSilence`); the leader reclaims a handed slot on the helper's NACK (`NrSlUeMac::ReclaimSlHelperSlot`); `SlHarqInfo::m_helperRnti` from SCI-2A | `NrSlUeMac`, `nack_only_harq.*`, patch in `patches/nr/local.patch` |
| S3 helper dynamic own grant | exists for NACK (`ownRes`); **check / add blind and Option-2 triggers** | `nr-sl-ue-mac.cc:1312-1376` |
| S2 helper own SB-SPS | **new**: a relay SPS reservation per flow, RRI = the flow's RRI, slots after the leader's first transmission + PSFCH gap, realigned when the leader reselects, released after `sl-ReselectAfter` unused periods | `NrSlCoopScheduler` (helper side) + `NrSlUeMac` |
| One-TB leader classes `periodic1tb` / `periodic2tb` / `aperiodic1tb` | **exists** (2026-10-14): start-up TB-fit guard, end-of-run split check, run-CSV columns `tb_max_b`, `payload_max_b`, `leader_pkt_multi_tb`, `leader_pkt_multi_tb_unqueued` | program (`BaseTraffic`, `MaxLeaderPayload`), `CustomTrafficApplication::Aperiodic1MaxSize` |
| Leader SB-SPS for aperiodic1tb | **new** RRI option (`--aperiodicRri`, used for the aperiodic base class only) | `TrafficFlowKeys` in the program (today aperiodic1 is `rri_ms=0;dynamic=1`) |
| H1 / H2 / H3 without error | exist (`static`, `kuo`, `map`) | `helper_select.{h,cc}` |
| H3 position error, vehicles = all CAM senders | **new**: `--posErrM` σ (Gaussian per CAM per car, its own deterministic PRNG so the simulation RNG is untouched); blockers include background cars | `helper_select.*` |
| H4–H6 HARQ statistics | **new**: per-member NACK/DTX rate over window W (all HARQ processes), fed by the Option-2 controller | `helper_select.*` (`OnFeedback`), program wiring |
| IVD 20 m traces | **new** traces (`gen_mobility.py --ivd 20`, cached by `run_campaign.make_trace`) | `gen_mobility.py`, `run_campaign.py` |
| Background load per car | **new** `--bgFlowsPerCar=k` (≥ 1, default 1): each background car gets ⌊k⌋ flows plus one more with probability k − ⌊k⌋, all of the car's class (periodic1 / aperiodic1), each with its own port, destination L2 ID and start phase. No extra random draw when k = 1 (byte-identical) | flow spec loop in the program (`spec << ",label=bg" …`) |
| T2 per numerology / PDB | **new**: T2 is fixed to 33 slots in `nr_sl_stack.cc:163`; make it min(PDB, configured) in slots | `nr_sl_stack.cc`, program flag |
| Per-member PRR in / outside PDB, loss reasons, collisions, latency | exist (`_member.csv`: `prr`, `prr_pdb`, `lost_collision/channel/notx/other`; `_pkt.csv`) | program |
| Reserved vs used resources, helper drop rate | **new** counters | program + scheduler traces |

## Phase 0 — Pinned configuration (no pool or lever study)

### 0a. Pool

| parameter | value | program flag |
|---|---|---|
| numerology | μ = 1 (30 kHz, 0.5 ms slot) | `--mu=1` (default) |
| carrier | dedicated: every slot sidelink (2000 SL slots/s; n47 is an ITS band) | `--dedicatedCarrier=true` |
| bandwidth / subchannels | 20 MHz, 5 × 10 PRB | defaults |
| MCS | 14 for every UE | default |
| PSFCH | period 1, sl-MinTimeGapPSFCH 3 | defaults |
| largest TB | 1333 B (5 subchannels, 9 PSSCH symbols); 264 / 531 / 798 / 1066 B for 1–4 | `tb_max_b` in the run CSV |

Measured with this pool on the server (`variants_sps/dedicated`, 5 × 10 PRB, before the
one-TB classes): periodic2 leader 2.99 reserved slots per TB at N 0. With the shared TDD
pool it was 1.8, which is why that pool is dropped.

### 0b. Leader traffic (implemented)

| `--traffic` | sizes (B) | arrivals | PDB | grant |
|---|---|---|---|---|
| `periodic1tb` | 190 / 300 | 100 ms | 100 ms | SB-SPS, RRI 100 ms |
| `periodic2tb` | 800 (p 0.8) / 1200 | 10 ms | 10 ms | SB-SPS, RRI 10 ms |
| `aperiodic1tb` | uniform {200, 400, …, 1200} | 50 ms + Exp(50 ms) | 50 ms | SB-SPS, RRI 20 ms (`--aperiodicRri=20`, new) |

The program's start-up guard and end-of-run check make "one packet = one TB" an assertion
(README "One-TB leader traffic classes"). SPS needs `--spsReselectOnMisfit=true` (part of the
base flags), or the end-of-run check aborts.

**Why RRI 20 ms for aperiodic1tb:**
- An arrival waits at most 20 ms (mean 10 ms), then 3 transmissions, which stays inside the
  50 ms PDB. RRI 50 would equal the PDB.
- With a mean inter-arrival of 100 ms, about 80 % of the reserved periods carry no data,
  against 90 % at RRI 10 ms. RRI 10 would also reserve as much as periodic2tb.
- A one-cell sensitivity at RRI 10 ms runs in stage A (below).

### 0c. Background load

- **Cars:** N = 60 background cars in today's area (highway ±1 km on the 5 non-platoon lanes,
  roundabout 500 m radius), 60 % periodic1 (SPS) / 40 % aperiodic1 (dynamic), as in every
  earlier campaign.
- **Measured base load:** on this pool, background CBR is 0.18 at N 60 and 0.135 at N 45, in
  both scenarios (`variants_sps/dedicated`: `cbr_occupied − cbr_leader`). That is ≈ 0.003 of
  CBR per car-flow.
- **Levels:** set by the number of flows per car, `--bgFlowsPerCar=k`, from that linear
  scaling; no run sets k and there is no lever validation (the `calib` step only prints the
  measured CBR). The measured CBR is reported per cell (run-CSV
  `cbr_occupied`, `cbr_leader`).

| level | N | k | expected background CBR |
|---|---|---|---|
| none | 0 | — | 0 |
| moderate | 60 | 1.67 | ≈ 0.30 |
| high | 60 | 3.33 | ≈ 0.60 by linear scaling — **but a 9 s laptop pilot measured 0.92** (highway IVD 30, periodic2tb leader, 223 background flows); see the note below |

**Why this lever:**
- It keeps the area: the simulator has no communication-range cut-off, so on the highway
  cars up to the edge of the area are sensed and interfere.
- It keeps today's car density: the roundabout gridlocks above about 70–80 cars.
- Runtime grows with the number of transmissions × receivers, i.e. about ×k, instead of
  ×k² for k times more cars.
- Moderate and high differ only in the load, not in the interferer geometry.

**k is not linear at high load (pilot 2026-10-15).** k = 3.33 overshot (CBR 0.92 instead
of ≈ 0.6). Before stage A, the `calib` step must report the measured CBR for k ∈ {1.67, 2.0,
2.5, 3.33} per scenario. Use the k closest to 0.3 / 0.6 and record the choice in the run
CSV (`bg_flows_per_car`). This picks k; it is not a lever validation.

**Cost knob** (not the default): `BG_CARS=30` with k doubled (3.33 / 6.67) halves the runtime
at the same CBR, with fewer, busier interferers.

## Phase 1 — Implementation (all new options default off; byte-identical when off)

1. **Option 2 at the leader** (`nack_only_harq.*` → mode `blind | opt1 | opt2`):
   - The wrapper records ACK / NACK per member per transmission and does not forward ACKs.
   - At the decision instant it delivers an ACK only if all M members ACKed; DTX counts as
     a NACK.
   - Receivers use the stock ACK/NACK, without the NACK-only gate.
   - Program flag `--harqMode`, replacing `option1Tx`, which is kept as an alias.
2. **Helper feedback gating** (`NrSlUeMac`, patch): `HelperFeedbackMode` {blind, nack, allAck}
   with a group-size attribute. The Option-2 rule is "relay unless every member's ACK was
   overheard for that PSSCH by its decision time"; the same rule gates the second relay.
3. **S3 own dynamic grant:** verify blind and allAck triggers on the `ownRes` path, and add
   them where missing.
4. **S2 helper SB-SPS:**
   - The first time a member is named helper for a flow, it selects (sensing) an SPS relay
     grant: R slots per period, RRI = the flow's RRI, inside [leader initial + PSFCH gap +
     processing, deadline].
   - The grant is used in every period where the member is named and its trigger fires.
   - It is released after `sl-ReselectAfter` = K unused periods (TS 38.321).
   - It is reselected when a leader reselection moves the initial transmission past it.
5. **Leader SB-SPS for aperiodic1tb:** `--aperiodicRri=20` gives the aperiodic base class
   `rri_ms=20;pdb_ms=50;dynamic=0` in `TrafficFlowKeys` (default 0 = today's dynamic grant).
   Arrivals use the next period; misfit reselection stays on (an SPS grant sized for a 200 B
   packet reselects for a 1200 B one); `sl-ReselectAfter` releases an unused reservation.
   Only the leader's flow is affected.
6. **Selectors** (`helper_select.*`):
   - `--posErrM` (H3, default 1.5 m) and background blockers from CAMs.
   - `harq` (H4): worst = argmax NACK/DTX rate over `--hstatWindowMs` (ties → farther); the
     helper is the member nearest to it, preferring one ahead of it; no data → member 4.
   - `harqKuo` (H5) and `harqMap` (H6): Kuo eq. 17 with P̂(L→r) = (n_r(1−ν_r) + n₀P_model) /
     (n_r + n₀), n₀ = `--hstatPrior` (default 10).
   - Windows {0.5, 1, 3} s.
   - Per-decision cost logged: link evaluations, building tests, CPU µs (log only).
7. **T2 = min(PDB, `--t2Ms`) in slots**; IVD 20 traces (`gen_mobility.py --ivd 20`, highway
   and roundabout, N 0 and N 60, seeds 1–20); `--bgFlowsPerCar` (table row above).
   Background cars carrying k flows each appear in the run CSV as `bg_flows_per_car` and
   `bg_flows`.
8. **Program composition:** `--retx=R1..R6` maps to the HARQ mode + helper mode;
   `--helperSched=S1|S2|S3` and `--helperSelect=…` stay orthogonal; illegal combinations
   abort (H4–H6 without Option 2, S2/S3 without a delegating R).

## Phase 2 — Metrics and assertions

**New run-CSV / `_member.csv` columns:**
- `res_reserved_{leader,helper}`, `res_used_{leader,helper}` (subchannel-slots), and
  `res_unused_{ack, no_trigger, no_data, helper_not_decoded, reselected}`
- `res_per_pkt_{used,reserved}`, `eff_member_pkts_per_res`, `platoon_cbr_share`
- `helper_assigned`, `helper_decoded`, `helper_drop_rate`, `relay_needed`, `relay_sent`,
  `relay_ok`
- Option 2 with a helper (exists): `helper_named_tx`, `helper_decoded_tx`, `helper_corrupt_tx`,
  `helper_nack_sent`, `helper_nack_not_sent`, `helper_nack_lost`, `helper_nack`,
  `helper_reclaim`, `helper_nack_no_slot`, `helper_sci_missed`, `helper_sci_missed_rate`,
  `helper_sci_missed_tb`, `helper_sci_missed_unserved`, `helper_ack_suppressed`,
  `helper_ack_rx` (= 0, asserted), `relay_after_allack` (README table)
- per member: `prr`, `prr_pdb`, `late = prr − prr_pdb`, delay p50 / p95 / p99, losses by
  reason (the collision / channel / other split exists)
- `sel_ops_mean`, `sel_cpu_us_mean`

**End-of-run assertions** (`NS_ABORT_MSG_IF` in the program; the run fails, it does not
just warn):
- rx_pdb ≤ rx ≤ sent per member
- Σ losses = sent − rx
- used ≤ reserved, and reserved − used = Σ unused reasons
- leader transmissions per TB ≤ maxNumTx
- relays only by the named member (`relay_by_unchosen = 0`), only for decoded TBs, only when
  the trigger holds (Option 1: a NACK; Option 2: not all ACK)
- Option 2: no retransmission after all-ACK
- blind: every handed slot used when the helper decoded
- selector rows = helper-assigned TBs

**Driver `verify` step** (all run CSVs):
- the flags of the configuration (pool, RRI, T2, `harq_mode`, `helper_sched`)
- `bg_flows_per_car` = the level's k, `dedicated_carrier` = 1, `traffic` ∈ the one-TB classes,
  `leader_pkt_multi_tb_unqueued` = 0, `tb_max_b` = 1333
- measured CBR per cell, reported (`cbr_summary.csv`, background and total); no pass / fail
  band
- t-Reordering 0
- `leader_grant_slots_mean` against the Phase-0 expectation at N 0

**Regression:** all new options off → byte-identical to `2721fcd` + the one-TB classes (the
reference binary to rebuild first) on the 9 pilots of the previous checks; shadow-mode identity for every new selector.

## Phase 3 — Campaign and run files

**Files (new, reuse existing helpers):**
- `run_retx.py`: arm generator R × H × S × retx count, cells, `check()` extended with the new
  invariants. Reuses `run_campaign.make_trace / trace_path / NET`, `run_coop` `check`,
  latency and summary code, the parallel and resumable runner (`run_coop.py:176` pattern).
- `run_retx.sh`: driver modelled on `run_dynhelper.sh`, with steps `build traces check calib
  stageA stageB stageC topup analyze status`, lock file, provenance (commit, binary sha256,
  flags), resumable, `JOBS` / `SEEDS` / `STAGE` / `BG_CARS` env.
  - `traces`: every missing trace (IVD 20 included).
  - `check`: smoke runs of every new mode plus the identity tests.
  - `calib`: one 20 s run per scenario × level with R2 and periodic1tb, only to print the
    measured CBR next to the expected one (report, no decision).

**Stages:**

| stage | arms | grid | runs at 5 seeds |
|---|---|---|---|
| A — retransmission strategies | R1, R2, R3 (leader) + R4, R5, R6 with S1 and H3 (default selector), each × {1, 2} retx = 12 | 2 scen × 4 IVD × 3 bg (none / moderate / high) × 3 traffic (periodic1tb / periodic2tb / aperiodic1tb) = 72 cells | 4 320 |
| A′ — aperiodic RRI sensitivity | R2, R5-S1-H3 at 2 retx, `--aperiodicRri=10` | aperiodic1tb, IVD 30, 2 scen × 3 bg = 6 cells | 60 |
| B — helper selection | R4: H1–H3; R5: H1–H3; R6: H1–H3 + H4–H6 × 3 windows = 18; S1; 2 retx | 2 scen × IVD {15, 30} × 3 bg × 3 traffic = 36 cells | 3 240 |
| C — helper scheduling | S2, S3 × R4, R5, R6 × the best H of stage B per R × {1, 2} retx = 12 (S1 reused from A / B) | 36 cells | 2 160 |

The best selector per R is chosen by a pre-registered rule: the highest mean worst-RX PRR
within PDB over the cells, paired against H1; if not significant, the lower-complexity
selector.

**Seeds:** 5 screening seeds. Top-up (`topup` step reads `analysis/seed_topup.csv`): any cell
where the 95 % CI half-width of a pre-registered paired contrast is > 0.02 on worst-RX
PRR within PDB and the sign is unresolved gets +5 seeds, up to 20.

**Budget** (server, 24 jobs). Measured on the dedicated carrier at N 60 (k = 1): highway
2000–3400 s per 100 s run, roundabout ≈ 1700 s. Highway runs are cut to 50 s
(`--maxTime=53`, KPIs from 3 s). Per-car load multiplies the runtime by about k.

| level | runtime per run |
|---|---|
| none | ≈ 30 s |
| moderate | ≈ 2.5 k s |
| high | ≈ 5 k s |

That gives a mean ≈ 2.5 k s per run, i.e. stage A ≈ 125 h, B ≈ 95 h, C ≈ 65 h, ≈ 285 h
(12 days) before top-ups.

Cuts, in order of preference:
1. `BG_CARS=30` with k doubled (≈ ×0.5)
2. stage B / C on IVD 30 only (≈ ×0.5 for B and C)
3. 3 screening seeds

`status` prints the projection from the measured wall times; tell the user the projected
total after `calib`, before launching stage A.

Run order on the server (one command per step):

```bash
JOBS=24 ./run_retx.sh build traces check calib
```

```bash
tmux new -d -s retx -c "$PWD" 'JOBS=24 ./run_retx.sh stageA stageB stageC topup analyze'
```

## Phase 4 — Analysis (`analyze_retx.py`)

1. **Retransmission strategies:**
   - group and worst-RX PRR within / outside the PDB
   - reliability–latency curves (group, worst RX)
   - resources used / reserved per packet
   - a Pareto front of worst-RX PRR against used resources, which picks the spectrum-efficient
     method
2. **Helper selection:**
   - a complexity table (operations, CPU µs, inputs needed: CAM, map, Option-2 PSFCH,
     window state)
   - resources used, latency, PRR of the group and the worst RX
   - helper drop rate
3. **Helper scheduling:**
   - PRR of the group and the worst RX
   - helper and leader collision rates
   - reserved / unused / used resources
4. **Every scenario:**
   - per-member PRR in and out of the PDB
   - collision metrics, loss reasons, reserved vs used, totals for leader + helper or helper
     only

The paired statistics and the per-seed tests reuse `analyze_dynhelper.py`.

## Verification

- The one-TB classes are already verified (README "One-TB leader traffic classes":
  byte-identity with the classes unused, both assertions fire on their negative tests).
- `--bgFlowsPerCar=1` byte-identical to the current binary; with k = 2, twice the background
  flows (run CSV `bg_flows`) and about twice the background CBR in a 20 s pilot.
- Laptop debug pilots of 10–20 s (JOBS=2): one per new mode (opt2 leader, R6 allAck, S2, S3
  blind, H4–H6, posErr, aperiodic SPS), with the new assertions active.
- A scripted check, after `check_sps.py`: Option 2 sends 0 retransmissions after all-ACK and
  retransmits every TB with a missing ACK while slots remain; S2 relay slots always come after
  the leader's initial transmission + gap.
- Byte-identity regression with the options off, and shadow identity for the selectors (as in
  `run_dynhelper.sh check`).
- `run_retx.sh check` and `calib` on the server before any campaign step; the smoke results,
  the measured CBR per level and the projected hours are reported to the user before
  stages A–C.

## Assumptions the user should check

- H4 rule: "the member closest to the most-NACKing member, preferring the one ahead of it".
- H3 position error σ = 1.5 m.
- Windows {0.5, 1, 3} s; prior n₀ = 10.
- Background: N = 60 cars in the full area, k = 1.67 (moderate) / 3.33 (high) flows per car
  from linear scaling of the measured 0.18 CBR at k = 1; high may land below 0.6. Reported as
  measured, not enforced.
- aperiodic1tb on SB-SPS at RRI 20 ms (RRI 10 ms as a sensitivity).
- Leader traffic = the one-TB classes (aperiodic1tb: sizes {200, …, 1200} B; background keeps
  aperiodic1 up to 2000 B).
- 5G-LENA PSFCH is lossless; Option-2 capacity is checked analytically only.
