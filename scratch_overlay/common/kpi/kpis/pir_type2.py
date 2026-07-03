"""
pir_type2.py — 3GPP TR 37.885 §5.2.4 PIR Type 2 (groupcast, intended set).

Same as PIR Type 1 but conditional on Rx being in Tx's intended set, and no
distance gate.

Output:
  <tag>_pir_type2.cdf.csv — one row per (tx_node, rx_node, gap_ms) sample
"""
from __future__ import annotations

from typing import Dict, List

from kpi.registry import (
    CdfResult, KpiBase, KpiOutputColumn, register,
)


OUTPUT_COLUMNS = (
    KpiOutputColumn("tx_node_id", "", "Transmitter node ID"),
    KpiOutputColumn("rx_node_id", "", "Receiver node ID"),
    KpiOutputColumn("intended_set_size", "", "|Z| for this TX"),
    KpiOutputColumn("gap_ms", "ms", "Time gap between successive different-packet receptions"),
)


@register(
    id="pir_type2",
    title="PIR Type 2 (groupcast, intended set)",
    spec_ref="3GPP TR 37.885 V15.3.0 §5.2.4",
    output_kind="cdf",
    output_columns=OUTPUT_COLUMNS,
)
class PirType2(KpiBase):
    def compute(self, ctx):
        topo = ctx.topology
        out = ctx.outcomes
        rows: List[Dict] = []

        for tx_node in topo.tx_nodes():
            intended = topo.intended_for(tx_node)
            if not intended:
                continue
            for rx_node in intended:
                recs = out.receptions_from(tx_node, rx_node)
                if not recs:
                    continue
                # Dedup by seq, keep earliest reception.
                seen: Dict[int, float] = {}
                for t, seq, path in recs:
                    if seq not in seen or t < seen[seq]:
                        seen[seq] = t
                seqs_sorted = sorted(seen.keys(), key=lambda s: seen[s])
                timeline = [(seen[s], s) for s in seqs_sorted]
                for i in range(1, len(timeline)):
                    t_prev, s_prev = timeline[i - 1]
                    t_curr, s_curr = timeline[i]
                    if s_curr == s_prev:
                        continue
                    rows.append({
                        "tx_node_id": tx_node,
                        "rx_node_id": rx_node,
                        "intended_set_size": len(intended),
                        "gap_ms": (t_curr - t_prev) * 1000.0,
                    })

        return [CdfResult(rows, filename="pir_type2.cdf.csv")]