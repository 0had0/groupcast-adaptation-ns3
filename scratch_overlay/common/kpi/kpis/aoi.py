"""
aoi.py — Age of Information (AoI) per link.

For each packet successfully received at Rx from Tx, AoI is the time elapsed
between the packet's TX at Tx and its FIRST successful reception at Rx. We
take the first reception per (Tx, Rx, seq) so that multi-path deliveries
(via the relay) are counted once with the faster path — this is the
information age as seen by the receiver (3GPP TR 37.885 §5.2.4).

Output:
  <tag>_aoi.csv — one row per (tx_node, rx_node) with AoI statistics.
"""
from __future__ import annotations

from typing import Dict, List

from kpi.registry import (
    ScalarResult, KpiBase, KpiOutputColumn, register,
)


OUTPUT_COLUMNS = (
    KpiOutputColumn("tx_node_id", "", "Transmitter node ID"),
    KpiOutputColumn("rx_node_id", "", "Receiver node ID"),
    KpiOutputColumn("n_samples", "", "Number of AoI samples (= |successful TXs|)"),
    KpiOutputColumn("aoi_mean_ms", "ms", "Mean Age of Information"),
    KpiOutputColumn("aoi_p5_ms", "ms", "5th-percentile AoI"),
    KpiOutputColumn("aoi_p95_ms", "ms", "95th-percentile AoI"),
    KpiOutputColumn("aoi_max_ms", "ms", "Worst-case (peak) AoI"),
    KpiOutputColumn("aoi_min_ms", "ms", "Best-case AoI"),
)


def _percentile(sorted_vals: List[float], q: float) -> float:
    """Linear-interp percentile on a sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_vals[0]
    idx = q * (n - 1)
    lo = int(idx)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[min(lo + 1, n - 1)] * frac


@register(
    id="aoi",
    title="Age of Information (AoI) per link",
    spec_ref="3GPP TR 37.885 V15.3.0 §5.2.4 (information age)",
    output_kind="scalar",
    output_columns=OUTPUT_COLUMNS,
)
class Aoi(KpiBase):
    def compute(self, ctx):
        topo = ctx.topology
        out = ctx.outcomes
        rows: List[Dict] = []

        for tx_node in topo.tx_nodes():
            tx_seq_times = out.tx_seq_times(tx_node)
            if not tx_seq_times:
                continue
            for rx_node in topo.all_nodes - {tx_node}:
                recs = out.receptions_from(tx_node, rx_node)
                if not recs:
                    continue
                # Take the FIRST reception per seq (fastest path wins).
                first_rx: Dict[int, float] = {}
                for t_rx, seq, path in recs:
                    if seq not in first_rx or t_rx < first_rx[seq]:
                        first_rx[seq] = t_rx
                aoi_ms: List[float] = []
                for seq, t_rx in first_rx.items():
                    t_tx = tx_seq_times.get(seq)
                    if t_tx is not None and t_rx >= t_tx:
                        aoi_ms.append((t_rx - t_tx) * 1000.0)
                if not aoi_ms:
                    continue
                aoi_sorted = sorted(aoi_ms)
                n = len(aoi_sorted)
                rows.append({
                    "tx_node_id": tx_node,
                    "rx_node_id": rx_node,
                    "n_samples": n,
                    "aoi_mean_ms": sum(aoi_sorted) / n,
                    "aoi_p5_ms": _percentile(aoi_sorted, 0.05),
                    "aoi_p95_ms": _percentile(aoi_sorted, 0.95),
                    "aoi_max_ms": aoi_sorted[-1],
                    "aoi_min_ms": aoi_sorted[0],
                })

        return [ScalarResult(rows, filename="aoi.csv")]