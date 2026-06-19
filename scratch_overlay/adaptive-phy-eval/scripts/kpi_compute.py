#!/usr/bin/env python3
"""
In-line KPI extraction for groupcast_eval runs.

`compute_kpis(db_path, n_ues, distances, warmup_s, sim_time_s)` opens a raw
sqlite DB written by groupcast_eval and returns two lists of dicts:

  - run_rows   : one row per flow (port) with run-level aggregates
                 PRR, PIR (mean / p95), throughput, plus the same KPIs that
                 analyze_sweep.py extracts (collision rate, PSSCH BLER,
                 mean SINR, HARQ ACK/NACK/timeout).
  - rx_rows    : one row per (rx_node, flow) with per-receiver
                 PRR, PIR (mean / p95), throughput, ACK/NACK counts,
                 distance from Tx (via SUMO XML when available).

Definitions (V2X-standard):
  PRR  = rx_count / expected, where expected for a given rx is the total
         #tx of that flow by all senders other than this rx.
  PIR  = inter-reception time, the gap (s) between consecutive RX rows of
         the same (flow, rx_node, src). Reported as mean and p95 over all
         such gaps in that scope.
  Tput = sum(pktSizeBytes) * 8 / duration_post_warmup, in kbps.
"""
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

FLOW_PORTS = {8001: "cam", 8002: "vid", 8003: "bg"}


def _percentile(sorted_vals: Sequence[float], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * q
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = k - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def _pir_stats(times: List[float]) -> Tuple[Optional[float], Optional[float], int]:
    """Return (mean, p95, n_gaps) of inter-arrival times over a sorted list."""
    if len(times) < 2:
        return None, None, 0
    times.sort()
    diffs = [times[i] - times[i - 1] for i in range(1, len(times))]
    diffs.sort()
    return (sum(diffs) / len(diffs), _percentile(diffs, 0.95), len(diffs))


def compute_kpis(db_path: Path,
                 n_ues: int,
                 distances: Optional[List[float]],
                 warmup_s: float,
                 sim_time_s: float,
                 stem: str = "",
                 meta: Optional[Dict] = None) -> Tuple[List[Dict], List[Dict]]:
    """Open db_path and return (run_rows, rx_rows). Connection closed on return."""
    duration = max(sim_time_s, 1e-9)  # post-warmup; sim_time_s == --simTime arg
    meta = meta or {}
    base = {"stem": stem, **meta, "n_ues": n_ues}

    con = sqlite3.connect(str(db_path))
    try:
        # ---- ip <-> nodeId map ----
        ip_to_node: Dict[str, int] = {}
        for ip, node in con.execute(
            "SELECT DISTINCT srcIp, nodeId FROM pktTxRx WHERE txRx='tx'"
        ):
            ip_to_node[str(ip)] = int(node)

        # ---- per-flow tx counts ----
        tx_by_node_flow: Dict[Tuple[int, str], int] = defaultdict(int)
        flow_total_tx: Dict[str, int] = defaultdict(int)
        for node, port, c in con.execute(
            "SELECT nodeId, dstPort, COUNT(*) FROM pktTxRx "
            "WHERE txRx='tx' AND timeSec>? GROUP BY nodeId, dstPort",
            (warmup_s,),
        ):
            f = FLOW_PORTS.get(int(port))
            if f is None:
                continue
            tx_by_node_flow[(int(node), f)] = int(c)
            flow_total_tx[f] += int(c)

        # ---- per-(rx, flow) rx aggregates ----
        rx_by_node_flow: Dict[Tuple[int, str], int] = defaultdict(int)
        rx_bytes_by_node_flow: Dict[Tuple[int, str], int] = defaultdict(int)
        for node, port, c, b in con.execute(
            "SELECT nodeId, dstPort, COUNT(*), COALESCE(SUM(pktSizeBytes),0) "
            "FROM pktTxRx WHERE txRx='rx' AND timeSec>? GROUP BY nodeId, dstPort",
            (warmup_s,),
        ):
            f = FLOW_PORTS.get(int(port))
            if f is None:
                continue
            rx_by_node_flow[(int(node), f)] = int(c)
            rx_bytes_by_node_flow[(int(node), f)] = int(b)

        # ---- PIR: gather rx times per (rx, flow, src) for inter-arrival diffs ----
        rx_times: Dict[Tuple[int, str, str], List[float]] = defaultdict(list)
        for t, node, src_ip, port in con.execute(
            "SELECT timeSec, nodeId, srcIp, dstPort FROM pktTxRx "
            "WHERE txRx='rx' AND timeSec>? ORDER BY nodeId, srcIp, dstPort, timeSec",
            (warmup_s,),
        ):
            f = FLOW_PORTS.get(int(port))
            if f is None:
                continue
            rx_times[(int(node), f, str(src_ip))].append(float(t))

        # ---- HARQ feedback per (rx, optional) ----
        # ATTRIBUTION FIX: slHarqFeedback.rxNodeId == data-Tx (the node whose MAC
        # receives the PSFCH ACK/NACK), NOT the data-Rx.  The feedback sender
        # (= data-Rx) is identified by txRnti.  We resolve txRnti -> nodeId via
        # the pktTxRx IP<->nodeId map already built above.
        #
        # Strategy: build rnti->nodeId from pktTxRx (tx rows carry srcIp which
        # maps to nodeId).  Then key harq_by_rx by the resolved data-Rx nodeId.
        # Fall back to the legacy rxNodeId key if txRnti is 0 / unresolvable so
        # old DBs without the txRnti column still produce valid run-level totals.
        harq_by_rx: Dict[int, Dict[str, int]] = defaultdict(lambda: {"ack": 0, "nack": 0})
        harq_total = {"ack": 0, "nack": 0}
        try:
            # Build rnti->nodeId map from pktTxRx TX rows (srcIp -> nodeId already
            # in ip_to_node; we need rnti -> nodeId from psschTxUeMac if available,
            # else we skip per-rx attribution and only accumulate totals).
            rnti_to_node: Dict[int, int] = {}
            try:
                for rnti, node in con.execute(
                    "SELECT DISTINCT rnti, nodeId FROM psschTxUeMac"
                ):
                    rnti_to_node[int(rnti)] = int(node)
            except sqlite3.OperationalError:
                pass  # table absent — per-rx attribution unavailable

            for txRnti, status, c in con.execute(
                "SELECT txRnti, status, COUNT(*) FROM slHarqFeedback "
                "WHERE timeSec>? GROUP BY txRnti, status", (warmup_s,)
            ):
                s = str(status).strip().upper()
                if s not in ("ACK", "NACK"):
                    continue
                # txRnti = feedback sender = data-Rx RNTI.
                data_rx_node = rnti_to_node.get(int(txRnti), -1)
                if data_rx_node >= 0:
                    harq_by_rx[data_rx_node][s.lower()] += int(c)
                harq_total[s.lower()] += int(c)
        except sqlite3.OperationalError:
            # Legacy fallback: try old rxNodeId column grouping for run-level totals.
            try:
                for rxn, status, c in con.execute(
                    "SELECT rxNodeId, status, COUNT(*) FROM slHarqFeedback "
                    "WHERE timeSec>? GROUP BY rxNodeId, status", (warmup_s,)
                ):
                    s = str(status).strip().upper()
                    if s not in ("ACK", "NACK"):
                        continue
                    harq_total[s.lower()] += int(c)
            except sqlite3.OperationalError:
                pass

        try:
            harq_timeout = con.execute(
                "SELECT COUNT(*) FROM slHarqTimeout WHERE timeSec>?", (warmup_s,)
            ).fetchone()[0]
        except sqlite3.OperationalError:
            harq_timeout = 0

        # ---- PHY-level sanity KPIs (mirror analyze_sweep.py) ----
        try:
            r = con.execute(
                "SELECT SUM(numOverlapping), SUM(totalTx) FROM simulPsschTx WHERE totalTx>0"
            ).fetchone()
            coll_rate = (r[0] / r[1]) if r and r[1] else None
            total_tx_phy = int(r[1]) if r and r[1] else 0
        except sqlite3.OperationalError:
            coll_rate, total_tx_phy = None, 0

        try:
            r = con.execute(
                "SELECT SUM(psschCorrupt), COUNT(*) FROM psschRxUePhy"
            ).fetchone()
            pssch_bler = (r[0] / r[1]) if r and r[1] else None
            pssch_rx_count = int(r[1]) if r else 0
        except sqlite3.OperationalError:
            pssch_bler, pssch_rx_count = None, 0

        try:
            r = con.execute("SELECT AVG(avrgSinr) FROM psschRxUePhy").fetchone()
            sinr_mean_db = (10 * math.log10(r[0])) if r and r[0] and r[0] > 0 else None
        except (sqlite3.OperationalError, ValueError):
            sinr_mean_db = None
    finally:
        con.close()

    # ---- assemble rows ----
    rx_node_ids = sorted({n for (n, _f) in tx_by_node_flow} | {n for (n, _f) in rx_by_node_flow}
                         | set(harq_by_rx.keys()))

    rx_rows: List[Dict] = []
    # PIR diffs per (rx, flow) and pooled per flow.
    diffs_by_rx_flow: Dict[Tuple[int, str], List[float]] = defaultdict(list)
    pir_per_flow_pooled: Dict[str, List[float]] = defaultdict(list)
    for (rxn, f, _src), times in rx_times.items():
        if len(times) < 2:
            continue
        times.sort()
        for i in range(1, len(times)):
            diff = times[i] - times[i - 1]
            diffs_by_rx_flow[(rxn, f)].append(diff)
            pir_per_flow_pooled[f].append(diff)

    for rxn in rx_node_ids:
        d_m = (distances[rxn] if (distances is not None and 0 <= rxn < len(distances)) else None)
        for port, f in FLOW_PORTS.items():
            # Skip flows that carried no TX traffic in this run.
            if flow_total_tx.get(f, 0) == 0:
                continue
            tx_self = tx_by_node_flow.get((rxn, f), 0)
            expected = flow_total_tx[f] - tx_self
            if expected <= 0 and rx_by_node_flow.get((rxn, f), 0) == 0:
                continue
            rx_count = rx_by_node_flow.get((rxn, f), 0)
            rx_bytes = rx_bytes_by_node_flow.get((rxn, f), 0)
            prr = (rx_count / expected) if expected > 0 else None
            tput_kbps = (rx_bytes * 8.0 / duration / 1000.0) if rx_bytes else 0.0
            diffs = sorted(diffs_by_rx_flow.get((rxn, f), []))
            if diffs:
                pir_mean = sum(diffs) / len(diffs)
                pir_p95 = _percentile(diffs, 0.95)
                pir_n = len(diffs)
            else:
                pir_mean, pir_p95, pir_n = None, None, 0
            row = {
                **base,
                "rx_node": rxn, "distance_m": d_m,
                "flow": f, "port": port,
                "expected": expected, "rx_count": rx_count, "rx_bytes": rx_bytes,
                "prr": prr,
                "pir_mean_s": pir_mean, "pir_p95_s": pir_p95, "pir_n_gaps": pir_n,
                "throughput_kbps": tput_kbps,
                "harq_ack": harq_by_rx.get(rxn, {}).get("ack", 0),
                "harq_nack": harq_by_rx.get(rxn, {}).get("nack", 0),
            }
            rx_rows.append(row)

    # ---- run-level (per-flow) ----
    run_rows: List[Dict] = []
    for port, f in FLOW_PORTS.items():
        if flow_total_tx.get(f, 0) == 0:
            continue
        # PRR aggregate: total rx / total expected (sum over rx)
        total_expected = 0
        total_rx = 0
        total_bytes = 0
        for rxn in rx_node_ids:
            tx_self = tx_by_node_flow.get((rxn, f), 0)
            exp = flow_total_tx[f] - tx_self
            if exp <= 0:
                continue
            total_expected += exp
            total_rx += rx_by_node_flow.get((rxn, f), 0)
            total_bytes += rx_bytes_by_node_flow.get((rxn, f), 0)
        diffs = sorted(pir_per_flow_pooled.get(f, []))
        pir_mean = (sum(diffs) / len(diffs)) if diffs else None
        pir_p95 = _percentile(diffs, 0.95) if diffs else None
        run_rows.append({
            **base,
            "flow": f, "port": port,
            "tx_count": flow_total_tx[f],
            "expected": total_expected, "rx_count": total_rx, "rx_bytes": total_bytes,
            "prr": (total_rx / total_expected) if total_expected else None,
            "pir_mean_s": pir_mean, "pir_p95_s": pir_p95, "pir_n_gaps": len(diffs),
            "throughput_kbps": (total_bytes * 8.0 / duration / 1000.0) if total_bytes else 0.0,
            # PHY KPIs (run-wide, not per-flow) — duplicated across flow rows for convenience
            "coll_rate": coll_rate, "total_tx_phy": total_tx_phy,
            "pssch_bler": pssch_bler, "pssch_rx_count": pssch_rx_count,
            "sinr_mean_db": sinr_mean_db,
            "harq_ack": harq_total["ack"], "harq_nack": harq_total["nack"],
            "harq_timeout": harq_timeout,
        })

    return run_rows, rx_rows


# Field lists used by run_sweep.py to write per-run mini CSVs
RUN_FIELDS = [
    "stem", "scen", "cond", "mu", "w", "mcs", "seed", "n_ues",
    "flow", "port", "tx_count", "expected", "rx_count", "rx_bytes",
    "prr", "pir_mean_s", "pir_p95_s", "pir_n_gaps", "throughput_kbps",
    "coll_rate", "total_tx_phy", "pssch_bler", "pssch_rx_count", "sinr_mean_db",
    "harq_ack", "harq_nack", "harq_timeout",
]
RX_FIELDS = [
    "stem", "scen", "cond", "mu", "w", "mcs", "seed", "n_ues",
    "rx_node", "distance_m", "flow", "port",
    "expected", "rx_count", "rx_bytes",
    "prr", "pir_mean_s", "pir_p95_s", "pir_n_gaps", "throughput_kbps",
    "harq_ack", "harq_nack",
]
