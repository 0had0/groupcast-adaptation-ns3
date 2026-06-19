#!/usr/bin/env python3
"""
extract_mcs_sweep.py

Reads every SQLite DB under results_mcs_sweep/<scenario>/*.db,
merges with the MCS=14 baseline from results_platoon_baseline/tidy/,
and writes tidy CSVs into results_mcs_sweep/tidy/:

  per_rx_kpis.csv     — one row per (scenario, mcs, seed, rx_node_id)
                         all MCS values including baseline MCS=14
  sinr_per_rx.csv     — SINR distribution per (scenario, mcs, seed, rnti)
  harq_stats.csv      — one row per (scenario, mcs, seed)
  mcs_comparison.csv  — aggregated: mean/worst-RX PRR, NACK rate per (scenario, mcs)

Run after run_mcs_sweep_campaign.sh.
"""

import sqlite3, sys, argparse
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR      = Path(__file__).resolve().parent
DEFAULT_SWEEP   = SCRIPT_DIR / "results_mcs_sweep"
DEFAULT_BASELINE= SCRIPT_DIR / "results_platoon_baseline" / "tidy"

SUMO_DIR = SCRIPT_DIR.parent.parent / "sumo_scenarios"
SCENARIO_TRACES = {
    "shadowing_blockage":   SUMO_DIR / "ccam_platoon_shadowing_blockage"        / "ccam_platoon_shadowing.mob.csv",
    "high_density":         SUMO_DIR / "ccam_platoon_high_density_interference"  / "ccam_platoon_hd_interference.mob.csv",
    "variable_packet_size": SUMO_DIR / "ccam_platoon_variable_packet_size"       / "ccam_platoon_vps_variable_packet.mob.csv",
}


def parse_db_name(db_path: Path):
    """<scenario>_mcs<M>_seed<N>.db → (scenario, mcs, seed)."""
    stem = db_path.stem
    # split off _seed<N>
    parts = stem.rsplit("_seed", 1)
    if len(parts) != 2:
        return None
    left, seed_str = parts
    # split off _mcs<M>
    mcs_parts = left.rsplit("_mcs", 1)
    if len(mcs_parts) != 2:
        return None
    scenario, mcs_str = mcs_parts
    try:
        return scenario, int(mcs_str), int(seed_str)
    except ValueError:
        return None


def load_trace_distances(trace_csv: Path, tx_node_id: int = 0):
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
        merged  = sub.merge(tx_df, on="time", suffixes=("", "_tx"))
        d = np.hypot(merged["x"] - merged["x_tx"], merged["y"] - merged["y_tx"])
        out[int(nid)] = (float(np.linalg.norm(rx_init - tx_init)), float(d.mean()))
    return out


def per_rx_for_db(conn, scenario, mcs, seed, distances, tx_node_id=0):
    df_pkt = pd.read_sql_query("SELECT txRx, nodeId, pktSeqNum FROM pktTxRx", conn)
    tx_pkts   = int(((df_pkt["txRx"]=="tx") & (df_pkt["nodeId"]==tx_node_id)).sum())
    rx_counts = df_pkt[df_pkt["txRx"]=="rx"].groupby("nodeId")["pktSeqNum"].count().to_dict()

    try:
        df_pir = pd.read_sql_query(
            "SELECT nodeId, avrgPirSec, TxRxDistance FROM avrgPir WHERE txRx='rx'", conn)
    except Exception:
        df_pir = pd.DataFrame(columns=["nodeId","avrgPirSec","TxRxDistance"])
    pir_map  = dict(zip(df_pir["nodeId"], df_pir["avrgPirSec"]))

    try:
        df_th = pd.read_sql_query(
            "SELECT nodeId, thputKbps FROM thput WHERE txRx='rx'", conn)
    except Exception:
        df_th = pd.DataFrame(columns=["nodeId","thputKbps"])
    th_map = dict(zip(df_th["nodeId"], df_th["thputKbps"]))

    all_rx_ids = set(rx_counts) | set(pir_map) | set(th_map)
    if not all_rx_ids:
        all_rx_ids = {nid for nid in distances if nid != tx_node_id}

    rows = []
    for nid in sorted(all_rx_ids):
        init_d, mean_d = distances.get(nid, (np.nan, np.nan))
        n_rx = int(rx_counts.get(nid, 0))
        prr  = (n_rx / tx_pkts) if tx_pkts > 0 else np.nan
        pir_s = pir_map.get(nid, np.nan)
        rows.append(dict(
            scenario   = scenario, mcs=mcs, seed=seed,
            rx_node_id = int(nid),
            init_dist_m= init_d, mean_dist_m=mean_d,
            prr        = prr,
            pir_ms     = pir_s * 1000.0 if not np.isnan(pir_s) else np.nan,
            tput_kbps  = th_map.get(nid, np.nan),
            n_rx_pkts  = n_rx, n_tx_pkts=tx_pkts,
        ))
    return rows


def sinr_for_db(conn, scenario, mcs, seed):
    try:
        df = pd.read_sql_query(
            "SELECT rnti, avrgSinr, psschCorrupt FROM psschRxUePhy", conn)
    except Exception:
        return []
    if df.empty:
        return []
    rows = []
    for rnti, sub in df.groupby("rnti"):
        sinr_db = 10.0 * np.log10(sub["avrgSinr"].clip(lower=1e-12))
        rows.append(dict(
            scenario=scenario, mcs=mcs, seed=seed,
            rx_rnti=int(rnti), n_tb=len(sub),
            mean_sinr_db   = float(sinr_db.mean()),
            p5_sinr_db     = float(np.percentile(sinr_db, 5)),
            p95_sinr_db    = float(np.percentile(sinr_db, 95)),
            corrupt_rate   = float(sub["psschCorrupt"].mean()),
        ))
    return rows


def harq_for_db(conn, scenario, mcs, seed):
    cur = conn.cursor()
    fb  = dict(cur.execute(
        "SELECT status, COUNT(*) FROM slHarqFeedback GROUP BY status").fetchall())
    ack  = int(fb.get("ACK",  0))
    nack = int(fb.get("NACK", 0))
    tot  = ack + nack
    init = int(cur.execute("SELECT COUNT(*) FROM psschTxUeMac WHERE ndi=1").fetchone()[0])
    retx = int(cur.execute("SELECT COUNT(*) FROM psschTxUeMac WHERE ndi=0").fetchone()[0])
    tr   = int(cur.execute("SELECT COUNT(*) FROM psschRxUePhy").fetchone()[0])
    er   = int(cur.execute(
        "SELECT COUNT(*) FROM psschRxUePhy WHERE psschCorrupt=1").fetchone()[0])
    return dict(
        scenario=scenario, mcs=mcs, seed=seed,
        ack_count=ack, nack_count=nack,
        nack_rate      = nack/tot if tot else np.nan,
        retx_ratio     = retx/(retx+init) if (retx+init) else np.nan,
        phy_err_rate   = er/tr if tr else np.nan,
        timeout_count  = int(cur.execute(
            "SELECT COUNT(*) FROM slHarqTimeout").fetchone()[0]),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep",    type=Path, default=DEFAULT_SWEEP)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                    help="Path to results_platoon_baseline/tidy/ (for MCS=14 rows)")
    ap.add_argument("--tx-node-id", type=int, default=0)
    ap.add_argument("--out-dir",  type=Path, default=None)
    args = ap.parse_args()

    if not args.sweep.is_dir():
        sys.exit(f"sweep dir not found: {args.sweep}")

    out_dir = args.out_dir or (args.sweep / "tidy")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Distance maps
    dist_maps = {}
    for scen, trace in SCENARIO_TRACES.items():
        if trace.is_file():
            dist_maps[scen] = load_trace_distances(trace, args.tx_node_id)

    dbs = sorted(args.sweep.rglob("*_seed*.db"))
    if not dbs:
        sys.exit(f"no *_seed*.db under {args.sweep}")
    print(f"Found {len(dbs)} DBs; extracting ...")

    prx_rows, sinr_rows, harq_rows = [], [], []
    for i, db in enumerate(dbs, 1):
        parsed = parse_db_name(db)
        if parsed is None:
            print(f"  [skip] {db.name}")
            continue
        scen, mcs, seed = parsed
        dist = dist_maps.get(scen, {})
        try:
            conn = sqlite3.connect(str(db))
            prx_rows.extend(per_rx_for_db(conn, scen, mcs, seed, dist, args.tx_node_id))
            sinr_rows.extend(sinr_for_db(conn, scen, mcs, seed))
            harq_rows.append(harq_for_db(conn, scen, mcs, seed))
            conn.close()
        except Exception as e:
            print(f"  [error] {db.name}: {e}")
        if i % 20 == 0 or i == len(dbs):
            print(f"  ...{i}/{len(dbs)}")

    # Merge MCS=14 baseline rows if available
    if args.baseline.is_dir():
        for fname, rows_list in [("per_rx_kpis.csv", prx_rows),
                                  ("sinr_per_rx.csv",  sinr_rows),
                                  ("harq_stats.csv",   harq_rows)]:
            p = args.baseline / fname
            if p.is_file():
                base_df = pd.read_csv(p)
                # Rename 'rx_rnti' column if needed to match sinr_rows key
                base_df["mcs"] = 14
                # Only include cols that exist in both
                rows_list += base_df.to_dict("records")
                print(f"  merged {len(base_df)} baseline rows from {fname}")

    def write(name, rows):
        path = out_dir / name
        if not rows:
            print(f"  (no rows for {name})")
            return
        df = pd.DataFrame(rows)
        # Deduplicate MCS=14 rows that may appear from both sweep and baseline
        df = df.drop_duplicates()
        df.to_csv(path, index=False)
        print(f"  → {path}  ({len(df)} rows)")

    print(f"\nWriting tidy CSVs → {out_dir}")
    write("per_rx_kpis.csv", prx_rows)
    write("sinr_per_rx.csv", sinr_rows)
    write("harq_stats.csv",  harq_rows)

    # Aggregated comparison table
    if prx_rows:
        df_prx = pd.DataFrame(prx_rows)
        df_prx = df_prx[df_prx["rx_node_id"] != 0]
        comp = df_prx.groupby(["scenario","mcs"]).agg(
            prr_mean      = ("prr",     "mean"),
            prr_std       = ("prr",     "std"),
            prr_worst_rx  = ("prr",     lambda s: s.groupby(
                                df_prx.loc[s.index,"rx_node_id"]).mean().min()),
            pir_mean_ms   = ("pir_ms",  "mean"),
            tput_mean_kbps= ("tput_kbps","mean"),
            n_seeds       = ("seed",    "nunique"),
        ).reset_index().sort_values(["scenario","mcs"])
        path = out_dir / "mcs_comparison.csv"
        comp.to_csv(path, index=False)
        print(f"  → {path}  ({len(comp)} rows)")

        print("\n=== Aggregated MCS comparison (mean PRR / worst-RX PRR) ===")
        print(comp.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    print("\nDone.")


if __name__ == "__main__":
    main()
