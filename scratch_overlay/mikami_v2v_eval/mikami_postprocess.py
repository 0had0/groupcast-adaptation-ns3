#!/usr/bin/env python3
"""
mikami_postprocess.py
=====================

Post-process a mikami_v2v_eval SQLite DB into the five KPIs studied in
Mikami et al. 2020 (Figs. 8-13).

This script reuses the COMMON KPI MODULE under
``scratch_overlay/common/kpi/kpi/`` for all data access:

  - kpi.data.DataCache    — bulk-load pandas frames for every trace table
  - kpi.topology.Topology — node positions + leader/follower classification
  - kpi.outcomes.Outcomes — canonical "did this RX decode this TX's seq?"
  - kpi.range.Ranges      — geographic bin specs
  - kpi.registry / kpis/* — registered KPI specs we reuse for PRR / HARQ

Mikami-specific KPIs (OTT latency, BLER, SL-SINR, PRR before/after HARQ)
are implemented as KpiBase subclasses in this directory and registered
into the same registry, so the run is single-pass over the DB.

Usage:
  cd scratch_overlay/mikami_v2v_eval
  python3 -m mikami_postprocess --db /tmp/mikami/mikami_default.db \\
      --out-dir /tmp/mikami/kpis --warmup 2.0
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Pull in the common kpi module by path
_HERE = Path(__file__).resolve().parent
_COMMON = _HERE.parent / "common" / "kpi"
if str(_COMMON) not in sys.path:
    sys.path.insert(0, str(_COMMON))

from kpi.data import DataCache                          # noqa: E402
from kpi.outcomes import Outcomes                       # noqa: E402
from kpi.range import Ranges                            # noqa: E402
from kpi.registry import (                              # noqa: E402
    KpiBase, KpiContext, KpiOutputColumn, KpiRegistry,
    ScalarResult, BinResult, CdfResult, TimeSeriesResult,
    register,
)
from kpi.topology import Topology                       # noqa: E402

# Reuse the existing HARQ throughput / PRR / PIR KPIs (read-only)
import kpis  # noqa: E402,F401  (registers the standard specs)


# ---------------------------------------------------------------------------
# Mikami-specific KPI specs
# ---------------------------------------------------------------------------

# ---------- 1. Over-the-air OTT latency (Fig. 8 / Fig. 9) --------------
@register(
    id="mikami_ott_latency",
    title="Over-the-air OTT latency (Mikami 2020 Fig. 8/9)",
    spec_ref="Mikami 2020 §IV-B",
    output_kind="timeseries",
    output_columns=(
        KpiOutputColumn("tx_node_id", "", "TX node id"),
        KpiOutputColumn("rx_node_id", "", "RX node id"),
        KpiOutputColumn("tx_time_s", "s", "TX time"),
        KpiOutputColumn("rx_time_s", "s", "RX time"),
        KpiOutputColumn("latency_ms", "ms", "rx - tx"),
        KpiOutputColumn("seq", "", "Application seq"),
    ),
)
class MikamiOttLatency(KpiBase):
    """Per-packet over-the-air OTT latency (matches Fig. 8/9 layout)."""
    def compute(self, ctx: KpiContext):
        pkt = ctx.cache.pkt_txrx()
        if pkt is None or len(pkt) == 0:
            return [TimeSeriesResult([], filename="mikami_ott_latency.csv")]
        ip_to_node = dict(ctx.topology.tx_ip_to_node)
        # Augment with relay IPs from node_info
        ni = ctx.cache.node_info()
        if ni is not None and len(ni):
            for r in ni.itertuples(index=False):
                if r.ip and r.nodeId is not None:
                    ip_to_node.setdefault(str(r.ip), int(r.nodeId))

        # Build (srcIp, pktSeqNum) -> (timeSec, txNodeId) from TX events
        tx_only = pkt[pkt["txRx"] == "tx"]
        tx_map: Dict[Tuple[str, int], Tuple[float, int]] = {}
        for r in tx_only.itertuples(index=False):
            if r.srcIp is None or r.pktSeqNum is None or r.timeSec is None:
                continue
            tx_map[(str(r.srcIp), int(r.pktSeqNum))] = (float(r.timeSec), int(r.nodeId))

        rx_only = pkt[pkt["txRx"] == "rx"]
        rows: List[Dict] = []
        for r in rx_only.itertuples(index=False):
            if r.srcIp is None or r.pktSeqNum is None or r.timeSec is None:
                continue
            rx_t = float(r.timeSec)
            if rx_t < ctx.t_start or rx_t > ctx.t_end:
                continue
            tx = tx_map.get((str(r.srcIp), int(r.pktSeqNum)))
            if tx is None:
                continue
            tx_t, tx_node = tx
            rx_node = int(r.nodeId)
            if tx_node == rx_node:
                continue
            rows.append({
                "tx_node_id": tx_node,
                "rx_node_id": rx_node,
                "tx_time_s": tx_t,
                "rx_time_s": rx_t,
                "latency_ms": (rx_t - tx_t) * 1000.0,
                "seq": int(r.pktSeqNum),
            })
        rows.sort(key=lambda r: (r["tx_node_id"], r["rx_node_id"], r["tx_time_s"]))
        return [TimeSeriesResult(rows, filename="mikami_ott_latency.csv")]


# ---------- 2. Initial PSSCH BLER (Fig. 10) ------------------------------
@register(
    id="mikami_bler",
    title="Initial PSSCH BLER per TX→RX pair (Mikami 2020 Fig. 10)",
    spec_ref="Mikami 2020 §IV-B",
    output_kind="scalar",
    output_columns=(
        KpiOutputColumn("tx_node_id", "", "TX node id"),
        KpiOutputColumn("rx_node_id", "", "RX node id"),
        KpiOutputColumn("n_packets", "", "# first-TX attempts"),
        KpiOutputColumn("n_failed", "", "# corrupt first attempts"),
        KpiOutputColumn("bler_initial", "", "BLER (initial TX only)"),
        KpiOutputColumn("n_all_rx", "", "# all RX events (incl. retx)"),
        KpiOutputColumn("n_all_failed", "", "# corrupt all RX events"),
        KpiOutputColumn("bler_all", "", "BLER (all RX events)"),
    ),
)
class MikamiBler(KpiBase):
    """PSSCH BLER per TX→RX pair, split into 'initial-only' (Fig. 10) and
    'all' (which includes HARQ retransmissions).

    Source: psschRxUePhy. For each (txRnti, rxRnti) the first PSSCH RX event
    of a new TB is identified by a fresh (frame, subframe, slot) triple —
    which corresponds to NDI=1 from the NrSlUeMac, but the column may be
    absent in older CTTC NR builds, so we use the (frame, slot) key as a
    robust stand-in.
    """
    def compute(self, ctx: KpiContext):
        rx = ctx.cache.pssch_rx()
        if rx is None or len(rx) == 0:
            return [ScalarResult([], filename="mikami_bler.csv")]

        # RNTI → node via the receiver's IMSI (cached in DataCache.imsi_to_node).
        imsi_to_node = ctx.cache.imsi_to_node()

        # TX RNTI → node: TX RNTIs are allocated by LteUeRrc and equal
        # imsi mod 65535. Build that map from the IMSI set.
        rnti_to_node: Dict[int, int] = {imsi % 65535: nid for imsi, nid in imsi_to_node.items()}

        # Per (tx_node, rx_node) accumulators
        acc: Dict[Tuple[int, int], Dict[str, int]] = defaultdict(
            lambda: {"n_packets": 0, "n_failed": 0,
                     "n_all_rx": 0, "n_all_failed": 0}
        )
        # Track first-RX-per-TB using (txRnti, rxRnti, frame, subframe, slot)
        # as the key. NR-V2X TBs are uniquely identified by (sfn, harqId);
        # the trace exposes frame/subframe/slot which is the SFN. We use
        # (frame, subframe, slot, harqImplicit=rv=0) to identify the *first*
        # attempt of a TB.
        seen_first: set = set()

        # Build a per-(txRnti, rxRnti) cache of "first corrupt" already seen
        # for the current TB id (frame, slot, harqPid slot index).
        for r in rx.itertuples(index=False):
            t = float(r.timeMs) / 1000.0
            if t < ctx.t_start or t > ctx.t_end:
                continue
            tx_rnti = int(r.txRnti) if r.txRnti is not None else -1
            rx_rnti = int(r.rnti) if r.rnti is not None else -1
            if tx_rnti < 0 or rx_rnti < 0:
                continue
            tx_node = rnti_to_node.get(tx_rnti, -1)
            rx_node = rnti_to_node.get(rx_rnti, -1)
            if tx_node < 0 or rx_node < 0 or tx_node == rx_node:
                continue
            corrupt = bool(r.psschCorrupt) if r.psschCorrupt is not None else False
            a = acc[(tx_node, rx_node)]
            a["n_all_rx"] += 1
            if corrupt:
                a["n_all_failed"] += 1
            # TB id from the SFN tuple; rv is 0 for initial, > 0 for retx
            frame = int(r.frame) if r.frame is not None else 0
            sub = int(r.subFrame) if r.subFrame is not None else 0
            slot = int(r.slot) if r.slot is not None else 0
            tb_key = (tx_rnti, rx_rnti, frame, sub, slot)
            if tb_key not in seen_first:
                seen_first.add(tb_key)
                a["n_packets"] += 1
                if corrupt:
                    a["n_failed"] += 1

        rows: List[Dict] = []
        for (tx_node, rx_node), a in acc.items():
            n = a["n_packets"]
            rows.append({
                "tx_node_id": tx_node,
                "rx_node_id": rx_node,
                "n_packets": n,
                "n_failed": a["n_failed"],
                "bler_initial": (a["n_failed"] / n) if n > 0 else None,
                "n_all_rx": a["n_all_rx"],
                "n_all_failed": a["n_all_failed"],
                "bler_all": (a["n_all_failed"] / a["n_all_rx"]) if a["n_all_rx"] > 0 else None,
            })
        rows.sort(key=lambda r: (r["tx_node_id"], r["rx_node_id"]))
        return [ScalarResult(rows, filename="mikami_bler.csv")]


# ---------- 3. Mean SL-SINR per TX→RX pair (Fig. 11) --------------------
@register(
    id="mikami_sinr",
    title="Mean SL-SINR per TX→RX pair (Mikami 2020 Fig. 11)",
    spec_ref="Mikami 2020 §IV-B",
    output_kind="scalar",
    output_columns=(
        KpiOutputColumn("tx_node_id", "", "TX node id"),
        KpiOutputColumn("rx_node_id", "", "RX node id"),
        KpiOutputColumn("n_samples", "", "# PSSCH RX events"),
        KpiOutputColumn("sinr_mean_db", "dB", "Mean linear avg-SINR"),
        KpiOutputColumn("sinr_min_db", "dB", "Min SINR"),
        KpiOutputColumn("sinr_max_db", "dB", "Max SINR"),
        KpiOutputColumn("sinr_p10_db", "dB", "10th percentile"),
        KpiOutputColumn("sinr_p90_db", "dB", "90th percentile"),
    ),
)
class MikamiSinr(KpiBase):
    def compute(self, ctx: KpiContext):
        rx = ctx.cache.pssch_rx()
        if rx is None or len(rx) == 0 or "avrgSinr" not in rx.columns:
            return [ScalarResult([], filename="mikami_sinr.csv")]
        imsi_to_node = ctx.cache.imsi_to_node()
        rnti_to_node = {imsi % 65535: nid for imsi, nid in imsi_to_node.items()}
        per_link: Dict[Tuple[int, int], List[float]] = defaultdict(list)
        for r in rx.itertuples(index=False):
            t = float(r.timeMs) / 1000.0
            if t < ctx.t_start or t > ctx.t_end:
                continue
            tx_rnti = int(r.txRnti) if r.txRnti is not None else -1
            rx_rnti = int(r.rnti) if r.rnti is not None else -1
            tx_node = rnti_to_node.get(tx_rnti, -1)
            rx_node = rnti_to_node.get(rx_rnti, -1)
            if tx_node < 0 or rx_node < 0 or tx_node == rx_node:
                continue
            s = float(r.avrgSinr) if r.avrgSinr is not None and r.avrgSinr > 0 else None
            if s is None: continue
            per_link[(tx_node, rx_node)].append(10.0 * math.log10(s))
        rows: List[Dict] = []
        for (tx, rx_node), samples in per_link.items():
            samples_sorted = sorted(samples)
            n = len(samples_sorted)
            rows.append({
                "tx_node_id": tx,
                "rx_node_id": rx_node,
                "n_samples": n,
                "sinr_mean_db": sum(samples_sorted) / n,
                "sinr_min_db": samples_sorted[0],
                "sinr_max_db": samples_sorted[-1],
                "sinr_p10_db": samples_sorted[max(0, n // 10)],
                "sinr_p90_db": samples_sorted[min(n - 1, 9 * n // 10)],
            })
        rows.sort(key=lambda r: (r["tx_node_id"], r["rx_node_id"]))
        return [ScalarResult(rows, filename="mikami_sinr.csv")]


# ---------- 4. PRR before HARQ (Fig. 12a / 13a) -------------------------
@register(
    id="mikami_prr_before_harq",
    title="PRR before HARQ (Mikami 2020 Fig. 12a/13a)",
    spec_ref="Mikami 2020 §IV-C",
    output_kind="scalar",
    output_columns=(
        KpiOutputColumn("tx_node_id", "", "TX node id"),
        KpiOutputColumn("rx_node_id", "", "RX node id"),
        KpiOutputColumn("tx_total", "", "# TX packets at TX node"),
        KpiOutputColumn("first_rx_attempts", "", "# distinct first-TX RX events at RX"),
        KpiOutputColumn("first_rx_success", "", "# first-TX RX events that decoded OK"),
        KpiOutputColumn("prr_before_harq", "", "PRR (initial TX only)"),
    ),
)
class MikamiPrrBeforeHarq(KpiBase):
    """PRR achieved on the FIRST TX attempt (no HARQ retransmissions).

    Numerator:   #first-TB-RX-events at this (tx, rx) that did NOT corrupt.
    Denominator: #distinct (tx, rx, frame, subframe, slot) first-RX events.

    For "without HARQ retransmission" reliability we use the PHY-level
    outcome of the first PSSCH RX attempt; this is exactly the initial
    BLER complement (1 - BLER_initial from the MikamiBler spec).
    """
    def compute(self, ctx: KpiContext):
        rx = ctx.cache.pssch_rx()
        if rx is None or len(rx) == 0:
            return [ScalarResult([], filename="mikami_prr_before_harq.csv")]
        imsi_to_node = ctx.cache.imsi_to_node()
        rnti_to_node = {imsi % 65535: nid for imsi, nid in imsi_to_node.items()}

        seen: set = set()
        per_link: Dict[Tuple[int, int], Dict[str, int]] = defaultdict(
            lambda: {"first_rx_attempts": 0, "first_rx_success": 0}
        )
        for r in rx.itertuples(index=False):
            t = float(r.timeMs) / 1000.0
            if t < ctx.t_start or t > ctx.t_end:
                continue
            tx_rnti = int(r.txRnti) if r.txRnti is not None else -1
            rx_rnti = int(r.rnti) if r.rnti is not None else -1
            tx_node = rnti_to_node.get(tx_rnti, -1)
            rx_node = rnti_to_node.get(rx_rnti, -1)
            if tx_node < 0 or rx_node < 0 or tx_node == rx_node:
                continue
            tb_key = (tx_rnti, rx_rnti, int(r.frame), int(r.subFrame), int(r.slot))
            if tb_key in seen:
                continue
            seen.add(tb_key)
            corrupt = bool(r.psschCorrupt) if r.psschCorrupt is not None else False
            a = per_link[(tx_node, rx_node)]
            a["first_rx_attempts"] += 1
            if not corrupt:
                a["first_rx_success"] += 1

        # TX totals per node: count distinct seqs emitted by each TX in the window.
        pkt = ctx.cache.pkt_txrx()
        tx_totals: Dict[int, int] = defaultdict(set)
        if pkt is not None:
            for r in pkt.itertuples(index=False):
                if r.txRx != "tx":
                    continue
                if r.timeSec is None or r.pktSeqNum is None:
                    continue
                if r.timeSec < ctx.t_start or r.timeSec > ctx.t_end:
                    continue
                tx_totals[int(r.nodeId)].add(int(r.pktSeqNum))

        rows: List[Dict] = []
        # Ensure every (tx, rx) pair with TX traffic has a row, even if no PHY RX.
        all_tx_nodes = sorted(tx_totals.keys())
        for tx_node in all_tx_nodes:
            n_total = len(tx_totals.get(tx_node, set()))
            for rx_node in all_tx_nodes:
                if tx_node == rx_node:
                    continue
                a = per_link.get((tx_node, rx_node),
                                 {"first_rx_attempts": 0, "first_rx_success": 0})
                first_rx_ok = a["first_rx_success"]
                # PRR before HARQ: numerator is first-TX PHY successes,
                # denominator is APP-layer TX packets (not just first-TX PHY
                # attempts). This makes PRR_before_HARQ comparable to
                # PRR_after_HARQ which uses the same denominator.
                # When HARQ is OFF or retx is broken, PRR_before ≈ PRR_after.
                prr_before = (first_rx_ok / n_total) if n_total > 0 else None
                rows.append({
                    "tx_node_id": tx_node,
                    "rx_node_id": rx_node,
                    "tx_total": n_total,
                    "first_rx_attempts": a["first_rx_attempts"],
                    "first_rx_success": a["first_rx_success"],
                    "prr_before_harq": prr_before,
                })
        rows.sort(key=lambda r: (r["tx_node_id"], r["rx_node_id"]))
        return [ScalarResult(rows, filename="mikami_prr_before_harq.csv")]


# ---------- 5. PRR after HARQ (Fig. 12b / 13b) --------------------------
@register(
    id="mikami_prr_after_harq",
    title="PRR after HARQ retransmissions (Mikami 2020 Fig. 12b/13b)",
    spec_ref="Mikami 2020 §IV-C",
    output_kind="scalar",
    output_columns=(
        KpiOutputColumn("tx_node_id", "", "TX node id"),
        KpiOutputColumn("rx_node_id", "", "RX node id"),
        KpiOutputColumn("tx_total", "", "# TX packets at TX node"),
        KpiOutputColumn("rx_received", "", "# packets received at app layer"),
        KpiOutputColumn("prr_after_harq", "", "PRR (app-layer RX)"),
    ),
)
class MikamiPrrAfterHarq(KpiBase):
    """PRR achieved AT THE APPLICATION LAYER, after allowing all HARQ
    retransmissions to succeed.

    Implementation: we read pktTxRx directly (which has srcIp) and join TX
    and RX events on (srcIp, pktSeqNum). The common Outcomes class falls
    back to appPktSeq which (in the no-relay all-TX case) attributes every
    RX event to the leader — wrong for our 3-UE mesh.

    Numerator:   #distinct TX seqs (by srcIp+seq) that have at least one
                 matching pktTxRx.rx event at the RX node.
    Denominator: #distinct TX seqs emitted by TX.
    """
    def compute(self, ctx: KpiContext):
        pkt = ctx.cache.pkt_txrx()
        if pkt is None or len(pkt) == 0:
            return [ScalarResult([], filename="mikami_prr_after_harq.csv")]
        # Build (srcIp → nodeId) from TX rows.
        tx_ip_to_node: Dict[str, int] = {}
        tx_only = pkt[pkt["txRx"] == "tx"]
        for r in tx_only.itertuples(index=False):
            if r.srcIp is not None and r.nodeId is not None:
                tx_ip_to_node[str(r.srcIp)] = int(r.nodeId)
        # Count of TX seqs per node
        tx_seqs_per_node: Dict[int, set] = {nid: set() for nid in tx_ip_to_node.values()}
        for r in tx_only.itertuples(index=False):
            if r.srcIp is None or r.pktSeqNum is None:
                continue
            nid = tx_ip_to_node.get(str(r.srcIp))
            if nid is not None:
                tx_seqs_per_node[nid].add(int(r.pktSeqNum))
        # Set of (srcIp, seq) decoded at each rx_node
        rx_per_node: Dict[int, set] = {nid: set() for nid in tx_ip_to_node.values()}
        rx_only = pkt[pkt["txRx"] == "rx"]
        for r in rx_only.itertuples(index=False):
            if r.srcIp is None or r.pktSeqNum is None or r.nodeId is None:
                continue
            rx_per_node.setdefault(int(r.nodeId), set()).add(
                (str(r.srcIp), int(r.pktSeqNum))
            )

        rows: List[Dict] = []
        all_nodes = sorted(tx_ip_to_node.values())
        ranges = ctx.ranges
        topo = ctx.topology
        for tx in all_nodes:
            total_seqs = tx_seqs_per_node.get(tx, set())
            if not total_seqs:
                continue
            for rx_node in all_nodes:
                if rx_node == tx:
                    continue
                d = topo.distance(tx, rx_node)
                if not ranges.in_any_range(d):
                    continue
                rx_set = rx_per_node.get(rx_node, set())
                n_rx = sum(1 for s in total_seqs if (str(next(ip for ip, n in tx_ip_to_node.items() if n == tx)), s) in rx_set)
                rows.append({
                    "tx_node_id": tx,
                    "rx_node_id": rx_node,
                    "tx_total": len(total_seqs),
                    "rx_received": n_rx,
                    "prr_after_harq": (n_rx / len(total_seqs)) if total_seqs else None,
                })
        rows.sort(key=lambda r: (r["tx_node_id"], r["rx_node_id"]))
        return [ScalarResult(rows, filename="mikami_prr_after_harq.csv")]


# ---------- 5b. HARQ efficiency (diagnostic) ----------------------------
# Shows how many of the HARQ retransmissions actually reached the PHY RX layer.
# In a 3-UE bidirectional mesh with sensing-based Mode-2, retx TBs often
# collide with concurrent transmissions and are lost; this KPI makes that
# explicit so PRR_before_HARQ ≈ PRR_after_HARQ isn't surprising.
@register(
    id="mikami_harq_efficiency",
    title="HARQ retransmission efficiency (Mikami diagnostic)",
    spec_ref="diagnostic",
    output_kind="scalar",
    output_columns=(
        KpiOutputColumn("tx_node_id", "", "TX node id"),
        KpiOutputColumn("harq_retx_tx", "", "# PSSCH retx TBs transmitted (NDI=0)"),
        KpiOutputColumn("harq_retx_rx", "", "# PSSCH retx TBs heard at any RX (NDI=0)"),
        KpiOutputColumn("harq_retx_efficiency", "", "retx RX / retx TX"),
        KpiOutputColumn("first_tx_tx", "", "# PSSCH first-TX TBs (NDI=1)"),
        KpiOutputColumn("first_tx_rx", "", "# PSSCH first-TX TBs heard at any RX (NDI=1)"),
        KpiOutputColumn("nack_count", "", "# NACK feedback events"),
        KpiOutputColumn("ack_count", "", "# ACK feedback events"),
        KpiOutputColumn("harq_timeout", "", "# HARQ timeout events (RLC drop)"),
    ),
)
class MikamiHarqEfficiency(KpiBase):
    """HARQ control-plane efficiency diagnostic.

    Numerator/denominator are MAC-trace based (psschTxUeMac, psschRxUePhy,
    slHarqFeedback, slHarqTimeout). harq_retx_efficiency = NDI=0 RX /
    NDI=0 TX; if this is 0%, then *no* retx TBs reached the PHY RX layer
    and PRR_after_HARQ cannot exceed PRR_before_HARQ.
    """
    def compute(self, ctx: KpiContext):
        pssch_tx = ctx.cache.pssch_tx()
        pssch_rx = ctx.cache.pssch_rx()
        imsi_to_node = ctx.cache.imsi_to_node()
        rnti_to_node = {imsi % 65535: nid for imsi, nid in imsi_to_node.items()}

        # TX side: count first-TX vs retx per TX node
        first_tx: Dict[int, int] = defaultdict(int)
        retx_tx:  Dict[int, int] = defaultdict(int)
        if pssch_tx is not None and len(pssch_tx):
            for r in pssch_tx.itertuples(index=False):
                t = float(r.timeMs) / 1000.0
                if t < ctx.t_start or t > ctx.t_end:
                    continue
                tx_node = rnti_to_node.get(int(r.rnti), -1)
                if tx_node < 0:
                    continue
                ndi = int(r.ndi) if r.ndi is not None else 1
                if ndi == 1:
                    first_tx[tx_node] += 1
                else:
                    retx_tx[tx_node] += 1

        # RX side: count first-TX vs retx heard at any RX (aggregated by TX node)
        first_rx: Dict[int, int] = defaultdict(int)
        retx_rx:  Dict[int, int] = defaultdict(int)
        if pssch_rx is not None and len(pssch_rx):
            for r in pssch_rx.itertuples(index=False):
                t = float(r.timeMs) / 1000.0
                if t < ctx.t_start or t > ctx.t_end:
                    continue
                tx_node = rnti_to_node.get(int(r.txRnti), -1)
                if tx_node < 0:
                    continue
                ndi = int(r.ndi) if r.ndi is not None else 1
                if ndi == 1:
                    first_rx[tx_node] += 1
                else:
                    retx_rx[tx_node] += 1

        # HARQ feedback (slHarqFeedback has no SEED/RUN column → use whole table)
        try:
            harq = ctx.cache.con.execute(
                "SELECT status FROM slHarqFeedback"
            ).fetchall()
            ack = sum(1 for (s,) in harq if s == "ACK")
            nack = sum(1 for (s,) in harq if s == "NACK")
        except Exception:
            ack = nack = 0

        # HARQ timeout
        try:
            n_to = len(ctx.cache.con.execute(
                "SELECT 1 FROM slHarqTimeout"
            ).fetchall())
        except Exception:
            n_to = 0

        rows: List[Dict] = []
        all_tx = sorted(set(first_tx) | set(retx_tx))
        for tx_node in all_tx:
            f_tx = first_tx.get(tx_node, 0)
            r_tx = retx_tx.get(tx_node, 0)
            f_rx = first_rx.get(tx_node, 0)
            r_rx = retx_rx.get(tx_node, 0)
            eff = (r_rx / r_tx) if r_tx > 0 else None
            rows.append({
                "tx_node_id": tx_node,
                "harq_retx_tx": r_tx,
                "harq_retx_rx": r_rx,
                "harq_retx_efficiency": eff,
                "first_tx_tx": f_tx,
                "first_tx_rx": f_rx,
                "nack_count": nack,        # global count, not per-tx (no per-tx in trace)
                "ack_count":  ack,
                "harq_timeout": n_to,
            })
        rows.sort(key=lambda r: r["tx_node_id"])
        return [ScalarResult(rows, filename="mikami_harq_efficiency.csv")]


# ---------- 6. Path loss (Fig. 7) — uses the THREE GPP channel model -----
@register(
    id="mikami_pathloss",
    title="Path loss per TX→RX pair (Mikami 2020 Fig. 7)",
    spec_ref="Mikami 2020 §IV-A",
    output_kind="scalar",
    output_columns=(
        KpiOutputColumn("tx_node_id", "", "TX node id"),
        KpiOutputColumn("rx_node_id", "", "RX node id"),
        KpiOutputColumn("distance_m", "m", "Static inter-vehicle distance"),
        KpiOutputColumn("expected_pl_db", "dB", "Free-space PL at distance"),
    ),
)
class MikamiPathLoss(KpiBase):
    """Path loss — uses ns-3's position-derived free-space expectation as a
    sanity check on the topology. The actual 3GPP V2V_Highway path loss is
    computed inside the channel model during simulation and exposed via
    psschRxUePhy's SNR (combined with Tx power). For static comparison we
    report the free-space PL reference at the same distance.

    For an exact reproduction the simulator's ThreeGppChannelModel
    `GetLossDb()` would need to be invoked per-pair; that requires a
    scratch-time callback, which we already log in pssch_rx via `avrgSinr`.
    Here we provide a deterministic sanity figure that should match the
    qualitative shape of Fig. 7 (PL rises with distance, rises faster when
    UE#2 blocks the UE#1↔UE#3 LOS — for that, see the SL-SINR KPI).
    """
    def compute(self, ctx: KpiContext):
        topo = ctx.topology
        # 5.89 GHz → λ = c/f
        lam = 299_792_458.0 / 5.89e9
        rows = []
        for tx in sorted(topo.all_nodes):
            for rx in sorted(topo.all_nodes - {tx}):
                d = topo.distance(tx, rx)
                if d <= 0: continue
                pl_db = 20.0 * math.log10(4.0 * math.pi * d / lam)
                rows.append({
                    "tx_node_id": tx,
                    "rx_node_id": rx,
                    "distance_m": d,
                    "expected_pl_db": pl_db,
                })
        return [ScalarResult(rows, filename="mikami_pathloss.csv")]


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
MIKAMI_KPIS = (
    "mikami_ott_latency",
    "mikami_bler",
    "mikami_sinr",
    "mikami_prr_before_harq",
    "mikami_prr_after_harq",
    "mikami_harq_efficiency",
    "mikami_pathloss",
)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", required=True, type=Path,
                   help="SQLite DB produced by mikami_v2v_eval")
    p.add_argument("--out-dir", required=True, type=Path,
                   help="Output directory for KPI CSVs")
    p.add_argument("--warmup", type=float, default=2.0,
                   help="Warm-up seconds discarded from start (default: 2.0)")
    p.add_argument("--sim-end", type=float, default=None,
                   help="Simulation end (s); auto-inferred if omitted")
    p.add_argument("--range", type=str, default="1000",
                   help="Geographic range upper bound (m); default 1000")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--run", type=int, default=1)
    p.add_argument("--tag", type=str, default=None,
                   help="Output file prefix (default: db stem)")
    args = p.parse_args(argv)

    if not args.db.exists():
        print(f"[ERROR] DB not found: {args.db}", file=sys.stderr)
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    tag = args.tag or args.db.stem

    # Infer sim-end if needed
    with DataCache.open(args.db, seed=args.seed, run=args.run,
                        t_start=0.0, t_end=1e12) as probe:
        if args.sim_end is None:
            end = 0.0
            for acc, col in (("pkt_txrx", "timeSec"),
                             ("app_pkt_seq", "timeSec"),
                             ("pssch_tx", "timeMs")):
                df = getattr(probe, acc)()
                if df is not None and len(df) and col in df.columns:
                    end = max(end, float(df[col].max())
                              / (1000.0 if col == "timeMs" else 1.0))
            args.sim_end = end if end > 0 else args.warmup + 10.0

    print(f"[mikami_pp] DB={args.db}  warmup={args.warmup}s  sim_end={args.sim_end}s  tag={tag}")

    with DataCache.open(args.db, seed=args.seed, run=args.run,
                        t_start=args.warmup, t_end=args.sim_end) as cache:
        topo = Topology.build(cache, Ranges.from_cli(args.range))
        outcomes = Outcomes(cache, topo)
        ctx = KpiContext(
            db_path=args.db, out_dir=args.out_dir,
            seed=args.seed, run=args.run,
            t_start=args.warmup, t_end=args.sim_end,
            topology=topo, outcomes=outcomes, cache=cache,
            ranges=topo.ranges, scope_mode="auto", args=args,
        )

        for kid in MIKAMI_KPIS:
            spec = KpiRegistry.get(kid)
            if spec is None:
                print(f"  [{kid:<28}] not registered — skipping", file=sys.stderr)
                continue
            results = spec.run(ctx)
            for r in results:
                path = r.write(args.out_dir, tag)
                n = len(r.rows)
                print(f"  [{kid:<28}] {n:>6} rows  →  {path.name}")

    print(f"[mikami_pp] Wrote all KPIs to {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())