#!/usr/bin/env python3
"""
Per-receiver PRR extractor for the s1 sweep.

For each completed run matching the filters, opens the gzipped sqlite KPI DB
(written by groupcast_eval) and computes, from the `pktTxRx` table:

  - per-rx aggregate PRR: rx_count / expected, where expected is the total
    #tx of that flow by all senders other than this rx
  - pairwise (tx_node, rx_node) PRR: rx_count(src=tx,dst=rx) / tx_count(src=tx)

Each rx node is joined to a tx<->rx initial distance read from the matching
SUMO vehicles.rou.xml (vehicle load order is assumed to equal nodeId order;
the s1 group_size scenarios use a single moving line at constant spacing,
so initial distance == steady-state distance).

Outputs (long CSVs):
  <out>            per-rx aggregate:
                   stem,scen,cond,n_ues,mu,w,mcs,seed,rx_node,distance_m,
                   flow,prr_rx,rx_count,expected
  <out>.pair.csv   per (tx_node, rx_node):
                   stem,...,tx_node,rx_node,distance_m,flow,prr_pair,
                   rx_count,tx_count

Pairwise attribution uses the DB's `srcIp` column on rx rows (which the
packets.csv export collapses into a single localIp field).

Usage:
    python3 per_rx_prr.py --tmp tmp --out per_rx_prr.csv
    python3 per_rx_prr.py --tmp tmp --scen s1 --conds nrx08 nrx12 --seeds 1 2
"""
import argparse
import csv
import gzip
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

RUN_RE = re.compile(
    r"^(?P<scen>s\d)_(?P<cond>[a-zA-Z0-9]+)_mu(?P<mu>\d+)_w(?P<w>\d+)_mcs(?P<mcs>\d+)_seed(?P<seed>\d+)$"
)
WARMUP_S = 3.0
FLOW_PORTS = {8001: "cam", 8002: "vid", 8003: "bg"}


def parse_stem(stem):
    m = RUN_RE.match(stem)
    if not m:
        return None
    d = m.groupdict()
    return {
        "scen": d["scen"],
        "cond": d["cond"],
        "mu": int(d["mu"]),
        "w": int(d["w"]),
        "mcs": int(d["mcs"]),
        "seed": int(d["seed"]),
    }


def num_ues_from_cond(scen, cond):
    if scen == "s1":
        try:
            return int(cond[3:])
        except (ValueError, IndexError):
            return 8
    return 8


def sumo_subdir(scen, cond):
    if scen == "s1":
        return f"s1_group_size/s1_{cond}"
    if scen == "s2":
        return f"s2_channel_heterogeneity/s2_{cond}"
    if scen == "s3":
        return f"s3_speed_doppler/s3_{cond}"
    if scen == "s4":
        return f"s4_propagation/s4_{cond}"
    return None


def load_distances(sumo_root, scen, cond):
    """Return list of distances in vehicle load order (index 0 == tx_0)."""
    sub = sumo_subdir(scen, cond)
    if sub is None:
        return None
    rou = sumo_root / sub / "vehicles.rou.xml"
    if not rou.exists():
        return None
    tree = ET.parse(rou)
    root = tree.getroot()
    positions = []
    tx_pos = None
    for v in root.findall("vehicle"):
        pos = float(v.get("departPos", "0"))
        positions.append(pos)
        role = None
        for p in v.findall("param"):
            if p.get("key") == "role":
                role = p.get("value")
        if role == "tx" and tx_pos is None:
            tx_pos = pos
    if tx_pos is None and positions:
        tx_pos = positions[0]
    return [abs(p - tx_pos) for p in positions]


def open_gz_db(db_gz):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    with gzip.open(db_gz, "rb") as fin:
        shutil.copyfileobj(fin, tmp, length=1 << 20)
    tmp.close()
    return sqlite3.connect(tmp.name), tmp.name


def per_rx_prr_for_run(db_gz, distances):
    con, tmp_name = open_gz_db(db_gz)
    try:
        # Discover IP <-> nodeId mapping from tx rows
        ip_to_node = {}
        for ip, node in con.execute(
            "SELECT DISTINCT srcIp, nodeId FROM pktTxRx WHERE txRx='tx'"
        ):
            ip_to_node[ip] = int(node)

        # Per-flow tx counts (post-warmup)
        tx_by_node = {}   # node -> flow -> count
        flow_total_tx = {f: 0 for f in FLOW_PORTS.values()}
        for node, port, c in con.execute(
            "SELECT nodeId, dstPort, COUNT(*) FROM pktTxRx "
            "WHERE txRx='tx' AND timeSec>? GROUP BY nodeId, dstPort",
            (WARMUP_S,),
        ):
            flow = FLOW_PORTS.get(int(port))
            if flow is None:
                continue
            tx_by_node.setdefault(int(node), {})[flow] = int(c)
            flow_total_tx[flow] += int(c)

        # Per-rx aggregate counts
        rx_by_node = {}
        for node, port, c in con.execute(
            "SELECT nodeId, dstPort, COUNT(*) FROM pktTxRx "
            "WHERE txRx='rx' AND timeSec>? GROUP BY nodeId, dstPort",
            (WARMUP_S,),
        ):
            flow = FLOW_PORTS.get(int(port))
            if flow is None:
                continue
            rx_by_node.setdefault(int(node), {})[flow] = int(c)

        # Pairwise rx counts
        pair_rx = {}  # (tx_node, rx_node, flow) -> count
        for src_ip, rx_node, port, c in con.execute(
            "SELECT srcIp, nodeId, dstPort, COUNT(*) FROM pktTxRx "
            "WHERE txRx='rx' AND timeSec>? GROUP BY srcIp, nodeId, dstPort",
            (WARMUP_S,),
        ):
            flow = FLOW_PORTS.get(int(port))
            if flow is None:
                continue
            tx_node = ip_to_node.get(src_ip)
            if tx_node is None:
                continue
            pair_rx[(tx_node, int(rx_node), flow)] = int(c)

        nodes = sorted(set(tx_by_node) | set(rx_by_node))

        agg_rows = []
        for node in nodes:
            d = distances[node] if (distances is not None and node < len(distances)) else None
            for flow in ("cam", "vid", "bg"):
                expected = flow_total_tx[flow] - tx_by_node.get(node, {}).get(flow, 0)
                if expected <= 0:
                    continue
                rx = rx_by_node.get(node, {}).get(flow, 0)
                agg_rows.append({
                    "rx_node": node,
                    "distance_m": d,
                    "flow": flow,
                    "prr_rx": rx / expected,
                    "rx_count": rx,
                    "expected": expected,
                })

        pair_rows = []
        for tx_n in nodes:
            for rx_n in nodes:
                if tx_n == rx_n:
                    continue
                if distances is not None and tx_n < len(distances) and rx_n < len(distances):
                    d = abs(distances[tx_n] - distances[rx_n])
                else:
                    d = None
                for flow in ("cam", "vid", "bg"):
                    tx_c = tx_by_node.get(tx_n, {}).get(flow, 0)
                    if tx_c <= 0:
                        continue
                    rx_c = pair_rx.get((tx_n, rx_n, flow), 0)
                    pair_rows.append({
                        "tx_node": tx_n,
                        "rx_node": rx_n,
                        "distance_m": d,
                        "flow": flow,
                        "prr_pair": rx_c / tx_c,
                        "rx_count": rx_c,
                        "tx_count": tx_c,
                    })
        return agg_rows, pair_rows
    finally:
        con.close()
        os.unlink(tmp_name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tmp", default=str(Path(__file__).resolve().parents[3] / "tmp"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--sumo", default=None,
                    help="Path to scratch_overlay/adaptive-phy-eval/sumo (auto)")
    ap.add_argument("--scen", nargs="*", default=["s1"])
    ap.add_argument("--conds", nargs="*", default=None)
    ap.add_argument("--mu", type=int, nargs="*", default=None)
    ap.add_argument("--w", type=int, nargs="*", default=None)
    ap.add_argument("--mcs", type=int, nargs="*", default=None)
    ap.add_argument("--seeds", type=int, nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    tmp = Path(args.tmp)
    out_csv = Path(args.out) if args.out else tmp / "_per_rx_prr.csv"
    sumo_root = Path(args.sumo) if args.sumo else (
        Path(__file__).resolve().parents[1] / "sumo"
    )

    db_files = sorted(tmp.glob("*.db.gz"))
    print(f"Found {len(db_files)} DB files in {tmp}", file=sys.stderr)

    dist_cache = {}
    agg_rows = []
    pair_rows = []
    processed = 0
    skipped = 0
    for pf in db_files:
        stem = pf.name[: -len(".db.gz")]
        meta = parse_stem(stem)
        if meta is None:
            skipped += 1
            continue
        if args.scen and meta["scen"] not in args.scen:
            continue
        if args.conds and meta["cond"] not in args.conds:
            continue
        if args.mu and meta["mu"] not in args.mu:
            continue
        if args.w and meta["w"] not in args.w:
            continue
        if args.mcs and meta["mcs"] not in args.mcs:
            continue
        if args.seeds and meta["seed"] not in args.seeds:
            continue

        n_ues = num_ues_from_cond(meta["scen"], meta["cond"])
        key = (meta["scen"], meta["cond"])
        if key not in dist_cache:
            dist_cache[key] = load_distances(sumo_root, *key)
        distances = dist_cache[key]

        try:
            ar, pr = per_rx_prr_for_run(pf, distances)
        except Exception as e:
            print(f"  ERROR on {stem}: {type(e).__name__}: {e}", file=sys.stderr)
            skipped += 1
            continue

        base = {"stem": stem, **meta, "n_ues": n_ues}
        for r in ar:
            agg_rows.append({**base, **r})
        for r in pr:
            pair_rows.append({**base, **r})
        processed += 1
        if args.limit and processed >= args.limit:
            break
        if processed % 50 == 0:
            print(f"  processed {processed} runs, {len(agg_rows)} agg / "
                  f"{len(pair_rows)} pair rows", file=sys.stderr)

    agg_fields = [
        "stem", "scen", "cond", "n_ues", "mu", "w", "mcs", "seed",
        "rx_node", "distance_m", "flow", "prr_rx", "rx_count", "expected",
    ]
    pair_fields = [
        "stem", "scen", "cond", "n_ues", "mu", "w", "mcs", "seed",
        "tx_node", "rx_node", "distance_m", "flow", "prr_pair", "rx_count", "tx_count",
    ]
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=agg_fields)
        w.writeheader()
        for r in agg_rows:
            w.writerow({k: r.get(k) for k in agg_fields})
    pair_csv = out_csv.with_name(out_csv.stem + ".pair.csv") \
        if out_csv.suffix == ".csv" else out_csv.with_suffix(out_csv.suffix + ".pair.csv")
    with open(pair_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=pair_fields)
        w.writeheader()
        for r in pair_rows:
            w.writerow({k: r.get(k) for k in pair_fields})
    print(f"Wrote {out_csv}: {len(agg_rows)} agg rows", file=sys.stderr)
    print(f"Wrote {pair_csv}: {len(pair_rows)} pair rows", file=sys.stderr)
    print(f"Processed {processed} runs ({skipped} skipped)", file=sys.stderr)


if __name__ == "__main__":
    main()
