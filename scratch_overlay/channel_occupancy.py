#!/usr/bin/env python3
"""
channel_occupancy.py
====================
Analyse NR-SL channel occupancy from a single simulation SQLite DB.

Outputs a dict (or pretty-printed table) with a three-layer breakdown:

  Layer 1 — Slot-level (time axis)
    psfch_slots         Reserved for HARQ feedback (one in every psfchPeriod slots).
    data_slots          PSSCH-eligible slots that had ≥1 TX grant (data or retx).
    empty_slots         PSSCH-eligible slots with no transmission.

  Layer 2 — Subchannel-slot-level (time × frequency)
    data_subch_slots    ΣΣ subchannels × slots used by psschTxUeMac grants.
    empty_subch_slots   PSSCH-eligible (slot,subchannel) pairs that were never used.

  Layer 3 — HARQ feedback (PRB-level, within PSFCH slots)
    harq_feedback_sent  ACK+NACK events recorded (each uses 1 PRB in 1 PSFCH slot).
    psfch_prb_capacity  PSFCH_slots × bwPRBs (upper bound on feedback volume).

Resource pool model
-------------------
NR-SL (numerology μ=1 inferred when max_slot=1 in psschTxUeMac):
  slot_duration = 0.5 ms  (μ=1, 2 slots/subframe)  or
                  1.0 ms  (μ=0, 1 slot/subframe)

Within the ACTIVE window [warmup_s, warmup_s+sim_time_s]:
  total_slots  = active_window_ms / slot_duration_ms

  PSFCH slots  = total_slots // psfch_period
                 (one slot in every psfch_period pool slots reserved for
                  HARQ feedback; those slots carry no PSSCH data)

  PSSCH-eligible slots = total_slots - psfch_slots

  n_subchannels = totSbCh read from pscchTxUeMac (= bwPRBs / sbChSize)

  Total PSSCH subchannel-slots = pssch_eligible_slots × n_subchannels

  For each grant in psschTxUeMac:
    subchannels_used = rbLen / sbChSize   (e.g., 100/10=10 or 10/10=1)

  data_subch_slots = Σ subchannels_used   over all grants
"""

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Core analysis function
# ─────────────────────────────────────────────────────────────────────────────

def analyze_channel_occupancy(
    db_path,
    sim_time_s: float = 60.0,
    warmup_s: float = 2.0,
    psfch_period: int = 4,
    bw_prbs=None,
    run_csv=None,
) -> dict:
    """
    Analyse channel occupancy for one simulation DB.

    Parameters
    ----------
    db_path      : Path to the .db file.
    sim_time_s   : Active simulation duration in seconds (excluding warmup).
    warmup_s     : Warm-up period discarded by V2xKpi (seconds).
    psfch_period : HARQ feedback period (# of pool slots between PSFCH slots).
                   Typically 4 (every 4th slot in pool is PSFCH).
    bw_prbs      : Total bandwidth in PRBs (for PSFCH PRB capacity calculation).
    run_csv      : If provided, override sim_time_s / warmup_s / psfch_period /
                   bw_prbs from the per-run CSV (columns: simTime, warmup,
                   psfchPeriod, bwPRBs).

    Returns
    -------
    dict with all computed metrics (see module docstring).
    """
    # ── Override params from run CSV ──────────────────────────────────────────
    if run_csv is not None:
        run_csv = Path(run_csv)
        if run_csv.exists():
            rc = pd.read_csv(run_csv).iloc[0]
            sim_time_s   = float(rc.get("simTime",   sim_time_s))
            warmup_s     = float(rc.get("warmup",    warmup_s))
            psfch_period = int(rc.get("psfchPeriod", psfch_period))
            if bw_prbs is None:
                bw_prbs = int(rc.get("bwPRBs", bw_prbs or 10))

    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))

    # ── Detect slot duration from psschTxUeMac ────────────────────────────────
    max_slot = pd.read_sql_query("SELECT MAX(slot) AS ms FROM psschTxUeMac", conn).iloc[0]["ms"]
    slots_per_subframe = int(max_slot) + 1          # 1 → μ=0 (1ms), 2 → μ=1 (0.5ms)
    slot_duration_ms   = 1.0 / slots_per_subframe   # 0.5 ms for μ=1

    # ── Total slots in active measurement window ──────────────────────────────
    active_window_ms = sim_time_s * 1000.0
    total_slots      = int(round(active_window_ms / slot_duration_ms))

    # ── Layer 1: Slot-level breakdown ─────────────────────────────────────────
    psfch_slots         = total_slots // psfch_period
    pssch_eligible_slots = total_slots - psfch_slots

    # Distinct (frame, subFrame, slot) tuples that had at least one TX grant.
    distinct_used = pd.read_sql_query(
        "SELECT COUNT(DISTINCT frame||'_'||subFrame||'_'||slot) AS n "
        "FROM psschTxUeMac",
        conn,
    ).iloc[0]["n"]
    data_slots  = int(distinct_used)
    empty_slots = pssch_eligible_slots - data_slots

    # ── Layer 2: Subchannel-slot occupancy ────────────────────────────────────
    # n_subchannels from pscchTxUeMac.totSbCh (stored per grant)
    n_subch_row = pd.read_sql_query(
        "SELECT MAX(totSbCh) AS n FROM pscchTxUeMac", conn
    )
    n_subchannels = int(n_subch_row.iloc[0]["n"]) if not n_subch_row.empty else 1

    pssch_eligible_subch = pssch_eligible_slots * n_subchannels

    # Each PSSCH grant occupies rbLen/sbChSize subchannels in one slot.
    grants = pd.read_sql_query(
        "SELECT rbLen, sbChSize FROM psschTxUeMac", conn
    )
    grants["n_subch"] = (grants["rbLen"] / grants["sbChSize"].clip(lower=1)).astype(int)
    data_subch_slots  = int(grants["n_subch"].sum())
    empty_subch_slots = pssch_eligible_subch - data_subch_slots

    # ── Layer 3: HARQ feedback ────────────────────────────────────────────────
    fb = pd.read_sql_query(
        "SELECT status, COUNT(*) AS n FROM slHarqFeedback GROUP BY status", conn
    )
    fb_dict       = dict(zip(fb["status"], fb["n"]))
    ack_count     = int(fb_dict.get("ACK", 0))
    nack_count    = int(fb_dict.get("NACK", 0))
    harq_fb_total = ack_count + nack_count

    bw_prbs = bw_prbs or n_subchannels * 10   # fallback: assume sbChSize=10
    psfch_prb_capacity = psfch_slots * bw_prbs  # upper bound on # feedback PRB-slots

    # ── New / initial TX vs retransmissions ──────────────────────────────────
    ndi_counts = pd.read_sql_query(
        "SELECT ndi, COUNT(*) AS n FROM psschTxUeMac GROUP BY ndi", conn
    )
    ndi_dict      = dict(zip(ndi_counts["ndi"], ndi_counts["n"]))
    initial_grants = int(ndi_dict.get(1, 0))
    retx_grants    = int(ndi_dict.get(0, 0))

    conn.close()

    # ── Derived fractions ─────────────────────────────────────────────────────
    def safe_frac(num, den):
        return num / den if den > 0 else float("nan")

    return dict(
        # metadata
        db_path             = str(db_path),
        slot_duration_ms    = slot_duration_ms,
        slots_per_subframe  = slots_per_subframe,
        n_subchannels       = n_subchannels,
        bw_prbs             = bw_prbs,
        # ─ Layer 1: slot counts ────────────────────────────────────────────
        total_slots          = total_slots,
        psfch_slots          = psfch_slots,
        pssch_eligible_slots = pssch_eligible_slots,
        data_slots           = data_slots,
        empty_slots          = empty_slots,
        initial_grants       = initial_grants,
        retx_grants          = retx_grants,
        # ─ Layer 2: subchannel-slot counts ────────────────────────────────
        pssch_eligible_subch = pssch_eligible_subch,
        data_subch_slots     = data_subch_slots,
        empty_subch_slots    = empty_subch_slots,
        # ─ Layer 3: HARQ feedback ─────────────────────────────────────────
        ack_count            = ack_count,
        nack_count           = nack_count,
        harq_fb_total        = harq_fb_total,
        psfch_prb_capacity   = psfch_prb_capacity,
        # ─ Fractions of total_slots ────────────────────────────────────────
        frac_psfch           = safe_frac(psfch_slots,          total_slots),
        frac_data_slots      = safe_frac(data_slots,           total_slots),
        frac_empty_slots     = safe_frac(empty_slots,          total_slots),
        # ─ Fractions of pssch-eligible subchannel-slots ───────────────────
        frac_data_subch      = safe_frac(data_subch_slots,     pssch_eligible_subch),
        frac_empty_subch     = safe_frac(empty_subch_slots,    pssch_eligible_subch),
        # ─ HARQ feedback utilisation ──────────────────────────────────────
        frac_psfch_used      = safe_frac(harq_fb_total,        psfch_prb_capacity),
        frac_nack            = safe_frac(nack_count,           harq_fb_total),
        retx_ratio           = safe_frac(retx_grants,          initial_grants + retx_grants),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pretty-print helper
# ─────────────────────────────────────────────────────────────────────────────

def print_occupancy(r: dict):
    print(f"\n{'─'*60}")
    print(f"  DB : {Path(r['db_path']).name}")
    print(f"  Slot duration : {r['slot_duration_ms']} ms  "
          f"(μ={'1' if r['slot_duration_ms']==0.5 else '0'})")
    print(f"  Subchannels   : {r['n_subchannels']}  |  bwPRBs : {r['bw_prbs']}")
    print(f"{'─'*60}")
    T = r['total_slots']
    print(f"\n  ── Layer 1: Slot-level (of {T:,} total slots) ──")
    print(f"    PSFCH reserved  : {r['psfch_slots']:>8,}  ({r['frac_psfch']*100:5.1f} %)")
    print(f"    Data TX         : {r['data_slots']:>8,}  ({r['frac_data_slots']*100:5.1f} %)")
    print(f"      of which retx : {r['retx_grants']:>8,}  (retx ratio {r['retx_ratio']*100:.1f} %)")
    print(f"    Empty (idle)    : {r['empty_slots']:>8,}  ({r['frac_empty_slots']*100:5.1f} %)")

    E = r['pssch_eligible_subch']
    print(f"\n  ── Layer 2: Subchannel×slot (of {E:,} PSSCH-eligible) ──")
    print(f"    Data used       : {r['data_subch_slots']:>10,}  ({r['frac_data_subch']*100:5.1f} %)")
    print(f"    Empty           : {r['empty_subch_slots']:>10,}  ({r['frac_empty_subch']*100:5.1f} %)")

    P = r['psfch_prb_capacity']
    print(f"\n  ── Layer 3: HARQ feedback (capacity {P:,} PRB-slots) ──")
    print(f"    ACK             : {r['ack_count']:>8,}")
    print(f"    NACK            : {r['nack_count']:>8,}")
    print(f"    Total FB sent   : {r['harq_fb_total']:>8,}  ({r['frac_psfch_used']*100:5.2f} % of PSFCH cap)")
    print(f"    NACK rate       : {r['frac_nack']*100:>8.1f} %")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Experiment wrapper
# ─────────────────────────────────────────────────────────────────────────────

def analyze_experiment(
    results_dir,
    db_glob: str = "**/*_seed*.db",
    default_sim_time_s: float = 60.0,
    default_warmup_s: float = 2.0,
    default_psfch_period: int = 4,
    default_bw_prbs: int = 10,
    run_csv_suffix: str = "_run.csv",
) -> pd.DataFrame:
    """
    Loop over all *_seed*.db files under results_dir.
    For each DB, look for a sibling <stem>_run.csv to auto-load simulation params.
    Returns a tidy DataFrame with one row per DB.

    Parameters
    ----------
    results_dir       : Root of the experiment results tree.
    db_glob           : Glob pattern for DB files (default captures subdirs).
    default_*         : Fallback values when no run CSV is found.
    run_csv_suffix    : Suffix appended to the DB stem to find the run CSV
                        (e.g., "_run.csv" → periodic3_seed1_run.csv).
    """
    results_dir = Path(results_dir)
    # rglob already handles recursion; strip any leading "**/" from pattern
    pattern = db_glob
    if pattern.startswith("**/"):
        pattern = pattern[3:]
    dbs = sorted(results_dir.rglob(pattern))
    if not dbs:
        raise FileNotFoundError(f"No DB files found under {results_dir}")

    rows = []
    for db in dbs:
        run_csv = db.with_name(db.stem + run_csv_suffix)
        if not run_csv.exists():
            run_csv = None
        try:
            r = analyze_channel_occupancy(
                db,
                sim_time_s    = default_sim_time_s,
                warmup_s      = default_warmup_s,
                psfch_period  = default_psfch_period,
                bw_prbs       = default_bw_prbs,
                run_csv       = run_csv,
            )
            # Annotate with path components
            try:
                rel = db.relative_to(results_dir)
                parts = rel.parts
                r["experiment_dir"] = str(results_dir.name)
                if len(parts) >= 3:          # <mode>/<class>/<file>.db
                    r["mode"]         = parts[0]
                    r["trafficClass"] = parts[1]
                elif len(parts) >= 2:        # <class>/<file>.db
                    r["mode"]         = "default"
                    r["trafficClass"] = parts[0]
                else:
                    r["mode"]         = "default"
                    r["trafficClass"] = "unknown"
            except ValueError:
                r["mode"]         = "default"
                r["trafficClass"] = "unknown"
            stem = db.stem  # e.g. periodic3_seed7
            if "_seed" in stem:
                r["seed"] = int(stem.rsplit("_seed", 1)[1])
            rows.append(r)
        except Exception as exc:
            print(f"  [warn] {db.name}: {exc}")

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="NR-SL channel occupancy analysis from a simulation DB."
    )
    sub = ap.add_subparsers(dest="cmd")

    # single-DB mode
    sp = sub.add_parser("single", help="Analyse one .db file")
    sp.add_argument("db",       type=Path)
    sp.add_argument("--run-csv",type=Path, default=None)
    sp.add_argument("--sim-time",  type=float, default=60.0)
    sp.add_argument("--warmup",    type=float, default=2.0)
    sp.add_argument("--psfch-period", type=int, default=4)
    sp.add_argument("--bw-prbs",   type=int,   default=None)

    # experiment mode
    ep = sub.add_parser("experiment", help="Loop over all DBs in a results dir")
    ep.add_argument("results_dir", type=Path)
    ep.add_argument("--out-csv",   type=Path,  default=None)
    ep.add_argument("--sim-time",  type=float, default=60.0)
    ep.add_argument("--warmup",    type=float, default=2.0)
    ep.add_argument("--psfch-period", type=int, default=4)
    ep.add_argument("--bw-prbs",   type=int,   default=10)

    args = ap.parse_args()

    if args.cmd == "single":
        r = analyze_channel_occupancy(
            args.db,
            sim_time_s   = args.sim_time,
            warmup_s     = args.warmup,
            psfch_period = args.psfch_period,
            bw_prbs      = args.bw_prbs,
            run_csv      = args.run_csv,
        )
        print_occupancy(r)

    elif args.cmd == "experiment":
        df = analyze_experiment(
            args.results_dir,
            default_sim_time_s   = args.sim_time,
            default_warmup_s     = args.warmup,
            default_psfch_period = args.psfch_period,
            default_bw_prbs      = args.bw_prbs,
        )
        print(df[["trafficClass", "mode", "seed",
                  "frac_psfch", "frac_data_slots", "frac_empty_slots",
                  "frac_data_subch", "frac_psfch_used", "retx_ratio"]].to_string(index=False))
        if args.out_csv:
            df.to_csv(args.out_csv, index=False)
            print(f"\nSaved → {args.out_csv}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
