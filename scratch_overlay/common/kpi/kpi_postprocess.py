#!/usr/bin/env python3
"""
kpi_postprocess.py
==================
Canonical post-simulation KPI extractor for NR-SL V2X groupcast simulations.

Reads a simulation SQLite DB produced by kpi_hooks.cc and emits a tidy CSV with
one row per (nodeId, flow) containing all required KPIs:

  PRR        — mean / p5 / p95 over sliding-window samples
  PIR        — mean / p5 / p95 over sliding-window inter-arrival samples
  Throughput — mean / p5 / p95 over sliding-window samples (kbit/s)
  AoI        — time-average and peak Age of Information (ms)
  HARQ       — ACK count, NACK count, NACK rate
  Collision  — half-duplex drops, resource-collision failures,
               channel/decode-error failures (all per RX)
  Occupancy  — fraction of PSSCH subchannel-slots used (TX side, per UE)

Plus a roll-up "ALL" row (min/mean/max across UEs) per flow and an "ALL"
flow row for aggregate across flows.

Sampling defaults are tuned for μ=1 numerology (0.5 ms slots):
  --window 1.0   (1 s observation window)
  --step   0.1   (100 ms step → 10 sample points per window width)

Usage
-----
  python3 kpi_postprocess.py --db run.db --out kpis.csv
  python3 kpi_postprocess.py --db run.db --out kpis.csv \\
      --warmup 2.0 --window 1.0 --step 0.1 --range 320 --seed 1 --run 1

Notes on metric definitions
----------------------------
PIR  : consecutive inter-RECEPTION gaps at this receiver from one TX, sampled
       over [window_start, window_start+window] for each sliding step.  Mean of
       those gaps per window → distribution over windows → p5/mean/p95.

AoI  : time-average area under the freshness sawtooth per (RX,TX,flow).
       At each delivery t_rx the age resets to (t_rx - t_gen).  Between
       deliveries the age grows linearly.  Peak AoI is the maximum freshness
       age observed just before any update.

HD   : half-duplex — slots where this RX node was simultaneously transmitting
       on a resource that overlaps (frame,subFrame,slot) with an expected
       reception from another TX within --range.  Derived entirely from
       psschTxUeMac without editing ns-3.

Resource-collision: psschRxUePhy rows where psschCorrupt=1 AND a concurrent
       overlapping TX grant from a *different* rnti exists in psschTxUeMac.

Channel-error: psschRxUePhy rows where psschCorrupt=1 AND no concurrent
       overlapping grant → low-SINR / fade corruption.
"""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
import warnings
from collections import defaultdict
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Flow port → label mapping (matches kpi_hooks / groupcast_flows)
# ---------------------------------------------------------------------------

FLOW_PORTS: Dict[int, str] = {
    8001: "cam",
    8002: "vid",
    8003: "bg",
}

FLOW_LABELS = list(FLOW_PORTS.values())

# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------

def _percentile(sorted_vals: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolation percentile of a *pre-sorted* list, q in [0,1]."""
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    k = (n - 1) * q
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(sorted_vals[lo])
    return float(sorted_vals[lo] * (1.0 - (k - lo)) + sorted_vals[hi] * (k - lo))


def _safe_mean(vals: Sequence[float]) -> Optional[float]:
    return float(sum(vals) / len(vals)) if vals else None


def _sliding_samples(
    events: List[float],   # sorted event times in seconds
    t_start: float,        # window start for first sample
    t_end: float,          # wall-clock end of measurement period
    window: float,         # window width (s)
    step: float,           # step (s)
) -> List[float]:
    """
    Compute a statistic over sliding windows.

    For PRR / throughput the caller passes one float per event (e.g. bytes),
    and this function returns a *count* per window (caller normalises).

    For PIR the caller should use _pir_sliding_samples below.
    """
    samples: List[float] = []
    t = t_start
    while t + window <= t_end + 1e-9:
        count = sum(1 for e in events if t <= e < t + window)
        samples.append(float(count))
        t += step
    return samples


def _pir_sliding_samples(
    rx_times: List[float],   # sorted RX timestamps (s)
    t_start: float,
    t_end: float,
    window: float,
    step: float,
) -> List[float]:
    """
    Sliding-window mean PIR: within each window collect the inter-arrival
    gaps between consecutive receptions, return the mean gap (s).
    Returns one value per window (skips windows with <2 events).
    """
    samples: List[float] = []
    t = t_start
    while t + window <= t_end + 1e-9:
        pkts = [r for r in rx_times if t <= r < t + window]
        if len(pkts) >= 2:
            pkts.sort()
            gaps = [pkts[i] - pkts[i - 1] for i in range(1, len(pkts))]
            samples.append(sum(gaps) / len(gaps))
        t += step
    return samples


def _aoi_stats(
    gen_times: List[float],   # packet generation (TX) times (s), one per seq
    rx_times: List[float],    # corresponding RX times (s); same length
    t_start: float,
    t_end: float,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Time-average and peak AoI (ms) over [t_start, t_end].

    Uses the trapezoidal sawtooth area:
      At delivery i (time r_i, generation g_i):
        - Age just after delivery: a_i = r_i - g_i
        - Age just before next delivery (r_{i+1}): a_peak_i = r_{i+1} - g_i
        - Contribution to area: 0.5 * (a_i + a_peak_i) * (r_{i+1} - r_i)

    gen_times and rx_times must be the same length and sorted by rx_times.
    """
    if len(gen_times) < 2 or len(gen_times) != len(rx_times):
        return None, None

    # Filter to the measurement window.
    pairs = [(g, r) for g, r in zip(gen_times, rx_times) if t_start <= r <= t_end]
    if len(pairs) < 2:
        return None, None

    pairs.sort(key=lambda x: x[1])  # sort by rx time

    total_area = 0.0
    peak = 0.0

    for i in range(len(pairs) - 1):
        g_i, r_i = pairs[i]
        _, r_next = pairs[i + 1]
        age_i = r_i - g_i                # age just after i-th delivery
        age_peak = r_next - g_i          # age just before (i+1)-th delivery
        if age_i < 0 or age_peak < 0:
            warnings.warn(f"Negative AoI (g={g_i:.3f} r={r_i:.3f}); skipping pair")
            continue
        peak = max(peak, age_peak)
        total_area += 0.5 * (age_i + age_peak) * (r_next - r_i)

    duration = t_end - t_start
    if duration <= 0:
        return None, None
    mean_aoi_ms = (total_area / duration) * 1000.0
    peak_aoi_ms = peak * 1000.0
    return mean_aoi_ms, peak_aoi_ms


# ---------------------------------------------------------------------------
# DB schema helpers
# ---------------------------------------------------------------------------

def _ensure_indexes(con: sqlite3.Connection) -> None:
    """Create covering indexes on the most-queried columns if they don't exist.

    These speed up the per-link queries by 10-100x on large DBs (800k+ rows).
    SQLite will reuse existing indexes; creating them is idempotent.
    """
    index_defs = [
        ("idx_pkttxrx_tx",    "pktTxRx",
         "(txRx, nodeId, dstPort, SEED, RUN, timeSec)"),
        ("idx_pkttxrx_rx",    "pktTxRx",
         "(txRx, nodeId, srcIp, dstPort, SEED, RUN, timeSec)"),
        ("idx_psschrx_rnti",  "psschRxUePhy",
         "(rnti, txRnti, psschCorrupt, SEED, RUN, timeMs)"),
        ("idx_psschttx_rnti", "psschTxUeMac",
         "(rnti, SEED, RUN, timeMs)"),
        ("idx_psschttx_slot", "psschTxUeMac",
         "(frame, subFrame, slot, SEED, RUN)"),
        ("idx_harq_rxrnti",   "slHarqFeedback",
         "(rxRnti, SEED, RUN, timeSec)"),
    ]
    for idx_name, table, cols in index_defs:
        if _has_table(con, table):
            try:
                con.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} {cols};"
                )
            except sqlite3.OperationalError:
                pass  # read-only DB or index already exists under different schema
    con.commit()


def _has_table(con: sqlite3.Connection, table: str) -> bool:
    cur = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?;", (table,)
    )
    return cur.fetchone()[0] > 0


def _has_column(con: sqlite3.Connection, table: str, col: str) -> bool:
    cur = con.execute(f"PRAGMA table_info({table});")
    return any(row[1] == col for row in cur.fetchall())


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------

@dataclass
class NodeMeta:
    node_id: int
    imsi: int
    ip: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rnti: Optional[int] = None   # resolved from rlcRx bridge


def _build_identity_map(
    con: sqlite3.Connection, seed: int, run: int
) -> Dict[int, NodeMeta]:
    """
    Build nodeId → NodeMeta from the nodeInfo table (requires C++ Flush to
    have been called with the updated code).  Falls back to a best-effort
    reconstruction from pktTxRx if nodeInfo is absent or empty.

    The rnti is resolved by cross-joining with rlcRx:
        rlcRx.imsi → rlcRx.rnti   (the receiving UE's rnti)
    This is a bijection in single-cell sidelink setups.
    """
    meta: Dict[int, NodeMeta] = {}

    if _has_table(con, "nodeInfo"):
        rows = con.execute(
            "SELECT nodeId, imsi, ip, x, y, z FROM nodeInfo "
            "WHERE SEED=? AND RUN=?;",
            (seed, run),
        ).fetchall()
        for node_id, imsi, ip, x, y, z in rows:
            meta[int(node_id)] = NodeMeta(
                node_id=int(node_id),
                imsi=int(imsi),
                ip=str(ip),
                x=float(x),
                y=float(y),
                z=float(z),
            )
    else:
        warnings.warn(
            "nodeInfo table not found — positions unavailable; "
            "reconstructing from pktTxRx (no positions, limited rnti)."
        )

    if not meta and _has_table(con, "pktTxRx"):
        # Fallback: build from pktTxRx TX rows.
        rows = con.execute(
            "SELECT DISTINCT nodeId, imsi, srcIp FROM pktTxRx "
            "WHERE txRx='tx' AND SEED=? AND RUN=?;",
            (seed, run),
        ).fetchall()
        for node_id, imsi, ip in rows:
            meta[int(node_id)] = NodeMeta(
                node_id=int(node_id), imsi=int(imsi), ip=str(ip)
            )

    # Resolve rnti via rlcRx (covers RX nodes) and psschTxUeMac (covers TX-only nodes).
    imsi_to_rnti: Dict[int, int] = {}
    if _has_table(con, "rlcRx"):
        rows = con.execute(
            "SELECT DISTINCT imsi, rnti FROM rlcRx WHERE SEED=? AND RUN=?;",
            (seed, run),
        ).fetchall()
        for imsi, rnti in rows:
            imsi_to_rnti[int(imsi)] = int(rnti)
    # Also pull from psschTxUeMac so TX-only nodes (leader, relay) get their rnti.
    if _has_table(con, "psschTxUeMac"):
        rows = con.execute(
            "SELECT DISTINCT imsi, rnti FROM psschTxUeMac WHERE SEED=? AND RUN=?;",
            (seed, run),
        ).fetchall()
        for imsi, rnti in rows:
            imsi_to_rnti.setdefault(int(imsi), int(rnti))
    for nm in meta.values():
        nm.rnti = imsi_to_rnti.get(nm.imsi)

    return meta


# ---------------------------------------------------------------------------
# PRR / PIR / Throughput computation
# ---------------------------------------------------------------------------

def _prr_pir_thput(
    con: sqlite3.Connection,
    seed: int,
    run: int,
    tx_node_id: int,
    tx_ip: str,
    rx_node_id: int,
    flow_port: int,
    t_start: float,
    t_end: float,
    window: float,
    step: float,
) -> Dict:
    """Return dict of prr/pir/thput stats for one (tx→rx, flow) link."""

    # TX times (generation times for this flow from this TX).
    tx_rows = con.execute(
        "SELECT timeSec FROM pktTxRx "
        "WHERE txRx='tx' AND nodeId=? AND dstPort=? AND SEED=? AND RUN=? "
        "AND timeSec>=? AND timeSec<=? ORDER BY timeSec;",
        (tx_node_id, flow_port, seed, run, t_start, t_end),
    ).fetchall()
    tx_times = [r[0] for r in tx_rows]

    # RX times (deliveries at this RX from this TX IP).
    rx_rows = con.execute(
        "SELECT r.timeSec, r.pktSeqNum "
        "FROM pktTxRx r "
        "WHERE r.txRx='rx' AND r.nodeId=? AND r.srcIp=? AND r.dstPort=? "
        "AND r.SEED=? AND r.RUN=? "
        "AND r.timeSec>=? AND r.timeSec<=? ORDER BY r.timeSec;",
        (rx_node_id, tx_ip, flow_port, seed, run, t_start, t_end),
    ).fetchall()
    rx_times = [r[0] for r in rx_rows]
    rx_seqs = set(r[1] for r in rx_rows)

    # TX seq numbers for denominator (PRR).
    tx_seq_rows = con.execute(
        "SELECT pktSeqNum, timeSec, pktSizeBytes FROM pktTxRx "
        "WHERE txRx='tx' AND nodeId=? AND dstPort=? AND SEED=? AND RUN=? "
        "AND timeSec>=? AND timeSec<=? ORDER BY timeSec;",
        (tx_node_id, flow_port, seed, run, t_start, t_end),
    ).fetchall()
    tx_seq_map = {r[0]: (r[1], r[2]) for r in tx_seq_rows}  # seq → (time, bytes)

    # ---- Sliding-window PRR samples ----
    # Each sample = fraction of TX packets within window that were received.
    prr_samples: List[float] = []
    t = t_start
    while t + window <= t_end + 1e-9:
        tx_in_w = {seq for seq, (ts, _) in tx_seq_map.items() if t <= ts < t + window}
        if tx_in_w:
            rx_in_w = tx_in_w & rx_seqs
            prr_samples.append(len(rx_in_w) / len(tx_in_w))
        t += step

    # ---- Sliding-window PIR samples ----
    pir_samples = _pir_sliding_samples(rx_times, t_start, t_end, window, step)

    # ---- Sliding-window throughput samples (kbit/s) ----
    # bytes received in window / window_width * 8 / 1000
    thput_samples: List[float] = []
    # Build rx bytes map: seq → pktSizeBytes
    rx_seq_bytes = {}
    if rx_seqs:
        rx_detail = con.execute(
            "SELECT pktSeqNum, pktSizeBytes, timeSec FROM pktTxRx "
            "WHERE txRx='rx' AND nodeId=? AND srcIp=? AND dstPort=? "
            "AND SEED=? AND RUN=? AND timeSec>=? AND timeSec<=?;",
            (rx_node_id, tx_ip, flow_port, seed, run, t_start, t_end),
        ).fetchall()
        rx_seq_bytes = {r[0]: (r[1], r[2]) for r in rx_detail}

    t = t_start
    while t + window <= t_end + 1e-9:
        rx_bytes = sum(
            sz for seq, (sz, ts) in rx_seq_bytes.items() if t <= ts < t + window
        )
        thput_kbps = rx_bytes * 8.0 / window / 1000.0
        thput_samples.append(thput_kbps)
        t += step

    def _stats(samples: List[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        if not samples:
            return None, None, None
        s = sorted(samples)
        return _safe_mean(s), _percentile(s, 0.05), _percentile(s, 0.95)

    prr_mean, prr_p5, prr_p95 = _stats(prr_samples)
    pir_mean, pir_p5, pir_p95 = _stats(pir_samples)
    thput_mean, thput_p5, thput_p95 = _stats(thput_samples)

    # Convert PIR from seconds to ms.
    def _ms(v):
        return v * 1000.0 if v is not None else None

    return {
        "n_tx": len(tx_seq_map),
        "n_rx": len(rx_seqs),
        "prr_mean": prr_mean,
        "prr_p5": prr_p5,
        "prr_p95": prr_p95,
        "pir_mean_ms": _ms(pir_mean),
        "pir_p5_ms": _ms(pir_p5),
        "pir_p95_ms": _ms(pir_p95),
        "thput_mean_kbps": thput_mean,
        "thput_p5_kbps": thput_p5,
        "thput_p95_kbps": thput_p95,
        "tx_times": tx_times,
        "rx_times": rx_times,
        "rx_seqs": rx_seqs,
    }


# ---------------------------------------------------------------------------
# AoI computation
# ---------------------------------------------------------------------------

def _aoi_for_link(
    con: sqlite3.Connection,
    seed: int,
    run: int,
    tx_node_id: int,
    tx_ip: str,
    rx_node_id: int,
    flow_port: int,
    t_start: float,
    t_end: float,
) -> Tuple[Optional[float], Optional[float]]:
    """
    AoI (mean_ms, peak_ms) for one (tx→rx, flow) link.

    Uses appPktSeq when available (preferred — has accurate sequence numbers
    at the relay) and falls back to pktTxRx join.
    """
    # Build gen_time map: seq → TX timestamp from the *transmitter*.
    tx_rows = con.execute(
        "SELECT pktSeqNum, timeSec FROM pktTxRx "
        "WHERE txRx='tx' AND nodeId=? AND dstPort=? AND SEED=? AND RUN=? "
        "AND timeSec>=? AND timeSec<=?;",
        (tx_node_id, flow_port, seed, run, t_start, t_end),
    ).fetchall()
    gen_map: Dict[int, float] = {int(r[0]): float(r[1]) for r in tx_rows}

    # RX events.
    rx_rows = con.execute(
        "SELECT pktSeqNum, timeSec FROM pktTxRx "
        "WHERE txRx='rx' AND nodeId=? AND srcIp=? AND dstPort=? "
        "AND SEED=? AND RUN=? AND timeSec>=? AND timeSec<=? "
        "ORDER BY timeSec;",
        (rx_node_id, tx_ip, flow_port, seed, run, t_start, t_end),
    ).fetchall()

    gen_times: List[float] = []
    rx_times: List[float] = []
    for seq, rx_t in rx_rows:
        g = gen_map.get(int(seq))
        if g is not None:
            gen_times.append(g)
            rx_times.append(float(rx_t))

    return _aoi_stats(gen_times, rx_times, t_start, t_end)


# ---------------------------------------------------------------------------
# HARQ stats
# ---------------------------------------------------------------------------

def _harq_stats(
    con: sqlite3.Connection,
    seed: int,
    run: int,
    rx_rnti: Optional[int],
    t_start: float,
    t_end: float,
) -> Dict:
    """ACK/NACK counts and rate for a RX node (by rnti).

    slHarqFeedback has a rxRnti column (the receiving UE's RNTI).
    We query by rxRnti directly — no subquery needed.
    NR-SL groupcast with option 1 uses NACK-only; ACK count may be 0.
    """
    if not _has_table(con, "slHarqFeedback") or rx_rnti is None:
        return {"harq_ack": None, "harq_nack": None, "harq_nack_rate": None}

    # Count per-status directly — avoids loading all rows into Python.
    row = con.execute(
        "SELECT "
        "  SUM(CASE WHEN status='ACK'  THEN 1 ELSE 0 END), "
        "  SUM(CASE WHEN status='NACK' THEN 1 ELSE 0 END) "
        "FROM slHarqFeedback "
        "WHERE rxRnti=? AND timeSec>=? AND timeSec<=? AND SEED=? AND RUN=?;",
        (rx_rnti, t_start, t_end, seed, run),
    ).fetchone()

    ack = int(row[0] or 0)
    nack = int(row[1] or 0)
    total = ack + nack
    nack_rate = nack / total if total > 0 else None
    return {"harq_ack": ack, "harq_nack": nack, "harq_nack_rate": nack_rate}


# ---------------------------------------------------------------------------
# Collision attribution (half-duplex / resource-collision / channel-error)
# NO ns-3 edits: derived entirely from psschTxUeMac + psschRxUePhy
# ---------------------------------------------------------------------------

def _distance_2d(x1, y1, x2, y2) -> float:
    return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)


def _collision_stats(
    con: sqlite3.Connection,
    seed: int,
    run: int,
    rx_meta: NodeMeta,
    tx_meta: NodeMeta,
    t_start: float,
    t_end: float,
    range_m: Optional[float],
) -> Dict:
    """
    Return per-link collision attribution for (tx_meta → rx_meta).

    Half-duplex (HD):
        Slots where rx_meta was simultaneously *transmitting* on the same
        (frame, subFrame, slot) that tx_meta was also transmitting — meaning
        rx_meta could not have received that tx_meta packet (it was TX-deaf).
        Derived from a self-join on psschTxUeMac where both rnti differ.
        Range-gated if range_m is given.

    Resource-collision (RC):
        psschRxUePhy rows for rx_rnti receiving from tx_rnti where
        psschCorrupt=1 AND a concurrent overlapping psschTxUeMac grant from
        a third rnti (≠tx_rnti, ≠rx_rnti) exists in the same slot+RB range.

    Channel-error (CE):
        psschRxUePhy rows where psschCorrupt=1 AND no such concurrent overlap
        exists → attributed to low SINR / fading.

    Returns dict with: hd_count, rc_count, ce_count, total_corrupt.
    """
    result = {
        "hd_count": 0,
        "rc_count": 0,
        "ce_count": 0,
        "total_corrupt": 0,
    }

    rx_rnti = rx_meta.rnti
    tx_rnti = tx_meta.rnti

    if not _has_table(con, "psschTxUeMac") or not _has_table(con, "psschRxUePhy"):
        return result

    t_start_ms = t_start * 1000.0
    t_end_ms = t_end * 1000.0

    # ---- Half-duplex: count slots where rx was also transmitting ----
    # Logic: find (frame,subFrame,slot) in psschTxUeMac where tx_rnti transmitted
    # AND rx_rnti ALSO transmitted in the same (frame,subFrame,slot).
    # These are slots where rx_meta could not have received the tx_meta packet.
    # We count distinct (frame,subFrame,slot) tuples.
    if rx_rnti is not None and tx_rnti is not None:
        # Range gate: only consider if rx is within range of tx.
        in_range = True
        if range_m is not None and (rx_meta.x, rx_meta.y) != (0.0, 0.0):
            d = _distance_2d(rx_meta.x, rx_meta.y, tx_meta.x, tx_meta.y)
            in_range = d <= range_m

        if in_range:
            hd_rows = con.execute(
                """
                SELECT COUNT(DISTINCT tx.frame || ',' || tx.subFrame || ',' || tx.slot)
                FROM psschTxUeMac tx
                JOIN psschTxUeMac rx_tx
                  ON tx.frame=rx_tx.frame AND tx.subFrame=rx_tx.subFrame
                 AND tx.slot=rx_tx.slot
                WHERE tx.rnti=? AND rx_tx.rnti=?
                  AND tx.timeMs>=? AND tx.timeMs<=?
                  AND tx.SEED=? AND tx.RUN=?
                  AND rx_tx.SEED=tx.SEED AND rx_tx.RUN=tx.RUN;
                """,
                (tx_rnti, rx_rnti, t_start_ms, t_end_ms, seed, run),
            ).fetchone()
            result["hd_count"] = hd_rows[0] if hd_rows else 0

    # ---- Corrupt RX rows (resource-collision vs channel-error) ----
    if rx_rnti is None or tx_rnti is None:
        return result

    corrupt_rows = con.execute(
        """
        SELECT frame, subFrame, slot, psschRbStart, psschRbLen,
               psschSymStart, psschSymLen
        FROM psschRxUePhy
        WHERE rnti=? AND txRnti=? AND psschCorrupt=1
          AND timeMs>=? AND timeMs<=? AND SEED=? AND RUN=?;
        """,
        (rx_rnti, tx_rnti, t_start_ms, t_end_ms, seed, run),
    ).fetchall()

    result["total_corrupt"] = len(corrupt_rows)

    # For each corrupt row, check if a 3rd-party TX was active in same slot+RB.
    # A 3rd-party TX = psschTxUeMac row with rnti ≠ rx_rnti ≠ tx_rnti in same
    # (frame,subFrame,slot) with overlapping (rbStart,rbLen).
    # Build a set of (frame,subFrame,slot,rbStart,rbLen) from all 3rd-party TXers.
    third_party_grants = con.execute(
        """
        SELECT frame, subFrame, slot, rbStart, rbLen
        FROM psschTxUeMac
        WHERE rnti!=? AND rnti!=?
          AND timeMs>=? AND timeMs<=?
          AND SEED=? AND RUN=?;
        """,
        (tx_rnti, rx_rnti, t_start_ms, t_end_ms, seed, run),
    ).fetchall()

    # Index by (frame,subFrame,slot) for fast lookup.
    slot_to_grants: Dict[Tuple, List] = defaultdict(list)
    for f, sf, sl, rb_s, rb_l in third_party_grants:
        slot_to_grants[(f, sf, sl)].append((rb_s, rb_l))

    for f, sf, sl, rb_s, rb_l, sym_s, sym_l in corrupt_rows:
        key = (f, sf, sl)
        grants = slot_to_grants.get(key, [])
        collided = False
        for g_rb_s, g_rb_l in grants:
            # Check RB overlap: [rb_s, rb_s+rb_l) ∩ [g_rb_s, g_rb_s+g_rb_l) ≠ ∅
            if rb_s < g_rb_s + g_rb_l and g_rb_s < rb_s + rb_l:
                collided = True
                break
        if collided:
            result["rc_count"] += 1
        else:
            result["ce_count"] += 1

    return result


# ---------------------------------------------------------------------------
# Channel occupancy (TX side, per UE)
# ---------------------------------------------------------------------------

def _channel_occupancy(
    con: sqlite3.Connection,
    seed: int,
    run: int,
    tx_rnti: Optional[int],
    t_start: float,
    t_end: float,
) -> Optional[float]:
    """
    Fraction of PSSCH subchannel-slots used by this TX in [t_start, t_end].

    subchannel_slots_used = Σ (rbLen / sbChSize) over all grants
    total_pssch_subch_slots = pssch_eligible_slots × n_subchannels

    Uses μ=1 (0.5 ms/slot) if max slot index ≤1, else μ=0 (1 ms/slot).
    Skips PSFCH slots (1 in every psfchPeriod=4 by default).
    """
    if not _has_table(con, "psschTxUeMac") or tx_rnti is None:
        return None

    t_start_ms = t_start * 1000.0
    t_end_ms = t_end * 1000.0

    # Determine slot duration from max subframe slot index.
    max_slot_row = con.execute(
        "SELECT MAX(slot) FROM psschTxUeMac WHERE SEED=? AND RUN=?;", (seed, run)
    ).fetchone()
    max_slot = max_slot_row[0] if max_slot_row and max_slot_row[0] is not None else 0
    slot_dur_ms = 0.5 if max_slot >= 1 else 1.0

    # Sub-channel size from grants of this TX.
    sb_row = con.execute(
        "SELECT sbChSize FROM psschTxUeMac WHERE rnti=? AND SEED=? AND RUN=? LIMIT 1;",
        (tx_rnti, seed, run),
    ).fetchone()
    if sb_row is None:
        return None
    sb_ch_size = sb_row[0]
    if sb_ch_size == 0:
        return None

    # Total sub-channels in pool.
    tot_sb_row = con.execute(
        "SELECT totSbCh FROM pscchTxUeMac WHERE SEED=? AND RUN=? LIMIT 1;", (seed, run)
    ).fetchone() if _has_table(con, "pscchTxUeMac") else None
    if tot_sb_row is None:
        # Estimate from max rbLen / sbChSize across all grants.
        est = con.execute(
            "SELECT MAX(rbLen)/? FROM psschTxUeMac WHERE SEED=? AND RUN=?;",
            (sb_ch_size, seed, run),
        ).fetchone()
        n_subch = int(est[0]) if est and est[0] else 1
    else:
        n_subch = int(tot_sb_row[0])

    # Active window.
    active_ms = t_end_ms - t_start_ms
    psfch_period = 4  # standard NR-SL pool; could be made a CLI arg
    total_slots = int(active_ms / slot_dur_ms)
    psfch_slots = total_slots // psfch_period
    pssch_slots = total_slots - psfch_slots
    total_subch_slots = pssch_slots * n_subch
    if total_subch_slots == 0:
        return None

    # Sum subchannel-slots used by this TX.
    grants = con.execute(
        "SELECT rbLen FROM psschTxUeMac "
        "WHERE rnti=? AND timeMs>=? AND timeMs<=? AND SEED=? AND RUN=?;",
        (tx_rnti, t_start_ms, t_end_ms, seed, run),
    ).fetchall()
    used_subch_slots = sum(int(row[0]) // sb_ch_size for row in grants)

    return min(1.0, used_subch_slots / total_subch_slots)


# ---------------------------------------------------------------------------
# Main per-(UE, flow) row builder
# ---------------------------------------------------------------------------

@dataclass
class KpiRow:
    """One output row: per (rx_node_id, tx_node_id, flow)."""
    # Identity
    rx_node_id: int = 0
    tx_node_id: int = 0
    rx_imsi: int = 0
    tx_imsi: int = 0
    rx_ip: str = ""
    tx_ip: str = ""
    flow: str = ""
    seed: int = 0
    run: int = 0

    # Packet counts
    n_tx: Optional[int] = None
    n_rx: Optional[int] = None

    # PRR
    prr_mean: Optional[float] = None
    prr_p5: Optional[float] = None
    prr_p95: Optional[float] = None

    # PIR (ms)
    pir_mean_ms: Optional[float] = None
    pir_p5_ms: Optional[float] = None
    pir_p95_ms: Optional[float] = None

    # Throughput (kbps)
    thput_mean_kbps: Optional[float] = None
    thput_p5_kbps: Optional[float] = None
    thput_p95_kbps: Optional[float] = None

    # AoI (ms)
    aoi_mean_ms: Optional[float] = None
    aoi_peak_ms: Optional[float] = None

    # HARQ
    harq_ack: Optional[int] = None
    harq_nack: Optional[int] = None
    harq_nack_rate: Optional[float] = None

    # Collision attribution
    hd_count: Optional[int] = None           # half-duplex deaf slots
    rc_count: Optional[int] = None           # resource-collision corrupt
    ce_count: Optional[int] = None           # channel/decode-error corrupt
    total_corrupt: Optional[int] = None      # total corrupt PHY decodes

    # Channel occupancy (TX side) — fraction of pool used (existing, unchanged).
    ch_occupancy: Optional[float] = None

    # ---- Absolute subchannel×slot counters (backward-compat add for resource study) ----
    # Computed from psschTxUeMac grants in the measurement window.
    # subch_slots_used    = Σ (rbLen / sbChSize) — total subchannel-slots consumed by this TX
    # subch_slots_total   = pssch_eligible_slots * n_subch — total subchannel-slots in the pool
    # subch_slots_per_pkt = subch_slots_used / n_tx — resource per emitted packet
    # All three are None for rows without TX-side context (e.g. RX-only flow rows).
    subch_slots_used: Optional[int] = None
    subch_slots_total: Optional[int] = None
    subch_slots_per_pkt: Optional[float] = None

    # ---- worst / best aggregates (relay_storm_eval additions) ----
    # PRR: worst = min, best = max
    prr_worst: Optional[float] = None
    prr_best: Optional[float] = None
    # PIR (ms): worst = max, best = min
    pir_worst_ms: Optional[float] = None
    pir_best_ms: Optional[float] = None
    # Throughput (kbps): worst = min, best = max
    thput_worst_kbps: Optional[float] = None
    thput_best_kbps: Optional[float] = None
    # AoI (ms): worst = max already captured by aoi_peak; best = min
    aoi_best_ms: Optional[float] = None

    # ---- Per-hop reTX counts (psschTxUeMac, ndi=0 rows, grouped by TX RNTI) ----
    # hop1_retx = retransmissions by leader (node 0 / min-nodeId TX)
    # hop2_retx = retransmissions by relay node
    hop1_retx: Optional[int] = None
    hop2_retx: Optional[int] = None

    # ---- Time-series export flag (rows with window_t set are time-series rows) ----
    # When window_t is not None this row represents a single time window, not
    # the aggregate. Aggregate rows have window_t = None.
    window_t: Optional[float] = None


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _row_to_dict(row: KpiRow) -> Dict:
    return {f.name: _fmt(getattr(row, f.name)) for f in fields(row)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def compute_kpis(
    db_path: Path,
    out_path: Path,
    warmup: float,
    sim_end: Optional[float],
    window: float,
    step: float,
    range_m: Optional[float],
    seed: int,
    run: int,
) -> List[KpiRow]:
    """
    Main computation driver.  Returns list of KpiRow (also written to out_path).

    Strategy: bulk-load each table into Python structures once, then iterate
    over (tx_node, rx_node, flow) links in memory.  This is 10-100x faster than
    issuing per-link SQL queries against large unindexed tables.
    """
    con = sqlite3.connect(str(db_path))

    # Determine measurement window.
    t_start = warmup
    if sim_end is not None:
        t_end = sim_end
    else:
        row = con.execute(
            "SELECT MAX(timeSec) FROM pktTxRx WHERE txRx='tx' AND SEED=? AND RUN=?;",
            (seed, run),
        ).fetchone()
        t_end = float(row[0]) if row and row[0] is not None else warmup + 1.0
    if t_end <= t_start:
        raise ValueError(f"Measurement window empty: t_start={t_start} t_end={t_end}")
    t_start_ms = t_start * 1000.0
    t_end_ms = t_end * 1000.0

    # Identity map (uses nodeInfo when available, falls back to pktTxRx).
    con.row_factory = sqlite3.Row
    meta = _build_identity_map(con, seed, run)
    con.row_factory = None
    if not meta:
        print(f"[WARN] No UE identity info found in {db_path}; output will be empty.",
              file=sys.stderr)
        con.close()
        return []
    ip_to_node: Dict[str, NodeMeta] = {nm.ip: nm for nm in meta.values()}

    # ------------------------------------------------------------------ #
    # Bulk load 1: pktTxRx — all TX and RX events in measurement window  #
    # ------------------------------------------------------------------ #
    print(f"[kpi_postprocess] Loading pktTxRx ...")
    # tx_events[(tx_node_id, port)] = list of (timeSec, seq, pktSizeBytes)
    tx_events: Dict[Tuple[int,int], List[Tuple[float,int,int]]] = defaultdict(list)
    # tx_ip[(tx_node_id)] = srcIp string
    tx_node_ip: Dict[int, str] = {}
    # rx_events[(rx_node_id, src_ip, port)] = list of (timeSec, seq, pktSizeBytes)
    rx_events: Dict[Tuple[int,str,int], List[Tuple[float,int,int]]] = defaultdict(list)

    for row in con.execute(
        "SELECT timeSec, txRx, nodeId, srcIp, dstPort, pktSeqNum, pktSizeBytes "
        "FROM pktTxRx WHERE SEED=? AND RUN=? AND timeSec>=? AND timeSec<=? "
        "ORDER BY timeSec;",
        (seed, run, t_start, t_end),
    ):
        t, txrx, nid, sip, port, seq, sz = (
            float(row[0]), str(row[1]), int(row[2]),
            str(row[3]), int(row[4]), int(row[5]), int(row[6])
        )
        if txrx == "tx":
            tx_events[(nid, port)].append((t, seq, sz))
            tx_node_ip[nid] = sip
        else:  # rx
            rx_events[(nid, sip, port)].append((t, seq, sz))

    # ------------------------------------------------------------------ #
    # Bulk load 2: HARQ — per rxRnti ACK/NACK counts                     #
    # ------------------------------------------------------------------ #
    print(f"[kpi_postprocess] Loading slHarqFeedback ...")
    # harq_counts[rx_rnti] = (ack_count, nack_count)
    harq_counts: Dict[int, Tuple[int,int]] = {}
    if _has_table(con, "slHarqFeedback"):
        harq_has_seed = _has_column(con, "slHarqFeedback", "SEED")
        if harq_has_seed:
            harq_where = "WHERE SEED=? AND RUN=? AND timeSec>=? AND timeSec<=?"
            harq_params = (seed, run, t_start, t_end)
        else:
            harq_where = "WHERE timeSec>=? AND timeSec<=?"
            harq_params = (t_start, t_end)
        for row in con.execute(
            f"SELECT rxRnti, "
            f"  SUM(CASE WHEN status='ACK'  THEN 1 ELSE 0 END), "
            f"  SUM(CASE WHEN status='NACK' THEN 1 ELSE 0 END) "
            f"FROM slHarqFeedback {harq_where} GROUP BY rxRnti;",
            harq_params,
        ):
            harq_counts[int(row[0])] = (int(row[1] or 0), int(row[2] or 0))

    # ------------------------------------------------------------------ #
    # Bulk load 3: psschTxUeMac — for HD and channel occupancy            #
    # ------------------------------------------------------------------ #
    print(f"[kpi_postprocess] Loading psschTxUeMac ...")
    # tx_grants[rnti] = list of (timeMs, frame, subFrame, slot, rbStart, rbLen, sbChSize)
    tx_grants: Dict[int, List[Tuple]] = defaultdict(list)
    slot_grants: Dict[Tuple[int,int,int], List[Tuple[int,int,int]]] = defaultdict(list)
    # slot_grants[(frame,sf,slot)] = list of (rnti, rbStart, rbLen)
    pool_info: Optional[Tuple[int,int,int]] = None  # (sbChSize, totSbCh, max_slot)
    if _has_table(con, "psschTxUeMac"):
        for row in con.execute(
            "SELECT timeMs, rnti, frame, subFrame, slot, rbStart, rbLen, sbChSize "
            "FROM psschTxUeMac WHERE SEED=? AND RUN=? AND timeMs>=? AND timeMs<=?;",
            (seed, run, t_start_ms, t_end_ms),
        ):
            tms, rnti, fr, sf, sl, rb_s, rb_l, sbch = (
                float(row[0]), int(row[1]), int(row[2]), int(row[3]),
                int(row[4]), int(row[5]), int(row[6]), int(row[7])
            )
            tx_grants[rnti].append((tms, fr, sf, sl, rb_s, rb_l, sbch))
            slot_grants[(fr, sf, sl)].append((rnti, rb_s, rb_l))
            if pool_info is None:
                pool_info = (sbch, 0, sl)
            else:
                pool_info = (pool_info[0], pool_info[1], max(pool_info[2], sl))

        # Get totSbCh from pscchTxUeMac if available.
        if _has_table(con, "pscchTxUeMac"):
            r = con.execute(
                "SELECT totSbCh FROM pscchTxUeMac WHERE SEED=? AND RUN=? LIMIT 1;",
                (seed, run)
            ).fetchone()
            if r and pool_info:
                pool_info = (pool_info[0], int(r[0]), pool_info[2])

    # ------------------------------------------------------------------ #
    # Bulk load 4: psschRxUePhy — corrupt rows for collision attribution  #
    # ------------------------------------------------------------------ #
    print(f"[kpi_postprocess] Loading psschRxUePhy ...")
    # corrupt_rx[(rx_rnti, tx_rnti)] = list of (frame, sf, slot, rbStart, rbLen)
    corrupt_rx: Dict[Tuple[int,int], List[Tuple[int,int,int,int,int]]] = defaultdict(list)
    if _has_table(con, "psschRxUePhy"):
        for row in con.execute(
            "SELECT rnti, txRnti, frame, subFrame, slot, psschRbStart, psschRbLen "
            "FROM psschRxUePhy "
            "WHERE psschCorrupt=1 AND SEED=? AND RUN=? AND timeMs>=? AND timeMs<=?;",
            (seed, run, t_start_ms, t_end_ms),
        ):
            rx_rn, tx_rn, fr, sf, sl, rb_s, rb_l = (
                int(row[0]), int(row[1]), int(row[2]), int(row[3]),
                int(row[4]), int(row[5]), int(row[6])
            )
            corrupt_rx[(rx_rn, tx_rn)].append((fr, sf, sl, rb_s, rb_l))

    # ------------------------------------------------------------------ #
    # Bulk load 5: per-hop reTX counts from psschTxUeMac (ndi=0 rows)    #
    # retx_by_rnti[rnti] = count of retransmissions (ndi=0) in window    #
    # ------------------------------------------------------------------ #
    print(f"[kpi_postprocess] Loading per-hop reTX ...")
    retx_by_rnti: Dict[int, int] = {}
    if _has_table(con, "psschTxUeMac"):
        ndi_col = None
        # Check if 'ndi' column exists.
        pragma = con.execute("PRAGMA table_info(psschTxUeMac);").fetchall()
        col_names = [r[1] for r in pragma]
        if "ndi" in col_names:
            ndi_col = "ndi"
        elif "rv" in col_names:
            ndi_col = "rv"  # rv>0 also identifies retx

        if ndi_col == "ndi":
            for row in con.execute(
                "SELECT rnti, COUNT(*) FROM psschTxUeMac "
                "WHERE ndi=0 AND SEED=? AND RUN=? AND timeMs>=? AND timeMs<=? "
                "GROUP BY rnti;",
                (seed, run, t_start_ms, t_end_ms),
            ):
                retx_by_rnti[int(row[0])] = int(row[1])
        elif ndi_col == "rv":
            for row in con.execute(
                "SELECT rnti, COUNT(*) FROM psschTxUeMac "
                "WHERE rv>0 AND SEED=? AND RUN=? AND timeMs>=? AND timeMs<=? "
                "GROUP BY rnti;",
                (seed, run, t_start_ms, t_end_ms),
            ):
                retx_by_rnti[int(row[0])] = int(row[1])

    con.close()

    # ------------------------------------------------------------------ #
    # Enumerate (TX, RX, flow) links and compute KPIs in memory           #
    # ------------------------------------------------------------------ #
    print(f"[kpi_postprocess] Computing KPIs ...")

    # Determine slot duration for occupancy.
    slot_dur_ms = 0.5  # default μ=1
    if pool_info and pool_info[2] <= 0:
        slot_dur_ms = 1.0  # μ=0

    active_ms = t_end_ms - t_start_ms

    rows: List[KpiRow] = []

    # All TX nodes and their flows.
    tx_links = set()
    for (nid, port) in tx_events:
        tx_links.add((nid, port))

    for (tx_node_id, port) in sorted(tx_links):
        tx_ip = tx_node_ip.get(tx_node_id, "")
        tx_nm = meta.get(tx_node_id) or ip_to_node.get(tx_ip)
        flow = FLOW_PORTS.get(port, str(port))

        tx_list = tx_events[(tx_node_id, port)]   # sorted by timeSec (ORDER BY above)
        tx_seq_map: Dict[int, Tuple[float,int]] = {seq: (t, sz) for t, seq, sz in tx_list}

        # All RX nodes that received from this TX.
        rx_nodes_for_tx = set()
        for (rx_nid, sip, p) in rx_events:
            if sip == tx_ip and p == port:
                rx_nodes_for_tx.add(rx_nid)

        for rx_node_id in sorted(rx_nodes_for_tx):
            if rx_node_id == tx_node_id:
                continue
            rx_nm = meta.get(rx_node_id)

            # Range gate.
            if range_m is not None and tx_nm and rx_nm:
                if (tx_nm.x, tx_nm.y) != (0.0, 0.0):
                    d = _distance_2d(tx_nm.x, tx_nm.y, rx_nm.x, rx_nm.y)
                    if d > range_m:
                        continue

            rx_list = rx_events[(rx_node_id, tx_ip, port)]
            rx_seqs = {seq for _, seq, _ in rx_list}
            rx_times = [t for t, _, _ in rx_list]
            rx_seq_bytes: Dict[int, Tuple[float,int]] = {seq: (t, sz) for t, seq, sz in rx_list}

            # ---- PRR / PIR / Throughput ----
            prr_samples: List[float] = []
            pir_samples: List[float] = []
            thput_samples: List[float] = []
            t = t_start
            while t + window <= t_end + 1e-9:
                tw_end = t + window
                tx_in_w = {seq for seq, (ts, _) in tx_seq_map.items() if t <= ts < tw_end}
                rx_times_w = [rt for rt, _, _ in rx_list if t <= rt < tw_end]
                rx_in_w = tx_in_w & rx_seqs

                if tx_in_w:
                    prr_samples.append(len(rx_in_w) / len(tx_in_w))

                if len(rx_times_w) >= 2:
                    rx_times_w.sort()
                    gaps = [rx_times_w[i] - rx_times_w[i-1]
                            for i in range(1, len(rx_times_w))]
                    pir_samples.append(sum(gaps) / len(gaps))

                rx_bytes_w = sum(sz for seq, (rt, sz) in rx_seq_bytes.items()
                                 if t <= rt < tw_end)
                thput_samples.append(rx_bytes_w * 8.0 / window / 1000.0)

                t += step

            def _stats(s: List[float]):
                if not s:
                    return None, None, None
                ss = sorted(s)
                return _safe_mean(ss), _percentile(ss, 0.05), _percentile(ss, 0.95)

            def _stats_ext(s: List[float]):
                """Returns (mean, p5, p95, worst, best) for a sample list.
                For PRR/Tput: worst=min, best=max.
                For PIR: worst=max, best=min.
                Caller picks the interpretation by field assignment."""
                if not s:
                    return None, None, None, None, None
                ss = sorted(s)
                return (_safe_mean(ss), _percentile(ss, 0.05), _percentile(ss, 0.95),
                        ss[0], ss[-1])  # (mean, p5, p95, min, max)

            prr_mean, prr_p5, prr_p95, prr_min, prr_max = _stats_ext(prr_samples)
            pir_mean, pir_p5, pir_p95, pir_min, pir_max = _stats_ext(pir_samples)
            thput_mean, thput_p5, thput_p95, thput_min, thput_max = _stats_ext(thput_samples)

            def _ms(v): return v * 1000.0 if v is not None else None

            # ---- AoI ----
            gen_times = [tx_seq_map[seq][0] for _, seq, _ in rx_list if seq in tx_seq_map]
            rx_for_aoi = [t for t, seq, _ in rx_list if seq in tx_seq_map]
            aoi_mean, aoi_peak = _aoi_stats(gen_times, rx_for_aoi, t_start, t_end)
            # AoI best = minimum instantaneous AoI at any RX event.
            aoi_best = None
            if gen_times and rx_for_aoi:
                instants = [(r - g) * 1000.0
                            for g, r in zip(gen_times, rx_for_aoi) if r >= g]
                aoi_best = min(instants) if instants else None

            # ---- HARQ ----
            rx_rnti = rx_nm.rnti if rx_nm else None
            ack, nack = harq_counts.get(rx_rnti, (0, 0)) if rx_rnti else (0, 0)
            total_harq = ack + nack
            nack_rate = nack / total_harq if total_harq > 0 else None
            harq = {"harq_ack": ack, "harq_nack": nack, "harq_nack_rate": nack_rate}

            # ---- Collision attribution (in-memory) ----
            tx_rnti = tx_nm.rnti if tx_nm else None
            hd_count = 0
            rc_count = 0
            ce_count = 0
            total_corrupt = 0

            if rx_rnti is not None and tx_rnti is not None:
                # Half-duplex: slots where rx was also TX-ing concurrently with tx_rnti.
                tx_rnti_slots = set(
                    (fr, sf, sl) for _, fr, sf, sl, _, _, _ in tx_grants.get(tx_rnti, [])
                )
                rx_rnti_slots = set(
                    (fr, sf, sl) for _, fr, sf, sl, _, _, _ in tx_grants.get(rx_rnti, [])
                )
                in_range = True
                if range_m is not None and tx_nm and rx_nm:
                    if (tx_nm.x, tx_nm.y) != (0.0, 0.0):
                        d = _distance_2d(tx_nm.x, tx_nm.y, rx_nm.x, rx_nm.y)
                        in_range = d <= range_m
                if in_range:
                    hd_count = len(tx_rnti_slots & rx_rnti_slots)

                # Collision attribution from corrupt RX rows.
                corrupt_list = corrupt_rx.get((rx_rnti, tx_rnti), [])
                total_corrupt = len(corrupt_list)
                for fr, sf, sl, rb_s, rb_l in corrupt_list:
                    grants_in_slot = slot_grants.get((fr, sf, sl), [])
                    collided = False
                    for g_rnti, g_rb_s, g_rb_l in grants_in_slot:
                        if g_rnti == tx_rnti or g_rnti == rx_rnti:
                            continue
                        if rb_s < g_rb_s + g_rb_l and g_rb_s < rb_s + rb_l:
                            collided = True
                            break
                    if collided:
                        rc_count += 1
                    else:
                        ce_count += 1

            # ---- Channel occupancy (TX side, per unique TX rnti) ----
            occupancy: Optional[float] = None
            # ---- Absolute subchannel×slot counters (per unique TX rnti) ----
            subch_slots_used: Optional[int] = None
            subch_slots_total: Optional[int] = None
            subch_slots_per_pkt: Optional[float] = None
            if tx_rnti is not None and pool_info is not None:
                sb_ch_size = pool_info[0]
                n_subch = pool_info[1] if pool_info[1] > 0 else 1
                if sb_ch_size > 0:
                    psfch_period = 4
                    total_slots = int(active_ms / slot_dur_ms)
                    pssch_slots = total_slots - total_slots // psfch_period
                    total_subch_slots = pssch_slots * n_subch
                    if total_subch_slots > 0:
                        grants = tx_grants.get(tx_rnti, [])
                        used = sum(rb_l // sb_ch_size
                                   for _, _, _, _, _, rb_l, _ in grants)
                        occupancy = min(1.0, used / total_subch_slots)
                        subch_slots_used = int(used)
                        subch_slots_total = int(total_subch_slots)
                        n_tx_pkts = len(tx_seq_map)
                        subch_slots_per_pkt = (
                            used / n_tx_pkts if n_tx_pkts > 0 else None
                        )

            row = KpiRow(
                rx_node_id=rx_node_id,
                tx_node_id=tx_node_id,
                rx_imsi=rx_nm.imsi if rx_nm else 0,
                tx_imsi=tx_nm.imsi if tx_nm else 0,
                rx_ip=rx_nm.ip if rx_nm else "",
                tx_ip=tx_ip,
                flow=flow,
                seed=seed,
                run=run,
                n_tx=len(tx_seq_map),
                n_rx=len(rx_seqs),
                prr_mean=prr_mean,
                prr_p5=prr_p5,
                prr_p95=prr_p95,
                pir_mean_ms=_ms(pir_mean),
                pir_p5_ms=_ms(pir_p5),
                pir_p95_ms=_ms(pir_p95),
                thput_mean_kbps=thput_mean,
                thput_p5_kbps=thput_p5,
                thput_p95_kbps=thput_p95,
                aoi_mean_ms=aoi_mean,
                aoi_peak_ms=aoi_peak,
                harq_ack=harq["harq_ack"],
                harq_nack=harq["harq_nack"],
                harq_nack_rate=harq["harq_nack_rate"],
                hd_count=hd_count,
                rc_count=rc_count,
                ce_count=ce_count,
                total_corrupt=total_corrupt,
                ch_occupancy=occupancy,
                subch_slots_used=subch_slots_used,
                subch_slots_total=subch_slots_total,
                subch_slots_per_pkt=subch_slots_per_pkt,
                # ---- worst / best ----
                prr_worst=prr_min,      # PRR worst = min
                prr_best=prr_max,       # PRR best  = max
                pir_worst_ms=_ms(pir_max),  # PIR worst = max latency
                pir_best_ms=_ms(pir_min),   # PIR best  = min latency
                thput_worst_kbps=thput_min, # Tput worst = min
                thput_best_kbps=thput_max,  # Tput best  = max
                aoi_best_ms=aoi_best,
                # ---- per-hop reTX ----
                # hop1 = leader RNTI (tx_node_id 0 → tx_rnti resolved above),
                # hop2 = sum of ndi=0 for any other TX RNTI in retx_by_rnti
                # (in relay mode the relay re-broadcasts with its own RNTI).
                hop1_retx=(retx_by_rnti.get(tx_rnti, 0)
                           if tx_rnti is not None else None),
                hop2_retx=(sum(c for rn, c in retx_by_rnti.items()
                               if rn != tx_rnti)
                           if tx_rnti is not None else None),
            )
            rows.append(row)

    # Write CSV.
    if rows:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[fld.name for fld in fields(KpiRow)])
            writer.writeheader()
            for r in rows:
                writer.writerow(_row_to_dict(r))
        print(f"[kpi_postprocess] Wrote {len(rows)} rows → {out_path}")
    else:
        print(f"[kpi_postprocess] No data rows produced for {db_path}", file=sys.stderr)

    return rows



def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="NR-SL V2X KPI post-processor — produces per-(UE, flow) CSV."
    )
    p.add_argument("--db", required=True, type=Path, help="Path to simulation .db file")
    p.add_argument("--out", required=True, type=Path, help="Output CSV path")
    p.add_argument(
        "--warmup", type=float, default=2.0,
        help="Warm-up duration (s) to skip at start [default: 2.0]"
    )
    p.add_argument(
        "--sim-end", type=float, default=None,
        help="Simulation end time (s). If omitted, inferred from pktTxRx."
    )
    p.add_argument(
        "--window", type=float, default=1.0,
        help=(
            "Sliding-window width (s) for PRR/PIR/thput samples. "
            "Default 1.0 s is suited to μ=1 (0.5 ms slots, 50 Hz packet rate). "
            "Use 2.0 s for μ=0 or sparse traffic."
        ),
    )
    p.add_argument(
        "--step", type=float, default=0.1,
        help=(
            "Sliding-window step (s). Default 0.1 s → 10 samples per window. "
            "Smaller values increase overlap but cost more CPU."
        ),
    )
    p.add_argument(
        "--range", type=float, default=None,
        dest="range_m",
        help="Inter-node range gate (m). Only (TX,RX) pairs within this distance "
             "are included. If omitted and nodeInfo has no positions, no gate applied."
    )
    p.add_argument(
        "--seed", type=int, default=1,
        help="RNG seed to filter DB rows [default: 1]"
    )
    p.add_argument(
        "--run", type=int, default=1,
        help="Run index to filter DB rows [default: 1]"
    )
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if not args.db.exists():
        print(f"[ERROR] DB not found: {args.db}", file=sys.stderr)
        sys.exit(1)
    compute_kpis(
        db_path=args.db,
        out_path=args.out,
        warmup=args.warmup,
        sim_end=args.sim_end,
        window=args.window,
        step=args.step,
        range_m=args.range_m,
        seed=args.seed,
        run=args.run,
    )


if __name__ == "__main__":
    main()
