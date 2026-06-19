#!/usr/bin/env python3
"""
extract_kpis.py — Platoon baseline KPI extractor

Reads every SQLite DB under results_platoon_baseline/<scenario>/*.db,
joins with the per-scenario mobility trace for distance info, and writes
five tidy long-format CSVs into results_platoon_baseline/tidy/:

  per_rx_kpis.csv     — one row per (scenario, seed, rx_node_id)
                         includes mean/p5/p95 per-packet SINR from psschRxUePhy
  time_series.csv     — one row per (scenario, seed, rx_node_id, t_bin_s)
  resource_usage.csv  — one row per PSSCH grant (psschTxUeMac)
  harq_stats.csv      — one row per (scenario, seed)
  sinr_per_rx.csv     — one row per (scenario, seed, rx_node_id)
                         with SINR distribution metrics

Run after run_platoon_baseline_campaign.sh.
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT  = SCRIPT_DIR.parent.parent
SUMO_DIR   = REPO_ROOT / "sumo_scenarios"

DEFAULT_RESULTS = SCRIPT_DIR / "results_platoon_baseline"

# Per-scenario mobility trace paths
SCENARIO_TRACES = {
    "shadowing_blockage":   SUMO_DIR / "ccam_platoon_shadowing_blockage"   / "ccam_platoon_shadowing.mob.csv",
    "high_density":         SUMO_DIR / "ccam_platoon_high_density_interference" / "ccam_platoon_hd_interference.mob.csv",
    "variable_packet_size": SUMO_DIR / "ccam_platoon_variable_packet_size" / "ccam_platoon_vps_variable_packet.mob.csv",
}

TIME_BIN_S = 1.0


def parse_db_name(db_path: Path):
    """
    Filename: <scenario>_seed<N>.db  →  (scenario, seed).
    Scenarios contain underscores so we split on '_seed' from the right.
    """
    stem = db_path.stem
    parts = stem.rsplit("_seed", 1)
    if len(parts) != 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


def load_trace_distances(trace_csv: Path, tx_node_id: int = 0):
    """
    For each node in the trace, compute its initial (t=t_min) and mean 2-D
    distance to the TX node. Returns dict[node_id] -> (init_d, mean_d).
    """
    df = pd.read_csv(trace_csv, comment="#", header=None,
                     names=["time", "node_id", "x", "y", "speed"])
    t_min = df["time"].min()
    init  = df[df["time"] == t_min].set_index("node_id")

    if tx_node_id not in init.index:
        return {}

    tx_init = init.loc[tx_node_id, ["x", "y"]].to_numpy()
    tx_df   = df[df["node_id"] == tx_node_id][["time", "x", "y"]]

    out = {}
    for nid, sub in df.groupby("node_id"):
        rx_init = init.loc[nid, ["x", "y"]].to_numpy() if nid in init.index else tx_init
        init_d  = float(np.linalg.norm(rx_init - tx_init))
        merged  = sub.merge(tx_df, on="time", suffixes=("", "_tx"))
        d       = np.hypot(merged["x"] - merged["x_tx"], merged["y"] - merged["y_tx"])
        out[int(nid)] = (init_d, float(d.mean()))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Per-DB extractors
# ─────────────────────────────────────────────────────────────────────────────

def per_rx_kpis_for_db(conn, scenario, seed, distances, tx_node_id):
    """PRR (packet counts), PIR, throughput — one row per RX node."""
    df_pkt = pd.read_sql_query(
        "SELECT txRx, nodeId, pktSeqNum FROM pktTxRx", conn
    )
    tx_pkts   = int(((df_pkt["txRx"] == "tx") & (df_pkt["nodeId"] == tx_node_id)).sum())
    rx_counts = (df_pkt[df_pkt["txRx"] == "rx"]
                 .groupby("nodeId")["pktSeqNum"].count().to_dict())

    # avrgPir and thput tables are created by V2xKpi only when packets arrive;
    # they may be absent in empty-run DBs (parallel execution collision).
    try:
        df_pir = pd.read_sql_query(
            "SELECT nodeId, avrgPirSec, TxRxDistance FROM avrgPir WHERE txRx='rx'", conn
        )
    except Exception:
        df_pir = pd.DataFrame(columns=["nodeId", "avrgPirSec", "TxRxDistance"])
    pir_map      = dict(zip(df_pir["nodeId"], df_pir["avrgPirSec"]))
    pir_dist_map = dict(zip(df_pir["nodeId"], df_pir["TxRxDistance"]))

    try:
        df_th = pd.read_sql_query(
            "SELECT nodeId, totalPktRxed, thputKbps FROM thput WHERE txRx='rx'", conn
        )
    except Exception:
        df_th = pd.DataFrame(columns=["nodeId", "totalPktRxed", "thputKbps"])
    th_map  = dict(zip(df_th["nodeId"], df_th["thputKbps"]))
    rxN_map = dict(zip(df_th["nodeId"], df_th["totalPktRxed"]))

    # Fall back to distance table when no RX events occurred
    all_rx_ids = set(rx_counts) | set(pir_map) | set(th_map)
    if not all_rx_ids:
        all_rx_ids = {nid for nid in distances if nid != tx_node_id}

    rows = []
    for db_node_id in sorted(all_rx_ids):
        init_d, mean_d = distances.get(db_node_id, (np.nan, np.nan))
        n_rx  = int(rx_counts.get(db_node_id, 0))
        prr   = (n_rx / tx_pkts) if tx_pkts > 0 else np.nan
        pir_s = pir_map.get(db_node_id, np.nan)
        pir_ms = pir_s * 1000.0 if not np.isnan(pir_s) else np.nan
        tput  = th_map.get(db_node_id, np.nan)
        rows.append(dict(
            scenario    = scenario,
            seed        = seed,
            rx_node_id  = int(db_node_id),
            tx_node_id  = int(tx_node_id),
            init_dist_m = init_d,
            mean_dist_m = mean_d,
            db_dist_m   = float(pir_dist_map.get(db_node_id, np.nan)),
            prr         = prr,
            pir_ms      = pir_ms,
            tput_kbps   = tput,
            n_rx_pkts   = n_rx,
            n_tx_pkts   = tx_pkts,
            n_lost_pkts = max(0, tx_pkts - n_rx),
        ))
    return rows


def sinr_per_rx_for_db(conn, scenario, seed):
    """
    Per-RX SINR distribution extracted from psschRxUePhy.
    Returns one row per (scenario, seed, rx_node_id).
    """
    try:
        df = pd.read_sql_query(
            "SELECT rnti, avrgSinr, minSinr, psschCorrupt FROM psschRxUePhy",
            conn,
        )
    except Exception:
        return []

    if df.empty:
        return []

    rows = []
    for rnti, sub in df.groupby("rnti"):
        sinr_db = 10.0 * np.log10(sub["avrgSinr"].clip(lower=1e-12))
        rows.append(dict(
            scenario       = scenario,
            seed           = seed,
            rx_rnti        = int(rnti),
            n_tb           = len(sub),
            mean_sinr_db   = float(sinr_db.mean()),
            median_sinr_db = float(sinr_db.median()),
            p5_sinr_db     = float(np.percentile(sinr_db, 5)),
            p95_sinr_db    = float(np.percentile(sinr_db, 95)),
            min_sinr_db    = float(sinr_db.min()),
            max_sinr_db    = float(sinr_db.max()),
            corrupt_rate   = float(sub["psschCorrupt"].mean()),
        ))
    return rows


def time_series_for_db(conn, scenario, seed, tx_node_id, bin_s=TIME_BIN_S):
    """Per-RX, per-bin PRR / PIR / throughput."""
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
            n_rx      = ("pktSeqNum",    "count"),
            bytes_rx  = ("pktSizeBytes", "sum"),
        )
        pir_per_bin = []
        for tb, ss in sub.groupby("t_bin_s"):
            ts = ss["timeSec"].sort_values().to_numpy()
            pir_per_bin.append((tb, float(np.diff(ts).mean()) * 1000.0
                                if len(ts) >= 2 else np.nan))
        pir_df = (pd.DataFrame(pir_per_bin, columns=["t_bin_s", "pir_window_ms"])
                  .set_index("t_bin_s"))
        joined = agg.join(tx_per_bin, how="outer").join(pir_df, how="left")
        joined = joined.fillna({"n_rx": 0, "bytes_rx": 0, "n_tx": 0})
        for tb, r in joined.iterrows():
            n_tx_b = r["n_tx"]
            n_rx_b = r["n_rx"]
            rows.append(dict(
                scenario            = scenario,
                seed                = seed,
                rx_node_id          = int(nid),
                t_bin_s             = float(tb),
                n_tx_pkts_window    = int(n_tx_b),
                n_rx_pkts_window    = int(n_rx_b),
                prr_window          = (n_rx_b / n_tx_b) if n_tx_b > 0 else np.nan,
                pir_window_ms       = r.get("pir_window_ms", np.nan),
                tput_window_kbps    = (r["bytes_rx"] * 8.0 / 1000.0) / bin_s,
            ))
    return rows


def resource_usage_for_db(conn, scenario, seed):
    df = pd.read_sql_query(
        "SELECT timeMs, frame, subFrame, slot, symStart, symLen, "
        "sbChSize, rbStart, rbLen, harqId, ndi, rv "
        "FROM psschTxUeMac",
        conn,
    )
    if df.empty:
        return []
    df.insert(0, "scenario", scenario)
    df.insert(1, "seed", seed)
    df.rename(columns={"timeMs": "time_ms"}, inplace=True)
    return df.to_dict("records")


def harq_stats_for_db(conn, scenario, seed):
    cur = conn.cursor()
    fb = dict(cur.execute(
        "SELECT status, COUNT(*) FROM slHarqFeedback GROUP BY status"
    ).fetchall())
    ack_count  = int(fb.get("ACK", 0))
    nack_count = int(fb.get("NACK", 0))
    timeout_count = int(cur.execute(
        "SELECT COUNT(*) FROM slHarqTimeout"
    ).fetchone()[0])
    initial_tx_count = int(cur.execute(
        "SELECT COUNT(*) FROM psschTxUeMac WHERE ndi=1"
    ).fetchone()[0])
    retx_count = int(cur.execute(
        "SELECT COUNT(*) FROM psschTxUeMac WHERE ndi=0"
    ).fetchone()[0])
    tot_rx = int(cur.execute("SELECT COUNT(*) FROM psschRxUePhy").fetchone()[0])
    err_rx = int(cur.execute(
        "SELECT COUNT(*) FROM psschRxUePhy WHERE psschCorrupt=1"
    ).fetchone()[0])
    ack_total = ack_count + nack_count
    tx_total  = retx_count + initial_tx_count
    return dict(
        scenario         = scenario,
        seed             = seed,
        ack_count        = ack_count,
        nack_count       = nack_count,
        timeout_count    = timeout_count,
        initial_tx_count = initial_tx_count,
        retx_count       = retx_count,
        nack_rate        = nack_count / ack_total if ack_total else np.nan,
        retx_ratio       = retx_count / tx_total if tx_total else np.nan,
        phy_err_count    = err_rx,
        phy_rx_count     = tot_rx,
        phy_err_rate     = err_rx / tot_rx if tot_rx else np.nan,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Extract per-RX KPIs + SINR from platoon baseline results."
    )
    ap.add_argument("--results",    type=Path, default=DEFAULT_RESULTS)
    ap.add_argument("--tx-node-id", type=int,  default=0)
    ap.add_argument("--out-dir",    type=Path, default=None)
    args = ap.parse_args()

    if not args.results.is_dir():
        sys.exit(f"results dir not found: {args.results}\n"
                 "  run run_platoon_baseline_campaign.sh first.")

    out_dir = args.out_dir or (args.results / "tidy")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load per-scenario distance maps
    distance_maps = {}
    for scen, trace in SCENARIO_TRACES.items():
        if trace.is_file():
            print(f"Loading trace distances for '{scen}' → {trace.name}")
            distance_maps[scen] = load_trace_distances(trace, args.tx_node_id)
            print(f"  {len(distance_maps[scen])} nodes loaded")
        else:
            print(f"  [warn] trace not found for '{scen}': {trace}")
            distance_maps[scen] = {}

    dbs = sorted(args.results.rglob("*_seed*.db"))
    if not dbs:
        sys.exit(f"no *_seed*.db files under {args.results}")
    print(f"\nFound {len(dbs)} DB files; extracting ...")

    per_rx_rows, ts_rows, ru_rows, harq_rows, sinr_rows = [], [], [], [], []

    for i, db in enumerate(dbs, 1):
        parsed = parse_db_name(db)
        if parsed is None:
            print(f"  [skip] cannot parse scenario/seed from {db.name}")
            continue
        scenario, seed = parsed
        dist = distance_maps.get(scenario, {})
        try:
            conn = sqlite3.connect(str(db))
            per_rx_rows.extend(per_rx_kpis_for_db(conn, scenario, seed,
                                                   dist, args.tx_node_id))
            sinr_rows.extend(sinr_per_rx_for_db(conn, scenario, seed))
            ts_rows.extend(time_series_for_db(conn, scenario, seed,
                                               args.tx_node_id))
            ru_rows.extend(resource_usage_for_db(conn, scenario, seed))
            harq_rows.append(harq_stats_for_db(conn, scenario, seed))
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
    write("sinr_per_rx.csv",    sinr_rows)
    write("time_series.csv",    ts_rows)
    write("resource_usage.csv", ru_rows)
    write("harq_stats.csv",     harq_rows)
    print("Done.")


if __name__ == "__main__":
    main()
