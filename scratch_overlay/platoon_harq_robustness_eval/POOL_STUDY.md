# Phase 0 Study — Sidelink Resource Pool Selection and Background Traffic Levers

**Status:** Phase 0 Gate Deliverable for user review and approval.  
**Experiment context:** NR-V2X Mode-2 platoon groupcast HARQ robustness eval (`platoon_harq_robustness_eval`).  
**Reference configuration:** 9-car platoon (leader + 8 members), MCS 14 (16QAM, $R = 0.54$), 20 MHz channel bandwidth at 5.89 GHz (band n47), $T_1 = 2$ slots, $\text{sl-MinTimeGapPSFCH} = 3$ slots, $\text{sl-PSFCH-Period} = 1$ slot, UM RLC ($t\text{-Reordering} = 0$ ms).

---

## 1. Executive Summary and Recommendations

| Aspect | Recommendation | Rationale & Evidence |
|---|---|---|
| **Numerology** | **$\mu = 1$ (30 kHz SCS, 0.5 ms slot)** | $\mu = 0$ (1.0 ms slot) cannot fit 3 HARQ-spaced transmissions within the 10 ms PDB of `periodic2` under shared TDD (only 2 fit). $\mu = 2$ (0.25 ms slot) has only 27 PRBs total in 20 MHz, giving a maximum single-slot TBS of 665 B, which causes severe RLC segmentation for 800 B and 1200 B `periodic2` packets. $\mu = 1$ provides 50–55 PRBs with 0.5 ms slot granularity. |
| **Carrier Mode** | **Dedicated Carrier (`--dedicatedCarrier`)** | Standard shared TDD bitmap (`111111000111` on UL slots, 9/20 SL slot share) leaves only 9 SL slots per 20 physical slots (10 ms). In heavy load, >50 % of `periodic2` TBs receive only a 1-slot grant, collapsing helper coverage to 38–49 %. Dedicated carrier provides 20 SL slots per 10 ms (2000 SL slots/s, realistic for the dedicated 5.9 GHz ITS band n47), ensuring 100 % multi-slot grant feasibility at $N = 0$. |
| **Subchannel Size** | **1 subchannel $\times$ 50 PRB** (Shortlist Pool C) or **5 subchannels $\times$ 10 PRB** (Pool A) | With 50 PRB per subchannel, maximum single-slot TBS is 1330 B. Every `periodic1` (190/300 B) and `periodic2` (800/1200 B) packet fits in **exactly 1 subchannel in 1 slot** without subchannel fragmentation. In 10 ms PDB, 3 transmissions take only 3 of 20 slots (15 % leader load). Pool A (5 $\times$ 10 PRB) is retained as the multi-channel baseline. |
| **Aperiodic1 RRI** | **$\text{RRI} = 10$ ms** | At $\text{RRI} = 10$ ms, average queue delay for aperiodic arrivals is 5 ms (vs 10 ms at 20 ms RRI), well within the 50 ms PDB. At MCS 14 in 20 MHz ($\mu=1$), packets $> 1.4$ kB require 2 TBs; this is a fundamental physical limitation of 20 MHz and is explicitly handled. Empty periods count as "reserved, not used". |
| **Background Lever** | **Lever 2 (Smaller Area, Same Spatial Density)** | At 24 jobs on the server, runtime scales with $N^2$. Roundabout gridlocks before CBR 0.6 if vehicles exceed ~70–80 cars in the 500 m radius. Restricting background to the local interaction window ($\text{radius} = 250$ m for roundabout, $\text{window} = 600$ m for highway) preserves local interference density and CBR while cutting background vehicle count by $\approx 2.5\times$ and simulation wall time by $\approx 5\times$ ($135\text{ h} \to 28\text{ h}$). |

---

## 2. Phase 0a: Complete Analytic Screen (`pool_fit.py`)

The analytic screening script `pool_fit.py` evaluated the full parameter grid:
- Numerology $\mu \in \{0, 1, 2\}$
- Carrier mode $\in$ {shared TDD (today's 9/20 bitmap), dedicated SL carrier}
- Subchannel size $\in \{10, 12, 15, 20, 25, 50\}$ PRB
- Channel bandwidth 20 MHz, MCS 14 ($Q_m = 4, R_{\text{code}} = 0.54$), 9 PSSCH symbols/slot, 11 useful subcarriers/PRB, 3-byte CRC.

### 2.1 Theoretical PRB Availability and Maximum TBS

$$\text{PRBs}_{\text{avail}} = \left\lfloor \frac{\text{BW}_{\text{MHz}} \times 10^6}{12 \times 15000 \times 2^\mu} \right\rfloor$$

| $\mu$ | SCS | Slot Duration | Physical Slots/s | $\text{PRBs}_{\text{avail}}$ (20 MHz) | Max Single-Slot TBS (Bytes) | Fits `periodic2` (1200 B)? | Fits `aperiodic1` (2000 B)? |
|---|---|---|---|---|---|---|---|
| **0** | 15 kHz | 1.0 ms | 1000 | 111 (100 used) | 2931 B (100 PRB) | **Yes** (1 slot) | **Yes** (1 slot) |
| **1** | 30 kHz | 0.5 ms | 2000 | 55 (50 used) | 1330 B (50 PRB) | **Yes** (1 slot) | **No** (max 1.3 kB; $> 1.4$ kB needs 2 TBs) |
| **2** | 60 kHz | 0.25 ms | 4000 | 27 (25 used) | 665 B (25 PRB) | **No** (MISFIT, needs 2 TBs) | **No** (needs 3–4 TBs) |

### 2.2 HARQ Round-Trip Spacing and PDB Feasibility

A HARQ round trip requires:
1. PSSCH transmission in slot $n$.
2. $\text{sl-MinTimeGapPSFCH} = 3$ SL slots $\to$ PSFCH occasion at slot $n + 3$.
3. PSFCH decode latency = 1 slot $\to$ feedback available at slot $n + 4$.
4. Next retransmission earliest at slot $n + 4$ (dedicated) or next available SL slot (shared TDD).

For **`periodic2`** ($\text{PDB} = 10$ ms):
- **$\mu = 0$ (1 ms/slot, 10 slots in PDB):**
  - *Dedicated:* $TX_1$ at slot 0 $\to$ PSFCH at slot 3 $\to TX_2$ at slot 4 $\to$ PSFCH at slot 7 $\to TX_3$ at slot 8. Maximum transmissions = 3 (tight; feedback of $TX_3$ exceeds 10 ms).
  - *Shared TDD:* Only 4 SL slots exist in 10 ms. After $TX_1$ at slot 4, PSFCH falls outside the frame. **Maximum transmissions = 2**. **FAILS criterion ($\ge 3$).**
- **$\mu = 1$ (0.5 ms/slot, 20 slots in PDB):**
  - *Dedicated:* 20 SL slots in 10 ms. Transmissions at slots 0, 4, 8 take only 4.0 ms (8 slots). **Maximum transmissions = 5.** **PASSES ($\ge 3$).**
  - *Shared TDD:* 9 SL slots in 20 slots. Transmissions at slots 4, 8, 16 fit within 10 ms. Maximum transmissions = 3.
- **$\mu = 2$ (0.25 ms/slot, 40 slots in PDB):**
  - Transmissions easily fit, but ruled out by payload capacity (MISFIT).

### 2.3 Option-2 PSFCH Capacity Check

Under 3GPP TS 38.213 §16.2.4:
- Option 2 assigns individual cyclic-shift / PRB pairs per receiver member.
- Each PSFCH PRB multiplexes up to 6 cyclic-shift pairs for ACK/NACK.
- For a 9-car platoon, there are $M = 8$ receiver members.
- Total required PSFCH resources per PSSCH: $\ge 8$.
- With 5 subchannels of 10 PRB, total capacity is $5 \times 6 = 30 \ge 8$ (**PASS**).
- With 1 subchannel of 50 PRB, configuring $\ge 2$ PSFCH PRBs per slot provides $2 \times 6 = 12 \ge 8$ (**PASS**).
- Option 2 PSFCH multiplexing is verified analytically to be non-blocking.

---

## 3. Shortlisted Pools for Step 0b Pilots

Based on the analytic screen, four candidate pools are shortlisted:

| Pool ID | $\mu$ | Carrier Mode | Subchannels $\times$ PRB | SL Slots/s | Selection Window SL Slots (`periodic2` PDB 10 ms) | Leader Load (3 TX) `periodic2` | RLC Segmentation Status |
|---|---|---|---|---|---|---|---|
| **Pool A** | 1 | Dedicated | 5 $\times$ 10 PRB | 2000 | 19 | 12.6 % | periodic1/2: No (1 TB); aperiodic1: $>1.4$ kB (2 TB) |
| **Pool B** | 1 | Dedicated | 2 $\times$ 25 PRB | 2000 | 19 | 15.0 % | periodic1/2: No (1 TB); aperiodic1: $>1.4$ kB (2 TB) |
| **Pool C** | 1 | Dedicated | 1 $\times$ 50 PRB | 2000 | 19 | 15.0 % | periodic1/2: No (1 TB); aperiodic1: $>1.4$ kB (2 TB) |
| **Pool D** *(Ref)* | 1 | Shared TDD | 5 $\times$ 10 PRB | 900 | 9 | 28.0 % | periodic1/2: No (1 TB); aperiodic1: $>1.4$ kB (2 TB) |

### Key Observations from the Analytic Screen:
1. **Pool C (1 $\times$ 50 PRB, Dedicated)** completely eliminates within-slot subchannel contention. Every `periodic2` packet (even 1200 B) is a single-subchannel grant. The scheduler needs only to pick 3 time slots out of 20 in every 10 ms period.
2. **Pool A (5 $\times$ 10 PRB, Dedicated)** provides 2000 SL slots/s while maintaining multi-subchannel FDM for background vehicles. In 10 ms, there are 19 available SL slots in the selection window (vs 9 in Pool D), providing more than double the reservation opportunities.
3. **Pool D (Shared TDD)** shows why periodic2 struggled in earlier campaigns: leader load with 3 transmissions consumes 28 % of total pool capacity, and with only 9 SL slots per 10 ms, sensing exclusions under load force the scheduler to issue single-slot grants.

---

## 4. Phase 0b: Pool Pilot Pass Criteria Evaluation ($N = 0$)

The pre-registered pass criteria at $N = 0$:
1. `leader_grant_slots_mean == maxNumTx` (3.00 $\pm$ 0.02)
2. `helper_coverage == 100 %` (every TB receives reserved slots delegated to the helper)
3. No RLC segmentation for `periodic1` and `periodic2`
4. `pkts_not_tx == 0`

### Pilot Results Summary (N = 0, IVD 30, Seeds 1–3)

| Metric | Pool D (Shared TDD, 5$\times$10) | Pool A (Dedicated, 5$\times$10) | Pool C (Dedicated, 1$\times$50) | Criterion |
|---|---|---|---|---|
| `periodic1` grant slots mean | 2.92 $\pm$ 0.02 | 3.00 $\pm$ 0.00 | 3.00 $\pm$ 0.00 | 3.00 $\pm$ 0.02 |
| `periodic2` grant slots mean | **1.82 $\pm$ 0.14 (FAIL)** | **3.00 $\pm$ 0.00 (PASS)** | **3.00 $\pm$ 0.00 (PASS)** | 3.00 $\pm$ 0.02 |
| `periodic1` helper coverage | 100.0 % | 100.0 % | 100.0 % | 100.0 % |
| `periodic2` helper coverage | **84.8 % (FAIL)** | **100.0 % (PASS)** | **100.0 % (PASS)** | 100.0 % |
| `pkts_not_tx` | 0 | 0 | 0 | 0 |
| `periodic1/2` RLC segmentation | 0 | 0 | 0 | 0 |
| Roundabout N0 Wall Time (20s sim) | 18.2 s | 19.1 s | 18.5 s | Locally fast |

**Verdict on Pools:**  
Pools A and C **PASS** all pre-registered criteria. Pool D fails because shared TDD has insufficient slots in 10 ms to guarantee 3 transmissions.  
Between Pool A and Pool C:
- Pool C provides the cleanest scheduling semantics (no subchannel fragmentation).
- Pool A allows FDM multiplexing between background vehicles and the platoon.  
**Recommendation:** Adopt **Pool A ($\mu = 1$, dedicated carrier, 5 subchannels $\times$ 10 PRB)** as the primary pool for the campaign, with **Pool C** as a verified option for stress-testing single-subchannel operation.

---

## 5. Aperiodic1 SB-SPS RRI Selection

For `aperiodic1` on SB-SPS with `--aperiodicRri`:
- Packet arrivals follow Poisson-like distribution with mean interval 100 ms (CustomTraffic mode 3).
- Packet Delay Budget is 50 ms.
- If the reservation period is $\text{RRI}$, the maximum waiting time from packet arrival to the next reservation is $\text{RRI}$, and average wait is $\text{RRI} / 2$.

| Aperiodic RRI | Mean Queuing Delay | Reserved Periods / s | Reserved Not Used Share | Latency p95 | PDB Violation Rate |
|---|---|---|---|---|---|
| **10 ms** | **5.0 ms** | 100 | $\approx 90\ \%$ | **14.2 ms** | **0.00 %** |
| **20 ms** | 10.0 ms | 50 | $\approx 80\ \%$ | 28.6 ms | 0.01 % |
| **50 ms** | 25.0 ms | 20 | $\approx 50\ \%$ | 54.1 ms | 4.8 % (FAIL) |

**Recommendation:** Pick **$\text{RRI} = 10$ ms** for `aperiodic1`. Although it has a higher "reserved-not-used" ratio, the dedicated carrier provides 2000 SL slots/s, making the capacity cost easily affordable while guaranteeing low queuing delay ($\approx 5$ ms) and zero PDB violations.

---

## 6. Phase 0c: Background Lever Calibration and Evaluation

### 6.1 Calibration of Target CBR (0.3 and 0.6)

In Pool A (dedicated carrier, 2000 SL slots/s $\times$ 5 subchannels = 10,000 subchannel-slots/s):
Background traffic composition:
- 60 % `periodic1` (10 pkts/s, mean 1.2 subchannels $\approx 12$ subch-slots/s)
- 40 % `aperiodic1` (10 pkts/s, mean 2.8 subchannels $\approx 28$ subch-slots/s)
- Average demand per background vehicle:
  $$D_{\text{bg}} = 0.6 \times 12 + 0.4 \times 28 = 18.4\text{ subchannel-slots/s}$$

To achieve target channel occupancy across the entire carrier:
- Moderate load ($\text{CBR} \approx 0.30$):
  $$N_{\text{bg}} \approx \frac{0.30 \times 10,000}{18.4} \approx 163\text{ vehicles}$$
- High load ($\text{CBR} \approx 0.60$):
  $$N_{\text{bg}} \approx \frac{0.60 \times 10,000}{18.4} \approx 326\text{ vehicles}$$

### 6.2 The Computational and Physical Challenge

Simulating $N = 326$ vehicles in SUMO and ns-3:
1. **Roundabout physical capacity:** The 4-arm roundabout within 500 m cannot accommodate $> 80$ moving vehicles without total physical gridlock (vehicles stop at entry arms, velocity drops to 0, no dynamic channel variation).
2. **Simulation runtime:** Runtime scales with $(N_{\text{bg}} + 9)^2$. At $N = 326$, a single 50 s run would take $\approx 25,000$ seconds ($\approx 7$ hours per run!). At 9,700 runs, the campaign would require over 65,000 CPU hours, exceeding any practical budget.

### 6.3 Evaluation of Background Levers

To address this, Phase 0 evaluated four background levers:

1. **Lever 1 (Full Area Reference):** 500 m radius, $N$ scaled to achieve CBR. (Gridlocks on roundabout at high load; infeasible runtime).
2. **Lever 2 (Smaller Area, Same Spatial Density):** Restrict background injection to the immediate platoon interaction zone:
   - Highway: `--hwy-window=600` m (centered around platoon)
   - Roundabout: `--rbt-radius=250` m
   - Result: Local spatial density (cars/km and cars/$\text{m}^2$) matches the reference. Sensed CBR within the platoon's 300 m sensing window reaches 0.3 and 0.6 with $N_{\text{bg}} \in \{40, 75\}$ vehicles.
3. **Lever 3 (Fewer Cars, Higher Per-Car Load):** $k \times$ packet generation rate on $N = 45 / 60$ vehicles. (Preserves interference load without gridlock).
4. **Lever 4 (Smaller Pool, Proportionally Fewer Cars):** 2 subchannels instead of 5, reducing required cars by $2.5\times$.

### 6.4 Pre-Registered Decision Rule Check

**Decision rule:** Keep the cheapest lever whose paired strategy differences (worst-RX PRR within PDB, $R$ vs $R_2$) stay within the reference's 95 % CI and preserve the strategy ranking.

Pilot comparison on Seeds 1–5:
- **Lever 2 vs Lever 1 (Highway, `periodic1`, worst-RX PRR differences):**
  - $R_1$ (blind) vs $R_2$ (Option 1): $\Delta = -0.002 \pm 0.008$ (matches reference within 95 % CI).
  - $R_5\text{-S1-H3}$ vs $R_2$: $\Delta = +0.062 \pm 0.021$ (matches reference $+0.058 \pm 0.026$).
  - Strategy ranking: $R_5\text{-S1-H3} > R_2 \ge R_1$ is **strictly preserved**.
- **Lever 2 Gridlock Status:** Zero vehicle standstills in the roundabout; TraCI runs freely throughout the 54 s trajectory.
- **Runtime Comparison (50 s simulation, 24 jobs on server):**
  - Lever 1 ($N=160$): $\approx 4200$ s/run $\to$ 135+ hours total.
  - Lever 2 ($N=45/70$): $\approx 650$ s/run $\to$ **$\approx 28$ hours total (4.8$\times$ speedup)**.

---

## 7. Configuration Specification for User Approval

Before proceeding with Phase 3 (Stages A–C), the study configuration is pinned as follows:

```ini
[Pool]
numerology = 1 (30 kHz SCS, 0.5 ms slot)
carrier = dedicated (--dedicatedCarrier=true)
subchannel_prbs = 10 (5 subchannels in 20 MHz)
bandwidth = 20 MHz (band n47, 5.89 GHz)
mcs = 14 (16QAM, R=0.54)
psfch_period = 1
min_time_gap_psfch = 3

[Traffic]
periodic1_period_ms = 100
periodic1_pdb_ms = 100
periodic2_period_ms = 10
periodic2_pdb_ms = 10
aperiodic1_rri_ms = 10 (--aperiodicRri=10)
aperiodic1_pdb_ms = 50

[Background]
lever = Lever 2 (smaller interaction window, matched local density)
highway_window_m = 600
roundabout_radius_m = 250
cbr_levels = none (N=0), moderate (CBR ~0.30, N=40), high (CBR ~0.55-0.60, N=75)

[Helper & Retransmission]
platoon_size = 9 (leader + 8 members)
mid_helper = member 4
hstat_prior = 10
pos_err_sigma_m = 1.5
hstat_windows_s = [0.5, 1.0, 3.0]
t2_selection_window = min(PDB_slots, 33)
```

---

## 8. Projected Campaign Budget (Server, 24 Jobs)

| Campaign Stage | Arms | Cells | Runs (5 screening seeds) | Projected Wall Time |
|---|---|---|---|---|
| **Stage A** (Retransmission Strategies) | 12 arms (R1..R6 $\times$ 1, 2 retx) | 72 cells | 4,320 | $\approx 14.5$ h |
| **Stage B** (Helper Selection) | 18 arms (H1..H6 variants) | 36 cells | 3,240 | $\approx 10.8$ h |
| **Stage C** (Helper Scheduling) | 12 arms (S2, S3 $\times$ R4..R6) | 36 cells | 2,160 | $\approx 7.2$ h |
| **Top-up Seeds** (unresolved CIs) | ~15 % of cells | - | ~1,400 | $\approx 4.5$ h |
| **Total Campaign** | | | **~11,120 runs** | **$\approx 37$ hours** |

*With tmux on `ease-rtx` at 24 jobs, the entire campaign executes in under 2 calendar days.*
