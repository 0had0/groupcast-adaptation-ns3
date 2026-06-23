#!/usr/bin/env python3
"""
Aggregate relay_storm_fixed sweep DBs into sweep_aggregate.csv.

KPIs extracted from the ns-3 trace tables:
  storm_ratio : n_retx / n_new  over all PSSCH transmissions in psschTxUeMac
                (ndi==0 -> HARQ retransmission, ndi==1 -> fresh transport block).
                It is the per-run retransmission overhead fraction: how many
                HARQ retransmissions occur per fresh TB. Higher => more storm.
  prr_mean    : mean effective PRR across nodes
                = max(leader_rxed, relay_rxed) / leader_total_txed
  prr_worst   : worst-case PRR across nodes (last vehicle)
  aoi_mean_ms : mean Age of Information at each node (ms)
  harq_nack   : HARQ NACK / total HARQ feedback
  ch_occ      : resource occupation = total PSSCH TBs / sim_duration_ms
                (mean number of sidelink transport blocks sent per ms; a
                proxy for how much of the shared sidelink resource pool the
                platoon consumes).
"""

import sqlite3, csv, re, argparse
from collections import defaultdict
from pathlib import Path

parser = argparse.ArgumentParser(description="Aggregate relay_storm_eval sweep DBs.")
parser.add_argument("db_dir", nargs="?", default="/tmp/relay_storm_10seeds_fixed",
                    help="Directory containing per-run subdirectories (default: /tmp/relay_storm_10seeds_fixed)")
args = parser.parse_args()
BASE = Path(args.db_dir)


# ── helpers ─────────────────────────────────────────────────────────────────

def open_db(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def parse_dir(name):
    m = re.match(
        r"(baseline|relay4|relay6)_s(\d+)_ivd(\d+)_mcd(\d+)_seed(\d+)",
        name
    )
    if not m:
        return None
    mode, spd, ivd, mcd, seed = m.groups()
    return dict(
        mode=mode,
        speed=int(spd), ivd=int(ivd), mcd=int(mcd), seed=int(seed),
        relay_node=0 if mode == "baseline" else (4 if mode == "relay4" else 6),
        is_baseline=mode == "baseline",
    )


def _agg(vals):
    """Return (mean, min, max, p5, p95) from a list of numbers."""
    if not vals:
        return (0.0, 0.0, 0.0, 0.0, 0.0)
    s = sorted(vals)
    n = len(s)
    return (
        sum(s) / n, s[0], s[-1],
        s[max(0, int(n * 0.05) - 1)],
        s[min(n - 1, int(n * 0.95))],
    )


def compute_kpis(conn, info):
    """
    Extract all KPIs from one run DB.
    """
    kpi = {}

    # ── PSSCH TX stats (storm_ratio source) ────────────────────────────────
    tx_rows = conn.execute(
        "SELECT srcL2Id, ndi, rv, harqId FROM psschTxUeMac"
    ).fetchall()
    n_new = sum(1 for r in tx_rows if r["ndi"] == 1)
    n_retx = sum(1 for r in tx_rows if r["ndi"] == 0)
    kpi["storm_ratio"] = n_retx / n_new if n_new > 0 else 0.0
    kpi["n_total_tx"] = len(tx_rows)

    # ── Sim duration (ms) ─────────────────────────────────────────────────
    max_tx_ms = conn.execute(
        "SELECT MAX(timeMs) FROM psschTxUeMac"
    ).fetchone()[0] or 1.0
    kpi["sim_duration_ms"] = max_tx_ms
    kpi["ch_occ"] = len(tx_rows) / max_tx_ms if max_tx_ms > 0 else 0.0

    # ── HARQ NACK rate ─────────────────────────────────────────────────────
    fb_rows = conn.execute(
        "SELECT status FROM slHarqFeedback"
    ).fetchall()
    total_fb = len(fb_rows)
    n_nack   = sum(1 for r in fb_rows if r["status"] == "NACK")
    kpi["harq_nack"] = n_nack / total_fb if total_fb > 0 else 0.0

    # ── PRR per node (from thput table) ───────────────────────────────────
    # thput: one row per (rxNodeId, srcIp) flow
    # baseline : one row per node (srcIp=leader only)
    # relay    : two rows per node (srcIp=leader, srcIp=relay)
    thput_rows = conn.execute(
        "SELECT nodeId, srcIp, totalPktTxed, totalPktRxed, thputKbps FROM thput"
    ).fetchall()

    nodeids = sorted(set(r["nodeId"] for r in thput_rows))
    prr_per_node = {}
    for nid in nodeids:
        # Get leader's delivery to this node (srcIp = leader IP = 7.0.0.2)
        leader_row = next(
            (r for r in thput_rows
             if r["nodeId"] == nid and r["srcIp"].endswith(".2")),
            None
        )
        leader_tx = leader_row["totalPktTxed"] if leader_row else 0
        leader_rx = leader_row["totalPktRxed"] if leader_row else 0

        # Get relay's delivery to this node (relay IP = 7.0.0.6)
        relay_row = next(
            (r for r in thput_rows
             if r["nodeId"] == nid and r["srcIp"].endswith(".6")),
            None
        )
        relay_rx = relay_row["totalPktRxed"] if relay_row else 0

        # Effective received = max(leader_rx, relay_rx)
        effective_rx = max(leader_rx, relay_rx)

        if leader_tx > 0:
            prr_per_node[nid] = effective_rx / leader_tx
        else:
            prr_per_node[nid] = 1.0

    kpi["prr_mean"]  = sum(prr_per_node.values()) / len(prr_per_node) if prr_per_node else 1.0
    kpi["prr_worst"] = min(prr_per_node.values()) if prr_per_node else 0.0

    # ── AoI (Age of Information) ───────────────────────────────────────────
    # Approach: match TX packet to earliest RX packet for same (nodeId, pktSeqNum).
    # For groupcast, all nodes receive the same seq from the same source.
    # AoI at node i for packet n = rx_time(n,i) - tx_time(n).
    # Mean AoI = average of (rx_time - tx_time) across all received packets.
    tx_rows_aoi = conn.execute("""
        SELECT timeSec, nodeId, pktSeqNum
        FROM pktTxRx
        WHERE txRx = 'tx'
        ORDER BY pktSeqNum
    """).fetchall()

    # Build tx_time lookup: seq → time
    tx_time = {r["pktSeqNum"]: r["timeSec"] for r in tx_rows_aoi}
    if not tx_time:
        kpi["aoi_mean_ms"] = 0.0
    else:
        # Get all RX times for non-leader nodes (nodes 1-8)
        rx_rows = conn.execute("""
            SELECT p.timeSec AS rx_time, p.pktSeqNum, p.nodeId
            FROM pktTxRx p
            JOIN pktTxRx t ON p.pktSeqNum = t.pktSeqNum
                             AND p.nodeId != t.nodeId
            WHERE p.txRx = 'rx' AND t.txRx = 'tx'
              AND p.nodeId > 0
            ORDER BY p.pktSeqNum, p.nodeId
        """).fetchall()

        if rx_rows:
            # Take the earliest RX for each (seq, nodeId) — that's when info arrived
            seen = {}
            for r in rx_rows:
                key = (r["nodeId"], r["pktSeqNum"])
                if key not in seen:
                    seen[key] = r["rx_time"]
            aoi_ms = [
                (rx_t - tx_time[seq]) * 1000.0
                for (nid, seq), rx_t in seen.items()
                if seq in tx_time and rx_t >= tx_time[seq]
            ]
            kpi["aoi_mean_ms"] = sum(aoi_ms) / len(aoi_ms) if aoi_ms else 0.0
        else:
            # Fallback: approximate AoI from throughput table
            # Mean latency ≈ (sim_duration - start_time) * (1 - PRR)
            sim_end = conn.execute(
                "SELECT MAX(timeSec) FROM pktTxRx WHERE txRx='rx'"
            ).fetchone()[0] or 5.0
            sim_start = conn.execute(
                "SELECT MIN(timeSec) FROM pktTxRx WHERE txRx='Tx'"
            ).fetchone()[0] or 0.0
            kpi["aoi_mean_ms"] = (sim_end - sim_start) * 500.0  # rough

    return kpi


# ── main ─────────────────────────────────────────────────────────────────────

def run():
    dirs = sorted(d for d in BASE.iterdir() if d.is_dir() and parse_dir(d.name))
    print(f"Found {len(dirs)} run directories")

    groups = defaultdict(list)

    for d in dirs:
        info = parse_dir(d.name)
        db_files = sorted(d.glob("*.db"))
        if not db_files:
            print(f"  WARNING: no .db in {d.name}")
            continue
        db_path = db_files[0]
        print(f"  {d.name} ...", end=" ", flush=True)

        try:
            conn = open_db(str(db_path))
            kpi  = compute_kpis(conn, info)
            conn.close()
        except Exception as ex:
            import traceback
            print(f"ERROR {ex}")
            continue

        key = (info["speed"], info["mcd"], info["relay_node"])
        groups[key].append({
            "speed":       info["speed"],
            "mcd":         info["mcd"],
            "relay_node":  info["relay_node"],
            "is_baseline": info["is_baseline"],
            "seed":        info["seed"],
            **kpi,
        })
        print(f"done  storm={kpi['storm_ratio']:.3f}  "
              f"prr_mean={kpi['prr_mean']:.4f}  "
              f"prr_worst={kpi['prr_worst']:.4f}  "
              f"aoi={kpi['aoi_mean_ms']:.1f}ms")

    # ── Aggregate across seeds ───────────────────────────────────────────────
    out_cols = [
        "speed", "mcd", "relay_node", "is_baseline", "n_rows",
        "prr_mean_agg_mean", "prr_mean_agg_min", "prr_mean_agg_max",
        "prr_mean_agg_p5",  "prr_mean_agg_p95",
        "prr_worst_agg_mean", "prr_worst_agg_min", "prr_worst_agg_max",
        "prr_worst_agg_p5",  "prr_worst_agg_p95",
        "aoi_mean_ms_agg_mean", "aoi_mean_ms_agg_p5", "aoi_mean_ms_agg_p95",
        "harq_nack_rate_agg_mean",
        "ch_occupancy_agg_mean",
        "storm_ratio_agg_mean",
    ]

    rows = []
    for key in sorted(groups):
        sd = groups[key]
        speed, mcd, relay_node = key

        prr_mean_a  = _agg([d["prr_mean"]      for d in sd])
        prr_wrst_a  = _agg([d["prr_worst"]     for d in sd])
        aoi_a       = _agg([d["aoi_mean_ms"]   for d in sd])
        nack_a      = _agg([d["harq_nack"]     for d in sd])
        occ_a       = _agg([d["ch_occ"]        for d in sd])
        storm_a     = _agg([d["storm_ratio"]  for d in sd])

        rows.append({
            "speed": speed, "mcd": mcd,
            "relay_node": relay_node,
            "is_baseline": sd[0]["is_baseline"],
            "n_rows": len(sd),
            "prr_mean_agg_mean": prr_mean_a[0],
            "prr_mean_agg_min":   prr_mean_a[1],
            "prr_mean_agg_max":   prr_mean_a[2],
            "prr_mean_agg_p5":   prr_mean_a[3],
            "prr_mean_agg_p95":  prr_mean_a[4],
            "prr_worst_agg_mean": prr_wrst_a[0],
            "prr_worst_agg_min":  prr_wrst_a[1],
            "prr_worst_agg_max":  prr_wrst_a[2],
            "prr_worst_agg_p5":  prr_wrst_a[3],
            "prr_worst_agg_p95": prr_wrst_a[4],
            "aoi_mean_ms_agg_mean": aoi_a[0],
            "aoi_mean_ms_agg_p5":   aoi_a[3],
            "aoi_mean_ms_agg_p95":  aoi_a[4],
            "harq_nack_rate_agg_mean": nack_a[0],
            "ch_occupancy_agg_mean":   occ_a[0],
            "storm_ratio_agg_mean":    storm_a[0],
        })

    out_path = BASE / "sweep_aggregate.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_cols)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows → {out_path}")
    for r in rows:
        print(f"  speed={r['speed']:3d} mcd={r['mcd']:3d} relay={r['relay_node']}  "
              f"storm={r['storm_ratio_agg_mean']:.3f}  "
              f"prr={r['prr_mean_agg_mean']:.4f}")


if __name__ == "__main__":
    run()
