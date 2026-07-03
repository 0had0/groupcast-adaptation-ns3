"""
harq.py — HARQ ACK/NACK counts and NACK rate.

Output:
  <tag>_harq.csv — one row per (rx_node) with aggregate ACK/NACK counts.
"""
from __future__ import annotations

from typing import Dict, List

from kpi.registry import (
    ScalarResult, KpiBase, KpiOutputColumn, register,
)


OUTPUT_COLUMNS = (
    KpiOutputColumn("rx_node_id", "", "Receiver node ID"),
    KpiOutputColumn("harq_ack", "", "ACK count"),
    KpiOutputColumn("harq_nack", "", "NACK count"),
    KpiOutputColumn("harq_total", "", "Total feedback events"),
    KpiOutputColumn("harq_nack_rate", "", "NACK / (ACK + NACK)"),
)


@register(
    id="harq",
    title="HARQ ACK/NACK counts",
    spec_ref="3GPP NR HARQ feedback",
    output_kind="scalar",
    output_columns=OUTPUT_COLUMNS,
)
class Harq(KpiBase):
    def compute(self, ctx):
        cache = ctx.cache
        topo = ctx.topology
        rows: List[Dict] = []

        harq = cache.harq_feedback()
        if harq is None or len(harq) == 0:
            return [ScalarResult(rows, filename="harq.csv")]

        # Aggregate ACK/NACK per rxNodeId.
        ack_per: Dict[int, int] = {}
        nack_per: Dict[int, int] = {}
        for r in harq.itertuples(index=False):
            try:
                rx_nid = int(r.rxNodeId); status = str(r.status)
            except (TypeError, ValueError, AttributeError):
                continue
            if status.upper() == "ACK":
                ack_per[rx_nid] = ack_per.get(rx_nid, 0) + 1
            elif status.upper() == "NACK":
                nack_per[rx_nid] = nack_per.get(rx_nid, 0) + 1

        for rx_nid in sorted(set(list(ack_per.keys()) + list(nack_per.keys()))):
            ack = ack_per.get(rx_nid, 0)
            nack = nack_per.get(rx_nid, 0)
            total = ack + nack
            nack_rate = nack / total if total > 0 else None
            rows.append({
                "rx_node_id": rx_nid,
                "harq_ack": ack,
                "harq_nack": nack,
                "harq_total": total,
                "harq_nack_rate": nack_rate,
            })

        return [ScalarResult(rows, filename="harq.csv")]