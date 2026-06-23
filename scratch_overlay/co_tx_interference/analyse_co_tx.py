#!/usr/bin/env python3
"""
analyse_co_tx.py — aggregate co_tx_interference sweep results.

Reads all per-run SQLite DBs under a results directory and produces:
  co_tx_aggregate.csv — one row per (speed, ivd, mcd, mode, gate) cell,
                        averaged over seeds. Includes:
                          - storm_ratio (leader hop-1 reTX / n_new)
                          - co_tx_overlap_frac (fraction of leader TBs whose
                            RX window contains a relay TB TX at the same
                            (frame, subFrame, slot))
                          - relay pipeline latency stats (from relayTiming)
                          - per-listener NACK rate against the leader
                          - hop-1 vs hop-2 reTX counts
  co_tx_per_run.csv   — same metrics but per-run (no seed averaging).

Usage:
    python3 analyse_co_tx.py <results_dir> [output_dir]
"""

import csv
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Per-DB metric extraction
# ---------------------------------------------------------------------------

# Match the run-tag produced by run_co_tx.sh:
#   <mode>_<gate>_s<speed>_ivd<ivd>_mcd<mcd>_seed<seed>
TAG_RE = re.compile(
    r"^(?P<mode>forward|noForward)_(?P<gate>on|off)"
    r"_s(?P<speed>\d+)_ivd(?P<ivd>\d+)_mcd(?P<mcd>\d+)_seed(?P<seed>\d+)$"
)


def parse_tag(name: str):
    m = TAG_RE.match(name)
    if not m:
        return None
    return m.groupdict()


def open_db(db_path: Path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def safe_stat(vals, fn):
    if not vals:
        return 0.0
    return fn(vals)


def per_db_metrics(db_path: Path) -> dict:
    """Extract one row of metrics from a single DB."""
    info = parse_tag(db_path.parent.name)
    if info is None:
        return None

    c = open_db(db_path)
    try:
        # --- psschTxUeMac: leader (srcL2Id=1) hop-1 TX ---
        leader_rows = list(c.execute(
            "SELECT ndi, srcL2Id FROM psschTxUeMac WHERE srcL2Id=1"
        ))
        n_new_l = sum(1 for r in leader_rows if r["ndi"] == 1)
        n_retx_l = sum(1 for r in leader_rows if r["ndi"] == 0)
        n_total_l = len(leader_rows)
        storm_ratio_l = n_retx_l / n_new_l if n_new_l > 0 else 0.0

        # --- psschTxUeMac: relay (srcL2Id=5) hop-2 TX ---
        relay_rows = list(c.execute(
            "SELECT ndi, srcL2Id, frame, subFrame, slot FROM psschTxUeMac "
            "WHERE srcL2Id=5"
        ))
        n_new_r = sum(1 for r in relay_rows if r["ndi"] == 1)
        n_retx_r = sum(1 for r in relay_rows if r["ndi"] == 0)
        n_total_r = len(relay_rows)

        # --- co-TX overlap: how many leader TBs share (frame, subFrame, slot)
        #     with a relay TB TX ---
        leader_slots = set()
        leader_with_relay = set()
        for r in c.execute(
            "SELECT frame, subFrame, slot FROM psschTxUeMac WHERE srcL2Id=1"
        ):
            slot = (r["frame"], r["subFrame"], r["slot"])
            leader_slots.add(slot)
            leader_with_relay.add(slot)  # will subtract later
        relay_slots = set()
        for r in c.execute(
            "SELECT frame, subFrame, slot FROM psschTxUeMac WHERE srcL2Id=5"
        ):
            relay_slots.add((r["frame"], r["subFrame"], r["slot"]))
        n_leader = len(leader_slots)
        n_overlap = len(leader_slots & relay_slots)
        co_tx_overlap_frac = n_overlap / n_leader if n_leader > 0 else 0.0

        # --- relayTiming: pipeline latency ---
        rt_rows = list(c.execute(
            "SELECT relayDecodeSec, leaderGenSec, forwardSendSec FROM relayTiming"
        ))
        if rt_rows:
            decode_lat = [r["relayDecodeSec"] - r["leaderGenSec"] for r in rt_rows]
            stage_lat  = [r["forwardSendSec"] - r["relayDecodeSec"] for r in rt_rows]
            tot_lat    = [r["forwardSendSec"] - r["leaderGenSec"] for r in rt_rows]
            # NB: leaderGenSec comes from the SeqTsSizeHeader timestamp the
            # leader app stamped when generating the packet.
            pipeline_mean_ms = safe_stat(tot_lat, statistics.mean) * 1000
            pipeline_p95_ms  = safe_stat(tot_lat, lambda v: sorted(v)[int(len(v) * 0.95)]) * 1000
            pipeline_p99_ms  = safe_stat(tot_lat, lambda v: sorted(v)[int(len(v) * 0.99)]) * 1000
            decode_p95_ms    = safe_stat(decode_lat, lambda v: sorted(v)[int(len(v) * 0.95)]) * 1000
            stage_p95_ms     = safe_stat(stage_lat,  lambda v: sorted(v)[int(len(v) * 0.95)]) * 1000
            n_relay_timing = len(rt_rows)
        else:
            pipeline_mean_ms = pipeline_p95_ms = pipeline_p99_ms = 0.0
            decode_p95_ms = stage_p95_ms = 0.0
            n_relay_timing = 0

        # --- per-listener NACK rate against the leader (rxRnti vs txRnti=1) ---
        nack_rates = {}
        for r in c.execute("""
            SELECT rxRnti,
                   SUM(CASE WHEN status='NACK' THEN 1 ELSE 0 END) nack,
                   COUNT(*) total
            FROM slHarqFeedback WHERE txRnti=1
            GROUP BY rxRnti
        """):
            if r["total"] > 0:
                # imsi = rxRnti in this sim
                nack_rates[r["rxRnti"]] = r["nack"] / r["total"]
        nack_rate_relay = nack_rates.get(5, 0.0)  # imsi=5 is relay4
        nack_rate_mean = safe_stat(list(nack_rates.values()), statistics.mean)

        # --- leader TBler at the relay: average of psschRxUePhy.tbler for
        #     leader-TB RX events where rx is the relay node (imsi=5) ---
        tbler_relay_vals = [
            r[0] for r in c.execute("""
                SELECT psschTbler FROM psschRxUePhy
                WHERE txRnti=1 AND srcL2Id=1 AND cellId=4
            """)
        ]
        tbler_relay_mean = safe_stat(tbler_relay_vals, statistics.mean)

    finally:
        c.close()

    return {
        **info,
        "n_seeds": 1,
        "n_total_tx_l": n_total_l,
        "n_new_l": n_new_l,
        "n_retx_l": n_retx_l,
        "storm_ratio_l": round(storm_ratio_l, 4),
        "n_total_tx_r": n_total_r,
        "n_new_r": n_new_r,
        "n_retx_r": n_retx_r,
        "co_tx_n_overlap": n_overlap,
        "co_tx_n_leader_slots": n_leader,
        "co_tx_overlap_frac": round(co_tx_overlap_frac, 4),
        "n_relay_timing": n_relay_timing,
        "pipeline_mean_ms": round(pipeline_mean_ms, 3),
        "pipeline_p95_ms": round(pipeline_p95_ms, 3),
        "pipeline_p99_ms": round(pipeline_p99_ms, 3),
        "decode_p95_ms": round(decode_p95_ms, 3),
        "stage_p95_ms": round(stage_p95_ms, 3),
        "nack_rate_relay_imsi5": round(nack_rate_relay, 4),
        "nack_rate_mean": round(nack_rate_mean, 4),
        "tbler_relay_mean": round(tbler_relay_mean, 4),
    }


# ---------------------------------------------------------------------------
# Aggregation across seeds
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    results_dir = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else results_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Per-run rows
    per_run = []
    for d in sorted(p for p in results_dir.iterdir() if p.is_dir()):
        db = next(iter(d.glob("*.db")), None)
        if db is None:
            continue
        m = per_db_metrics(db)
        if m is None:
            continue
        per_run.append(m)

    if not per_run:
        print(f"No DBs found under {results_dir}")
        sys.exit(1)
    print(f"Per-run rows: {len(per_run)}")

    per_run_path = output_dir / "co_tx_per_run.csv"
    cols = list(per_run[0].keys())
    with open(per_run_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(per_run)
    print(f"Wrote {per_run_path}")

    # Seed-aggregated: group by (mode, gate, speed, ivd, mcd)
    groups = defaultdict(list)
    for row in per_run:
        key = (row["mode"], row["gate"], row["speed"], row["ivd"], row["mcd"])
        groups[key].append(row)

    def _agg(vals, fn):
        return fn(vals) if vals else 0.0

    agg_rows = []
    for key in sorted(groups):
        sd = groups[key]
        mode, gate, speed, ivd, mcd = key
        agg_rows.append({
            "mode": mode,
            "gate": gate,
            "speed": int(speed),
            "ivd":   int(ivd),
            "mcd":   int(mcd),
            "n_seeds": len(sd),
            "storm_ratio_l_mean": round(_agg([r["storm_ratio_l"]   for r in sd], statistics.mean), 4),
            "storm_ratio_l_p95":  round(_agg([r["storm_ratio_l"]   for r in sd], lambda v: sorted(v)[int(len(v)*0.95)]), 4),
            "n_new_l_mean":       round(_agg([r["n_new_l"]         for r in sd], statistics.mean), 1),
            "n_retx_l_mean":      round(_agg([r["n_retx_l"]        for r in sd], statistics.mean), 1),
            "n_total_tx_r_mean":  round(_agg([r["n_total_tx_r"]    for r in sd], statistics.mean), 1),
            "co_tx_overlap_frac_mean": round(_agg([r["co_tx_overlap_frac"] for r in sd], statistics.mean), 4),
            "co_tx_overlap_frac_p95":  round(_agg([r["co_tx_overlap_frac"] for r in sd], lambda v: sorted(v)[int(len(v)*0.95)]), 4),
            "pipeline_mean_ms":   round(_agg([r["pipeline_mean_ms"] for r in sd], statistics.mean), 3),
            "pipeline_p95_ms":    round(_agg([r["pipeline_p95_ms"]  for r in sd], statistics.mean), 3),
            "pipeline_p99_ms":    round(_agg([r["pipeline_p99_ms"]  for r in sd], statistics.mean), 3),
            "decode_p95_ms":      round(_agg([r["decode_p95_ms"]    for r in sd], statistics.mean), 3),
            "stage_p95_ms":       round(_agg([r["stage_p95_ms"]     for r in sd], statistics.mean), 3),
            "nack_rate_relay_imsi5_mean": round(_agg([r["nack_rate_relay_imsi5"] for r in sd], statistics.mean), 4),
            "nack_rate_mean":     round(_agg([r["nack_rate_mean"]   for r in sd], statistics.mean), 4),
            "tbler_relay_mean":   round(_agg([r["tbler_relay_mean"] for r in sd], statistics.mean), 4),
        })

    agg_path = output_dir / "co_tx_aggregate.csv"
    with open(agg_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(agg_rows[0].keys()))
        w.writeheader()
        w.writerows(agg_rows)
    print(f"Wrote {len(agg_rows)} rows → {agg_path}")

    # Quick validation print: storm_ratio by (mode, gate)
    print("\nstorm_ratio_l_mean by (mode, gate):")
    by_mg = defaultdict(list)
    for r in agg_rows:
        by_mg[(r["mode"], r["gate"])].append(r["storm_ratio_l_mean"])
    for k in sorted(by_mg):
        vals = by_mg[k]
        print(f"  {k}: n={len(vals)} mean={statistics.mean(vals):.3f} "
              f"min={min(vals):.3f} max={max(vals):.3f}")


if __name__ == "__main__":
    main()