"""
pir_type1.py — 3GPP TR 37.885 §5.2.3 PIR Type 1 (broadcast, distance-gated).

Per-link definition:
    PIR is the time elapsed between two successive successful receptions of
    two DIFFERENT packets from Tx to Rx, if the distances at both reception
    times are within (0, D].

Output:
  <tag>_pir_type1.cdf.csv — one row per (tx_node, rx_node, gap_ms) sample
"""
from __future__ import annotations

from typing import Dict, List

from kpi.registry import (
    CdfResult, KpiBase, KpiOutputColumn, register,
)


OUTPUT_COLUMNS = (
    KpiOutputColumn("tx_node_id", "", "Transmitter node ID"),
    KpiOutputColumn("rx_node_id", "", "Receiver node ID"),
    KpiOutputColumn("D_m", "m", "Distance gate D"),
    KpiOutputColumn("gap_ms", "ms", "Time gap between successive different-packet receptions"),
)


@register(
    id="pir_type1",
    title="PIR Type 1 (broadcast, distance-gated)",
    spec_ref="3GPP TR 37.885 V15.3.0 §5.2.3",
    output_kind="cdf",
    output_columns=OUTPUT_COLUMNS,
)
class PirType1(KpiBase):
    def compute(self, ctx):
        topo = ctx.topology
        out = ctx.outcomes
        # D = max range upper bound (or a single default if no ranges).
        if ctx.ranges.ranges:
            D = max(r.b for r in ctx.ranges.ranges)
        else:
            D = 320.0  # fallback default
        rows: List[Dict] = []

        for tx_node in topo.tx_nodes():
            for rx_node in topo.all_nodes - {tx_node}:
                d_const = topo.distance(tx_node, rx_node)
                # For moving-platoon the distance is roughly constant.
                # If we have ranges configured, only emit rows for rx in range.
                if ctx.ranges.ranges and not ctx.ranges.in_any_range(d_const):
                    continue

                recs = out.receptions_from(tx_node, rx_node)
                if not recs:
                    continue
                # Dedup by seq, keep earliest reception.
                seen: Dict[int, float] = {}
                for t, seq, path in recs:
                    if seq not in seen or t < seen[seq]:
                        seen[seq] = t
                # Sort by time, then compute gaps between successive different seqs.
                seqs_sorted = sorted(seen.keys(), key=lambda s: seen[s])
                # Re-build (time, seq) tuples
                timeline = [(seen[s], s) for s in seqs_sorted]
                for i in range(1, len(timeline)):
                    t_prev, s_prev = timeline[i - 1]
                    t_curr, s_curr = timeline[i]
                    if s_curr == s_prev:
                        continue
                    # Distance gate: both endpoints must be within D.
                    # We use the static distance (constant for our moving-platoon).
                    if d_const <= D:
                        rows.append({
                            "tx_node_id": tx_node,
                            "rx_node_id": rx_node,
                            "D_m": D,
                            "gap_ms": (t_curr - t_prev) * 1000.0,
                        })

        return [CdfResult(rows, filename="pir_type1.cdf.csv")]