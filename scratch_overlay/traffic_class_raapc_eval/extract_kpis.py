#!/usr/bin/env python3
"""
extract_kpis.py

Reads every SQLite DB under results_traffic_class_eval/<class>/*.db,
joins with the trace CSV for distance info, and writes four tidy
long-format CSVs into results_traffic_class_eval/tidy/:

  per_rx_kpis.csv     — one row per (class, seed, rx_node_id)
  time_series.csv     — one row per (class, seed, rx_node_id, t_bin_s)
  resource_usage.csv  — one row per PSSCH grant
  harq_stats.csv      — one row per (class, seed)

Run after run_traffic_class_campaign.sh.
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT  = SCRIPT_DIR.parent.parent
DEFAULT_RESULTS = SCRIPT_DIR / "results_traffic_class_raapc_eval"
DEFAULT_TRACE   = REPO_ROOT / "experiment_ra_apc" / "traces" / "intersection_nlosv.mob.csv"

TIME_BIN_S = 1.0


def parse_db_name(db_path: Path):
    """Filename: <class>_seed<N>.db  →  (trafficClass, seed)."""
    stem = db_path.stem  # e.g. periodic1_seed7
    parts = stem.rsplit("_seed", 1)
    if len(parts) != 2:
        return None
    return parts[0], int(parts[1])


def load_trace_distances(trace_csv: Path, tx_node_id: int = 0):
    """
    For each node in the trace, compute its initial (t=0) and mean 2-D
    distance to the TX node. Returns dict[node_id] -> (init_d, mean_d).
    """
    df = pd.read_csv(trace_csv, comment="#")
    if "node_id" not in df.columns:
        df = df.rename(columns={"id": "node_id"})
    # Pivot so each timestep has all nodes' positions
    init = df[df["time"] == df["time"].min()].set_index("node_id")
    tx_init = init.loc[tx_node_id, ["x", "y"]].to_numpy()

    out = {}
    for nid, sub in df.groupby("node_id"):
        # initial distance: at t = min
        rx_init = init.loc[nid, ["x", "y"]].to_numpy()
        init_d  = float(np.linalg.norm(rx_init - tx_init))
        # mean distance: average over time, joining with TX positions per timestep
        merged = sub.merge(df[df["node_id"] == tx_node_id][["time", "x", "y"]],
                           on="time", suffixes=("", "_tx"))
        d = np.hypot(merged["x"] - merged["x_tx"], merged["y"] - merged["y_tx"])
        out[int(nid)] = (init_d, float(d.mean()))
    return out


# ────────────────────────────────────────────────────────────────────────────
# Per-DB extractors
# ────────────────────────────────────────────────────────────────────────────

def per_rx_kpis_for_db(conn, traffic_class, seed, distances, tx_node_id):
    """
    Compute per-RX PRR (from packet counts), PIR (avrgPir table), and
    throughput (thput table). Returns a list of dicts.
    """
    # 1) packet counts per (tx, rx) for PRR
    df_pkt = pd.read_sql_query(
        "SELECT txRx, nodeId, pktSeqNum FROM pktTxRx", conn
    )
    tx_pkts = int(((df_pkt["txRx"] == "tx") & (df_pkt["nodeId"] == tx_node_id)).sum())

    rx_counts = (df_pkt[df_pkt["txRx"] == "rx"]
                 .groupby("nodeId")["pktSeqNum"].count().to_dict())

    # 2) PIR (s → ms)
    df_pir = pd.read_sql_query(
        "SELECT nodeId, avrgPirSec, TxRxDistance FROM avrgPir WHERE txRx='rx'",
        conn,
    )
    pir_map = dict(zip(df_pir["nodeId"], df_pir["avrgPirSec"]))
    pir_dist_map = dict(zip(df_pir["nodeId"], df_pir["TxRxDistance"]))

    # 3) throughput
    df_th = pd.read_sql_query(
        "SELECT nodeId, totalPktRxed, thputKbps FROM thput WHERE txRx='rx'",
        conn,
    )
    th_map  = dict(zip(df_th["nodeId"], df_th["thputKbps"]))
    rxN_map = dict(zip(df_th["nodeId"], df_th["totalPktRxed"]))

    rows = []
    # Build the set of RX node IDs. Normally this is the union of nodes that
    # appear in pktTxRx/avrgPir/thput. For traffic classes with zero received
    # packets (e.g. Periodic3 whose large packets are always dropped after RLC
    # segmentation), all three maps are empty — fall back to every node in the
    # distance table except the TX, so we still emit PRR=0 rows.
    all_rx_ids = set(rx_counts) | set(pir_map) | set(th_map)
    if not all_rx_ids:
        all_rx_ids = {nid for nid in distances if nid != tx_node_id}
    for db_node_id in sorted(all_rx_ids):
        # db_node_id = imsi index = trace nodeId + 1  (ns-3 IMSI is 1-based,
        # but we built nodes from the trace in trace-node-id order, so
        # nodeId here = container index; trace nodeId == db nodeId in our case).
        trace_nid = db_node_id  # WaypointMobility: container index == trace id

        # When the trace's TX is node 0, the RX trace ids are 1..8 in the trace.
        # The DB stores them with offset (the container loader preserves order),
        # but trace_nid 0 is the TX → not in RX set. We use trace_nid directly.
        init_d, mean_d = distances.get(trace_nid, (np.nan, np.nan))

        n_rx = int(rx_counts.get(db_node_id, 0))
        prr  = (n_rx / tx_pkts) if tx_pkts > 0 else np.nan
        pir_ms = pir_map.get(db_node_id, np.nan) * 1000.0 \
                 if not pd.isna(pir_map.get(db_node_id, np.nan)) else np.nan
        tput   = th_map.get(db_node_id, np.nan)

        rows.append(dict(
            trafficClass = traffic_class,
            seed         = seed,
            rx_node_id   = int(trace_nid),
            tx_node_id   = int(tx_node_id),
            init_dist_m  = init_d,
            mean_dist_m  = mean_d,
            db_dist_m    = float(pir_dist_map.get(db_node_id, np.nan)),
            prr          = prr,
            pir_ms       = pir_ms,
            tput_kbps    = tput,
            n_rx_pkts    = n_rx,
            n_tx_pkts    = tx_pkts,
            n_lost_pkts  = max(0, tx_pkts - n_rx),
        ))
    return rows


def time_series_for_db(conn, traffic_class, seed, tx_node_id, bin_s=TIME_BIN_S):
    """
    Bin pktTxRx into bin_s-second windows per RX node. Compute per-window
    PRR (rx_count / tx_count_in_same_bin), PIR (mean inter-arrival within
    the window), and throughput (bytes×8 / 1000 / bin_s).
    """
    df = pd.read_sql_query(
        "SELECT timeSec, txRx, nodeId, pktSizeBytes, pktSeqNum FROM pktTxRx",
        conn,
    )
    if df.empty:
        return []
    df["t_bin_s"] = (df["timeSec"] // bin_s * bin_s).astype(float)

    tx = df[(df["txRx"] == "tx") & (df["nodeId"] == tx_node_id)]
    rx = df[df["txRx"] == "rx"]

    tx_per_bin = tx.groupby("t_bin_s")["pktSeqNum"].count().rename("n_tx")
    rows = []
    for nid, sub in rx.groupby("nodeId"):
        agg = sub.groupby("t_bin_s").agg(
            n_rx       = ("pktSeqNum",   "count"),
            bytes_rx   = ("pktSizeBytes", "sum"),
        )
        # PIR per bin: mean diff between successive rx timestamps inside the bin
        pir_per_bin = []
        for tb, ss in sub.groupby("t_bin_s"):
            ts = ss["timeSec"].sort_values().to_numpy()
            if len(ts) >= 2:
                pir_per_bin.append((tb, float(np.diff(ts).mean()) * 1000.0))
            else:
                pir_per_bin.append((tb, np.nan))
        pir_df = pd.DataFrame(pir_per_bin, columns=["t_bin_s", "pir_window_ms"]) \
                   .set_index("t_bin_s")

        joined = agg.join(tx_per_bin, how="outer").join(pir_df, how="left")
        joined = joined.fillna({"n_rx": 0, "bytes_rx": 0, "n_tx": 0})

        for tb, r in joined.iterrows():
            n_tx_b = r["n_tx"]
            n_rx_b = r["n_rx"]
            prr_b  = (n_rx_b / n_tx_b) if n_tx_b > 0 else np.nan
            tput_b = (r["bytes_rx"] * 8.0 / 1000.0) / bin_s   # kbps
            rows.append(dict(
                trafficClass = traffic_class,
                seed         = seed,
                rx_node_id   = int(nid),
                t_bin_s      = float(tb),
                n_tx_pkts_window   = int(n_tx_b),
                n_rx_pkts_window   = int(n_rx_b),
                prr_window         = prr_b,
                pir_window_ms      = r.get("pir_window_ms", np.nan),
                tput_window_kbps   = tput_b,
            ))
    return rows


def resource_usage_for_db(conn, traffic_class, seed):
    df = pd.read_sql_query(
        "SELECT timeMs, frame, subFrame, slot, symStart, symLen, "
        "sbChSize, rbStart, rbLen, harqId, ndi, rv "
        "FROM psschTxUeMac",
        conn,
    )
    if df.empty:
        return []
    df.insert(0, "trafficClass", traffic_class)
    df.insert(1, "seed", seed)
    df.rename(columns={"timeMs": "time_ms"}, inplace=True)
    return df.to_dict("records")


def harq_stats_for_db(conn, traffic_class, seed):
    cur = conn.cursor()

    # PSFCH ACK / NACK counts
    fb = dict(cur.execute(
        "SELECT status, COUNT(*) FROM slHarqFeedback GROUP BY status"
    ).fetchall())
    ack_count  = int(fb.get("ACK", 0))
    nack_count = int(fb.get("NACK", 0))

    # HARQ-process timeouts (TBs that ran out of retries without an ACK)
    timeout_count = int(cur.execute(
        "SELECT COUNT(*) FROM slHarqTimeout"
    ).fetchone()[0])

    # Initial TX vs retransmissions (ndi=1 == new data, ndi=0 == retx)
    initial_tx_count = int(cur.execute(
        "SELECT COUNT(*) FROM psschTxUeMac WHERE ndi=1"
    ).fetchone()[0])
    retx_count = int(cur.execute(
        "SELECT COUNT(*) FROM psschTxUeMac WHERE ndi=0"
    ).fetchone()[0])

    # PHY-layer decode-error rate at the RX side
    tot_rx = int(cur.execute("SELECT COUNT(*) FROM psschRxUePhy").fetchone()[0])
    err_rx = int(cur.execute(
        "SELECT COUNT(*) FROM psschRxUePhy WHERE psschCorrupt=1"
    ).fetchone()[0])

    nack_rate     = nack_count / (ack_count + nack_count) if (ack_count + nack_count) else np.nan
    retx_ratio    = retx_count / (retx_count + initial_tx_count) if (retx_count + initial_tx_count) else np.nan
    phy_err_rate  = err_rx / tot_rx if tot_rx else np.nan

    return dict(
        trafficClass     = traffic_class,
        seed             = seed,
        ack_count        = ack_count,
        nack_count       = nack_count,
        timeout_count    = timeout_count,
        initial_tx_count = initial_tx_count,
        retx_count       = retx_count,
        nack_rate        = nack_rate,
        retx_ratio       = retx_ratio,
        phy_err_count    = err_rx,
        phy_rx_count     = tot_rx,
        phy_err_rate     = phy_err_rate,
    )


def adapt_log_for_db(conn, traffic_class, seed):
    """
    Read the per-decision adaptLog table written by KpiHooks::WriteAdaptLog.
    Returns a list of dicts (one per controller evaluation tick that produced
    a row). The table is absent on fixed-MCS runs; we handle that defensively.
    """
    cur = conn.cursor()
    has_tbl = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='adaptLog';"
    ).fetchone() is not None
    if not has_tbl:
        return []
    try:
        df = pd.read_sql_query(
            "SELECT timeMs, metric, prevMcs, newMcs, decision, "
            "ackCount, nackCount, windowStartMs, windowEndMs FROM adaptLog "
            "ORDER BY timeMs",
            conn,
        )
    except Exception:
        return []
    if df.empty:
        return []
    df.insert(0, "trafficClass", traffic_class)
    df.insert(1, "seed", seed)
    df.rename(columns={
        "timeMs":        "t_ms",
        "metric":        "nack_rate",   # for NackRate mode; ETP for raApc*
        "prevMcs":       "prev_mcs",
        "newMcs":        "new_mcs",
        "ackCount":      "ack_count",
        "nackCount":     "nack_count",
        "windowStartMs": "win_start_ms",
        "windowEndMs":   "win_end_ms",
    }, inplace=True)
    df["t_s"] = df["t_ms"] / 1000.0
    return df.to_dict("records")


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=DEFAULT_RESULTS,
                    help="Campaign results directory containing <class>/<class>_seed<N>.db files")
    ap.add_argument("--trace", type=Path, default=DEFAULT_TRACE,
                    help="Trace CSV used for distance lookup (default: intersection_nlosv.mob.csv)")
    ap.add_argument("--tx-node-id", type=int, default=0,
                    help="Trace node ID of the transmitter (default 0 = leader)")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="Output directory (default: <results>/tidy)")
    args = ap.parse_args()

    if not args.results.is_dir():
        sys.exit(f"results dir not found: {args.results}\n"
                 "  run run_traffic_class_campaign.sh first.")
    if not args.trace.is_file():
        sys.exit(f"trace not found: {args.trace}")

    out_dir = args.out_dir or (args.results / "tidy")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading trace for distances → {args.trace}")
    distances = load_trace_distances(args.trace, args.tx_node_id)
    print(f"  {len(distances)} node distances loaded "
          f"(TX = node {args.tx_node_id})")

    dbs = sorted(args.results.rglob("*_seed*.db"))
    if not dbs:
        sys.exit(f"no *_seed*.db files under {args.results}")
    print(f"Found {len(dbs)} DB files; extracting ...")

    per_rx_rows, ts_rows, ru_rows, harq_rows, adapt_rows = [], [], [], [], []

    for i, db in enumerate(dbs, 1):
        parsed = parse_db_name(db)
        if parsed is None:
            print(f"  [skip] cannot parse class/seed from {db.name}")
            continue
        traffic_class, seed = parsed
        try:
            conn = sqlite3.connect(str(db))
            per_rx_rows.extend(per_rx_kpis_for_db(conn, traffic_class, seed,
                                                  distances, args.tx_node_id))
            ts_rows.extend(time_series_for_db(conn, traffic_class, seed,
                                              args.tx_node_id))
            ru_rows.extend(resource_usage_for_db(conn, traffic_class, seed))
            harq_rows.append(harq_stats_for_db(conn, traffic_class, seed))
            adapt_rows.extend(adapt_log_for_db(conn, traffic_class, seed))
            conn.close()
        except Exception as e:
            print(f"  [error] {db.name}: {e}")
            continue
        if i % 10 == 0 or i == len(dbs):
            print(f"  ...{i}/{len(dbs)}")

    def write(name, rows):
        path = out_dir / name
        if not rows:
            print(f"  (no rows for {name})")
            return
        pd.DataFrame(rows).to_csv(path, index=False)
        print(f"  → {path}  ({len(rows)} rows)")

    print(f"\nWriting tidy CSVs → {out_dir}")
    write("per_rx_kpis.csv",    per_rx_rows)
    write("time_series.csv",    ts_rows)
    write("resource_usage.csv", ru_rows)
    write("harq_stats.csv",     harq_rows)
    write("adapt_log.csv",      adapt_rows)
    print("Done.")


if __name__ == "__main__":
    main()
