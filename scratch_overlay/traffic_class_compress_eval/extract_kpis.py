#!/usr/bin/env python3
"""
extract_kpis.py — adaptive-compression variant.

Walks every SQLite DB under
    results_traffic_class_compress_eval/<mode>/<class>/*_seed*.db
joins with the trace CSV for per-RX distance info, and writes tidy
long-format CSVs into results_traffic_class_compress_eval/tidy/:

  per_rx_kpis.csv       — one row per (scheme, class, seed, rx_node_id)
  time_series.csv       — one row per (scheme, class, seed, rx_node_id, t_bin_s)
  resource_usage.csv    — one row per PSSCH grant
  harq_stats.csv        — one row per (scheme, class, seed)
  compression_log.csv   — one row per packet event (codec/sizes/encUs/decUs)
  rlc_frag.csv          — one row per TX SDU (segment counts before/after)
  pdb_violations.csv    — one row per (scheme, class, seed, rx_node_id):
                          n_pkts, n_violations, viol_rate, mean/p95 e2e_ms
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT  = SCRIPT_DIR.parent.parent
DEFAULT_RESULTS = SCRIPT_DIR / "results_traffic_class_compress_eval"
DEFAULT_TRACE   = REPO_ROOT / "experiment_ra_apc" / "traces" / "intersection_nlosv.mob.csv"

TIME_BIN_S = 1.0

# Per-class PDB defaults (ms) — must mirror DefaultPdbMsForClass in
# traffic_class_compress_eval.cc.
PDB_MS = {
    "periodic1":  100,
    "periodic2":  100,
    "periodic3":  500,
    "aperiodic1": 100,
    "aperiodic2": 500,
}


# ──────────────────────────────────────────────────────────────────────────
# Path helpers
# ──────────────────────────────────────────────────────────────────────────

def parse_db_path(db_path: Path, root: Path):
    """
    Expect path layout:
        root / <scheme> / <class> / <class>_seed<N>.db
    Returns (scheme, class, seed) or None on parse failure.
    """
    try:
        rel = db_path.relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 3:
        return None
    scheme    = parts[0]
    traffic_class = parts[1]
    stem      = db_path.stem
    if "_seed" not in stem:
        return None
    seed = int(stem.rsplit("_seed", 1)[1])
    return scheme, traffic_class, seed


def load_trace_distances(trace_csv: Path, tx_node_id: int = 0):
    df = pd.read_csv(trace_csv, comment="#")
    if "node_id" not in df.columns:
        df = df.rename(columns={"id": "node_id"})
    init = df[df["time"] == df["time"].min()].set_index("node_id")
    tx_init = init.loc[tx_node_id, ["x", "y"]].to_numpy()

    out = {}
    for nid, sub in df.groupby("node_id"):
        rx_init = init.loc[nid, ["x", "y"]].to_numpy()
        init_d  = float(np.linalg.norm(rx_init - tx_init))
        merged = sub.merge(df[df["node_id"] == tx_node_id][["time", "x", "y"]],
                           on="time", suffixes=("", "_tx"))
        d = np.hypot(merged["x"] - merged["x_tx"], merged["y"] - merged["y_tx"])
        out[int(nid)] = (init_d, float(d.mean()))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Per-DB extractors
# ──────────────────────────────────────────────────────────────────────────

def per_rx_kpis_for_db(conn, scheme, traffic_class, seed, distances, tx_node_id):
    df_pkt = pd.read_sql_query(
        "SELECT txRx, nodeId, pktSeqNum FROM pktTxRx", conn
    )
    tx_pkts = int(((df_pkt["txRx"] == "tx") & (df_pkt["nodeId"] == tx_node_id)).sum())
    rx_counts = (df_pkt[df_pkt["txRx"] == "rx"]
                 .groupby("nodeId")["pktSeqNum"].count().to_dict())

    df_pir = pd.read_sql_query(
        "SELECT nodeId, avrgPirSec, TxRxDistance FROM avrgPir WHERE txRx='rx'", conn,
    )
    pir_map = dict(zip(df_pir["nodeId"], df_pir["avrgPirSec"]))
    pir_dist_map = dict(zip(df_pir["nodeId"], df_pir["TxRxDistance"]))

    df_th = pd.read_sql_query(
        "SELECT nodeId, totalPktRxed, thputKbps FROM thput WHERE txRx='rx'", conn,
    )
    th_map  = dict(zip(df_th["nodeId"], df_th["thputKbps"]))

    rows = []
    all_rx_ids = set(rx_counts) | set(pir_map) | set(th_map)
    if not all_rx_ids:
        all_rx_ids = {nid for nid in distances if nid != tx_node_id}
    for db_node_id in sorted(all_rx_ids):
        trace_nid = db_node_id
        init_d, mean_d = distances.get(trace_nid, (np.nan, np.nan))
        n_rx = int(rx_counts.get(db_node_id, 0))
        prr  = (n_rx / tx_pkts) if tx_pkts > 0 else np.nan
        pir_v = pir_map.get(db_node_id, np.nan)
        pir_ms = pir_v * 1000.0 if not pd.isna(pir_v) else np.nan
        tput   = th_map.get(db_node_id, np.nan)
        rows.append(dict(
            scheme         = scheme,
            trafficClass   = traffic_class,
            seed           = seed,
            rx_node_id     = int(trace_nid),
            tx_node_id     = int(tx_node_id),
            init_dist_m    = init_d,
            mean_dist_m    = mean_d,
            db_dist_m      = float(pir_dist_map.get(db_node_id, np.nan)),
            prr            = prr,
            pir_ms         = pir_ms,
            tput_kbps      = tput,
            n_rx_pkts      = n_rx,
            n_tx_pkts      = tx_pkts,
            n_lost_pkts    = max(0, tx_pkts - n_rx),
        ))
    return rows


def time_series_for_db(conn, scheme, traffic_class, seed, tx_node_id, bin_s=TIME_BIN_S):
    df = pd.read_sql_query(
        "SELECT timeSec, txRx, nodeId, pktSizeBytes, pktSeqNum FROM pktTxRx", conn,
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
            n_rx     = ("pktSeqNum", "count"),
            bytes_rx = ("pktSizeBytes", "sum"),
        )
        joined = agg.join(tx_per_bin, how="outer").fillna({"n_rx": 0, "bytes_rx": 0, "n_tx": 0})
        for tb, r in joined.iterrows():
            n_tx_b = r["n_tx"]; n_rx_b = r["n_rx"]
            prr_b  = (n_rx_b / n_tx_b) if n_tx_b > 0 else np.nan
            tput_b = (r["bytes_rx"] * 8.0 / 1000.0) / bin_s
            rows.append(dict(
                scheme = scheme, trafficClass = traffic_class, seed = seed,
                rx_node_id = int(nid), t_bin_s = float(tb),
                n_tx_pkts_window = int(n_tx_b), n_rx_pkts_window = int(n_rx_b),
                prr_window = prr_b, tput_window_kbps = tput_b,
            ))
    return rows


def resource_usage_for_db(conn, scheme, traffic_class, seed):
    df = pd.read_sql_query(
        "SELECT timeMs, frame, subFrame, slot, sbChSize, rbStart, rbLen, harqId, ndi, rv "
        "FROM psschTxUeMac", conn,
    )
    if df.empty:
        return []
    df.insert(0, "scheme", scheme)
    df.insert(1, "trafficClass", traffic_class)
    df.insert(2, "seed", seed)
    df.rename(columns={"timeMs": "time_ms"}, inplace=True)
    return df.to_dict("records")


def harq_stats_for_db(conn, scheme, traffic_class, seed):
    cur = conn.cursor()
    fb = dict(cur.execute("SELECT status, COUNT(*) FROM slHarqFeedback GROUP BY status").fetchall())
    ack_count  = int(fb.get("ACK", 0)); nack_count = int(fb.get("NACK", 0))
    timeout_count = int(cur.execute("SELECT COUNT(*) FROM slHarqTimeout").fetchone()[0])
    initial_tx_count = int(cur.execute("SELECT COUNT(*) FROM psschTxUeMac WHERE ndi=1").fetchone()[0])
    retx_count = int(cur.execute("SELECT COUNT(*) FROM psschTxUeMac WHERE ndi=0").fetchone()[0])
    tot_rx = int(cur.execute("SELECT COUNT(*) FROM psschRxUePhy").fetchone()[0])
    err_rx = int(cur.execute("SELECT COUNT(*) FROM psschRxUePhy WHERE psschCorrupt=1").fetchone()[0])
    nack_rate    = nack_count / (ack_count + nack_count) if (ack_count + nack_count) else np.nan
    retx_ratio   = retx_count / (retx_count + initial_tx_count) if (retx_count + initial_tx_count) else np.nan
    phy_err_rate = err_rx / tot_rx if tot_rx else np.nan
    return dict(
        scheme=scheme, trafficClass=traffic_class, seed=seed,
        ack_count=ack_count, nack_count=nack_count, timeout_count=timeout_count,
        initial_tx_count=initial_tx_count, retx_count=retx_count,
        nack_rate=nack_rate, retx_ratio=retx_ratio,
        phy_err_count=err_rx, phy_rx_count=tot_rx, phy_err_rate=phy_err_rate,
    )


# ──────────────────────────────────────────────────────────────────────────
# New extractors for the compression variant
# ──────────────────────────────────────────────────────────────────────────

CODEC_NAME = {0: "NONE", 1: "LZ4", 2: "ZSTD_L1", 3: "ZSTD_L3"}


def compression_log_for_db(conn, scheme, traffic_class, seed):
    try:
        df = pd.read_sql_query(
            "SELECT timeSec, txRx, nodeId, flowId, pktSeq, codec, origSize, "
            "compSize, encodeUs, decodeUs, entropyEst, reason FROM compressionLog",
            conn)
    except Exception:
        return []
    if df.empty:
        return []
    df["codec_name"] = df["codec"].map(CODEC_NAME).fillna("UNKNOWN")
    df["ratio"] = df["compSize"].astype(float) / df["origSize"].clip(lower=1)
    df.insert(0, "scheme", scheme)
    df.insert(1, "trafficClass", traffic_class)
    df.insert(2, "seed", seed)
    return df.to_dict("records")


def rlc_frag_for_db(conn, scheme, traffic_class, seed):
    try:
        df = pd.read_sql_query(
            "SELECT timeSec, txNodeId, pktSeq, origSdu, wireBytes, "
            "nSegmentsPre, nSegmentsPost, tbSize FROM rlcFrag",
            conn)
    except Exception:
        return []
    if df.empty:
        return []
    df.insert(0, "scheme", scheme)
    df.insert(1, "trafficClass", traffic_class)
    df.insert(2, "seed", seed)
    return df.to_dict("records")


def pdb_violations_for_db(conn, scheme, traffic_class, seed, tx_node_id):
    """Compute per-RX PDB-violation count using pktTxRx timestamps."""
    df = pd.read_sql_query(
        "SELECT timeSec, txRx, nodeId, pktSeqNum FROM pktTxRx", conn)
    if df.empty:
        return []
    tx = df[(df["txRx"] == "tx") & (df["nodeId"] == tx_node_id)]
    rx = df[df["txRx"] == "rx"]
    tx_t = dict(zip(tx["pktSeqNum"], tx["timeSec"]))
    pdb_ms = PDB_MS.get(traffic_class, 100)
    rows = []
    for rid, sub in rx.groupby("nodeId"):
        e2e_ms = []
        for _, r in sub.iterrows():
            t_tx = tx_t.get(int(r["pktSeqNum"]))
            if t_tx is None:
                continue
            e2e_ms.append((r["timeSec"] - t_tx) * 1000.0)
        if not e2e_ms:
            continue
        arr = np.asarray(e2e_ms)
        viol = int((arr > pdb_ms).sum())
        rows.append(dict(
            scheme=scheme, trafficClass=traffic_class, seed=seed,
            rx_node_id=int(rid), pdb_ms=pdb_ms,
            n_pkts=int(arr.size), n_violations=viol,
            viol_rate=viol / arr.size if arr.size else np.nan,
            mean_e2e_ms=float(arr.mean()),
            p95_e2e_ms=float(np.percentile(arr, 95)),
            max_e2e_ms=float(arr.max()),
        ))
    return rows


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=DEFAULT_RESULTS,
                    help="Campaign results dir (default %(default)s)")
    ap.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    ap.add_argument("--tx-node-id", type=int, default=0)
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args()

    if not args.results.is_dir():
        sys.exit(f"results dir not found: {args.results}")
    if not args.trace.is_file():
        sys.exit(f"trace not found: {args.trace}")

    out_dir = args.out_dir or (args.results / "tidy")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading trace for distances → {args.trace}")
    distances = load_trace_distances(args.trace, args.tx_node_id)
    print(f"  {len(distances)} node distances loaded (TX = node {args.tx_node_id})")

    dbs = sorted(args.results.rglob("*_seed*.db"))
    if not dbs:
        sys.exit(f"no *_seed*.db files under {args.results}")
    print(f"Found {len(dbs)} DB files; extracting ...")

    per_rx_rows, ts_rows, ru_rows, harq_rows = [], [], [], []
    comp_rows, frag_rows, pdb_rows = [], [], []

    for i, db in enumerate(dbs, 1):
        parsed = parse_db_path(db, args.results)
        if parsed is None:
            print(f"  [skip] cannot parse {db}")
            continue
        scheme, traffic_class, seed = parsed
        try:
            conn = sqlite3.connect(str(db))
            per_rx_rows.extend(per_rx_kpis_for_db(conn, scheme, traffic_class, seed,
                                                  distances, args.tx_node_id))
            ts_rows.extend(time_series_for_db(conn, scheme, traffic_class, seed,
                                              args.tx_node_id))
            ru_rows.extend(resource_usage_for_db(conn, scheme, traffic_class, seed))
            harq_rows.append(harq_stats_for_db(conn, scheme, traffic_class, seed))
            comp_rows.extend(compression_log_for_db(conn, scheme, traffic_class, seed))
            frag_rows.extend(rlc_frag_for_db(conn, scheme, traffic_class, seed))
            pdb_rows.extend(pdb_violations_for_db(conn, scheme, traffic_class, seed,
                                                  args.tx_node_id))
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
    write("per_rx_kpis.csv",     per_rx_rows)
    write("time_series.csv",     ts_rows)
    write("resource_usage.csv",  ru_rows)
    write("harq_stats.csv",      harq_rows)
    write("compression_log.csv", comp_rows)
    write("rlc_frag.csv",        frag_rows)
    write("pdb_violations.csv",  pdb_rows)
    print("Done.")


if __name__ == "__main__":
    main()
