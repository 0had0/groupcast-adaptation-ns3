"""
psr.py — Packet-Successfully-Relayed: fraction of leader's packets
that the relay actually forwarded to the tail.

Definition:
  psr = |{seq : relayTiming.forwarded=1}| / |{seq : leader TX}|

Output:
  <tag>_psr.csv — one row per relay node with psr + forward count + gen count.
"""
from __future__ import annotations

from typing import Dict, List

from kpi.registry import (
    ScalarResult, KpiBase, KpiOutputColumn, register,
)


OUTPUT_COLUMNS = (
    KpiOutputColumn("relay_node_id", "", "Relay node ID"),
    KpiOutputColumn("psr", "", "forwarded packets / leader-generated packets"),
    KpiOutputColumn("forwarded_count", "", "relayTiming rows with forwarded=1"),
    KpiOutputColumn("leader_gen_count", "", "Distinct leader TX seqs"),
)


@register(
    id="psr",
    title="Packet-Successfully-Relayed",
    spec_ref="agg_fixed_sweep.psr (legacy)",
    output_kind="scalar",
    output_columns=OUTPUT_COLUMNS,
)
class Psr(KpiBase):
    def compute(self, ctx):
        cache = ctx.cache
        topo = ctx.topology
        rows: List[Dict] = []

        # Leader generation count: distinct TX seqs from pktTxRx.
        pkt = cache.pkt_txrx()
        if pkt is None or len(pkt) == 0:
            return [ScalarResult(rows, filename="psr.csv")]
        leader_tx = pkt[(pkt["txRx"] == "tx") & (pkt["nodeId"] == topo.leader_node)]
        gen_count = int(leader_tx["pktSeqNum"].dropna().nunique()) if len(leader_tx) else 0

        if gen_count == 0:
            return [ScalarResult(rows, filename="psr.csv")]

        relay_t = cache.relay_timing()
        if relay_t is None or len(relay_t) == 0:
            return [ScalarResult(rows, filename="psr.csv")]

        # For each relay, count distinct (seq) where forwarded=1.
        fwd = relay_t[relay_t["forwarded"].astype(int) == 1]
        if len(fwd) == 0:
            return [ScalarResult(rows, filename="psr.csv")]

        for relay_nid in sorted(fwd["relayNodeId"].dropna().astype(int).unique()):
            fwd_seqs = fwd[fwd["relayNodeId"] == relay_nid]["seq"].nunique()
            psr = fwd_seqs / gen_count
            rows.append({
                "relay_node_id": relay_nid,
                "psr": psr,
                "forwarded_count": int(fwd_seqs),
                "leader_gen_count": gen_count,
            })

        return [ScalarResult(rows, filename="psr.csv")]