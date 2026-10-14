#!/usr/bin/env python3
"""pool_fit.py — Analytic screen of sidelink resource pools for platoon HARQ.

Evaluates combinations of:
  - numerology μ ∈ {0, 1, 2}
  - carrier mode ∈ {shared_tdd, dedicated}
  - subchannel size ∈ {10, 12, 15, 20, 25, 50} PRB
for 20 MHz channel bandwidth at MCS 14 under:
  - periodic1 (100 ms, 190/300 B, PDB 100 ms)
  - periodic2 (10 ms, 800/1200 B, PDB 10 ms)
  - aperiodic1 (mean 100 ms, 200..2000 B, PDB 50 ms)
"""

import sys
import math
import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# Physical and 3GPP specifications
# ---------------------------------------------------------------------------
BW_MHZ = 20.0
MCS = 14
QM = 4           # 16QAM
RCODE = 0.54     # Table 5.1.3.1-1
SYMBOLS_PER_SLOT = 9  # PSSCH symbols in 5G-LENA SL slot
USEFUL_SC = 11        # 12 subcarriers - 1 DM-RS reference subcarrier
CRC_LEN = 3           # 24-bit CRC = 3 bytes
OVERHEAD_BYTES = 35   # 28 B UDP/IP + 2 B RLC UM header + 5 B SCI-2A

PSFCH_PERIOD = 1       # 1 slot
MIN_TIME_GAP_PSFCH = 3 # 3 slots
T1_SLOTS = 2
T2_MS = 33             # T2 fixed to 33 slots or min(T2, PDB)

# Shared TDD bitmap: 10 subframes per frame.
# At mu=1 (20 slots/frame): DL(3), F(1), UL(6) subframes.
# Bitmap on UL slots: 9 of 20 slots are SL (0.45).
SL_SLOT_FRAC_TDD = 9.0 / 20.0
SL_SLOT_FRAC_DEDICATED = 1.0

# Traffic profiles
# (name, period_ms, pdb_ms, sizes_list, weights_list, pkts_per_s)
TRAFFIC_CLASSES = [
    {
        "name": "periodic1",
        "period_ms": 100.0,
        "pdb_ms": 100.0,
        "sizes": [190, 300],
        "weights": [0.8, 0.2],
        "rate": 10.0,
    },
    {
        "name": "periodic2",
        "period_ms": 10.0,
        "pdb_ms": 10.0,
        "sizes": [800, 1200],
        "weights": [0.8, 0.2],
        "rate": 100.0,
    },
    {
        "name": "aperiodic1",
        "period_ms": 100.0, # mean interval
        "pdb_ms": 50.0,
        "sizes": list(range(200, 2001, 200)),
        "weights": [1.0 / 10.0] * 10,
        "rate": 10.0,
    },
]

def calc_avail_prbs(mu, bw_mhz=20.0):
    scs_hz = 15000.0 * (1 << mu)
    full_band_100khz = round(bw_mhz * 10.0)
    avail = math.floor((full_band_100khz * 100e3) / (12.0 * scs_hz))
    return int(avail)

def calc_tb_size(n_prb, mcs=MCS):
    if n_prb <= 0:
        return 0
    nprb_eff = n_prb * SYMBOLS_PER_SLOT
    rsc_element = USEFUL_SC * nprb_eff
    spectral_eff = rsc_element * QM * RCODE
    payload_size = math.floor(spectral_eff / 8.0)
    
    tb_size = payload_size - CRC_LEN
    if tb_size <= 0:
        return 0
    # Code block segmentation
    if tb_size * 8 <= 3824:
        cb_size = 480
    else:
        cb_size = 1056
    if tb_size > cb_size:
        C = math.ceil(tb_size / cb_size)
        tb_size = payload_size - C * CRC_LEN
    return max(0, tb_size)

def subchannels_for_size(pkt_size, subch_prb, n_subch):
    """Determine minimum subchannels needed to carry pkt_size + OVERHEAD_BYTES."""
    req_tb = pkt_size + OVERHEAD_BYTES
    for s in range(1, n_subch + 1):
        if calc_tb_size(s * subch_prb) >= req_tb:
            return s
    return None # Does not fit in 1 slot!

def max_harq_transmissions(carrier, mu, pdb_ms):
    """Compute maximum HARQ-spaced transmissions within PDB.
    
    Initial TX at t=0.
    PSFCH occasion after MIN_TIME_GAP_PSFCH = 3 SL slots.
    Feedback available 1 slot later (slot 4).
    Retransmission earliest at slot 4 (dedicated).
    Under shared TDD, accounts for non-SL slots.
    """
    slot_ms = 1.0 / (1 << mu)
    pdb_slots = int(math.floor(pdb_ms / slot_ms))
    
    if carrier == "dedicated":
        # Slot spacing between HARQ attempts:
        # TX at slot k -> PSFCH at k + 3 -> decode at k + 4 -> Retx at k + 4.
        gap = MIN_TIME_GAP_PSFCH + 1 # 4 slots
        # Attempts fit if (tx_count - 1) * gap < pdb_slots
        max_tx = 1 + (pdb_slots - 1) // gap
        return max_tx
    else:
        # Shared TDD: TDD period 10 ms = 10 * 2^mu slots.
        # We simulate slot by slot according to TDD bitmap.
        # TDD pattern: 3 DL, 1 F, 6 UL subframes.
        # In each subframe: 2^mu slots.
        slots_per_sf = 1 << mu
        # Subframe 0..2: DL, 3: F, 4..9: UL
        # In UL subframes: bitmap is 111111000111 on UL slots.
        # Let's build explicit SL slot flags for 10 ms (1 frame)
        sl_flags = []
        # Total slots in 10 ms = 10 * 2^mu
        # In 5G-LENA nr_sl_stack:
        # slBitmap = {1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1}
        # mapped to the UL slots.
        # For mu=1: 20 slots/frame. UL slots are subframes 4..9 -> 6 subframes = 12 slots!
        # The 12 bitmap bits match exactly the 12 UL slots!
        # Bit 1 = SL slot.
        # Bitmap: 1,1,1,1,1,1, 0,0,0, 1,1,1 -> 9 SL slots of 12 UL slots (and 20 total slots).
        if mu == 1:
            bitmap_12 = [1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1]
            for sf in range(10):
                if sf < 4:
                    sl_flags.extend([0, 0])
                else:
                    ul_idx = (sf - 4) * 2
                    sl_flags.append(bitmap_12[ul_idx])
                    sl_flags.append(bitmap_12[ul_idx + 1])
        elif mu == 0:
            # 10 slots: 4 DL/F, 6 UL. Bitmap of 6 UL slots: 1,1,1, 0, 1,1 -> ~4 SL slots.
            # 5G-LENA repeats bitmap modulo size.
            bitmap = [1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1]
            for s in range(10):
                if s < 4:
                    sl_flags.append(0)
                else:
                    sl_flags.append(bitmap[(s - 4) % len(bitmap)])
        else: # mu == 2: 40 slots
            bitmap = [1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1]
            for sf in range(10):
                for sub in range(4):
                    if sf < 4:
                        sl_flags.append(0)
                    else:
                        ul_slot = (sf - 4) * 4 + sub
                        sl_flags.append(bitmap[ul_slot % len(bitmap)])
        
        # Extend to cover PDB
        total_slots = pdb_slots
        while len(sl_flags) < total_slots + 50:
            sl_flags.extend(sl_flags[:10 * (1 << mu)])
            
        # Simulate HARQ attempts starting at first SL slot
        # Find first SL slot
        cur_slot = 0
        while cur_slot < total_slots and not sl_flags[cur_slot]:
            cur_slot += 1
        if cur_slot >= total_slots:
            return 0
            
        tx_count = 0
        while cur_slot < total_slots:
            tx_count += 1
            # Next PSFCH is at least MIN_TIME_GAP_PSFCH SL slots later
            sl_seen = 0
            psfch_slot = cur_slot + 1
            while psfch_slot < len(sl_flags):
                if sl_flags[psfch_slot]:
                    sl_seen += 1
                    if sl_seen >= MIN_TIME_GAP_PSFCH:
                        break
                psfch_slot += 1
            # Feedback decoded 1 slot later
            next_cand = psfch_slot + 1
            # Next retx must be an SL slot
            while next_cand < len(sl_flags) and not sl_flags[next_cand]:
                next_cand += 1
            cur_slot = next_cand
            
        return tx_count

def run_analytic_screen():
    rows = []
    
    mu_list = [0, 1, 2]
    carrier_list = ["shared_tdd", "dedicated"]
    subch_sizes = [10, 12, 15, 20, 25, 50]
    
    for mu in mu_list:
        avail_prbs = calc_avail_prbs(mu, BW_MHZ)
        slot_ms = 1.0 / (1 << mu)
        phys_slots_per_s = 1000 * (1 << mu)
        
        for carrier in carrier_list:
            sl_frac = SL_SLOT_FRAC_DEDICATED if carrier == "dedicated" else SL_SLOT_FRAC_TDD
            sl_slots_per_s = phys_slots_per_s * sl_frac
            
            for subch_prb in subch_sizes:
                n_subch = avail_prbs // subch_prb
                if n_subch < 1:
                    continue # Cannot fit even 1 subchannel
                
                pool_prbs_used = n_subch * subch_prb
                pool_cap_subch_slots = sl_slots_per_s * n_subch
                
                # Option-2 PSFCH capacity check:
                # 3GPP TS 38.213: each subchannel provides 6 cyclic shifts / PRB.
                # Total cyclic shifts = n_subch * 6 (or PRB-based).
                # ≥ 8 members per PSSCH requires total CS ≥ 8.
                opt2_psfch_capacity = n_subch * 6
                opt2_ok = (opt2_psfch_capacity >= 8)
                
                for tc in TRAFFIC_CLASSES:
                    tc_name = tc["name"]
                    pdb_ms = tc["pdb_ms"]
                    pdb_slots = int(math.floor(pdb_ms / slot_ms))
                    
                    # SL slots in [T1, min(T2, PDB)]
                    t2_eff_slots = min(T2_MS, pdb_slots)
                    win_phys_slots = max(0, t2_eff_slots - T1_SLOTS + 1)
                    win_sl_slots = int(round(win_phys_slots * sl_frac))
                    
                    # HARQ-spaced transmissions
                    max_tx = max_harq_transmissions(carrier, mu, pdb_ms)
                    harq_ok = (max_tx >= 3)
                    
                    # Check packet fit and subchannels per packet
                    subch_reqs = []
                    all_fit = True
                    for pkt_sz in tc["sizes"]:
                        req = subchannels_for_size(pkt_sz, subch_prb, n_subch)
                        if req is None:
                            all_fit = False
                            subch_reqs.append(n_subch + 1) # sentinel for misfit
                        else:
                            subch_reqs.append(req)
                    
                    # Weighted mean subchannels
                    mean_subch = sum(w * s for w, s in zip(tc["weights"], subch_reqs) if s <= n_subch)
                    if not all_fit:
                        mean_subch = float("nan")
                    
                    # Maximum single-slot TB size in this pool
                    max_slot_tbs = calc_tb_size(n_subch * subch_prb)
                    
                    # Leader own load (at maxNumTx = 3)
                    # TBs/s * subch * maxNumTx / pool_cap_subch_slots
                    if all_fit:
                        leader_load_1tx = (tc["rate"] * mean_subch) / pool_cap_subch_slots
                        leader_load_3tx = (tc["rate"] * mean_subch * 3) / pool_cap_subch_slots
                    else:
                        leader_load_1tx = float("nan")
                        leader_load_3tx = float("nan")
                    
                    # Background vehicles needed for CBR 0.3 and 0.6
                    # Background cars generate 60% periodic1 (10 pkts/s) and 40% aperiodic1 (10 pkts/s)
                    # We compute mean subch for background traffic
                    bg_p1_subch = [subchannels_for_size(s, subch_prb, n_subch) for s in [190, 300]]
                    bg_ap1_subch = [subchannels_for_size(s, subch_prb, n_subch) for s in range(200, 2001, 200)]
                    
                    # If BG packets fit:
                    bg_fit = (all(s is not None for s in bg_p1_subch) and any(s is not None for s in bg_ap1_subch))
                    if bg_fit:
                        mean_bg_p1 = 0.8 * bg_p1_subch[0] + 0.2 * (bg_p1_subch[1] if bg_p1_subch[1] else n_subch)
                        valid_ap1 = [s for s in bg_ap1_subch if s is not None]
                        mean_bg_ap1 = sum(valid_ap1) / len(valid_ap1)
                        bg_demand_per_car = 10.0 * (0.6 * mean_bg_p1 + 0.4 * mean_bg_ap1)
                        
                        nbg_cbr30 = (0.30 * pool_cap_subch_slots) / bg_demand_per_car
                        nbg_cbr60 = (0.60 * pool_cap_subch_slots) / bg_demand_per_car
                        # Estimated runtime for 50s run: ~20s at N=9, scaling as (N/9)^2
                        n_tot30 = nbg_cbr30 + 9
                        n_tot60 = nbg_cbr60 + 9
                        wall_cbr30 = 20.0 * (n_tot30 / 9.0)**2
                        wall_cbr60 = 20.0 * (n_tot60 / 9.0)**2
                    else:
                        nbg_cbr30 = float("nan")
                        nbg_cbr60 = float("nan")
                        wall_cbr30 = float("nan")
                        wall_cbr60 = float("nan")
                    
                    rows.append({
                        "mu": mu,
                        "carrier": carrier,
                        "subch_prb": subch_prb,
                        "avail_prbs": avail_prbs,
                        "n_subch": n_subch,
                        "pool_prbs": pool_prbs_used,
                        "sl_slots_per_s": sl_slots_per_s,
                        "traffic": tc_name,
                        "pdb_ms": pdb_ms,
                        "pdb_slots": pdb_slots,
                        "win_sl_slots": win_sl_slots,
                        "max_harq_tx": max_tx,
                        "harq_ok": harq_ok,
                        "max_slot_tbs": max_slot_tbs,
                        "all_packets_fit": all_fit,
                        "subch_reqs": str(subch_reqs),
                        "mean_subch": round(mean_subch, 2) if not math.isnan(mean_subch) else "MISFIT",
                        "leader_load_1tx": round(leader_load_1tx, 4) if not math.isnan(leader_load_1tx) else "N/A",
                        "leader_load_3tx": round(leader_load_3tx, 4) if not math.isnan(leader_load_3tx) else "N/A",
                        "opt2_psfch_cap": opt2_psfch_capacity,
                        "opt2_ok": opt2_ok,
                        "nbg_cbr0.3": round(nbg_cbr30, 1) if not math.isnan(nbg_cbr30) else "N/A",
                        "nbg_cbr0.6": round(nbg_cbr60, 1) if not math.isnan(nbg_cbr60) else "N/A",
                        "est_wall_cbr0.3_s": round(wall_cbr30, 0) if not math.isnan(wall_cbr30) else "N/A",
                        "est_wall_cbr0.6_s": round(wall_cbr60, 0) if not math.isnan(wall_cbr60) else "N/A",
                    })

    df = pd.DataFrame(rows)
    return df

def main():
    df = run_analytic_screen()
    out_csv = "pool_fit_results.csv"
    df.to_csv(out_csv, index=False)
    print(f"Saved complete analytic screen to {out_csv} ({len(df)} configurations)")
    
    # Print summary highlights
    print("\n=== SUMMARY HIGHLIGHTS ===")
    
    # 1. Check which pools pass periodic1 and periodic2 with max_harq_tx >= 3 and no segmentation
    print("\n--- Pools fitting periodic1 & periodic2 without segmentation and max_harq_tx >= 3 ---")
    p1_p2 = df[(df["traffic"].isin(["periodic1", "periodic2"])) & (df["harq_ok"] == True) & (df["all_packets_fit"] == True)]
    # Group by config: must pass both
    valid_configs = []
    for (mu, carrier, subch_prb), grp in p1_p2.groupby(["mu", "carrier", "subch_prb"]):
        if len(grp) == 2:
            n_subch = grp["n_subch"].iloc[0]
            pool_prbs = grp["pool_prbs"].iloc[0]
            valid_configs.append((mu, carrier, subch_prb, n_subch, pool_prbs))
            
    print(f"{'μ':<3} {'carrier':<12} {'subch_prb':<10} {'n_subch':<8} {'pool_prb':<10}")
    print("-" * 50)
    for c in valid_configs:
        print(f"{c[0]:<3} {c[1]:<12} {c[2]:<10} {c[3]:<8} {c[4]:<10}")
        
    print("\n--- Aperiodic1 fit across pools (200..2000 B) ---")
    ap1 = df[df["traffic"] == "aperiodic1"]
    for (mu, carrier), grp in ap1.groupby(["mu", "carrier"]):
        max_tbs = grp["max_slot_tbs"].max()
        all_fit_any = grp["all_packets_fit"].any()
        print(f"μ={mu} carrier={carrier:<12}: max single-slot TBS = {max_tbs} B, fits 2000 B? {all_fit_any}")

    print("\nDone.")

if __name__ == "__main__":
    main()
